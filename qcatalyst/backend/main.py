"""
QCatalyst – FastAPI Backend
===========================
Endpoints:
  GET  /api/reactions
  GET  /api/catalysts
  POST /api/catalysts
  POST /api/simulate
  GET  /api/jobs/{job_id}
  POST /api/rank
  GET  /api/report?reaction=...
  POST /api/explain

Run:
  uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations
import csv
import io
import json
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# ── Local imports ─────────────────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(__file__))

from models import (
    Catalyst, AddCatalystRequest,
    Reaction,
    SimulateRequest, SimulationResult, JobStatus, VQEIteration,
    RankRequest, RankResponse, RankedCatalyst, Weights,
    ExplainRequest, ExplainResponse,
)
from scoring import rank_catalysts
from quantum import new_job, get_job, _run_simulation_sync

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="QCatalyst API",
    description="Quantum Simulation-based Catalyst Design System",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory data stores ─────────────────────────────────────────────────────
_DATA_PATH   = os.path.join(os.path.dirname(__file__), "data", "catalysts.json")
_custom_catalysts: List[Catalyst] = []
_sim_cache:  Dict[str, SimulationResult] = {}   # key: f"{catalyst_id}:{reaction_id}:{mode}"

# ── Load base catalysts ───────────────────────────────────────────────────────
def _load_catalysts() -> List[Catalyst]:
    with open(_DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return [Catalyst(**c) for c in raw]


def get_all_catalysts() -> List[Catalyst]:
    return _load_catalysts() + _custom_catalysts


# ── Reactions master list ─────────────────────────────────────────────────────
REACTIONS: List[Reaction] = [
    Reaction(
        id="HER",
        name="Hydrogen Evolution Reaction",
        description=(
            "Electrocatalytic reduction of protons to H₂ gas.  "
            "Critical for green hydrogen production via water electrolysis."
        ),
        descriptor="Hydrogen adsorption free energy (ΔG_H)",
        optimum=0.0,
        adsorbate="H",
        unit="eV",
    ),
    Reaction(
        id="CO2RR",
        name="CO₂ Reduction Reaction",
        description=(
            "Electrochemical conversion of CO₂ to fuels or chemicals (CO, formate, "
            "methanol, ethylene).  Key to carbon capture utilisation."
        ),
        descriptor="CO adsorption energy (ΔE_CO)",
        optimum=-0.67,
        adsorbate="CO",
        unit="eV",
    ),
    Reaction(
        id="ORR",
        name="Oxygen Reduction Reaction",
        description=(
            "Cathodic reaction in fuel cells converting O₂ to H₂O.  "
            "Sluggish kinetics make this the main efficiency bottleneck."
        ),
        descriptor="OH adsorption energy (ΔE_OH)",
        optimum=-0.20,
        adsorbate="OH",
        unit="eV",
    ),
    Reaction(
        id="NH3",
        name="Ammonia Synthesis (Haber-Bosch)",
        description=(
            "Catalytic reduction of N₂ + 3H₂ → 2NH₃.  "
            "Accounts for ~2% of global energy consumption; key fertiliser route."
        ),
        descriptor="N₂ adsorption energy (ΔE_N2)",
        optimum=-0.80,
        adsorbate="N2",
        unit="eV",
    ),
    Reaction(
        id="OER",
        name="Oxygen Evolution Reaction",
        description=(
            "Anodic water oxidation producing O₂.  "
            "The kinetically challenging half-reaction of water splitting."
        ),
        descriptor="OH adsorption energy (ΔE_OH)",
        optimum=-1.60,
        adsorbate="OH",
        unit="eV",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/reactions", response_model=List[Reaction], tags=["Reactions"])
def list_reactions():
    """Return all supported target reactions with descriptor info."""
    return REACTIONS


@app.get("/api/catalysts", response_model=List[Catalyst], tags=["Catalysts"])
def list_catalysts():
    """Return the full catalyst library including any custom entries."""
    return get_all_catalysts()


@app.post("/api/catalysts", response_model=Catalyst, tags=["Catalysts"])
def add_catalyst(body: AddCatalystRequest):
    """Add a custom catalyst to the session library."""
    # Generate a unique id
    cat_id = body.formula.replace(" ", "_").replace("/", "-") + f"_{len(_custom_catalysts)+1}"
    cat = Catalyst(
        id=cat_id,
        is_custom=True,
        **body.model_dump(),
    )
    _custom_catalysts.append(cat)
    return cat


@app.post("/api/simulate", response_model=JobStatus, tags=["Simulation"])
async def simulate(body: SimulateRequest, background_tasks: BackgroundTasks):
    """
    Start a simulation for a single catalyst.
    Returns a job_id immediately; poll GET /api/jobs/{id} for progress.
    """
    cache_key = f"{body.catalyst_id}:{body.reaction_id}:{body.mode}"

    # Cache hit – wrap existing result in a completed job
    if cache_key in _sim_cache:
        job_id = new_job()
        from quantum import _JOB_STORE
        _JOB_STORE[job_id].update(
            status="done", progress=1.0, result=_sim_cache[cache_key].model_dump()
        )
        return JobStatus(job_id=job_id, status="done", progress=1.0, result=_sim_cache[cache_key])

    # Find catalyst
    catalysts = get_all_catalysts()
    cat = next((c for c in catalysts if c.id == body.catalyst_id), None)
    if cat is None:
        raise HTTPException(status_code=404, detail=f"Catalyst '{body.catalyst_id}' not found")

    # Find reaction
    rxn = next((r for r in REACTIONS if r.id == body.reaction_id), None)
    if rxn is None:
        raise HTTPException(status_code=404, detail=f"Reaction '{body.reaction_id}' not found")

    job_id = new_job()

    # Launch background simulation (runs in FastAPI's background thread pool)
    def _bg_task():
        from quantum import _JOB_STORE, _run_simulation_sync
        try:
            raw = _run_simulation_sync(
                job_id, cat.id, rxn.id, body.mode,
                cat.d_band_center, cat.electronegativity, cat.atomic_radius,
            )
            if raw.get("vqe_history"):
                raw["vqe_history"] = [VQEIteration(**it) for it in raw["vqe_history"]]
            _JOB_STORE[job_id].update(status="done", progress=1.0, result=raw)
            # cache it
            cache_key_bg = f"{cat.id}:{rxn.id}:{body.mode}"
            _sim_cache[cache_key_bg] = SimulationResult(**raw)
        except Exception as exc:
            _JOB_STORE[job_id].update(status="error", error=str(exc))

    background_tasks.add_task(_bg_task)

    return JobStatus(job_id=job_id, status="pending", progress=0.0)


@app.get("/api/jobs/{job_id}", response_model=JobStatus, tags=["Simulation"])
def get_job_status(job_id: str):
    """Poll simulation job status and result."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result_obj: Optional[SimulationResult] = None
    if job["result"]:
        raw = job["result"]
        if isinstance(raw, dict):
            # Reconstruct VQEIteration list if present
            if raw.get("vqe_history"):
                raw["vqe_history"] = [
                    VQEIteration(**it) if isinstance(it, dict) else it
                    for it in raw["vqe_history"]
                ]
            result_obj = SimulationResult(**raw)
        else:
            result_obj = raw

    return JobStatus(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        result=result_obj,
        error=job.get("error"),
    )


