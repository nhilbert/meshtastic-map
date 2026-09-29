"""Coverage walk: tracker positions or traceroute probes, phone GPX track, model scores."""

import hashlib
import json
from collections import Counter
from datetime import date

import numpy as np

from meshplay.config import DEFAULT_PRESET
from meshplay.mapapp.registry import Context, Layer, Setting, collection, feature, line
from meshplay.mapapp.style import (
    GREY,
    RED,
    RESIDUAL_RAMP,
    ramp_color,
    snr_color,
    snr_legend,
)
from meshplay.sim.compare import signal_power_dbm
from meshplay.walk import (
    classify_track,
    load_gpx,
    load_points,
    load_probes,
    most_common,
    parse_node,
    probe_points,
    typical_interval_s,
)


def observed_signal(rssi: float, snr: float | None) -> float:
    return float(rssi) if snr is None else float(signal_power_dbm(rssi, snr))


PRESETS = ["LongFast", "MediumSlow", "MediumFast", "ShortSlow", "ShortFast", "LongSlow"]
INDOOR = [
    ["open", "Fenster offen"],
    ["none", "Antenne außen"],
    ["trad", "Fenster zu, Altbau"],
    ["lowe", "Fenster zu, Wärmeschutzglas"],
]
TRACK_COLORS = {"direct": "#1a9850", "relayed": GREY, "none": RED}


def position_senders(path) -> Counter:
    """Senders of position packets in a packet log, with counts."""
    senders = Counter()
    for line_ in path.open(encoding="utf-8"):
        if '"POSITION_APP"' in line_:
            p = json.loads(line_)
            if "latitude" in p.get("decoded", {}).get("position", {}):
                senders[p.get("from")] += 1
    return senders


def probe_targets(path) -> Counter:
    """Targets of traceroute probes in a probe_walk.py log, with counts."""
    targets = Counter()
    for line_ in path.open(encoding="utf-8"):
        targets[parse_node(json.loads(line_)["to"])] += 1
    return targets


