"""Build a coverage map from a walk: tracker positions heard by the home node, or probe results.

Record first with scripts/listen.py (positions) or scripts/probe_walk.py (traceroutes), then:

python scripts/coverage_map.py --tracker !abcd1234 [--gpx data/tracks/walk.gpx] [--open]
python scripts/coverage_map.py --tracker !abcd1234 --probes --gpx data/tracks/walk.gpx [--open]

Writes data/maps/coverage-<tracker>-<date>.html and a matching .csv.
Points received directly (0 hops) are colored by SNR; relayed points are drawn gray.
With --gpx (e.g. a smartphone track of the walk), the full route is drawn and colored by
whether the home node heard the tracker there. Without it, dashed red lines join the
points before and after a stretch with no reception.
With --probes, each traceroute is placed on the GPX track at the time it was sent and
colored by the weaker of its two directions; unanswered probes are red rings.
--open serves the map on http://localhost:8765 and opens it (map tiles don't load from file://).
"""

import argparse
import csv
import math
from datetime import date
from pathlib import Path

import folium

from meshplay import load_settings
from meshplay.walk import (
    classify_track,
    distance_m,
    load_gpx,
    load_points,
    load_probes,
    parse_node,
    probe_points,
    serve,
    typical_interval_s,
)

# LongFast (SF11) decodes down to about -17.5 dB SNR.
SNR_BANDS = [
    (0, "#1a9850", "> 0 dB"),
    (-7, "#91cf60", "0 to -7 dB"),
    (-13, "#fc8d59", "-7 to -13 dB"),
    (-math.inf, "#d73027", "< -13 dB"),
]
RELAYED = "#999999"
NO_RECEPTION = "#d73027"
TRACK_STYLES = {
    "direct": {"color": "#1a9850", "weight": 5, "opacity": 0.5},
    "relayed": {"color": RELAYED, "weight": 5, "opacity": 0.6},
    "none": {"color": NO_RECEPTION, "weight": 4, "opacity": 0.8, "dash_array": "6 8"},
}


def snr_color(snr: float) -> str:
    return next(color for limit, color, _ in SNR_BANDS if snr > limit)


def draw_track(fmap: folium.Map, track: list[dict]) -> dict[str, float]:
    """Draw the track as runs of equal status; return walked meters per status."""
    meters = dict.fromkeys(TRACK_STYLES, 0.0)
    run = [track[0]]
    for prev, cur in zip(track, track[1:], strict=False):
        meters[prev["status"]] += distance_m((prev["lat"], prev["lon"]), (cur["lat"], cur["lon"]))
        run.append(cur)
        if cur["status"] != prev["status"] or cur is track[-1]:
            status = prev["status"]
            folium.PolyLine(
                [(p["lat"], p["lon"]) for p in run],
                tooltip={"direct": "Heard directly", "relayed": "Heard via relay"}.get(
                    status, "No reception"
                ),
                **TRACK_STYLES[status],
            ).add_to(fmap)
            run = [cur]
    return meters


def draw_gaps(fmap: folium.Map, points: list[dict], interval: int) -> None:
    for prev, cur in zip(points, points[1:], strict=False):
        seconds = (cur["time"] - prev["time"]).total_seconds()
        if seconds > interval * 2.5:
            folium.PolyLine(
                [(prev["lat"], prev["lon"]), (cur["lat"], cur["lon"])],
                tooltip=f"No reception: ~{round(seconds / interval) - 1} packets missed",
                **TRACK_STYLES["none"],
            ).add_to(fmap)


