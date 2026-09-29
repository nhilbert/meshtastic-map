"""The road graph for routing: downloaded from the Overpass API or imported from an OSM file.

Overpass (overpass-api.de) answers "all highway ways in this bounding box" with the ways, their
tags and node coordinates as JSON; a few megabytes for a city. The answer becomes a graph
(junctions and way ends as nodes, the way pieces between them as edges with their geometry)
stored as data/osm/<name>.json.gz; routing is offline afterwards. The bounding box is the only
thing the query reveals; it is snapped outward to a 0.01° grid so it isn't centred on home.
Every user downloads their own area; the file is never committed.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from meshplay.mapapp.i18n import N_, L, _
from meshplay.mapapp.jobs import Job, JobKind
from meshplay.mapapp.registry import Context, Setting
from meshplay.walk import distance_m

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "meshplay/0.1 (coordination mode; https://github.com/nhilbert/meshtastic-map)"
SKIP_HIGHWAYS = "proposed|construction|abandoned|razed|platform|raceway|bus_stop|elevator"
KEEP_TAGS = (
    "highway",
    "name",
    "oneway",
    "oneway:bicycle",
    "access",
    "foot",
    "bicycle",
    "motor_vehicle",
)
MAX_SPAN_DEG = 0.5  # about 50 km: more than a city is more than Overpass likes in one go


def snap_bbox(south: float, west: float, north: float, east: float, grid: float = 0.01):
    """Outward to the grid: a box that is not centred on the home position."""
    return (
        round(math.floor(south / grid) * grid, 6),
        round(math.floor(west / grid) * grid, 6),
        round(math.ceil(north / grid) * grid, 6),
        round(math.ceil(east / grid) * grid, 6),
    )


def default_bbox(home: tuple[float, float] | None, radius_m: float = 3000.0):
    if home is None:
        return None
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * math.cos(math.radians(home[0])))
    return snap_bbox(home[0] - dlat, home[1] - dlon, home[0] + dlat, home[1] + dlon)


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    try:
        s, w, n, e = (float(v) for v in re.split(r"[,\s]+", text.strip()))
    except ValueError:
        raise ValueError(_("Bounding Box: vier Zahlen Süd, West, Nord, Ost")) from None
    if not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
        raise ValueError(_("Bounding Box: Süd < Nord und West < Ost"))
    if n - s > MAX_SPAN_DEG or e - w > MAX_SPAN_DEG:
        raise ValueError(_("Bounding Box zu groß: höchstens {deg}° je Seite", deg=MAX_SPAN_DEG))
    return s, w, n, e


def overpass_query(bbox) -> str:
    s, w, n, e = bbox
    return (
        "[out:json][timeout:300];\n"
        f'way["highway"]["highway"!~"^({SKIP_HIGHWAYS})$"]["area"!="yes"]({s},{w},{n},{e});\n'
        "out body geom;\n"
    )


def download(bbox, raw_path: Path, on_progress=None) -> None:
    """Stream the Overpass answer to raw_path; one retry after a minute on 429/504."""
    data = overpass_query(bbox).encode("utf-8")
    req = urllib.request.Request(OVERPASS_URL, data=data, headers={"User-Agent": USER_AGENT})
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=330) as resp, raw_path.open("wb") as f:
                got = 0
                for chunk in iter(lambda: resp.read(1 << 16), b""):
                    f.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        on_progress(got)
            return
        except urllib.error.HTTPError as e:
            if attempt == 2 or e.code not in (429, 504):
                raise RuntimeError(_("Overpass antwortet mit HTTP {code}", code=e.code)) from None
            time.sleep(60)


# ---------------------------------------------------------------- graph building
def ways_from_overpass(raw: dict):
    """(way id, tags, [(lat, lon), …], [node ids]) for every way of an Overpass JSON answer."""
    for el in raw.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        yield (
            el["id"],
            el.get("tags", {}),
            [(p["lat"], p["lon"]) for p in el["geometry"]],
            el["nodes"],
        )


def ways_from_osm_xml(path: Path):
    """The same from an .osm file (JOSM, Overpass XML export)."""
    coords: dict[str, tuple[float, float]] = {}
    for _ev, el in ET.iterparse(path, events=("end",)):
        if el.tag == "node":
            coords[el.get("id")] = (float(el.get("lat")), float(el.get("lon")))
        elif el.tag == "way":
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            refs = [nd.get("ref") for nd in el.findall("nd")]
            if "highway" in tags and all(r in coords for r in refs):
                if (
                    not re.match(f"^({SKIP_HIGHWAYS})$", tags["highway"])
                    and tags.get("area") != "yes"
                ):
                    yield int(el.get("id")), tags, [coords[r] for r in refs], [int(r) for r in refs]
            el.clear()


def build_graph(ways, bbox=None, source: str = "overpass") -> dict:
    """Junctions and way ends become graph nodes; the way pieces between them edges."""
    ways = list(ways)
    uses = Counter()
    for _id, _tags, _pts, refs in ways:
        uses.update(refs)
    node_index: dict[int, int] = {}
    nodes: list[list[float]] = []
    edges: list[list] = []

    def node_id(ref: int, pt) -> int:
        if ref not in node_index:
            node_index[ref] = len(nodes)
            nodes.append([round(pt[0], 7), round(pt[1], 7)])
        return node_index[ref]

    for _id, tags, pts, refs in ways:
        if len(refs) < 2:
            continue
        kept = {k: tags[k] for k in KEEP_TAGS if k in tags}
        start = 0
        for i in range(1, len(refs)):
            if uses[refs[i]] >= 2 or i == len(refs) - 1:
                geom = pts[start : i + 1]
                length = sum(distance_m(a, b) for a, b in zip(geom, geom[1:], strict=False))
                edges.append(
                    [
                        node_id(refs[start], pts[start]),
                        node_id(refs[i], pts[i]),
                        round(length, 1),
                        [[round(p[0], 7), round(p[1], 7)] for p in geom],
                        kept,
                    ]
                )
                start = i
    return {
        "bbox": list(bbox) if bbox else None,
        "source": source,
        "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_ways": len(ways),
        "nodes": nodes,
        "edges": edges,
    }


def write_graph(graph: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(graph, f, separators=(",", ":"))


def graph_files(ctx: Context) -> list[Path]:
    return sorted((ctx.data_dir / "osm").glob("*.json.gz"))


def graph_info(path: Path) -> dict:
    """Header of a graph file without loading the edges (for the page)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        head = f.read(4000)
    info = {"name": path.name[: -len(".json.gz")], "file": path.name}
    for key in ("bbox", "source", "downloaded", "n_ways"):
        m = re.search(rf'"{key}":\s*(\[[^\]]*\]|"[^"]*"|\d+)', head)
        if m:
            info[key] = json.loads(m[1])
    return info