class WalkLayer(Layer):
    id = "walk"
    name = "Rundgang (Messung)"
    group = "Abdeckung"
    description = (
        "Positionspakete eines Trackers, die der Heimknoten empfangen hat (data/packets/), "
        "oder Traceroutes vom Heimknoten zum Tracker (data/probes/, braucht GPX-Spur); "
        "optional mit GPX-Spur (data/tracks/) und Vergleich mit den Modellen."
    )
    enabled_by_default = True

    def settings(self, ctx: Context) -> list[Setting]:
        packet_logs = {p.stem: p for p in (ctx.data_dir / "packets").glob("*.jsonl")}
        probe_logs = {p.stem: p for p in (ctx.data_dir / "probes").glob("*.jsonl")}
        dates = [[d, d] for d in sorted(packet_logs.keys() | probe_logs.keys(), reverse=True)]
        # Option values carry the source: "!id" for positions, "probe:!id" for traceroutes.
        trackers = {}
        for d, _ in dates:
            options = []
            if d in probe_logs:
                options += [
                    [f"probe:!{n:08x}", f"!{n:08x} ({c} Traceroutes)"]
                    for n, c in probe_targets(probe_logs[d]).most_common()
                ]
            if d in packet_logs:
                options += [
                    [f"!{n:08x}", f"!{n:08x} ({c} Positionen)"]
                    for n, c in position_senders(packet_logs[d]).most_common()
                    if n
                ]
            trackers[d] = options
        first = trackers.get(dates[0][0], [["", ""]])[0][0] if dates else ""
        gpx = [["", "—"]] + [
            [p.name, p.name] for p in sorted((ctx.data_dir / "tracks").glob("*.gpx"))
        ]
        sites = [[n, n] for n, s in ctx.sites.items()]
        home = sites[0][0] if sites else ""
        if ctx.settings.home and ctx.sites:
            from meshplay.sim.walkcompare import nearest_site

            home = nearest_site(ctx.sites, *ctx.settings.home)
        return [
            Setting("date", "Datum", "select", dates[0][0] if dates else "", options=dates),
            Setting("tracker", "Tracker", "select", first, depends_on="date", options_map=trackers),
            Setting("gpx", "GPX-Spur", "select", "", options=gpx),
            Setting(
                "color",
                "Punkte färben nach",
                "select",
                "snr",
                options=[["snr", "SNR"], ["residual", "Messung − Modell (ENS)"]],
            ),
            Setting("home_site", "Heimknoten (für Vergleich)", "select", home, options=sites),
            Setting("home_indoor", "Heimknoten steht", "select", "open", options=INDOOR),
            # Named walk_preset (was preset) so values saved in the browser don't override "auto".
            Setting(
                "walk_preset",
                "Preset beim Rundgang",
                "select",
                "auto",
                options=[["auto", "aus Log"]] + [[p, p] for p in PRESETS],
                help=f"Traceroute-Logs enthalten das Preset, Paketlogs nicht ({DEFAULT_PRESET}).",
            ),
        ]

    def data(self, ctx: Context, values: dict) -> dict:
        tracker = values["tracker"] or ""
        probes = tracker.startswith("probe:")
        folder = "probes" if probes else "packets"
        log = ctx.data_dir / folder / f"{values['date']}.jsonl"
        if not values["date"] or not tracker or not log.exists():
            return collection([], note="Kein Log / Tracker gewählt")
        if probes and not values["gpx"]:
            return collection([], note="Traceroutes brauchen eine GPX-Spur")

        track = load_gpx(ctx.data_dir / "tracks" / values["gpx"]) if values["gpx"] else []
        node = parse_node(tracker.removeprefix("probe:"))
        if probes:
            all_probes = probe_points(load_probes(log, node), track)
            points = [p for p in all_probes if p["hops"] is not None]
            missed = [p for p in all_probes if p["hops"] is None]
            interval = typical_interval_s(all_probes) or 60.0
            # With one probe per slot, the nearest probe decides each track point.
            window_s = interval / 2
            logged_preset = most_common(p["preset"] for p in all_probes)
        else:
            points = load_points(log, node)
            missed = []
            interval = typical_interval_s(points) or 30.0
            window_s = interval * 0.75
            logged_preset = None
        if values["walk_preset"] != "auto":
            preset, preset_from = values["walk_preset"], "gewählt"
        elif logged_preset in PRESETS:
            preset, preset_from = logged_preset, "aus Log"
        else:
            preset, preset_from = DEFAULT_PRESET, "nicht im Log, angenommen"
        direct = [p for p in points if p["hops"] == 0 and p["rssi"] is not None]
        features, extra = [], {}

        if track:
            classify_track(track, points, window_s=window_s)
            run = [track[0]] if track else []
            for prev, cur in zip(track, track[1:], strict=False):
                run.append(cur)
                if cur["status"] != prev["status"] or cur is track[-1]:
                    features.append(
                        line(
                            [(p["lon"], p["lat"]) for p in run],
                            _title={
                                "direct": "direkt gehört",
                                "relayed": "über Relais",
                                "none": "kein Empfang",
                            }[prev["status"]],
                            _style={
                                "color": TRACK_COLORS[prev["status"]],
                                "weight": 4,
                                "opacity": 0.7,
                                "dash": "6 8" if prev["status"] == "none" else None,
                            },
                        )
                    )
                    run = [cur]

        # The comparison needs the scene and a home site; without them fall back to SNR.
        residual = values["color"] == "residual"
        if residual and not ctx.has_scene:
            residual = False
            extra["note"] = "Vergleich mit Modellen braucht eine Laserscan-Szene: gefärbt nach SNR."
        elif residual and values["home_site"] not in ctx.sites:
            residual = False
            extra["note"] = (
                "Vergleich mit Modellen braucht einen Heimknoten-Standort: gefärbt nach SNR."
            )
        scores = {}
        if residual:
            scores, extra["summary"] = self.scores(
                ctx, values, log, direct, track, interval, preset
            )

        for p in points:
            key = p["time"].isoformat()
            obs = observed_signal(p["rssi"], p["snr"]) if p["rssi"] is not None else None
            fields = {
                "Zeit": p["time"].astimezone().strftime("%H:%M:%S"),
                "RSSI": f"{p['rssi']} dBm" if p["rssi"] is not None else "–",
                "SNR": f"{p['snr']} dB" if p["snr"] is not None else "–",
                "Signal (RSSI−Rauschanteil)": f"{obs:.1f} dBm" if obs is not None else "–",
                "Hops": p["hops"],
            }
            if probes:
                fields["SNR"] = f"{p['snr']} dB (Tracker → Heim)"
                fields["SNR Heim → Tracker"] = f"{p['snrTowards']} dB"
            color = snr_color(p["snr"]) if p["hops"] == 0 else GREY
            if key in scores:
                s = scores[key]
                fields["Modell ENS"] = f"{s['pred']:.1f} dBm"
                fields["Messung − Modell"] = f"{s['resid']:+.1f} dB"
                color = ramp_color(s["resid"], -15, 15, RESIDUAL_RAMP)
            features.append(
                feature(
                    p["lon"],
                    p["lat"],
                    _title=f"{'Traceroute' if probes else 'Paket'} {fields['Zeit']}",
                    _fields=fields,
                    _style={"color": "#333", "fillColor": color, "radius": 6, "weight": 1},
                    _z=1.5,
                    _endpoint={
                        "name": f"Rundgang {fields['Zeit']}",
                        "height_m": [1.0, 1.6],
                        "clutter_m": 12.0,
                        "device": "t1000e",
                        "measured": {
                            "rssi": p["rssi"],
                            "snr": p["snr"],
                            "signal": obs,
                            "hops": p["hops"],
                            "time": fields["Zeit"],
                        },
                    },
                )
            )
        for p in missed:
            t = p["time"].astimezone().strftime("%H:%M:%S")
            features.append(
                feature(
                    p["lon"],
                    p["lat"],
                    _title=f"Traceroute {t}: keine Antwort",
                    _fields={"Zeit": t, "Ergebnis": p["result"]},
                    _style={"color": RED, "fillColor": "#fff", "radius": 5, "weight": 3},
                    _z=1.5,
                )
            )
        if residual:
            legend = {
                "title": "Messung − Modell (ENS)",
                "ramp": RESIDUAL_RAMP,
                "min": -15,
                "max": 15,
                "unit": "dB",
            }
        else:
            legend = snr_legend()
        what = "Traceroutes beantwortet" if probes else "Positionen"
        extra["stats"] = {
            "positions": len(points),
            "direct": len(direct),
            "text": f"{len(points)} {what}, {len(direct)} direkt"
            + (f", {len(missed)} ohne Antwort" if probes else ""),
            "basis": f"Intervall {interval:.0f} s (aus {'Log' if probes else 'Zeitstempeln'}), "
            f"Preset {preset} ({preset_from})",
        }
        # Today's log grows while the map app is connected and logging: refresh live.
        dev = ctx.device
        if (
            dev is not None
            and dev.state == "verbunden"
            and dev.log_packets
            and values["date"] == date.today().isoformat()
            and values["color"] == "snr"
        ):
            extra["refresh_s"] = 20
        # the same for today's probe log while a traceroute walk task is running
        if (
            probes
            and ctx.jobs is not None
            and ctx.jobs.running("probe")
            and values["date"] == date.today().isoformat()
            and values["color"] == "snr"
        ):
            extra["refresh_s"] = 20
        return collection(features, legend, **extra)

    def scores(self, ctx, values, log, direct, track, interval, preset):
        """Per-packet ENS prediction and residual plus the model summary; cached on disk."""
        from meshplay.sim.compare import summarize
        from meshplay.sim.predictor import LinkSetup, Predictor
        from meshplay.sim.walkcompare import model_names, score_packets, score_slots

        gpx = ctx.data_dir / "tracks" / values["gpx"] if values["gpx"] else None
        stamp = [
            log.stat().st_mtime,
            gpx.stat().st_mtime if gpx else 0,
            (ctx.sim_dir / "scene" / "scene_meta.json").stat().st_mtime,
        ]
        key_data = [values, stamp, interval, preset]
        key = hashlib.sha1(json.dumps(key_data, sort_keys=True).encode()).hexdigest()[:16]
        path = ctx.cache_path(self.id, key)
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached["scores"], cached["summary"]

        site = ctx.sites[values["home_site"]]
        indoor = None if values["home_indoor"] == "none" else values["home_indoor"]
        setup = LinkSetup(preset=preset, site_indoor=indoor)
        predictor = Predictor(ctx.scene, site, setup, np.random.default_rng(1))
        rows, skipped = score_packets(direct, predictor)
        slots, skipped_slots = score_slots(track, direct, predictor, interval) if track else ([], 0)
        summary = summarize(rows + slots, model_names(rows + slots)) if rows or slots else {}
        summary = {
            m: {
                k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                for k, v in s.items()
            }
            for m, s in summary.items()
        }
        # rows skip packets outside the scene, so match them to packets by time
        by_time = {}
        times = {r["time"]: r for r in rows}
        for p in direct:
            r = times.get(p["time"].astimezone().isoformat(timespec="seconds"))
            if r:
                by_time[p["time"].isoformat()] = {"pred": r["ENS__pred"], "resid": r["ENS__resid"]}
        out = {
            "scores": by_time,
            "summary": {
                "models": summary,
                "packets": len(rows),
                "slots": len(slots),
                "skipped": skipped + skipped_slots,
            },
        }
        path.write_text(json.dumps(out), encoding="utf-8")
        return out["scores"], out["summary"]
