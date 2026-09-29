import numpy as np
import pytest

from meshplay.sim.compare import fit_distance_exponent, signal_power_dbm, summarize


def test_signal_power_removes_noise_share():
    assert signal_power_dbm(-100, 0) == pytest.approx(-103.01, abs=0.01)
    assert signal_power_dbm(-110, -10) == pytest.approx(-120.41, abs=0.01)
    assert signal_power_dbm(-80, 20) == pytest.approx(-80.04, abs=0.01)


def test_fit_distance_exponent():
    d = np.array([100, 200, 300, 450, 600, 750])
    n, sigma = fit_distance_exponent(d, -40 - 30 * np.log10(d))
    assert n == pytest.approx(3.0)
    assert sigma == pytest.approx(0.0, abs=1e-9)


def test_weights_only_compare_models_on_the_same_packets():
    rows = [
        {
            "A__resid": 1.0,
            "A__logscore": -3.0,
            "A__hit80": True,
            "B__resid": 0.0,
            "B__logscore": -2.0,
            "B__hit80": True,
        },
        {"A__resid": -1.0, "A__logscore": -3.0, "A__hit80": True},
    ]
    s = summarize(rows, ["A", "B"])
    assert s["A"]["n_signal"] == 2 and s["B"]["n_signal"] == 1
    assert s["A"]["weight"] == pytest.approx(1.0)
    assert "weight" not in s["B"]
