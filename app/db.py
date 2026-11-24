"""SQLite storage. All timestamps are stored in UTC (epoch seconds)."""
from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
    id               TEXT PRIMARY KEY,   -- stable id from Google (data point name) or derived
    start_ts         INTEGER NOT NULL,   -- UTC epoch seconds
    end_ts           INTEGER NOT NULL,
    civil_start      TEXT,               -- local wall-clock start as Google reports it, 'YYYY-MM-DDTHH:MM:SS'
    civil_date       TEXT,               -- 'YYYY-MM-DD' local date
    activity_type    TEXT NOT NULL,
    display_name     TEXT,
    duration_s       INTEGER NOT NULL,
    active_duration_s INTEGER,
    calories         REAL,
    avg_hr           INTEGER,            -- from samples when present, else Google's summary
    max_hr           INTEGER,
    min_hr           INTEGER,
    sample_count     INTEGER NOT NULL DEFAULT 0,
    zones_json       TEXT,               -- Google HR zones in effect that day (snapshot), NULL = use fallback
    zones_date       TEXT,               -- the date those zones are from
    hr_status        TEXT NOT NULL DEFAULT 'pending',  -- pending | ok | none
    hr_fetched_at    INTEGER,
    google_update_time TEXT,
    raw_json         TEXT NOT NULL,
    synced_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS workouts_start ON workouts(start_ts DESC);
CREATE INDEX IF NOT EXISTS workouts_hr_status ON workouts(hr_status);

-- Raw HR samples per workout: zlib-compressed JSON [[epoch_seconds, bpm], ...]
CREATE TABLE IF NOT EXISTS hr_samples (
    workout_id TEXT PRIMARY KEY REFERENCES workouts(id) ON DELETE CASCADE,
    data       BLOB NOT NULL
);

-- Google daily HR zones, one row per local date.
CREATE TABLE IF NOT EXISTS daily_zones (
    date       TEXT PRIMARY KEY,
    zones_json TEXT NOT NULL
);

-- Local-only annotations. Never sent to Google. Deliberately no foreign key to workouts,
-- so nothing a sync or a full re-backfill does to the workouts table can remove them.
CREATE TABLE IF NOT EXISTS workout_pins (
    workout_id TEXT PRIMARY KEY,
    pinned_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS workout_tags (
    workout_id TEXT NOT NULL,
    tag        TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (workout_id, tag)
);
CREATE INDEX IF NOT EXISTS workout_tags_tag ON workout_tags(tag);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

MAX_TAG_LEN = 40
MAX_TAGS = 20


def normalize_tags(tags) -> list[str]:
    """Trim, collapse whitespace, cap length, drop empties and case-insensitive duplicates."""
    out, seen = [], set()
    for t in tags or []:
        t = " ".join(str(t).split())[:MAX_TAG_LEN]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:MAX_TAGS]


DEFAULT_SETTINGS = {"y_min": 50, "y_max": 200, "fallback_max_hr": 190}


