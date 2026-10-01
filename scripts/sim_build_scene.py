"""Build a named 3D scene (terrain, surface, buildings, vegetation) from a state's elevation data.

python scripts/sim_build_scene.py [--name default] [--radius 1500 | --size 3000 --center LAT,LON
    | --bbox XMIN YMIN XMAX YMAX] [--source auto|nrw|ni|sh] [--download] [--no-osm]
    [--tiles DIR] [--activate] [--force]
python scripts/sim_build_scene.py --name NAME --import <folder with scene_raw.npz, ...>
python scripts/sim_build_scene.py --list | --use NAME

The source follows from the state of the centre (meshplay.sim.sources): North Rhine-Westphalia
(laser-scan points), Lower Saxony and Schleswig-Holstein (1 m terrain and surface rasters; their
buildings come from OpenStreetMap footprints, unless --no-osm). --download fetches the missing
tiles first through the source's documented interface (resumable); otherwise the tiles must be
in data/sim/laz/ (NRW) or data/sim/tiles/<source>/. Writes data/sim/scenes/<name>/ and makes
it the active scene if there was none (or with --activate). The map app runs this script for
its scene task. A 3 x 3 km scene at 1 m takes about a minute and ~1 GB of RAM.

Note: the Mesh Bonn export (2026-09-20) was built interactively; its gap filling can't be
reproduced exactly. Rebuilding gives the same raw rasters and classification method, but
terrain under buildings may differ by a few metres; the predictions shift accordingly.
"""

import argparse
import shutil
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from meshplay import load_settings
from meshplay.sim import scenes, sources
from meshplay.sim.lidar import download_url
from meshplay.sim.sites import to_lonlat, to_utm

