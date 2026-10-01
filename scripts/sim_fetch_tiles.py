"""List (and optionally download) the elevation tiles around home, a centre or a bounding box.

python scripts/sim_fetch_tiles.py [--radius 1500 | --center LAT,LON | --bbox XMIN YMIN XMAX YMAX]
    [--source auto|nrw|ni|sh] [--download]

The source follows from the state of the area's centre (meshplay.sim.sources): North
Rhine-Westphalia (laser-scan tiles, 60-130 MB each), Lower Saxony (DGM1 and DOM1, ~4 MB per
file), Schleswig-Holstein (DGM1 and bDOM, ~27 and ~105 MB). Without --download the script only
prints the files with their links and marks those already present; with it, it fetches the
missing ones through the source's documented interface and continues a leftover *.part file.
scripts/sim_build_scene.py --download does the same and builds the scene right after; the map
app's scene form too.
"""

import argparse
import urllib.error

from meshplay import load_settings
from meshplay.sim import scenes, sources
from meshplay.sim.lidar import download_url
from meshplay.sim.sites import to_lonlat, to_utm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--radius", type=float, default=1500, help="m around the centre")
    parser.add_argument("--center", help="'lat,lon' instead of MESHPLAY_HOME")
    parser.add_argument("--bbox", type=float, nargs=4, help="EPSG:25832 bounding box instead")
    parser.add_argument(
        "--source", default="auto", help="auto (by the centre's state), nrw, ni, sh"
    )
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if args.bbox:
        bbox = tuple(args.bbox)
    else:
        center = scenes.parse_center(args.center) if args.center else settings.home
        if not center:
            raise SystemExit("Set MESHPLAY_HOME in .env or pass --center or --bbox.")
        x, y = to_utm(center[1], center[0])
        bbox = scenes.bbox_around(x, y, 2 * args.radius)
    if args.source == "auto":
        lon, lat = to_lonlat((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        source, state = sources.for_point(lat, lon)
        if source is None:
            raise SystemExit(
                f"No elevation source for {state or 'this place (outside Germany)'} yet; "
                f"supported: {', '.join(s.state for s in sources.SOURCES)}."
            )
    else:
        source = sources.by_id(args.source)
    tiles_dir = source.tiles_dir(settings.data_dir / "sim")
    print(f"Source: {source.state}, {source.product} ({source.licence}, {source.attribution})")
    try:
        tiles = source.tiles(bbox, tiles_dir)
    except OSError as e:
        raise SystemExit(
            f"Tile index not reachable ({e}). Check the connection, or download by hand: "
            f"{source.portal}"
        ) from None

    todo = []
    for t in tiles:
        if not t.files:
            print(
                f"tile {t.key[0]}_{t.key[1]}  not offered by the source (outside {source.state}?)"
            )
        for f in t.files:
            print(f"{f.name}  ~{f.mb:.0f} MB  {'present' if f.present else f.url}")
            if not f.present:
                todo.append(f)
    print(f"{len(todo)} files, about {sum(f.mb for f in todo):.0f} MB to download into {tiles_dir}")

    if args.download:
        for f in todo:

            def show(done: int, total: int, name=f.name) -> None:
                print(f"  {name}  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="\r", flush=True)

            try:
                download_url(f.url, f.path, show, timeout=120)
            except urllib.error.HTTPError as e:
                print(f"  {f.name}: HTTP {e.code}, skipped (not on the server?)")
                continue
            print()
        print("done.")


if __name__ == "__main__":
    main()
