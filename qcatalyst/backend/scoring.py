"""
Multi-criteria scoring and Sabatier volcano logic for QCatalyst.

Each criterion is mapped to a 0–1 score, then combined with user weights.
All values are approximations based on simplified models — not DFT-validated.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List

from models import Catalyst, RankedCatalyst, SimulationResult, Weights


# ── Volcano parameters per reaction ─────────────────────────────────────────
# optimum: ideal adsorption-energy value at the volcano peak (eV)
# width:   Gaussian width parameter controlling how sharply activity drops off
VOLCANO_PARAMS: Dict[str, Dict[str, float]] = {
    "HER":  {"optimum": 0.0,   "width": 0.40},   # ΔG_H ≈ 0 eV (Nørskov 2005)
    "CO2RR":{"optimum": -0.67, "width": 0.45},   # CO binding energy
    "ORR":  {"optimum": -0.20, "width": 0.50},   # OH adsorption offset
    "NH3":  {"optimum": -0.80, "width": 0.55},   # N2 dissociation / N* binding
    "OER":  {"optimum": -1.60, "width": 0.50},   # OH / O / OOH scaling
}

# Descriptor adsorbate key per reaction
REACTION_ADSORBATE: Dict[str, str] = {
    "HER":  "H",
    "CO2RR":"CO",
    "ORR":  "OH",
    "NH3":  "N2",
    "OER":  "OH",
}

# Maximum realistic cost per kg for normalisation (USD)
MAX_COST = 65_000.0
# Maximum crustal abundance for normalisation (ppm)
MAX_ABUNDANCE = 60_000.0


def gaussian_activity(descriptor_value: float, optimum: float, width: float) -> float:
    """
    Gaussian Sabatier volcano: activity = exp(-((x - opt)^2) / (2*w^2)).
    Returns a 0–1 activity score.
    """
    exponent = -((descriptor_value - optimum) ** 2) / (2 * width ** 2)
    return math.exp(exponent)


def cost_score(cost_per_kg: float) -> float:
    """Lower cost → higher score. Log-normalised to compress the Pt/Au extreme."""
    if cost_per_kg <= 0:
        return 1.0
    log_cost = math.log1p(cost_per_kg)
    log_max  = math.log1p(MAX_COST)
    return 1.0 - min(log_cost / log_max, 1.0)


def abundance_score(abundance_ppm: float) -> float:
    """Higher abundance → higher score. Log-normalised."""
    if abundance_ppm <= 0:
        return 0.0
    log_ab  = math.log1p(abundance_ppm)
    log_max = math.log1p(MAX_ABUNDANCE)
    return min(log_ab / log_max, 1.0)


def build_energy_profile(
    catalyst: Catalyst,
    reaction_id: str,
    adsorption_energy: float,
    barrier: float,
) -> List[Dict[str, Any]]:
    """
    Construct a simplified free-energy profile along the reaction coordinate.
    Points: initial state → adsorbed intermediate → transition state → product.

    NOTE: This is a schematic profile, not a real IRC or NEB calculation.
    In a production system, plug in NEB/CI-NEB results from DFT here.
    """
    if reaction_id == "HER":
        # H+ + e- → H* → ½H2
        return [
            {"step": "H⁺ + e⁻",          "label": "Initial",      "energy": 0.0},
            {"step": "H*",                 "label": "Adsorbed H",   "energy": adsorption_energy},
            {"step": "TS",                 "label": "Trans. State",  "energy": adsorption_energy + barrier},
            {"step": "½ H₂",              "label": "Product",      "energy": -0.04},
        ]
    elif reaction_id == "CO2RR":
        return [
            {"step": "CO₂ + H⁺ + e⁻",    "label": "Initial",      "energy": 0.0},
            {"step": "COOH*",              "label": "COOH*",        "energy": adsorption_energy + 0.30},
            {"step": "CO* + H₂O",          "label": "CO*",          "energy": adsorption_energy},
            {"step": "TS",                 "label": "Trans. State",  "energy": adsorption_energy + barrier},
            {"step": "CO (g)",             "label": "Product",      "energy": -0.10},
        ]
    elif reaction_id == "ORR":
        return [
            {"step": "O₂ + H⁺ + e⁻",     "label": "Initial",      "energy": 0.0},
            {"step": "OOH*",               "label": "OOH*",         "energy": adsorption_energy + 0.40},
            {"step": "O* + H₂O",           "label": "O*",           "energy": adsorption_energy + 0.80},
            {"step": "OH*",                "label": "OH*",          "energy": adsorption_energy},
            {"step": "TS",                 "label": "Trans. State",  "energy": adsorption_energy + barrier},
            {"step": "H₂O",               "label": "Product",      "energy": -1.23},
        ]
    elif reaction_id == "NH3":
        return [
            {"step": "N₂ + 3H₂",          "label": "Initial",      "energy": 0.0},
            {"step": "N₂*",                "label": "N₂ adsorbed",  "energy": adsorption_energy},
            {"step": "2 NH*",              "label": "NH*",          "energy": adsorption_energy + 1.0},
            {"step": "TS",                 "label": "Trans. State",  "energy": adsorption_energy + 1.0 + barrier},
            {"step": "2 NH₃",             "label": "Product",      "energy": -0.33},
        ]
    else:  # OER
        return [
            {"step": "H₂O",               "label": "Initial",      "energy": 0.0},
            {"step": "OH*",                "label": "OH*",          "energy": adsorption_energy},
            {"step": "O*",                 "label": "O*",           "energy": adsorption_energy + 0.80},
            {"step": "OOH*",               "label": "OOH*",         "energy": adsorption_energy + 2.40},
            {"step": "TS",                 "label": "Trans. State",  "energy": adsorption_energy + 2.40 + barrier},
            {"step": "O₂",                "label": "Product",      "energy": 1.23},
        ]


def compute_composite_score(
    activity:    float,
    stability:   float,
    selectivity: float,
    cost:        float,
    abund:       float,
    weights:     Weights,
) -> float:
    """Weighted sum normalised by total weight."""
    total_w = (
        weights.activity + weights.stability + weights.selectivity
        + weights.cost + weights.abundance
    )
    if total_w == 0:
        return 0.0
    score = (
        weights.activity    * activity    +
        weights.stability   * stability   +
        weights.selectivity * selectivity +
        weights.cost        * cost        +
        weights.abundance   * abund
    ) / total_w
    return round(min(max(score, 0.0), 1.0), 4)


def rank_catalysts(
    catalysts:   List[Catalyst],
    simulations: Dict[str, SimulationResult],
    reaction_id: str,
    weights:     Weights,
) -> List[RankedCatalyst]:
    """
    Compute per-criterion scores, composite score, and volcano coordinates,
    then return a sorted leaderboard.
    """
    volcano = VOLCANO_PARAMS.get(reaction_id, {"optimum": 0.0, "width": 0.45})
    adsorbate_key = REACTION_ADSORBATE.get(reaction_id, "H")

    ranked: List[RankedCatalyst] = []

    for cat in catalysts:
        sim = simulations.get(cat.id)
        if sim is None:
            continue

        # --- activity score via Sabatier volcano ---
        descriptor_val = sim.adsorption_energy
        act_score = gaussian_activity(descriptor_val, volcano["optimum"], volcano["width"])

        # --- other per-criterion scores ---
        stab_score  = cat.stability_score
        sel_score   = cat.selectivity_score
        c_score     = cost_score(cat.cost_per_kg)
        ab_score    = abundance_score(cat.abundance_ppm)

        composite = compute_composite_score(
            act_score, stab_score, sel_score, c_score, ab_score, weights
        )

        # --- volcano plot coordinates ---
        volcano_x = descriptor_val
        volcano_y = act_score  # proxy for catalytic activity (0–1)

        # --- energy profile ---
        profile = build_energy_profile(cat, reaction_id, sim.adsorption_energy, sim.barrier_estimate)

        ranked.append(RankedCatalyst(
            rank=0,                           # filled in after sort
            catalyst=cat,
            simulation=sim,
            composite_score=composite,
            activity_score=round(act_score, 4),
            stability_score=round(stab_score, 4),
            selectivity_score=round(sel_score, 4),
            cost_score=round(c_score, 4),
            abundance_score=round(ab_score, 4),
            volcano_x=round(volcano_x, 4),
            volcano_y=round(volcano_y, 4),
            energy_profile=profile,
        ))

    ranked.sort(key=lambda r: r.composite_score, reverse=True)
    for i, r in enumerate(ranked, start=1):
        r.rank = i
    return ranked