@app.post("/api/rank", response_model=RankResponse, tags=["Ranking"])
def rank(body: RankRequest):
    """
    Run surrogate/quantum simulations for all (or selected) catalysts,
    compute composite scores, and return a sorted leaderboard.
    """
    all_cats = get_all_catalysts()

    if body.catalyst_ids:
        cats = [c for c in all_cats if c.id in body.catalyst_ids]
    else:
        cats = all_cats

    rxn = next((r for r in REACTIONS if r.id == body.reaction_id), None)
    if rxn is None:
        raise HTTPException(status_code=404, detail=f"Unknown reaction: {body.reaction_id}")

    sims: Dict[str, SimulationResult] = {}

    for cat in cats:
        cache_key = f"{cat.id}:{body.reaction_id}:{body.mode}"
        if cache_key in _sim_cache:
            sims[cat.id] = _sim_cache[cache_key]
            continue
        try:
            # Run synchronously (surrogate is fast; quantum falls back to surrogate for bulk)
            raw = _run_simulation_sync(
                job_id="bulk",
                catalyst_id=cat.id,
                reaction_id=body.reaction_id,
                mode=body.mode,
                d_band_center=cat.d_band_center,
                electronegativity=cat.electronegativity,
                atomic_radius=cat.atomic_radius,
            )
            # rebuild vqe_history
            if raw.get("vqe_history"):
                raw["vqe_history"] = [VQEIteration(**it) for it in raw["vqe_history"]]
            sim = SimulationResult(**raw)
            _sim_cache[cache_key] = sim
            sims[cat.id] = sim
        except Exception as ex:
            # Skip this catalyst if simulation fails entirely
            continue

    leaderboard = rank_catalysts(cats, sims, body.reaction_id, body.weights)

    return RankResponse(
        reaction_id=body.reaction_id,
        mode=body.mode,
        leaderboard=leaderboard,
        disclaimer=(
            "⚠️  All scores are prototype approximations based on simplified models "
            "(ML surrogate or minimal VQE proxy).  They are NOT experimentally validated "
            "and should not be used for production catalyst selection without rigorous DFT "
            "and experimental validation."
        ),
    )


