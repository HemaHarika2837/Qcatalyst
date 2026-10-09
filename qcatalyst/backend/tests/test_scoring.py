"""
Pytest tests for QCatalyst – scoring engine and surrogate model.
Run from the project root:
  pytest backend/tests/test_scoring.py -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pytest
from models import Catalyst, AdsorptionEnergies, SimulationResult, Weights
from scoring import (
    gaussian_activity,
    cost_score,
    abundance_score,
    compute_composite_score,
    rank_catalysts,
    build_energy_profile,
)
from surrogate import predict_adsorption_energy, estimate_barrier


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_catalyst(cid, d_band=-2.25, cost=32000, abund=0.005, stab=0.95, sel=0.85,
                   ads_H=-0.09, eneg=2.28, radius=1.39):
    return Catalyst(
        id=cid, formula=cid, name=cid, structure_type="FCC",
        d_band_center=d_band,
        adsorption_energies=AdsorptionEnergies(H=ads_H, CO=-1.48, OH=-2.20, N2=-0.35, O=-3.60),
        cost_per_kg=cost, abundance_ppm=abund,
        stability_score=stab, selectivity_score=sel,
        electronegativity=eneg, atomic_radius=radius,
    )


def make_sim(cat_id, rxn_id, energy, barrier=0.10, mode="surrogate"):
    return SimulationResult(
        catalyst_id=cat_id, reaction_id=rxn_id, mode=mode,
        adsorption_energy=energy, barrier_estimate=barrier,
        disclaimer="Test approximation",
    )


# ── gaussian_activity ────────────────────────────────────────────────────────

def test_gaussian_activity_peak():
    """Activity is exactly 1.0 at the optimum."""
    score = gaussian_activity(0.0, optimum=0.0, width=0.4)
    assert score == pytest.approx(1.0, abs=1e-9)


def test_gaussian_activity_off_peak():
    """Activity falls off away from optimum."""
    at_peak = gaussian_activity(0.0, optimum=0.0, width=0.4)
    off_peak = gaussian_activity(0.5, optimum=0.0, width=0.4)
    assert at_peak > off_peak


def test_gaussian_activity_symmetry():
    """Gaussian is symmetric around the optimum."""
    left  = gaussian_activity(-0.3, optimum=0.0, width=0.4)
    right = gaussian_activity( 0.3, optimum=0.0, width=0.4)
    assert left == pytest.approx(right, rel=1e-9)


def test_gaussian_activity_range():
    """Activity score is always in [0, 1]."""
    for x in [-3.0, -1.0, 0.0, 0.5, 1.5, 5.0]:
        s = gaussian_activity(x, optimum=0.0, width=0.4)
        assert 0.0 <= s <= 1.0


# ── cost_score ────────────────────────────────────────────────────────────────

def test_cost_score_cheap_beats_expensive():
    cheap     = cost_score(10)       # Iron-level cost
    expensive = cost_score(32000)    # Pt-level cost
    assert cheap > expensive


def test_cost_score_range():
    for cost in [0, 1, 100, 10000, 65000]:
        s = cost_score(cost)
        assert 0.0 <= s <= 1.0


def test_cost_score_zero():
    """Zero cost gives score of 1.0."""
    assert cost_score(0) == pytest.approx(1.0)


# ── abundance_score ───────────────────────────────────────────────────────────

def test_abundance_high_beats_low():
    rare      = abundance_score(0.005)    # Pt
    abundant  = abundance_score(56000)    # Fe
    assert abundant > rare


def test_abundance_range():
    for ab in [0.001, 1.0, 100.0, 56000.0]:
        s = abundance_score(ab)
        assert 0.0 <= s <= 1.0


# ── compute_composite_score ───────────────────────────────────────────────────

def test_composite_all_ones():
    """All-one inputs with any weights → composite = 1.0."""
    w = Weights(activity=0.35, stability=0.20, selectivity=0.20, cost=0.15, abundance=0.10)
    score = compute_composite_score(1.0, 1.0, 1.0, 1.0, 1.0, w)
    assert score == pytest.approx(1.0, abs=1e-6)


def test_composite_all_zeros():
    w = Weights(activity=0.35, stability=0.20, selectivity=0.20, cost=0.15, abundance=0.10)
    score = compute_composite_score(0.0, 0.0, 0.0, 0.0, 0.0, w)
    assert score == pytest.approx(0.0, abs=1e-6)


def test_composite_range():
    w = Weights()
    score = compute_composite_score(0.7, 0.8, 0.6, 0.4, 0.9, w)
    assert 0.0 <= score <= 1.0


def test_composite_weight_influence():
    """Increasing activity weight raises score when activity is high."""
    w_low  = Weights(activity=0.10, stability=0.30, selectivity=0.30, cost=0.15, abundance=0.15)
    w_high = Weights(activity=0.60, stability=0.15, selectivity=0.10, cost=0.10, abundance=0.05)
    s_low  = compute_composite_score(0.95, 0.5, 0.5, 0.5, 0.5, w_low)
    s_high = compute_composite_score(0.95, 0.5, 0.5, 0.5, 0.5, w_high)
    assert s_high > s_low


# ── rank_catalysts ────────────────────────────────────────────────────────────

def test_rank_order():
    """Better activity catalyst should rank #1."""
    cat_good = make_catalyst("Good", d_band=-2.25, ads_H=-0.01)  # near volcano peak
    cat_bad  = make_catalyst("Bad",  d_band=-4.50, ads_H=1.20)   # far from peak
    sims = {
        "Good": make_sim("Good", "HER", energy=-0.01),
        "Bad":  make_sim("Bad",  "HER", energy=1.20),
    }
    ranked = rank_catalysts([cat_good, cat_bad], sims, "HER", Weights())
    assert ranked[0].catalyst.id == "Good"
    assert ranked[1].catalyst.id == "Bad"


