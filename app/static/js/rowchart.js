// One uPlot heart-rate chart for a workout row: HR line over elapsed minutes,
// faint HR zone bands behind it, gaps broken, tap/hover tooltip.
import uPlot from "../vendor/uPlot.esm.js";
import { cssVar, zoneColor, fmtElapsed } from "./format.js";

export const GAP_SECONDS = 30;

/** Convert sample offsets (s) + bpm to uPlot data in minutes, inserting nulls across gaps
 *  so the line breaks instead of interpolating. Only samples within [0, untilSec] are kept. */
export function toSeries(t, bpm, untilSec) {
  const xs = [], ys = [];
  let prev = null;
  for (let i = 0; i < t.length; i++) {
    if (t[i] < 0 || t[i] > untilSec) continue;
    if (prev !== null && t[i] - prev > GAP_SECONDS) {
      xs.push((prev + 1) / 60);
      ys.push(null);
    }
    xs.push(t[i] / 60);
    ys.push(bpm[i]);
    prev = t[i];
  }
  return [xs, ys];
}

export function withAlpha(color, alpha) {
  // color is #rrggbb
  const m = /^#([0-9a-f]{6})$/i.exec(color);
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export function zoneBandsPlugin(zones) {
  return {
    hooks: {
      drawClear: [(u) => {
        const { ctx } = u;
        const { left, top, width, height } = u.bbox;
        const alpha = parseFloat(cssVar("--band-alpha")) || 0.13;
        ctx.save();
        ctx.beginPath();
        ctx.rect(left, top, width, height);
        ctx.clip();
        zones.forEach((z, i) => {
          const hi = i + 1 < zones.length ? zones[i + 1].min : Math.max(z.max + 1, u.scales.y.max);
          const y0 = u.valToPos(hi, "y", true), y1 = u.valToPos(z.min, "y", true);
          ctx.fillStyle = withAlpha(zoneColor(z.type), alpha);
          ctx.fillRect(left, y0, width, y1 - y0);
        });
        ctx.restore();
      }],
    },
  };
}

function tooltipPlugin(tip) {
  return {
    hooks: {
      setCursor: [(u) => {
        const i = u.cursor.idx;
        const y = i == null ? null : u.data[1][i];
        if (i == null || y == null) { tip.hidden = true; return; }
        tip.hidden = false;
        tip.textContent = `${fmtElapsed(u.data[0][i] * 60)} · ${y} bpm`;
        const x = u.valToPos(u.data[0][i], "x") + u.over.offsetLeft;
        const w = u.root.clientWidth;
        tip.style.left = `${Math.min(Math.max(x, 50), w - 50)}px`;
      }],
    },
  };
}

/**
 * @param {HTMLElement} el   container (positioned)
 * @param {object} o  { t, bpm, durationSec, xMaxMin, yMin, yMax, zones, compact, tip }
 */
export function makeRowChart(el, o) {
  const [xs, ys] = toSeries(o.t, o.bpm, o.durationSec);
  const fg = cssVar("--muted"), grid = cssVar("--grid");
  const axisX = {
    stroke: fg, grid: { stroke: grid, width: 1 }, ticks: { show: false }, size: 22, gap: 2,
    font: "11px system-ui", values: (u, vals) => vals.map((v) => `${Math.round(v)}′`),
  };
  const axisY = {
    stroke: fg, grid: { stroke: grid, width: 1 }, ticks: { show: false }, size: 30, gap: 2,
    font: "11px system-ui", splits: (u) => niceSplits(o.yMin, o.yMax, u.bbox.height),
  };
  const opts = {
    width: el.clientWidth || 300,
    height: el.clientHeight || 140,
    padding: o.compact ? [2, 0, 2, 0] : [6, 6, 0, 0],
    legend: { show: false },
    cursor: { y: false, points: { size: 6 }, drag: { x: false, y: false, setScale: false } },
    select: { show: false },
    scales: {
      x: { time: false, range: () => [0, Math.max(o.xMaxMin, 1)] },
      y: { range: () => [o.yMin, o.yMax] },
    },
    axes: o.compact ? [{ show: false }, { show: false }] : [axisX, axisY],
    series: [{}, { stroke: cssVar("--line"), width: 1.5, spanGaps: false, points: { show: false } }],
    plugins: [zoneBandsPlugin(o.zones), tooltipPlugin(o.tip)],
  };
  const u = new uPlot(opts, [xs, ys], el);
  attachTouch(u);
  return u;
}

export function niceSplits(min, max, px) {
  const n = Math.max(2, Math.min(6, Math.floor(px / 28)));
  const raw = (max - min) / n;
  const step = [10, 20, 25, 50, 100].find((s) => s >= raw) || 100;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max; v += step) out.push(v);
  return out;
}

// uPlot's cursor follows the mouse; on phones, follow the finger too (without blocking scroll).
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
