// Live heart rate from the band over Web Bluetooth (Heart Rate Service 0x180D, measurement 0x2A37).
// Everything here runs in the browser. Readings are kept in memory only: nothing is saved or sent to the server.
import uPlot from "../vendor/uPlot.esm.js";
import { parseHeartRateMeasurement } from "./hrm.js";
import { LiveStats, LIVE_GAP_SECONDS, zoneIndex } from "./livestats.js";
import { zoneBandsPlugin, niceSplits } from "./rowchart.js";
import { cssVar, zoneColor, fmtElapsed, fmtMinutes, ZONE_ORDER, ZONE_LABEL } from "./format.js";

const $ = (id) => document.getElementById(id);
const HR_SERVICE = "heart_rate";                 // 0x180D
const HR_MEASUREMENT = "heart_rate_measurement"; // 0x2A37
const MAX_RETRIES = 5;
const STALE_SECONDS = 5;      // no packet for this long while connected → "waiting"
const KEEP_SECONDS = 1900;    // chart history kept in memory (a bit over the longest window)
const PREF_KEY = "fahr.live.v1";

// Fallback percentages match the server's (app/zones.py) for when the zones request fails.
const FALLBACK_PCT = [["LIGHT", .5], ["MODERATE", .6], ["VIGOROUS", .7], ["PEAK", .85]];
function fallbackZones(maxHr) {
  const mins = FALLBACK_PCT.map(([, p]) => Math.round(maxHr * p));
  return FALLBACK_PCT.map(([type], i) => ({ type, min: mins[i], max: i + 1 < mins.length ? mins[i + 1] - 1 : maxHr }));
}

let zones = fallbackZones(190);
let yMin = 50, yMax = 200;
let stats = new LiveStats(zones);
let t0 = null;               // session start (performance.now() seconds)
let lastPacketAt = null;     // performance.now() seconds
let device = null, characteristic = null, userDisconnect = false, connected = false, retrying = false;
let wakeLock = null;
let chart = null;
let windowSec = 600;

const now = () => performance.now() / 1000;
const elapsedNow = () => (t0 == null ? 0 : now() - t0);

// ---------- prefs (only the chart window length is remembered) ----------
function loadPrefs() {
  try { const p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}"); if (p.windowSec) windowSec = +p.windowSec; } catch { /* ignore */ }
  const sel = $("window");
  if (![...sel.options].some((o) => +o.value === windowSec)) windowSec = 600;
  sel.value = String(windowSec);
}
function savePrefs() { try { localStorage.setItem(PREF_KEY, JSON.stringify({ windowSec })); } catch { /* ignore */ } }

// ---------- status ----------
function setStatus(text, cls = "") { const s = $("status"); s.textContent = text; s.className = `status ${cls}`; }

function unsupportedReason() {
  if (!window.isSecureContext) return "This page isn't on HTTPS, so the browser blocks Bluetooth. Open it through your tailscale serve https:// address.";
  if (!("bluetooth" in navigator)) return "Web Bluetooth isn't available in this browser. Use Chrome on Android.";
  return null;
}

// ---------- zones ----------
async function loadZones() {
  try {
    const r = await fetch("/api/zones/current");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    if (Array.isArray(j.zones) && j.zones.length) zones = j.zones;
    if (j.settings) { yMin = j.settings.y_min ?? yMin; yMax = j.settings.y_max ?? yMax; }
    $("zonesNote").textContent = j.source === "google"
      ? `Zones from Google Health (${j.date})`
      : `Zones from max HR ${j.settings?.fallback_max_hr ?? 190} (no Google zones synced yet)`;
  } catch {
    $("zonesNote").textContent = "Couldn't load your zones, using defaults for max HR 190";
  }
  stats = new LiveStats(zones);
}

// ---------- chart ----------
function chartData() {
  return [stats.t.slice(), stats.bpm.slice()];
}

function buildChart() {
  const el = $("chart");
  el.innerHTML = "";
  const fg = cssVar("--muted"), grid = cssVar("--grid");
  const opts = {
    width: el.clientWidth || 320,
    height: el.clientHeight || 220,
    padding: [8, 20, 0, 0],
    legend: { show: false },
    cursor: { show: false },
    select: { show: false },
    scales: {
      x: { time: false, range: () => { const e = elapsedNow(); return [e - windowSec, Math.max(e, 1)]; } },
      y: { range: () => {
        const vals = stats.bpm.filter((v) => v != null);
        const lo = Math.min(yMin, ...(vals.length ? [Math.min(...vals) - 5] : []));
        const hi = Math.max(yMax, ...(vals.length ? [Math.max(...vals) + 5] : []));
        return [lo, hi];
      } },
    },
    axes: [
      { stroke: fg, grid: { stroke: grid, width: 1 }, ticks: { show: false }, size: 26, gap: 2, font: "12px system-ui",
        splits: (u) => timeSplits(u.scales.x.min, u.scales.x.max, u.bbox.width / devicePixelRatio),
        values: (u, vals) => vals.map((v) => (v < 0 ? "" : fmtElapsed(v))) },
      { stroke: fg, grid: { stroke: grid, width: 1 }, ticks: { show: false }, size: 34, gap: 2, font: "12px system-ui",
        splits: (u) => niceSplits(u.scales.y.min, u.scales.y.max, u.bbox.height / devicePixelRatio) },
    ],
    series: [{}, { stroke: cssVar("--line"), width: 2.5, spanGaps: false, points: { show: false } }],
    plugins: [zoneBandsPlugin(zones)],
  };
  chart = new uPlot(opts, chartData(), el);
}

