"""Loading coverage-walk data: tracker positions or probe results, and phone GPX tracks."""

from __future__ import annotations

import bisect
import functools
import json
import math
import statistics
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def parse_node(value: str) -> int:
    """Node ID like !abcd1234 (or a decimal node number) -> node number."""
    return int(value.lstrip("!"), 16) if value.startswith("!") else int(value)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def load_points(path: Path, tracker: int) -> list[dict]:
    """Position packets from `tracker` in a listen.py packet log (JSONL)."""
    points = []
    for line in path.open(encoding="utf-8"):
        p = json.loads(line)
        pos = p.get("decoded", {}).get("position", {})
        if p.get("from") != tracker or "latitude" not in pos:
            continue
        points.append(
            {
                # receivedAt is local time without offset; make it comparable to GPX (UTC).
                "time": datetime.fromisoformat(p["receivedAt"]).astimezone(timezone.utc),
                "lat": pos["latitude"],
                "lon": pos["longitude"],
                "precisionBits": pos.get("precisionBits", 32),
                "snr": p.get("rxSnr"),
                "rssi": p.get("rxRssi"),
                # hopLimit is left out of the log when it is 0, so a missing value means 0.
                "hops": (p.get("hopStart") or 0) - (p.get("hopLimit") or 0),
                "relayNode": p.get("relayNode"),
            }
        )
    return points


def load_probes(path: Path, target: int) -> list[dict]:
    """Traceroute results for `target` in a probe_walk.py log (JSONL)."""
    probes = []
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        if parse_node(r["to"]) != target:
            continue
        probes.append(
            {
                # sentAt is local time without offset; make it comparable to GPX (UTC).
                "time": datetime.fromisoformat(r["sentAt"]).astimezone(timezone.utc),
                "result": r["result"],
                "snrTowards": r.get("snrTowards"),
                "snrBack": r.get("snrBack"),
                "rssi": r.get("rssiBack"),
                "relays": r.get("relays"),
                "interval": r.get("interval"),
                "preset": r.get("preset"),
            }
        )
    return probes


def most_common(values) -> object | None:
    """Most frequent non-empty value, or None."""
    counts = Counter(v for v in values if v)
    return counts.most_common(1)[0][0] if counts else None


def typical_interval_s(points: list[dict]) -> float | None:
    """Sending interval: from probe records if logged, else the median gap between points.

    The median gap overestimates the interval once more than half of the packets are lost.
    """
    logged = most_common(p.get("interval") for p in points)
    if logged:
        return float(logged)
    gaps = [
        (b["time"] - a["time"]).total_seconds() for a, b in zip(points, points[1:], strict=False)
    ]
    return float(statistics.median(gaps)) if gaps else None


# A GPX recording with a longer pause says nothing about where the walker was in between: no
# position is interpolated across it.
MAX_GAP_S = 900.0


def position_at(
    track: list[dict], t: datetime, max_gap_s: float | None = MAX_GAP_S
) -> tuple[float, float] | None:
    """(lat, lon) on a time-sorted track at time t, interpolated; None outside the track or
    inside a gap longer than max_gap_s."""
    times = [p["time"] for p in track]
    i = bisect.bisect_left(times, t)
    if i == len(track) or (i == 0 and t < times[0]):
        return None
    b = track[i]
    if b["time"] == t or i == 0:
        return b["lat"], b["lon"]
    a = track[i - 1]
    if max_gap_s is not None and (b["time"] - a["time"]).total_seconds() > max_gap_s:
        return None
    f = (t - a["time"]) / (b["time"] - a["time"])
    return a["lat"] + f * (b["lat"] - a["lat"]), a["lon"] + f * (b["lon"] - a["lon"])


