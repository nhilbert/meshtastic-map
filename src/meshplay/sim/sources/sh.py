"""Schleswig-Holstein: DGM1 (laser scan) and bDOM (image-based surface, 20 cm), LVermGeo SH.

The office offers a "Massendownload": per product a GeoJSON index of all 1 km tiles, each with
its download link (OpenGBD). The indexes (about 9 MB each) are kept for 30 days. DGM1 comes as
ASCII x y z (about 27 MB per tile), bDOM as GeoTIFF (about 105 MB); the scene takes the
highest bDOM value per metre. The bDOM is matched from aerial images, so trees are less exact
than from a laser surface.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from functools import lru_cache
from pathlib import Path

from meshplay.sim.lidar import download_url
from meshplay.sim.sources.base import Source, Tile, TileFile, grid_keys
from meshplay.sim.sources.raster import scene_from_rasters

BASE = "https://geodaten.schleswig-holstein.de/gaialight-sh/_apps/dladownload/"
PRODUCTS = {  # product: (index file, file pattern of a tile, MB per file)
    "dgm1": ("DGM1_SH__Massendownload.geojson", "dgm1_32_{e}_{n}_1_sh_*.xyz", 27.0),
    "bdom": ("bDOM_SH_Massendownload.geojson", "bdom20nc_32_{e}_{n}_1_sh_*.tif", 105.0),
}
INDEX_DAYS = 30


def _index_path(tiles_dir: Path, product: str) -> Path:
    return tiles_dir / "index" / f"{product}.geojson"


def _fetch_index(tiles_dir: Path, product: str) -> Path:
    """The product's tile index, downloaded when missing or older than INDEX_DAYS. Keeps an
    older copy when the server can't be reached (raises OSError only without one)."""
    path = _index_path(tiles_dir, product)
    if path.exists() and time.time() - path.stat().st_mtime < INDEX_DAYS * 86400:
        return path
    url = BASE + "single.php?" + urllib.parse.urlencode({"file": PRODUCTS[product][0], "id": 4})
    fresh = path.with_name(path.name + ".new")
    try:
        fresh.unlink(missing_ok=True)
        download_url(url, fresh, timeout=120)
        json.loads(fresh.read_text(encoding="utf-8"))["features"]  # a real index, not an error page
        fresh.replace(path)
    except (OSError, ValueError, KeyError):
        fresh.unlink(missing_ok=True)
        if not path.exists():
            raise OSError(f"tile index of Schleswig-Holstein ({product}) not reachable") from None
    return path


@lru_cache(maxsize=4)
def _parse(path: Path, mtime: float) -> dict:
    """{(E, N): (file name, url)} of an index (mtime only keys the cache)."""
    out = {}
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        ring = f["geometry"]["coordinates"][0][0]
        key = (round(min(p[0] for p in ring) / 1000), round(min(p[1] for p in ring) / 1000))
        url = f["properties"]["link_data"]
        name = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["file"][0]
        out[key] = (name, url)
    return out


def _local(tiles_dir: Path, product: str, key) -> Path | None:
    found = sorted((tiles_dir / product).glob(PRODUCTS[product][1].format(e=key[0], n=key[1])))
    return found[-1] if found else None


class Sh(Source):
    id = "sh"
    state = "Schleswig-Holstein"
    product = "DGM1 (1 m) und bildbasiertes DOM (20 cm)"
    licence = "CC BY 4.0"
    attribution = "© GeoBasis-DE/LVermGeo SH/CC BY 4.0"
    portal = "https://geodaten.schleswig-holstein.de/gaialight-sh/_apps/dladownload/"
    interface = "Massendownload-Index (GeoJSON) des LVermGeo SH"

    def tiles(self, bbox, tiles_dir: Path) -> list[Tile]:
        index = {}
        for p in PRODUCTS:
            path = _fetch_index(tiles_dir, p)
            index[p] = _parse(path, path.stat().st_mtime)
        out = []
        for key in grid_keys(bbox):
            files = [
                TileFile(
                    index[p][key][0],
                    index[p][key][1],
                    PRODUCTS[p][2],
                    tiles_dir / p / index[p][key][0],
                )
                for p in PRODUCTS
                if key in index[p]
            ]
            out.append(Tile(key, files if len(files) == len(PRODUCTS) else []))
        return out

    def tiles_offline(self, bbox, tiles_dir: Path) -> list[Tile]:
        out = []
        for key in grid_keys(bbox):
            paths = [_local(tiles_dir, p, key) for p in PRODUCTS]
            files = [TileFile(p.name, None, 0.0, p) for p in paths if p]
            out.append(Tile(key, files if len(files) == len(PRODUCTS) else []))
        return out

    def build(self, bbox, tiles_dir: Path, res: float, on_file=None, buildings=None):
        keys = grid_keys(bbox)
        dgm = [p for k in keys if (p := _local(tiles_dir, "dgm1", k))]
        dom = [p for k in keys if (p := _local(tiles_dir, "bdom", k))]
        return scene_from_rasters(bbox, res, dgm, dom, buildings, on_file)
