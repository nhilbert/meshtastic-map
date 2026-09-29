"""Score fixed-point measurements (Messprotokoll CSV) against frozen predictions.

python scripts/sim_score.py [data/measurements/Messprotokoll.csv] [--predictions <file.npz>]

Default predictions: the newest data/sim/predictions/predictions-*.npz. Scenario IDs in the CSV
must match the prediction (A1 ..., corridor K100 ...). Rows marked NICHT WERTEN (relayed) count
as not delivered. The per-scenario observation is the mean signal power of the delivered
packets (RSSI corrected by SNR); the prediction for a mean of k packets is the mean level plus
fading/sqrt(k). Scenarios with delivery below 30 % enter the log score at half weight, because
their mean level is censored upward (only the packets that made it are seen).

Writes the report to stdout; redirect it into a file, e.g. data/measurements/Auswertung.md.
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

from meshplay import load_settings
from meshplay.sim.compare import (
    fit_distance_exponent,
    interval_hit,
    kde_logpdf,
    model_weights,
    signal_power_dbm,
)

ORDER = [
    "M1_P1812",
    "M1b_P1812_clut",
    "M6_P1812_P833",
    "M2_P1812_bare",
    "M3_Bullington",
    "M4_P1411",
    "M5_LogDist",
    "ENS",
]


def _num(row: dict, key: str) -> float:
    """Number from a protocol cell; accepts decimal comma, empty -> NaN."""
    v = (row.get(key) or "").strip().replace(",", ".")
    return float(v) if v else np.nan


def read_protocol(path: Path) -> dict[str, list[dict]]:
    rows = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            sid = (r.get("Szenario") or "").strip()
            if not sid:
                continue
            ok = (r.get("Angekommen_JN") or "").strip().upper().startswith("J")
            ok &= "NICHT WERTEN" not in (r.get("Bemerkung") or "")
            rows[sid].append(dict(ok=ok, rssi=_num(r, "RSSI_dBm"), snr=_num(r, "SNR_dB")))
    return rows


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "protocol",
        nargs="?",
        type=Path,
        default=settings.data_dir / "measurements" / "Messprotokoll.csv",
    )
    parser.add_argument("--predictions", type=Path, help="predictions-*.npz")
    parser.add_argument("--raw-rssi", action="store_true", help="no SNR correction of RSSI")
    args = parser.parse_args()

    pred_path = args.predictions or max((settings.data_dir / "sim" / "predictions").glob("*.npz"))
    pred = np.load(pred_path)
    sigma_f = float(pred["fading_sigma_db"])
    keys = [k for k in pred.files if k.endswith("__prx")]
    available = defaultdict(list)
    for k in keys:
        sid, model, _ = k.split("__")
        available[sid].append(model)
    corridor_d = dict(
        zip(pred["corridor_ids"].tolist(), pred["corridor_d_m"].tolist(), strict=True)
    )

    obs = read_protocol(args.protocol)
    lines = [
        "# Measurements vs. predictions",
        "",
        f"Protocol `{args.protocol.name}`, predictions `{pred_path.name}`, observed level: "
        f"{'raw RSSI' if args.raw_rssi else 'RSSI corrected by SNR'}.",
        "",
    ]
    loglik, n_scored, cover, brier = (
        defaultdict(float),
        defaultdict(int),
        defaultdict(list),
        defaultdict(list),
    )
    noise, corridor = [], []
    unknown = sorted(set(obs) - set(available))
    for sid in [s for s in available if s in obs]:
        rows = obs[sid]
        ok = np.array([r["ok"] for r in rows])
        rate = ok.mean()
        good = [r for r in rows if r["ok"] and np.isfinite(r["rssi"])]
        level = None
        if good:
            rssi = np.array([r["rssi"] for r in good])
            snr = np.array([r["snr"] if np.isfinite(r["snr"]) else 99.0 for r in good])
            sig = rssi if args.raw_rssi else signal_power_dbm(rssi, snr)
            level = float(sig.mean())
            noise += [r["rssi"] - r["snr"] for r in good if np.isfinite(r["snr"])]
        lines.append(
            f"## {sid}: {len(rows)} packets, delivered {rate:.0%}"
            + (f", mean signal {level:.1f} dBm" if level is not None else "")
        )
        lines += [
            "",
            "| Model | median | 80 % | log score | covered | P(packet) | Brier |",
            "|---|---:|---|---:|:---:|---:|---:|",
        ]
        weight = 0.5 if rate < 0.3 else 1.0
        for m in [x for x in ORDER if x in available[sid]]:
            prx = pred[f"{sid}__{m}__prx"].astype(float)
            delivered = pred[f"{sid}__{m}__delivered"].astype(float)
            p_pkt = float(delivered.mean() / 10.0)
            b = (p_pkt - rate) ** 2
            brier[m].append(b)
            lo, med, hi = np.percentile(prx, [10, 50, 90])
            if level is not None:
                rng = np.random.default_rng(0)
                mean_of_k = prx + rng.normal(0, sigma_f / np.sqrt(len(good)), len(prx))
                ls = float(kde_logpdf(mean_of_k, level)[0]) * weight
                hit = interval_hit(mean_of_k, level)
                loglik[m] += ls
                n_scored[m] += 1
                cover[m].append(hit)
                lines.append(
                    f"| {m} | {med:.1f} | {lo:.0f}…{hi:.0f} | {ls:+.2f} | "
                    f"{'yes' if hit else 'no'} | {p_pkt:.2f} | {b:.3f} |"
                )
            else:
                lines.append(
                    f"| {m} | {med:.1f} | {lo:.0f}…{hi:.0f} | – | – | {p_pkt:.2f} | {b:.3f} |"
                )
        lines.append("")
        if sid in corridor_d and level is not None:
            corridor.append((corridor_d[sid], level))

    n_max = max(n_scored.values(), default=0)
    weights = (
        model_weights({m: v for m, v in loglik.items() if n_scored[m] == n_max}) if n_max else {}
    )
    lines += [
        "## Total",
        "",
        "| Model | scenarios | Σ log score | covered 80 % | mean Brier | weight |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for m in [x for x in ORDER if x in brier]:
        c = f"{np.mean(cover[m]):.0%}" if cover[m] else "–"
        w = f"{weights[m]:.2f}" if m in weights else "–"
        lines.append(
            f"| {m} | {n_scored[m]} | {loglik[m]:+.2f} | {c} | {np.mean(brier[m]):.3f} | {w} |"
        )
    lines.append("")
    if noise:
        ne = np.array(noise)
        lines += [
            f"**Noise floor estimate** (RSSI - SNR): {ne.mean():.1f} dBm, σ {ne.std():.1f}. "
            "Thermal at 250 kHz with 6 dB noise figure would be -114 dBm; the difference is "
            "the urban noise level.",
            "",
        ]
    if len(corridor) >= 3:
        n_hat, sigma = fit_distance_exponent(*zip(*corridor, strict=True))
        lines += [
            f"**Corridor fit** ({len(corridor)} points): n = {n_hat:.2f}, residual σ = "
            f"{sigma:.1f} dB. Prior was n in [2.6, 3.8].",
            "",
        ]
    if unknown:
        lines.append(f"Scenarios in the protocol without prediction: {', '.join(unknown)}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
