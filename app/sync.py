"""On-demand sync from Google Health into SQLite.

There is no scheduler. A sync runs only when the app is opened (or "Sync now" is pressed).
Every step commits as it goes, so an interrupted sync (container restart, network drop,
revoked token) picks up where it stopped next time:

1. Incremental: fetch workouts starting from a few days before the newest one stored.
2. Backfill: page backwards through older history. The resume cursor is the oldest
   workout start already seen, kept in the kv table, so it doesn't depend on page tokens.
3. Zones: fetch Google's daily HR zones for the dates workouts need, and snapshot
   the zones in effect onto each workout.
4. Heart rate: fetch raw HR samples for every workout still marked 'pending'.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import time
import traceback

from . import zones as Z
from .config import POST_WORKOUT_SECONDS
from .db import DB
from .google import ApiError, AuthRequired, GoogleHealth, RateLimited

RECENT_REFETCH_DAYS = 3        # re-check workouts and HR this far back on every sync (late band uploads)
HR_SETTLE_SECONDS = 6 * 3600   # HR fetched sooner than this after a workout ended is fetched again later


# ---------------------------------------------------------------- parsing ----
def parse_ts(s: str) -> float:
    s = s.strip().replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        n = 0
        while n < len(rest) and rest[n].isdigit():
            n += 1
        s = f"{head}.{rest[:n][:6].ljust(6, '0')}{rest[n:]}"
    return dt.datetime.fromisoformat(s).timestamp()


def rfc3339(ts: float) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_duration(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return round(float(s.rstrip("s")))
    except ValueError:
        return None


def parse_offset(s: str | None) -> int:
    return parse_duration(s) or 0


def civil_string(civil: dict | None, fallback_ts: float, offset_s: int) -> str:
    if civil and civil.get("date"):
        d, t = civil["date"], civil.get("time") or {}
        return (f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}T"
                f"{t.get('hours', 0):02d}:{t.get('minutes', 0):02d}:{t.get('seconds', 0):02d}")
    return dt.datetime.fromtimestamp(fallback_ts + offset_s, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_exercise(dp: dict, now: int) -> dict | None:
    ex = dp.get("exercise")
    if not ex or not ex.get("interval"):
        return None
    iv = ex["interval"]
    try:
        start, end = parse_ts(iv["startTime"]), parse_ts(iv["endTime"])
    except (KeyError, ValueError):
        return None
    name = dp.get("name") or ""
    wid = name.rsplit("/", 1)[-1] if "/dataPoints/" in name else \
        hashlib.sha1(f"{iv['startTime']}|{ex.get('exerciseType')}".encode()).hexdigest()[:20]
    civil = civil_string(iv.get("civilStartTime"), start, parse_offset(iv.get("startUtcOffset")))
    ms = ex.get("metricsSummary") or {}
    avg = ms.get("averageHeartRateBeatsPerMinute")
    return {
        "id": wid,
        "start_ts": int(start), "end_ts": int(end),
        "civil_start": civil, "civil_date": civil[:10],
        "activity_type": ex.get("exerciseType") or "EXERCISE_TYPE_UNSPECIFIED",
        "display_name": ex.get("displayName"),
        "duration_s": max(0, int(end - start)),
        "active_duration_s": parse_duration(ex.get("activeDuration")),
        "calories": ms.get("caloriesKcal"),
        "avg_hr": int(avg) if avg not in (None, "") else None,
        "raw_json": json.dumps(dp, separators=(",", ":")),
        "google_update_time": ex.get("updateTime"),
        "synced_at": now,
    }


def parse_hr_points(points: list[dict]) -> list[tuple[int, int]]:
    seen: dict[int, int] = {}
    for p in points:
        hr = p.get("heartRate") or {}
        try:
            t = int(parse_ts(hr["sampleTime"]["physicalTime"]))
            bpm = int(hr["beatsPerMinute"])
        except (KeyError, ValueError, TypeError):
            continue
        if 1 <= bpm <= 300:
            seen[t] = bpm
    return sorted(seen.items())


def zones_date(dp: dict) -> str | None:
    d = (dp.get("dailyHeartRateZones") or {}).get("date")
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}" if d else None


def add_days(date: str, n: int) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- manager ----
class SyncManager:
    def __init__(self, db: DB, client: GoogleHealth, now=lambda: int(time.time())):
        self.db, self.client, self.now = db, client, now
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.state = {"running": False, "phase": None, "done": 0, "total": 0, "message": "",
                      "error": None, "error_kind": None, "backoff_until": None}
        client.on_backoff = self._on_backoff

    def _on_backoff(self, wait: float, status: int) -> None:
        self.state["backoff_until"] = self.now() + int(wait)
        self.state["message"] = ("Google is rate-limiting requests; waiting" if status == 429
                                 else "Google API busy or unreachable; retrying") + f" {int(wait)} s…"

    def status(self) -> dict:
        return {**self.state,
                "last_synced_at": self.db.kv_get("last_synced_at"),
                "backfill_complete": bool(self.db.kv_get("backfill_complete", False)),
                "workout_count": self.db.q("SELECT COUNT(*) c FROM workouts")[0]["c"],
                "hr_pending": self.db.q("SELECT COUNT(*) c FROM workouts WHERE hr_status='pending'")[0]["c"]}

    def start(self) -> bool:
        with self._lock:
            if self.state["running"]:
                return False
            self.state.update(running=True, phase="starting", done=0, total=0, message="",
                              error=None, error_kind=None, backoff_until=None)
            self._thread = threading.Thread(target=self.run, daemon=True, name="sync")
            self._thread.start()
            return True

    def run(self) -> None:
        """Run one full sync. Safe to call directly (tests) or via start()."""
        self.state.update(running=True, error=None, error_kind=None, backoff_until=None)
        try:
            if not self.client.has_token():
                raise AuthRequired("Not authorized yet.")
            self._phase("workouts", "Checking for new workouts…")
            self.incremental()
            if not self.db.kv_get("backfill_complete", False):
                self._phase("backfill", "Loading workout history…")
                self.backfill()
            self._phase("zones", "Loading heart-rate zones…")
            self.sync_zones()
            self._phase("heart rate", "Loading heart-rate samples…")
            self.fetch_heart_rate()
            self.db.kv_set("last_synced_at", self.now())
            self._phase(None, "")
        except AuthRequired as e:
            self._fail("auth", str(e))
        except RateLimited:
            self._fail("rate", "Google is rate-limiting requests. Sync paused; it will continue next time.")
        except ApiError as e:
            self._fail("api", f"Google Health API error: {e}")
        except Exception as e:  # never let the thread die silently
            traceback.print_exc()
            self._fail("api", f"Sync failed: {e}")
        finally:
            self.state["running"] = False
            self.state["backoff_until"] = None

    def _phase(self, phase, message):
        self.state.update(phase=phase, message=message, done=0, total=0)

    def _fail(self, kind, msg):
        self.state.update(error=msg, error_kind=kind, phase=None, message="")

    # ---- 1. new workouts ----
    def incremental(self) -> int:
        newest = self.db.q("SELECT MAX(civil_date) d FROM workouts")[0]["d"]
        if not newest:
            # Nothing stored yet (first run, or the account had no workouts last time):
            # (re)start the backfill from the newest workout so a first workout is always found.
            self.db.kv_set("backfill_complete", False)
            self.db.kv_set("backfill_before", None)
            return 0
        since = add_days(newest, -RECENT_REFETCH_DAYS)
        token, n = None, 0
        while True:
            page = self.client.list_exercises(page_token=token, civil_start_from=since)
            n += self._store_page(page.get("dataPoints", []))[0]
            token = page.get("nextPageToken")
            if not token:
                break
        # HR for recently finished workouts may still be uploading from the band: re-fetch later.
        self.db.x("""UPDATE workouts SET hr_status='pending'
                     WHERE hr_status IN ('ok','none') AND end_ts > ? AND (hr_fetched_at IS NULL OR hr_fetched_at < end_ts + ?)""",
                  (self.now() - RECENT_REFETCH_DAYS * 86400, HR_SETTLE_SECONDS))
        return n

    # ---- 2. history ----
    def backfill(self) -> None:
        while True:
            cursor = self.db.kv_get("backfill_before")  # civil start; fetch workouts starting before it
            page = self.client.list_exercises(civil_start_before=cursor)
            new, oldest = self._store_page(page.get("dataPoints", []))
            self.state["done"] = self.db.q("SELECT COUNT(*) c FROM workouts")[0]["c"]
            if oldest:
                self.state["message"] = f"Loading workout history… back to {oldest[:10]}"
            if not oldest or (cursor and oldest >= cursor) or (new == 0 and not page.get("nextPageToken")):
                self.db.kv_set("backfill_complete", True)
                return
            # +1 s so a workout starting in the same second as `oldest` isn't skipped; duplicates are upserts.
            nxt = (dt.datetime.fromisoformat(oldest) + dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")
            if cursor and nxt >= cursor:
                nxt = oldest  # a full page of workouts in one second: step past it
            self.db.kv_set("backfill_before", nxt)
            if not page.get("nextPageToken"):
                self.db.kv_set("backfill_complete", True)
                return

    def _store_page(self, points: list[dict]) -> tuple[int, str | None]:
        new, oldest, now = 0, None, self.now()
        for dp in points:
            w = parse_exercise(dp, now)
            if not w:
                continue
            exists = bool(self.db.q("SELECT 1 FROM workouts WHERE id=?", (w["id"],)))
            self.db.upsert_workout(w)
            new += 0 if exists else 1
            if oldest is None or w["civil_start"] < oldest:
                oldest = w["civil_start"]
        return new, oldest

    # ---- 3. zones ----
    def sync_zones(self) -> None:
        r = self.db.q("""SELECT MIN(civil_date) lo, MAX(civil_date) hi FROM workouts
                         WHERE zones_date IS NULL OR zones_date != civil_date""")[0]
        if r["lo"]:
            lo, hi = add_days(r["lo"], -7), add_days(r["hi"], 1)
            have_lo = self.db.kv_get("zones_fetched_from")
            have_hi = self.db.kv_get("zones_fetched_to")  # exclusive
            ranges = []
            if not have_lo:
                ranges.append((lo, add_days(self._today(), 1)))
            else:
                if lo < have_lo:
                    ranges.append((lo, have_lo))
                ranges.append((min(add_days(have_hi, -2), hi), add_days(self._today(), 1)))
            for a, b in ranges:
                while a < b:  # chunk requests to 90 days
                    c = min(add_days(a, 90), b)
                    for dp in self.client.daily_zones(a, c):
                        d, zs = zones_date(dp), Z.parse_google_zones(dp)
                        if d and zs:
                            self.db.x("INSERT INTO daily_zones(date,zones_json) VALUES(?,?) "
                                      "ON CONFLICT(date) DO UPDATE SET zones_json=excluded.zones_json",
                                      (d, json.dumps(zs)))
                    a = c
            self.db.kv_set("zones_fetched_from", min(lo, have_lo or lo))
            self.db.kv_set("zones_fetched_to", add_days(self._today(), 1))
        self.assign_zones()

    def assign_zones(self) -> None:
        daily = {r["date"]: json.loads(r["zones_json"]) for r in self.db.q("SELECT * FROM daily_zones")}
        if not daily:
            return
        for w in self.db.q("SELECT id, civil_date FROM workouts WHERE zones_date IS NULL OR zones_date != civil_date"):
            zs, d = Z.choose_zones(daily, w["civil_date"])
            if zs:
                self.db.x("UPDATE workouts SET zones_json=?, zones_date=? WHERE id=?", (json.dumps(zs), d, w["id"]))

    def _today(self) -> str:
        return dt.datetime.fromtimestamp(self.now(), dt.timezone.utc).date().isoformat()

    # ---- 4. heart rate ----
    def fetch_heart_rate(self) -> None:
        pending = self.db.q("SELECT id,start_ts,end_ts FROM workouts WHERE hr_status='pending' ORDER BY start_ts DESC")
        self.state["total"] = len(pending)
        for i, w in enumerate(pending):
            points = self.client.heart_rate(rfc3339(w["start_ts"]), rfc3339(w["end_ts"] + POST_WORKOUT_SECONDS))
            samples = parse_hr_points(points)
            self.db.save_samples(w["id"], samples, Z.sample_stats(samples, w["start_ts"], w["end_ts"]), self.now())
            self.state["done"] = i + 1
