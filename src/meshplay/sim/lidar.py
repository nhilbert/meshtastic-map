"""Download of the NRW laser-scan tiles (Geobasis NRW, 3D-Messdaten, dl-de/zero-2-0).

Used by scripts/sim_fetch_tiles.py and the map app's scene task. A download goes to
<tile>.part first and continues where it stopped (HTTP Range), so an interrupted or cancelled
download costs nothing.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from meshplay.sim.scene import TILE_URL

CHUNK = 1 << 20


def remote_size(name: str, timeout: float = 30) -> int | None:
    """Size of a tile on the server in bytes, or None if the server doesn't have it."""
    req = urllib.request.Request(TILE_URL + name, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.headers.get("Content-Length", 0))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def download_tile(name: str, laz_dir: Path, on_progress=None, timeout: float = 60) -> Path:
    """Download one tile into laz_dir (resuming a .part file); returns the path.

    on_progress(bytes_done, bytes_total) is called after every chunk; it may raise to stop
    (the .part file stays for the next attempt).
    """
    laz_dir = Path(laz_dir)
    laz_dir.mkdir(parents=True, exist_ok=True)
    target = laz_dir / name
    if target.exists():
        return target
    part = target.with_suffix(".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(TILE_URL + name)
    if have:
        req.add_header("Range", f"bytes={have}-")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code != 416:  # 416: the .part file is already complete
            raise
        part.rename(target)
        return target
    with resp:
        if have and resp.status != 206:  # server ignored the range: start over
            have = 0
        total = have + int(resp.headers.get("Content-Length", 0))
        with part.open("ab" if have else "wb") as f:
            done = have
            while chunk := resp.read(CHUNK):
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    if total and part.stat().st_size != total:
        raise OSError(f"{name}: download incomplete ({part.stat().st_size} of {total} bytes)")
    part.rename(target)
    return target
