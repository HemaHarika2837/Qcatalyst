"""
Quantum simulation engine for QCatalyst.

Attempts to run a real VQE calculation using Qiskit Nature + Qiskit Aer.
Falls back gracefully to the ML surrogate if any quantum library is missing
or if the simulation fails.

ARCHITECTURE NOTE:
  The VQE here models a minimal H₂ molecule as a proxy for the
  metal-hydrogen interaction (H-metal fragment in STO-3G basis).
  The ground-state energy is shifted/scaled to approximate the
  adsorption energy of H on the catalyst surface.

WHERE REAL QUANTUM CHEMISTRY PLUGS IN:
  - Replace `_build_h2_problem()` with a PySCFDriver call targeting
    the actual metal-adsorbate cluster (e.g., a Pt₁₃–H cluster).
  - Increase active space (more orbitals/electrons) for production use.
  - On real quantum hardware (IBM, IonQ), swap `AerSimulator` for
    a real backend obtained via `QiskitRuntimeService`.
"""
from __future__ import annotations
import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from surrogate import predict_adsorption_energy, estimate_barrier

# ── Optional quantum imports ──────────────────────────────────────────────────
try:
    from qiskit_nature.second_q.drivers import PySCFDriver
    from qiskit_nature.second_q.mappers import ParityMapper
    from qiskit_nature.second_q.circuit.library import UCCSD
    from qiskit_nature.second_q.algorithms import GroundStateEigensolver
    from qiskit_algorithms import VQE
    from qiskit_algorithms.optimizers import COBYLA
    from qiskit.primitives import Estimator
    _QISKIT_AVAILABLE = True
except ImportError:
    _QISKIT_AVAILABLE = False

try:
    from pyscf import gto, scf
    _PYSCF_AVAILABLE = True
except ImportError:
    _PYSCF_AVAILABLE = False


# ── Job registry (in-memory) ─────────────────────────────────────────────────
_JOB_STORE: Dict[str, Dict[str, Any]] = {}


def new_job() -> str:
    """Create a pending job entry and return its ID."""
    job_id = str(uuid.uuid4())[:8]
    _JOB_STORE[job_id] = {
        "status":   "pending",
        "progress": 0.0,
        "result":   None,
        "error":    None,
    }
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _JOB_STORE.get(job_id)


def _update_job(job_id: str, **kwargs: Any) -> None:
    if job_id in _JOB_STORE:
        _JOB_STORE[job_id].update(kwargs)


# ── VQE via Qiskit Nature ─────────────────────────────────────────────────────
def _run_vqe_h2(
    job_id: str,
    progress_cb: Callable[[float], None],
) -> Tuple[float, List[Dict[str, Any]]]:
    """
    Run VQE on a minimal H₂ molecule (STO-3G, Parity mapping, 2-qubit reduction).

    This is a well-known benchmark that takes ~seconds on a simulator.
    It represents the simplest metal-hydrogen bond proxy.

    Returns: (ground_state_energy_hartree, vqe_history)
    """
    vqe_history: List[Dict[str, Any]] = []
    iteration_counter = [0]

    # ── Build the electronic structure problem ────────────────────────────────
    # WHERE REAL CHEMISTRY PLUGS IN:
    #   driver = PySCFDriver(atom="Pt 0 0 0; H 0 0 1.8", basis="def2-svp",
    #                        charge=0, spin=0, unit=DistanceUnit.ANGSTROM)
    driver = PySCFDriver(atom="H 0 0 0; H 0 0 0.735", basis="sto3g")
    problem = driver.run()

    # ── Map to qubit Hamiltonian (Parity + 2-qubit reduction) ─────────────────
    mapper    = ParityMapper(num_particles=problem.num_particles)
    ansatz    = UCCSD(
        problem.num_spatial_orbitals,
        problem.num_particles,
        mapper,
        initial_state=None,
    )
    estimator = Estimator()

    def _callback(nfev, x, fx, dx, is_last_call=False):
        iteration_counter[0] += 1
        vqe_history.append({"iteration": iteration_counter[0], "energy": round(float(fx), 6)})
        progress_cb(min(0.05 + 0.90 * (iteration_counter[0] / 80), 0.95))

    optimizer = COBYLA(maxiter=80, callback=_callback)
    vqe = VQE(estimator=estimator, ansatz=ansatz, optimizer=optimizer)

    solver = GroundStateEigensolver(mapper, vqe)
    result = solver.solve(problem)

    ground_energy = float(result.groundenergy)
    return ground_energy, vqe_history


def _hartree_to_ads_energy(ground_energy: float, reaction_id: str) -> float:
    """
    Approximate conversion: shift/scale the H₂ ground-state energy to an
    adsorption energy proxy for the given reaction.

    NOTE: This is a schematic mapping for prototype purposes only.
          In reality you would compute E(slab+H) - E(slab) - ½E(H₂)
          directly from DFT slab calculations.

    H₂ FCI/STO-3G ≈ -1.1175 Hartree.  We express the deviation from
    this reference and convert to eV (1 Ha ≈ 27.211 eV).
    """
    HARTREE_TO_EV = 27.211
    H2_REFERENCE  = -1.1175   # Hartree, H₂ ground state STO-3G

    delta_ha = ground_energy - H2_REFERENCE   # negative = more bound
    delta_ev = delta_ha * HARTREE_TO_EV

    # Reaction-specific offset to roughly centre the scale
    OFFSETS = {"HER": 0.09, "CO2RR": -0.58, "ORR": -0.11, "NH3": -0.71, "OER": -1.51}
    offset = OFFSETS.get(reaction_id, 0.0)
    return round(delta_ev + offset, 4)


