/* ════════════════════════════════════════════════════════════════════
   QCatalyst – Frontend Application
   Plain ES2020 JavaScript, no build step required.
   Backend expected at http://localhost:8000
════════════════════════════════════════════════════════════════════ */

const API = "http://localhost:8000";

/* ── State ─────────────────────────────────────────────────────── */
const state = {
  reactions:    [],
  catalysts:    [],
  leaderboard:  [],
  selectedIdx:  null,        // index into leaderboard
  currentRxnId: null,
  weights: {
    activity:    0.35,
    stability:   0.20,
    selectivity: 0.20,
    cost:        0.15,
    abundance:   0.10,
  },
};

/* ── Colour palette for plots ───────────────────────────────────── */
const PALETTE = [
  "#58a6ff","#3fb950","#ffa657","#f85149","#bc8cff",
  "#79c0ff","#56d364","#ffc680","#ff7b72","#d2a8ff",
  "#a5d6ff","#7ee787","#ffe585","#ffa198","#efb8ff",
];

/* ══════════════════════════════════════════════════════════════════
   Bootstrap
══════════════════════════════════════════════════════════════════ */
document.addEventListener("DOMContentLoaded", async () => {
  setupThemeToggle();
  setupModal();
  setupWeightSliders();
  setupCustomCatalystForm();

  await loadReactions();
  await loadCatalysts();

  document.getElementById("btnRun").addEventListener("click", runScreening);
  document.getElementById("btnDownloadCSV").addEventListener("click", downloadCSV);

  // Auto-select HER for the demo
  const sel = document.getElementById("selectReaction");
  const her = [...sel.options].find(o => o.value === "HER");
  if (her) { sel.value = "HER"; onReactionChange(); }
});

/* ══════════════════════════════════════════════════════════════════
   Theme
══════════════════════════════════════════════════════════════════ */
function setupThemeToggle() {
  const btn = document.getElementById("btnTheme");
  btn.addEventListener("click", () => {
    const html = document.documentElement;
    const isDark = html.dataset.theme === "dark";
    html.dataset.theme = isDark ? "light" : "dark";
    btn.textContent = isDark ? "🌙" : "☀️";
    // Re-render plots with updated theme
    if (state.leaderboard.length) renderAllPlots();
  });
}

function isDark() { return document.documentElement.dataset.theme === "dark"; }

function plotBg()     { return isDark() ? "#161b22" : "#ffffff"; }
function plotPaper()  { return isDark() ? "#0d1117" : "#f6f8fa"; }
function plotFont()   { return isDark() ? "#8b949e" : "#57606a"; }
function plotGrid()   { return isDark() ? "#30363d" : "#d0d7de"; }

/* ══════════════════════════════════════════════════════════════════
   Modal
══════════════════════════════════════════════════════════════════ */
function setupModal() {
  const overlay = document.getElementById("modalOverlay");
  document.getElementById("btnHowItWorks").addEventListener("click", () => overlay.classList.remove("hidden"));
  document.getElementById("btnCloseModal").addEventListener("click", () => overlay.classList.add("hidden"));
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.classList.add("hidden"); });
}

/* ══════════════════════════════════════════════════════════════════
   Weight sliders
══════════════════════════════════════════════════════════════════ */
const WEIGHT_LABELS = {
  activity:    "Activity",
  stability:   "Stability",
  selectivity: "Selectivity",
  cost:        "Cost",
  abundance:   "Abundance",
};

function setupWeightSliders() {
  const container = document.getElementById("weightSliders");
  container.innerHTML = "";
  for (const [key, label] of Object.entries(WEIGHT_LABELS)) {
    const val = state.weights[key];
    container.insertAdjacentHTML("beforeend", `
      <div class="slider-row" id="slider-row-${key}">
        <div class="slider-label-row">
          <span class="slider-label">${label}</span>
          <span class="slider-value" id="sv-${key}">${(val * 100).toFixed(0)}%</span>
        </div>
        <input type="range" min="0" max="1" step="0.01" value="${val}"
               id="sl-${key}" data-key="${key}" />
      </div>
    `);
    document.getElementById(`sl-${key}`).addEventListener("input", e => {
      state.weights[e.target.dataset.key] = parseFloat(e.target.value);
      document.getElementById(`sv-${e.target.dataset.key}`).textContent =
        (parseFloat(e.target.value) * 100).toFixed(0) + "%";
    });
  }
}

