"""Map tiles for the page: cached under data/tiles/ so the map works without internet.

The page asks the server for /tiles/<z>/<x>/<y>.png. A tile that was fetched before is served
from the cache; otherwise it is fetched from OpenStreetMap's tile server once and stored.
Offline, tiles that were never viewed are missing (404) and the map shows grey there: look
at the area at the zoom levels you need while online, and it stays available.

OpenStreetMap's tile usage policy asks for a valid User-Agent and no bulk downloads, so the
cache fills only from what the page shows; there is no prefetch. The attribution stays on
the map.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "meshplay/0.1 map app (https://github.com/nhilbert/meshtastic-map)"
MAX_ZOOM = 19
TIMEOUT_S = 8


class TileCache:
    def __init__(self, directory: Path):
        self.dir = directory
        self.online = True  # False after a failed fetch: don't wait for timeouts offline
        self._lock = threading.Lock()
        self._retry_after = 0.0

    def path(self, z: int, x: int, y: int) -> Path:
        return self.dir / str(z) / str(x) / f"{y}.png"

    def valid(self, z: int, x: int, y: int) -> bool:
        return 0 <= z <= MAX_ZOOM and 0 <= x < 2**z and 0 <= y < 2**z

    def get(self, z: int, x: int, y: int) -> bytes | None:
        """The tile's bytes from the cache or the tile server; None when neither has it."""
        if not self.valid(z, x, y):
            return None
        path = self.path(z, x, y)
        if path.exists():
            return path.read_bytes()
        data = self.fetch(z, x, y)
        if data is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return data

    def fetch(self, z: int, x: int, y: int) -> bytes | None:
        """From the tile server; after a failure, no further attempts for a minute."""
        import time

        with self._lock:
            if time.time() < self._retry_after:
                return None
        req = urllib.request.Request(
            TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = resp.read()
            with self._lock:
                self.online = True
            return data
        except (urllib.error.URLError, OSError, ValueError) as e:
            with self._lock:
                self.online = False
                self._retry_after = time.time() + 60
            log.info("Tile %s/%s/%s not available (offline?): %s", z, x, y, e)
            return None

    def count(self) -> int:
        return sum(1 for _ in self.dir.rglob("*.png")) if self.dir.exists() else 0
