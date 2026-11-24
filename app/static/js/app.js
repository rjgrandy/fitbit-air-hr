// Main page: one continuous list of workouts, newest first, each with its own HR chart.
import { makeRowChart } from "./rowchart.js";
import {
  ZONE_ORDER, ZONE_LABEL, zoneColor, setTimeZone, fmtDate, fmtTime, fmtDuration, fmtMinutes, prettyType, relTime,
} from "./format.js";

const $ = (id) => document.getElementById(id);
const COMPACT_BELOW = 90; // px row height under which rows become sparklines (no axes)

// ---------------------------------------------------------------- prefs ----
const PREFS_KEY = "fahr.prefs.v1";
const prefs = Object.assign({ type: "", from: "", to: "", axis: "shared", rowH: 140 }, loadPrefs());
function loadPrefs() { try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; } }
function savePrefs() { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* private mode */ } }

// ---------------------------------------------------------------- state ----
let settings = { y_min: 50, y_max: 200, fallback_max_hr: 190 };
let workouts = [];                 // all, newest first
let visibleList = [];              // after filters
const rows = new Map();            // id -> { el, w, chart }
const hrCache = new Map();         // id -> hr payload
let lastStatus = null;

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body || `HTTP ${r.status}`);
  return body;
}

// ------------------------------------------------------------ lazy HR ----
const pendingIds = new Set();
let flushTimer = null;
function requestHr(id) {
  if (hrCache.has(id)) return renderChart(id);
  pendingIds.add(id);
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flushHr, 40);
}
async function flushHr() {
  const ids = [...pendingIds].slice(0, 50);
  ids.forEach((id) => pendingIds.delete(id));
  if (pendingIds.size) flushTimer = setTimeout(flushHr, 0);
  if (!ids.length) return;
  try {
    const res = await api(`/api/hr?ids=${ids.map(encodeURIComponent).join(",")}`);
    for (const [id, d] of Object.entries(res)) {
      hrCache.set(id, d);
      renderChart(id);
    }
  } catch (e) {
    ids.forEach((id) => { const r = rows.get(id); if (r) setPlaceholder(r, "Couldn't load heart rate"); });
  }
}

// Render charts only for rows near the viewport; free them when far away.
const io = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const id = e.target.dataset.id;
    const r = rows.get(id);
    if (!r) continue;
    r.visible = e.isIntersecting;
    if (e.isIntersecting) requestHr(id);
    else destroyChart(r);
  }
}, { rootMargin: "800px 0px" });

function destroyChart(r) {
  if (r.chart) { r.chart.destroy(); r.chart = null; }
}

function setPlaceholder(r, text) {
  const ph = r.el.querySelector(".placeholder");
  ph.textContent = text;
  ph.hidden = !text;
}

function xMaxMinutes(w) {
  if (prefs.axis === "fit") return w.duration_s / 60;
  return Math.max(1, ...visibleList.map((x) => x.duration_s)) / 60;
}

function renderChart(id) {
  const r = rows.get(id);
  const d = hrCache.get(id);
  if (!r || !d || !r.visible) return;
  destroyChart(r);
  renderTiz(r, d);
  if (d.hr_status === "pending") return setPlaceholder(r, "Loading heart rate…");
  const inWindow = d.t.filter((t) => t >= 0 && t <= r.w.duration_s).length;
  if (!inWindow) return setPlaceholder(r, "No HR data");
  setPlaceholder(r, "");
  const box = r.el.querySelector(".chart");
  r.chart = makeRowChart(box, {
    t: d.t, bpm: d.bpm, durationSec: r.w.duration_s, xMaxMin: xMaxMinutes(r.w),
    yMin: settings.y_min, yMax: settings.y_max, zones: d.zones,
    compact: prefs.rowH < COMPACT_BELOW, tip: box.querySelector(".tip"),
  });
}