class DB:
    """Thin wrapper: one connection, serialised with a lock (single-user app, tiny load)."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.lock = threading.RLock()

    # ---- generic ----
    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args=()) -> None:
        with self.lock:
            self.conn.execute(sql, args)

    # ---- kv / settings ----
    def kv_get(self, key: str, default=None):
        r = self.q("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r[0]["value"]) if r else default

    def kv_set(self, key: str, value) -> None:
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

    def settings(self) -> dict:
        return {**DEFAULT_SETTINGS, **(self.kv_get("settings", {}) or {})}

    def save_settings(self, s: dict) -> dict:
        merged = {**self.settings(), **{k: v for k, v in s.items() if k in DEFAULT_SETTINGS}}
        self.kv_set("settings", merged)
        return merged

    # ---- workouts ----
    def upsert_workout(self, w: dict) -> bool:
        """Insert or update a workout. Returns True if HR needs (re)fetching.

        Only Google-derived columns are touched, so local-only data kept in other
        tables (pins, tags) is never affected by a re-sync or full re-backfill.
        """
        with self.lock:
            old = self.conn.execute(
                "SELECT start_ts,end_ts,google_update_time,hr_status FROM workouts WHERE id=?", (w["id"],)
            ).fetchone()
            changed = (old is None or old["start_ts"] != w["start_ts"] or old["end_ts"] != w["end_ts"]
                       or old["google_update_time"] != w.get("google_update_time"))
            self.conn.execute(
                """INSERT INTO workouts(id,start_ts,end_ts,civil_start,civil_date,activity_type,display_name,
                       duration_s,active_duration_s,calories,avg_hr,raw_json,google_update_time,synced_at,hr_status)
                   VALUES(:id,:start_ts,:end_ts,:civil_start,:civil_date,:activity_type,:display_name,
                       :duration_s,:active_duration_s,:calories,:avg_hr,:raw_json,:google_update_time,:synced_at,'pending')
                   ON CONFLICT(id) DO UPDATE SET
                       start_ts=excluded.start_ts, end_ts=excluded.end_ts, civil_start=excluded.civil_start,
                       civil_date=excluded.civil_date, activity_type=excluded.activity_type,
                       display_name=excluded.display_name, duration_s=excluded.duration_s,
                       active_duration_s=excluded.active_duration_s, calories=excluded.calories,
                       avg_hr=CASE WHEN workouts.sample_count>0 AND NOT :changed THEN workouts.avg_hr
                                   ELSE excluded.avg_hr END,
                       raw_json=excluded.raw_json, google_update_time=excluded.google_update_time,
                       synced_at=excluded.synced_at,
                       hr_status=CASE WHEN :changed THEN 'pending' ELSE workouts.hr_status END""",
                {**w, "changed": int(changed)},
            )
            return changed

    def save_samples(self, workout_id: str, samples: list[tuple[int, int]], stats: dict, fetched_at: int) -> None:
        with self.lock:
            if samples:
                blob = zlib.compress(json.dumps(samples, separators=(",", ":")).encode())
                self.conn.execute("INSERT INTO hr_samples(workout_id,data) VALUES(?,?) "
                                  "ON CONFLICT(workout_id) DO UPDATE SET data=excluded.data", (workout_id, blob))
            else:
                self.conn.execute("DELETE FROM hr_samples WHERE workout_id=?", (workout_id,))
            self.conn.execute(
                """UPDATE workouts SET hr_status=?, hr_fetched_at=?, sample_count=?,
                       avg_hr=COALESCE(?, avg_hr), max_hr=?, min_hr=? WHERE id=?""",
                ("ok" if stats["count"] else "none", fetched_at, stats["count"],
                 stats["avg"], stats["max"], stats["min"], workout_id),
            )

    def samples(self, workout_id: str) -> list[list[int]]:
        r = self.q("SELECT data FROM hr_samples WHERE workout_id=?", (workout_id,))
        return json.loads(zlib.decompress(r[0]["data"])) if r else []

    def workouts(self) -> list[dict]:
        rows = self.q(
            """SELECT id,start_ts,end_ts,civil_date,activity_type,display_name,duration_s,calories,
                      avg_hr,max_hr,min_hr,sample_count,hr_status,zones_json,zones_date
               FROM workouts ORDER BY start_ts DESC""")
        return [dict(r) for r in rows]

    # ---- pins & tags (local only) ----
    def set_pinned(self, workout_id: str, pinned: bool, now: int) -> None:
        if pinned:
            self.x("INSERT OR IGNORE INTO workout_pins(workout_id,pinned_at) VALUES(?,?)", (workout_id, now))
        else:
            self.x("DELETE FROM workout_pins WHERE workout_id=?", (workout_id,))

    def set_tags(self, workout_id: str, tags: list[str]) -> list[str]:
        clean = normalize_tags(tags)
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.execute("DELETE FROM workout_tags WHERE workout_id=?", (workout_id,))
                self.conn.executemany("INSERT OR IGNORE INTO workout_tags(workout_id,tag) VALUES(?,?)",
                                      [(workout_id, t) for t in clean])
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return self.tags_for(workout_id)

    def tags_for(self, workout_id: str) -> list[str]:
        return [r["tag"] for r in self.q("SELECT tag FROM workout_tags WHERE workout_id=? ORDER BY tag", (workout_id,))]

    def is_pinned(self, workout_id: str) -> bool:
        return bool(self.q("SELECT 1 FROM workout_pins WHERE workout_id=?", (workout_id,)))

    def all_tags(self) -> list[dict]:
        """Existing tags with usage counts (for autocomplete). Uses the first-seen spelling."""
        return [dict(r) for r in self.q(
            "SELECT MIN(tag) AS tag, COUNT(*) AS count FROM workout_tags GROUP BY tag COLLATE NOCASE ORDER BY count DESC, tag")]

    def annotations(self) -> tuple[set[str], dict[str, list[str]]]:
        pins = {r["workout_id"] for r in self.q("SELECT workout_id FROM workout_pins")}
        tags: dict[str, list[str]] = {}
        for r in self.q("SELECT workout_id, tag FROM workout_tags ORDER BY tag"):
            tags.setdefault(r["workout_id"], []).append(r["tag"])
        return pins, tags

    def workout(self, workout_id: str) -> dict | None:
        r = self.q("SELECT * FROM workouts WHERE id=?", (workout_id,))
        return dict(r[0]) if r else None
