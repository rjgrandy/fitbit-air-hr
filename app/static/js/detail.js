// Workout detail page: zoomable HR chart with zone bands, compare overlays, zone breakdown,
// recovery HR and exports.
import uPlot from "../vendor/uPlot.esm.js";
import { toSeries, zoneBandsPlugin, niceSplits } from "./rowchart.js";
import {
  ZONE_LABEL, zoneColor, cssVar, setTimeZone, fmtDate, fmtTime, fmtDuration, fmtElapsed, fmtMinutes, prettyType, esc,
} from "./format.js";
import { pinButton, tagEditor, refreshTagSuggestions } from "./tags.js";

const $ = (id) => document.getElementById(id);
const OVERLAY_COLORS = ["#8b5cf6", "#06b6d4", "#f97316", "#ec4899", "#84cc16", "#eab308"];
const MAX_OVERLAYS = OVERLAY_COLORS.length;

const id = new URLSearchParams(location.search).get("id");
let W = null;                 // this workout (detail payload)
let settings = { y_min: 50, y_max: 200 };
let all = [];                 // list of all workouts (for the picker)
const overlays = [];          // [{ id, w, hr, color }]
let chart = null;

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
  return r.json();
}

function banner(text) { const b = $("banner"); b.textContent = text; b.hidden = false; }

// ---------------------------------------------------------------- chart ----
function xMax() {
  const own = (W.duration_s + W.post_window_s) / 60;
  return Math.max(own, ...overlays.map((o) => o.w.duration_s / 60), 1);
}

function buildChart() {
  const box = $("bigChart");
  chart?.destroy(); chart = null;
  const hasMain = W.t.some((t) => t >= 0 && t <= W.duration_s);
  const ph = box.querySelector(".placeholder");
  if (!hasMain && !overlays.length) { ph.textContent = "No HR data for this workout"; ph.hidden = false; return; }
  ph.hidden = true;

  const tables = [toSeries(W.t, W.bpm, W.duration_s + W.post_window_s)];
  for (const o of overlays) tables.push(toSeries(o.hr.t, o.hr.bpm, o.w.duration_s));
  // Align series on one x axis. NULL_EXPAND (2) widens each gap marker over the alignment
  // slots next to it, so lines still break across gaps once the series are interleaved.
  const data = uPlot.join(tables, tables.map(() => [0, 2]));

  const fg = cssVar("--muted"), grid = cssVar("--grid");
  const series = [{},
    { label: "This workout", stroke: cssVar("--line"), width: 2, spanGaps: false, points: { show: false } },
    ...overlays.map((o) => ({ label: overlayLabel(o.w), stroke: o.color, width: 1.5, spanGaps: false, points: { show: false } })),
  ];
  const endMin = W.duration_s / 60;
  const opts = {
    width: box.clientWidth, height: box.clientHeight,
    padding: [8, 10, 0, 0],
    legend: { show: false },
    cursor: { y: false, drag: { x: true, y: false, setScale: true }, points: { size: 7 } },
    scales: { x: { time: false, range: (u, min, max) => [min ?? 0, max ?? xMax()] }, y: { range: () => [settings.y_min, settings.y_max] } },
    axes: [
      { stroke: fg, grid: { stroke: grid }, ticks: { show: false }, font: "12px system-ui", values: (u, v) => v.map((m) => fmtElapsed(m * 60)) },
      { stroke: fg, grid: { stroke: grid }, ticks: { show: false }, font: "12px system-ui", size: 36, splits: (u) => niceSplits(settings.y_min, settings.y_max, u.bbox.height) },
    ],
    series,
    plugins: [zoneBandsPlugin(W.zones), endMarkerPlugin(endMin), readoutPlugin(), pinchZoomPlugin()],
  };
  chart = new uPlot(opts, data, box);
  chart.setScale("x", { min: 0, max: xMax() });
}

function endMarkerPlugin(endMin) {
  return { hooks: { draw: [(u) => {
    if (!(W.post_window_s && W.t.some((t) => t > W.duration_s))) return;
    const x = u.valToPos(endMin, "x", true);
    if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) return;
    const ctx = u.ctx, dpr = devicePixelRatio || 1;
    ctx.save();
    ctx.strokeStyle = cssVar("--muted"); ctx.setLineDash([4 * dpr, 4 * dpr]); ctx.lineWidth = dpr;
    ctx.beginPath(); ctx.moveTo(x, u.bbox.top); ctx.lineTo(x, u.bbox.top + u.bbox.height); ctx.stroke();
    ctx.fillStyle = cssVar("--muted"); ctx.font = `${11 * dpr}px system-ui`; ctx.setLineDash([]);
    ctx.fillText("end", x + 4 * dpr, u.bbox.top + 12 * dpr);
    ctx.restore();
  }] } };
}

