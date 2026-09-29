"""Meshtastic node positions: live from the connected device or from a node-database export."""

import json
import time

from meshplay.mapapp.registry import Context, Layer, Setting, collection, feature
from meshplay.mapapp.style import GREY, ramp_color, snr_color, snr_legend

HOPS_RAMP = ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027"]
OWN = "#2cc4d6"
LIVE = "live"
STALE_S = 2 * 3600  # nodes not heard for longer are drawn faded
ROUTER_ROLES = {"ROUTER", "ROUTER_LATE", "REPEATER", "ROUTER_CLIENT"}
TRACKER_ROLES = {"TRACKER", "TAK_TRACKER", "LOST_AND_FOUND"}


def symbol_for(user: dict) -> str:
    """Marker symbol from the node's role (and hardware for trackers)."""
    role = user.get("role", "CLIENT")
    if role in ROUTER_ROLES:
        return "router"
    if role in TRACKER_ROLES or "TRACKER" in user.get("hwModel", ""):
        return "tracker"
    if role == "SENSOR":
        return "sensor"
    return "client"


class NodesLayer(Layer):
    id = "nodes"
    name = "Meshtastic-Knoten"
    group = "Meshtastic"
    description = (
        "Knoten mit Position: live aus der Knotenliste des verbundenen Geräts oder aus "
        "data/exports/nodes-*.json (python scripts/export_nodes.py)."
    )
    enabled_by_default = True

    def exports(self, ctx: Context):
        return sorted((ctx.data_dir / "exports").glob("nodes-*.json"), reverse=True)

    def settings(self, ctx: Context) -> list[Setting]:
        sources = [[LIVE, "Live vom Gerät"]] + [
            [p.name, f"Export {p.stem.removeprefix('nodes-')}"] for p in self.exports(ctx)
        ]
        connected = ctx.device is not None and ctx.device.state == "verbunden"
        default = LIVE if connected or len(sources) == 1 else sources[1][0]
        return [
            Setting("source", "Quelle", "select", default, options=sources),
            Setting("refresh_s", "Live: aktualisieren alle … s", "number", 15, min=5),
            Setting(
                "color",
                "Farbe nach",
                "select",
                "hops",
                options=[["hops", "Hops entfernt"], ["snr", "SNR (nur 0 Hops)"]],
            ),
            Setting("max_age_h", "Nur gehört in den letzten … h (0 = alle)", "number", 0, min=0),
            Setting(
                "markers",
                "Darstellung",
                "select",
                "badge",
                options=[["badge", "Symbol + Kurzname"], ["dot", "nur Punkt"]],
            ),
        ]

    def load(self, ctx: Context, source: str) -> tuple[dict, int | None, dict]:
        if source == LIVE:
            if ctx.device is None:
                raise ValueError("kein Gerät: mapapp mit --device starten oder oben verbinden")
            nodes, me = ctx.device.nodes()
            return nodes, me, {"refresh_s": None}
        path = ctx.data_dir / "exports" / source
        return json.loads(path.read_text(encoding="utf-8")), None, {}

    def data(self, ctx: Context, values: dict) -> dict:
        try:
            nodes, me, extra = self.load(ctx, values["source"])
        except (ValueError, RuntimeError, OSError) as e:
            out = collection([], note=str(e))
            if values["source"] == LIVE:
                out["refresh_s"] = values["refresh_s"]  # keep polling until the device is there
            return out
        if values["source"] == LIVE:
            extra["refresh_s"] = values["refresh_s"]
        now = time.time()
        features, without_pos = [], 0
        for node in nodes.values():
            pos = node.get("position") or {}
            if "latitude" not in pos or "longitude" not in pos:
                without_pos += 1
                continue
            last = node.get("lastHeard")
            own = me is not None and node.get("num") == me
            if (
                values["max_age_h"]
                and not own
                and (not last or now - last > values["max_age_h"] * 3600)
            ):
                continue
            user = node.get("user", {})
            hops = node.get("hopsAway")
            if own:
                color = OWN
            elif values["color"] == "snr":
                color = snr_color(node.get("snr")) if hops == 0 else GREY
            else:
                color = GREY if hops is None else ramp_color(min(hops, 4), 0, 4, HOPS_RAMP)
            metrics = node.get("deviceMetrics", {})
            age = "–"
            if last:
                mins = (now - last) / 60
                age = (
                    f"vor {mins:.0f} min"
                    if mins < 120
                    else time.strftime("%d.%m. %H:%M", time.localtime(last))
                )
            features.append(
                feature(
                    pos["longitude"],
                    pos["latitude"],
                    _title=f"{user.get('longName', '?')} ({user.get('id', '')})"
                    + (" · eigenes Gerät" if own else ""),
                    _icon={
                        "text": user.get("shortName") or user.get("id", "????")[-4:],
                        "symbol": symbol_for(user),
                        "color": color,
                        "own": own,
                        "faded": bool(last) and now - last > STALE_S and not own,
                        "hint": user.get("longName", ""),
                    }
                    if values["markers"] == "badge"
                    else None,
                    _fields={
                        "Hardware": user.get("hwModel", ""),
                        "Rolle": user.get("role", "CLIENT"),
                        "Hops": "–" if hops is None else hops,
                        "SNR": "–" if node.get("snr") is None else f"{node['snr']:.1f} dB",
                        "Zuletzt gehört": age,
                        "Akku": f"{metrics['batteryLevel']} %"
                        if "batteryLevel" in metrics
                        else "–",
                        "Positionsgenauigkeit": f"{pos.get('precisionBits', 32)} Bit",
                    },
                    _style={
                        "color": "#222",
                        "fillColor": color,
                        "radius": 9 if own else 6,
                        "weight": 2 if own else 1,
                    },
                    _z=3.0,
                    _endpoint={
                        "name": user.get("shortName", "Knoten"),
                        "height_m": [1.5, 3.0],
                        "clutter_m": 12.0,
                        "device": "p1pro",
                    },
                )
            )
        if values["color"] == "snr":
            legend = snr_legend("SNR beim eigenen Knoten")
        else:
            legend = {
                "title": "Hops entfernt",
                "items": [
                    [ramp_color(h, 0, 4, HOPS_RAMP), str(h) if h < 4 else "4+"] for h in range(5)
                ]
                + [[GREY, "unbekannt"]],
            }
        if me is not None:
            legend["items"].append([OWN, "eigenes Gerät"])
        note = f"{len(features)} Knoten mit Position, {without_pos} ohne"
        return collection(features, legend, note=note, **extra)