/* ══════════════════════════════════════════════════════════════════
   Load reactions & catalysts
══════════════════════════════════════════════════════════════════ */
async function loadReactions() {
  const data = await apiFetch("/api/reactions");
  state.reactions = data;
  const sel = document.getElementById("selectReaction");
  sel.innerHTML = data.map(r => `<option value="${r.id}">${r.name}</option>`).join("");
  sel.addEventListener("change", onReactionChange);
  onReactionChange();
}

function onReactionChange() {
  const rxnId = document.getElementById("selectReaction").value;
  state.currentRxnId = rxnId;
  const rxn = state.reactions.find(r => r.id === rxnId);
  if (!rxn) return;
  document.getElementById("reactionInfo").innerHTML = `
    <p>${rxn.description}</p>
    <p style="margin-top:6px">Key descriptor:<br>
      <span class="descriptor">${rxn.descriptor} (optimum: ${rxn.optimum} eV)</span>
    </p>
  `;
}

async function loadCatalysts() {
  state.catalysts = await apiFetch("/api/catalysts");
}

/* ══════════════════════════════════════════════════════════════════
   Run Screening
══════════════════════════════════════════════════════════════════ */
async function runScreening() {
  const rxnId = document.getElementById("selectReaction").value;
  const mode  = "quantum";

  setRunning(true);
  setProgress(0.05, "Contacting backend…");
  showDisclaimer("");

  try {
    const body = {
      reaction_id: rxnId,
      mode,
      weights: state.weights,
    };
    setProgress(0.15, "Running simulations…");

    const result = await apiFetch("/api/rank", { method: "POST", body });

    setProgress(0.85, "Computing scores…");
    state.leaderboard  = result.leaderboard;
    state.currentRxnId = rxnId;
    state.selectedIdx  = null;

    setProgress(1.0, "Done ✓");
    showDisclaimer(result.disclaimer);
    renderLeaderboard();
    renderAllPlots();

    document.getElementById("btnDownloadCSV").disabled = false;

    // Auto-select rank #1 for detail view
    if (state.leaderboard.length > 0) {
      selectCatalyst(0);
    }
  } catch (err) {
    setProgress(0, "");
    showDisclaimer("⛔ Error: " + err.message);
  } finally {
    setRunning(false);
    setTimeout(() => hideProgress(), 1800);
  }
}

/* ══════════════════════════════════════════════════════════════════
   Leaderboard
══════════════════════════════════════════════════════════════════ */
function renderLeaderboard() {
  const rxn  = state.reactions.find(r => r.id === state.currentRxnId);
  document.getElementById("leaderboardSubtitle").textContent =
    rxn ? `${rxn.name} — ${state.leaderboard.length} candidates` : "";

  const tbody = document.getElementById("tblBody");
  if (!state.leaderboard.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-msg">No results.</td></tr>`;
    return;
  }

  tbody.innerHTML = state.leaderboard.map((row, idx) => {
    const cat = row.catalyst;
    const sim = row.simulation;
    const rank = row.rank;
    const rankClass = rank <= 3 ? `rank-${rank}` : "rank-n";
    const modeClass = sim.mode.replace("_", "");
    const scoreColor = scoreToColor(row.composite_score);
    return `
      <tr data-idx="${idx}" onclick="selectCatalyst(${idx})">
        <td><span class="rank-badge ${rankClass}">${rank}</span></td>
        <td><strong>${cat.formula}</strong></td>
        <td>${cat.name}</td>
        <td style="font-family:monospace">${sim.adsorption_energy.toFixed(3)}</td>
        <td style="font-family:monospace">${sim.barrier_estimate.toFixed(3)}</td>
        <td>${scoreBar(row.activity_score, "#58a6ff")}</td>
        <td>${scoreBar(row.stability_score, "#3fb950")}</td>
        <td>${scoreBar(row.cost_score, "#ffa657")}</td>
        <td>${scoreBar(row.composite_score, scoreColor)}</td>
        <td><span class="mode-chip ${sim.mode}">${sim.mode}</span></td>
      </tr>
    `;
  }).join("");
}