function renderTiz(r, d) {
  const el = r.el.querySelector(".tiz");
  const tiz = d.time_in_zones || {};
  const total = ZONE_ORDER.reduce((a, k) => a + (tiz[k] || 0), 0);
  if (!total) { el.innerHTML = ""; return; }
  const keys = ZONE_ORDER.filter((k) => k !== "below" || tiz.below > 0);
  el.innerHTML =
    `<div class="bar" title="Time in zone${d.zones_source === "fallback" ? " (fallback zones from max HR)" : ""}">` +
    keys.map((k) => `<span style="width:${(100 * (tiz[k] || 0)) / total}%;background:${zoneColor(k)}"></span>`).join("") +
    `</div><div class="chips">` +
    keys.map((k) => `<span class="chip" style="--c:${zoneColor(k)}">${ZONE_LABEL[k]} ${fmtMinutes(tiz[k] || 0)}</span>`).join("") +
    `</div>`;
}

// ---------------------------------------------------------------- rows ----
function buildRow(w) {
  const el = $("rowTpl").content.firstElementChild.cloneNode(true);
  el.dataset.id = w.id;
  const head = el.querySelector(".row-head");
  head.href = `workout.html?id=${encodeURIComponent(w.id)}`;
  fillHead(el, w);
  el.querySelector(".png").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); exportPng(w.id); });
  const r = { el, w, chart: null, visible: false };
  setPlaceholder(r, w.hr_status === "pending" ? "Loading heart rate…" : "");
  rows.set(w.id, r);
  io.observe(el);
  return r;
}

function fillHead(el, w) {
  el.querySelector(".date").textContent = fmtDate(w.start_ts);
  el.querySelector(".time").textContent = fmtTime(w.start_ts);
  el.querySelector(".type").textContent = prettyType(w.activity_type, w.display_name);
  el.querySelector(".dur").textContent = fmtDuration(w.duration_s);
  el.querySelector(".avg").innerHTML = w.avg_hr ? `avg <b>${w.avg_hr}</b>` : "avg –";
  el.querySelector(".max").innerHTML = w.max_hr ? `max <b>${w.max_hr}</b>` : "max –";
}

function applyFilters() {
  visibleList = workouts.filter((w) =>
    (!prefs.type || w.activity_type === prefs.type) &&
    (!prefs.from || w.civil_date >= prefs.from) &&
    (!prefs.to || w.civil_date <= prefs.to));
}

function renderList() {
  applyFilters();
  const list = $("list");
  const frag = document.createDocumentFragment();
  const keep = new Set(visibleList.map((w) => w.id));
  for (const [id, r] of rows) {
    if (!keep.has(id)) { io.unobserve(r.el); destroyChart(r); r.el.remove(); rows.delete(id); }
  }
  for (const w of visibleList) {
    let r = rows.get(w.id);
    if (!r) r = buildRow(w);
    else if (r.w.hr_status !== w.hr_status || r.w.max_hr !== w.max_hr || r.w.zones_date !== w.zones_date ||
             r.w.duration_s !== w.duration_s) {
      r.w = w; fillHead(r.el, w); hrCache.delete(w.id);
      setPlaceholder(r, w.hr_status === "pending" ? "Loading heart rate…" : "");
      if (r.visible) requestHr(w.id);
    }
    r.w = w;
    frag.appendChild(r.el);
  }
  list.appendChild(frag);
  // Shared x-scale depends on the longest visible workout: redraw live charts if it changed.
  const maxDur = Math.max(0, ...visibleList.map((w) => w.duration_s));
  if (prefs.axis === "shared" && renderList.lastMax !== maxDur) {
    renderList.lastMax = maxDur;
    for (const r of rows.values()) if (r.chart) renderChart(r.w.id);
  }
  updateCount();
  updateEmpty();
}

function rerenderAllCharts() {
  for (const r of rows.values()) if (r.visible) { if (hrCache.has(r.w.id)) renderChart(r.w.id); else requestHr(r.w.id); }
}

function updateCount() {
  const n = visibleList.length, all = workouts.length;
  $("count").textContent = all ? (n === all ? `${all} workouts` : `${n} of ${all} workouts`) : "";
}

function updateEmpty() {
  const el = $("empty");
  if (visibleList.length) { el.hidden = true; return; }
  el.hidden = false;
  const s = lastStatus?.sync;
  if (workouts.length) el.textContent = "No workouts match these filters.";
  else if (lastStatus?.auth !== "ok") el.textContent = "Connect your Google account above to load your workouts.";
  else if (s?.running || !s?.backfill_complete) el.textContent = "Loading your workout history…";
  else el.textContent = "No workouts yet. Record one with your Fitbit Air, let it sync, then tap Sync now.";
}