def test_rank_assigns_ranks():
    cats = [make_catalyst(f"C{i}", ads_H=float(i) * 0.1) for i in range(5)]
    sims = {c.id: make_sim(c.id, "HER", energy=float(i) * 0.1) for i, c in enumerate(cats)}
    ranked = rank_catalysts(cats, sims, "HER", Weights())
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))


def test_rank_missing_sim_skipped():
    """Catalyst with no simulation entry is skipped gracefully."""
    cat1 = make_catalyst("A", ads_H=-0.05)
    cat2 = make_catalyst("B", ads_H=0.50)
    sims = {"A": make_sim("A", "HER", energy=-0.05)}   # B has no sim
    ranked = rank_catalysts([cat1, cat2], sims, "HER", Weights())
    assert len(ranked) == 1
    assert ranked[0].catalyst.id == "A"


# ── energy profile ────────────────────────────────────────────────────────────

def test_energy_profile_has_steps():
    cat = make_catalyst("Pt", ads_H=-0.09)
    profile = build_energy_profile(cat, "HER", -0.09, 0.10)
    assert len(profile) >= 3
    for step in profile:
        assert "step" in step
        assert "energy" in step


def test_energy_profile_all_reactions():
    cat = make_catalyst("Test")
    for rxn in ["HER", "CO2RR", "ORR", "NH3", "OER"]:
        profile = build_energy_profile(cat, rxn, -0.20, 0.15)
        assert len(profile) >= 3


# ── surrogate model ───────────────────────────────────────────────────────────

def test_surrogate_predict_her():
    """Surrogate gives a plausible HER prediction for Pt-like features."""
    energy, unc, info = predict_adsorption_energy(
        reaction_id="HER",
        d_band_center=-2.25,
        electronegativity=2.28,
        atomic_radius=1.39,
    )
    # Should be a reasonable adsorption energy (roughly in -1.5 to +1.5 eV range)
    assert -3.0 < energy < 3.0
    assert unc >= 0.0
    assert "R²" in info


def test_surrogate_uncertainty_is_positive():
    _, unc, _ = predict_adsorption_energy("ORR", -1.83, 2.20, 1.37)
    assert unc >= 0.0


def test_surrogate_different_reactions():
    """Each reaction should produce a distinct prediction."""
    results = {}
    for rxn in ["HER", "CO2RR", "ORR", "NH3", "OER"]:
        e, _, _ = predict_adsorption_energy(rxn, -2.25, 2.28, 1.39)
        results[rxn] = e
    # At least two reactions should give different predictions
    values = list(results.values())
    assert not all(v == values[0] for v in values)


# ── estimate_barrier ──────────────────────────────────────────────────────────

def test_barrier_non_negative():
    for rxn in ["HER", "CO2RR", "ORR", "NH3", "OER"]:
        b = estimate_barrier(-0.50, rxn)
        assert b > 0.0


def test_barrier_scales_with_energy():
    """Larger adsorption energy → larger barrier (BEP linear scaling)."""
    b_small = estimate_barrier(-0.10, "HER")
    b_large = estimate_barrier(-1.50, "HER")
    assert b_large > b_small