function scoreBar(val, color) {
  const pct = (val * 100).toFixed(0);
  return `<div class="score-bar">
    <div class="score-bar-bg">
      <div class="score-bar-fill" style="width:${pct}%;background:${color}"></div>
    </div>
    <span class="score-val" style="color:${color}">${pct}</span>
  </div>`;
}

function scoreToColor(s) {
  if (s >= 0.70) return "#3fb950";
  if (s >= 0.45) return "#ffa657";
  return "#f85149";
}

/* ══════════════════════════════════════════════════════════════════
   Select catalyst → detail view + AI
══════════════════════════════════════════════════════════════════ */
async function selectCatalyst(idx) {  state.selectedIdx = idx;

  // Highlight row
  document.querySelectorAll("#tblBody tr").forEach(tr => {
    tr.classList.toggle("selected", parseInt(tr.dataset.idx) === idx);
  });

  const row = state.leaderboard[idx];
  const cat = row.catalyst;
  const sim = row.simulation;

  // Update detail header
  document.getElementById("detailTitle").textContent = `${cat.name} (${cat.formula})`;
  document.getElementById("detailSubtitle").textContent =
    `Rank #${row.rank} · ${cat.structure_type} · Score ${row.composite_score.toFixed(3)}`;

  // Energy profile plot
  renderEnergyProfile(row);

  // 3D viewer
  render3DViewer(cat);
}

