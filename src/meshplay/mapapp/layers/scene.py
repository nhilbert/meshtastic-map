"""Extent of the laser-scan scene and the cells without measurements."""

import numpy as np

from meshplay.mapapp.i18n import N_, _
from meshplay.mapapp.registry import Context, Layer, Setting, collection
from meshplay.mapapp.style import png_data_url


class SceneLayer(Layer):
    id = "scene"
    name = N_("Laserscan-Szene")
    group = N_("Simulation")
    description = N_(
        "Umriss der Szene (data/sim/scene) und Flächen ohne Messpunkte (interpoliert)."
    )

    def settings(self, ctx: Context) -> list[Setting]:
        return [Setting("unmeasured", _("Flächen ohne Messpunkte zeigen"), "bool", True)]

    def data(self, ctx: Context, values: dict) -> dict:
        from meshplay.sim.sites import to_lonlat

        if not ctx.has_scene:
            return collection(
                [], note=_("Keine Laserscan-Szene (README, Abschnitt „3D laser-scan data“).")
            )
        s = ctx.scene
        b = s.bbox
        ring = [
            to_lonlat(x, y) for x, y in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))
        ]
        ring.append(ring[0])
        outline = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "_title": _("Laserscan-Szene"),
                "_fields": {
                    _("Größe"): f"{(b[2] - b[0]) / 1000:.1f} × {(b[3] - b[1]) / 1000:.1f} km",
                    _("gemessen"): f"{s.measured.mean():.0%}",
                },
                "_style": {"color": "#00707f", "weight": 2, "fillOpacity": 0, "dash": "4 4"},
            },
        }
        out = collection([outline])
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
