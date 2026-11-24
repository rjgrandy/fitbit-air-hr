import json

import pytest

from app.google import SCOPES, ApiError, AuthRequired, GoogleHealth


def gh(tmp_path, responses):
    g = GoogleHealth("cid", "secret", tmp_path / "token.json", sleep=lambda s: None)
    calls = []

    def fake_post(url, data):
        calls.append(data)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    g._post_form = fake_post
    return g, calls


def test_exchange_code_accepts_full_redirect_url(tmp_path):
    g, calls = gh(tmp_path, [{"access_token": "a", "expires_in": 3600, "refresh_token": "r", "scope": " ".join(SCOPES)}])
    g.exchange_code("https://www.google.com/?code=4%2Fabc&scope=x")
    assert calls[0]["code"] == "4/abc" and calls[0]["redirect_uri"] == "https://www.google.com"
    tok = json.loads((tmp_path / "token.json").read_text())
    assert tok["refresh_token"] == "r"
    assert oct((tmp_path / "token.json").stat().st_mode)[-3:] == "600"


def test_exchange_code_requires_both_scopes(tmp_path):
    g, _ = gh(tmp_path, [{"access_token": "a", "refresh_token": "r", "scope": SCOPES[0]}])
    with pytest.raises(ValueError, match="Both"):
        g.exchange_code("abc")


def test_revoked_refresh_token_requires_reauth(tmp_path):
    (tmp_path / "token.json").write_text(json.dumps({"refresh_token": "r", "access_token": "old", "expires_in": 0, "obtained_at": 0}))
    g, _ = gh(tmp_path, [ApiError(400, '{"error": "invalid_grant"}')])
    with pytest.raises(AuthRequired):
        g._access_token()
    assert g.was_revoked() and not g.has_token()


def test_access_token_refreshes_and_keeps_refresh_token(tmp_path):
    (tmp_path / "token.json").write_text(json.dumps({"refresh_token": "r", "access_token": "old", "expires_in": 0, "obtained_at": 0}))
    g, calls = gh(tmp_path, [{"access_token": "new", "expires_in": 3600}])
    assert g._access_token() == "new"
    assert g._access_token() == "new" and len(calls) == 1  # cached until near expiry
    assert json.loads((tmp_path / "token.json").read_text())["refresh_token"] == "r"
