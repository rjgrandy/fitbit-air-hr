"""Heart-rate zone, time-in-zone and summary-statistic calculations (pure functions)."""
from __future__ import annotations

import bisect

ZONE_TYPES = ["LIGHT", "MODERATE", "VIGOROUS", "PEAK"]
# Fallback zones as % of max HR, used only when Google zones aren't available.
FALLBACK_PCT = {"LIGHT": 0.50, "MODERATE": 0.60, "VIGOROUS": 0.70, "PEAK": 0.85}


def parse_google_zones(daily_zones_point: dict) -> list[dict]:
    """DataPoint.dailyHeartRateZones -> [{'type','min','max'}] sorted by min."""
    zs = (daily_zones_point.get("dailyHeartRateZones") or {}).get("heartRateZones") or []
    out = [{"type": z.get("heartRateZoneType", "HEART_RATE_ZONE_TYPE_UNSPECIFIED"),
            "min": int(z["minBeatsPerMinute"]), "max": int(z["maxBeatsPerMinute"])} for z in zs
           if "minBeatsPerMinute" in z and "maxBeatsPerMinute" in z]
    out = [z for z in out if z["type"] in ZONE_TYPES]
    return sorted(out, key=lambda z: z["min"])


def fallback_zones(max_hr: int) -> list[dict]:
    """Zones from % of a user-set max HR."""
    max_hr = int(max_hr)
    mins = [round(max_hr * FALLBACK_PCT[t]) for t in ZONE_TYPES]
    out = []
    for i, t in enumerate(ZONE_TYPES):
        hi = mins[i + 1] - 1 if i + 1 < len(mins) else max_hr
        out.append({"type": t, "min": mins[i], "max": hi})
    return out


def choose_zones(daily: dict[str, list[dict]], civil_date: str | None) -> tuple[list[dict] | None, str | None]:
    """Zones in effect on `civil_date`: that day's, else the nearest earlier day, else the nearest later day."""
    if not daily or not civil_date:
        return None, None
    dates = sorted(daily)
    i = bisect.bisect_right(dates, civil_date)
    if i > 0:
        d = dates[i - 1]
    else:
        d = dates[0]
    return daily[d], d


def zone_index(bpm: float, zones: list[dict]) -> int:
    """Index into `zones` (sorted by min) for a bpm value, or -1 if below the lowest zone.
    Values above the top zone's max count as the top zone."""
    idx = -1
    for i, z in enumerate(zones):
        if bpm >= z["min"]:
            idx = i
    return idx


def time_in_zones(samples: list[list[int]] | list[tuple[int, int]], zones: list[dict],
                  start_ts: int, end_ts: int, gap_s: int = 30) -> dict:
    """Seconds spent in each zone during [start_ts, end_ts).

    Each sample's bpm is held until the next sample (capped at the workout end). An interval longer
    than `gap_s` is a gap in the data and isn't counted toward any zone.
    Returns {'below': s, 'LIGHT': s, 'MODERATE': s, ...}.
    """
    out = {"below": 0.0, **{z["type"]: 0.0 for z in zones}}
    pts = [(t, b) for t, b in samples if start_ts <= t < end_ts]
    for (t, bpm), nxt in zip(pts, pts[1:] + [None]):
        t_next = nxt[0] if nxt else end_ts
        dt = min(t_next, end_ts) - t
        if dt <= 0 or dt > gap_s:
            continue
        i = zone_index(bpm, zones)
        out["below" if i < 0 else zones[i]["type"]] += dt
    return {k: round(v) for k, v in out.items()}


def sample_stats(samples, start_ts: int, end_ts: int) -> dict:
    vals = [b for t, b in samples if start_ts <= t < end_ts]
    if not vals:
        return {"count": 0, "avg": None, "max": None, "min": None}
    return {"count": len(vals), "avg": round(sum(vals) / len(vals)), "max": max(vals), "min": min(vals)}
