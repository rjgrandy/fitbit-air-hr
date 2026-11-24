"""Run the real app against an in-memory fake Google Health API, for local UI testing.

    python tests/py/dev_server.py [port]
"""
import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.db import DB  # noqa: E402
from app.main import create_app  # noqa: E402
from fake_google import FakeGoogle, exercise, iso  # noqa: E402

KINDS = [("WALKING", 25), ("ROWING_MACHINE", 30), ("SWIMMING", 18), ("STRENGTH_TRAINING", 60), ("RUNNING", 45)]


class DemoGoogle(FakeGoogle):
    configured = True

    def __init__(self, n=300):
        super().__init__(n_workouts=0)
        rnd = random.Random(7)
        t = 1_763_900_000
        for i in range(n):
            kind, mins = KINDS[rnd.randrange(len(KINDS))]
            mins = max(8, int(mins * rnd.uniform(0.5, 2.2)))
            t -= rnd.randint(9, 60) * 3600
            self.workouts.append(exercise(i, t, minutes=mins, kind=kind))
        self.rnd = rnd

    def was_revoked(self):
        return False

    def auth_url(self):
        return "https://accounts.google.com/"

    def heart_rate(self, start, end):
        import datetime as dt
        s = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
        e = dt.datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
        seed = int(s) % 1000
        if seed % 17 == 0:
            return []  # some workouts without HR
        pts, k = [], 0
        dur = e - s - 180
        while s + k < e:
            if seed % 5 == 0 and 0.4 * dur < k < 0.5 * dur:  # a data gap
                k += 3
                continue
            phase = min(1, k / 300) if k < dur else max(0, 1 - (k - dur) / 120)
            bpm = 85 + phase * (40 + 30 * math.sin(k / 240 + seed)) + random.uniform(-3, 3)
            pts.append({"heartRate": {"beatsPerMinute": str(int(bpm)), "sampleTime": {"physicalTime": iso(s + k)}}})
            k += random.choice([1, 2, 3])
        return pts


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    tmp = Path(tempfile.mkdtemp())
    app = create_app(DB(tmp / "dev.sqlite3"), DemoGoogle())
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
