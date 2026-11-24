import json

import pytest

from app.db import DB
from app.google import AuthRequired, RateLimited
from app.sync import SyncManager
from fake_google import FakeGoogle, exercise

BASE, DAY = 1_760_000_000, 86400


def make(n=60, **kw):
    g = FakeGoogle(n_workouts=n, base=BASE, spacing=DAY, **kw)
    db = DB(":memory:")
    now = BASE + n * DAY
    return g, db, SyncManager(db, g, now=lambda: now)


def count(db, where="1=1"):
    return db.q(f"SELECT COUNT(*) c FROM workouts WHERE {where}")[0]["c"]


def test_full_backfill_stores_everything():
    g, db, s = make(60)
    s.run()
    assert s.state["error"] is None
    assert count(db) == 60
    assert db.kv_get("backfill_complete") is True
    assert count(db, "hr_status='ok'") == 60
    assert count(db, "zones_json IS NOT NULL AND zones_date = civil_date") == 60
    w = db.workouts()[0]
    assert w["activity_type"] == "RUNNING" and w["duration_s"] == 1800
    assert w["max_hr"] == 159 and w["min_hr"] == 100  # samples ramp 100..159 within the 30-min window
    assert db.kv_get("last_synced_at") is not None


def test_backfill_resumes_after_interruption_without_duplicates():
    g, db, s = make(80, fail_after=2)  # dies after 2 exercise pages
    s.run()
    assert s.state["error_kind"] == "api"
    partial = count(db)
    assert 0 < partial < 80
    assert not db.kv_get("backfill_complete", False)

    g.fail_after = None
    s.run()
    assert s.state["error"] is None
    assert count(db) == 80
    assert db.kv_get("backfill_complete") is True
    assert count(db, "hr_status='ok'") == 80


def test_heart_rate_phase_resumes_only_remaining():
    g, db, s = make(30)
    # 2 exercise pages + 1 zone chunk, then 10 HR calls succeed, then fail
    g.fail_after = 2 + 1 + 10
    s.run()
    assert count(db) == 30
    assert count(db, "hr_status='ok'") == 10
    assert count(db, "hr_status='pending'") == 20

    g.fail_after = None
    before = len(g.hr_calls)
    s.run()
    assert count(db, "hr_status='ok'") == 30
    assert len(g.hr_calls) - before == 20  # nothing fetched twice


def test_incremental_sync_fetches_only_new():
    g, db, s = make(40)
    s.run()
    hr_before = len(g.hr_calls)
    g.workouts.append(exercise(999, BASE + 40 * DAY - 3600, minutes=20, kind="ROWING_MACHINE"))
    s.run()
    assert count(db) == 41
    assert len(g.hr_calls) - hr_before == 1
    assert db.workouts()[0]["activity_type"] == "ROWING_MACHINE"


def test_hr_window_includes_post_workout_padding():
    g, db, s = make(1)
    s.run()
    start, end = g.hr_calls[0]
    w = db.workouts()[0]
    assert end > start
    from app.sync import parse_ts
    assert int(parse_ts(end)) == w["end_ts"] + 180


def test_resync_does_not_reset_unchanged_workouts():
    g, db, s = make(10)
    s.run()
    db.x("UPDATE workouts SET hr_status='ok'")
    s.backfill_done = True
    calls = len(g.hr_calls)
    s.run()  # incremental re-fetches the last few days, but nothing changed
    assert count(db, "hr_status='pending'") == 0
    assert len(g.hr_calls) == calls


def test_full_rebackfill_keeps_local_rows():
    """A forced re-backfill (cursor reset) upserts in place: no row is deleted or duplicated."""
    g, db, s = make(30)
    s.run()
    db.x("CREATE TABLE IF NOT EXISTS local_note(workout_id TEXT PRIMARY KEY, note TEXT)")
    wid = db.workouts()[5]["id"]
    db.x("INSERT INTO local_note VALUES(?, 'keep me')", (wid,))
    db.kv_set("backfill_complete", False)
    db.kv_set("backfill_before", None)
    s.run()
    assert count(db) == 30
    assert db.q("SELECT note FROM local_note WHERE workout_id=?", (wid,))[0]["note"] == "keep me"
    assert db.workout(wid) is not None


def test_auth_error_is_reported():
    g, db, s = make(5)
    g.token = False
    s.run()
    assert s.state["error_kind"] == "auth"
    assert s.state["running"] is False


def test_rate_limit_is_reported_and_resumable():
    g, db, s = make(5, fail_after=1, fail_with=RateLimited(429, "quota"))
    s.run()
    assert s.state["error_kind"] == "rate"
    g.fail_after = None
    s.run()
    assert count(db) == 5 and s.state["error"] is None


def test_no_workouts_yet():
    g, db, s = make(0)
    s.run()
    assert s.state["error"] is None
    assert count(db) == 0
    assert db.kv_get("backfill_complete") is True