FILES = ("scene_raw.npz", "scene_cls2.npz", "scene_meta.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default=scenes.LEGACY_NAME, help="scene name (default: default)")
    parser.add_argument("--radius", type=float, default=1500, help="m around MESHPLAY_HOME")
    parser.add_argument("--size", type=float, help="edge length in m (with --center or home)")
    parser.add_argument("--center", help="'lat,lon' instead of MESHPLAY_HOME")
    parser.add_argument("--bbox", type=float, nargs=4, help="EPSG:25832 bounding box instead")
    parser.add_argument(
        "--source", default="auto", help="auto (by the centre's state), nrw, ni, sh"
    )
    parser.add_argument("--download", action="store_true", help="fetch missing tiles first")
    parser.add_argument("--no-osm", action="store_true", help="no OpenStreetMap footprints")
    parser.add_argument("--tiles", "--laz", type=Path, help="tile folder (default per source)")
    parser.add_argument("--res", type=float, default=1.0, help="cell size in m")
    parser.add_argument("--import", dest="import_dir", type=Path, help="copy an existing scene")
    parser.add_argument("--activate", action="store_true", help="make it the active scene")
    parser.add_argument("--force", action="store_true", help="overwrite an existing scene")
    parser.add_argument("--list", action="store_true", help="list the scenes and stop")
    parser.add_argument("--use", metavar="NAME", help="make NAME the active scene and stop")
    args = parser.parse_args()

    settings = load_settings()
    sim_dir = settings.data_dir / "sim"
    scenes.migrate_legacy(sim_dir)
    if args.list:
        active = scenes.active_name(sim_dir)
        for name in scenes.names(sim_dir):
            b = scenes.meta(sim_dir, name)["bbox"]
            mark = "*" if name == active else " "
            print(f"{mark} {name:<20} {(b[2] - b[0]) / 1000:.1f} x {(b[3] - b[1]) / 1000:.1f} km")
        return
    if args.use:
        scenes.set_active(sim_dir, args.use)
        print(f"Active scene: {args.use}")
        return

    name = scenes.check_name(args.name)
    out = scenes.scenes_dir(sim_dir) / name
    if out.exists() and any(out.iterdir()) and not args.force:
        raise SystemExit(f"{out} exists; use --force to replace it.")
    activate = args.activate or not scenes.active_name(sim_dir)

    if args.import_dir:
        out.mkdir(parents=True, exist_ok=True)
        for f in FILES:
            shutil.copy2(args.import_dir / f, out / f)
        print(f"Imported scene from {args.import_dir} as {name}")
    else:
        center = None
        if args.bbox:
            bbox = tuple(args.bbox)
        else:
            if args.center:
                center = scenes.parse_center(args.center)
            elif settings.home:
                center = settings.home
            else:
                raise SystemExit("Set MESHPLAY_HOME in .env or pass --center or --bbox.")
            x, y = to_utm(center[1], center[0])
            bbox = scenes.bbox_around(x, y, args.size or 2 * args.radius)
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
        tiles_dir = args.tiles or source.tiles_dir(sim_dir)
        print(f"Source: {source.state}, {source.product} ({source.attribution})", flush=True)
        tiles = get_tiles(source, bbox, tiles_dir, args.download)
        missing = [t for t in tiles if not t.present]
        if len(missing) == len(tiles):
            raise SystemExit(
                f"No tiles for this area in {tiles_dir}. Run with --download, or download them "
                f"by hand: {source.portal}"
            )
        if missing:
            print(f"Warning: {len(missing)} of {len(tiles)} tiles missing (interpolated there)")

        t0 = time.time()

        def on_tile(tile: str, k: int, n: int) -> None:
            print(f"reading tile {k}/{n} {tile}  {time.time() - t0:.0f} s", flush=True)

        buildings, how = None, "points"
        if source.kind == "raster":
            buildings, how = (None, "surface") if args.no_osm else footprints(bbox, args.res)
        scene = source.build(bbox, tiles_dir, args.res, on_file=on_tile, buildings=buildings)
        print("classifying and saving ...", flush=True)
        info = dict(
            name=name,
            source=source.id,
            attribution=source.attribution,
            buildings=how,
            created=datetime.now().isoformat(timespec="seconds"),
            tiles=len(tiles) - len(missing),
            measured=round(float(scene.measured.mean()), 3),
        )
        if center:
            info["center"] = [round(center[0], 6), round(center[1], 6)]
        scene.save(out, info)
        points = f"{scene.n_points:,} points, " if hasattr(scene, "n_points") else ""
        print(
            f"Scene {name}: {scene.shape[1]} x {scene.shape[0]} cells, {points}"
            f"{scene.measured.mean():.0%} measured, {scene.bld.sum() * args.res**2 / 1e4:.1f} ha "
            f"buildings, {scene.veg.sum() * args.res**2 / 1e4:.1f} ha vegetation, "
            f"{time.time() - t0:.0f} s -> {out}"
        )
        if not np.isfinite(scene.dtm).all():
            print(
                "Warning: some cells have no terrain height (large gaps); paths through them fail."
            )
    if activate:
        scenes.set_active(sim_dir, name)
        print(f"Active scene: {name}")


def get_tiles(source, bbox, tiles_dir: Path, download: bool):
    """The tiles of the area; with download, the missing ones fetched first (resumable)."""
    if not download:
        return source.tiles_offline(bbox, tiles_dir)
    try:
        tiles = source.tiles(bbox, tiles_dir)
    except OSError as e:
        print(f"Warning: tile index not reachable ({e}); using the tiles already here")
        return source.tiles_offline(bbox, tiles_dir)
    todo = [f for t in tiles for f in t.files if not f.present and f.url]
    total = sum(f.mb for f in todo)
    for k, f in enumerate(todo, 1):
        print(
            f"downloading file {k}/{len(todo)} {f.name} (~{f.mb:.0f} of {total:.0f} MB)", flush=True
        )
        shown = [-1]

        def progress(done: int, size: int, k=k, shown=shown) -> None:
            pct = int(100 * done / size) if size else 0
            if pct // 10 != shown[0]:
                shown[0] = pct // 10
                print(f"download {k}/{len(todo)} {pct} %", flush=True)

        download_url(f.url, f.path, progress, timeout=120)
    return tiles


def footprints(bbox, res: float):
    """(building mask, "osm") from OpenStreetMap, or (None, "surface") when unreachable."""
    from meshplay.sim.sources.raster import fetch_buildings, rasterize_rings

    print("fetching building footprints from OpenStreetMap ...", flush=True)
    try:
        rings = fetch_buildings(bbox)
    except OSError as e:
        print(f"Warning: OpenStreetMap not reachable ({e}); buildings estimated from the surface")
        return None, "surface"
    print(f"  {len(rings)} buildings", flush=True)
    shape = (round((bbox[3] - bbox[1]) / res), round((bbox[2] - bbox[0]) / res))
    return rasterize_rings(rings, bbox, res, shape), "osm"


if __name__ == "__main__":
    main()