@app.get("/api/report", tags=["Export"])
def download_report(reaction: str = Query(..., description="Reaction ID")):
    """Return a CSV of the latest ranked results for the given reaction."""
    # Re-run ranking with default weights using cached simulations
    rank_resp = rank(RankRequest(reaction_id=reaction, mode="surrogate"))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Rank", "Formula", "Name", "Structure",
        "Adsorption Energy (eV)", "Barrier (eV)",
        "Composite Score", "Activity Score", "Stability Score",
        "Selectivity Score", "Cost Score", "Abundance Score",
        "Cost ($/kg)", "Abundance (ppm)", "Mode", "Disclaimer",
    ])
    for r in rank_resp.leaderboard:
        c = r.catalyst
        s = r.simulation
        writer.writerow([
            r.rank, c.formula, c.name, c.structure_type,
            s.adsorption_energy, s.barrier_estimate,
            r.composite_score, r.activity_score, r.stability_score,
            r.selectivity_score, r.cost_score, r.abundance_score,
            c.cost_per_kg, c.abundance_ppm, s.mode, s.disclaimer,
        ])

    output.seek(0)
    filename = f"qcatalyst_{reaction.lower()}_results.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/api/explain", response_model=ExplainResponse, tags=["AI Advisor"])
def explain(body: ExplainRequest):
    """
    Generate a plain-language recommendation for the top-ranked catalyst.
    Uses Anthropic Claude if ANTHROPIC_API_KEY is set, else a template fallback.
    """
    rc  = body.ranked_catalyst
    cat = rc.catalyst
    sim = rc.simulation
    rxn_name = next((r.name for r in REACTIONS if r.id == body.reaction_id), body.reaction_id)

    # ── Anthropic path ────────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            prompt = (
                f"You are a computational catalysis expert advising a research team.\n\n"
                f"Target reaction: {rxn_name}\n"
                f"Top catalyst: {cat.name} ({cat.formula}), {cat.structure_type}\n"
                f"Composite score: {rc.composite_score:.3f}\n"
                f"Adsorption energy: {sim.adsorption_energy:.3f} eV\n"
                f"Activation barrier: {sim.barrier_estimate:.3f} eV\n"
                f"d-band centre: {cat.d_band_center:.2f} eV\n"
                f"Cost: ${cat.cost_per_kg:,.0f}/kg  |  Abundance: {cat.abundance_ppm} ppm\n"
                f"Stability: {cat.stability_score:.2f}  |  Selectivity: {cat.selectivity_score:.2f}\n\n"
                "Provide:\n"
                "1. A 3-sentence plain-language explanation of why this catalyst ranks highest.\n"
                "2. 3 key trade-offs or limitations.\n"
                "3. 3 concrete next experimental steps or alloy modifications to improve it.\n"
                "Keep the tone scientific but accessible."
            )
            msg = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text

            # Parse into sections (best-effort)
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            explanation = " ".join(lines[:3])
            trade_offs  = [l.lstrip("0123456789.-) ") for l in lines[3:6]] or ["See full text above."]
            next_steps  = [l.lstrip("0123456789.-) ") for l in lines[6:9]] or ["Consult full text above."]

            return ExplainResponse(
                explanation=explanation,
                trade_offs=trade_offs,
                next_experiments=next_steps,
                source="anthropic",
            )
        except Exception as ex:
            pass   # fall through to template

    # ── Template fallback ─────────────────────────────────────────────────────
    explanation = _template_explanation(cat, sim, rc, rxn_name, body.reaction_id)
    trade_offs  = _template_tradeoffs(cat, sim, body.reaction_id)
    next_steps  = _template_next_steps(cat, body.reaction_id)

    return ExplainResponse(
        explanation=explanation,
        trade_offs=trade_offs,
        next_experiments=next_steps,
        source="template",
    )


