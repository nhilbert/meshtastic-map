"""Lower Saxony: DGM1 and DOM1 (LGLN, OpenGeoData.NI), 1 m rasters from the laser scan.

The LGLN publishes both as Cloud-Optimized GeoTIFFs of 1 km with a STAC API for programs
(dgm.stac.lgln.niedersachsen.de, dom.stac.lgln.niedersachsen.de); a search by area returns
each tile's file URL, so no file name is guessed. About 4 MB per file.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date
from functools import lru_cache
from pathlib import Path

from meshplay.sim.sources.base import Source, Tile, TileFile, grid_keys
from meshplay.sim.sources.raster import scene_from_rasters

CATALOGS = {
    "dgm1": "https://dgm.stac.lgln.niedersachsen.de",
    "dom1": "https://dom.stac.lgln.niedersachsen.de",
}
ID_RE = re.compile(r"^(dgm1|dom1)_32_(\d+)_(\d+)_1_ni_(\d{4})$")
FILE_MB = 4.0


@lru_cache(maxsize=64)
def _search(product: str, w: float, s: float, e: float, n: float) -> dict:
    """{(E, N): (file name, url, year)} of the newest tile per key in a lon/lat box."""
    q = urllib.parse.urlencode({"bbox": f"{w},{s},{e},{n}", "collections": product, "limit": 500})
    req = urllib.request.Request(
        f"{CATALOGS[product]}/search?{q}",
        headers={"User-Agent": "meshplay/0.1 (https://github.com/nhilbert/meshtastic-map)"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    out = {}
    for item in data.get("features", []):
        m = ID_RE.match(item.get("id", ""))
        url = next(
            (a["href"] for a in item.get("assets", {}).values() if a["href"].endswith(".tif")), None
        )
        if not m or not url:
            continue
        key, year = (int(m[2]), int(m[3])), int(m[4])
        if key not in out or year > out[key][2]:
            out[key] = (url.rsplit("/", 1)[-1], url, year)
    return out


def _lonlat_box(bbox):
    from meshplay.sim.sites import to_lonlat

    pts = [to_lonlat(x, y) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
    return (
        round(min(p[0] for p in pts), 5),
        round(min(p[1] for p in pts), 5),
        round(max(p[0] for p in pts), 5),
        round(max(p[1] for p in pts), 5),
    )


def _local(tiles_dir: Path, product: str, key) -> Path | None:
    """The newest file of a tile already downloaded."""
    found = sorted((tiles_dir / product).glob(f"{product}_32_{key[0]}_{key[1]}_1_ni_*.tif"))
    return found[-1] if found else None


class Ni(Source):
    id = "ni"
    state = "Niedersachsen"
    product = "DGM1 und DOM1 (1 m)"
    licence = "CC BY 4.0"
    attribution = f"© GeoBasis-DE/LGLN {date.today().year}, Daten geändert"  # year of download
    portal = "https://ni-lgln-opengeodata.hub.arcgis.com/"
    interface = "STAC-API des LGLN"

    def tiles(self, bbox, tiles_dir: Path) -> list[Tile]:
        box = _lonlat_box(bbox)
        found = {p: _search(p, *box) for p in CATALOGS}
        out = []
        for key in grid_keys(bbox):
            files = []
            for p in CATALOGS:
                if key in found[p]:
                    name, url, _year = found[p][key]
                    files.append(TileFile(name, url, FILE_MB, tiles_dir / p / name))
            out.append(Tile(key, files if len(files) == len(CATALOGS) else []))
        return out

    def tiles_offline(self, bbox, tiles_dir: Path) -> list[Tile]:
        out = []
        for key in grid_keys(bbox):
            paths = [_local(tiles_dir, p, key) for p in CATALOGS]
            files = [TileFile(p.name, None, FILE_MB, p) for p in paths if p]
            out.append(Tile(key, files if len(files) == len(CATALOGS) else []))
        return out

    def build(self, bbox, tiles_dir: Path, res: float, on_file=None, buildings=None):
        keys = grid_keys(bbox)
        dgm = [p for k in keys if (p := _local(tiles_dir, "dgm1", k))]
        dom = [p for k in keys if (p := _local(tiles_dir, "dom1", k))]
        return scene_from_rasters(bbox, res, dgm, dom, buildings, on_file)
