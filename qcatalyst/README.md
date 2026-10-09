# ⚛ QCatalyst — Quantum Simulation-based Catalyst Design System

> **Prototype / Research Tool** — All simulation results are simplified approximations.
> Not experimentally validated. See disclaimer below.

## What It Does

QCatalyst helps researchers screen catalyst materials for key electrochemical reactions:

| Reaction | Descriptor | Optimum |
|----------|------------|---------|
| HER – Hydrogen Evolution | ΔG_H (H adsorption) | 0.0 eV |
| CO2RR – CO₂ Reduction | ΔE_CO (CO adsorption) | −0.67 eV |
| ORR – Oxygen Reduction | ΔE_OH (OH adsorption) | −0.20 eV |
| NH₃ Synthesis (Haber-Bosch) | ΔE_N2 (N₂ adsorption) | −0.80 eV |
| OER – Oxygen Evolution | ΔE_OH (OH adsorption) | −1.60 eV |

The system:
1. Predicts adsorption energies via an ML surrogate **or** a real VQE quantum circuit
2. Scores every catalyst on activity, stability, selectivity, cost, and abundance
3. Renders a volcano plot, radar chart, energy profile, VQE convergence, and 3D cluster viewer
4. Generates an AI-written recommendation for the top-ranked material

---

## Project Structure

```
qcatalyst/
├── backend/
│   ├── main.py          – FastAPI app (all endpoints)
│   ├── models.py        – Pydantic request/response schemas
│   ├── quantum.py       – VQE engine (Qiskit Nature) + job tracking
│   ├── surrogate.py     – ML surrogate (GradientBoosting + bootstrap uncertainty)
│   ├── scoring.py       – Multi-criteria ranking + volcano + energy profile
│   ├── data/
│   │   └── catalysts.json  – 32-material dataset
│   ├── tests/
│   │   └── test_scoring.py – pytest test suite
│   └── requirements.txt
├── frontend/
│   ├── index.html       – Single-page app (no build step)
│   ├── app.js           – All UI logic (plain ES2020)
│   └── style.css        – Dark/light scientific dashboard theme
└── README.md
```

---

## Setup & Running

### 1 — Create and activate a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac / Linux
python -m venv venv
source venv/bin/activate
```

### 2 — Install dependencies

```bash
pip install -r backend/requirements.txt
```

> **Minimal install** (surrogate mode only, fast):
> ```bash
> pip install fastapi uvicorn[standard] numpy pandas scikit-learn pydantic pytest httpx
> ```
>
> **Full install** (enables Quantum VQE mode):
> ```bash
> pip install -r backend/requirements.txt
> ```
> Qiskit + PySCF take a few minutes to install and require ~1 GB disk.

### 3 — Start the backend

```bash
# From the qcatalyst/ directory:
uvicorn backend.main:app --reload --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 4 — Open the frontend

In a **second terminal** (also from `qcatalyst/`):

```bash
python -m http.server 5500 --directory frontend
```

Then open **http://localhost:5500** in your browser.

### 5 — (Optional) Enable AI recommendations via Anthropic

```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-...

# Mac / Linux
export ANTHROPIC_API_KEY=sk-ant-...
```

Without the key, QCatalyst falls back to template-based recommendations automatically.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/reactions` | List all target reactions |
| GET | `/api/catalysts` | List all catalysts |
| POST | `/api/catalysts` | Add a custom catalyst |
| POST | `/api/simulate` | Start a simulation (returns job_id) |
| GET | `/api/jobs/{id}` | Poll job status + result |
| POST | `/api/rank` | Run full screening + return leaderboard |
| GET | `/api/report?reaction=HER` | Download CSV of ranked results |
| POST | `/api/explain` | Get AI recommendation for top catalyst |

---

## Running Tests

```bash
# From qcatalyst/
pytest backend/tests/test_scoring.py -v
```

---

## Demo Walkthrough — HER Screening

1. **Open** http://localhost:5500
2. **Select** "Hydrogen Evolution Reaction (HER)" from the dropdown
3. **Mode:** leave on "Fast Surrogate (ML)" for instant results
4. **Click** "▶ Run Screening"

### What you'll see:
- **Leaderboard:** Ranked list of all 32 catalysts
  - **Pt** appears near the top — the established benchmark (ΔG_H ≈ −0.09 eV, near-zero)
  - **MoS₂** ranks highly with a composite score competitive with Pt at ~$120/kg vs $32,000/kg
  - **NiMo** and **CoP** also appear in the top tier — earth-abundant alternatives
- **Volcano Plot:** Points cluster around the Sabatier peak; Pt, Ru, MoS₂ near the top
- **Radar Chart:** Top-3 compared across all 5 criteria — shows Pt's activity advantage vs MoS₂'s cost advantage
- **Click any row** to see the energy profile, 3D cluster, and AI recommendation

### Try Quantum VQE mode:
1. Switch to "🔬 Quantum VQE" in the sidebar
2. Click "▶ Run Screening" — runs a real VQE circuit on H₂/STO-3G via Qiskit Aer
3. A VQE convergence plot appears in the detail view
4. *(Falls back to surrogate automatically if Qiskit is not installed)*

---

## Where Real DFT / Quantum Hardware Would Plug In

The codebase is annotated with `# WHERE REAL ... PLUGS IN` comments. Key locations:

| File | Location | What to replace |
|------|----------|-----------------|
| `quantum.py` | `_build_h2_problem()` | Use full metal-adsorbate cluster with `PySCFDriver(atom="Pt13 cluster...")` |
| `quantum.py` | `_hartree_to_ads_energy()` | Replace with `E(slab+ads) − E(slab) − ½E(H₂)` from DFT |
| `quantum.py` | `AerSimulator` | Swap for `QiskitRuntimeService` backend for real quantum hardware |
| `surrogate.py` | `predict()` | Replace with ASE/GPAW or VASP workflow returning DFT adsorption energy |
| `scoring.py` | `build_energy_profile()` | Replace schematic profile with NEB/CI-NEB data from DFT |
| `scoring.py` | `estimate_barrier()` | Replace BEP approximation with computed activation energies |

---

## ⚠ Disclaimer

All simulation results in QCatalyst are **prototype approximations**:

- The **VQE** runs on a minimal H₂ molecule (2 electrons, STO-3G basis) as a proxy for
  the metal-hydrogen interaction. It does **not** model a real metal surface.
- The **surrogate model** is a GradientBoosting regression over 32 data points — a tiny
  training set by ML standards.
- **Adsorption energies, barriers, and scores are NOT experimentally validated.**
- Do **not** use these results for production catalyst selection without rigorous DFT
  slab calculations and experimental verification.

This tool is designed to demonstrate the architecture and workflow of a quantum-enhanced
catalyst screening platform, not to produce research-grade predictions.
