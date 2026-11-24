from fastapi.testclient import TestClient

from app.db import DB
from app.main import create_app
from fake_google import FakeGoogle


class G(FakeGoogle):
    configured = True

    def was_revoked(self):
        return not self.token

    def auth_url(self):
        return "https://accounts.google.com/o/oauth2/v2/auth?x=1"

    def exchange_code(self, code):
        if code != "good":
            raise ValueError("bad code")
        self.token = True
        return {}


def client(n=5, token=True):
    g = G(n_workouts=n)
    g.token = token
    app = create_app(DB(":memory:"), g)
    app.state.sync.run()
    return TestClient(app), app


def test_status_and_list_and_hr():
    c, app = client(5)
    st = c.get("/api/status").json()
    assert st["auth"] == "ok" and st["sync"]["workout_count"] == 5
    ws = c.get("/api/workouts").json()
    assert len(ws) == 5 and ws[0]["start_ts"] > ws[-1]["start_ts"]
    assert ws[0]["zones_source"] == "google" and len(ws[0]["zones"]) == 4
    hr = c.get("/api/hr", params={"ids": ws[0]["id"]}).json()[ws[0]["id"]]
    assert len(hr["t"]) == len(hr["bpm"]) > 0
    assert sum(hr["time_in_zones"].values()) > 0


def test_fallback_zones_follow_settings():
    c, app = client(2)
    app.state.db.x("UPDATE workouts SET zones_json=NULL")
    c.put("/api/settings", json={"fallback_max_hr": 200})
    w = c.get("/api/workouts").json()[0]
    assert w["zones_source"] == "fallback" and w["zones"][0]["min"] == 100


def test_settings_validation():
    c, _ = client(1)
    assert c.put("/api/settings", json={"y_min": 150, "y_max": 100}).status_code == 400
    assert c.put("/api/settings", json={"y_min": 40, "y_max": 210}).json()["y_max"] == 210


def test_revoked_shows_reauthorize_and_code_flow():
    c, app = client(0, token=False)
    assert c.get("/api/status").json()["auth"] == "revoked"
    assert c.post("/api/auth/code", json={"code": "nope"}).status_code == 400
    assert c.post("/api/auth/code", json={"code": "good"}).json()["ok"] is True


def test_detail_and_exports():
    c, app = client(2)
    wid = c.get("/api/workouts").json()[0]["id"]
    d = c.get(f"/api/workouts/{wid}").json()
    assert d["id"] == wid and len(d["t"]) == len(d["bpm"]) > 0
    assert {r["type"] for r in d["zone_breakdown"]} >= {"LIGHT", "PEAK"}
    assert set(d["recovery"]) == {"end_hr", "hr_1", "drop_1", "hr_2", "drop_2"}
    r = c.get(f"/api/workouts/{wid}/export.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "time_utc,time_local,elapsed_s,bpm,phase" and len(lines) == len(d["t"]) + 1
    j = c.get(f"/api/workouts/{wid}/export.json").json()
    assert len(j["samples"]) == len(d["t"])
    assert j["samples"][0]["time_local"][-6:] in ("-06:00", "-05:00")
    assert c.get("/api/workouts/nope").status_code == 404


def test_current_zones_for_live_page():
    c, app = client(3)
    z = c.get("/api/zones/current").json()
    assert z["source"] == "google" and [x["type"] for x in z["zones"]] == ["LIGHT", "MODERATE", "VIGOROUS", "PEAK"]
    app.state.db.x("DELETE FROM daily_zones")
    z = c.get("/api/zones/current").json()
    assert z["source"] == "fallback" and z["zones"][-1]["max"] == 190
