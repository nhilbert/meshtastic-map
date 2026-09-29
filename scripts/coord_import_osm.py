"""Build the road graph of the coordination mode from an OSM file instead of the Overpass API.

python scripts/coord_import_osm.py <extract.osm | overpass.json> [--name roads]

Takes an .osm XML file (JOSM export, Overpass XML, a small extract cut from a regional file
with osmium: `osmium extract -b W,S,E,N region.osm.pbf -o city.osm`) or a saved Overpass
JSON answer, and writes data/osm/<name>.json.gz like the map app's task "Straßennetz laden".
"""

import argparse
import json
from pathlib import Path

from meshplay.config import load_settings
from meshplay.mapapp.coord.osm import (
    build_graph,
    ways_from_osm_xml,
    ways_from_overpass,
    write_graph,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help=".osm XML or Overpass JSON")
    parser.add_argument("--name", default="roads", help="file stem under data/osm/")
    args = parser.parse_args()
    if args.source.suffix.lower() == ".json":
        with args.source.open("rb") as f:
            ways = list(ways_from_overpass(json.load(f)))
    else:
        ways = list(ways_from_osm_xml(args.source))
    if not ways:
        raise SystemExit(f"{args.source}: no highway ways found")
    lats = [p[0] for w in ways for p in w[2]]
    lons = [p[1] for w in ways for p in w[2]]
    graph = build_graph(ways, (min(lats), min(lons), max(lats), max(lons)), source=args.source.name)
    out = load_settings().data_dir / "osm" / f"{args.name}.json.gz"
    write_graph(graph, out)
    print(
        f"{graph['n_ways']} ways, {len(graph['nodes'])} nodes, {len(graph['edges'])} edges -> {out}"
    )


if __name__ == "__main__":
    main()
