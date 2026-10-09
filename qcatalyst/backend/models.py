"""
Pydantic models for QCatalyst API request/response validation.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Catalyst ────────────────────────────────────────────────────────────────

class AdsorptionEnergies(BaseModel):
    H:  Optional[float] = None
    CO: Optional[float] = None
    OH: Optional[float] = None
    N2: Optional[float] = None
    O:  Optional[float] = None


class Catalyst(BaseModel):
    id:                str
    formula:           str
    name:              str
    structure_type:    str
    d_band_center:     float        = Field(..., description="eV, Hammer-Nørskov d-band centre")
    adsorption_energies: AdsorptionEnergies
    cost_per_kg:       float        = Field(..., description="USD per kg")
    abundance_ppm:     float        = Field(..., description="Crustal abundance in ppm")
    stability_score:   float        = Field(..., ge=0, le=1, description="0–1 stability index")
    selectivity_score: float        = Field(..., ge=0, le=1, description="0–1 selectivity index")
    electronegativity: float
    atomic_radius:     float        = Field(..., description="Angstrom")
    notes:             Optional[str] = None
    is_custom:         bool         = False


class AddCatalystRequest(BaseModel):
    formula:           str
    name:              str
    structure_type:    str           = "Unknown"
    d_band_center:     float
    adsorption_energies: AdsorptionEnergies = AdsorptionEnergies()
    cost_per_kg:       float
    abundance_ppm:     float         = 1.0
    stability_score:   float         = Field(0.5, ge=0, le=1)
    selectivity_score: float         = Field(0.5, ge=0, le=1)
    electronegativity: float         = 2.0
    atomic_radius:     float         = 1.4
    notes:             Optional[str] = None


# ── Reactions ────────────────────────────────────────────────────────────────

class Reaction(BaseModel):
    id:          str
    name:        str
    description: str
    descriptor:  str   = Field(..., description="Key thermodynamic descriptor")
    optimum:     float = Field(..., description="Optimal descriptor value (eV)")
    adsorbate:   str   = Field(..., description="Key adsorbate species symbol")
    unit:        str   = "eV"


# ── Simulation ────────────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    catalyst_id: str
    reaction_id: str
    mode:        str = Field("surrogate", pattern="^(quantum|surrogate)$")


class VQEIteration(BaseModel):
    iteration: int
    energy:    float


class SimulationResult(BaseModel):
    catalyst_id:       str
    reaction_id:       str
    mode:              str
    adsorption_energy: float   = Field(..., description="eV")
    barrier_estimate:  float   = Field(..., description="Activation barrier estimate, eV")
    ground_state_energy: Optional[float] = None
    vqe_history:       Optional[List[VQEIteration]] = None
    convergence_info:  Optional[str] = None
    uncertainty:       Optional[float] = None
    disclaimer:        str
    error:             Optional[str] = None


class JobStatus(BaseModel):
    job_id:   str
    status:   str   = Field(..., description="pending | running | done | error")
    progress: float = Field(0.0, ge=0, le=1)
    result:   Optional[SimulationResult] = None
    error:    Optional[str] = None


# ── Ranking ────────────────────────────────────────────────────────────────

class Weights(BaseModel):
    activity:    float = Field(0.35, ge=0, le=1)
    stability:   float = Field(0.20, ge=0, le=1)
    selectivity: float = Field(0.20, ge=0, le=1)
    cost:        float = Field(0.15, ge=0, le=1)
    abundance:   float = Field(0.10, ge=0, le=1)


class RankRequest(BaseModel):
    reaction_id: str
    weights:     Weights = Weights()
    mode:        str     = Field("surrogate", pattern="^(quantum|surrogate)$")
    catalyst_ids: Optional[List[str]] = None   # None = rank all


class RankedCatalyst(BaseModel):
    rank:              int
    catalyst:          Catalyst
    simulation:        SimulationResult
    composite_score:   float = Field(..., ge=0, le=1)
    activity_score:    float
    stability_score:   float
    selectivity_score: float
    cost_score:        float
    abundance_score:   float
    volcano_x:         float   = Field(..., description="x-axis on volcano plot (descriptor value)")
    volcano_y:         float   = Field(..., description="y-axis on volcano plot (activity proxy)")
    energy_profile:    List[Dict[str, Any]]


class RankResponse(BaseModel):
    reaction_id: str
    mode:        str
    leaderboard: List[RankedCatalyst]
    disclaimer:  str


# ── Explain ────────────────────────────────────────────────────────────────

class ExplainRequest(BaseModel):
    reaction_id:    str
    ranked_catalyst: RankedCatalyst


class ExplainResponse(BaseModel):
    explanation:     str
    trade_offs:      List[str]
    next_experiments: List[str]
    source:          str   = Field(..., description="anthropic | template")
