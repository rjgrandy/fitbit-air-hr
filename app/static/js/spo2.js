// Nightly SpO2 page: a trend of nightly averages, then one row per night (newest first)
// with that night's per-minute SpO2 chart over clock time.
import uPlot from "../vendor/uPlot.esm.js";
import { cssVar, esc, setTimeZone, fmtDate, fmtTime, fmtDuration, relTime } from "./format.js";
import { withAlpha } from "./rowchart.js";

const $ = (id) => document.getElementById(id);
const COMPACT_BELOW = 90;

const PREFS_KEY = "fahr.spo2.prefs.v1";
const prefs = Object.assign({ from: "", to: "", axis: "shared", rowH: 120 }, loadPrefs());
function loadPrefs() { try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; } }
function savePrefs() { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* private mode */ } }

let data = { nights: [], low_pct: 90, gap_seconds: 600, error: null, backfill_complete: false };
let visible = [];
const rows = new Map();     // night -> { el, n, chart, visible }
const cache = new Map();    // night -> { ts, pct }
let trend = null;
let lastStatus = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body || `HTTP ${r.status}`);
  return body;
}

// ------------------------------------------------------------- helpers ----
const pct = (v, d = 1) => (v == null ? "–" : `${(+v).toFixed(d).replace(/\.0$/, "")}%`);
const shortTime = (ts) => fmtTime(ts).replace(":00", "");
// Nights are keyed by the local date the sleep ends on. Noon UTC shows the right date in any US zone.
const nightTs = (night) => Date.parse(`${night}T12:00:00Z`) / 1000;
const dayFmt = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });

function yRange() {
  const lows = visible.map((n) => n.min_pct ?? n.daily?.lower_pct).filter((v) => v != null);
  const lo = Math.min(85, ...lows.map((v) => Math.floor((v - 1) / 5) * 5));
  return [Math.max(50, lo), 100];
}

function lowBandPlugin(low) {
  return {
    hooks: {
      drawClear: [(u) => {
        const { ctx } = u;
        const { left, top, width, height } = u.bbox;
        ctx.save();
        ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
        const y0 = u.valToPos(low, "y", true), y1 = u.valToPos(u.scales.y.min, "y", true);
        ctx.fillStyle = withAlpha(cssVar("--spo2-low"), parseFloat(cssVar("--band-alpha")) || 0.13);
        ctx.fillRect(left, y0, width, y1 - y0);
        ctx.restore();
      }],
    },
  };
}

function avgLinePlugin(avg) {
  return {
    hooks: {
      draw: [(u) => {
        if (avg == null) return;
        const { ctx } = u;
        const { left, width } = u.bbox;
        const y = Math.round(u.valToPos(avg, "y", true)) + 0.5;
        ctx.save();
        ctx.strokeStyle = cssVar("--spo2-avg");
        ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]);
        ctx.lineWidth = devicePixelRatio;
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + width, y); ctx.stroke();
        ctx.restore();
      }],
    },
  };
}

function tipPlugin(tip, text) {
  return {
    hooks: {
      setCursor: [(u) => {
        const i = u.cursor.idx;
        const y = i == null ? null : u.data[1][i];
        if (i == null || y == null) { tip.hidden = true; return; }
        tip.hidden = false;
        tip.textContent = text(u, i);
        const x = u.valToPos(u.data[0][i], "x") + u.over.offsetLeft;
        tip.style.left = `${Math.min(Math.max(x, 60), u.root.clientWidth - 60)}px`;
      }],
    },
  };
}

function attachTouch(u) {
  const move = (e) => {
    const t = e.touches[0];
    if (!t) return;
    const r = u.over.getBoundingClientRect();
    u.setCursor({ left: t.clientX - r.left, top: t.clientY - r.top });
  };
  u.over.addEventListener("touchstart", move, { passive: true });
  u.over.addEventListener("touchmove", move, { passive: true });
}

function pctSplits(min, max, px) {
  const step = [1, 2, 5, 10, 20].find((s) => (max - min) / s <= Math.max(2, Math.floor(px / 22))) || 20;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(v);
  return out;
}

