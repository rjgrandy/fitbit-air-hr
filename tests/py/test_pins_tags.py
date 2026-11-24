from fastapi.testclient import TestClient

from app.db import DB, normalize_tags
from app.main import create_app
from app.sync import SyncManager
from fake_google import FakeGoogle
from test_api import G

BASE, DAY = 1_760_000_000, 86400


def test_normalize_tags():
    assert normalize_tags(["  zone   2 ", "Race", "race", "", "x" * 60]) == ["zone 2", "Race", "x" * 40]


def test_pins_and_tags_survive_resync_and_full_rebackfill():
    g = FakeGoogle(n_workouts=30, base=BASE, spacing=DAY)
    db = DB(":memory:")
    s = SyncManager(db, g, now=lambda: BASE + 31 * DAY)
    s.run()
    wid = db.workouts()[3]["id"]
    db.set_pinned(wid, True, 1)
    db.set_tags(wid, ["race", "zone 2"])

    s.run()  # normal re-sync
    # full re-backfill from scratch, with Google changing the workout so it's re-fetched
    g.workouts[-4]["exercise"]["updateTime"] = "2030-01-01T00:00:00Z"
    db.kv_set("backfill_complete", False)
    db.kv_set("backfill_before", None)
    s.run()
    assert db.workout(wid)["hr_status"] == "ok"
    assert db.is_pinned(wid)
    assert db.tags_for(wid) == ["race", "zone 2"]


def test_pin_and_tag_api():
    g = G(n_workouts=3)
    app = create_app(DB(":memory:"), g)
    app.state.sync.run()
    c = TestClient(app)
    ws = c.get("/api/workouts").json()
    a, b = ws[0]["id"], ws[1]["id"]
    assert ws[0]["pinned"] is False and ws[0]["tags"] == []

    assert c.put(f"/api/workouts/{a}/pin", json={"pinned": True}).json() == {"pinned": True}
    assert c.put(f"/api/workouts/{a}/tags", json={"tags": ["Race", " sick "]}).json()["tags"] == ["Race", "sick"]
    c.put(f"/api/workouts/{b}/tags", json={"tags": ["race"]})
    tags = c.get("/api/tags").json()
    assert tags[0] == {"tag": "Race", "count": 2} and {"tag": "sick", "count": 1} in tags

    w = {x["id"]: x for x in c.get("/api/workouts").json()}
    assert w[a]["pinned"] and w[a]["tags"] == ["Race", "sick"] and not w[b]["pinned"]
    d = c.get(f"/api/workouts/{a}").json()
    assert d["pinned"] and d["tags"] == ["Race", "sick"]

    assert c.put(f"/api/workouts/{a}/pin", json={"pinned": False}).json() == {"pinned": False}
    assert c.put(f"/api/workouts/{a}/tags", json={"tags": []}).json()["tags"] == []
    assert c.put(f"/api/workouts/{a}/tags", json={"tags": "race"}).status_code == 400
    assert c.put("/api/workouts/nope/pin", json={"pinned": True}).status_code == 404
