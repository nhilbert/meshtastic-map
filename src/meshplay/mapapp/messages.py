"""Text messages and recent traffic for the map app's messaging pane.

Received and sent texts are kept with their delivery state and appended to
data/messages.jsonl, so conversations survive a restart of the server. Every change (new
message, new state) gets a revision number; the page asks for everything newer than the last
revision it has. The list of recent packets ("traffic") lives in memory only.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from meshplay.mapapp.i18n import _

# A Meshtastic data payload holds about 233 bytes; stay below that for the packet overhead.
MAX_TEXT_BYTES = 200
KEEP_MESSAGES = 1000
KEEP_TRAFFIC = 1000  # a few hours of traffic, so the page's filter by type has rare ones too


class MessageStore:
    def __init__(self, path: Path, keep: int = KEEP_MESSAGES):
        self.path = path
        self.messages: deque[dict] = deque(maxlen=keep)
        self.traffic: deque[dict] = deque(maxlen=KEEP_TRAFFIC)
        self.rev = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        """Messages from earlier runs; state updates are separate lines, applied in order."""
        if not self.path.exists():
            return
        by_id: dict[int, dict] = {}
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "update" in rec:
                if rec["update"] in by_id:
                    by_id[rec["update"]]["status"] = rec["status"]
                continue
            self.messages.append(rec)
            if rec.get("id") is not None:
                by_id[rec["id"]] = rec
        for rec in self.messages:
            self.rev += 1
            rec["rev"] = self.rev

    def _write(self, rec: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in rec.items() if k != "rev"}) + "\n")

    def add(self, rec: dict) -> dict:
        """rec: id (packet id), time, dir (in/out), from, to, channel, text, and for received
        messages snr/rssi/hops, for sent ones status."""
        with self._lock:
            self.rev += 1
            rec["rev"] = self.rev
            self.messages.append(rec)
            self._write(rec)
        return rec

    def set_status(self, packet_id: int, status: str) -> None:
        with self._lock:
            for rec in reversed(self.messages):
                if rec.get("id") == packet_id and rec["dir"] == "out":
                    self.rev += 1
                    rec["status"], rec["rev"] = status, self.rev
                    self._write({"update": packet_id, "status": status})
                    return

    def since(self, rev: int) -> list[dict]:
        with self._lock:
            return [m for m in self.messages if m["rev"] > rev]

    def add_traffic(self, summary: dict) -> None:
        """summary: time, from, to, channel, snr, rssi, hops, port, local (only given to the
        app, not transmitted), text and packet (the whole packet as a plain dict, for the
        details)."""
        with self._lock:
            self.rev += 1
            summary["rev"] = self.rev
            self.traffic.append(summary)

    def traffic_since(self, rev: int) -> list[dict]:
        with self._lock:
            return [p for p in self.traffic if p["rev"] > rev]


def check_text(text: str) -> str:
    """The text as it will be sent, or ValueError with a message for the page."""
    text = (text or "").strip()
    if not text:
        raise ValueError(_("Leere Nachricht"))
    size = len(text.encode("utf-8"))
    if size > MAX_TEXT_BYTES:
        raise ValueError(
            _("Nachricht zu lang: {n} Bytes, höchstens {max}", n=size, max=MAX_TEXT_BYTES)
        )
    return text


def now() -> float:
    return round(time.time(), 1)