// ---------------------------------------------------------------- trend ----
function renderTrend() {
  const box = $("trendChart");
  if (trend) { trend.destroy(); trend = null; }
  const pts = visible.filter((n) => n.avg_pct != null || n.daily?.avg_pct != null).slice().reverse();
  $("trend").hidden = pts.length < 2;
  if (pts.length < 2) return;
  const xs = pts.map((n) => nightTs(n.night));
  const avg = pts.map((n) => n.daily?.avg_pct ?? n.avg_pct);
  const lower = pts.map((n) => n.daily?.lower_pct ?? null);
  const upper = pts.map((n) => n.daily?.upper_pct ?? null);
  const all = [...avg, ...lower].filter((v) => v != null);
  const yMin = Math.max(50, Math.min(88, Math.floor((Math.min(...all) - 1) / 2) * 2));
  const fg = cssVar("--muted"), grid = cssVar("--grid");
  const tip = box.querySelector(".tip");
  trend = new uPlot({
    width: box.clientWidth || 300, height: box.clientHeight || 170,
    padding: [6, 18, 0, 0],
    legend: { show: false },
    cursor: { y: false, points: { size: 7 } },
    scales: { x: { time: true }, y: { range: () => [yMin, 100] } },
    axes: [
      { stroke: fg, grid: { stroke: grid }, ticks: { show: false }, font: "11px system-ui", size: 22, gap: 2,
        values: (u, vals) => vals.map((v) => dayFmt.format(new Date(v * 1000))) },
      { stroke: fg, grid: { stroke: grid }, ticks: { show: false }, font: "11px system-ui", size: 34,
        splits: (u) => pctSplits(yMin, 100, u.bbox.height / devicePixelRatio), values: (u, v) => v.map((x) => `${x}%`) },
    ],
    series: [
      {},
      { stroke: cssVar("--line"), width: 1.5, spanGaps: true, points: { show: pts.length <= 120, size: 4, fill: cssVar("--line") } },
      { stroke: "transparent", spanGaps: true, points: { show: false } },
      { stroke: "transparent", spanGaps: true, points: { show: false } },
    ],
    bands: [{ series: [3, 2], fill: withAlpha(cssVar("--spo2-range"), 0.22) }],
    plugins: [lowBandPlugin(data.low_pct), tipPlugin(tip, (u, i) => {
      const n = pts[i];
      const rng = n.daily?.lower_pct != null ? ` (${pct(n.daily.lower_pct)}–${pct(n.daily.upper_pct)})` : "";
      return `${fmtDate(nightTs(n.night)).replace(/, \d{4}$/, "")} · ${pct(avg[i])}${rng}`;
    })],
  }, [xs, avg, lower, upper], box);
  attachTouch(trend);
  trend.over.addEventListener("click", () => {
    const i = trend.cursor.idx;
    const r = i == null ? null : rows.get(pts[i].night);
    if (!r) return;
    r.el.scrollIntoView({ behavior: "smooth", block: "center" });
    r.el.classList.add("selected");
    setTimeout(() => r.el.classList.remove("selected"), 1500);
  });
}

// ----------------------------------------------------------- row charts ----
const pending = new Set();
let flushTimer = null;
function requestSamples(night) {
  if (cache.has(night)) return renderRow(night);
  pending.add(night);
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flush, 40);
}
async function flush() {
  const ids = [...pending].slice(0, 50);
  ids.forEach((id) => pending.delete(id));
  if (pending.size) flushTimer = setTimeout(flush, 0);
  if (!ids.length) return;
  try {
    const res = await api(`/api/spo2/samples?nights=${ids.join(",")}`);
    for (const [id, d] of Object.entries(res)) { cache.set(id, d); renderRow(id); }
  } catch {
    ids.forEach((id) => { const r = rows.get(id); if (r) setPlaceholder(r, "Couldn't load SpO2"); });
  }
}

