"""Fitbit Air HR web app (single user, tailnet-only).

History comes from the Google Health API (read-only) and is cached in SQLite.
The live page (Web Bluetooth) runs entirely in the browser and never talks to this server.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
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

    DETAIL_FIELDS = ("id", "start_ts", "end_ts", "civil_date", "activity_type", "display_name", "duration_s",
                     "active_duration_s", "calories", "avg_hr", "max_hr", "min_hr", "sample_count", "hr_status",
                     "zones_date")

    def load(wid: str) -> dict:
        w = db.workout(wid)
        if not w:
            raise HTTPException(404, "Workout not found")
        return w

    @app.get("/api/workouts/{wid}")
    def workout_detail(wid: str):
        w = load(wid)
        s = db.settings()
        samples = db.samples(wid)
        zs, src = zones_for(w, s)
        tiz = Z.time_in_zones(samples, zs, w["start_ts"], w["end_ts"], config.GAP_SECONDS)
        return {
            **{k: w[k] for k in DETAIL_FIELDS},
            "zones": zs, "zones_source": src,
            "t": [t - w["start_ts"] for t, _ in samples], "bpm": [b for _, b in samples],
            "time_in_zones": tiz,
            "zone_breakdown": Z.zone_breakdown(tiz, zs),
            "recovery": Z.recovery_hr(samples, w["end_ts"]),
            "post_window_s": config.POST_WORKOUT_SECONDS,
        }

    def export_rows(w: dict):
        tz = ZoneInfo(config.DISPLAY_TZ)
        for t, b in db.samples(w["id"]):
            utc = dt.datetime.fromtimestamp(t, dt.timezone.utc)
            yield {"time_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "time_local": utc.astimezone(tz).isoformat(),
                   "elapsed_s": t - w["start_ts"], "bpm": b,
                   "phase": "workout" if t < w["end_ts"] else "after"}

    def export_name(w: dict, ext: str) -> str:
        stamp = dt.datetime.fromtimestamp(w["start_ts"], ZoneInfo(config.DISPLAY_TZ)).strftime("%Y-%m-%d-%H%M")
        return f"workout-{stamp}-{w['activity_type'].lower()}-hr.{ext}"

    @app.get("/api/workouts/{wid}/export.csv")
    def export_csv(wid: str):
        w = load(wid)
        buf = io.StringIO()
        wr = csv.DictWriter(buf, fieldnames=["time_utc", "time_local", "elapsed_s", "bpm", "phase"])
        wr.writeheader()
        wr.writerows(export_rows(w))
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{export_name(w, "csv")}"'})

    @app.get("/api/workouts/{wid}/export.json")
    def export_json(wid: str):
        w = load(wid)
        body = {"workout": {k: w[k] for k in DETAIL_FIELDS if k not in ("hr_status",)},
                "start_utc": dt.datetime.fromtimestamp(w["start_ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_utc": dt.datetime.fromtimestamp(w["end_ts"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "samples": list(export_rows(w))}
        return Response(json.dumps(body, indent=1), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{export_name(w, "json")}"'})

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