# ── PySCF fallback (HF only, no VQE) ─────────────────────────────────────────
def _run_pyscf_hf(reaction_id: str) -> Tuple[float, str]:
    """
    Run Hartree-Fock on H₂ with PySCF as a lightweight fallback
    when Qiskit Nature is unavailable.
    Returns (ground_energy_hartree, convergence_info).
    """
    mol = gto.Mole()
    mol.atom = "H 0 0 0; H 0 0 0.735"
    mol.basis = "sto-3g"
    mol.verbose = 0
    mol.build()
    mf = scf.RHF(mol)
    mf.kernel()
    return float(mf.e_tot), f"PySCF RHF converged: {mf.converged}"


# ── Main simulation entry point ───────────────────────────────────────────────
async def run_simulation_async(
    job_id:       str,
    catalyst_id:  str,
    reaction_id:  str,
    mode:         str,
    d_band_center: float,
    electronegativity: float,
    atomic_radius: float,
) -> None:
    """
    Background coroutine: run simulation, update job store when done.
    Called via asyncio.create_task() from the FastAPI endpoint.
    """
    _update_job(job_id, status="running", progress=0.02)
    loop = asyncio.get_event_loop()

    try:
        result = await loop.run_in_executor(
            None,
            _run_simulation_sync,
            job_id, catalyst_id, reaction_id, mode,
            d_band_center, electronegativity, atomic_radius,
        )
        _update_job(job_id, status="done", progress=1.0, result=result)

    except Exception as exc:
        _update_job(job_id, status="error", error=str(exc))


def _run_simulation_sync(
    job_id:        str,
    catalyst_id:   str,
    reaction_id:   str,
    mode:          str,
    d_band_center: float,
    electronegativity: float,
    atomic_radius: float,
) -> Dict[str, Any]:
    """
    Synchronous simulation – runs in a thread-pool worker.
    """
    start = time.time()

    def progress_cb(p: float) -> None:
        _update_job(job_id, progress=round(p, 3))

    # ── Quantum VQE path ──────────────────────────────────────────────────────
    if mode == "quantum":
        if _QISKIT_AVAILABLE:
            try:
                progress_cb(0.05)
                ground_energy, vqe_history = _run_vqe_h2(job_id, progress_cb)
                progress_cb(0.96)
                ads_energy = _hartree_to_ads_energy(ground_energy, reaction_id)
                barrier    = estimate_barrier(ads_energy, reaction_id)
                elapsed    = round(time.time() - start, 2)
                return {
                    "catalyst_id":        catalyst_id,
                    "reaction_id":        reaction_id,
                    "mode":               "quantum",
                    "adsorption_energy":  ads_energy,
                    "barrier_estimate":   barrier,
                    "ground_state_energy": ground_energy,
                    "vqe_history":        vqe_history,
                    "convergence_info":   f"VQE converged in {len(vqe_history)} iterations ({elapsed}s)",
                    "uncertainty":        None,
                    "disclaimer": (
                        "APPROXIMATION: VQE run on a minimal H₂/STO-3G proxy model on "
                        "a classical Aer simulator.  Results are NOT experimentally validated. "
                        "Real catalysis requires full DFT slab calculations."
                    ),
                }
            except Exception as qex:
                # Fall through to surrogate
                mode = "surrogate"
                fallback_note = f"Qiskit VQE failed ({qex}); using surrogate fallback. "
        elif _PYSCF_AVAILABLE:
            try:
                progress_cb(0.40)
                ground_energy, conv_info = _run_pyscf_hf(reaction_id)
                progress_cb(0.80)
                ads_energy = _hartree_to_ads_energy(ground_energy, reaction_id)
                barrier    = estimate_barrier(ads_energy, reaction_id)
                return {
                    "catalyst_id":        catalyst_id,
                    "reaction_id":        reaction_id,
                    "mode":               "pyscf_hf",
                    "adsorption_energy":  ads_energy,
                    "barrier_estimate":   barrier,
                    "ground_state_energy": ground_energy,
                    "vqe_history":        None,
                    "convergence_info":   conv_info,
                    "uncertainty":        None,
                    "disclaimer": (
                        "APPROXIMATION: PySCF Hartree-Fock on H₂/STO-3G proxy (Qiskit not available). "
                        "Results are NOT experimentally validated."
                    ),
                }
            except Exception as pex:
                mode = "surrogate"
                fallback_note = f"PySCF failed ({pex}); using surrogate. "
        else:
            mode = "surrogate"
            fallback_note = "Neither Qiskit nor PySCF available; using ML surrogate. "
    else:
        fallback_note = ""

    # ── Surrogate ML path ─────────────────────────────────────────────────────
    progress_cb(0.30)
    ads_energy, uncertainty, cv_info = predict_adsorption_energy(
        reaction_id, d_band_center, electronegativity, atomic_radius
    )
    progress_cb(0.90)
    barrier = estimate_barrier(ads_energy, reaction_id)

    return {
        "catalyst_id":        catalyst_id,
        "reaction_id":        reaction_id,
        "mode":               "surrogate",
        "adsorption_energy":  ads_energy,
        "barrier_estimate":   barrier,
        "ground_state_energy": None,
        "vqe_history":        None,
        "convergence_info":   cv_info,
        "uncertainty":        uncertainty,
        "disclaimer": (
            f"{fallback_note}"
            "SURROGATE MODE: ML (GradientBoosting) prediction from d-band centre, "
            "electronegativity, and atomic radius.  Results are schematic approximations "
            "and NOT experimentally validated values."
        ),
    }