function timeSplits(min, max, px) {
  const span = max - min;
  const n = Math.max(2, Math.min(8, Math.floor(px / 70)));
  const step = [10, 15, 30, 60, 120, 300, 600].find((s) => s >= span / n) || 600;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(v);
  return out;
}

function redraw() {
  if (!chart) return;
  chart.setData(chartData(), true);
}

new ResizeObserver(() => {
  const el = $("chart");
  if (chart && el.clientWidth) chart.setSize({ width: el.clientWidth, height: el.clientHeight });
}).observe($("chart"));

// ---------- numbers ----------
function paintBpm(bpm, stale) {
  const hero = document.querySelector(".hero");
  if (bpm == null) {
    $("bpm").textContent = "—";
    $("zoneName").textContent = "bpm";
    hero.style.setProperty("--zc", cssVar("--muted"));
    hero.classList.remove("stale");
    return;
  }
  const i = zoneIndex(bpm, zones);
  const type = i < 0 ? "below" : zones[i].type;
  $("bpm").textContent = String(bpm);
  $("zoneName").textContent = `bpm · ${ZONE_LABEL[type]}`;
  hero.style.setProperty("--zc", zoneColor(type));
  hero.classList.toggle("stale", !!stale);
}

function paintNumbers() {
  $("elapsed").textContent = fmtElapsed(elapsedNow());
  $("avg").textContent = stats.avg ?? "—";
  $("max").textContent = stats.max ?? "—";
  paintTiz();
}

function paintTiz() {
  const tiz = { ...stats.tiz };
  // Count the time since the last reading toward its zone, so the bar moves smoothly between packets.
  if (stats.last && stats.last.bpm != null && connected) {
    const dt = Math.min(elapsedNow() - stats.last.t, LIVE_GAP_SECONDS);
    if (dt > 0) { const i = zoneIndex(stats.last.bpm, zones); tiz[i < 0 ? "below" : zones[i].type] += dt; }
  }
  const keys = ZONE_ORDER.filter((k) => k !== "below" || tiz.below > 0);
  const total = keys.reduce((a, k) => a + (tiz[k] || 0), 0);
  $("tiz").innerHTML =
    `<div class="bar">${total ? keys.map((k) => `<span style="width:${(100 * (tiz[k] || 0)) / total}%;background:${zoneColor(k)}"></span>`).join("") : ""}</div>` +
    `<div class="chips">${keys.map((k) => `<span class="chip" style="--c:${zoneColor(k)}">${ZONE_LABEL[k]} ${fmtMinutes(tiz[k] || 0)}</span>`).join("")}</div>`;
}

// ---------- packets ----------
function onPacket(ev) {
  let m;
  try { m = parseHeartRateMeasurement(ev.target.value); } catch { return; }
  if (!m.bpm) return; // 0 bpm means no reading
  const n = now();
  if (t0 == null) t0 = n;
  if (lastPacketAt != null && n - lastPacketAt > LIVE_GAP_SECONDS) stats.gap(lastPacketAt - t0 + 1);
  lastPacketAt = n;
  stats.add(n - t0, m.bpm);
  trim();
  paintBpm(m.bpm, false);
  if (m.contactSupported && !m.contactDetected) setStatus("Live · band not touching skin", "warn");
  else setStatus("Live", "ok");
  paintNumbers();
  redraw();
}

function trim() {
  const cutoff = elapsedNow() - KEEP_SECONDS;
  let k = 0;
  while (k < stats.t.length && stats.t[k] < cutoff) k++;
  if (k > 0) { stats.t.splice(0, k); stats.bpm.splice(0, k); }
}

// Once a second: advance the chart, elapsed time and staleness even when no packets arrive.
setInterval(() => {
  if (t0 == null) return;
  if (connected && lastPacketAt != null) {
    const quiet = now() - lastPacketAt;
    if (quiet > LIVE_GAP_SECONDS) { stats.gap(lastPacketAt - t0 + 1); paintBpm(null); setStatus("Connected, no heart rate. Is sharing still on?", "warn"); }
    else if (quiet > STALE_SECONDS) { paintBpm(stats.last?.bpm ?? null, true); setStatus("Waiting for heart rate…", "warn"); }
  }
  paintNumbers();
  redraw();
}, 1000);

// ---------- Bluetooth ----------
function setConnectedUi(on) {
  connected = on;
  $("connect").disabled = $("connectAll").disabled = on;
  $("disconnect").hidden = !on;
  document.body.classList.toggle("is-live", on);
}

