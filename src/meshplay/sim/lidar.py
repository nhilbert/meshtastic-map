"""Downloads of elevation tiles for the sources in meshplay.sim.sources.

A download goes to <file>.part first and continues where it stopped (HTTP Range), so an
interrupted or cancelled download costs nothing; a server that ignores the range starts over.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 1 << 20
USER_AGENT = "meshplay/0.1 (https://github.com/nhilbert/meshtastic-map)"


def download_url(url: str, target: Path, on_progress=None, timeout: float = 60) -> Path:
    """Download url to target (resuming target's .part file); returns target.

    on_progress(bytes_done, bytes_total) is called after every chunk; it may raise to stop
    (the .part file stays for the next attempt).
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    name = target.name
    if target.exists():
        return target
    part = target.with_suffix(".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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
