"""Files of the coordination mode under data/coord/ (personal, never committed).

settings.json, targets.json, missions.json are written atomically with the previous version
kept as .bak, like sites.json. events-<date>.jsonl gets one line per decision, message and
accepted position, for looking back at a mission.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path


class CoordStore:
    def __init__(self, directory: Path):
        self.dir = directory
        self._lock = threading.Lock()

    def read(self, name: str, default):
        path = self.dir / f"{name}.json"
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            shutil.copyfile(path, path.with_suffix(".json.broken"))
            return default

    def write(self, name: str, data) -> None:
        path = self.dir / f"{name}.json"
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            if path.exists():
                shutil.copyfile(path, path.with_suffix(".json.bak"))
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
            )
            tmp.replace(path)

    def log_event(self, node: str, event: str, **fields) -> dict:
        rec = {"time": round(time.time(), 1), "node": node, "kind": event, **fields}
        path = self.dir / f"events-{datetime.now():%Y-%m-%d}.jsonl"
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
