"""Scoring model predictions against measurements.

Rules carried over from the Mesh Bonn scoring (10_Simulation_v3/rfsim/score.py):
  1. log score: log density of the observation under the model samples (Gaussian KDE);
     higher is better, this is the grade
  2. coverage: does the observation fall inside the 80 % interval?
  3. Brier score of delivery: (P_predicted - observed)^2, lower is better
  4. model weights w ~ exp(sum of log scores), uniform prior

One change: the models predict the power of the wanted signal, while the radio's packet RSSI
measures signal plus noise. Near the noise floor (SNR < 0) RSSI therefore reads several dB too
high. signal_power_dbm() removes the noise share using the packet SNR:
    S = RSSI - 10 log10(1 + 10^(-SNR/10))
At SNR = +10 dB the correction is 0.4 dB, at 0 dB 3 dB, at -10 dB 10.4 dB. This assumes the
reported RSSI is the total in-band power over the packet (SX126x RssiPkt) [BELEG?].
"""

from __future__ import annotations

import numpy as np


def signal_power_dbm(rssi, snr):
    rssi = np.asarray(rssi, float)
    snr = np.asarray(snr, float)
    return rssi - 10 * np.log10(1 + 10 ** (-snr / 10))


def kde_logpdf(samples, x, bw=None):
    """Log density at x of a Gaussian KDE over samples (Silverman bandwidth, at least 1 dB)."""
    s = np.asarray(samples, float)
    s = s[np.isfinite(s)]
    x = np.atleast_1d(np.asarray(x, float))
    if bw is None:
        bw = max(1.06 * s.std() * len(s) ** (-1 / 5), 1.0)
    z = (x[None, :] - s[:, None]) / bw
    dens = np.exp(-0.5 * z**2).sum(axis=0) / (len(s) * bw * np.sqrt(2 * np.pi))
    return np.log(np.maximum(dens, 1e-12))


def interval_hit(samples, x, level=80.0) -> bool:
    lo, hi = np.percentile(samples, [(100 - level) / 2, 100 - (100 - level) / 2])
    return bool(lo <= x <= hi)


def model_weights(total_logscores: dict[str, float]) -> dict[str, float]:
    names = list(total_logscores)
    ll = np.array([total_logscores[m] for m in names])
    w = np.exp(ll - ll.max())
    w /= w.sum()
    return dict(zip(names, map(float, w), strict=True))


def summarize(per_point: list[dict], models: list[str]) -> dict[str, dict]:
    """Aggregate per-point scores into one row per model.

    Each per-point dict may hold, per model m: f"{m}__resid" (observed - predicted median),
    f"{m}__logscore", f"{m}__hit80" (signal comparisons) and f"{m}__brier" (delivery).
    """
    out = {}
    for m in models:
        resid = np.array([p[f"{m}__resid"] for p in per_point if f"{m}__resid" in p])
        ls = np.array([p[f"{m}__logscore"] for p in per_point if f"{m}__logscore" in p])
        hit = np.array([p[f"{m}__hit80"] for p in per_point if f"{m}__hit80" in p])
        brier = np.array([p[f"{m}__brier"] for p in per_point if f"{m}__brier" in p])
        out[m] = dict(
            n_signal=len(resid),
            bias_db=float(np.median(resid)) if len(resid) else np.nan,
            mae_db=float(np.mean(np.abs(resid))) if len(resid) else np.nan,
            coverage80=float(hit.mean()) if len(hit) else np.nan,
            logscore=float(ls.sum()) if len(ls) else np.nan,
            n_delivery=len(brier),
            brier=float(brier.mean()) if len(brier) else np.nan,
        )
    # Log scores are only comparable over the same observations: models with a restricted
    # validity range (M4 up to 660 m) are left out of the weights.
    n_max = max((r["n_signal"] for r in out.values()), default=0)
    scored = {
        m: r["logscore"]
        for m, r in out.items()
        if np.isfinite(r["logscore"]) and r["n_signal"] == n_max
    }
    if scored:
        for m, w in model_weights(scored).items():
            out[m]["weight"] = w
    return out


def fit_distance_exponent(d_m, level_dbm) -> tuple[float, float]:
    """Least-squares fit level = a - 10 n log10(d); returns (n, residual sigma)."""
    d = np.asarray(d_m, float)
    y = np.asarray(level_dbm, float)
    x = np.vstack([np.ones_like(d), np.log10(d)]).T
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    sigma = float(resid.std(ddof=2)) if len(y) > 2 else float("nan")
    return float(-beta[1] / 10.0), sigma
