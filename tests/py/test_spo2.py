import datetime as dt

from fastapi.testclient import TestClient

from app.db import DB
from app.google import ApiError
from app.main import create_app
from app.sync import SyncManager, night_of, parse_spo2_points
from fake_google import FakeGoogle

# 2026-11-23 22:00 Chicago (UTC-6) = 2026-11-24 04:00Z
BED = int(dt.datetime(2026, 11, 24, 4, 0, tzinfo=dt.timezone.utc).timestamp())
NOW = BED + 2 * 86400
DAY = 86400


def night(g, start, minutes=480, dip_at=None):
    for k in range(minutes):
        p = 95 + (k % 3)
        if dip_at is not None and dip_at <= k < dip_at + 4:
            p = 87
        g.spo2.append((start + k * 60, float(p)))


def make(**kw):
    g = FakeGoogle(n_workouts=1, base=NOW - DAY, **kw)
    db = DB(":memory:")
    return g, db, SyncManager(db, g, now=lambda: NOW)


def test_night_of_uses_wake_date():
    assert night_of(BED, -21600) == "2026-11-24"            # 22:00 the evening before
    assert night_of(BED + 8 * 3600, -21600) == "2026-11-24"  # 06:00 waking up
    assert night_of(BED - 5 * 3600, -21600) == "2026-11-23"  # 17:00 nap belongs to that day
    assert night_of(BED, None, "America/Chicago") == "2026-11-24"


def test_parse_drops_bad_and_duplicate_points():
    pts = [{"oxygenSaturation": {"percentage": 96.04, "sampleTime": {"physicalTime": "2026-11-24T04:00:00Z"}}},
           {"oxygenSaturation": {"percentage": 97, "sampleTime": {"physicalTime": "2026-11-24T04:00:00.5Z"}}},
           {"oxygenSaturation": {"percentage": 12, "sampleTime": {"physicalTime": "2026-11-24T04:01:00Z"}}},
           {"oxygenSaturation": {"sampleTime": {"physicalTime": "2026-11-24T04:02:00Z"}}}]
    rows = parse_spo2_points(pts)
    assert [(r[0], r[1]) for r in rows] == [(BED, 97.0)]


def test_sync_groups_nights_and_stops_backfill():
    g, db, s = make()
    night(g, BED, dip_at=100)
    night(g, BED - DAY)
    night(g, BED - 70 * DAY, minutes=60)  # older history, found by the backfill
    g.spo2_daily = {"2026-11-24": (95.8, 93.1, 97.9)}
    s.run()
    assert s.state["error"] is None
    nights = db.spo2_nights()
    assert [n["night"] for n in nights] == ["2026-11-24", "2026-11-23", "2026-09-15"]
    n = nights[0]
    assert n["sample_count"] == 480 and n["min_pct"] == 87 and n["low_count"] == 4
    assert n["daily"] == {"avg_pct": 95.8, "lower_pct": 93.1, "upper_pct": 97.9, "sd_pct": None}
    assert db.kv_get("spo2_backfill_complete") is True
    calls = len(g.spo2_calls)
    s.run()  # incremental: only the last few days are fetched again
    assert len(g.spo2_calls) - calls == 1
    assert db.spo2_nights()[0]["sample_count"] == 480  # re-fetch replaced, didn't duplicate


def test_refetch_removes_points_google_dropped():
    g, db, s = make()
    night(g, BED)
    s.run()
    g.spo2 = [x for x in g.spo2 if x[0] < BED + 3600]
    s.run()
    assert db.spo2_nights()[0]["sample_count"] == 60


def test_spo2_failure_does_not_break_workout_sync():
    class G(FakeGoogle):
        def oxygen_saturation(self, a, b):
            raise ApiError(403, '{"error":{"message":"Request had insufficient authentication scopes."}}')
    g = G(n_workouts=3, base=NOW - 5 * DAY)
    db = DB(":memory:")
    s = SyncManager(db, g, now=lambda: NOW)
    s.run()
    assert s.state["error"] is None
    assert db.q("SELECT COUNT(*) c FROM workouts WHERE hr_status='ok'")[0]["c"] == 3
    assert "insufficient authentication scopes" in db.kv_get("spo2_error")
    assert db.kv_get("last_synced_at") == NOW


def test_api_lists_nights_and_samples():
    g = FakeGoogle(n_workouts=0)
    g.configured = True
    g.was_revoked = lambda: False
    night(g, BED, dip_at=10)
    g.spo2_daily = {"2026-11-24": (95.8, 93.1, 97.9), "2026-11-20": (96.0, 94.0, 98.0)}
    app = create_app(DB(":memory:"), g)
    app.state.sync.now = lambda: NOW
    app.state.sync.run()
    c = TestClient(app)
    r = c.get("/api/spo2").json()
    assert r["error"] is None and r["low_pct"] == 90
    first, daily_only = r["nights"][0], r["nights"][1]
    assert first["night"] == "2026-11-24" and first["low_minutes"] == 4
    # anchor = 18:00 Chicago the evening before
    assert first["anchor_ts"] == BED - 4 * 3600
    assert daily_only["night"] == "2026-11-20" and daily_only["sample_count"] == 0
    smp = c.get("/api/spo2/samples", params={"nights": "2026-11-24,2026-11-20"}).json()
    assert len(smp["2026-11-24"]["ts"]) == 480 and smp["2026-11-20"]["ts"] == []
    csv = c.get("/api/spo2/export.csv").text.splitlines()
    assert csv[0] == "night,time_utc,time_local,spo2_pct" and len(csv) == 481
    assert c.get("/spo2.html").status_code == 200