async function loadWorkouts() {
  workouts = await api("/api/workouts");
  fillTypeFilter();
  renderList();
}

function fillTypeFilter() {
  const sel = $("fType");
  const counts = {};
  for (const w of workouts) counts[w.activity_type] = (counts[w.activity_type] || 0) + 1;
  const types = Object.keys(counts).sort((a, b) => prettyType(a).localeCompare(prettyType(b)));
  if (prefs.type && !counts[prefs.type]) types.push(prefs.type);
  sel.innerHTML = `<option value="">All activities</option>` +
    types.map((t) => `<option value="${t}">${prettyType(t)} (${counts[t] || 0})</option>`).join("");
  sel.value = prefs.type;
}

// ----------------------------------------------------------- PNG export ----
function exportPng(id) {
  const r = rows.get(id);
  if (!r) return;
  const w = r.w, d = hrCache.get(id);
  const dpr = window.devicePixelRatio || 1;
  const src = r.chart?.ctx.canvas;
  const W = src ? src.width : Math.round((r.el.clientWidth || 600) * dpr);
  const chartH = src ? src.height : Math.round(80 * dpr);
  const headH = Math.round(34 * dpr), tizH = Math.round(30 * dpr), pad = Math.round(10 * dpr);
  const c = document.createElement("canvas");
  c.width = W + pad * 2; c.height = headH + chartH + tizH + pad * 2;
  const ctx = c.getContext("2d");
  const cs = getComputedStyle(document.documentElement);
  ctx.fillStyle = cs.getPropertyValue("--surface").trim(); ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = cs.getPropertyValue("--fg").trim();
  ctx.font = `600 ${14 * dpr}px system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  const head = [fmtDate(w.start_ts), fmtTime(w.start_ts), prettyType(w.activity_type, w.display_name),
    fmtDuration(w.duration_s), w.avg_hr ? `avg ${w.avg_hr}` : null, w.max_hr ? `max ${w.max_hr} bpm` : null]
    .filter(Boolean).join("  ·  ");
  ctx.fillText(head, pad, pad + headH / 2);
  if (src) ctx.drawImage(src, pad, pad + headH);
  else { ctx.fillStyle = cs.getPropertyValue("--muted").trim(); ctx.fillText("No HR data", pad, pad + headH + chartH / 2); }
  const tiz = d?.time_in_zones;
  if (tiz) {
    const keys = ZONE_ORDER.filter((k) => tiz[k] > 0);
    const total = keys.reduce((a, k) => a + tiz[k], 0) || 1;
    let x = pad; const y = pad + headH + chartH + 8 * dpr;
    ctx.font = `${11 * dpr}px system-ui, sans-serif`;
    for (const k of keys) {
      const ww = (W * tiz[k]) / total;
      ctx.fillStyle = zoneColor(k); ctx.fillRect(x, y, ww, 8 * dpr);
      if (ww > 70 * dpr) { ctx.fillStyle = cs.getPropertyValue("--fg").trim(); ctx.fillText(`${ZONE_LABEL[k]} ${fmtMinutes(tiz[k])}`, x + 2 * dpr, y + 17 * dpr); }
      x += ww;
    }
  }
  const a = document.createElement("a");
  const stamp = new Date(w.start_ts * 1000).toISOString().slice(0, 16).replace(/[:T]/g, "-");
  a.download = `workout-${stamp}-${w.activity_type.toLowerCase()}.png`;
  a.href = c.toDataURL("image/png");
  a.click();
}

// -------------------------------------------------------- status & sync ----
let pollTimer = null, lastCountKey = "", lastListRefresh = 0;

async function refreshStatus() {
  let st;
  try { st = await api("/api/status"); } catch (e) { showBanner("error", `Can't reach the app server: ${e.message}`); return; }
  lastStatus = st;
  setTimeZone(st.tz);
  if (JSON.stringify(st.settings) !== JSON.stringify(settings)) {
    settings = st.settings;
    rerenderAllCharts();
  }
  renderSync(st);
  renderAuthOrError(st);
  // While syncing, refresh the list as workouts and HR arrive (at most every 3 s).
  const s = st.sync;
  const key = `${s.workout_count}|${s.hr_pending}|${s.phase}`;
  if (key !== lastCountKey && Date.now() - lastListRefresh > 3000) {
    lastCountKey = key; lastListRefresh = Date.now();
    await loadWorkouts();
  }
  clearTimeout(pollTimer);
  if (s.running) pollTimer = setTimeout(refreshStatus, 1500);
  else if (key !== lastCountKey) { lastCountKey = key; await loadWorkouts(); }
  updateEmpty();
}