# ---------------------------------------------------------------- task kind
class OsmDownload(JobKind):
    id = "osm"
    name = N_("Straßennetz laden")
    description = N_(
        "Lädt alle Straßen und Wege in einer Bounding Box von der Overpass-API (OpenStreetMap) "
        "und baut daraus den Graphen für die Wegführung des Koordinationsmodus (data/osm/). "
        "Die Box ist das Einzige, was der Abfrage-Server erfährt; danach läuft alles offline. "
        "Eine Stadt: einige MB, ein bis zwei Minuten."
    )

    def settings(self, ctx: Context) -> list[Setting]:
        home = ctx.settings.home
        if not home and ctx.sites:
            s = next(iter(ctx.sites.values()))
            home = (s["lat"], s["lon"])
        box = default_bbox(home)
        return [
            Setting(
                "bbox",
                _("Bounding Box (Süd, West, Nord, Ost)"),
                "text",
                "" if box is None else ", ".join(f"{v:.2f}" for v in box),
                help=_("Vorgabe: 3 km um den Heimstandort, auf ein 1-km-Raster gerundet"),
            ),
            Setting(
                "name",
                _("Dateiname"),
                "text",
                "roads",
                help=_("data/osm/<Name>.json.gz; ein neuer Name behält den alten Graphen"),
            ),
        ]

    def validate(self, ctx: Context, params: dict) -> None:
        parse_bbox(str(params["bbox"]))
        if not re.match(r"^[A-Za-z0-9_-]{1,32}$", str(params["name"])):
            raise ValueError(_("Dateiname: 1–32 Zeichen, nur Buchstaben, Ziffern, _ und -"))

    def title(self, params: dict) -> L:
        return L("Straßennetz {name}", name=params["name"])

    def run(self, ctx: Context, job: Job) -> None:
        bbox = parse_bbox(str(job.params["bbox"]))
        out = ctx.data_dir / "osm" / f"{job.params['name']}.json.gz"
        raw = out.with_suffix("").with_suffix(".overpass.json")
        job.add_log(_("Overpass-Abfrage für {bbox}", bbox=", ".join(f"{v:.2f}" for v in bbox)))
        job.detail = L("lädt von overpass-api.de …")

        def on_progress(got: int) -> None:
            job.check_stop()
            job.detail = L("lädt von overpass-api.de … {mb} MB", mb=f"{got / 1e6:.1f}")

        download(bbox, raw, on_progress)
        job.check_stop()
        job.detail = L("baut den Graphen …")
        try:
            with raw.open("rb") as f:
                data = json.load(f)
            if "elements" not in data:
                raise RuntimeError(_("Unerwartete Antwort von Overpass (kein JSON mit elements)"))
            graph = build_graph(ways_from_overpass(data), bbox)
        finally:
            raw.unlink(missing_ok=True)
        write_graph(graph, out)
        job.result = {"file": out.name, "nodes": len(graph["nodes"]), "edges": len(graph["edges"])}
        job.add_log(
            _(
                "{ways} Wege, {nodes} Knoten, {edges} Kanten -> {file}",
                ways=graph["n_ways"],
                nodes=len(graph["nodes"]),
                edges=len(graph["edges"]),
                file=out.name,
            )
        )
        job.progress = 1.0
        job.detail = L("fertig: {file}", file=out.name)
        if ctx.coord is not None:
            ctx.coord.graph_changed()