def probe_points(probes: list[dict], track: list[dict]) -> list[dict]:
    """Probes placed on the track by send time, shaped like load_points() output.

    snr/rssi are the tracker -> home direction, as for received position packets; snrMin is
    the weaker of both directions. hops is None for unanswered probes.
    """
    points = []
    for pr in probes:
        pos = position_at(track, pr["time"])
        if pos is None:
            continue
        snrs = [v for v in (pr["snrTowards"], pr["snrBack"]) if v is not None]
        points.append(
            {
                "time": pr["time"],
                "lat": pos[0],
                "lon": pos[1],
                "result": pr["result"],
                "snr": pr["snrBack"],
                "rssi": pr["rssi"],
                "snrTowards": pr["snrTowards"],
                "snrMin": min(snrs) if snrs else None,
                "hops": pr["relays"] if pr["result"] == "ok" else None,
                "interval": pr["interval"],
                "preset": pr["preset"],
            }
        )
    return points


def load_gpx(path: Path) -> list[dict]:
    """Track points with a timestamp, from any GPX 1.0/1.1 file."""
    return parse_gpx(ET.parse(path).getroot())


def parse_gpx(root: ET.Element | str) -> list[dict]:
    """Track points with a timestamp from a parsed GPX document or its text."""
    if isinstance(root, str):
        root = ET.fromstring(root)
    track = []
    for el in root.iter():
        if not el.tag.endswith("trkpt"):
            continue
        time_el = next((c for c in el if c.tag.endswith("time")), None)
        if time_el is None or not time_el.text:
            continue
        t = datetime.fromisoformat(time_el.text.strip().replace("Z", "+00:00"))
        track.append(
            {
                "time": t if t.tzinfo else t.replace(tzinfo=timezone.utc),
                "lat": float(el.get("lat")),
                "lon": float(el.get("lon")),
            }
        )
    return sorted(track, key=lambda p: p["time"])


def load_heard(path: Path) -> tuple[dict, list[dict]]:
    """(meta, records) of a passive walk (data/heard/<name>.jsonl, written by the map app)."""
    meta, records = {}, []
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if "meta" in r:
            meta = r["meta"]
            continue
        r["time"] = datetime.fromisoformat(r["time"])
        records.append(r)
    return meta, records


def heard_points(records: list[dict], track: list[dict], receiver: int | None) -> list[dict]:
    """Packets of other nodes placed on the track by time; packets in track gaps are left out."""
    from meshplay.applog import hops

    points = []
    for r in records:
        if r["from"] == receiver:
            continue
        pos = position_at(track, r["time"])
        if pos is not None:
            points.append({**r, "lat": pos[0], "lon": pos[1], "hops": hops(r)})
    return points


def heard_windows(
    records: list[dict], receiver: int | None, t0: datetime, t1: datetime, window_s: float
) -> list[tuple[datetime, datetime, str]]:
    """The walk in windows of window_s: "rx" (another node heard), "quiet" (only the receiving
    device's own packets: it was logging, nothing came in) or "none" (no data at all)."""
    out = []
    times_rx = sorted(r["time"] for r in records if r["from"] != receiver)
    times_own = sorted(r["time"] for r in records if r["from"] == receiver)

    def any_in(times: list[datetime], a: datetime, b: datetime) -> bool:
        i = bisect.bisect_left(times, a)
        return i < len(times) and times[i] < b

    a, step = t0, timedelta(seconds=window_s)
    while a < t1:
        b = min(a + step, t1)
        state = "rx" if any_in(times_rx, a, b) else "quiet" if any_in(times_own, a, b) else "none"
        out.append((a, b, state))
        a = b
    return out


def classify_track(track: list[dict], points: list[dict], window_s: float) -> None:
    """Mark each track point by the best reception within window_s of it."""
    for tp in track:
        near = [p for p in points if abs((p["time"] - tp["time"]).total_seconds()) <= window_s]
        if any(p["hops"] == 0 for p in near):
            tp["status"] = "direct"
        elif near:
            tp["status"] = "relayed"
        else:
            tp["status"] = "none"


def serve(directory: Path, filename: str, port: int = 8765) -> None:
    """Serve a map on localhost and open it; OSM tiles don't load from file:// pages."""

    class QuietHandler(SimpleHTTPRequestHandler):
        # Keep-alive: closing the socket after each response can reset larger transfers on
        # Windows.
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

    handler = functools.partial(QuietHandler, directory=str(directory))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://localhost:{port}/{filename}"
        print(f"Serving {url} (Ctrl+C to stop)")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
