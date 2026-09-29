import numpy as np
import pytest

pytest.importorskip("scipy")

from meshplay.sim.scene import Scene, classify, tiles_for_bbox  # noqa: E402


def make_scene() -> Scene:
    """100 x 60 m flat scene at 50 m NHN with one 20 m building across the middle."""
    ny, nx = 60, 100
    dtm = np.full((ny, nx), 50.0)
    nd = np.zeros((ny, nx))
    bld = np.zeros((ny, nx), bool)
    nd[:, 48:53] = 20.0
    bld[:, 48:53] = True
    return Scene(dtm, nd, bld, np.zeros_like(bld), np.ones_like(bld), (0.0, 0.0, 100.0, 60.0), 1.0)


def test_profile_sees_the_building():
    prof = make_scene().profile((5.0, 30.0), (95.0, 30.0))
    assert prof["L"] == pytest.approx(90.0)
    top = np.array(prof["surface_bld"])
    assert top.max() == pytest.approx(70.0)
    assert np.array(prof["clutter"]).max() == 1
    assert np.array(prof["ground"]) == pytest.approx(50.0)


def test_profile_outside_the_data_fails():
    scene = make_scene()
    scene.dtm[:, 80:] = np.nan
    with pytest.raises(ValueError):
        scene.profile((5.0, 30.0), (95.0, 30.0))


def test_classify_separates_roof_from_crown():
    nd = np.zeros((20, 40))
    nd[5:15, 2:15] = 15.0  # roof: single returns, class 20
    nd[5:15, 25:38] = 15.0  # crown: multiple returns, class 1
    nab = np.where(nd > 0, 10, 0)
    n20 = np.zeros_like(nab)
    n20[:, :20] = nab[:, :20]
    nmul = np.zeros_like(nab)
    nmul[:, 20:] = nab[:, 20:]
    bld, veg = classify(nd, nab, n20, nmul)
    assert bld[10, 8] and not veg[10, 8]
    assert veg[10, 31] and not bld[10, 31]


def test_tiles_for_bbox():
    assert tiles_for_bbox((365060, 5620920, 366280, 5623480)) == [
        "3dm_32_365_5623_1_nw.laz",
        "3dm_32_366_5623_1_nw.laz",
        "3dm_32_365_5622_1_nw.laz",
        "3dm_32_366_5622_1_nw.laz",
        "3dm_32_365_5621_1_nw.laz",
        "3dm_32_366_5621_1_nw.laz",
        "3dm_32_365_5620_1_nw.laz",
        "3dm_32_366_5620_1_nw.laz",
    ]
