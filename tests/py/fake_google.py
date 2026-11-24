"""In-memory stand-in for the Google Health API, mimicking v4 list semantics:
exercise pages of 25, newest first, civil_start_time filters, opaque page tokens."""
import datetime as dt

from app.google import AuthRequired, RateLimited

UTC_OFFSET = -6 * 3600  # America/Chicago in winter


def civil(ts):
    d = dt.datetime.fromtimestamp(ts + UTC_OFFSET, dt.timezone.utc)
    return {"date": {"year": d.year, "month": d.month, "day": d.day},
            "time": {"hours": d.hour, "minutes": d.minute, "seconds": d.second}}


def civil_str(ts):
    return dt.datetime.fromtimestamp(ts + UTC_OFFSET, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def exercise(i, start, minutes=30, kind="RUNNING"):
    end = start + minutes * 60
    return {"name": f"users/123/dataTypes/exercise/dataPoints/ex{i:05d}",
            "exercise": {"exerciseType": kind, "displayName": kind.title(),
                         "interval": {"startTime": iso(start), "endTime": iso(end), "startUtcOffset": "-21600s",
                                      "civilStartTime": civil(start)},
                         "metricsSummary": {"averageHeartRateBeatsPerMinute": "130", "caloriesKcal": 200.0},
                         "updateTime": iso(end + 60)}}


class FakeGoogle:
    def __init__(self, n_workouts=60, base=1_760_000_000, spacing=86400, fail_after=None, fail_with=None):
        self.workouts = [exercise(i, base + i * spacing) for i in range(n_workouts)]
        self.calls = 0
        self.fail_after = fail_after
        self.fail_with = fail_with or RuntimeError("simulated network drop")
        self.on_backoff = None
        self.hr_calls = []
        self.token = True
        self.spo2 = []        # [(epoch_s, pct)]
        self.spo2_daily = {}  # 'YYYY-MM-DD' -> (avg, lower, upper)
        self.spo2_calls = []

    def _tick(self):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise self.fail_with

    def has_token(self):
        return self.token

    def list_exercises(self, page_token=None, civil_start_before=None, civil_start_from=None):
        self._tick()
        ws = sorted(self.workouts, key=lambda w: w["exercise"]["interval"]["startTime"], reverse=True)

        def cs(w):
            c = w["exercise"]["interval"]["civilStartTime"]
            d, t = c["date"], c["time"]
            return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}T{t['hours']:02d}:{t['minutes']:02d}:{t['seconds']:02d}"
        if civil_start_before:
            ws = [w for w in ws if cs(w) < civil_start_before]
        if civil_start_from:
            ws = [w for w in ws if cs(w) >= civil_start_from]
        off = int(page_token or 0)
        page = ws[off:off + 25]
        out = {"dataPoints": page}
        if off + 25 < len(ws):
            out["nextPageToken"] = str(off + 25)
        return out

    def heart_rate(self, start, end):
        self._tick()
        self.hr_calls.append((start, end))
        s = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
        pts = []
        for k in range(0, 600, 3):  # 10 min of samples every 3 s, ramping 100 -> 160
            pts.append({"heartRate": {"beatsPerMinute": str(100 + k // 10),
                                      "sampleTime": {"physicalTime": iso(s + k)}}})
        return pts

    def daily_zones(self, a, b):
        self._tick()
        out, d = [], dt.date.fromisoformat(a)
        while d.isoformat() < b:
            out.append({"dailyHeartRateZones": {
                "date": {"year": d.year, "month": d.month, "day": d.day},
                "heartRateZones": [
                    {"heartRateZoneType": "LIGHT", "minBeatsPerMinute": "30", "maxBeatsPerMinute": "108"},
                    {"heartRateZoneType": "MODERATE", "minBeatsPerMinute": "109", "maxBeatsPerMinute": "131"},
                    {"heartRateZoneType": "VIGOROUS", "minBeatsPerMinute": "132", "maxBeatsPerMinute": "161"},
                    {"heartRateZoneType": "PEAK", "minBeatsPerMinute": "162", "maxBeatsPerMinute": "220"}]}})
            d += dt.timedelta(days=1)
        return out

    def oxygen_saturation(self, start, end):
        self._tick()
        self.spo2_calls.append((start, end))
        a = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
        b = dt.datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
        return [{"oxygenSaturation": {"percentage": p, "sampleTime": {"physicalTime": iso(t), "utcOffset": "-21600s"}}}
                for t, p in sorted(self.spo2, reverse=True) if a <= t < b]

    def daily_oxygen_saturation(self, a, b):
        self._tick()
        out = []
        for d, (avg, lo, hi) in sorted(self.spo2_daily.items(), reverse=True):
            if a <= d < b:
                y, m, dd = map(int, d.split("-"))
                out.append({"dailyOxygenSaturation": {"date": {"year": y, "month": m, "day": dd},
                            "averagePercentage": avg, "lowerBoundPercentage": lo, "upperBoundPercentage": hi}})
        return out
