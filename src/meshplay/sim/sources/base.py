"""What every source of elevation data offers; which state a point lies in."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from meshplay.config import PROJECT_ROOT

BASEMAP = PROJECT_ROOT / "webmap" / "vendor" / "basemap" / "germany.json"


@dataclass
class TileFile:
    """One file of a tile: where it comes from (None: unknown offline) and where it goes."""

    name: str
    url: str | None
    mb: float
    path: Path

    @property
    def present(self) -> bool:
        return self.path.exists()


@dataclass
class Tile:
    key: tuple[int, int]  # lower-left corner in km (E, N), EPSG:25832
    files: list[TileFile] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return bool(self.files) and all(f.present for f in self.files)


class Source:
    """A state's elevation data. Subclasses set the attributes and implement tiles/build."""

    id = ""
    state = ""  # as in the overview map (Natural Earth, German names)
    product = ""
    licence = ""
    attribution = ""  # to show with the scene
    portal = ""  # where a person downloads by hand
    interface = ""  # the documented interface the download helper uses
    kind = "raster"  # or "laz"

    def tiles_dir(self, sim_dir: Path) -> Path:
        return sim_dir / "tiles" / self.id

    def tiles(self, bbox, tiles_dir: Path) -> list[Tile]:
        """The tiles covering bbox (EPSG:25832); may ask the source's index (network, cached).

        Raises OSError when the index can't be reached; tiles_offline() then still knows the
        files that are there.
        """
        raise NotImplementedError

    def tiles_offline(self, bbox, tiles_dir: Path) -> list[Tile]:
        """The tiles covering bbox with the files already in tiles_dir (no network)."""
        raise NotImplementedError

    def build(self, bbox, tiles_dir: Path, res: float, on_file=None, buildings=None):
        raise NotImplementedError

    def describe(self) -> dict:
        return dict(
            id=self.id,
            state=self.state,
            product=self.product,
            licence=self.licence,
            attribution=self.attribution,
            portal=self.portal,
            interface=self.interface,
            kind=self.kind,
        )


def grid_keys(bbox) -> list[tuple[int, int]]:
    """The 1 km tiles covering an EPSG:25832 box, north to south, west to east."""
    e0, n0 = math.floor(bbox[0] / 1000), math.floor(bbox[1] / 1000)
    e1, n1 = math.floor((bbox[2] - 1e-6) / 1000), math.floor((bbox[3] - 1e-6) / 1000)
    return [(e, n) for n in range(n1, n0 - 1, -1) for e in range(e0, e1 + 1)]


@lru_cache(maxsize=1)
def _states():
    from shapely.geometry import shape

    data = json.loads(BASEMAP.read_text(encoding="utf-8"))
    return [
        (f["properties"]["name"], shape(f["geometry"]))
        for f in data["features"]
        if f["properties"]["k"] == "state"
    ]


def state_at(lat: float, lon: float) -> str | None:
    """The German state of a point (overview map borders, about 300 m exact), or None."""
    from shapely.geometry import Point

    p = Point(lon, lat)
    hit = [name for name, geom in _states() if geom.covers(p)]
    if hit:
        return hit[0]
    near = min(_states(), key=lambda s: s[1].distance(p))  # just outside a simplified border
    return near[0] if near[1].distance(p) < 0.01 else None
