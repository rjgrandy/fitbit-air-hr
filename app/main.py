"""Fitbit Air HR web app.

Milestone 2: serves only the static live-test page. Google Health sync arrives in milestone 3.
The live heart-rate data never touches this server: it flows phone <-> band over Bluetooth.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Fitbit Air HR", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/live-test.html")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
