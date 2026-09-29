"""Self-tests of the propagation code (from 11_Simulation_ITU/out/selftest.txt)."""

import numpy as np
import pytest

pytest.importorskip("scipy")

from meshplay.sim.itu import noise_floor_dbm, per_from_snr  # noqa: E402
from meshplay.sim.p1812 import _J, tl_p1812  # noqa: E402


def test_single_knife_edge_j0():
    # ITU-R P.526 approximation gives 6.03 dB at nu = 0 (exact value 6.02 dB).
    assert _J(0.0) == pytest.approx(6.0329, abs=1e-3)
    assert _J(-0.78) == 0.0


def test_free_space_1km_flat_high_antennas():
    d = np.linspace(0, 1.0, 101)
    h = np.zeros_like(d)
    out = tl_p1812(0.868, 50.0, d, h, htg=100.0, hrg=100.0, full=True)
    assert out["Lbfs"] == pytest.approx(91.17, abs=0.01)


def test_reciprocity():
    rng = np.random.default_rng(3)
    d = np.linspace(0, 0.8, 401)
    h = 55 + np.cumsum(rng.normal(0, 0.2, len(d)))
    r = np.where(rng.random(len(d)) < 0.3, rng.uniform(5, 25, len(d)), 0.0)
    r[0] = r[-1] = 0
    fwd = tl_p1812(0.868, 50.0, d, h, r, htg=3.5, hrg=17.0, phi_path=50.74)
    rev = tl_p1812(
        0.868, 50.0, d[-1] - d[::-1], h[::-1], r[::-1], htg=17.0, hrg=3.5, phi_path=50.74
    )
    assert fwd == pytest.approx(rev, abs=1e-6)


def test_noise_floor_and_per():
    # kTB at 250 kHz is -120 dBm; +6 dB noise figure gives -114 dBm.
    assert noise_floor_dbm(250e3, 6.0) == pytest.approx(-114.0, abs=0.05)
    assert per_from_snr(-10.0, -10.0) == pytest.approx(0.5)
    assert per_from_snr(0.0, -10.0) < 1e-5
