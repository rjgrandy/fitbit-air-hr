"""Error states: retries/backoff, rate limiting, API and network errors, expired access tokens."""
import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app import google as G
from app.db import DB
from app.google import ApiError, AuthRequired, GoogleHealth, RateLimited
from app.main import create_app
from app.sync import SyncManager
from fake_google import FakeGoogle


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body="{}", headers=None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(body.encode()))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A GoogleHealth whose HTTP layer replays a scripted list of responses."""
    (tmp_path / "token.json").write_text(json.dumps(
        {"refresh_token": "r", "access_token": "a", "expires_in": 3600, "obtained_at": 10**10}))
    waits = []
    g = GoogleHealth("cid", "secret", tmp_path / "token.json", sleep=lambda s: None,
                     on_backoff=lambda w, st: waits.append((round(w, 1), st)))
    monkeypatch.setattr(G, "MIN_INTERVAL", 0)
    script, seen = [], []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        r = script.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResp(json.dumps(r).encode())
    monkeypatch.setattr(G.urllib.request, "urlopen", fake_urlopen)
    return g, script, waits, seen


def test_retries_transient_errors_with_growing_backoff(client):
    g, script, waits, _ = client
    script += [http_error(503), http_error(500), urllib.error.URLError("reset"), {"ok": 1}]
    assert g.get("x") == {"ok": 1}
    assert [st for _, st in waits] == [503, 500, 0]
    assert waits[0][0] < waits[1][0] <= 8  # exponential with jitter


def test_retry_after_header_is_respected(client):
    g, script, waits, _ = client
    script += [http_error(429, headers={"Retry-After": "7"}), {"ok": 1}]
    assert g.get("x") == {"ok": 1}
    assert waits == [(7.0, 429)]


def test_persistent_rate_limit_raises_rate_limited(client):
    g, script, waits, _ = client
    script += [http_error(429)] * 7
    with pytest.raises(RateLimited):
        g.get("x")
    assert len(waits) == 6


def test_client_errors_are_not_retried(client):
    g, script, waits, _ = client
    script += [http_error(400, json.dumps({"error": {"code": 400, "message": "Invalid filter."}}))]
    with pytest.raises(ApiError) as e:
        g.get("x")
    assert waits == [] and e.value.friendly == "HTTP 400: Invalid filter."


def test_network_down_gives_a_readable_error(client):
    g, script, _, _ = client
    script += [urllib.error.URLError("Name or service not known")] * 7
    with pytest.raises(ApiError) as e:
        g.get("x")
    assert e.value.status == 0 and "internet connection" in e.value.friendly


def test_api_not_enabled_hint():
    e = ApiError(403, json.dumps({"error": {"message": "Google Health API has not been used in project 1",
                                            "status": "PERMISSION_DENIED"}}))
    assert "isn't enabled" in e.friendly


def test_401_refreshes_access_token_once_then_succeeds(client):
    g, script, _, seen = client
    script += [http_error(401), {"access_token": "new", "expires_in": 3600}, {"ok": 1}]
    assert g.get("x") == {"ok": 1}
    assert "oauth2.googleapis.com/token" in seen[1]
    assert json.loads(g.token_path.read_text())["access_token"] == "new"


def test_401_after_refresh_requires_reauth(client):
    g, script, _, _ = client
    script += [http_error(401), {"access_token": "new", "expires_in": 3600}, http_error(401)]
    with pytest.raises(AuthRequired):
        g.get("x")


def test_sync_reports_api_error_and_recovers():
    g = FakeGoogle(n_workouts=5, fail_after=2, fail_with=ApiError(500, "{}"))
    db = DB(":memory:")
    s = SyncManager(db, g)
    s.run()
    assert s.state["error_kind"] == "api" and "having trouble" in s.state["error"]
    g.fail_after = None
    s.run()
    assert s.state["error"] is None
    assert db.q("SELECT COUNT(*) c FROM workouts")[0]["c"] == 5


def test_unexpected_exception_does_not_kill_sync():
    g = FakeGoogle(n_workouts=3, fail_after=0, fail_with=RuntimeError("boom"))
    s = SyncManager(DB(":memory:"), g)
    s.run()
    assert s.state["error_kind"] == "api" and not s.state["running"]


def test_status_when_oauth_client_not_configured():
    class Unconfigured(FakeGoogle):
        configured = False
    c = TestClient(create_app(DB(":memory:"), Unconfigured(n_workouts=0)))
    assert c.get("/api/status").json()["auth"] == "unconfigured"
