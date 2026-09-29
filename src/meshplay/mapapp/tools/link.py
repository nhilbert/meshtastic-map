"""Link calculator: one direct link between two points, all model families."""

from __future__ import annotations

from meshplay.config import DEFAULT_PRESET
from meshplay.mapapp.i18n import _
from meshplay.mapapp.registry import Context


def run(ctx: Context, body: dict) -> dict:
    """body: {"a": endpoint, "b": endpoint, "preset", "leaf"}; an endpoint has lat/lon (or x/y in
    EPSG:25832), height_m [lo, hi], clutter_m, indoor (null/"open"/"trad"/"lowe"), device."""
    if not ctx.has_scene:
        raise ValueError(
            _(
                "Keine Laserscan-Szene: die Streckenberechnung braucht sie "
                "(README, Abschnitt „3D laser-scan data“)."
            )
        )
    from meshplay.sim.link import Endpoint, predict_link
    from meshplay.sim.sites import to_lonlat, to_utm

    ends = []
    for key in ("a", "b"):
        e = dict(body[key])
        if "x" not in e:
            e["x"], e["y"] = to_utm(float(e["lon"]), float(e["lat"]))
        if e.get("indoor") == "none":
            e["indoor"] = None
        ends.append(Endpoint.from_dict(e))
    result = predict_link(
        ctx.scene,
        ends[0],
        ends[1],
        preset=body.get("preset", DEFAULT_PRESET),
        leaf=body.get("leaf", "belaubt"),
        draws=int(body.get("draws", 4000)),
    )
    result["a_lonlat"] = to_lonlat(ends[0].x, ends[0].y)
    result["b_lonlat"] = to_lonlat(ends[1].x, ends[1].y)
    return result
