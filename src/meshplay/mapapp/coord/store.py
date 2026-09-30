"""Files of the coordination mode under data/coord/ (personal, never committed).

settings.json, targets.json, missions.json are written atomically with the previous version
kept as .bak, like sites.json. events-<date>.jsonl gets one line per decision, message and
accepted position, for looking back at a mission. archive/<id>.json keeps a mission that was
replaced by a new one or removed from the list.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import date, datetime, timedelta
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

    def archive(self, mission_id: str, data: dict) -> None:
        d = self.dir / "archive"
        with self._lock:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f"{mission_id}.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(d / f"{mission_id}.json")

    def archived(self) -> list[dict]:
        """Every archived mission (unreadable files are skipped)."""
        out = []
        for path in sorted((self.dir / "archive").glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return out

    def read_archived(self, mission_id: str) -> dict | None:
        path = self.dir / "archive" / f"{mission_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def events_for(self, node: str, start: float, end: float) -> list[dict]:
        """The node's events between start and end, from the daily files they fall in."""
        out = []
        day = date.fromtimestamp(start)
        while day <= date.fromtimestamp(end):
            path = self.dir / f"events-{day:%Y-%m-%d}.jsonl"
            if path.exists():
                with path.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if rec.get("node") == node and start <= rec.get("time", 0) <= end:
                            out.append(rec)
            day += timedelta(days=1)
        return out

    def log_event(self, node: str, event: str, **fields) -> dict:
        rec = {"time": round(time.time(), 1), "node": node, "kind": event, **fields}
        path = self.dir / f"events-{datetime.now():%Y-%m-%d}.jsonl"
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec
