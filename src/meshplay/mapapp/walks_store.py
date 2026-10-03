"""The walks of the page's view Rundgänge: both kinds listed and deleted the same way.

An active walk is a day: data/probes/<date>.jsonl (traceroutes) and data/packets/<date>.jsonl
(the packet log with the tracker's positions), with the GPX tracks recorded on that day. A
passive walk is data/heard/<name>.jsonl with its track (heard_store.py). Both kinds keep their
tracks in data/tracks/, and one track can serve a walk of each kind: deleting a walk removes its
track only when no other walk uses it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from meshplay.mapapp import heard_store
from meshplay.mapapp.i18n import _
from meshplay.mapapp.layers.walk import track_span, walk_logs
from meshplay.mapapp.registry import Context
from meshplay.walk import load_heard


def _passive_track(ctx: Context, walk: dict) -> Path:
    """The track of a passive walk; the name comes from a file, so only its last part counts."""
    return ctx.data_dir / "tracks" / Path(walk["gpxName"]).name


def list_active(ctx: Context) -> list[dict]:
    """Active walks, newest first: the days of walk_logs() that have a tracker, each with the
    GPX tracks recorded on that day ({"name", "bbox", "shared": another walk uses it too})."""
    walks = [w for w in walk_logs(ctx) if w["trackers"]]
    days = {w["date"] for w in walks}
    passive = {_passive_track(ctx, w).name for w in heard_store.list_walks(ctx)}
    for w in walks:
        w["tracks"] = []
    by_day = {w["date"]: w["tracks"] for w in walks}
    for path in sorted((ctx.data_dir / "tracks").glob("*.gpx")):
        covered, bbox = track_span(path)
        used = days.intersection(covered)
        for d in used:
            shared = path.name in passive or len(used) > 1
            by_day[d].append({"name": path.name, "bbox": bbox, "shared": shared})
    return walks


def list_passive(ctx: Context) -> list[dict]:
    """Passive walks, newest first, each with "gpxShared": an active walk uses its track too."""
    days = {w["date"] for w in walk_logs(ctx) if w["trackers"]}
    walks = heard_store.list_walks(ctx)
    for w in walks:
        w["gpxShared"] = bool(days.intersection(track_span(_passive_track(ctx, w))[0]))
    return walks


def delete_active(ctx: Context, day: str) -> dict:
    """Delete an active walk: both logs of the day and its tracks no other walk uses. The packet
    log is the day's whole log, not only the tracker's positions."""
    walk = next((w for w in list_active(ctx) if w["date"] == day), None)
    if walk is None:
        raise ValueError(_("Unbekannter Rundgang: {walk}", walk=day))
    if day == date.today().isoformat():  # today's logs may still be written
        if ctx.jobs is not None and ctx.jobs.running("probe"):
            raise ValueError(_("Ein Traceroute-Rundgang läuft: erst stoppen"))
        dev = ctx.device
        if dev is not None and dev.state == "verbunden" and dev.log_packets:
            raise ValueError(_("Das Paketlog von heute wird gerade geschrieben: erst trennen"))
    files = [ctx.data_dir / folder / f"{day}.jsonl" for folder in ("probes", "packets")]
    files += [ctx.data_dir / "tracks" / t["name"] for t in walk["tracks"] if not t["shared"]]
    return _delete(files, kept=[t["name"] for t in walk["tracks"] if t["shared"]])


def delete_passive(ctx: Context, walk: str) -> dict:
    """Delete a passive walk: its packets and its track, unless an active walk uses the track."""
    path = heard_store.walk_path(ctx, walk)
    meta, _records = load_heard(path)
    track = _passive_track(ctx, {"gpxName": meta.get("gpxName", f"{walk}.gpx")})
    days = {w["date"] for w in walk_logs(ctx) if w["trackers"]}
    if days.intersection(track_span(track)[0]):
        return _delete([path], kept=[track.name])
    return _delete([path, track], kept=[])


def _delete(files: list[Path], kept: list[str]) -> dict:
    deleted = []
    for f in files:
        if f.is_file():
            f.unlink()
            deleted.append(f"{f.parent.name}/{f.name}")
    return {"deleted": deleted, "kept": kept}
