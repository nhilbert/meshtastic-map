"""Packet log exported by the Meshtastic Android app (CSV), for passive coverage walks.

python -m meshplay.applog export.csv [track.gpx]   # summary of an export, nothing is written

The app writes one row per received packet with local time (no zone), sender, SNR, hop limit and
start, and the last byte of the relaying node. The rows say what a node carried along a walk
heard; the map app keeps the part of an export that falls into a GPX track (mapapp/heard_store.py).
"""

from __future__ import annotations

import csv
import io
import statistics
import sys
from datetime import datetime, timedelta, tzinfo

REQUIRED = (
    "date",
    "time",
    "from",
    "rx snr",
    "hop limit",
    "hop start",
    "relay node",
    "payload",
)
# The relaying node is only the last byte of its ID: a candidate further than this from the
# receiver can't plausibly have been the last hop. Assumption, changeable in the layer.
MAX_RELAY_KM = 15.0


class MissingColumns(ValueError):
    """The CSV lacks required columns (not an export of the app, or its format changed)."""

    def __init__(self, columns: list[str]):
        super().__init__(", ".join(columns))
        self.columns = columns


def node_id(num: int) -> str:
    return f"!{num & 0xFFFFFFFF:08x}"


def _float(value: str) -> float | None:
    try:
        return float(value) if value.strip() else None
    except ValueError:
        return None


def _int(value: str) -> int | None:
    try:
        return int(value) if value.strip() else None
    except ValueError:
        return None


def _relay_byte(value: str) -> int | None:
    """ "14" -> 0x14; the app writes the last byte of the relaying node in hex."""
    v = value.strip().lower()
    if not 1 <= len(v) <= 2:
        return None
    try:
        return int(v, 16)
    except ValueError:
        return None


def _portnum(payload: str) -> str:
    """ "<POSITION_APP>" -> POSITION_APP; anything else is a text message, whose wording is never
    kept (it is other people's message)."""
    p = payload.strip()
    if p.startswith("<") and p.endswith(">") and len(p) > 2:
        return p[1:-1]
    return "TEXT_MESSAGE_APP"


def _pos(lat: str, lon: str) -> list[float] | None:
    a, b = _float(lat), _float(lon)
    return [a, b] if a is not None and b is not None and (a, b) != (0.0, 0.0) else None