def draw_probe(fmap: folium.Map, p: dict, home: tuple[float, float] | None) -> None:
    local_time = p["time"].astimezone()
    dist = f"<br>{p['distance_m']} m from home" if home else ""
    if p["hops"] is None:
        folium.CircleMarker(
            (p["lat"], p["lon"]),
            radius=6,
            color=NO_RECEPTION,
            weight=3,
            fill=False,
            tooltip=f"{local_time:%H:%M:%S}<br>no answer ({p['result']}){dist}",
        ).add_to(fmap)
        return
    color = snr_color(p["snrMin"]) if p["hops"] == 0 and p["snrMin"] is not None else RELAYED
    folium.CircleMarker(
        (p["lat"], p["lon"]),
        radius=7,
        color=color,
        fill=True,
        fill_opacity=0.85,
        tooltip=(
            f"{local_time:%H:%M:%S}<br>home &rarr; tracker {p['snrTowards']} dB"
            f"<br>tracker &rarr; home {p['snr']} dB, RSSI {p['rssi']} dBm{dist}"
        ),
    ).add_to(fmap)


def add_legend(fmap: folium.Map, with_track: bool) -> None:
    rows = "".join(
        f'<div><span style="color:{c}">&#9679;</span> {label}</div>' for _, c, label in SNR_BANDS
    )
    rows += f'<div><span style="color:{RELAYED}">&#9679;</span> relayed</div>'
    if with_track:
        rows += '<div style="margin-top:4px"><b>Walked route</b></div>'
        rows += '<div><span style="color:#1a9850">&#9644;</span> heard directly</div>'
        rows += f'<div><span style="color:{RELAYED}">&#9644;</span> heard via relay</div>'
    rows += f'<div><span style="color:{NO_RECEPTION}">- - -</span> no reception</div>'
    fmap.get_root().html.add_child(
        folium.Element(
            '<div style="position:fixed;bottom:20px;left:20px;z-index:1000;background:white;'
            'padding:8px 12px;border-radius:6px;font:13px sans-serif;box-shadow:0 1px 4px #0004">'
            f"<b>Direct SNR</b>{rows}</div>"
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tracker", required=True, help="tracker node ID, e.g. !abcd1234")
    parser.add_argument("--date", default=date.today().isoformat(), help="log date (YYYY-MM-DD)")
    parser.add_argument("--gpx", type=Path, help="GPS track of the walk (GPX file)")
    parser.add_argument(
        "--probes", action="store_true", help="use probe_walk.py results (needs --gpx)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="position or probe interval in s (default: from the probe log or the timestamps)",
    )
    parser.add_argument("--open", action="store_true", help="serve the map and open a browser")
    args = parser.parse_args()

    if args.probes and not args.gpx:
        parser.error("--probes needs --gpx: probes carry no position")

    settings = load_settings()
    if args.probes:
        track = load_gpx(args.gpx)
        log_path = settings.data_dir / "probes" / f"{args.date}.jsonl"
        points = probe_points(load_probes(log_path, parse_node(args.tracker)), track)
        if not points:
            raise SystemExit(f"No probes to {args.tracker} in {log_path} during the GPX track")
        interval = args.interval or typical_interval_s(points) or 60
        run_probe_map(args, settings, track, points, interval)
        return

    log_path = settings.data_dir / "packets" / f"{args.date}.jsonl"
    points = load_points(log_path, parse_node(args.tracker))
    if not points:
        raise SystemExit(f"No position packets from {args.tracker} in {log_path}")
    interval = args.interval or typical_interval_s(points) or 30

    coarse = [p for p in points if p["precisionBits"] < 32]
    if coarse:
        print(
            f"Warning: {len(coarse)} of {len(points)} positions are blurred "
            f"(precisionBits={coarse[0]['precisionBits']}). Enable precise location on the channel."
        )

    home = settings.home
    fmap = folium.Map(location=home or (points[0]["lat"], points[0]["lon"]), zoom_start=16)
    if home:
        icon = folium.Icon(color="blue", icon="home")
        folium.Marker(home, tooltip="Home node", icon=icon).add_to(fmap)

    meters = None
    if args.gpx:
        track = load_gpx(args.gpx)
        if not track:
            raise SystemExit(f"No timestamped track points in {args.gpx}")
        overlap = [p for p in points if track[0]["time"] <= p["time"] <= track[-1]["time"]]
        if not overlap:
            raise SystemExit("The GPX track and the received packets don't overlap in time.")
        classify_track(track, points, window_s=interval * 0.75)
        meters = draw_track(fmap, track)
    else:
        draw_gaps(fmap, points, interval)

    for p in points:
        p["distance_m"] = round(distance_m(home, (p["lat"], p["lon"]))) if home else None
        direct = p["hops"] == 0
        color = snr_color(p["snr"]) if direct and p["snr"] is not None else RELAYED
        route = "direct" if direct else f"{p['hops']} hop(s), via ?{p['relayNode'] or 0:02x}"
        dist = f"<br>{p['distance_m']} m from home" if home else ""
        local_time = p["time"].astimezone()
        folium.CircleMarker(
            (p["lat"], p["lon"]),
            radius=7,
            color=color,
            fill=True,
            fill_opacity=0.85,
            tooltip=(
                f"{local_time:%H:%M:%S}<br>SNR {p['snr']} dB, RSSI {p['rssi']} dBm<br>{route}{dist}"
            ),
        ).add_to(fmap)
    add_legend(fmap, with_track=meters is not None)

    out_dir = settings.data_dir / "maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"coverage-{args.tracker.lstrip('!')}-{args.date}"
    fmap.save(out_dir / f"{stem}.html")
    with (out_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(points[0]))
        writer.writeheader()
        writer.writerows(points)

    direct = [p for p in points if p["hops"] == 0]
    print(f"{len(points)} positions received, {len(direct)} direct.")
    if home and direct:
        far = max(direct, key=lambda p: p["distance_m"])
        print(f"Farthest direct reception: {far['distance_m']} m (SNR {far['snr']} dB)")
    if meters:
        total = sum(meters.values()) or 1
        print(
            f"Walked {total / 1000:.2f} km: "
            + ", ".join(f"{k} {v / total:.0%}" for k, v in meters.items())
        )
    print(f"Map: {out_dir / f'{stem}.html'}")
    if args.open:
        serve(out_dir, f"{stem}.html")


def run_probe_map(args, settings, track: list[dict], points: list[dict], interval: int) -> None:
    home = settings.home
    fmap = folium.Map(location=home or (points[0]["lat"], points[0]["lon"]), zoom_start=16)
    if home:
        icon = folium.Icon(color="blue", icon="home")
        folium.Marker(home, tooltip="Home node", icon=icon).add_to(fmap)

    # Each track point takes the status of the answered probe nearest in time, if any.
    answered = [p for p in points if p["hops"] is not None]
    classify_track(track, answered, window_s=interval / 2)
    meters = draw_track(fmap, track)
    for p in points:
        p["distance_m"] = round(distance_m(home, (p["lat"], p["lon"]))) if home else None
        draw_probe(fmap, p, home)
    add_legend(fmap, with_track=True)

    out_dir = settings.data_dir / "maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"probes-{args.tracker.lstrip('!')}-{args.date}"
    fmap.save(out_dir / f"{stem}.html")
    with (out_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(points[0]))
        writer.writeheader()
        writer.writerows(points)

    direct = [p for p in answered if p["hops"] == 0]
    print(f"{len(points)} probes during the walk, {len(answered)} answered, {len(direct)} direct.")
    if home and direct:
        far = max(direct, key=lambda p: p["distance_m"])
        print(f"Farthest direct answer: {far['distance_m']} m (weaker SNR {far['snrMin']} dB)")
    total = sum(meters.values()) or 1
    print(
        f"Walked {total / 1000:.2f} km: "
        + ", ".join(f"{k} {v / total:.0%}" for k, v in meters.items() if v)
    )
    print(f"Map: {out_dir / f'{stem}.html'}")
    if args.open:
        serve(out_dir, f"{stem}.html")


if __name__ == "__main__":
    main()