function readoutPlugin() {
  return { hooks: { setCursor: [(u) => {
    const i = u.cursor.idx;
    const el = $("readout");
    if (i == null) { el.innerHTML = "&nbsp;"; return; }
    const parts = [`<b>${fmtElapsed(u.data[0][i] * 60)}</b>`];
    u.series.forEach((s, si) => {
      if (!si || !s.show) return;
      // Overlays have their own sample times, so read each series at its own nearest index.
      const j = u.cursor.idxs?.[si] ?? i;
      const v = j == null ? null : u.data[si][j];
      if (v != null) parts.push(`<span style="color:${s.stroke()}">●</span> ${v} bpm`);
    });
    el.innerHTML = parts.join(" &nbsp; ");
  }] } };
}

// Two-finger pinch to zoom (and pan) the x axis; one finger moves the readout without blocking scroll.
function pinchZoomPlugin() {
  return { hooks: { ready: [(u) => {
    let start = null;
    const over = u.over;
    const xAt = (clientX) => u.posToVal(clientX - over.getBoundingClientRect().left, "x");
    over.addEventListener("touchstart", (e) => {
      if (e.touches.length === 2) {
        const [a, b] = e.touches;
        start = { d: Math.abs(a.clientX - b.clientX) || 1, mid: xAt((a.clientX + b.clientX) / 2),
                  min: u.scales.x.min, max: u.scales.x.max };
        e.preventDefault();
      } else if (e.touches.length === 1) {
        const r = over.getBoundingClientRect();
        u.setCursor({ left: e.touches[0].clientX - r.left, top: e.touches[0].clientY - r.top });
      }
    }, { passive: false });
    over.addEventListener("touchmove", (e) => {
      if (e.touches.length === 2 && start) {
        e.preventDefault();
        const [a, b] = e.touches;
        const k = start.d / (Math.abs(a.clientX - b.clientX) || 1);
        const span = Math.min(Math.max((start.max - start.min) * k, 0.25), xMax());
        const r = over.getBoundingClientRect();
        const midFrac = ((a.clientX + b.clientX) / 2 - r.left) / r.width;
        let min = start.mid - span * midFrac;
        min = Math.max(0, Math.min(min, xMax() - span));
        u.setScale("x", { min, max: min + span });
      } else if (e.touches.length === 1) {
        const r = over.getBoundingClientRect();
        u.setCursor({ left: e.touches[0].clientX - r.left, top: e.touches[0].clientY - r.top });
      }
    }, { passive: false });
    over.addEventListener("touchend", (e) => { if (e.touches.length < 2) start = null; });
  }] } };
}

// --------------------------------------------------------------- legend ----
function overlayLabel(w) {
  return `${fmtDate(w.start_ts)} ${fmtTime(w.start_ts)} · ${prettyType(w.activity_type, w.display_name)}`;
}

