"""Check the P.1812-6 port against the official ITU-R WP 3K validation data.

python scripts/sim_validate_p1812.py [DIR]

DIR (default data/sim/itu-p1812) must contain validation_profiles/ and validation_results/ from
the ITU reference implementation, e.g.:
    git clone https://github.com/eeveetza/p1812 data/sim/itu-p1812-repo
    (the folders are in its matlab/ subfolder; pass that path)
The original port reproduced all 63 cases to 5e-8 dB.
"""

import argparse
import csv
from pathlib import Path

import numpy as np

from meshplay import load_settings
from meshplay.sim.p1812 import tl_p1812

KEYS = [
    ("Lb (dB)", "Lb"),
    ("Ld50 (dB)", "Ld50"),
    ("Ldp (dB)", "Ldp"),
    ("Lbulla (dB)", "Lbulla"),
    ("Lbulls (dB)", "Lbulls"),
    ("Ldsph (dB)", "Ldsph"),
    ("Lbfs", "Lbfs"),
    ("Lb0p", "Lb0p"),
    ("Lba (dB)", "Lba"),
    ("Lbs (dB)", "Lbs"),
    ("Lbc (dB)", "Lbc"),
    ("b0 (%)", "b0"),
    ("hstd (m)", "hstd"),
    ("hsrd (m)", "hsrd"),
    ("hte (m)", "hte"),
    ("hre (m)", "hre"),
    ("hm (m)", "hm"),
    ("dlt (km)", "dlt"),
    ("dlr (km)", "dlr"),
    ("th (mrad)", "theta"),
]


def read_profile(path: Path):
    rows = list(csv.reader(path.open(encoding="latin-1")))
    first, i0 = "T", None
    for i, r in enumerate(rows):
        if r and r[0].startswith("First Point TX or RX"):
            first = (r[1] or "T").strip()
        if r and r[0].startswith("Number of Points"):
            i0 = i + 1
    d, h, cc, gc, rm = [], [], [], [], []
    for r in rows[i0:]:
        if not r or not r[0] or r[0].startswith("{"):
            break
        try:
            d.append(float(r[0]))
        except ValueError:
            break
        h.append(float(r[1]))
        cc.append(int(float(r[2])) if len(r) > 2 and r[2] not in ("", "NaN") else 0)
        gc.append(float(r[3]) if len(r) > 3 and r[3] not in ("", "NaN") else 0.0)
        rm.append(int(float(r[4])) if len(r) > 4 and r[4] not in ("", "NaN") else 4)
    d, h, cc, gc, rm = map(np.array, (d, h, cc, gc, rm))
    if first.upper().startswith("R"):
        d, h, cc, gc, rm = d[-1] - d[::-1], h[::-1], cc[::-1], gc[::-1], rm[::-1]
    return d, h, gc, cc, rm


def read_result(path: Path) -> dict[str, float]:
    out = {}
    for r in csv.reader(path.open(encoding="latin-1")):
        if len(r) >= 4 and r[0] and r[3]:
            try:
                out[r[0].strip()] = float(r[3])
            except ValueError:
                pass
    return out


def validate(base: Path) -> dict[str, np.ndarray]:
    errs = {k: [] for _, k in KEYS}
    for rp in sorted((base / "validation_results").glob("*.csv")):
        if rp.name == "combined_results.csv":
            continue
        pp = base / "validation_profiles" / f"{rp.stem.rsplit('_', 1)[0]}.csv"
        ref = read_result(rp)
        if not pp.exists() or "Lb (dB)" not in ref:
            continue
        d, h, r, ct, zone = read_profile(pp)
        got = tl_p1812(
            f=ref["f (GHz)"],
            p=ref["p (%)"],
            d=d,
            h=h,
            R=r,
            Ct=ct,
            zone=zone,
            htg=ref["htg (m)"],
            hrg=ref["hrg (m)"],
            pol=int(ref["pol"]),
            phi_path=ref["phi (deg)"],
            pL=ref.get("pL (%)", 50),
            sigmaL=ref.get("sigmaL (dB)", 0),
            DN=ref.get("DN", 45),
            N0=ref.get("N0", 325),
            dct=ref.get("dct (km)", 500),
            dcr=ref.get("dcr (km)", 500),
            full=True,
        )
        for rk, gk in KEYS:
            if rk in ref:
                errs[gk].append(abs(got[gk] - ref[rk]))
    return {k: np.array(v) for k, v in errs.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "dir", nargs="?", type=Path, default=load_settings().data_dir / "sim" / "itu-p1812"
    )
    args = parser.parse_args()
    errs = validate(args.dir)
    n = len(errs["Lb"])
    if not n:
        raise SystemExit(f"No validation cases found in {args.dir}")
    print(f"{n} validation cases")
    for k, a in errs.items():
        if len(a):
            print(
                f"  {k:8s} max. deviation {a.max():.3e}   median {np.median(a):.2e}  (n={len(a)})"
            )


if __name__ == "__main__":
    main()
