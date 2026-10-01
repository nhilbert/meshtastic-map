"""Extent of the laser-scan scenes and the cells of the active one without measurements."""

import numpy as np

from meshplay.mapapp.i18n import N_, _
from meshplay.mapapp.registry import Context, Layer, Setting, collection
from meshplay.mapapp.style import png_data_url
from meshplay.sim.sites import to_lonlat


class SceneLayer(Layer):
    id = "scene"
    name = N_("Laserscan-Szene")
    group = N_("Simulation")
    uses_models = True
    description = N_(
        "Umriss der Szenen (data/sim/scenes; durchgezogen = die verwendete) und Flächen ohne "
        "Messpunkte (interpoliert). Unten: Szenen wechseln, löschen und neu erstellen."
    )

    def settings(self, ctx: Context) -> list[Setting]:
        return [Setting("unmeasured", _("Flächen ohne Messpunkte zeigen"), "bool", True)]

    def data(self, ctx: Context, values: dict) -> dict:
        from meshplay.mapapp.scenes import scene_list

        if not ctx.has_scene:
            return collection(
                [],
                note=_(
                    "Keine Laserscan-Szene: unten unter „＋ Neue Szene“ eine erstellen "
                    "(nur Nordrhein-Westfalen)."
                ),
            )
        features = []
        for sc in scene_list(ctx)["scenes"]:
            ring = [[lon, lat] for lat, lon in sc["ring"]]
            ring.append(ring[0])
            fields = {_("Größe"): "{:.1f} × {:.1f} km".format(*sc["size_km"])}
            if sc["measured"] is not None:
                fields[_("gemessen")] = f"{sc['measured']:.0%}"
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {
                        "_title": _("Laserscan-Szene {name}", name=sc["name"])
                        + (" · " + _("verwendet") if sc["active"] else ""),
                        "_fields": fields,
                        "_style": {
                            "color": "#00707f",
                            "weight": 2 if sc["active"] else 1,
                            "fillOpacity": 0,
                            "dash": "" if sc["active"] else "4 4",
                        },
                    },
                }
            )
        out = collection(features)
        s = ctx.scene
        b = s.bbox
        if values["unmeasured"] and not s.measured.all():
            k = 8  # 8 m pixels are plenty for an overview mask
            m = s.measured[: s.shape[0] // k * k, : s.shape[1] // k * k]
            m = m.reshape(m.shape[0] // k, k, m.shape[1] // k, k).mean(axis=(1, 3)) < 0.5
            rgba = np.zeros((*m.shape, 4), np.uint8)
            rgba[m] = [120, 0, 160, 110]
            (w, sth), (e, nth) = to_lonlat(b[0], b[1]), to_lonlat(b[2], b[3])
            # UTM is rotated slightly against lat/lon; the mask is an approximate overlay.
            out["raster"] = {"image": png_data_url(rgba[::-1]), "bounds": [[sth, w], [nth, e]]}
        return out