function renderLegend() {
  const el = $("legend");
  if (!overlays.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<span class="item"><span class="sw" style="--c:${cssVar("--line")}"></span>This workout</span>` +
    overlays.map((o) => `<span class="item"><span class="sw" style="--c:${o.color}"></span>${esc(overlayLabel(o.w))}${o.hr?.t?.length ? "" : ' <span class="muted">(no HR data)</span>'}
      <button data-rm="${esc(o.id)}" title="Remove" aria-label="Remove ${esc(overlayLabel(o.w))}">×</button></span>`).join("");
  el.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => removeOverlay(b.dataset.rm));
}

async function addOverlay(oid) {
  if (overlays.length >= MAX_OVERLAYS || overlays.some((o) => o.id === oid)) return;
  const w = all.find((x) => x.id === oid);
  const used = new Set(overlays.map((o) => o.color));
  const color = OVERLAY_COLORS.find((c) => !used.has(c));
  const res = await api(`/api/hr?ids=${encodeURIComponent(oid)}`);
  overlays.push({ id: oid, w, hr: res[oid], color });
  renderLegend(); buildChart();
}
function removeOverlay(oid) {
  const i = overlays.findIndex((o) => o.id === oid);
  if (i >= 0) overlays.splice(i, 1);
  renderLegend(); buildChart(); renderPicker();
}

// --------------------------------------------------------------- picker ----
function renderPicker() {
  const q = $("pSearch").value.trim().toLowerCase();
  const type = $("pType").value;
  const tag = $("pTag").value.toLowerCase();
  const pinnedOnly = $("pPinned").checked;
  const items = all.filter((w) => w.id !== id && (!type || w.activity_type === type) &&
    (!tag || w.tags.some((t) => t.toLowerCase() === tag)) && (!pinnedOnly || w.pinned) &&
    (!q || `${fmtDate(w.start_ts)} ${fmtTime(w.start_ts)} ${prettyType(w.activity_type, w.display_name)} ${w.civil_date} ${w.tags.join(" ")}`.toLowerCase().includes(q)));
  const shown = items.slice(0, 200);
  const sel = new Set(overlays.map((o) => o.id));
  $("pCount").textContent = `${items.length} workouts${items.length > shown.length ? ` (showing first ${shown.length}, refine the search)` : ""} · ${overlays.length}/${MAX_OVERLAYS} selected`;
  $("pList").innerHTML = shown.map((w) => `<li><label>
      <input type="checkbox" value="${esc(w.id)}" ${sel.has(w.id) ? "checked" : ""} ${!sel.has(w.id) && overlays.length >= MAX_OVERLAYS ? "disabled" : ""}>
      <span>${w.pinned ? "★ " : ""}${fmtDate(w.start_ts)} · ${esc(prettyType(w.activity_type, w.display_name))}${w.tags.length ? ` <span class="muted small">${w.tags.map(esc).join(", ")}</span>` : ""}</span>
      <span class="sub">${fmtDuration(w.duration_s)}${w.avg_hr ? ` · avg ${w.avg_hr}` : ""}${w.sample_count ? "" : " · no HR"}</span>
    </label></li>`).join("");
  $("pList").querySelectorAll("input").forEach((cb) => cb.onchange = async () => {
    cb.disabled = true;
    try { if (cb.checked) await addOverlay(cb.value); else removeOverlay(cb.value); }
    catch (e) { banner(`Couldn't load that workout: ${e.message}`); }
    renderPicker();
  });
}

function initPicker() {
  const types = [...new Set(all.map((w) => w.activity_type))].sort((a, b) => prettyType(a).localeCompare(prettyType(b)));
  $("pType").innerHTML = `<option value="">All activities</option>` + types.map((t) => `<option value="${esc(t)}">${esc(prettyType(t))}</option>`).join("");
  $("pType").value = W.activity_type;  // most comparisons are like-for-like
  const tags = new Map();
  for (const w of all) for (const t of w.tags) if (!tags.has(t.toLowerCase())) tags.set(t.toLowerCase(), t);
  $("pTag").innerHTML = `<option value="">All tags</option>` +
    [...tags.values()].sort((a, b) => a.localeCompare(b)).map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  $("pSearch").oninput = renderPicker;
  $("pType").onchange = renderPicker;
  $("pTag").onchange = renderPicker;
  $("pPinned").onchange = renderPicker;
  $("compare").onclick = () => { renderPicker(); $("picker").showModal(); };
}

