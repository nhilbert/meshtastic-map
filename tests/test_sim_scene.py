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


def profile_loop(s: Scene, a_utm, b_utm, step=2.0, corr=6.0, core=3.0) -> dict:
    """The original per-station profile (geo/profil3.py), kept as the reference."""
    a, b = np.asarray(a_utm, float), np.asarray(b_utm, float)
    length = float(np.hypot(*(b - a)))
    u = (b - a) / length
    q = np.array([-u[1], u[0]])
    ny, nx = s.shape
    n = int(length // step) + 1
    p = a[None, :] + (np.arange(n) * step)[:, None] * u[None, :]
    off, offc = np.arange(-corr, corr + 0.5, 1.0), np.arange(-core, core + 0.5, 1.0)
    g, sa, sb, sv, cl = np.empty(n), np.empty(n), np.empty(n), np.empty(n), np.zeros(n, int)
    for t in range(n):
        for kind, o in ((0, off), (1, offc)):
            pts = p[t][None, :] + o[:, None] * q[None, :]
            ii = ((pts[:, 1] - s.bbox[1]) / s.res).astype(int)
            jj = ((pts[:, 0] - s.bbox[0]) / s.res).astype(int)
            ok = (ii >= 0) & (ii < ny) & (jj >= 0) & (jj < nx)
            ii, jj = ii[ok], jj[ok]
            if kind == 0:
                g[t] = np.median(s.dtm[ii, jj])
                continue
            hb = np.where(s.bld[ii, jj], s.nd[ii, jj], 0.0)
            hv = np.where(s.veg[ii, jj], s.nd[ii, jj], 0.0)
            ha = s.nd[ii, jj]
            kb, kv, ka = int(np.argmax(hb)), int(np.argmax(hv)), int(np.argmax(ha))
            sb[t] = s.dtm[ii[kb], jj[kb]] + hb[kb] if hb[kb] > 0 else g[t]
            sv[t] = s.dtm[ii[kv], jj[kv]] + hv[kv] if hv[kv] > 0 else g[t]
            sa[t] = max(s.dtm[ii[ka], jj[ka]] + ha[ka], g[t])
            cl[t] = 1 if hb[kb] > 2.5 else (2 if hv[kv] > 2.5 else 0)
        sb[t] = max(sb[t], g[t])
        sv[t] = max(sv[t], g[t])
    return dict(ground=g, surface=sa, surface_bld=sb, surface_veg=sv, clutter=cl)


def test_profile_matches_the_original_loop():
    rng = np.random.default_rng(3)
    ny, nx = 300, 400
    dtm = 50 + np.cumsum(rng.normal(0, 0.3, (ny, nx)), axis=1)
    nd = np.where(rng.random((ny, nx)) < 0.3, rng.uniform(0, 25, (ny, nx)), 0.0)
    nd = np.round(nd, 0)  # ties: the first highest cell must win, as in the loop
    bld = (nd > 2.5) & (rng.random((ny, nx)) < 0.5)
    veg = (nd > 2.5) & ~bld
    scene = Scene(dtm, nd, bld, veg, np.ones_like(bld), (1000.0, 2000.0, 1400.0, 2300.0), 1.0)
    # inside, diagonal, and along the edge (part of the corridor outside the scene)
    for a, b in [
        ((1010, 2010), (1390, 2290)),
        ((1200, 2002), (1395, 2002)),
        ((1001, 2150), (1300, 2299)),
    ]:
        new, old = scene.profile(a, b), profile_loop(scene, a, b)
        for k, v in old.items():
            assert np.array_equal(np.asarray(new[k]), v), k