/* ══════════════════════════════════════════════════════════════════
   Volcano Plot  –  clean version
   • No text labels on dots (they caused the clutter)
   • Top-5 catalysts get a separate labelled trace so their names
     appear cleanly with enough spacing
   • All other catalysts: hover tooltip only
   • Dots sized by composite score, coloured by rank tier
══════════════════════════════════════════════════════════════════ */
function renderVolcanoPlot() {
  const rxn     = state.reactions.find(r => r.id === state.currentRxnId);
  const optimum = rxn ? rxn.optimum : 0;
  const lb      = state.leaderboard;

  // ── Volcano background curve ────────────────────────────────────
  const xMin = Math.min(...lb.map(r => r.volcano_x)) - 0.6;
  const xMax = Math.max(...lb.map(r => r.volcano_x)) + 0.6;
  const curveX = [], curveY = [];
  for (let x = xMin; x <= xMax; x += 0.015) {
    curveX.push(parseFloat(x.toFixed(3)));
    curveY.push(Math.exp(-Math.pow(x - optimum, 2) / (2 * 0.4 * 0.4)));
  }

  // ── Colour by rank tier ─────────────────────────────────────────
  function rankColor(rank) {
    if (rank === 1) return "#ffd700";  // gold
    if (rank === 2) return "#c0c0c0";  // silver
    if (rank === 3) return "#cd7f32";  // bronze
    if (rank <= 8)  return "#58a6ff";  // blue — top tier
    if (rank <= 16) return "#3fb950";  // green — mid
    return "#6e7681";                  // grey — lower
  }

  // ── Build rich hover text ───────────────────────────────────────
  const hoverText = lb.map(r => {
    const c = r.catalyst, s = r.simulation;
    return (
      `<b>#${r.rank} ${c.name}</b><br>` +
      `Formula: ${c.formula}<br>` +
      `Adsorption energy: ${s.adsorption_energy.toFixed(3)} eV<br>` +
      `Barrier: ${s.barrier_estimate.toFixed(3)} eV<br>` +
      `Composite score: ${r.composite_score.toFixed(3)}<br>` +
      `Cost: $${c.cost_per_kg.toLocaleString()}/kg<br>` +
      `Abundance: ${c.abundance_ppm} ppm`
    );
  });

  // ── Main scatter (no text, hover only) ─────────────────────────
  const mainScatter = {
    x: lb.map(r => r.volcano_x),
    y: lb.map(r => r.volcano_y),
    mode: "markers",
    name: "Catalysts",
    marker: {
      color:  lb.map(r => rankColor(r.rank)),
      size:   lb.map(r => 9 + r.composite_score * 13),
      line:   { color: isDark() ? "#0d1117" : "#ffffff", width: 1.5 },
      opacity: 0.9,
    },
    hovertemplate: "%{customdata}<extra></extra>",
    customdata: hoverText,
    showlegend: false,
  };

  // ── Top-5 labelled overlay (text labels only for top 5) ─────────
  const top5 = lb.slice(0, 5);
  const labelledTrace = {
    x: top5.map(r => r.volcano_x),
    y: top5.map(r => r.volcano_y),
    mode: "markers+text",
    name: "Top 5",
    text: top5.map(r => r.catalyst.formula),
    textposition: top5.map((r, i) => {
      // stagger positions to avoid overlap
      return i % 2 === 0 ? "top center" : "bottom center";
    }),
    textfont: { size: 11, color: isDark() ? "#e6edf3" : "#1f2328", family: "monospace" },
    marker: {
      color:  top5.map(r => rankColor(r.rank)),
      size:   top5.map(r => 11 + r.composite_score * 13),
      line:   { color: isDark() ? "#e6edf3" : "#1f2328", width: 2 },
    },
    hovertemplate: "%{customdata}<extra></extra>",
    customdata: top5.map(r => {
      const c = r.catalyst, s = r.simulation;
      return (
        `<b>#${r.rank} ${c.name}</b><br>` +
        `Adsorption energy: ${s.adsorption_energy.toFixed(3)} eV<br>` +
        `Score: ${r.composite_score.toFixed(3)}<br>` +
        `Cost: $${c.cost_per_kg.toLocaleString()}/kg`
      );
    }),
    showlegend: true,
  };

  // ── Volcano curve ───────────────────────────────────────────────
  const curveLine = {
    x: curveX, y: curveY,
    mode: "lines",
    name: "Volcano curve",
    line: { color: "rgba(88,166,255,0.35)", width: 2, dash: "dot" },
    hoverinfo: "skip",
    showlegend: true,
  };

  // ── Optimum star ────────────────────────────────────────────────
  const optimumStar = {
    x: [optimum], y: [1.0],
    mode: "markers",
    name: "Optimum",
    marker: { color: "#3fb950", size: 16, symbol: "star", line: { color: "#fff", width: 1.5 } },
    hovertemplate: `<b>Volcano optimum</b><br>ΔE = ${optimum} eV<extra></extra>`,
    showlegend: true,
  };

  const layout = {
    paper_bgcolor: plotPaper(),
    plot_bgcolor:  plotBg(),
    font: { color: plotFont(), size: 12 },
    margin: { l: 60, r: 30, t: 30, b: 60 },
    xaxis: {
      title: { text: rxn ? rxn.descriptor.split("(")[0].trim() + " (eV)" : "Descriptor (eV)", standoff: 12 },
      gridcolor: plotGrid(), zerolinecolor: plotGrid(), zeroline: true,
      zerolinewidth: 1,
    },
    yaxis: {
      title: { text: "Activity (Sabatier score)", standoff: 12 },
      range: [-0.02, 1.12],
      gridcolor: plotGrid(), zerolinecolor: plotGrid(),
    },
    legend: {
      bgcolor: isDark() ? "rgba(22,27,34,0.85)" : "rgba(255,255,255,0.85)",
      bordercolor: plotGrid(), borderwidth: 1,
      font: { size: 11 }, x: 0.01, y: 0.99, xanchor: "left", yanchor: "top",
    },
    hovermode: "closest",
    hoverlabel: {
      bgcolor:     isDark() ? "#1c2128" : "#ffffff",
      bordercolor: isDark() ? "#58a6ff"  : "#0969da",
      font: {
        size:   13,
        color:  isDark() ? "#e6edf3" : "#1f2328",
        family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      },
      align: "left",
    },
  };

  Plotly.react("plotVolcano", [curveLine, mainScatter, labelledTrace, optimumStar],
    layout, { responsive: true, displaylogo: false });
}

