"""Runtime configuration from environment variables (.env via docker compose)."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "fitbit-air-hr.sqlite3"
TOKEN_PATH = DATA_DIR / "token.json"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

DISPLAY_TZ = os.environ.get("DISPLAY_TZ", "America/Chicago")

# Extra HR window fetched after each workout ends (used for recovery HR in milestone 4).
POST_WORKOUT_SECONDS = 180
# Consecutive samples further apart than this are treated as a gap: the chart line breaks
# and the time is not counted towards any zone.
GAP_SECONDS = 30
