"""Compare a coverage walk with the propagation models.

python scripts/sim_compare_walk.py --tracker !abcd1234 [--date 2026-09-29]
    [--gpx data/tracks/walk.gpx] [--home-site HOME] [--home-indoor open] [--preset P] [--open]
python scripts/sim_compare_walk.py --tracker !abcd1234 --probes --gpx data/tracks/walk.gpx ...

For every position packet the home node received directly (0 hops), the models predict the
per-packet signal power at that spot (home site -> tracker, T1000-E antenna prior, profile
through the LiDAR scene). The measured signal power (RSSI corrected by SNR, see
meshplay.sim.compare) is scored against each model family.

With --gpx, the walk is additionally cut into slots of --interval seconds; each slot counts as
received if a direct packet arrived in it. The models' predicted delivery probability at the
slot's position gives a Brier score and a calibration table.

With --probes, traceroutes from scripts/probe_walk.py take the place of position packets: each
answered probe is placed on the GPX track at its send time and its reply (tracker -> home) is
scored like a received packet; slots are the probe interval, so each holds one probe. The
interval and the preset are taken from the probe log unless given.

Writes data/sim/compare/walk-<tracker>-<date>.{md,csv,html} (probes-... with --probes).
"""

import argparse
import csv
import subprocess
from datetime import date
from pathlib import Path

import branca.colormap as cm
import folium
import numpy as np

from meshplay import load_settings
from meshplay.config import DEFAULT_PRESET
from meshplay.sim.compare import summarize
from meshplay.sim.predictor import LinkSetup, Predictor
from meshplay.sim.scene import Scene
from meshplay.sim.sites import load_config
from meshplay.sim.walkcompare import model_names, nearest_site, score_packets, score_slots
from meshplay.walk import (
    load_gpx,
    load_points,
    load_probes,
    most_common,
    parse_node,
    probe_points,
    serve,
    typical_interval_s,
)

