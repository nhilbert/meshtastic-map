"""Coordination mode on the map: waypoints, trails, the line to the current stop, targets."""

from meshplay.mapapp.i18n import N_, _
from meshplay.mapapp.registry import Context, Layer, Setting, collection


class CoordLayer(Layer):
    id = "coord"
    name = N_("Koordination")
    group = N_("Meshtastic")
    description = N_(
        "Einsätze des Koordinationsmodus: Wegpunkte, die Spur des Knotens und die Linie zum "
        "aktuellen Halt; dazu die gespeicherten Ziele."
    )
    enabled_by_default = True

    def settings(self, ctx: Context) -> list[Setting]:
        return [Setting("refresh_s", _("Aktualisieren alle … s"), "number", 10, min=3)]

    def data(self, ctx: Context, values: dict) -> dict:
        coord = getattr(ctx, "coord", None)
        if coord is None:
            return collection([], note=_("Der Koordinationsmodus ist nicht geladen."))
        features = coord.layer_features()
        from meshplay.mapapp.coord.missions import (
            ABORTED,
            ARRIVED,
            ASSIGNED,
            ENDED,
            HOLDING,
            STATE_COLORS,
            UNDERWAY,
        )

        legend = {
            "title": _("Einsatz"),
            "items": [
                [STATE_COLORS[ASSIGNED], _("zugewiesen")],
                [STATE_COLORS[UNDERWAY], _("unterwegs")],
                [STATE_COLORS[HOLDING], _("wartet")],
                [STATE_COLORS[ARRIVED], _("erreicht")],
                [STATE_COLORS[ABORTED], _("abgebrochen")],
                [STATE_COLORS[ENDED], _("beendet")],
            ],
        }
        active = sum(1 for m in coord.missions.values() if m.active)
        note = (
            _("{n} aktive Einsätze", n=active)
            if coord.enabled
            else _("Der Koordinationsmodus ist aus.")
        )
        return collection(features, legend, note=note, refresh_s=values["refresh_s"])
