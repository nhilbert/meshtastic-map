"""Passive walks: a GPX track and the app's packet export (CSV), stored as a pair.

data/tracks/<name>.gpx and data/heard/<name>.jsonl share the name. The JSONL starts with a
header {"meta": {...}} (source files, receiving device, the senders known from the whole export)
followed by the packets of the export that fall into the track. Text messages keep their type
only, never their wording.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

from meshplay.applog import MissingColumns, node_id, own_candidate, parse_app_csv, time_offset_check
from meshplay.mapapp.i18n import _
from meshplay.mapapp.registry import Context
from meshplay.walk import load_heard, parse_gpx

MAX_BYTES = 30_000_000
MARGIN = timedelta(minutes=5)  # packets just before the start or after the end still count


def heard_dir(ctx: Context) -> Path:
    return ctx.data_dir / "heard"


def walk_path(ctx: Context, walk: str) -> Path:
    """The JSONL of a walk; the name comes from the page, so it must be a plain file name."""
    if not walk or Path(walk).name != walk or walk.startswith("."):
        raise ValueError(_("Unbekannter Rundgang: {walk}", walk=walk))
    path = heard_dir(ctx) / f"{walk}.jsonl"
    if not path.is_file():
        raise ValueError(_("Unbekannter Rundgang: {walk}", walk=walk))
    return path


def _record_json(r: dict) -> dict:
    return {**r, "time": r["time"].isoformat()}


def _parse_receiver(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).lstrip("!"), 16) if str(value).startswith("!") else int(value)
    except ValueError:
        raise ValueError(_("Unbekannter Empfänger: {node}", node=value)) from None


def _write(path: Path, text: str) -> Path:
    """Text to a temporary file next to path, as is (no newline translation: a GPX with CRLF
    stays byte-identical on Windows)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    return tmp


def _same_text(path: Path, text: str) -> bool:
    """Whether a stored file holds this text, whatever its line endings or a BOM (a GPX stored
    earlier by the plain track upload keeps the bytes of the phone's file)."""
    stored = path.read_bytes().decode("utf-8", errors="replace")
    return _norm(stored) == _norm(text)


def _norm(text: str) -> str:
    return text.lstrip("\ufeff").replace("\r\n", "\n")


def save_walk(ctx: Context, files: list[dict], receiver=None, tz: tzinfo | None = None) -> dict:
    """Store a walk from exactly one GPX and one CSV ({"name", "text"} each); both files are
    checked before either is written, so a failed import leaves nothing behind. tz: zone of the
    export's local times (None: this computer's)."""
    by_ext: dict[str, list[dict]] = {".gpx": [], ".csv": []}
    for f in files or []:
        ext = Path(str(f.get("name", ""))).suffix.lower()
        if ext not in by_ext:
            raise ValueError(_("Nur .gpx- und .csv-Dateien: {name}", name=f.get("name", "")))
        by_ext[ext].append(f)
    if len(by_ext[".gpx"]) != 1 or len(by_ext[".csv"]) != 1:
        raise ValueError(_("Bitte genau eine GPX-Spur und einen CSV-Export zusammen auswählen."))
    gpx, csv_file = by_ext[".gpx"][0], by_ext[".csv"][0]
    gpx_text, csv_text = str(gpx.get("text", "")), str(csv_file.get("text", ""))
    if len(gpx_text) + len(csv_text) > MAX_BYTES:
        raise ValueError(_("Dateien zu groß (zusammen max. 30 MB)"))

    try:
        track = parse_gpx(gpx_text)
    except Exception as e:
        raise ValueError(_("Keine lesbare GPX-Datei: {error}", error=e)) from None
    if not track:
        raise ValueError(_("Die GPX-Datei hat keine Punkte mit Zeitstempel"))
    try:
        records, nodes, stats = parse_app_csv(csv_text, tz)
    except MissingColumns as e:
        raise ValueError(
            _(
                "Im CSV fehlen Spalten ({columns}). Ist das ein Export der Meshtastic-App?",
                columns=", ".join(e.columns),
            )
        ) from None
    if not records:
        raise ValueError(_("Der CSV-Export enthält keine lesbaren Zeilen."))

    t0, t1 = track[0]["time"], track[-1]["time"]
    inside = [r for r in records if t0 - MARGIN <= r["time"] <= t1 + MARGIN]
    if not inside:
        raise ValueError(
            _(
                "Export und Spur liegen zeitlich nicht übereinander: Export {a} bis {b}, "
                "Spur {c} bis {d}.",
                a=_local(stats["start"]),
                b=_local(stats["end"]),
                c=_local(t0.isoformat()),
                d=_local(t1.isoformat()),
            )
        )

    stem = re.sub(r"[^\w .()-]", "_", Path(str(gpx["name"])).stem).strip() or "walk"
    gpx_out = ctx.data_dir / "tracks" / f"{stem}.gpx"
    out = heard_dir(ctx) / f"{stem}.jsonl"
    same_track = gpx_out.exists() and _same_text(gpx_out, gpx_text)
    if (gpx_out.exists() or out.exists()) and not same_track:
        raise ValueError(
            _(
                "Es gibt schon einen Rundgang oder eine Spur „{name}“ mit anderem Inhalt. "
                "GPX-Datei umbenennen und erneut hochladen.",
                name=stem,
            )
        )

    guessed = receiver in (None, "")
    own = own_candidate(records) if guessed else _parse_receiver(receiver)
    offset = time_offset_check(records, track)
    meta = {
        "source": "android-csv",
        "csvName": csv_file["name"],
        "gpxName": gpx_out.name,
        "uploaded": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tz": datetime.now().astimezone().tzname(),
        "receiver": node_id(own) if own is not None else None,
        "receiverGuessed": guessed,
        "trackStart": t0.isoformat(),
        "trackEnd": t1.isoformat(),
        "bbox": [
            [min(p["lat"] for p in track), min(p["lon"] for p in track)],
            [max(p["lat"] for p in track), max(p["lon"] for p in track)],
        ],
        "nodes": nodes,
    }
    body = "\n".join(
        [json.dumps({"meta": meta}, ensure_ascii=False)]
        + [json.dumps(_record_json(r), ensure_ascii=False) for r in inside]
    )
    gpx_out.parent.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_gpx = _write(gpx_out, gpx_text)
    tmp_out = _write(out, body + "\n")
    tmp_gpx.replace(gpx_out)
    tmp_out.replace(out)

    hint = None
    if offset["clear"]:
        hint = _(
            "Die Zeiten des Exports passen um {h} h verschoben besser zur Spur. "
            "Stimmt die Zeitzone von Handy und Rechner überein?",
            h=f"{offset['best_h']:+d}",
        )
    return {
        "walk": stem,
        "rows": stats["rows"],
        "inWalk": sum(1 for r in inside if r["from"] != own),
        "skipped": sum(stats["skipped"].values()),
        "start": t0.isoformat(),
        "end": t1.isoformat(),
        "receiver": meta["receiver"],
        "receiverName": _name(nodes, meta["receiver"]),
        "receiverGuessed": guessed,
        "offsetHint": hint,
        "bbox": meta["bbox"],
    }


