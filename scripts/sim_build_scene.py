"""Build the 3D scene (terrain, surface, buildings, vegetation) from NRW laser-scan tiles.

python scripts/sim_build_scene.py [--radius 1500 | --bbox XMIN YMIN XMAX YMAX] [--laz DIR]
python scripts/sim_build_scene.py --import <folder with scene_raw.npz, scene_cls2.npz, ...>

Writes data/sim/scene/. Get the tiles first with scripts/sim_fetch_tiles.py. A 3 x 3 km scene
at 1 m takes about a minute and ~1 GB of RAM.

Note: the Mesh Bonn export (2026-09-20) was built interactively; its gap filling can't be
reproduced exactly. Rebuilding gives the same raw rasters and classification method, but
terrain under buildings may differ by a few metres; the predictions shift accordingly.
"""

import argparse
import shutil
import time
from pathlib import Path

import numpy as np

from meshplay import load_settings
from meshplay.sim.scene import Scene, tiles_for_bbox
from meshplay.sim.sites import to_utm

FILES = ("scene_raw.npz", "scene_cls2.npz", "scene_meta.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--radius", type=float, default=1500, help="m around MESHPLAY_HOME")
    parser.add_argument("--bbox", type=float, nargs=4, help="EPSG:25832 bounding box instead")
    parser.add_argument("--laz", type=Path, help="tile folder (default data/sim/laz)")
    parser.add_argument("--res", type=float, default=1.0, help="cell size in m")
    parser.add_argument("--import", dest="import_dir", type=Path, help="copy an existing scene")
    parser.add_argument("--force", action="store_true", help="overwrite an existing scene")
    args = parser.parse_args()

    settings = load_settings()
    out = settings.data_dir / "sim" / "scene"
    if out.exists() and any(out.iterdir()) and not args.force:
        raise SystemExit(f"{out} exists; use --force to replace it.")

    if args.import_dir:
        out.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            shutil.copy2(args.import_dir / name, out / name)
        print(f"Imported scene from {args.import_dir}")
        return

    if args.bbox:
        bbox = tuple(args.bbox)
    else:
        if not settings.home:
            raise SystemExit("Set MESHPLAY_HOME in .env or pass --bbox.")
        x, y = to_utm(settings.home[1], settings.home[0])
        r = args.radius
        bbox = (round(x - r), round(y - r), round(x + r), round(y + r))
    laz_dir = args.laz or settings.data_dir / "sim" / "laz"
    tiles = tiles_for_bbox(bbox)
    missing = [t for t in tiles if not (laz_dir / t).exists()]
    if len(missing) == len(tiles):
        raise SystemExit(
            f"No laser-scan tiles in {laz_dir}. Download them first: "
            "python scripts/sim_fetch_tiles.py --download (README, section '3D laser-scan data')."
        )
    if missing:
        print(f"Warning: {len(missing)} tiles missing in {laz_dir} (those areas get interpolated):")
        for t in missing:
            print("  ", t)

    t0 = time.time()
    scene = Scene.build(laz_dir, bbox, args.res)
    scene.save(out)
    print(
        f"Scene {scene.shape[1]} x {scene.shape[0]} cells, {scene.n_points:,} points, "
        f"{scene.measured.mean():.0%} measured, {scene.bld.sum() * args.res**2 / 1e4:.1f} ha "
        f"buildings, {scene.veg.sum() * args.res**2 / 1e4:.1f} ha vegetation, "
        f"{time.time() - t0:.0f} s -> {out}"
    )
    if not np.isfinite(scene.dtm).all():
        print("Warning: some cells have no terrain height (large gaps); paths through them fail.")


if __name__ == "__main__":
    main()