/* ══════════════════════════════════════════════════════════════════
   Radar Chart (top 3)
══════════════════════════════════════════════════════════════════ */
function renderRadarChart() {
  const top3   = state.leaderboard.slice(0, 3);
  const cats   = ["Activity", "Stability", "Selectivity", "Cost", "Abundance"];
  const keyMap = ["activity_score", "stability_score", "selectivity_score", "cost_score", "abundance_score"];

  const traces = top3.map((row, i) => {
    const values = keyMap.map(k => row[k]);
    return {
      type: "scatterpolar",
      r: [...values, values[0]],
      theta: [...cats, cats[0]],
      name: row.catalyst.formula,
      fill: "toself",
      fillcolor: PALETTE[i].replace(")", ",0.15)").replace("rgb", "rgba").replace("#", ""),
      line: { color: PALETTE[i], width: 2 },
      opacity: 0.85,
    };
  });

  // Fix fillcolor for hex values
  top3.forEach((_, i) => {
    traces[i].fillcolor = hexToRgba(PALETTE[i], 0.15);
    traces[i].line.color = PALETTE[i];
  });

  const layout = {
    paper_bgcolor: plotPaper(),
    plot_bgcolor:  plotBg(),
    font: { color: plotFont(), size: 10 },
    margin: { l: 30, r: 30, t: 30, b: 30 },
    polar: {
      bgcolor: plotBg(),
      radialaxis: {
        visible: true, range: [0, 1], tickfont: { size: 9 },
        gridcolor: plotGrid(), linecolor: plotGrid(),
      },
      angularaxis: { gridcolor: plotGrid(), linecolor: plotGrid() },
    },
    legend: { bgcolor: "transparent", font: { size: 10 }, orientation: "h", y: -0.1 },
    showlegend: true,
  };

  Plotly.react("plotRadar", traces, layout, { responsive: true, displaylogo: false });
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1,3), 16);
  const g = parseInt(hex.slice(3,5), 16);
  const b = parseInt(hex.slice(5,7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/* ══════════════════════════════════════════════════════════════════
   Energy Profile
══════════════════════════════════════════════════════════════════ */
function renderEnergyProfile(row) {
  const profile = row.energy_profile;
  const rxn     = state.reactions.find(r => r.id === state.currentRxnId);

  const steps  = profile.map((_, i) => i);
  const labels = profile.map(p => p.label || p.step);
  const energies = profile.map(p => p.energy);

  const trace = {
    x: steps, y: energies,
    text: labels,
    textposition: "top center",
    textfont: { size: 9, color: isDark() ? "#e6edf3" : "#1f2328" },
    mode: "lines+markers+text",
    line: { color: "#58a6ff", width: 2.5, shape: "spline" },
    marker: { color: energies.map(e => e < 0 ? "#3fb950" : "#ffa657"), size: 9 },
    fill: "tozeroy",
    fillcolor: "rgba(88,166,255,0.06)",
    hovertemplate: "<b>%{text}</b><br>ΔG = %{y:.3f} eV<extra></extra>",
  };

  const energyLayout = {
    paper_bgcolor: plotPaper(),
    plot_bgcolor:  plotBg(),
    font: { color: plotFont(), size: 10 },
    margin: { l: 50, r: 20, t: 20, b: 60 },
    xaxis: {
      tickvals: steps, ticktext: profile.map(p => p.step),
      tickangle: -30, tickfont: { size: 8 },
      gridcolor: plotGrid(), zerolinecolor: plotGrid(),
    },
    yaxis: {
      title: "Free Energy (eV)",
      gridcolor: plotGrid(), zerolinecolor: plotGrid(),
    },
    hovermode: "closest",
    hoverlabel: {
      bgcolor: isDark() ? "#1c2128" : "#ffffff",
      bordercolor: isDark() ? "#58a6ff" : "#0969da",
      font: { size: 12, color: isDark() ? "#e6edf3" : "#1f2328" },
    },
    shapes: [{
      type: "line", x0: 0, x1: steps[steps.length - 1],
      y0: 0, y1: 0,
      line: { color: "rgba(88,166,255,0.3)", dash: "dot", width: 1 },
    }],
  };

  Plotly.react("plotEnergy", [trace], energyLayout, { responsive: true, displaylogo: false });
}

/* ══════════════════════════════════════════════════════════════════
   VQE Convergence Plot
══════════════════════════════════════════════════════════════════ */
function renderVQEPlot(sim) {
  const note = document.getElementById("vqeNote");

  if (!sim.vqe_history || sim.vqe_history.length === 0) {
    note.textContent = sim.mode === "surrogate"
      ? "VQE convergence not available in surrogate mode."
      : "No VQE history returned.";
    Plotly.react("plotVQE", [], {
      paper_bgcolor: plotPaper(), plot_bgcolor: plotBg(),
      margin: { l: 50, r: 20, t: 20, b: 50 },
      annotations: [{
        text: sim.mode === "surrogate" ? "Surrogate mode – no VQE data" : "No VQE data",
        xref: "paper", yref: "paper", x: 0.5, y: 0.5,
        showarrow: false, font: { color: plotFont(), size: 13 },
      }],
    }, { responsive: true, displaylogo: false });
    return;
  }

  note.textContent = sim.convergence_info || "";
  const iters  = sim.vqe_history.map(h => h.iteration);
  const energs = sim.vqe_history.map(h => h.energy);

  const trace = {
    x: iters, y: energs,
    mode: "lines+markers",
    line: { color: "#bc8cff", width: 2 },
    marker: { color: "#bc8cff", size: 4 },
    name: "VQE Energy",
    hovertemplate: "Iter %{x}<br>E = %{y:.6f} Ha<extra></extra>",
  };

  const vqeLayout = {
    paper_bgcolor: plotPaper(),
    plot_bgcolor:  plotBg(),
    font: { color: plotFont(), size: 10 },
    margin: { l: 60, r: 20, t: 20, b: 50 },
    xaxis: { title: "Iteration", gridcolor: plotGrid(), zerolinecolor: plotGrid() },
    yaxis: { title: "Energy (Hartree)", gridcolor: plotGrid(), zerolinecolor: plotGrid() },
    hovermode: "closest",
    hoverlabel: {
      bgcolor: isDark() ? "#1c2128" : "#ffffff",
      bordercolor: isDark() ? "#bc8cff" : "#8250df",
      font: { size: 12, color: isDark() ? "#e6edf3" : "#1f2328" },
    },
  };

  Plotly.react("plotVQE", [trace], vqeLayout, { responsive: true, displaylogo: false });
}

/* ══════════════════════════════════════════════════════════════════
   3D Molecular Viewer (3Dmol.js)
   Generates a schematic cluster: metal slab + adsorbate
══════════════════════════════════════════════════════════════════ */
function render3DViewer(cat) {
  const container = document.getElementById("mol3dContainer");
  container.innerHTML = "";  // clear previous

  // Generate a schematic XYZ string for a small metal cluster + adsorbate
  const xyz = buildClusterXYZ(cat);
  const viewer = $3Dmol.createViewer(container, {
    backgroundColor: isDark() ? "0x0d1117" : "0xf6f8fa",
  });

  viewer.addModel(xyz, "xyz");

  // Style the metal atoms
  viewer.setStyle({ elem: metalElement(cat.formula) }, {
    sphere: { radius: 0.35, colorscheme: "Jmol" },
  });

  // Style the adsorbate (H in red, C in yellow, N in blue, O in orange)
  ["H","C","N","O","S"].forEach(elem => {
    viewer.setStyle({ elem }, {
      sphere: { radius: 0.28, colorscheme: "Jmol" },
    });
  });

  viewer.addStyle({}, { stick: { radius: 0.10, colorscheme: "Jmol" } });
  viewer.zoomTo();
  viewer.render();
  viewer.spin("y", 0.5);
}

function metalElement(formula) {
  // Extract the first element symbol from a formula like "Pt", "MoS2", "Ni3Fe"
  const m = formula.match(/^([A-Z][a-z]?)/);
  return m ? m[1] : "Pt";
}

function buildClusterXYZ(cat) {
  const elem = metalElement(cat.formula);
  // 3x3 surface slab (9 atoms) + adsorbate above centre
  const d = 2.77;   // approximate lattice constant (Å)
  let lines = [];
  const metalAtoms = [];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      metalAtoms.push([i * d, j * d, 0.0]);
    }
  }
  // Second layer offset
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      metalAtoms.push([i * d + d/2, j * d + d/2, -d * 0.816]);
    }
  }

  const totalAtoms = metalAtoms.length;
  const adsorbate  = getAdsorbateAtoms(cat);
  const totalLines = totalAtoms + adsorbate.length;

  lines.push(String(totalLines));
  lines.push(`${cat.name} cluster model (schematic – not DFT geometry)`);
  metalAtoms.forEach(([x, y, z]) => lines.push(`${elem}\t${x.toFixed(3)}\t${y.toFixed(3)}\t${z.toFixed(3)}`));
  adsorbate.forEach(([e, x, y, z]) => lines.push(`${e}\t${x.toFixed(3)}\t${y.toFixed(3)}\t${z.toFixed(3)}`));

  return lines.join("\n");
}

