"""Predict the configured links (scenarios, corridor, preset comparison) and freeze the result.

python scripts/sim_predict.py [--draws 20000] [--seed 20260920]

Reads data/sim/sites.json and the scene in data/sim/scene/. Writes
data/sim/predictions/predictions-<timestamp>.json (summary), .npz (samples for scoring) and
.sha256. Predictions are never overwritten: measure first, then score against a frozen file
with scripts/sim_score.py or scripts/sim_compare_walk.py.
"""

import argparse
import hashlib
import json
import time
from datetime import datetime

import numpy as np

from meshplay import load_settings
from meshplay.config import DEFAULT_PRESET
from meshplay.sim.itu import MESHTASTIC_PRESETS
from meshplay.sim.models import (
    MODEL_DESCRIPTIONS,
    PRIORS,
    add_ensemble,
    budget,
    prep_channels,
    prep_profile,
    run_link,
)
from meshplay.sim.scene import Scene
from meshplay.sim.sites import load_config


def pct(a, qs=(10, 50, 90)) -> list[float]:
    return [float(np.percentile(a, q)) for q in qs]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--draws", type=int, default=20000)
    parser.add_argument("--preset-draws", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=20260920)
    parser.add_argument("--preset", default=DEFAULT_PRESET, help="Meshtastic modem preset")
    args = parser.parse_args()

    sim_dir = load_settings().data_dir / "sim"
    cfg = load_config(sim_dir / "sites.json")
    sites = cfg["sites"]
    scene = Scene.load(sim_dir / "scene")
    n = args.draws
    rng = np.random.default_rng(args.seed)
    profiles: dict[tuple[str, str], dict] = {}

    def profile(a: str, b: str) -> dict:
        key = (sites[a]["node"], sites[b]["node"])
        if key not in profiles:
            profiles[key] = scene.profile(sites[key[0]]["utm"], sites[key[1]]["utm"])
        return profiles[key]

    def link_models(sc: dict, draws: int, leaf: str) -> dict:
        prof = profile(sc["a"], sc["b"])
        d, h, r, ct, zone = prep_profile(prof)
        rb, rv = prep_channels(prof)
        a, b = sites[sc["a"]], sites[sc["b"]]
        return run_link(
            d,
            h,
            r,
            ct,
            zone,
            a["height_m"],
            b["height_m"],
            draws,
            rng,
            a["clutter_m"],
            b["clutter_m"],
            rb=rb,
            rv=rv,
            leaf=leaf,
        )

    t0 = time.time()
    results, samples = {}, {}
    for sc in cfg["scenarios"]:
        leaf = sc.get("leaf", "belaubt")
        models = link_models(sc, n, leaf)
        valid = add_ensemble(models)
        entry = dict(
            id=sc["id"],
            label=sc["label"],
            a=sc["a"],
            b=sc["b"],
            d_m=float(models["_dtot_km"] * 1000),
            h_a=list(sites[sc["a"]]["height_m"]),
            h_b=list(sites[sc["b"]]["height_m"]),
            valid=valid,
            d_veg_m=float(models.get("_d_veg_m", 0.0)),
            leaf=leaf,
            models={},
        )
        for m in [*valid, "ENS"]:
            bd = budget(
                models[m],
                rng,
                n,
                dev=sc.get("device", "p1pro"),
                preset=args.preset,
                tx_indoor=sc.get("a_indoor"),
                rx_indoor=sc.get("b_indoor"),
            )
            entry["models"][m] = dict(
                Lb=pct(models[m]),
                prx=pct(bd["prx"]),
                snr=float(np.median(bd["snr"])),
                sens=float(np.median(bd["sens"])),
                p_any=float(bd["p_any"]),
                p_all=float(bd["p_all"]),
                delivered=pct(bd["delivered"]),
            )
            samples[f"{sc['id']}__{m}__prx"] = bd["prx"].astype(np.float32)
            samples[f"{sc['id']}__{m}__delivered"] = bd["delivered"].astype(np.uint8)
        results[sc["id"]] = entry
        e = entry["models"]["ENS"]
        print(
            f"{sc['id']:4s} {sc['label'][:48]:48s} Lb {e['Lb'][1]:6.1f}  Prx {e['prx'][1]:7.1f} dBm"
            f"  P(any) {e['p_any']:.2f}  P(10/10) {e['p_all']:.2f}"
        )

    corridor, corridor_models = [], []
    cc = cfg.get("corridor")
    if cc:
        prof = profile(cc["from"], cc["towards"])
        d0, h0, r0, ct0, z0 = prep_profile(prof)
        rb0, rv0 = prep_channels(prof)
        tx = sites[cc["from"]]
        for dm in cc["distances_m"]:
            k = int(np.searchsorted(d0, dm / 1000.0))
            rr, rrb, rrv = r0[: k + 1].copy(), rb0[: k + 1].copy(), rv0[: k + 1].copy()
            rr[-1] = rrb[-1] = rrv[-1] = 0.0
            models = run_link(
                d0[: k + 1],
                h0[: k + 1],
                rr,
                ct0[: k + 1],
                z0[: k + 1],
                tx["height_m"],
                tuple(cc["rx_height_m"]),
                n,
                rng,
                tx["clutter_m"],
                cc["rx_clutter_m"],
                rb=rrb,
                rv=rrv,
            )
            valid = add_ensemble(models)
            bd = budget(models["ENS"], rng, n, preset=args.preset, tx_indoor=cc.get("tx_indoor"))
            cid = f"{cc.get('id_prefix', 'K')}{dm}"
            corridor.append(
                dict(
                    id=cid,
                    d_m=float(models["_dtot_km"] * 1000),
                    Lb=pct(models["ENS"]),
                    prx=pct(bd["prx"]),
                    p_any=float(bd["p_any"]),
                    p_all=float(bd["p_all"]),
                    per_model={m: float(np.median(models[m])) for m in valid},
                )
            )
            corridor_models.append((cid, models, valid))
            c = corridor[-1]
            print(
                f"Corridor {dm:4d} m: Lb {c['Lb'][1]:6.1f}  Prx {c['prx'][1]:7.1f}"
                f"  P(any) {c['p_any']:.2f}"
            )

    presets = {}
    by_id = {sc["id"]: sc for sc in cfg["scenarios"]}
    for sid in cfg.get("preset_comparison", []):
        sc = by_id[sid]
        models = link_models(sc, args.preset_draws, "belaubt")
        add_ensemble(models)
        presets[sid] = {}
        for name in MESHTASTIC_PRESETS:
            bd = budget(
                models["ENS"],
                rng,
                args.preset_draws,
                preset=name,
                tx_indoor=sc.get("a_indoor"),
                rx_indoor=sc.get("b_indoor"),
            )
            presets[sid][name] = dict(
                p_any=float(bd["p_any"]),
                p_all=float(bd["p_all"]),
                sens=float(np.median(bd["sens"])),
                margin=float(np.median(bd["prx"]) - np.median(bd["sens"])),
            )

    # Extra samples for scoring the corridor per model family. Uses a separate random stream
    # so the numbers above stay identical to the original run.py.
    rng_extra = np.random.default_rng(args.seed + 1)
    for cid, models, valid in corridor_models:
        for m in [*valid, "ENS"]:
            bd = budget(models[m], rng_extra, n, preset=args.preset, tx_indoor=cc.get("tx_indoor"))
            samples[f"{cid}__{m}__prx"] = bd["prx"].astype(np.float32)
            samples[f"{cid}__{m}__delivered"] = bd["delivered"].astype(np.uint8)

    out = dict(
        created=datetime.now().isoformat(timespec="seconds"),
        seed=args.seed,
        draws=n,
        f_MHz=868.0,
        preset=args.preset,
        priors={k: (list(v) if isinstance(v, tuple) else v) for k, v in PRIORS.items()},
        sites={k: {kk: vv for kk, vv in v.items() if kk != "utm"} for k, v in sites.items()},
        scenarios=results,
        corridor=corridor,
        presets=presets,
        models=MODEL_DESCRIPTIONS,
    )
    out_dir = sim_dir / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"predictions-{datetime.now():%Y%m%d-%H%M%S}"
    text = json.dumps(out, ensure_ascii=False, indent=1)
    stem.with_suffix(".json").write_text(text, encoding="utf-8")
    np.savez_compressed(
        stem.with_suffix(".npz"),
        fading_sigma_db=PRIORS["fading_sigma_db"],
        corridor_d_m=np.array([c["d_m"] for c in corridor]),
        corridor_ids=np.array([c["id"] for c in corridor]),
        **samples,
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    stem.with_suffix(".sha256").write_text(f"{digest}  {stem.name}.json\n", encoding="utf-8")
    print(f"\nsha256 {digest[:16]}  {time.time() - t0:.0f} s")
    print(f"Written: {stem}.json / .npz / .sha256")


if __name__ == "__main__":
    main()
