"""Site and scenario configuration for the simulation (data/sim/sites.json, not committed).

Format (see data/sim/sites.example.json):

{
  "sites": {
    "HOME":   {"lon": 7.09, "lat": 50.74, "height_m": [3, 4], "clutter_m": 24.1},
    "HOME_W": {"same_as": "HOME", "height_m": [6, 10]}        # inherits position/clutter
  },
  "scenarios": [
    {"id": "A1", "label": "...", "a": "HOME", "b": "ROOF", "a_indoor": "open",
     "b_indoor": null, "device": "p1pro", "leaf": "belaubt"}
  ],
  "corridor": {"id_prefix": "K", "from": "HOME", "towards": "ROOF",
               "distances_m": [100, 200], "rx_height_m": [1.2, 1.8], "rx_clutter_m": 12.0,
               "tx_indoor": "open"},
  "preset_comparison": ["A1"]
}

height_m: antenna height above ground as a uniform prior range. clutter_m: local clutter
(roof/tree) height at the node, used for location variability and terminal clutter loss.
indoor: null (antenna outside), "open" window, "trad" (closed, old glazing), "lowe" (low-E).
"""

from __future__ import annotations

import json
from pathlib import Path

from pyproj import Transformer

_TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
_TO_LL = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)


def to_utm(lon: float, lat: float) -> tuple[float, float]:
    return _TO_UTM.transform(lon, lat)


def to_lonlat(x: float, y: float) -> tuple[float, float]:
    return _TO_LL.transform(x, y)


def load_config(path: Path) -> dict:
    if not Path(path).exists():
        raise SystemExit(
            f"{path} not found. Copy data/sim/sites.example.json to data/sim/sites.json "
            "and enter your sites."
        )
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    sites = cfg["sites"]
    for name, site in sites.items():
        base = site.get("same_as")
        if base:
            site.update({k: v for k, v in sites[base].items() if k not in site})
            site["node"] = sites[base].get("node", base)
        site.setdefault("node", name)
        site["utm"] = to_utm(site["lon"], site["lat"])
        site["height_m"] = tuple(site["height_m"])
    return cfg
