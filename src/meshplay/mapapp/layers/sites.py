"""Own sites from data/sim/sites.json."""

from meshplay.mapapp.i18n import N_, _
from meshplay.mapapp.registry import Context, Layer, Setting, collection, feature


class SitesLayer(Layer):
    id = "sites"
    name = N_("Eigene Standorte")
    group = N_("Meshtastic")
    description = N_(
        "Standorte aus data/sim/sites.json; als Start/Ziel der Streckenberechnung nutzbar."
    )
    enabled_by_default = True

    def settings(self, ctx: Context) -> list[Setting]:
        return [Setting("labels", _("Namen anzeigen"), "bool", True)]

    def data(self, ctx: Context, values: dict) -> dict:
        features = []
        for name, s in ctx.sites.items():
            if s.get("same_as"):
                continue
            variants = [n for n, v in ctx.sites.items() if v.get("same_as") == name]
            fields = {
                _("Beschreibung"): s.get("description", ""),
                _("Antennenhöhe"): f"{s['height_m'][0]:g} … {s['height_m'][1]:g} m",
                _("Umgebung (Clutter)"): f"{s['clutter_m']:g} m",
            }
            if variants:
                fields[_("Varianten")] = ", ".join(variants)
            features.append(
                feature(
                    s["lon"],
                    s["lat"],
                    _title=name,
                    _icon={
                        "text": name if values["labels"] else "",
                        "symbol": "home",
                        "color": "#00707f",
                        "hint": s.get("description", ""),
                    },
                    _fields=fields,
                    _style={"color": "#00707f", "fillColor": "#2cc4d6", "radius": 8, "weight": 2},
                    _z=float(s["height_m"][1]),
                    _ref={"type": "site", "id": name},
                    _endpoint={
                        "name": name,
                        "height_m": list(s["height_m"]),
                        "clutter_m": s["clutter_m"],
                        "device": "p1pro",
                    },
                )
            )
        return collection(features)