// -------------------------------------------------------------- panels ----
function renderSummary() {
  const type = prettyType(W.activity_type, W.display_name);
  document.title = `${type} · ${fmtDate(W.start_ts)}`;
  $("title").textContent = type;
  $("date").textContent = fmtDate(W.start_ts);
  $("time").textContent = `${fmtTime(W.start_ts)} – ${fmtTime(W.end_ts)}`;
  const box = (k, v) => v == null ? "" : `<div class="stat-box"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  $("stats").innerHTML = box("Duration", fmtDuration(W.duration_s)) +
    box("Avg HR", W.avg_hr ? `${W.avg_hr} bpm` : "–") + box("Max HR", W.max_hr ? `${W.max_hr} bpm` : "–") +
    box("Min HR", W.min_hr ? `${W.min_hr} bpm` : null) +
    box("Calories", W.calories != null ? `${Math.round(W.calories)} kcal` : null) +
    box("Active time", W.active_duration_s && Math.abs(W.active_duration_s - W.duration_s) > 30 ? fmtDuration(W.active_duration_s) : null);
}

function renderZones() {
  const rows = W.zone_breakdown;
  const total = rows.reduce((a, r) => a + r.seconds, 0);
  $("zoneTable").innerHTML = rows.map((r) => {
    const range = r.type === "below" ? `< ${r.max + 1}` : `${r.min}–${r.max}`;
    return `<tr style="--c:${zoneColor(r.type)}">
      <td><span class="sw"></span>${ZONE_LABEL[r.type]} <span class="muted small">${range}</span></td>
      <td class="num">${fmtMinutes(r.seconds)}</td><td class="num">${r.pct.toFixed(0)}%</td>
      <td class="pbar"><span style="width:${r.pct}%"></span></td></tr>`;
  }).join("");
  $("zoneNote").textContent = !total ? "No heart-rate data, so no time in zones."
    : W.zones_source === "google" ? `Zones from Google Health${W.zones_date ? ` (${W.zones_date})` : ""}.`
    : "Google Health zones weren't available, so these use % of the max HR in Settings.";
}

function renderRecovery() {
  const r = W.recovery;
  const row = (label, drop, at) => `<dt>${label}</dt><dd>${drop == null ? '<span class="muted">not available</span>'
    : `${drop > 0 ? "−" : drop < 0 ? "+" : ""}${Math.abs(drop)} bpm <span class="muted small">(${r.end_hr} → ${at})</span>`}</dd>`;
  $("recovery").innerHTML = `<dt>HR at end</dt><dd>${r.end_hr != null ? `${r.end_hr} bpm` : '<span class="muted">not available</span>'}</dd>` +
    row("After 1 min", r.drop_1, r.hr_1) + row("After 2 min", r.drop_2, r.hr_2);
}

// --------------------------------------------------------------- export ----
function exportPng() {
  if (!chart) return;
  const src = chart.ctx.canvas, dpr = devicePixelRatio || 1;
  const pad = 12 * dpr, headH = 28 * dpr, legendH = (overlays.length ? 20 * (overlays.length + 1) : 0) * dpr;
  const c = document.createElement("canvas");
  c.width = src.width + pad * 2; c.height = src.height + headH + legendH + pad * 2;
  const ctx = c.getContext("2d");
  ctx.fillStyle = cssVar("--surface"); ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = cssVar("--fg"); ctx.font = `600 ${15 * dpr}px system-ui, sans-serif`; ctx.textBaseline = "middle";
  ctx.fillText([fmtDate(W.start_ts), fmtTime(W.start_ts), prettyType(W.activity_type, W.display_name), fmtDuration(W.duration_s),
    W.avg_hr && `avg ${W.avg_hr}`, W.max_hr && `max ${W.max_hr} bpm`].filter(Boolean).join("  ·  "), pad, pad + headH / 2);
  let y = pad + headH;
  if (overlays.length) {
    ctx.font = `${12 * dpr}px system-ui, sans-serif`;
    [["This workout", cssVar("--line")], ...overlays.map((o) => [overlayLabel(o.w), o.color])].forEach(([label, col]) => {
      ctx.fillStyle = col; ctx.fillRect(pad, y + 8 * dpr, 16 * dpr, 3 * dpr);
      ctx.fillStyle = cssVar("--fg"); ctx.fillText(label, pad + 22 * dpr, y + 10 * dpr);
      y += 20 * dpr;
    });
  }
  ctx.drawImage(src, pad, y);
  const a = document.createElement("a");
  a.download = `workout-${W.civil_date}-${W.activity_type.toLowerCase()}${overlays.length ? "-compare" : ""}.png`;
  a.href = c.toDataURL("image/png");
  a.click();
}

// ----------------------------------------------------------------- boot ----
(async function boot() {
  if (!id) { banner("No workout selected."); return; }
  try {
    const st = await api("/api/status");
    setTimeZone(st.tz); settings = st.settings;
    [W, all] = await Promise.all([api(`/api/workouts/${encodeURIComponent(id)}`), api("/api/workouts")]);
  } catch (e) {
    banner(e.message === "Workout not found" ? "That workout isn't in the local cache (it may have been removed in Google Health)." : `Couldn't load this workout: ${e.message}`);
    return;
  }
  $("detail").hidden = false;
  renderSummary(); renderZones(); renderRecovery(); renderLegend(); initPicker();
  refreshTagSuggestions();
  pinButton($("pin"), { id, pinned: W.pinned });
  tagEditor($("tags"), { id, tags: W.tags });
  buildChart();
  $("resetZoom").onclick = () => chart?.setScale("x", { min: 0, max: xMax() });
  $("csv").href = `/api/workouts/${encodeURIComponent(id)}/export.csv`;
  $("json").href = `/api/workouts/${encodeURIComponent(id)}/export.json`;
  $("png").onclick = exportPng;
  let rt;
  new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => {
    const box = $("bigChart"); chart?.setSize({ width: box.clientWidth, height: box.clientHeight });
  }, 100); }).observe($("bigChart"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { buildChart(); renderLegend(); renderZones(); });
})();

