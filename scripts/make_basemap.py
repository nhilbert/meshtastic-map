"""Build the offline overview map of the map app from Natural Earth (public domain).

python scripts/make_basemap.py [--src data/basemap_src]

The page draws it under the OpenStreetMap tiles, so the map shows where things are even when
the tiles are missing (offline, area never viewed): borders, the German states, larger cities,
rivers and lakes. That is what the area pickers of the scene and road-graph tasks need.
Natural Earth is in the public domain (naturalearthdata.com/about/terms-of-use), so the result
is committed (webmap/vendor/basemap/germany.json); the sources (~120 MB) are downloaded once
into --src and stay local. Run it again only to change what the map shows.
"""

import argparse
import json
import urllib.request
from pathlib import Path

from shapely.geometry import box, mapping, shape

from meshplay.config import PROJECT_ROOT

SOURCE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
FILES = {
    "country": "ne_10m_admin_0_countries.geojson",
    "state": "ne_10m_admin_1_states_provinces.geojson",
    "city": "ne_10m_populated_places.geojson",  # the full file has German names
    "river": "ne_10m_rivers_lake_centerlines.geojson",
    "lake": "ne_10m_lakes.geojson",
}
CLIP = box(3.0, 46.3, 17.5, 56.0)  # Germany with a strip of its neighbours
TOLERANCE = 0.004  # degrees, about 300 m: enough for an overview up to zoom 10
OUT = PROJECT_ROOT / "webmap" / "vendor" / "basemap" / "germany.json"


def load(src: Path, kind: str) -> list[dict]:
    path = src / FILES[kind]
    if not path.exists():
        src.mkdir(parents=True, exist_ok=True)
        print(f"downloading {FILES[kind]} ...")
        urllib.request.urlretrieve(SOURCE + FILES[kind], path)
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def rounded(geom: dict) -> dict:
    """Coordinates to 4 decimals (about 10 m): halves the file."""

    def r(c):
        return [round(c[0], 4), round(c[1], 4)] if isinstance(c[0], float) else [r(x) for x in c]

    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


def clipped(feature: dict, tolerance: float = TOLERANCE) -> dict | None:
    geom = shape(feature["geometry"])
    if not geom.intersects(CLIP):
        return None
    geom = geom.intersection(CLIP).simplify(tolerance, preserve_topology=True)
    return None if geom.is_empty else rounded(mapping(geom))


def feature(kind: str, geom: dict, **props) -> dict:
    return {"type": "Feature", "properties": {"k": kind, **props}, "geometry": geom}


def build(src: Path) -> dict:
    out = []
    for f in load(src, "country"):
        p = f["properties"]
        if g := clipped(f):
            out.append(feature("country", g, de=p["ADM0_A3"] == "DEU"))
    for f in load(src, "state"):
        p = f["properties"]
        if p["adm0_a3"] == "DEU" and (g := clipped(f)):
            out.append(feature("state", g, name=p.get("name_de") or p["name"]))
    for f in load(src, "lake"):
        if (g := clipped(f)) and shape(f["geometry"]).area > 0.002:
            out.append(feature("lake", g))
    for f in load(src, "river"):
        p = f["properties"]
        if p.get("featurecla") == "River" and (g := clipped(f)):
            out.append(feature("river", g, name=p.get("name_de") or p.get("name") or ""))
    for f in load(src, "city"):
        p = f["properties"]
        if p["ADM0_A3"] != "DEU":
            continue
        lon, lat = f["geometry"]["coordinates"]
        geom = {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]}
        out.append(feature("city", geom, name=p.get("NAME_DE") or p["NAME"], pop=p["POP_MAX"]))
    return {
        "type": "FeatureCollection",
        "attribution": "Natural Earth (public domain)",
        "features": out,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, default=PROJECT_ROOT / "data" / "basemap_src")
    args = parser.parse_args()
    data = build(args.src)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kinds = {}
    for f in data["features"]:
        kinds[f["properties"]["k"]] = kinds.get(f["properties"]["k"], 0) + 1
    print(f"{OUT.relative_to(PROJECT_ROOT)}: {OUT.stat().st_size // 1024} KB, {kinds}")


if __name__ == "__main__":
    main()
