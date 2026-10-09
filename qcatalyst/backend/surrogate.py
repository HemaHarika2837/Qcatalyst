"""
ML surrogate model for QCatalyst.

Trains a GradientBoostingRegressor on the built-in catalyst dataset to
predict adsorption energy from readily available features (d-band centre,
electronegativity, atomic radius).  Uncertainty is estimated via a
bootstrap ensemble.

SURROGATE DISCLAIMER:
  All predictions are simplified approximations based on regression over
  a small training set.  Results do not represent experimentally validated
  or DFT-computed values.

Where real DFT plugs in:
  Replace `predict()` with a call to your ASE/GPAW or VASP/Quantum ESPRESSO
  workflow that returns the actual converged adsorption energy.
"""
from __future__ import annotations
import json
import os
import math
import random
from typing import Dict, List, Tuple

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

# ── Reaction-specific adsorbate keys ─────────────────────────────────────────
REACTION_ADSORBATE: Dict[str, str] = {
    "HER":   "H",
    "CO2RR": "CO",
    "ORR":   "OH",
    "NH3":   "N2",
    "OER":   "OH",
}


def _load_training_data(
    catalysts: List[dict],
    adsorbate: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Extract features and labels from the catalyst dataset.
    Features: [d_band_center, electronegativity, atomic_radius]
    Label:    adsorption energy for the target adsorbate
    """
    X, y = [], []
    for c in catalysts:
        val = c.get("adsorption_energies", {}).get(adsorbate)
        if val is None:
            continue
        X.append([
            c["d_band_center"],
            c["electronegativity"],
            c["atomic_radius"],
        ])
        y.append(val)
    return np.array(X, dtype=float), np.array(y, dtype=float)


class SurrogateModel:
    """
    One surrogate model instance per reaction.
    Trained lazily on first use.
    """

    def __init__(self, reaction_id: str, catalysts: List[dict]):
        self.reaction_id = reaction_id
        self.adsorbate   = REACTION_ADSORBATE.get(reaction_id, "H")
        self.catalysts   = catalysts
        self._pipeline: Pipeline | None = None
        self._ensemble:  List[Pipeline] = []   # bootstrap models for uncertainty
        self._cv_score: float = 0.0
        self._trained = False

    def _build_pipeline(self) -> Pipeline:
        return Pipeline([
            ("scaler", StandardScaler()),
            ("model",  GradientBoostingRegressor(
                n_estimators=120,
                max_depth=3,
                learning_rate=0.08,
                subsample=0.85,
                random_state=42,
            )),
        ])

    def train(self) -> None:
        X, y = _load_training_data(self.catalysts, self.adsorbate)
        if len(X) < 5:
            raise ValueError(f"Not enough training data for {self.reaction_id}: {len(X)} samples")

        # Main pipeline
        pipe = self._build_pipeline()
        pipe.fit(X, y)
        self._pipeline = pipe

        # Cross-val score (R²)
        scores = cross_val_score(self._build_pipeline(), X, y, cv=min(5, len(X)), scoring="r2")
        self._cv_score = float(np.mean(scores))

        # Bootstrap ensemble (20 models) for uncertainty estimation
        rng = np.random.default_rng(0)
        self._ensemble = []
        n = len(X)
        for _ in range(20):
            idx = rng.integers(0, n, size=n)
            p = self._build_pipeline()
            p.fit(X[idx], y[idx])
            self._ensemble.append(p)

        self._trained = True

    def predict(self, d_band_center: float, electronegativity: float, atomic_radius: float
                ) -> Tuple[float, float]:
        """
        Returns (predicted_adsorption_energy_eV, uncertainty_eV).
        Uncertainty is the std-dev over the bootstrap ensemble.
        """
        if not self._trained:
            self.train()

        x = np.array([[d_band_center, electronegativity, atomic_radius]])
        main_pred = float(self._pipeline.predict(x)[0])

        ensemble_preds = [float(p.predict(x)[0]) for p in self._ensemble]
        uncertainty = float(np.std(ensemble_preds))

        return round(main_pred, 4), round(uncertainty, 4)

    @property
    def cv_r2(self) -> float:
        return round(self._cv_score, 4)


# ── Module-level cache: one model per reaction ────────────────────────────────
_MODEL_CACHE: Dict[str, SurrogateModel] = {}
_CATALYST_DATA: List[dict] = []


def _ensure_loaded() -> None:
    global _CATALYST_DATA
    if _CATALYST_DATA:
        return
    here = os.path.dirname(__file__)
    path = os.path.join(here, "data", "catalysts.json")
    with open(path, encoding="utf-8") as f:
        _CATALYST_DATA = json.load(f)


def get_surrogate(reaction_id: str) -> SurrogateModel:
    """Retrieve (and train if needed) the surrogate for a given reaction."""
    _ensure_loaded()
    if reaction_id not in _MODEL_CACHE:
        model = SurrogateModel(reaction_id, _CATALYST_DATA)
        model.train()
        _MODEL_CACHE[reaction_id] = model
    return _MODEL_CACHE[reaction_id]


def predict_adsorption_energy(
    reaction_id: str,
    d_band_center: float,
    electronegativity: float,
    atomic_radius: float,
) -> Tuple[float, float, str]:
    """
    Public API used by quantum.py and main.py.
    Returns (energy_eV, uncertainty_eV, cv_r2_str).
    """
    model = get_surrogate(reaction_id)
    energy, unc = model.predict(d_band_center, electronegativity, atomic_radius)
    return energy, unc, f"CV R²={model.cv_r2}"


# ── Barrier estimate ──────────────────────────────────────────────────────────
def estimate_barrier(adsorption_energy: float, reaction_id: str) -> float:
    """
    Brønsted–Evans–Polanyi (BEP) linear scaling approximation.
    barrier ≈ α * |ΔE_ads| + β

    NOTE: Real BEP parameters come from DFT surface reaction data.
          These are illustrative values for screening purposes only.
    """
    BEP: Dict[str, Tuple[float, float]] = {
        "HER":   (0.50, 0.10),
        "CO2RR": (0.55, 0.15),
        "ORR":   (0.45, 0.20),
        "NH3":   (0.60, 0.25),
        "OER":   (0.48, 0.18),
    }
    alpha, beta = BEP.get(reaction_id, (0.5, 0.15))
    barrier = alpha * abs(adsorption_energy) + beta
    return round(max(barrier, 0.05), 4)