async function pick(showAll) {
  const opts = showAll
    ? { acceptAllDevices: true, optionalServices: [HR_SERVICE] }
    : { filters: [{ services: [HR_SERVICE] }] };
  let picked;
  try {
    picked = await navigator.bluetooth.requestDevice(opts);
  } catch (e) {
    if (e.name === "NotFoundError") { setStatus(device ? $("status").textContent : "No device chosen"); return; }
    setStatus(`Couldn't open the Bluetooth picker: ${e.message}`, "bad"); return;
  }
  teardown();  // never leave a previous band attached when switching devices
  device = picked;
  device.addEventListener("gattserverdisconnected", onDisconnected);
  userDisconnect = false;
  await connect();
}

function teardown() {
  if (!device) return;
  userDisconnect = true;
  device.removeEventListener("gattserverdisconnected", onDisconnected);
  characteristic?.removeEventListener("characteristicvaluechanged", onPacket);
  characteristic = null;
  if (device.gatt.connected) device.gatt.disconnect();
  device = null;
}

async function connect() {
  setStatus(`Connecting to ${device.name || "band"}…`);
  const server = await device.gatt.connect();
  const svc = await server.getPrimaryService(HR_SERVICE);
  const ch = await svc.getCharacteristic(HR_MEASUREMENT);
  characteristic?.removeEventListener("characteristicvaluechanged", onPacket);
  characteristic = ch;
  ch.addEventListener("characteristicvaluechanged", onPacket);
  await ch.startNotifications();
  if (t0 == null) t0 = now();
  lastPacketAt = now();  // start the "waiting" clock from the moment we're subscribed
  setConnectedUi(true);
  setStatus("Connected, waiting for heart rate…", "ok");
  $("connect").textContent = "Connect";
  requestWakeLock();
}

async function onDisconnected() {
  setConnectedUi(false);
  if (t0 != null) stats.gap(elapsedNow());
  paintBpm(null);
  if (userDisconnect) { setStatus("Disconnected"); releaseWakeLock(); return; }
  if (retrying) return;
  retrying = true;
  try {
    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
      setStatus(`Disconnected. Reconnecting (${attempt} of ${MAX_RETRIES})…`, "bad");
      await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** (attempt - 1), 15000)));
      if (userDisconnect || !device) return;
      try { await connect(); return; } catch { /* try again */ }
    }
    setStatus("Disconnected. Automatic reconnect failed.", "bad");
    $("connect").textContent = "Reconnect";
    releaseWakeLock();
  } finally { retrying = false; }
}

// ---------- screen wake lock ----------
async function requestWakeLock() {
  if (!("wakeLock" in navigator) || wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
  } catch { /* e.g. battery saver; the page still works */ }
}
function releaseWakeLock() { wakeLock?.release().catch(() => {}); wakeLock = null; }
document.addEventListener("visibilitychange", () => {
  // The browser drops the lock when the page is hidden; take it again when we come back.
  if (document.visibilityState === "visible" && connected) requestWakeLock();
});

// ---------- controls ----------
$("connect").addEventListener("click", async () => {
  try {
    if (device && $("connect").textContent === "Reconnect") { userDisconnect = false; await connect(); }
    else await pick(false);
  } catch (e) { setStatus(`Couldn't connect: ${e.message}. Is heart-rate sharing on?`, "bad"); $("connect").textContent = device ? "Reconnect" : "Connect"; }
});
$("connectAll").addEventListener("click", async () => {
  try { await pick(true); }
  catch (e) { setStatus(`Couldn't connect: ${e.message}. Is heart-rate sharing on?`, "bad"); $("connect").textContent = device ? "Reconnect" : "Connect"; }
});
$("disconnect").addEventListener("click", () => {
  userDisconnect = true;
  if (device?.gatt.connected) device.gatt.disconnect();
  setConnectedUi(false);
  paintBpm(null);
  setStatus("Disconnected");
  releaseWakeLock();
});
$("window").addEventListener("change", (e) => { windowSec = +e.target.value; savePrefs(); redraw(); });
$("reset").addEventListener("click", () => {
  stats.reset();
  t0 = now();
  if (lastPacketAt != null) lastPacketAt = now();
  paintNumbers();
  redraw();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { buildChart(); paintTiz(); });

// ---------- start ----------
async function init() {
  loadPrefs();
  await loadZones();
  buildChart();
  paintBpm(null);
  paintNumbers();
  const why = unsupportedReason();
  if (why) {
    $("unsupported").textContent = why; $("unsupported").hidden = false;
    $("connect").disabled = $("connectAll").disabled = true;
    setStatus("Bluetooth unavailable", "bad");
    return;
  }
  try {
    if (navigator.bluetooth.getAvailability && !(await navigator.bluetooth.getAvailability())) {
      $("unsupported").textContent = "Bluetooth looks turned off on this phone. Turn it on, then reload this page.";
      $("unsupported").hidden = false;
    }
  } catch { /* ignore */ }
}

// Test hook for headless checks (no Bluetooth there): feed fake readings.
if (new URLSearchParams(location.search).has("selftest")) window.__liveTest = { feed: (bpm) => onPacket({ target: { value: new DataView(new Uint8Array([0, bpm]).buffer) } }), stats: () => stats };

init();
