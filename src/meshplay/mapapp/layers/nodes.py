"""Meshtastic node positions: live from the connected device or from a node-database export."""

import json
import time

from meshplay.mapapp.i18n import N_, _
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
    name = N_("Meshtastic-Knoten")
    group = N_("Meshtastic")
    description = N_(
        "Knoten mit Position: live aus der Knotenliste des verbundenen Geräts oder aus "
        "data/exports/nodes-*.json (python scripts/export_nodes.py)."
    )
    enabled_by_default = True

    def exports(self, ctx: Context):
        return sorted((ctx.data_dir / "exports").glob("nodes-*.json"), reverse=True)

    def settings(self, ctx: Context) -> list[Setting]:
        sources = [[LIVE, _("Live vom Gerät")]] + [
            [p.name, _("Export {date}", date=p.stem.removeprefix("nodes-"))]
            for p in self.exports(ctx)
        ]
        connected = ctx.device is not None and ctx.device.state == "verbunden"
        default = LIVE if connected or len(sources) == 1 else sources[1][0]
        return [
            Setting("source", _("Quelle"), "select", default, options=sources),
            Setting("refresh_s", _("Live: aktualisieren alle … s"), "number", 15, min=5),
            Setting(
                "color",
                _("Farbe nach"),
                "select",
                "hops",
                options=[["hops", _("Hops entfernt")], ["snr", _("SNR (nur 0 Hops)")]],
            ),
            Setting("max_age_h", _("Nur gehört in den letzten … h (0 = alle)"), "number", 0, min=0),
            Setting(
                "markers",
                _("Darstellung"),
                "select",
                "badge",
                options=[["badge", _("Symbol + Kurzname")], ["dot", _("nur Punkt")]],
            ),
        ]

    def load(self, ctx: Context, source: str) -> tuple[dict, int | None, dict]:
        if source == LIVE:
            if ctx.device is None:
                raise ValueError(_("kein Gerät: mapapp mit --device starten oder oben verbinden"))
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
        features, rows, without_pos = [], [], 0
        for node in nodes.values():
            pos = node.get("position") or {}
            has_pos = "latitude" in pos and "longitude" in pos
            last = node.get("lastHeard")
            own = me is not None and node.get("num") == me
            if (
                values["max_age_h"]
                and not own
                and (not last or now - last > values["max_age_h"] * 3600)
            ):
                continue
            user = node.get("user", {})
            # the page's node list shows every node of the source, with or without position
            rows.append(node_row(node, own, pos if has_pos else None))
            if not has_pos:
                without_pos += 1
                continue
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
                    _("vor {n} min", n=f"{mins:.0f}")
                    if mins < 120
                    else time.strftime("%d.%m. %H:%M", time.localtime(last))
                )
            features.append(
                feature(
                    pos["longitude"],
                    pos["latitude"],
                    _title=f"{user.get('longName', '?')} ({user.get('id', '')})"
                    + (" · " + _("eigenes Gerät") if own else ""),
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
                        _("Hardware"): user.get("hwModel", ""),
                        _("Rolle"): user.get("role", "CLIENT"),
                        _("Hops"): "–" if hops is None else hops,
                        "SNR": "–" if node.get("snr") is None else f"{node['snr']:.1f} dB",
                        _("Zuletzt gehört"): age,
                        _("Akku"): f"{metrics['batteryLevel']} %"
                        if "batteryLevel" in metrics
                        else "–",
                        _("Positionsgenauigkeit"): _("{n} Bit", n=pos.get("precisionBits", 32)),
                    },
                    _style={
                        "color": "#222",
                        "fillColor": color,
                        "radius": 9 if own else 6,
                        "weight": 2 if own else 1,
                    },
                    _z=3.0,
                    _node_id=user.get("id"),
                    _endpoint={
                        "name": user.get("shortName", _("Knoten")),
                        "height_m": [1.5, 3.0],
                        "clutter_m": 12.0,
                        "device": "p1pro",
                    },
                )
            )
        if values["color"] == "snr":
            legend = snr_legend(N_("SNR beim eigenen Knoten"))
        else:
            legend = {
                "title": _("Hops entfernt"),
                "items": [
                    [ramp_color(h, 0, 4, HOPS_RAMP), str(h) if h < 4 else "4+"] for h in range(5)
                ]
                + [[GREY, _("unbekannt")]],
            }
        if me is not None:
            legend["items"].append([OWN, _("eigenes Gerät")])
        note = _("{n} Knoten mit Position, {m} ohne", n=len(features), m=without_pos)
        return collection(features, legend, note=note, nodes=rows, **extra)


def node_row(node: dict, own: bool, pos: dict | None) -> dict:
    """One node for the page's node list (plain values, the page formats them)."""
    user = node.get("user", {})
    metrics = node.get("deviceMetrics", {})
    return {
        "id": user.get("id") or f"!{node.get('num', 0):08x}",
        "short": user.get("shortName", ""),
        "long": user.get("longName", ""),
        "hw": user.get("hwModel", ""),
        "role": user.get("role", "CLIENT"),
        "hops": node.get("hopsAway"),
        "snr": node.get("snr"),
        "last": node.get("lastHeard"),
        "battery": metrics.get("batteryLevel"),
        "lat": pos["latitude"] if pos else None,
        "lon": pos["longitude"] if pos else None,
        "own": own,
        "favorite": bool(node.get("isFavorite")),
    }