function renderSync(st) {
  const s = st.sync;
  $("syncNow").disabled = s.running || st.auth !== "ok";
  $("syncLabel").textContent = s.running ? "Syncing…" : `Synced ${relTime(s.last_synced_at)}`;
  const p = $("progress");
  if (!s.running) { p.hidden = true; return; }
  p.hidden = false;
  const bar = p.querySelector(".bar"), fill = bar.querySelector("span");
  const determinate = s.phase === "heart rate" && s.total > 0;
  bar.classList.toggle("indeterminate", !determinate);
  fill.style.width = determinate ? `${(100 * s.done) / s.total}%` : "";
  let msg = s.message || "Syncing…";
  if (s.phase === "backfill") msg += ` (${s.done} workouts so far)`;
  if (determinate) msg = `Loading heart rate: ${s.done} of ${s.total} workouts`;
  if (s.backoff_until && s.message) msg = s.message;
  p.querySelector(".msg").textContent = msg + " You can keep browsing.";
}

function renderAuthOrError(st) {
  const s = st.sync;
  if (st.auth === "unconfigured") {
    return showBanner("warn", `<h2>Google client not configured</h2>
      <p>Add <code>GOOGLE_CLIENT_ID</code> and <code>GOOGLE_CLIENT_SECRET</code> to <code>.env</code> on the server and restart the container.</p>`, true);
  }
  if (st.auth !== "ok" || s.error_kind === "auth") {
    const revoked = st.auth === "revoked" || s.error_kind === "auth";
    return showAuthBanner(revoked);
  }
  if (s.error_kind === "rate") {
    return showBanner("warn", `<p><b>Google is rate-limiting requests.</b> Showing what's already synced. Try Sync now again in a few minutes.</p>`, true);
  }
  if (s.error_kind === "api") {
    return showBanner("warn", `<p><b>Sync failed.</b> ${escapeHtml(s.error || "")}</p>
      <div class="row-actions"><button class="btn small" id="retrySync">Retry</button></div>`, true, () => {
      $("retrySync").onclick = startSync;
    });
  }
  hideBanner();
}

let bannerKey = "";
function showBanner(kind, html, isHtml = false, after) {
  const b = $("banner");
  const key = kind + html;
  if (key === bannerKey && !b.hidden) return;
  bannerKey = key;
  b.hidden = false;
  if (isHtml) b.innerHTML = html; else b.textContent = html;
  after?.();
}
function hideBanner() { bannerKey = ""; $("banner").hidden = true; }

function showAuthBanner(revoked) {
  showBanner("auth", `
    <h2>${revoked ? "Re-authorize Google Health" : "Connect Google Health"}</h2>
    ${revoked ? "<p>Google access was revoked or expired, so syncing has stopped. Your saved workouts are still here.</p>" : ""}
    <ol>
      <li><button class="btn small" id="authOpen">Open Google sign-in</button> Sign in, click through the "unverified app" warning, and allow both permissions.</li>
      <li>You'll land on google.com. Copy the whole address-bar URL and paste it here:</li>
    </ol>
    <input type="text" id="authCode" placeholder="https://www.google.com/?code=…" autocomplete="off" spellcheck="false">
    <div class="row-actions"><button class="btn small" id="authSubmit">Connect</button></div>
    <p id="authErr" class="error small" hidden></p>`, true, () => {
    $("authOpen").onclick = async () => {
      try { const { url } = await api("/api/auth/url"); window.open(url, "_blank", "noopener"); }
      catch (e) { authErr(e.message); }
    };
    $("authSubmit").onclick = async () => {
      const code = $("authCode").value.trim();
      if (!code) return authErr("Paste the google.com address first.");
      $("authSubmit").disabled = true;
      try {
        const r = await api("/api/auth/code", { method: "POST", body: JSON.stringify({ code }) });
        hideBanner();
        if (r.refresh_token_expires_in) {
          showBanner("warn", `<p>Connected. Note: Google says this authorization expires in ${Math.round(r.refresh_token_expires_in / 86400)} days because the OAuth app is still in Testing. Publish it to stop that.</p>`, true);
        }
        refreshStatus();
      } catch (e) { authErr(e.message); }
      finally { const b = $("authSubmit"); if (b) b.disabled = false; }
    };
  });
}
function authErr(m) { const e = $("authErr"); e.textContent = m; e.hidden = false; }
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