def _local(iso: str | None) -> str:
    if not iso:
        return "–"
    return datetime.fromisoformat(iso).astimezone().strftime("%d.%m. %H:%M")


def _name(nodes: dict, nid: str | None) -> str:
    return (nodes.get(nid) or {}).get("name", "") if nid else ""


def walk_summary(path: Path) -> dict:
    """What the walk list and the layer need to know about a walk."""
    meta, records = load_heard(path)
    receiver = _parse_receiver(meta.get("receiver"))
    nodes = meta.get("nodes", {})
    senders = Counter(r["from"] for r in records)
    first, last = (
        (records[0]["time"].isoformat(), records[-1]["time"].isoformat())
        if records
        else (None, None)
    )
    return {
        "walk": path.stem,
        "start": meta.get("trackStart", first),
        "end": meta.get("trackEnd", last),
        "bbox": meta.get("bbox"),
        "packets": sum(n for s, n in senders.items() if s != receiver),
        "receiver": meta.get("receiver"),
        "receiverName": _name(nodes, meta.get("receiver")),
        "receiverGuessed": meta.get("receiverGuessed", False),
        "senders": [[node_id(s), _name(nodes, node_id(s)), n] for s, n in senders.most_common()],
        "gpxName": meta.get("gpxName", f"{path.stem}.gpx"),
    }


def list_walks(ctx: Context) -> list[dict]:
    """All passive walks, newest first."""
    d = heard_dir(ctx)
    walks = [walk_summary(p) for p in d.glob("*.jsonl")] if d.is_dir() else []
    return sorted(walks, key=lambda w: w["start"] or "", reverse=True)


def set_receiver(ctx: Context, walk: str, receiver) -> dict:
    """Change the receiving device of a walk: only the header line is rewritten."""
    path = walk_path(ctx, walk)
    num = _parse_receiver(receiver)
    lines = path.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])["meta"] if lines and lines[0].startswith('{"meta"') else {}
    senders = {json.loads(line)["from"] for line in lines[1:] if line.strip()}
    if num is None or num not in senders:
        raise ValueError(_("Unbekannter Empfänger: {node}", node=receiver))
    meta.update(receiver=node_id(num), receiverGuessed=False)
    head = json.dumps({"meta": meta}, ensure_ascii=False)
    rest = lines[1:] if lines and lines[0].startswith('{"meta"') else lines
    _write(path, "\n".join([head, *rest]) + "\n").replace(path)
    return walk_summary(path)