# ── Template helpers ──────────────────────────────────────────────────────────

def _template_explanation(cat, sim, rc, rxn_name: str, reaction_id: str) -> str:
    volcano_quality = "near-optimal" if abs(sim.adsorption_energy) < 0.15 else (
        "moderately placed on" if abs(sim.adsorption_energy) < 0.45 else "off-peak on"
    )
    return (
        f"{cat.name} ({cat.formula}) ranks #1 for {rxn_name} with a composite score of "
        f"{rc.composite_score:.3f}. Its adsorption energy of {sim.adsorption_energy:.3f} eV places it "
        f"{volcano_quality} the Sabatier volcano, indicating {'strong' if rc.activity_score > 0.7 else 'moderate'} "
        f"intrinsic activity. "
        f"Combined with a stability score of {cat.stability_score:.2f} and a cost of "
        f"${cat.cost_per_kg:,.0f}/kg, it offers "
        f"{'an excellent' if rc.composite_score > 0.75 else 'a reasonable'} "
        f"overall balance for practical deployment. "
        f"[NOTE: All values are surrogate-model approximations, not experimentally validated.]"
    )


def _template_tradeoffs(cat, sim, reaction_id: str) -> List[str]:
    tradeoffs = []
    if cat.cost_per_kg > 5000:
        tradeoffs.append(
            f"High cost (${cat.cost_per_kg:,.0f}/kg) limits large-scale deployment; "
            "consider alloying with cheaper base metals."
        )
    if cat.abundance_ppm < 1.0:
        tradeoffs.append(
            f"Low crustal abundance ({cat.abundance_ppm} ppm) raises supply-chain concerns "
            "for terawatt-scale applications."
        )
    if cat.stability_score < 0.75:
        tradeoffs.append(
            f"Moderate stability score ({cat.stability_score:.2f}) suggests susceptibility "
            "to surface restructuring or dissolution under operating conditions."
        )
    if abs(sim.adsorption_energy) > 0.30:
        tradeoffs.append(
            f"Adsorption energy of {sim.adsorption_energy:.3f} eV deviates from the "
            "volcano optimum, limiting turnover frequency."
        )
    if not tradeoffs:
        tradeoffs.append("No major trade-offs identified at this level of approximation.")
    return tradeoffs[:3]


def _template_next_steps(cat, reaction_id: str) -> List[str]:
    steps = [
        f"Perform DFT slab calculations (VASP or Quantum ESPRESSO) on the {cat.formula}(111) "
        "surface to obtain validated adsorption energies.",
        f"Explore binary alloys: add 10–25 at.% of a cheap metal "
        "(Ni, Fe, or Co) to tune the d-band centre towards 0 eV (ΔG_H).",
        "Run microkinetic modelling (using CatMAP or mkm-master) to translate "
        "adsorption energies into predicted turnover frequencies and onset potentials.",
    ]
    if reaction_id == "HER":
        steps.append("Test in 0.5 M H₂SO₄ and 1 M KOH to assess pH-dependent activity.")
    elif reaction_id == "CO2RR":
        steps.append("Characterise selectivity via gas-chromatography; screen electrolyte (KHCO₃ vs. KCl).")
    elif reaction_id in ("OER", "ORR"):
        steps.append("Measure stability via accelerated stress tests (10 000 CV cycles at 50 mV/s).")
    return steps[:3]


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "QCatalyst API", "version": "0.1.0"}
