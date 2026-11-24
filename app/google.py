"""Read-only Google Health API v4 client (stdlib only).

Endpoints, data types and scopes were confirmed against the v4 discovery document
(revision 20261119) and developers.google.com/health:
  exercise               -> googlehealth.activity_and_fitness.readonly
  heart-rate             -> googlehealth.health_metrics_and_measurements.readonly
  daily-heart-rate-zones -> googlehealth.health_metrics_and_measurements.readonly
Only GET requests are ever made to health.googleapis.com.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://health.googleapis.com/v4"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "https://www.google.com"
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]
RETRY_STATUSES = (429, 500, 503, 504)
MIN_INTERVAL = 0.25  # seconds between requests: max 4 req/s, below the 300/min per-user quota


class AuthRequired(Exception):
    """No token yet, or the refresh token was revoked/expired: user must re-authorize."""


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status, self.body = status, body


class RateLimited(ApiError):
    pass


class GoogleHealth:
    def __init__(self, client_id: str, client_secret: str, token_path: Path,
                 sleep=time.sleep, on_backoff=None):
        self.client_id, self.client_secret = client_id, client_secret
        self.token_path = Path(token_path)
        self.sleep = sleep
        self.on_backoff = on_backoff or (lambda wait, status: None)
        self._lock = threading.Lock()
        self._last_request = 0.0

    # ------------------------------------------------------------ auth ----
    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def has_token(self) -> bool:
        try:
            return bool(json.loads(self.token_path.read_text()).get("refresh_token"))
        except (OSError, ValueError):
            return False

    def auth_url(self) -> str:
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": REDIRECT_URI, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        })

    def exchange_code(self, pasted: str) -> dict:
        """Accepts the bare code or the whole https://www.google.com/?code=... URL."""
        pasted = pasted.strip()
        code = pasted
        if pasted.startswith("http"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
            if "error" in qs:
                raise ValueError(f"Google returned an error: {qs['error'][0]}")
            code = qs.get("code", [""])[0]
        if not code:
            raise ValueError("No authorization code found in what you pasted.")
        try:
            tok = self._post_form(TOKEN_URL, {
                "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
                "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
            })
        except ApiError as e:
            if "invalid_grant" in e.body:
                raise ValueError("That code was already used or has expired. Start again and paste the new one.")
            raise
        if "refresh_token" not in tok:
            raise ValueError("Google didn't return a refresh token. Remove the app at "
                             "myaccount.google.com/permissions, then authorize again.")
        missing = [s for s in SCOPES if s not in tok.get("scope", "").split()]
        if missing:
            raise ValueError("Both Google Health permissions are needed. Authorize again and tick every box.")
        self._save_token(tok)
        return {"refresh_token_expires_in": tok.get("refresh_token_expires_in")}

    def _save_token(self, tok: dict) -> None:
        tok = dict(tok)
        tok["obtained_at"] = int(time.time())
        try:
            old = json.loads(self.token_path.read_text())
            tok.setdefault("refresh_token", old.get("refresh_token"))
        except (OSError, ValueError):
            pass
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tok, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.token_path)

    def _access_token(self) -> str:
        try:
            tok = json.loads(self.token_path.read_text())
        except (OSError, ValueError):
            raise AuthRequired("Not authorized yet.")
        if not tok.get("refresh_token"):
            raise AuthRequired("Not authorized yet.")
        if tok.get("access_token") and time.time() < tok.get("obtained_at", 0) + int(tok.get("expires_in", 0)) - 120:
            return tok["access_token"]
        try:
            new = self._post_form(TOKEN_URL, {
                "client_id": self.client_id, "client_secret": self.client_secret,
                "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
            })
        except ApiError as e:
            if e.status in (400, 401) and ("invalid_grant" in e.body or "unauthorized_client" in e.body):
                self._mark_revoked()
                raise AuthRequired("Google access was revoked or expired. Re-authorize to continue syncing.")
            raise
        self._save_token(new)
        return new["access_token"]

    def _mark_revoked(self) -> None:
        try:
            tok = json.loads(self.token_path.read_text())
            tok.pop("refresh_token", None)
            tok.pop("access_token", None)
            tok["revoked_at"] = int(time.time())
            self.token_path.write_text(json.dumps(tok, indent=2))
        except (OSError, ValueError):
            pass

    def was_revoked(self) -> bool:
        try:
            return "revoked_at" in json.loads(self.token_path.read_text())
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------ http ----
    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_request + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last_request = time.monotonic()

    def _request(self, req_factory, max_tries: int = 7) -> dict:
        delay = 2.0
        for attempt in range(1, max_tries + 1):
            self._throttle()
            try:
                with urllib.request.urlopen(req_factory(), timeout=60) as r:
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                if e.code in RETRY_STATUSES and attempt < max_tries:
                    ra = e.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.isdigit() else delay + random.uniform(0, delay)
                    self.on_backoff(wait, e.code)
                    self.sleep(wait)
                    delay = min(delay * 2, 120)
                    continue
                if e.code == 429:
                    raise RateLimited(e.code, body) from None
                raise ApiError(e.code, body) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < max_tries:
                    self.on_backoff(delay, 0)
                    self.sleep(delay + random.uniform(0, delay))
                    delay = min(delay * 2, 120)
                    continue
                raise ApiError(0, f"Network error: {e}") from None
        raise ApiError(0, "unreachable")

    def _post_form(self, url: str, data: dict) -> dict:
        body = urllib.parse.urlencode(data).encode()
        return self._request(lambda: urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}))

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{API}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})

        def make():
            return urllib.request.Request(url, method="GET", headers={
                "Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"})
        try:
            return self._request(make)
        except ApiError as e:
            if e.status == 401:
                raise AuthRequired("Google rejected the access token. Re-authorize to continue.")
            if e.status == 400 and "ACCOUNT_NOT_LINKED" in e.body:
                raise ApiError(400, "Your Google account isn't linked in the Google Health app yet.")
            raise

    # --------------------------------------------------------- data API ----
    def list_exercises(self, page_token: str | None = None, civil_start_before: str | None = None,
                       civil_start_from: str | None = None) -> dict:
        """One page (max 25) of exercise sessions, newest first."""
        parts = []
        if civil_start_from:
            parts.append(f'exercise.interval.civil_start_time >= "{civil_start_from}"')
        if civil_start_before:
            parts.append(f'exercise.interval.civil_start_time < "{civil_start_before}"')
        return self.get("users/me/dataTypes/exercise/dataPoints", {
            "pageSize": 25, "pageToken": page_token, "filter": " AND ".join(parts) or None})

    def heart_rate(self, start_rfc3339: str, end_rfc3339: str) -> list[dict]:
        flt = (f'heart_rate.sample_time.physical_time >= "{start_rfc3339}" AND '
               f'heart_rate.sample_time.physical_time < "{end_rfc3339}"')
        return self._all("heart-rate", {"filter": flt, "pageSize": 10000})

    def daily_zones(self, date_from: str, date_to_exclusive: str) -> list[dict]:
        flt = (f'daily_heart_rate_zones.date >= "{date_from}" AND '
               f'daily_heart_rate_zones.date < "{date_to_exclusive}"')
        return self._all("daily-heart-rate-zones", {"filter": flt, "pageSize": 1000})

    def _all(self, data_type: str, params: dict) -> list[dict]:
        out, token = [], None
        while True:
            r = self.get(f"users/me/dataTypes/{data_type}/dataPoints", {**params, "pageToken": token})
            out.extend(r.get("dataPoints", []))
            token = r.get("nextPageToken")
            if not token:
                return out
