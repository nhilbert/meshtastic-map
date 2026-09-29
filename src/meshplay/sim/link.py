"""Prediction for one direct link between two arbitrary points of the scene."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from meshplay.config import DEFAULT_PRESET
from meshplay.sim.models import (
    PRIORS,
    add_ensemble,
    budget,
    prep_channels,
    prep_profile,
    run_link,
)
from meshplay.sim.scene import Scene
from meshplay.sim.view3d import link_geometry

INDOOR = (None, "open", "trad", "lowe")


@dataclass
class Endpoint:
    x: float  # EPSG:25832
    y: float
    height_m: tuple[float, float] = (1.0, 1.6)  # antenna above ground, uniform prior
    clutter_m: float = 12.0  # typical building/tree height around the point
    indoor: str | None = None  # None (antenna outside), "open", "trad", "lowe"
    device: str = "p1pro"  # "p1pro" (whip antenna) or "t1000e"

    @classmethod
    def from_dict(cls, d: dict) -> Endpoint:
        indoor = d.get("indoor") or None
        if indoor not in INDOOR:
            raise ValueError(f"indoor must be one of {INDOOR}")
        return cls(
            x=float(d["x"]),
            y=float(d["y"]),
            height_m=tuple(float(v) for v in d.get("height_m", (1.0, 1.6))),
            clutter_m=float(d.get("clutter_m", 12.0)),
            indoor=indoor,
            device=d.get("device", "p1pro"),
        )


def predict_link(
    scene: Scene,
    a: Endpoint,
    b: Endpoint,
    preset: str = DEFAULT_PRESET,
    leaf: str = "belaubt",
    draws: int = 4000,
    seed: int = 1,
) -> dict:
    """Profile, geometry and all model families for the direct link a -> b.

    Per model: Lb (basic transmission loss) and prx (received signal of a single packet, fading
    included) as 10/50/90 % percentiles, median SNR, sensitivity and the probability that a
    single packet is received. Raises ValueError if the path leaves the scene data or is shorter
    than 20 m.
    """
    if np.hypot(b.x - a.x, b.y - a.y) < 20:
        raise ValueError("points are less than 20 m apart")
    for p in (a, b):
        if not scene.contains(p.x, p.y, margin=5):
            raise ValueError("point outside the scene")
    prof = scene.profile((a.x, a.y), (b.x, b.y))
    d, h, r, ct, zone = prep_profile(prof)
    rb, rv = prep_channels(prof)
    rng = np.random.default_rng(seed)
    models = run_link(
        d,
        h,
        r,
        ct,
        zone,
        a.height_m,
        b.height_m,
        draws,
        rng,
        a.clutter_m,
        b.clutter_m,
        cheap_grid=5,
        rb=rb,
        rv=rv,
        leaf=leaf,
    )
    valid = add_ensemble(models)
    # The second terminal's antenna prior is set by b.device; a's is always the whip prior.
    dev = "t1000e" if "t1000e" in (a.device, b.device) else "p1pro"
    out_models = {}
    for m in [*valid, "ENS"]:
        bd = budget(
            models[m],
            rng,
            draws,
            dev=dev,
            preset=preset,
            tx_indoor=a.indoor,
            rx_indoor=b.indoor,
            npkt=1,
        )
        out_models[m] = dict(
            Lb=np.percentile(models[m], [10, 50, 90]).tolist(),
            prx=np.percentile(bd["prx_pkt"][0], [10, 50, 90]).tolist(),
            snr=float(np.median(bd["snr_pkt"][0])),
            sens=float(np.median(bd["sens"])),
            p_rx=float(bd["ok_pkt"].mean()),
        )
    ha, hb = float(np.mean(a.height_m)), float(np.mean(b.height_m))
    return dict(
        profile=prof,
        geometry=link_geometry(prof, ha, hb),
        models=out_models,
        valid=valid,
        d_m=prof["L"],
        measured=scene.measured_fraction((a.x, a.y), (b.x, b.y)),
        preset=preset,
        leaf=leaf,
        draws=draws,
        fading_sigma_db=PRIORS["fading_sigma_db"],
    )