async function startSync() {
  try { await api("/api/sync", { method: "POST" }); } catch { /* status shows it */ }
  hideBanner();
  refreshStatus();
}

// -------------------------------------------------------------- controls ----
function initControls() {
  const apply = () => { savePrefs(); renderList(); };
  $("fType").onchange = (e) => { prefs.type = e.target.value; apply(); };
  $("fFrom").value = prefs.from; $("fTo").value = prefs.to;
  $("fFrom").onchange = (e) => { prefs.from = e.target.value; apply(); };
  $("fTo").onchange = (e) => { prefs.to = e.target.value; apply(); };
  $("fClear").onclick = () => {
    prefs.type = prefs.from = prefs.to = "";
    $("fType").value = $("fFrom").value = $("fTo").value = "";
    apply();
  };

  const segs = document.querySelectorAll(".seg-btn");
  const paintSeg = () => segs.forEach((b) => b.classList.toggle("on", b.dataset.axis === prefs.axis));
  segs.forEach((b) => b.onclick = () => { prefs.axis = b.dataset.axis; paintSeg(); savePrefs(); rerenderAllCharts(); });
  paintSeg();

  const slider = $("rowH");
  slider.value = prefs.rowH;
  const applyH = () => {
    document.documentElement.style.setProperty("--row-h", `${prefs.rowH}px`);
    document.body.classList.toggle("compact", prefs.rowH < COMPACT_BELOW);
  };
  applyH();
  let t;
  slider.oninput = () => {
    prefs.rowH = +slider.value; applyH();
    clearTimeout(t); t = setTimeout(() => { savePrefs(); rerenderAllCharts(); }, 120);
  };

  $("syncNow").onclick = startSync;

  const dlg = $("settings"), form = $("settingsForm");
  $("openSettings").onclick = () => {
    for (const k of ["y_min", "y_max", "fallback_max_hr"]) form.elements[k].value = settings[k];
    $("settingsErr").hidden = true;
    dlg.showModal();
  };
  form.addEventListener("submit", async (e) => {
    if (e.submitter?.value !== "save") return;
    e.preventDefault();
    const body = Object.fromEntries(["y_min", "y_max", "fallback_max_hr"].map((k) => [k, +form.elements[k].value]));
    try {
      settings = await api("/api/settings", { method: "PUT", body: JSON.stringify(body) });
      dlg.close();
      hrCache.clear();
      await loadWorkouts();
      rerenderAllCharts();
    } catch (err) { $("settingsErr").textContent = err.message; $("settingsErr").hidden = false; }
  });

  // Keep charts sized to the container, and recolor on light/dark switch.
  let rt;
  new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => {
    for (const r of rows.values()) if (r.chart) { const box = r.el.querySelector(".chart"); r.chart.setSize({ width: box.clientWidth, height: box.clientHeight }); }
  }, 100); }).observe($("list"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => rerenderAllCharts());
  setInterval(() => { if (lastStatus && !lastStatus.sync.running) renderSync(lastStatus); }, 30000);
}

// ------------------------------------------------------------------ boot ----
(async function boot() {
  initControls();
  try { await loadWorkouts(); } catch (e) { showBanner("error", `Can't load workouts: ${e.message}`); }
  const st = await api("/api/status").catch(() => null);
  if (st?.auth === "ok" && !st.sync.running) await api("/api/sync", { method: "POST" }).catch(() => {});
  refreshStatus();
})();
