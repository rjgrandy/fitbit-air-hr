// Formatting helpers. Timestamps arrive as UTC epoch seconds and are shown in the app's display time zone.

export const ZONE_ORDER = ["below", "LIGHT", "MODERATE", "VIGOROUS", "PEAK"];
export const ZONE_LABEL = { below: "Below", LIGHT: "Light", MODERATE: "Moderate", VIGOROUS: "Vigorous", PEAK: "Peak" };
export const ZONE_VAR = { below: "--z-below", LIGHT: "--z-light", MODERATE: "--z-moderate", VIGOROUS: "--z-vigorous", PEAK: "--z-peak" };

/** Escape text for use inside HTML (element content or a quoted attribute). */
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
export function zoneColor(type) { return cssVar(ZONE_VAR[type] || "--z-below"); }

let TZ = "America/Chicago";
let dateFmt, timeFmt;
export function setTimeZone(tz) {
  TZ = tz || TZ;
  dateFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric", year: "numeric" });
  timeFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
}
setTimeZone(TZ);

export const fmtDate = (ts) => dateFmt.format(new Date(ts * 1000));
export const fmtTime = (ts) => timeFmt.format(new Date(ts * 1000));

export function fmtDuration(s) {
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function fmtElapsed(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function fmtMinutes(sec) {
  const m = sec / 60;
  return m >= 10 || m === 0 ? `${Math.round(m)}m` : `${m.toFixed(1)}m`;
}

export function prettyType(activityType, displayName) {
  if (displayName && activityType === "OTHER") return displayName;
  if (displayName) return displayName;
  const s = (activityType || "Workout").replace(/^EXERCISE_TYPE_UNSPECIFIED$/, "Workout").toLowerCase().replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function relTime(ts) {
  if (!ts) return "never";
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}
