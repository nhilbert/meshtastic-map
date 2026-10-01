"""Sources of elevation data for scenes, one per German state that has one here.

The map app and scripts/sim_build_scene.py pick the source by the state a scene's centre lies
in. A source downloads only through an interface its office documents for programs (a folder
with fixed names, a STAC API, a published tile index), only when the owner asks for it, and the
plan always lists the files with their links for a download by hand. All deliver EPSG:25832.
New state: a Source subclass here (see base.py), listed in SOURCES.
"""

from __future__ import annotations

from meshplay.sim.sources.base import Source, Tile, TileFile, grid_keys, state_at
from meshplay.sim.sources.ni import Ni
from meshplay.sim.sources.nrw import Nrw
from meshplay.sim.sources.sh import Sh

SOURCES: list[Source] = [Nrw(), Ni(), Sh()]

__all__ = ["SOURCES", "Source", "Tile", "TileFile", "by_id", "for_point", "grid_keys", "state_at"]


def by_id(source_id: str) -> Source:
    for s in SOURCES:
        if s.id == source_id:
            return s
    raise KeyError(f"no source {source_id}")


def for_point(lat: float, lon: float) -> tuple[Source | None, str | None]:
    """(source, state) at a point; source None where the state has none yet (state None
    outside Germany)."""
    state = state_at(lat, lon)
    return next((s for s in SOURCES if s.state == state), None), state