function getAdsorbateAtoms(cat) {
  // Place adsorbate above the centre of the slab (top bridge site)
  const cx = 2.77, cy = 2.77, cz = 1.8;
  const rxn = state.currentRxnId;
  if (rxn === "HER")  return [["H", cx, cy, cz]];
  if (rxn === "CO2RR") return [["C", cx, cy, cz], ["O", cx, cy, cz + 1.16], ["O", cx, cy, cz - 1.16]];
  if (rxn === "ORR")   return [["O", cx, cy, cz], ["H", cx, cy, cz + 0.97]];
  if (rxn === "NH3")   return [["N", cx, cy, cz], ["N", cx, cy, cz + 1.10]];
  if (rxn === "OER")   return [["O", cx, cy, cz], ["H", cx, cy, cz + 0.97]];
  return [["H", cx, cy, cz]];
}

/* ══════════════════════════════════════════════════════════════════
   Custom Catalyst Form
══════════════════════════════════════════════════════════════════ */
function setupCustomCatalystForm() {
  document.getElementById("formCustom").addEventListener("submit", async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      formula:           fd.get("formula"),
      name:              fd.get("name"),
      d_band_center:     parseFloat(fd.get("d_band_center")),
      cost_per_kg:       parseFloat(fd.get("cost_per_kg")),
      electronegativity: parseFloat(fd.get("electronegativity") || 2.0),
      atomic_radius:     parseFloat(fd.get("atomic_radius") || 1.4),
      adsorption_energies: {},
      stability_score:   0.6,
      selectivity_score: 0.6,
      abundance_ppm:     1.0,
    };

    try {
      const cat = await apiFetch("/api/catalysts", { method: "POST", body });
      state.catalysts.push(cat);
      e.target.reset();
      alert(`✅ Added ${cat.name} (${cat.formula}) to the library. Re-run screening to include it.`);
    } catch (err) {
      alert("Error adding catalyst: " + err.message);
    }
  });
}

