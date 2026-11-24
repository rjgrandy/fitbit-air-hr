#!/usr/bin/env python3
"""Milestone 1: prove read-only access to the Google Health API (v4).

Standard library only (Python 3.9+). Nothing here writes to Google Health.

  python3 gh_probe.py auth      # one-time: copy/paste OAuth flow, stores refresh token
  python3 gh_probe.py run       # list last 10 workouts, dump one workout's HR + zones
  python3 gh_probe.py run --pick 3   # dump the 3rd workout in the list instead of the newest

Config (env vars or a .env file next to this script):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET   (or GOOGLE_CLIENT_SECRETS_FILE=path/to/client_secret.json)
  DATA_DIR   where token.json and the JSON dumps go (default ./data)

Endpoints, data types and scopes confirmed against the v4 discovery document
(revision 20261119) and developers.google.com/health on 2026-11-23:
  exercise                -> activity_and_fitness.readonly          (session)
  heart-rate              -> health_metrics_and_measurements.readonly (sample, 1 s resolution)
  daily-heart-rate-zones  -> health_metrics_and_measurements.readonly (daily)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
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
HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- config ----
def load_dotenv() -> None:
    p = HERE / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def client_creds() -> tuple[str, str]:
    f = os.environ.get("GOOGLE_CLIENT_SECRETS_FILE")
    if f:
        j = json.loads(Path(f).read_text())
        j = j.get("web") or j.get("installed") or j
        return j["client_id"], j["client_secret"]
    cid, sec = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (or GOOGLE_CLIENT_SECRETS_FILE) in .env")
    return cid, sec


def data_dir() -> Path:
    d = Path(os.environ.get("DATA_DIR", HERE / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def token_path() -> Path:
    return data_dir() / "token.json"


# ------------------------------------------------------------------ http ----
class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:800]}")
        self.status, self.body = status, body


def http(method: str, url: str, *, data: dict | None = None, headers: dict | None = None,
         max_tries: int = 6) -> dict:
    """HTTP with exponential backoff + jitter on 429/503/504 (per the rate-limits doc)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    delay = 2.0
    for attempt in range(1, max_tries + 1):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code in (429, 503, 504) and attempt < max_tries:
                ra = e.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay + random.uniform(0, delay)
                print(f"  .. HTTP {e.code}, backing off {wait:.1f}s (try {attempt}/{max_tries})", file=sys.stderr)
                time.sleep(wait)
                delay = min(delay * 2, 120)
                continue
            raise ApiError(e.code, text) from None
        except urllib.error.URLError as e:
            if attempt < max_tries:
                time.sleep(delay + random.uniform(0, delay))
                delay = min(delay * 2, 120)
                continue
            raise
    raise RuntimeError("unreachable")


