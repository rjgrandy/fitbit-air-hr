"""Fitbit Air HR web app (single user, tailnet-only).

History comes from the Google Health API (read-only) and is cached in SQLite.
The live page (Web Bluetooth) runs entirely in the browser and never talks to this server.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from . import zones as Z
from .db import DB
from .google import GoogleHealth
from .sync import SyncManager

STATIC = Path(__file__).parent / "static"


def create_app(db: DB | None = None, client: GoogleHealth | None = None) -> FastAPI:
    db = db or DB(config.DB_PATH)
    client = client or GoogleHealth(config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET, config.TOKEN_PATH)
    sync = SyncManager(db, client)
    app = FastAPI(title="Fitbit Air HR", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.client, app.state.sync = db, client, sync

    def zones_for(w: dict, settings: dict) -> tuple[list[dict], str]:
        if w.get("zones_json"):
            return json.loads(w["zones_json"]), "google"
        return Z.fallback_zones(settings["fallback_max_hr"]), "fallback"

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/status")
    def status():
        if not client.configured:
            auth = "unconfigured"
        elif client.has_token():
            auth = "ok"
        else:
            auth = "revoked" if client.was_revoked() else "needed"
        return {"auth": auth, "sync": sync.status(), "settings": db.settings(), "tz": config.DISPLAY_TZ}

    @app.get("/api/auth/url")
    def auth_url():
        if not client.configured:
            raise HTTPException(400, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are missing from .env")
        return {"url": client.auth_url()}

    @app.post("/api/auth/code")
    def auth_code(payload: dict = Body(...)):
        try:
            info = client.exchange_code(str(payload.get("code", "")))
        except ValueError as e:
            raise HTTPException(400, str(e))
        sync.start()
        return {"ok": True, **info}

    @app.post("/api/sync")
    def start_sync():
        started = sync.start()
        return {"started": started, "sync": sync.status()}

    @app.get("/api/workouts")
    def workouts():
        s = db.settings()
        out = []
        for w in db.workouts():
            zs, src = zones_for(w, s)
            w.pop("zones_json", None)
            w["zones"], w["zones_source"] = zs, src
            out.append(w)
        return JSONResponse(out)

    @app.get("/api/hr")
    def hr_batch(ids: str = Query(..., description="comma-separated workout ids (max 50)")):
        """Samples + time-in-zone for several workouts at once (used by lazily rendered rows)."""
        s = db.settings()
        res = {}
        for wid in ids.split(",")[:50]:
            w = db.workout(wid)
            if not w:
                continue
            samples = db.samples(wid)
            zs, src = zones_for(w, s)
            res[wid] = {
                "start_ts": w["start_ts"], "end_ts": w["end_ts"], "hr_status": w["hr_status"],
                # offsets in seconds from workout start (includes the post-workout window)
                "t": [t - w["start_ts"] for t, _ in samples],
                "bpm": [b for _, b in samples],
                "zones": zs, "zones_source": src,
                "time_in_zones": Z.time_in_zones(samples, zs, w["start_ts"], w["end_ts"], config.GAP_SECONDS),
            }
        return JSONResponse(res)

    @app.get("/api/settings")
    def get_settings():
        return db.settings()

    @app.put("/api/settings")
    def put_settings(payload: dict = Body(...)):
        clean = {}
        for k in ("y_min", "y_max", "fallback_max_hr"):
            if k in payload:
                try:
                    clean[k] = int(payload[k])
                except (TypeError, ValueError):
                    raise HTTPException(400, f"{k} must be a number")
        s = {**db.settings(), **clean}
        if not (0 <= s["y_min"] < s["y_max"] <= 260):
            raise HTTPException(400, "Y-axis range must satisfy 0 ≤ min < max ≤ 260")
        if not (100 <= s["fallback_max_hr"] <= 240):
            raise HTTPException(400, "Max HR must be between 100 and 240")
        return db.save_settings(clean)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app