const io = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const r = rows.get(e.target.dataset.night);
    if (!r) continue;
    r.visible = e.isIntersecting;
    if (e.isIntersecting) { if (r.n.sample_count) requestSamples(r.n.night); }
    else destroy(r);
  }
}, { rootMargin: "800px 0px" });

function destroy(r) { if (r.chart) { r.chart.destroy(); r.chart = null; } }
function setPlaceholder(r, text) {
  const ph = r.el.querySelector(".placeholder");
  ph.textContent = text;
  ph.hidden = !text;
}

/** Hours since the night's anchor (18:00 the evening before), with nulls across gaps. */
function toSeries(n, d) {
  const xs = [], ys = [];
  let prev = null;
  for (let i = 0; i < d.ts.length; i++) {
    if (prev !== null && d.ts[i] - prev > data.gap_seconds) { xs.push((prev + 30 - n.anchor_ts) / 3600); ys.push(null); }
    xs.push((d.ts[i] - n.anchor_ts) / 3600);
    ys.push(d.pct[i]);
    prev = d.ts[i];
  }
  return [xs, ys];
}

function xRange(n) {
  const span = (m) => [(m.start_ts - m.anchor_ts) / 3600, (m.end_ts - m.anchor_ts) / 3600];
  if (prefs.axis === "fit") {
    const [a, b] = span(n);
    return [a, Math.max(b, a + 0.5)];
  }
  const withData = visible.filter((m) => m.sample_count);
  const a = Math.min(...withData.map((m) => span(m)[0])), b = Math.max(...withData.map((m) => span(m)[1]));
  return [Math.floor(a), Math.ceil(b)];
}

function hourSplits(lo, hi, px) {
  const step = [1, 2, 3, 4, 6].find((s) => (hi - lo) / s <= Math.max(2, Math.floor(px / 55))) || 6;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v);
  return out;
}

function renderRow(night) {
  const r = rows.get(night), d = cache.get(night);
  if (!r || !d || !r.visible) return;
  destroy(r);
  if (!d.ts.length) return setPlaceholder(r, "No per-minute readings");
  setPlaceholder(r, "");
  const n = r.n, box = r.el.querySelector(".chart");
  const [xMin, xMax] = xRange(n);
  const [yMin, yMax] = yRange();
  const compact = prefs.rowH < COMPACT_BELOW;
  const fg = cssVar("--muted"), grid = cssVar("--grid");
  r.chart = new uPlot({
    width: box.clientWidth || 300, height: box.clientHeight || 120,
    padding: compact ? [2, 0, 2, 0] : [6, 18, 0, 0],
    legend: { show: false },
    cursor: { y: false, points: { size: 6 }, drag: { x: false, y: false, setScale: false } },
    select: { show: false },
    scales: { x: { time: false, range: () => [xMin, xMax] }, y: { range: () => [yMin, yMax] } },
    axes: compact ? [{ show: false }, { show: false }] : [
      { stroke: fg, grid: { stroke: grid }, ticks: { show: false }, font: "11px system-ui", size: 22, gap: 2,
        splits: (u) => hourSplits(xMin, xMax, u.bbox.width / devicePixelRatio),
        values: (u, vals) => vals.map((v) => shortTime(n.anchor_ts + v * 3600)) },
      { stroke: fg, grid: { stroke: grid }, ticks: { show: false }, font: "11px system-ui", size: 34, gap: 2,
        splits: (u) => pctSplits(yMin, yMax, u.bbox.height / devicePixelRatio), values: (u, v) => v.map((x) => `${x}%`) },
    ],
    series: [{}, { stroke: cssVar("--line"), width: 1.5, spanGaps: false, points: { show: false } }],
    plugins: [lowBandPlugin(data.low_pct), avgLinePlugin(n.avg_pct),
      tipPlugin(box.querySelector(".tip"), (u, i) => `${fmtTime(n.anchor_ts + u.data[0][i] * 3600)} · ${pct(u.data[1][i])}`)],
  }, toSeries(n, d), box);
  attachTouch(r.chart);
}

