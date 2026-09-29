"""Simulated coverage around a site, per model family (from scripts/sim_coverage_map.py)."""

import json

import numpy as np

from meshplay.mapapp.i18n import N_, _
from meshplay.mapapp.registry import Context, Layer, Setting
from meshplay.mapapp.style import DELIVERY_RAMP, png_data_url, ramp_rgba

# German source texts, translated with _() where shown
PLACEMENT = {
    "none": N_("außen"),
    "open": N_("Fenster offen"),
    "trad": N_("Fenster zu"),
    "lowe": N_("Wärmeschutz"),
}
MODEL_NAMES = {
    "ENS": N_("Ensemble (Median)"),
    "M1_P1812": N_("M1 P.1812 volles Profil"),
    "M1b_P1812_clut": N_("M1b P.1812 + Endgeräte-Clutter"),
    "M6_P1812_P833": N_("M6 Gebäude beugen, Bäume dämpfen"),
    "M2_P1812_bare": N_("M2 nur Gelände + P.2108"),
    "M3_Bullington": N_("M3 Delta-Bullington"),
    "M4_P1411": N_("M4 P.1411 Kurzstrecke"),
    "M5_LogDist": N_("M5 Log-Distanz"),
}


class CoverageLayer(Layer):
    id = "coverage"
    name = N_("Simulierte Abdeckung")
    group = N_("Simulation")
    kind = "raster"
    description = N_(
        "Vorhergesagte Empfangswahrscheinlichkeit eines Pakets je Modellfamilie "
        "(Aufgaben → Abdeckung simulieren, oder scripts/sim_coverage_map.py)."
    )

    def files(self, ctx: Context):
        """Grids, newest first."""
        paths = (ctx.sim_dir / "maps").glob("coverage-*.npz")
        return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)

    @staticmethod
    def label(path) -> str:
        """Setup of a grid from its meta (older grids have none: file name)."""
        try:
            with np.load(path) as z:
                m = json.loads(str(z["meta"])) if "meta" in z.files else None
        except (OSError, ValueError):
            m = None
        if not m:
            return path.stem.removeprefix("coverage-")
        place = _(PLACEMENT.get(m["site_indoor"], m["site_indoor"]))
        winter = ", " + _("Winter") if m.get("leaf") == "unbelaubt" else ""
        return f"{m['site']} · {m['preset']} · {place} · {m['radius']:g} m{winter}"

    def settings(self, ctx: Context) -> list[Setting]:
        files = [[p.name, self.label(p)] for p in self.files(ctx)]
        # M4 is only valid up to 660 m and not stored in the coverage grids
        models = [[m, _(name)] for m, name in MODEL_NAMES.items() if m != "M4_P1411"]
        return [
            Setting("file", _("Berechnung"), "select", files[0][0] if files else "", options=files),
            Setting("model", _("Modell"), "select", "ENS", options=models),
            Setting("opacity", _("Deckkraft"), "number", 0.6, min=0.1, max=1.0, step=0.1),
        ]

    def data(self, ctx: Context, values: dict) -> dict:
        if not values["file"]:
            return {
                "type": "raster",
                "image": None,
                "note": _(
                    "Noch keine Berechnung: Aufgaben → Abdeckung simulieren "
                    "(braucht eine Laserscan-Szene)"
                ),
            }
        z = np.load(ctx.sim_dir / "maps" / values["file"])
        if values["model"] not in z.files:
            return {"type": "raster", "image": None, "note": _("Modell nicht in dieser Berechnung")}
        grid = z[values["model"]]
        lats, lons = z["lats"], z["lons"]
        dlat, dlon = abs(lats[0] - lats[1]), abs(lons[1] - lons[0])
        rgba = ramp_rgba(grid, 0.0, 1.0, DELIVERY_RAMP, alpha=int(255 * values["opacity"]))
        return {
            "type": "raster",
            "image": png_data_url(rgba),
            "bounds": [
                [float(lats[-1] - dlat / 2), float(lons[0] - dlon / 2)],
                [float(lats[0] + dlat / 2), float(lons[-1] + dlon / 2)],
            ],
            "legend": {
                "title": _("P(Paket)")
                + " · "
                + _(MODEL_NAMES.get(values["model"], values["model"])),
                "ramp": DELIVERY_RAMP,
                "min": 0,
                "max": 1,
                "unit": "",
            },
        }
