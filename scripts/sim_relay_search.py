"""Search roofs for a relay site between two sites (the weaker leg decides).

python scripts/sim_relay_search.py --a HOME --b FAR [--min-roof 12] [--grid 8] [--scene NAME]
    [--open]

Every grid-th building cell with a roof at least --min-roof m high is a candidate; the antenna
sits --mast m above the roof. For each candidate the basic transmission loss to both sites is
computed with ITU-R P.1812-6 over a coarse LiDAR profile (M1 only, no Monte Carlo) and turned
into a received level with a fixed nominal budget. Ranking by the weaker leg: a relay is only as
good as its worse link. Ported from 11_Simulation_ITU/site/.

Writes data/sim/relay-<a>-<b>.json (top 400) and .html (top 15 on a map).
"""

import argparse
import json
import time

import folium
import numpy as np

from meshplay import load_settings
from meshplay.sim.p1812 import tl_p1812
from meshplay.sim.scene import Scene
from meshplay.sim.scenes import load_for_script
from meshplay.sim.sites import load_config, to_lonlat
from meshplay.walk import serve

PTX_DBM, G_ANT_DBI, L_FEED_DB = 21.0, 0.0, 1.0  # nominal budget of the original search


def lb(scene: Scene, a, ha, b, hb) -> float:
    _, d, g, r = scene.quick_profile(a, b)
    r = r.copy()
    r[0] = r[-1] = 0.0
    return float(
        tl_p1812(
            0.868,
            50.0,
            d,
            g,
            r,
            None,
            None,
            htg=max(ha, 1.0),
            hrg=max(hb, 1.0),
            pol=2,
            phi_path=50.74,
        )
    )


def prx(loss_db: float) -> float:
    return PTX_DBM + 2 * G_ANT_DBI - L_FEED_DB - loss_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--a", required=True, help="site name in sites.json")
    parser.add_argument("--b", required=True, help="site name in sites.json")
    parser.add_argument("--min-roof", type=float, default=12.0, help="minimum roof height [m]")
    parser.add_argument("--mast", type=float, default=2.0, help="antenna above roof [m]")
    parser.add_argument("--grid", type=int, default=8, help="candidate spacing in cells")
    parser.add_argument(
        "--cutoff", type=float, default=-132.0, help="skip leg b if leg a is below this level [dBm]"
    )
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--scene", help="scene name (default: the active scene)")
    args = parser.parse_args()

    sim_dir = load_settings().data_dir / "sim"
    cfg = load_config(sim_dir / "sites.json")
    _, scene = load_for_script(sim_dir, args.scene)
    sa, sb = cfg["sites"][args.a], cfg["sites"][args.b]
    ha, hb = float(np.mean(sa["height_m"])), float(np.mean(sb["height_m"]))
    ny, nx = scene.shape
    x0, y0 = scene.bbox[0], scene.bbox[1]
    cand = [
        (x0 + (j + 0.5) * scene.res, y0 + (i + 0.5) * scene.res, float(scene.nd[i, j]))
        for i in range(0, ny, args.grid)
        for j in range(0, nx, args.grid)
        if scene.bld[i, j] and scene.nd[i, j] >= args.min_roof and scene.measured[i, j]
    ]
    print(f"Candidates on roofs >= {args.min_roof:.0f} m: {len(cand)}", flush=True)

    t0, out = time.time(), []
    for k, (x, y, h) in enumerate(cand):
        da = np.hypot(x - sa["utm"][0], y - sa["utm"][1])
        db = np.hypot(x - sb["utm"][0], y - sb["utm"][1])
        if da < 40 or db < 40:
            continue
        la = lb(scene, (x, y), h + args.mast, sa["utm"], ha)
        if prx(la) < args.cutoff:
            continue  # leg a hopeless, save leg b
        lbb = lb(scene, (x, y), h + args.mast, sb["utm"], hb)
        lon, lat = to_lonlat(x, y)
        out.append(
            dict(
                lat=lat,
                lon=lon,
                x=x,
                y=y,
                roof_m=h,
                prx_a=prx(la),
                prx_b=prx(lbb),
                worst=min(prx(la), prx(lbb)),
                d_a=da,
                d_b=db,
            )
        )
        if k % 2000 == 0:
            print(f"  {k}/{len(cand)}  {time.time() - t0:.0f} s", flush=True)
    out.sort(key=lambda r: -r["worst"])
    print(f"Scored {len(out)} in {time.time() - t0:.0f} s")

    stem = sim_dir / f"relay-{args.a}-{args.b}"
    stem.with_suffix(".json").write_text(json.dumps(out[:400], indent=1), encoding="utf-8")
    print(f"\nTop 15 (nominal budget {PTX_DBM:.0f} dBm, {G_ANT_DBI:.0f} dBi, feed {L_FEED_DB} dB):")
    print(f"   Prx_{args.a}  Prx_{args.b}  weaker   roof    d_{args.a}  d_{args.b}   lat, lon")
    fmap = folium.Map(location=(sa["lat"], sa["lon"]), zoom_start=15)
    for name, s in ((args.a, sa), (args.b, sb)):
        folium.Marker((s["lat"], s["lon"]), tooltip=name).add_to(fmap)
    for rank, r in enumerate(out[:15], 1):
        print(
            f"  {r['prx_a']:7.1f} {r['prx_b']:8.1f} {r['worst']:8.1f} {r['roof_m']:6.1f} m "
            f"{r['d_a']:7.0f} {r['d_b']:7.0f}   {r['lat']:.6f}, {r['lon']:.6f}"
        )
        folium.CircleMarker(
            (r["lat"], r["lon"]),
            radius=6,
            color="#7b3294",
            fill=True,
            tooltip=f"#{rank}: weaker leg {r['worst']:.1f} dBm, roof {r['roof_m']:.0f} m",
        ).add_to(fmap)
    fmap.save(stem.with_suffix(".html"))
    print(f"\nWritten: {stem}.json / .html")
    if args.open:
        serve(sim_dir, stem.with_suffix(".html").name)


if __name__ == "__main__":
    main()