// ----------------------------------------------------------------- rows ----
function fillHead(el, n) {
  el.querySelector(".date").textContent = fmtDate(nightTs(n.night));
  el.querySelector(".time").textContent = n.start_ts
    ? `${fmtTime(n.start_ts)} – ${fmtTime(n.end_ts)} · ${fmtDuration(n.end_ts - n.start_ts)}` : "";
  const avg = n.avg_pct ?? n.daily?.avg_pct;
  el.querySelector(".avg").innerHTML = `avg <b>${pct(avg)}</b>`;
  el.querySelector(".min").innerHTML = n.min_pct != null ? `low <b>${pct(n.min_pct, 0)}</b>` : "";
  el.querySelector(".range").innerHTML = n.daily?.lower_pct != null
    ? `<span title="Google's nightly confidence range">range <b>${pct(n.daily.lower_pct)}–${pct(n.daily.upper_pct)}</b></span>` : "";
  const low = el.querySelector(".lowmin");
  low.innerHTML = n.sample_count ? `below ${data.low_pct}% <b>${n.low_minutes} min</b>` : "";
  low.classList.toggle("bad", n.low_minutes > 0);
}

function buildRow(n) {
  const el = $("rowTpl").content.firstElementChild.cloneNode(true);
  el.dataset.night = n.night;
  fillHead(el, n);
  const r = { el, n, chart: null, visible: false };
  setPlaceholder(r, n.sample_count ? "Loading SpO2…" : "Google's nightly summary only, no per-minute readings");
  rows.set(n.night, r);
  io.observe(el);
  return r;
}

function renderList() {
  visible = data.nights.filter((n) => (!prefs.from || n.night >= prefs.from) && (!prefs.to || n.night <= prefs.to));
  const keep = new Set(visible.map((n) => n.night));
  for (const [k, r] of rows) if (!keep.has(k)) { io.unobserve(r.el); destroy(r); r.el.remove(); rows.delete(k); }
  const frag = document.createDocumentFragment();
  for (const n of visible) {
    let r = rows.get(n.night);
    if (!r) r = buildRow(n);
    else if (r.n.sample_count !== n.sample_count || r.n.end_ts !== n.end_ts || r.n.start_ts !== n.start_ts) {
      cache.delete(n.night); r.n = n; fillHead(r.el, n);
      if (r.visible && n.sample_count) requestSamples(n.night);
    } else { r.n = n; fillHead(r.el, n); }
    frag.appendChild(r.el);
  }
  $("list").appendChild(frag);
  rerenderCharts();
  renderTrend();
  const all = data.nights.length;
  $("count").textContent = all ? (visible.length === all ? `${all} nights` : `${visible.length} of ${all} nights`) : "";
  renderEmpty();
}

function rerenderCharts() {
  for (const r of rows.values()) if (r.visible && r.n.sample_count) requestSamples(r.n.night);
}

function renderEmpty() {
  const el = $("empty");
  if (visible.length) { el.hidden = true; return; }
  el.hidden = false;
  const s = lastStatus?.sync;
  if (data.nights.length) el.textContent = "No nights in this date range.";
  else if (lastStatus && lastStatus.auth !== "ok") el.innerHTML = `Connect your Google account on the <a href="./">Workouts</a> page first.`;
  else if (s?.running || !data.backfill_complete) el.textContent = "Loading your SpO2 history…";
  else el.textContent = "No SpO2 readings yet. The Fitbit Air measures SpO2 every minute while you sleep. Wear it to bed, let it sync to the Google Health app, then tap Sync now.";
}

async function loadNights() {
  data = await api("/api/spo2");
  document.querySelectorAll(".lowPct").forEach((e) => { e.textContent = data.low_pct; });
  renderList();
  renderBanner();
}