# ----------------------------------------------------------------- oauth ----
def cmd_auth(_args) -> None:
    cid, sec = client_creds()
    q = {
        "client_id": cid,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # ask for a refresh token
        "prompt": "consent",        # force a refresh token even on re-auth
    }
    print("\n1) Open this URL in a browser signed in to the Google account your Fitbit Air syncs to:\n")
    print(f"{AUTH_URL}?{urllib.parse.urlencode(q)}\n")
    print("2) Click through the 'Google hasn't verified this app' warning (Advanced > Go to ...),")
    print("   tick BOTH Google Health permissions, and continue.")
    print("3) You'll land on google.com. Copy the whole address-bar URL (or just the code= value).\n")
    pasted = input("Paste here: ").strip()
    code = pasted
    if pasted.startswith("http"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        if "error" in qs:
            sys.exit(f"Google returned error: {qs['error'][0]}")
        code = qs.get("code", [""])[0]
    if not code:
        sys.exit("No code found in what you pasted.")
    tok = http("POST", TOKEN_URL, data={
        "code": code, "client_id": cid, "client_secret": sec,
        "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
    })
    if "refresh_token" not in tok:
        sys.exit("No refresh_token returned. Remove the app at myaccount.google.com/permissions and run auth again.")
    granted = set(tok.get("scope", "").split())
    missing = [s for s in SCOPES if s not in granted]
    save_token(tok)
    print(f"\nSaved refresh token to {token_path()}")
    if missing:
        print("WARNING: these scopes were NOT granted (re-run auth and tick every box):")
        for s in missing:
            print("  ", s)
    if "refresh_token_expires_in" in tok:
        days = int(tok["refresh_token_expires_in"]) / 86400
        print(f"NOTE: Google says this refresh token expires in {days:.1f} days. "
              "That means the OAuth app is still in 'Testing'; set it to 'In production'.")


def save_token(tok: dict) -> None:
    tok = dict(tok)
    tok["obtained_at"] = int(time.time())
    p = token_path()
    if p.exists():  # keep the refresh token if a refresh response omits it
        old = json.loads(p.read_text())
        tok.setdefault("refresh_token", old.get("refresh_token"))
    p.write_text(json.dumps(tok, indent=2))
    os.chmod(p, 0o600)


def access_token() -> str:
    p = token_path()
    if not p.exists():
        sys.exit("No token yet. Run:  python3 gh_probe.py auth")
    tok = json.loads(p.read_text())
    if tok.get("access_token") and time.time() < tok["obtained_at"] + int(tok.get("expires_in", 0)) - 120:
        return tok["access_token"]
    cid, sec = client_creds()
    try:
        new = http("POST", TOKEN_URL, data={
            "client_id": cid, "client_secret": sec,
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
        })
    except ApiError as e:
        if "invalid_grant" in e.body:
            sys.exit("Refresh token was revoked or expired. Re-authorize:  python3 gh_probe.py auth")
        raise
    save_token(new)
    return new["access_token"]


def api_get(path: str, params: dict | None = None) -> dict:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return http("GET", url, headers={"Authorization": f"Bearer {access_token()}"})


# --------------------------------------------------------------- helpers ----
def parse_ts(s: str) -> dt.datetime:
    s = s.replace("Z", "+00:00")
    if "." in s:  # trim fractional seconds to 6 digits for fromisoformat on 3.9/3.10
        head, rest = s.split(".", 1)
        n = 0
        while n < len(rest) and rest[n].isdigit():
            n += 1
        frac, tz = rest[:n], rest[n:]
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    return dt.datetime.fromisoformat(s)


def fmt_local(ts: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        return parse_ts(ts).astimezone(ZoneInfo("America/Chicago")).strftime("%a %Y-%m-%d %H:%M")
    except Exception:
        return ts


def civil_date(ex: dict) -> str | None:
    d = (ex.get("interval", {}).get("civilStartTime") or {}).get("date")
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}" if d else None


def list_all(data_type: str, params: dict) -> list[dict]:
    out, token = [], None
    while True:
        r = api_get(f"users/me/dataTypes/{data_type}/dataPoints", {**params, "pageToken": token})
        out.extend(r.get("dataPoints", []))
        token = r.get("nextPageToken")
        if not token:
            return out


# ------------------------------------------------------------------- run ----
def cmd_run(args) -> None:
    out = data_dir()

    # 0. Profile must be linked in the Google Health app, else 400 ACCOUNT_NOT_LINKED.
    try:
        ident = api_get("users/me/identity")
    except ApiError as e:
        if "ACCOUNT_NOT_LINKED" in e.body:
            sys.exit("Your Google account isn't linked in the Google Health mobile app yet. Open the app, sign in, retry.")
        raise
    print(f"Health user id: {ident.get('healthUserId')}")

    # 1. Last 10 workouts. exercise page size max is 25; results come newest first.
    resp = api_get("users/me/dataTypes/exercise/dataPoints", {"pageSize": 10})
    workouts = resp.get("dataPoints", [])
    (out / "workouts_last10.json").write_text(json.dumps(resp, indent=2))
    if not workouts:
        sys.exit("No workouts returned. Raw response saved to workouts_last10.json")

    print(f"\nLast {len(workouts)} workouts (America/Chicago):")
    print(f"{'#':>2}  {'start':<20} {'type':<22} {'dur':>6} {'avgHR':>5} {'kcal':>6}")
    for i, dp in enumerate(workouts, 1):
        ex = dp["exercise"]
        iv = ex["interval"]
        mins = (parse_ts(iv["endTime"]) - parse_ts(iv["startTime"])).total_seconds() / 60
        ms = ex.get("metricsSummary", {})
        kcal = ms.get("caloriesKcal")
        print(f"{i:>2}  {fmt_local(iv['startTime']):<20} {ex.get('exerciseType','?')[:22]:<22} "
              f"{mins:>5.0f}m {ms.get('averageHeartRateBeatsPerMinute','-'):>5} "
              f"{(f'{kcal:.0f}' if kcal is not None else '-'):>6}")

    # 2. HR samples for one workout's window.
    pick = workouts[min(max(args.pick, 1), len(workouts)) - 1]
    ex = pick["exercise"]
    start, end = ex["interval"]["startTime"], ex["interval"]["endTime"]
    # Pad +3 min after the end so later milestones can compute recovery HR.
    end_pad = (parse_ts(end) + dt.timedelta(minutes=3)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    flt = (f'heart_rate.sample_time.physical_time >= "{start}" AND '
           f'heart_rate.sample_time.physical_time < "{end_pad}"')
    print(f"\nFetching HR samples for #{args.pick} ({ex.get('exerciseType')}, {fmt_local(start)}) ...")
    hr = list_all("heart-rate", {"filter": flt, "pageSize": 10000})
    hr.sort(key=lambda p: p["heartRate"]["sampleTime"]["physicalTime"])
    bpms = [int(p["heartRate"]["beatsPerMinute"]) for p in hr]
    sources = sorted({json.dumps(p.get("dataSource", {}).get("device") or p.get("dataSource", {}).get("platform"))
                      for p in hr})
    gaps = 0
    for a, b in zip(hr, hr[1:]):
        if (parse_ts(b["heartRate"]["sampleTime"]["physicalTime"]) -
                parse_ts(a["heartRate"]["sampleTime"]["physicalTime"])).total_seconds() > 30:
            gaps += 1
    (out / "workout_hr_samples.json").write_text(json.dumps(
        {"workout": pick, "filter": flt, "sample_count": len(hr), "samples": hr}, indent=2))
    if bpms:
        print(f"  {len(hr)} samples, min/avg/max {min(bpms)}/{sum(bpms)/len(bpms):.0f}/{max(bpms)} bpm, "
              f"{gaps} gaps >30 s, sources: {', '.join(sources)}")
    else:
        print("  No HR samples in that window.")

    # 3. HR zones: daily-heart-rate-zones for the workout's local date, else the most recent.
    day = civil_date(ex)
    zones = []
    if day:
        nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
        zones = list_all("daily-heart-rate-zones",
                         {"filter": f'daily_heart_rate_zones.date >= "{day}" AND daily_heart_rate_zones.date < "{nxt}"'})
    latest = api_get("users/me/dataTypes/daily-heart-rate-zones/dataPoints", {"pageSize": 1}).get("dataPoints", [])
    (out / "hr_zones.json").write_text(json.dumps(
        {"for_workout_date": day, "workout_date_zones": zones, "latest_zones": latest}, indent=2))
    show = (zones or latest)
    if show:
        print("\nHR zones" + (f" on {day}" if zones else " (latest available)") + ":")
        for z in show[0]["dailyHeartRateZones"]["heartRateZones"]:
            print(f"  {z['heartRateZoneType']:<9} {z['minBeatsPerMinute']:>3}-{z['maxBeatsPerMinute']} bpm")
    else:
        print("\nNo daily-heart-rate-zones data returned.")

    print(f"\nWrote workouts_last10.json, workout_hr_samples.json, hr_zones.json to {out}")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="one-time OAuth copy/paste flow").set_defaults(fn=cmd_auth)
    r = sub.add_parser("run", help="list last 10 workouts and dump one workout's HR + zones")
    r.add_argument("--pick", type=int, default=1, help="which workout (1 = newest) to dump HR for")
    r.set_defaults(fn=cmd_run)
    args = ap.parse_args()
    try:
        args.fn(args)
    except ApiError as e:
        sys.exit(f"Google Health API error: {e}")


if __name__ == "__main__":
    main()
