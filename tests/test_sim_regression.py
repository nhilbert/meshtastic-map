"""The port must reproduce the frozen Mesh Bonn prediction (11_Simulation_ITU, 2026-09-20).

Needs the original scene in data/sim/reference/2026-09-20/scene and data/sim/sites.json with
scenario A1 first (run with: pytest -m data).
"""

import numpy as np
import pytest

pytest.importorskip("scipy")

from meshplay import load_settings  # noqa: E402
from meshplay.sim.models import (  # noqa: E402
    add_ensemble,
    budget,
    prep_channels,
    prep_profile,
    run_link,
)
from meshplay.sim.scene import Scene  # noqa: E402
from meshplay.sim.sites import load_config  # noqa: E402

SIM = load_settings().data_dir / "sim"
SCENE = SIM / "reference" / "2026-09-20" / "scene"  # the scene the frozen prediction was made on
# A1 ensemble, received level percentiles 10/50/90 from the original predictions.json.
A1_ENS_PRX = [-130.2231225587211, -125.8647238681713, -121.55376696025965]


@pytest.mark.data
@pytest.mark.skipif(
    not (SCENE / "scene_raw.npz").exists() or not (SIM / "sites.json").exists(),
    reason="no local scene/sites",
)
def test_reproduces_frozen_a1_prediction():
    cfg = load_config(SIM / "sites.json")
    sc = cfg["scenarios"][0]
    if sc["id"] != "A1":
        pytest.skip("sites.json is not the Mesh Bonn configuration")
    sites = cfg["sites"]
    a, b = sites[sc["a"]], sites[sc["b"]]
    prof = Scene.load(SCENE).profile(a["utm"], b["utm"])
    d, h, r, ct, zone = prep_profile(prof)
    rb, rv = prep_channels(prof)
    rng = np.random.default_rng(20260920)
    n = 20000
    models = run_link(
        d,
        h,
        r,
        ct,
        zone,
        a["height_m"],
        b["height_m"],
        n,
        rng,
        a["clutter_m"],
        b["clutter_m"],
        rb=rb,
        rv=rv,
    )
    valid = add_ensemble(models)
    for m in [*valid, "ENS"]:
        bd = budget(models[m], rng, n, tx_indoor="open")
    prx = [np.percentile(bd["prx"], q) for q in (10, 50, 90)]
    assert prx == pytest.approx(A1_ENS_PRX, abs=1e-3)
