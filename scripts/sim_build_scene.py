"""Build a named 3D scene (terrain, surface, buildings, vegetation) from NRW laser-scan tiles.

python scripts/sim_build_scene.py [--name default] [--radius 1500 | --size 3000 --center LAT,LON
    | --bbox XMIN YMIN XMAX YMAX] [--laz DIR] [--activate] [--force]
python scripts/sim_build_scene.py --name NAME --import <folder with scene_raw.npz, ...>
python scripts/sim_build_scene.py --list | --use NAME

Writes data/sim/scenes/<name>/ and makes it the active scene if there was none (or with
--activate). Get the tiles first with scripts/sim_fetch_tiles.py, or let the map app do both
(layer "Laserscan-Szene"). A 3 x 3 km scene at 1 m takes about a minute and ~1 GB of RAM.

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
from meshplay.sim import scenes
from meshplay.sim.scene import Scene, tiles_for_bbox
from meshplay.sim.sites import to_utm

FILES = ("scene_raw.npz", "scene_cls2.npz", "scene_meta.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default=scenes.LEGACY_NAME, help="scene name (default: default)")
    parser.add_argument("--radius", type=float, default=1500, help="m around MESHPLAY_HOME")
    parser.add_argument("--size", type=float, help="edge length in m (with --center or home)")
    parser.add_argument("--center", help="'lat,lon' instead of MESHPLAY_HOME")
    parser.add_argument("--bbox", type=float, nargs=4, help="EPSG:25832 bounding box instead")
    parser.add_argument("--laz", type=Path, help="tile folder (default data/sim/laz)")
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
        laz_dir = args.laz or sim_dir / "laz"
        tiles = tiles_for_bbox(bbox)
        missing = [t for t in tiles if not (laz_dir / t).exists()]
        if len(missing) == len(tiles):
            raise SystemExit(
                f"No laser-scan tiles in {laz_dir}. Download them first: "
                "python scripts/sim_fetch_tiles.py --download (README, section '3D laser-scan "
                "data')."
            )
        if missing:
            print(f"Warning: {len(missing)} tiles missing in {laz_dir} (interpolated there):")
            for t in missing:
                print("  ", t)

        t0 = time.time()

        def on_tile(tile: str, k: int, n: int) -> None:
            print(f"reading tile {k}/{n} {tile}  {time.time() - t0:.0f} s", flush=True)

        scene = Scene.build(laz_dir, bbox, args.res, on_tile=on_tile)
        print("classifying and saving ...", flush=True)
        info = dict(
            name=name,
            source="nrw",
            created=datetime.now().isoformat(timespec="seconds"),
            tiles=len(tiles) - len(missing),
            measured=round(float(scene.measured.mean()), 3),
        )
        if center:
            info["center"] = [round(center[0], 6), round(center[1], 6)]
        scene.save(out, info)
        print(
            f"Scene {name}: {scene.shape[1]} x {scene.shape[0]} cells, {scene.n_points:,} points, "
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


if __name__ == "__main__":
    main()