/* ══════════════════════════════════════════════════════════════════
   CSV Download
══════════════════════════════════════════════════════════════════ */
function downloadCSV() {
  if (!state.currentRxnId) return;
  window.location.href = `${API}/api/report?reaction=${state.currentRxnId}`;
}

/* ══════════════════════════════════════════════════════════════════
   Helpers
══════════════════════════════════════════════════════════════════ */
function renderAllPlots() {
  if (!state.leaderboard.length) return;
  renderVolcanoPlot();
  renderRadarChart();
  if (state.selectedIdx !== null) {
    renderEnergyProfile(state.leaderboard[state.selectedIdx]);
  }
}

function setRunning(flag) {
  document.getElementById("btnRun").disabled = flag;
  document.getElementById("btnRun").textContent = flag ? "⏳ Running…" : "▶ Run Screening";
}

function setProgress(frac, label) {
  const wrap = document.getElementById("progressWrap");
  const bar  = document.getElementById("progressBar");
  const lbl  = document.getElementById("progressLabel");
  wrap.classList.remove("hidden");
  bar.style.width = `${Math.round(frac * 100)}%`;
  if (label) lbl.textContent = label;
}

function hideProgress() {
  document.getElementById("progressWrap").classList.add("hidden");
}

function showDisclaimer(text) {
  const banner = document.getElementById("bannerDisclaimer");
  if (!text) { banner.classList.add("hidden"); return; }
  document.getElementById("disclaimerText").textContent = text;
  banner.classList.remove("hidden");
}

async function apiFetch(path, opts = {}) {
  const options = {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json" },
  };
  if (opts.body) options.body = JSON.stringify(opts.body);

  const res = await fetch(API + path, options);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText} – ${detail}`);
  }
  return res.json();
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