def parse_app_csv(text: str, tz: tzinfo | None = None) -> tuple[list[dict], dict, dict]:
    """(records, nodes, stats) of an app export.

    tz: zone of the export's local times; None = the computer's zone, per date (summer time).
    records: {time, from, rxSnr, hopStart, hopLimit, relayNode, portnum, rxPos}, time-sorted.
    nodes: every sender of the whole export, {"!id": {name, lastSeen, pos}}, pos = last position.
    Raises MissingColumns when a required column is missing.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise MissingColumns(missing)
    col = {name: header[name] for name in header}

    def get(row: dict, name: str) -> str:
        return (row.get(col.get(name, ""), "") or "").strip()

    records, nodes = [], {}
    skipped: dict[str, int] = {}
    rows = 0
    for row in reader:
        rows += 1
        try:
            naive = datetime.fromisoformat(f"{get(row, 'date')}T{get(row, 'time')}")
            num = int(get(row, "from"))
        except ValueError:
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue
        t = naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()
        num &= 0xFFFFFFFF
        records.append(
            {
                "time": t,
                "from": num,
                "rxSnr": _float(get(row, "rx snr")),
                "hopStart": _int(get(row, "hop start")),
                "hopLimit": _int(get(row, "hop limit")),
                "relayNode": _relay_byte(get(row, "relay node")),
                "portnum": _portnum(get(row, "payload")),
                "rxPos": _pos(get(row, "rx lat"), get(row, "rx long")),
            }
        )
        node = nodes.setdefault(node_id(num), {"name": "", "lastSeen": None, "pos": None})
        node["name"] = get(row, "sender name") or node["name"]
        node["lastSeen"] = t.isoformat()
        node["pos"] = _pos(get(row, "sender lat"), get(row, "sender long")) or node["pos"]
    records.sort(key=lambda r: r["time"])
    stats = {
        "rows": rows,
        "used": len(records),
        "skipped": skipped,
        "start": records[0]["time"].isoformat() if records else None,
        "end": records[-1]["time"].isoformat() if records else None,
    }
    return records, nodes, stats


def hops(rec: dict) -> int | None:
    """Hops taken; None when unknown (hop start 0 is firmware without hop_start, not direct)."""
    start, limit = rec.get("hopStart"), rec.get("hopLimit")
    if not start or limit is None or start < limit:
        return None
    return start - limit


def own_candidate(records: list[dict]) -> int | None:
    """The sender that is most likely the receiving device itself: its own packets carry SNR 0.0
    and no relay. By majority of its rows, since a real packet may have SNR 0.0 too and the own
    node also shows up as relay of packets it rebroadcast."""
    rows: dict[int, list[int]] = {}
    for r in records:
        own_like = r["rxSnr"] == 0.0 and r["relayNode"] is None
        c = rows.setdefault(r["from"], [0, 0])
        c[0] += 1
        c[1] += own_like
    best = [(n, own) for n, (total, own) in rows.items() if own * 2 > total]
    return max(best, key=lambda x: x[1])[0] if best else None


def km_between(a: list[float] | tuple, b: list[float] | tuple) -> float:
    from meshplay.walk import distance_m

    return distance_m(tuple(a), tuple(b)) / 1000


def time_offset_check(records: list[dict], track: list[dict], hours: int = 3) -> dict:
    """Whether the export's times fit the track: the app's rx position (stale, but roughly where
    the receiver was) against the track at offsets -hours..+hours.

    Returns {"best_h", "median_m": {h: m}, "clear"}; clear = another offset fits clearly better
    than 0 (half the median distance or less, at least 5 positions).
    """
    from meshplay.walk import distance_m, position_at

    with_pos = [r for r in records if r["rxPos"]]
    medians = {}
    for h in range(-hours, hours + 1):
        d = []
        for r in with_pos:
            p = position_at(track, r["time"] + timedelta(hours=h))
            if p is not None:
                d.append(distance_m(tuple(r["rxPos"]), p))
        if len(d) >= 5:
            medians[h] = statistics.median(d)
    if not medians:
        return {"best_h": 0, "median_m": {}, "clear": False}
    best = min(medians, key=medians.get)
    at0 = medians.get(0)
    clear = best != 0 and (at0 is None or medians[best] * 2 <= at0)
    return {"best_h": best, "median_m": medians, "clear": clear}


def relay_candidates(
    nodes: dict,
    byte: int | None,
    rx_pos: tuple[float, float],
    sender: int,
    n_hops: int | None,
    max_km: float = MAX_RELAY_KM,
) -> list[dict]:
    """Known nodes whose ID ends in the relay byte, nearest first.

    Each {id, name, pos, km, plausible}: the original sender is no candidate once the packet
    was relayed; a node further than max_km from the receiver is not plausible; a node without
    position stays plausible (it can't be ruled out).
    """
    if byte is None:
        return []
    out = []
    for nid, n in nodes.items():
        if int(nid[1:], 16) & 0xFF != byte:
            continue
        if n_hops and int(nid[1:], 16) == sender:
            continue
        km = km_between(rx_pos, n["pos"]) if n["pos"] else None
        out.append(
            {
                "id": nid,
                "name": n["name"],
                "pos": n["pos"],
                "km": km,
                "plausible": km is None or km <= max_km,
            }
        )
    return sorted(out, key=lambda c: (c["km"] is None, c["km"] or 0))


def relay_grade(candidates: list[dict], byte: int | None = 0) -> str:
    """unique (one plausible candidate), likely (the nearest plausible one with position is at
    most half as far as the next, and none without position), ambiguous, or unknown (no byte)."""
    if byte is None:
        return "unknown"
    ok = [c for c in candidates if c["plausible"]]
    if len(ok) == 1:
        return "unique"
    if not ok or any(c["km"] is None for c in ok):
        return "ambiguous"
    return "likely" if ok[0]["km"] * 2 <= ok[1]["km"] else "ambiguous"


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__.splitlines()[2])
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        records, nodes, stats = parse_app_csv(f.read())
    print(f"rows {stats['rows']}, used {stats['used']}, skipped {stats['skipped']}")
    print(f"from {stats['start']} to {stats['end']}, {len(nodes)} senders")
    own = own_candidate(records)
    print(f"receiving device (guess): {node_id(own) if own is not None else '-'}")
    if len(sys.argv) > 2:
        from pathlib import Path

        from meshplay.walk import load_gpx

        track = load_gpx(Path(sys.argv[2]))
        t0, t1 = track[0]["time"], track[-1]["time"]
        inside = [r for r in records if t0 <= r["time"] <= t1 and r["from"] != own]
        print(f"track {t0.isoformat()} .. {t1.isoformat()}: {len(inside)} packets of other nodes")
        print(f"time offset check: {time_offset_check(records, track)}")


if __name__ == "__main__":
    main()