// ------------------------------------------------------- status & sync ----
let pollTimer = null, lastKey = "";
async function refreshStatus() {
  let st;
  try { st = await api("/api/status"); } catch (e) { return showBanner(`Can't reach the app server: ${esc(e.message)}`); }
  if (st.tz !== lastStatus?.tz) setTimeZone(st.tz);
  lastStatus = st;
  const s = st.sync;
  $("syncNow").disabled = s.running || st.auth !== "ok";
  $("syncLabel").textContent = s.running ? "Syncing…" : `Synced ${relTime(s.last_synced_at)}`;
  $("progress").hidden = !s.running;
  if (s.running) $("progress").querySelector(".msg").textContent =
    (s.phase === "spo2" ? s.message || "Loading SpO2…" : "Syncing workouts first, SpO2 next…") + " You can keep browsing.";
  const key = `${s.running}|${s.phase}|${s.message}`;
  if (key !== lastKey && (s.phase === "spo2" || !s.running)) { lastKey = key; await loadNights(); }
  clearTimeout(pollTimer);
  if (s.running) pollTimer = setTimeout(refreshStatus, 2000);
  renderBanner();
  renderEmpty();
}

function renderBanner() {
  const st = lastStatus;
  if (st && st.auth !== "ok") return showBanner(`<p><b>Google Health isn't connected.</b> Connect it on the <a href="./">Workouts</a> page, then come back.</p>`);
  if (st?.sync.error_kind === "rate") return showBanner(`<p><b>Google is rate-limiting requests.</b> Showing what's already synced.</p>`);
  if (data.error) return showBanner(`<p><b>Couldn't load SpO2.</b> ${esc(data.error)}</p>`);
  $("banner").hidden = true;
}
function showBanner(html) { const b = $("banner"); b.innerHTML = html; b.hidden = false; }

async function startSync() {
  try { await api("/api/sync", { method: "POST" }); } catch { /* status shows it */ }
  refreshStatus();
}

// -------------------------------------------------------------- controls ----
function initControls() {
  const apply = () => { savePrefs(); renderList(); };
  $("fFrom").value = prefs.from; $("fTo").value = prefs.to;
  $("fFrom").onchange = (e) => { prefs.from = e.target.value; apply(); };
  $("fTo").onchange = (e) => { prefs.to = e.target.value; apply(); };
  $("fClear").onclick = () => { prefs.from = prefs.to = ""; $("fFrom").value = $("fTo").value = ""; apply(); };

  const segs = document.querySelectorAll(".seg-btn");
  const paint = () => segs.forEach((b) => b.classList.toggle("on", b.dataset.axis === prefs.axis));
  segs.forEach((b) => b.onclick = () => { prefs.axis = b.dataset.axis; paint(); savePrefs(); rerenderCharts(); });
  paint();

  const slider = $("rowH");
  slider.value = prefs.rowH;
  const applyH = () => {
    document.documentElement.style.setProperty("--row-h", `${prefs.rowH}px`);
    document.body.classList.toggle("compact", prefs.rowH < COMPACT_BELOW);
  };
  applyH();
  let t;
  slider.oninput = () => { prefs.rowH = +slider.value; applyH(); clearTimeout(t); t = setTimeout(() => { savePrefs(); rerenderCharts(); }, 120); };

  $("syncNow").onclick = startSync;

  let rt;
  new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => {
    for (const r of rows.values()) if (r.chart) { const b = r.el.querySelector(".chart"); r.chart.setSize({ width: b.clientWidth, height: b.clientHeight }); }
    if (trend) { const b = $("trendChart"); trend.setSize({ width: b.clientWidth, height: b.clientHeight }); }
  }, 100); }).observe(document.body);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { rerenderCharts(); renderTrend(); });
  setInterval(() => { if (lastStatus && !lastStatus.sync.running) $("syncLabel").textContent = `Synced ${relTime(lastStatus.sync.last_synced_at)}`; }, 30000);
}

// ------------------------------------------------------------------ boot ----
(async function boot() {
  initControls();
  const st = await api("/api/status").catch(() => null);
  if (st) { setTimeZone(st.tz); lastStatus = st; }
  try { await loadNights(); } catch (e) { showBanner(`Can't load SpO2: ${esc(e.message)}`); }
  if (st?.auth === "ok" && !st.sync.running) await api("/api/sync", { method: "POST" }).catch(() => {});
  refreshStatus();
})();
