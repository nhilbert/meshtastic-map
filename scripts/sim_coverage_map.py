"""Predicted coverage around a site, as map layers per model family.

python scripts/sim_coverage_map.py [--site HOME] [--radius 800] [--step 25] [--preset ShortSlow]
    [--walk data/sim/compare/walk-<id>-<date>.csv] [--open]

For each grid cell the models predict the delivery probability of a single packet from a
walker (T1000-E, street level) to the site. Each model family becomes a layer, so you can switch
between them and see where they disagree. --walk overlays the scored packets from
sim_compare_walk.py for a direct visual comparison.

Writes data/sim/maps/coverage-<site>-<preset>-<placement>-<radius>m-<step>m[-winter].html and
.npz (the grid, with the setup as "meta" so the map app can label it). 25 m cells over 800 m
radius take 10-15 minutes.
The map app starts this script as a background task (Aufgaben -> Abdeckung simulieren).
"""

import argparse
import base64
import csv
import io
import json
import time

import branca.colormap as cm
import folium
import numpy as np

from meshplay import load_settings
from meshplay.config import DEFAULT_PRESET
from meshplay.sim.predictor import LinkSetup, Predictor
from meshplay.sim.scene import Scene
from meshplay.sim.sites import load_config, to_utm
from meshplay.walk import serve

INDOOR = {"none": None, "open": "open", "trad": "trad", "lowe": "lowe"}
LAYERS = [
    "ENS",
    "M1_P1812",
    "M6_P1812_P833",
    "M1b_P1812_clut",
    "M2_P1812_bare",
    "M3_Bullington",
    "M5_LogDist",
]
COLORS = ["#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850"]


def png_data_url(rgba: np.ndarray) -> str:
    """Encode an RGBA uint8 array as a PNG data URL (no extra dependency)."""
    import struct
    import zlib

    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    buf = io.BytesIO()
    buf.write(b"\x89PNG\r\n\x1a\n")
    buf.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
    buf.write(chunk(b"IDAT", zlib.compress(raw, 9)))
    buf.write(chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", help="site name in sites.json (default: first site)")
    parser.add_argument("--site-indoor", choices=INDOOR, default="open")
    parser.add_argument("--radius", type=float, default=800)
    parser.add_argument("--step", type=float, default=25)
    parser.add_argument("--preset", default=DEFAULT_PRESET)
    parser.add_argument("--draws", type=int, default=600)
    parser.add_argument("--leafless", action="store_true")
    parser.add_argument("--walk", help="CSV from sim_compare_walk.py to overlay")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    sim_dir = load_settings().data_dir / "sim"
    cfg = load_config(sim_dir / "sites.json")
    name = args.site or next(iter(cfg["sites"]))
    site = cfg["sites"][name]
    scene = Scene.load(sim_dir / "scene")
    setup = LinkSetup(
        preset=args.preset,
        site_indoor=INDOOR[args.site_indoor],
        draws=args.draws,
        cheap_grid=3,
        leaf="unbelaubt" if args.leafless else "belaubt",
    )
    predictor = Predictor(scene, site, setup, np.random.default_rng(7))

    # Grid in lat/lon (not UTM, which is rotated ~1.5 degrees here) so the image overlay lines up.
    dlat = args.step / 111_320
    dlon = args.step / (111_320 * np.cos(np.radians(site["lat"])))
    k = int(np.ceil(args.radius / args.step))
    lats = site["lat"] + np.arange(k, -k - 1, -1) * dlat  # image rows run north -> south
    lons = site["lon"] + np.arange(-k, k + 1) * dlon
    grid = {m: np.full((len(lats), len(lons)), np.nan) for m in LAYERS}
    sx, sy = site["utm"]
    t0, done = time.time(), 0
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            x, y = to_utm(lon, lat)
            if (x - sx) ** 2 + (y - sy) ** 2 > args.radius**2:
                continue
            pred = predictor.at(x, y)
            done += 1
            if pred is None:
                continue
            for m in LAYERS:
                if m in pred:
                    grid[m][i, j] = pred[m]["p_rx"]
        print(f"  row {i + 1}/{len(lats)}  {done} cells  {time.time() - t0:.0f} s", end="\r")
    print()

    out_dir = sim_dir / "maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    # all parameters that change the result, so different runs don't overwrite each other
    stem = f"coverage-{name}-{args.preset}-{args.site_indoor}-{args.radius:g}m-{args.step:g}m" + (
        "-winter" if args.leafless else ""
    )
    meta = dict(
        site=name,
        site_indoor=args.site_indoor,
        preset=args.preset,
        radius=args.radius,
        step=args.step,
        draws=args.draws,
        leaf="unbelaubt" if args.leafless else "belaubt",
        created=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    grid_path = out_dir / f"{stem}.npz"
    np.savez_compressed(grid_path, lats=lats, lons=lons, meta=np.array(json.dumps(meta)), **grid)
    print(f"Grid: {grid_path}")

    cmap = cm.LinearColormap(COLORS, vmin=0, vmax=1, caption="predicted P(packet received)")
    lat0, lat1 = lats[-1] - dlat / 2, lats[0] + dlat / 2
    lon0, lon1 = lons[0] - dlon / 2, lons[-1] + dlon / 2
    fmap = folium.Map(location=(site["lat"], site["lon"]), zoom_start=15)
    for k, m in enumerate(LAYERS):
        g = grid[m]
        rgba = np.zeros((*g.shape, 4), np.uint8)
        ok = np.isfinite(g)
        for idx in zip(*np.nonzero(ok), strict=True):
            c = cmap(float(g[idx])).lstrip("#")[:6]
            rgba[idx] = [int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 150]
        folium.raster_layers.ImageOverlay(
            png_data_url(rgba),
            bounds=[[lat0, lon0], [lat1, lon1]],
            name=m,
            show=(k == 0),
            overlay=True,
            interactive=False,
        ).add_to(fmap)
    folium.Marker(
        (site["lat"], site["lon"]), tooltip=name, icon=folium.Icon(color="blue", icon="home")
    ).add_to(fmap)
    if args.walk:
        layer = folium.FeatureGroup(name="walk: received packets", show=True)
        with open(args.walk, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["kind"] == "slot":
                    color = "#000000" if r["received"] == "True" else "#ffffff"
                    folium.RegularPolygonMarker(
                        (float(r["lat"]), float(r["lon"])),
                        number_of_sides=4,
                        radius=4,
                        color=color,
                        weight=2,
                        fill=False,
                    ).add_to(layer)
                else:
                    folium.CircleMarker(
                        (float(r["lat"]), float(r["lon"])),
                        radius=4,
                        color="#000000",
                        fill=True,
                        fill_color="#000000",
                        tooltip=f"{r['signal_obs']} dBm",
                    ).add_to(layer)
        layer.add_to(fmap)
    cmap.add_to(fmap)
    folium.LayerControl(collapsed=False).add_to(fmap)
    path = out_dir / f"{stem}.html"
    fmap.save(path)
    print(f"Map: {path}")
    if args.open:
        serve(out_dir, path.name)


if __name__ == "__main__":
    main()
