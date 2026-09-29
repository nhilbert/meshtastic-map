"""Model predictions for single packets from a fixed site to arbitrary positions."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from meshplay.config import DEFAULT_PRESET
from meshplay.sim.models import add_ensemble, budget, prep_channels, prep_profile, run_link
from meshplay.sim.scene import Scene


@dataclass
class LinkSetup:
    rx_height_m: tuple[float, float] = (1.0, 1.6)  # the mobile end, above ground
    rx_clutter_m: float = 12.0  # typical building/tree height around the mobile end
    device: str = "t1000e"  # antenna prior of the mobile end
    preset: str = DEFAULT_PRESET
    site_indoor: str | None = "open"  # None, "open", "trad", "lowe"
    leaf: str = "belaubt"
    draws: int = 4000
    cheap_grid: int = 5


class Predictor:
    """Per-packet predictions site -> position, cached on a 5 m grid.

    at(x, y) returns None outside the scene or closer than 30 m to the site, else a dict with
    d_m, measured (share of the path over measured cells), models (names) and per model:
    signal (per-packet signal power samples, dBm), snr (samples), p_rx (delivery probability).
    """

    def __init__(self, scene: Scene, site: dict, setup: LinkSetup, rng: np.random.Generator):
        self.scene, self.site, self.setup, self.rng = scene, site, setup, rng
        self.cache: dict[tuple[int, int], dict | None] = {}

    def at(self, x: float, y: float) -> dict | None:
        key = (round(x / 5), round(y / 5))
        if key not in self.cache:
            self.cache[key] = self._predict(x, y)
        return self.cache[key]

    def _predict(self, x: float, y: float) -> dict | None:
        s = self.setup
        sx, sy = self.site["utm"]
        if not self.scene.contains(x, y, margin=10) or np.hypot(x - sx, y - sy) < 30:
            return None
        try:
            prof = self.scene.profile((sx, sy), (x, y))
        except ValueError:  # path leaves the scene data
            return None
        d, h, r, ct, zone = prep_profile(prof)
        rb, rv = prep_channels(prof)
        models = run_link(
            d,
            h,
            r,
            ct,
            zone,
            self.site["height_m"],
            s.rx_height_m,
            s.draws,
            self.rng,
            self.site["clutter_m"],
            s.rx_clutter_m,
            cheap_grid=s.cheap_grid,
            rb=rb,
            rv=rv,
            leaf=s.leaf,
        )
        valid = add_ensemble(models)
        out = dict(
            d_m=prof["L"],
            measured=self.scene.measured_fraction((sx, sy), (x, y)),
            models=[*valid, "ENS"],
        )
        for m in out["models"]:
            bd = budget(
                models[m],
                self.rng,
                s.draws,
                dev=s.device,
                preset=s.preset,
                tx_indoor=s.site_indoor,
                npkt=1,
            )
            out[m] = dict(
                signal=bd["prx_pkt"][0], snr=bd["snr_pkt"][0], p_rx=float(bd["ok_pkt"].mean())
            )
        return out
