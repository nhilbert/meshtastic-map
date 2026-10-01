"""North Rhine-Westphalia: the classified laser-scan point cloud (Geobasis NRW, 3D-Messdaten).

Tiles of 1 km in a public folder with fixed names (3dm_32_<E>_<N>_1_nw.laz, 60-130 MB each); the
scene is built from the points themselves (meshplay.sim.scene). Kept in data/sim/laz/, where
earlier versions put them.
"""

from __future__ import annotations

from pathlib import Path

from meshplay.sim.scene import TILE_URL, Scene, tile_name
from meshplay.sim.sources.base import Source, Tile, TileFile, grid_keys

TILE_MB = 95  # typical tile (60-130 MB), for the estimate


class Nrw(Source):
    id = "nrw"
    state = "Nordrhein-Westfalen"
    product = "3D-Messdaten, Laserscan-Punktwolke"
    licence = "dl-de/zero-2-0"
    attribution = "Geobasis NRW"
    portal = TILE_URL
    interface = "opengeodata.nrw.de (Ordner mit festen Dateinamen)"
    kind = "laz"

    def tiles_dir(self, sim_dir: Path) -> Path:
        return sim_dir / "laz"

    def tiles(self, bbox, tiles_dir: Path) -> list[Tile]:
        out = []
        for e, n in grid_keys(bbox):
            name = tile_name(e, n)
            out.append(Tile((e, n), [TileFile(name, TILE_URL + name, TILE_MB, tiles_dir / name)]))
        return out

    tiles_offline = tiles  # the names follow from the grid

    def build(self, bbox, tiles_dir: Path, res: float, on_file=None, buildings=None) -> Scene:
        # the points separate buildings from trees themselves; footprints aren't needed
        return Scene.build(tiles_dir, bbox, res, on_tile=on_file)