INDOOR = {"none": None, "open": "open", "trad": "trad", "lowe": "lowe"}


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tracker", required=True, help="tracker node ID, e.g. !abcd1234")
    parser.add_argument("--date", default=date.today().isoformat(), help="log date (YYYY-MM-DD)")
    parser.add_argument("--log", help="packet log (default: data/packets/<date>.jsonl)")
    parser.add_argument("--gpx", help="phone GPS track of the walk, enables the delivery score")
    parser.add_argument(
        "--probes", action="store_true", help="score probe_walk.py traceroutes (needs --gpx)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="position or probe interval in s (default: from the probe log or the timestamps)",
    )
    parser.add_argument("--home-site", help="site name in sites.json (default: nearest to home)")
    parser.add_argument(
        "--home-indoor",
        choices=INDOOR,
        default="open",
        help="home node placement: none = antenna outside, open window, "
        "trad/lowe = closed window with old/low-E glazing",
    )
    parser.add_argument(
        "--preset",
        help="modem preset of the walk (default: from the probe log, else the mesh's preset)",
    )
    parser.add_argument(
        "--rx-height",
        type=float,
        nargs=2,
        default=(1.0, 1.6),
        help="tracker height above ground, range in m",
    )
    parser.add_argument(
        "--rx-clutter",
        type=float,
        default=12.0,
        help="typical building/tree height around the walker [m]",
    )
    parser.add_argument("--leafless", action="store_true", help="trees without leaves (winter)")
    parser.add_argument(
        "--raw-rssi", action="store_true", help="compare RSSI without SNR correction"
    )
    parser.add_argument("--draws", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--open", action="store_true", help="serve the map and open a browser")
    args = parser.parse_args()
    args.leaf = "unbelaubt" if args.leafless else "belaubt"
    if args.probes and not args.gpx:
        parser.error("--probes needs --gpx: probes carry no position")

    settings = load_settings()
    sim_dir = settings.data_dir / "sim"
    cfg = load_config(sim_dir / "sites.json")
    site_name = args.home_site or (
        nearest_site(cfg["sites"], *settings.home) if settings.home else next(iter(cfg["sites"]))
    )
    home = cfg["sites"][site_name]

    folder = "probes" if args.probes else "packets"
    log = Path(args.log) if args.log else settings.data_dir / folder / f"{args.date}.jsonl"
    if args.probes:
        probes = probe_points(load_probes(log, parse_node(args.tracker)), load_gpx(args.gpx))
        points = [p for p in probes if p["hops"] is not None]
        what = f"{len(probes)} probes, {len(points)} answered"
        args.interval = args.interval or typical_interval_s(probes) or 60
        args.preset = args.preset or most_common(p["preset"] for p in probes)
    else:
        points = load_points(log, parse_node(args.tracker))
        what = f"{len(points)} positions"
        args.interval = args.interval or typical_interval_s(points) or 30
    args.preset = args.preset or DEFAULT_PRESET
    direct = [p for p in points if p["hops"] == 0 and p["rssi"] is not None]
    print(
        f"Home site {site_name} ({args.home_indoor}), preset {args.preset}, "
        f"interval {args.interval:.0f} s, {what}, {len(direct)} direct"
    )
    if not points:
        raise SystemExit(f"Nothing from this tracker in {log}.")

    scene = Scene.load(sim_dir / "scene")
    setup = LinkSetup(
        rx_height_m=tuple(args.rx_height),
        rx_clutter_m=args.rx_clutter,
        preset=args.preset,
        site_indoor=INDOOR[args.home_indoor],
        leaf=args.leaf,
        draws=args.draws,
    )
    predictor = Predictor(scene, home, setup, np.random.default_rng(args.seed))

    rows, skipped = score_packets(direct, predictor, args.raw_rssi)
    slots = []
    if args.gpx:
        slots, skipped_slots = score_slots(load_gpx(args.gpx), direct, predictor, args.interval)
        skipped += skipped_slots

    all_rows = rows + slots
    if not all_rows:
        raise SystemExit(
            "Nothing to compare: all positions are outside the scene or too close to home."
        )
    names = model_names(all_rows)
    summary = summarize(all_rows, names)

    out_dir = sim_dir / "compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = "probes" if args.probes else "walk"
    stem = out_dir / f"{kind}-{args.tracker.lstrip('!')}-{args.date}"
    level = "raw RSSI" if args.raw_rssi else "RSSI corrected by SNR"
    lines = [
        f"# Walk {args.date}: tracker {args.tracker} vs. models",
        "",
        f"Home site {site_name}, placement `{args.home_indoor}`, preset {args.preset}, "
        f"tracker height {args.rx_height[0]}-{args.rx_height[1]} m, clutter {args.rx_clutter} m, "
        f"trees {'bare' if args.leafless else 'in leaf'}. {args.draws} draws, seed {args.seed}, "
        f"code {git_commit()}. Observed level: {level}.",
        "",
        f"{len(rows)} direct {'probe replies' if args.probes else 'packets'} scored, "
        f"{len(slots)} delivery slots, {skipped} positions "
        "skipped (outside the scene or < 30 m from home).",
        "",
        "| Model | packets | bias dB (obs-pred) | MAE dB | in 80 % | Σ log score | weight "
        "| slots | Brier |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m, s in summary.items():
        weight = f"{s['weight']:.2f}" if "weight" in s else "–"
        lines.append(
            f"| {m} | {s['n_signal']} | {s['bias_db']:+.1f} | {s['mae_db']:.1f} | "
            f"{s['coverage80']:.0%} | {s['logscore']:+.1f} | {weight} | {s['n_delivery']} | "
            f"{s['brier']:.3f} |"
        )
    lines += ["", "Weights compare only models scored on all packets (M4 is limited to 660 m)."]
    if rows:
        low = sum(1 for r in rows if r["measured_share"] < 0.95)
        noise = [r["rssi"] - r["snr"] for r in rows if r["snr"] is not None and r["snr"] < 5]
        lines += [""]
        if noise:
            lines.append(
                f"Noise floor estimate (RSSI - SNR, packets with SNR < 5 dB): "
                f"{np.median(noise):.1f} dBm (n={len(noise)})."
            )
        if low:
            lines.append(f"{low} packets have paths crossing interpolated scene cells.")
        lines.append(
            "Received packets only: near the edge of coverage the observed levels are "
            "biased upward (weak packets are lost), so the bias there favours optimistic models."
        )
    if slots:
        lines += [
            "",
            "Calibration (ENS): predicted P(rx) vs. observed share",
            "",
            "| predicted | slots | observed |",
            "|---|---:|---:|",
        ]
        p = np.array([s["ENS__p_rx"] for s in slots])
        o = np.array([s["received"] for s in slots], float)
        for lo in np.arange(0, 1, 0.2):
            sel = (p >= lo) & (p < lo + 0.2 + (lo >= 0.8))
            if sel.any():
                lines.append(f"| {lo:.1f}-{lo + 0.2:.1f} | {sel.sum()} | {o[sel].mean():.0%} |")
    report = "\n".join(lines) + "\n"
    stem.with_suffix(".md").write_text(report, encoding="utf-8")

    fields = sorted({k for r in all_rows for k in r}, key=lambda k: (("__" in k), k))
    with stem.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(all_rows)

    fmap = folium.Map(location=(home["lat"], home["lon"]), zoom_start=16)
    folium.Marker(
        (home["lat"], home["lon"]),
        tooltip=f"Home ({site_name})",
        icon=folium.Icon(color="blue", icon="home"),
    ).add_to(fmap)
    if slots:
        pcol = cm.LinearColormap(
            ["#d73027", "#fee08b", "#1a9850"],
            vmin=0,
            vmax=1,
            caption="ENS predicted P(rx) per packet (squares)",
        )
        for s in slots:
            folium.RegularPolygonMarker(
                (s["lat"], s["lon"]),
                number_of_sides=4,
                radius=5,
                weight=2,
                color="#000000" if s["received"] else "#ffffff",
                fill=True,
                fill_color=pcol(s["ENS__p_rx"]),
                fill_opacity=0.9,
                tooltip=f"{s['time'][11:19]} {'received' if s['received'] else 'not received'}"
                f"<br>ENS P(rx) {s['ENS__p_rx']:.2f}",
            ).add_to(fmap)
        pcol.add_to(fmap)
    rcol = cm.LinearColormap(
        ["#b2182b", "#f7f7f7", "#2166ac"],
        vmin=-15,
        vmax=15,
        caption="measured - ENS predicted signal [dB] (circles)",
    )
    for r in rows:
        tip = "<br>".join(f"{m}: {r[f'{m}__pred']:.1f}" for m in names if f"{m}__pred" in r)
        folium.CircleMarker(
            (r["lat"], r["lon"]),
            radius=7,
            color="#333333",
            weight=1,
            fill=True,
            fill_color=rcol(float(np.clip(r["ENS__resid"], -15, 15))),
            fill_opacity=0.9,
            tooltip=f"{r['time'][11:19]}, {r['d_m']} m<br>measured {r['signal_obs']} dBm "
            f"(RSSI {r['rssi']}, SNR {r['snr']})<br>{tip}",
        ).add_to(fmap)
    rcol.add_to(fmap)
    fmap.save(stem.with_suffix(".html"))

    print(report)
    print(f"Written: {stem}.md / .csv / .html")
    if args.open:
        serve(out_dir, stem.with_suffix(".html").name)


if __name__ == "__main__":
    main()
