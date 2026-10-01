"""List (and optionally download) the NRW laser-scan tiles around home or a bounding box.

python scripts/sim_fetch_tiles.py [--radius 1500] [--bbox XMIN YMIN XMAX YMAX] [--download]

Tiles are 1 km x 1 km in UTM32 (EPSG:25832), about 60-130 MB each, from Geobasis NRW
(3D-Messdaten Laserscanning, open data, dl-de/zero-2-0). Without --download the script only
prints the list with sizes and marks tiles already present in data/sim/laz/. Tiles the server
doesn't have (outside North Rhine-Westphalia) are listed as "not available" and skipped. An
interrupted download continues where it stopped. It depends on the server's current folder and
file names (not a documented interface); if it fails, download by hand as the README (section
"3D laser-scan data") explains. The map app never downloads; it lists the tiles it needs.
"""

import argparse
import urllib.error

from meshplay import load_settings
from meshplay.sim.lidar import download_tile, remote_size
from meshplay.sim.scene import TILE_URL, tiles_for_bbox
from meshplay.sim.sites import to_utm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--radius", type=float, default=1500, help="m around MESHPLAY_HOME")
    parser.add_argument("--bbox", type=float, nargs=4, help="EPSG:25832 bounding box instead")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    laz_dir = settings.data_dir / "sim" / "laz"
    if args.bbox:
        bbox = args.bbox
    else:
        if not settings.home:
            raise SystemExit("Set MESHPLAY_HOME in .env or pass --bbox.")
        x, y = to_utm(settings.home[1], settings.home[0])
        bbox = (x - args.radius, y - args.radius, x + args.radius, y + args.radius)

    names = tiles_for_bbox(bbox)
    total, available = 0, []
    for name in names:
        have = (laz_dir / name).exists()
        try:
            size = remote_size(name)
        except (urllib.error.URLError, TimeoutError) as e:
            raise SystemExit(
                f"Can't reach {TILE_URL}: {e}. Check the connection, or download the tiles by "
                "hand (README, section '3D laser-scan data')."
            ) from None
        if size is None:
            print(f"{name}  not available on the server (outside NRW?)")
            continue
        available.append(name)
        total += 0 if have else size
        print(f"{name}  {size / 1e6:6.1f} MB  {'present' if have else ''}")
    print(
        f"{len(available)} of {len(names)} tiles available, {total / 1e6:.0f} MB to download "
        f"into {laz_dir}"
    )
    if not available:
        raise SystemExit(
            "No tiles for this area: the NRW laser scan only covers North Rhine-Westphalia."
        )

    if args.download:
        for name in available:
            if (laz_dir / name).exists():
                continue

            def show(done: int, total: int, name=name) -> None:
                print(f"  {name}  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="\r", flush=True)

            download_tile(name, laz_dir, show)
            print()
        print("done.")


if __name__ == "__main__":
    main()
