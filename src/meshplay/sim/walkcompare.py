"""Scoring a coverage walk against the models (used by sim_compare_walk.py and sim_app.py)."""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from meshplay.sim.compare import interval_hit, kde_logpdf, signal_power_dbm
from meshplay.sim.predictor import Predictor
from meshplay.sim.sites import to_utm

MODEL_ORDER = [
    "M1_P1812",
    "M1b_P1812_clut",
    "M6_P1812_P833",
    "M2_P1812_bare",
    "M3_Bullington",
    "M4_P1411",
    "M5_LogDist",
    "ENS",
]


def nearest_site(sites: dict, lat: float, lon: float) -> str:
    x, y = to_utm(lon, lat)
    return min(sites, key=lambda s: np.hypot(sites[s]["utm"][0] - x, sites[s]["utm"][1] - y))


def observed_signal(rssi: float, snr: float | None, raw_rssi: bool = False) -> float:
    """Measured signal power: RSSI corrected by SNR (see meshplay.sim.compare)."""
    if raw_rssi or snr is None:
        return float(rssi)
    return float(signal_power_dbm(rssi, snr))


def score_packets(direct: list[dict], predictor: Predictor, raw_rssi: bool = False):
    """One row per directly received packet with per-model prediction, residual and scores.

    Returns (rows, skipped) where skipped counts packets outside the scene / too close.
    """
    rows, skipped = [], 0
    for p in direct:
        pred = predictor.at(*to_utm(p["lon"], p["lat"]))
        if pred is None:
            skipped += 1
            continue
        obs = observed_signal(p["rssi"], p["snr"], raw_rssi)
        row = dict(
            kind="signal",
            time=p["time"].astimezone().isoformat(timespec="seconds"),
            lat=p["lat"],
            lon=p["lon"],
            d_m=round(pred["d_m"]),
            measured_share=round(pred["measured"], 2),
            rssi=p["rssi"],
            snr=p["snr"],
            signal_obs=round(obs, 1),
        )
        for m in pred["models"]:
            sig = pred[m]["signal"]
            row[f"{m}__pred"] = round(float(np.median(sig)), 1)
            row[f"{m}__resid"] = obs - float(np.median(sig))
            row[f"{m}__logscore"] = float(kde_logpdf(sig, obs)[0])
            row[f"{m}__hit80"] = interval_hit(sig, obs)
        rows.append(row)
    return rows, skipped


def score_slots(track: list[dict], direct: list[dict], predictor: Predictor, interval: int):
    """Cut the GPX track into slots of `interval` s; a slot counts as received if a direct
    packet arrived within +-interval/2. Per model: predicted P(rx) and Brier score.

    Returns (slots, skipped).
    """
    slots, skipped = [], 0
    if not track:
        return slots, skipped
    t, end = track[0]["time"], track[-1]["time"]
    half = timedelta(seconds=interval / 2)
    times = [tp["time"] for tp in track]
    while t <= end:
        tp = track[int(np.argmin([abs((tt - t).total_seconds()) for tt in times]))]
        got = any(abs(p["time"] - t) <= half for p in direct)
        pred = predictor.at(*to_utm(tp["lon"], tp["lat"]))
        if pred is None:
            skipped += 1
        else:
            row = dict(
                kind="slot",
                time=t.astimezone().isoformat(timespec="seconds"),
                lat=tp["lat"],
                lon=tp["lon"],
                d_m=round(pred["d_m"]),
                received=got,
            )
            for m in pred["models"]:
                row[f"{m}__p_rx"] = round(pred[m]["p_rx"], 3)
                row[f"{m}__brier"] = (pred[m]["p_rx"] - got) ** 2
            slots.append(row)
        t += timedelta(seconds=interval)
    return slots, skipped


def model_names(rows: list[dict]) -> list[str]:
    """Models present in the rows, in canonical order (M4 is only valid up to 660 m)."""
    present = {k.split("__")[0] for r in rows for k in r if "__" in k}
    return [m for m in MODEL_ORDER if m in present]
