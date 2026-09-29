"""Monte Carlo prediction of Meshtastic links: several model families on identical draws.

Ported from the Mesh Bonn project (11_Simulation_ITU/rf/predict.py, 2026-09-20). The order of
random draws is unchanged, so a run with the same seed and profiles reproduces the original
predictions exactly (see tests/test_sim_regression.py).

Model families (basic transmission loss Lb):
  M1_P1812        ITU-R P.1812-6 over the LiDAR profile (terrain + buildings/trees as edges)
  M1b_P1812_clut  as M1, but clutter within 50 m of the ends as terminal loss, eq. (64)
                  (pessimistic bracket)
  M2_P1812_bare   P.1812-6 over bare terrain + statistical clutter loss P.2108-1
  M3_Bullington   free space + delta-Bullington diffraction over the surface
  M4_P1411        ITU-R P.1411-12 street-level model (only valid up to 660 m)
  M5_LogDist      empirical log-distance, exponent n as prior
  M6_P1812_P833   buildings as diffraction edges, vegetation as attenuation (ITU-R P.833-10)
  ENS             per-draw median over the valid families

The spread between the families is the honest measure of uncertainty before measuring.
"""

from __future__ import annotations

import numpy as np

from meshplay.config import DEFAULT_PRESET
from meshplay.sim.itu import (
    MESHTASTIC_PRESETS,
    P833_GAMMA,
    SNR_MIN_DB,
    bel_p2109,
    cl_loss_p1812,
    cl_p2108_terrestrial,
    noise_floor_dbm,
    per_from_snr,
    tl_p1411_lowheight,
    veg_loss_p833,
)
from meshplay.sim.p1812 import dl_delta_bull, earth_rad_eff, smooth_earth_heights, tl_p1812

F_GHZ = 0.868
F_MHZ = 868.0
PHI = 50.74  # latitude of the paths (Bonn)

MODEL_DESCRIPTIONS = {
    "M1_P1812": "ITU-R P.1812-6 over LiDAR profile (terrain + buildings as diffraction edges)",
    "M1b_P1812_clut": "P.1812-6, 50 m near range as terminal clutter loss eq. (64), not edges",
    "M2_P1812_bare": "P.1812-6 over bare terrain + P.2108-1 clutter",
    "M3_Bullington": "free space + delta-Bullington diffraction (P.526/P.452 core)",
    "M6_P1812_P833": "buildings as diffraction edges, vegetation as attenuation (ITU-R P.833-10)",
    "M4_P1411": "ITU-R P.1411-12 short range (only <= 660 m)",
    "M5_LogDist": "empirical log-distance",
    "ENS": "per-draw median of the valid families",
}

# Priors are uniform ranges (a, b) unless a single number. [BELEG?] = no verified source.
PRIORS = dict(
    p_tx_dbm=(20.0, 22.0),  # SX1262 conducted, datasheet max. 22 dBm
    g_ant_dbi=(-2.0, 2.0),  # whip antenna in real mounting [BELEG?]
    g_t1000e_dbi=(-4.0, 0.0),  # T1000-E internal antenna [BELEG?]
    loss_feed_db=(0.3, 1.5),  # connector, mismatch per side [BELEG?]
    nf_db=(5.0, 8.0),  # SX1262 receiver noise figure [BELEG?]
    noise_rise_db=(0.0, 6.0),  # urban noise above thermal [BELEG?]
    fading_sigma_db=4.0,  # packet-to-packet fading, urban NLoS [BELEG?]
    p2109_loc_pct=(2.0, 30.0),  # location percentile in P.2109: device right at the window
    window_open_db=(1.0, 6.0),  # open window: frame, reveal, oblique incidence [BELEG?]
    n_logdist=(2.6, 3.8),
    sigma_logdist_db=7.0,
    sigmaL_wa_m=50.0,  # reference area of location variability, P.1812 eq. (66)
)


def _u(rng, ab, n):
    return rng.uniform(ab[0], ab[1], n)


def valid_models(dtot_km: float) -> list[str]:
    models = [
        "M1_P1812",
        "M1b_P1812_clut",
        "M6_P1812_P833",
        "M2_P1812_bare",
        "M3_Bullington",
        "M5_LogDist",
    ]
    if dtot_km * 1000 <= 660:
        models.insert(5, "M4_P1411")
    return models


def prep_profile(link: dict):
    """Profile dict (d in m, ground/surface heights, clutter class) -> P.1812 inputs."""
    d = np.asarray(link["d"], float) / 1000.0
    h = np.asarray(link["ground"], float)
    r = np.maximum(np.asarray(link["surface"], float) - h, 0.0)
    zone = 4 * np.ones(len(d), int)
    clutter = np.asarray(link["clutter"])
    ct = np.where(clutter == 1, 5, np.where(clutter == 2, 4, 2))
    return d, h, r, ct, zone


def prep_channels(link: dict):
    """Separate channels: building heights and vegetation heights above terrain."""
    h = np.asarray(link["ground"], float)
    rb = np.maximum(np.asarray(link["surface_bld"], float) - h, 0.0)
    rv = np.maximum(np.asarray(link["surface_veg"], float) - h, 0.0)
    return rb, rv


def veg_path_len(d_km, h, rb, rv, ha, hb, step_m, gap=3):
    """Vegetation depth directly at the two terminals [m].

    ITU-R P.833-10 section 4 models exactly this case: a terminal stands in or behind a stand of
    depth d, the rest of the path is clear. Only that section is counted; further out the field
    is already diffracted, P.833 does not apply there and would count twice. Counting continues
    while the direct ray is below the canopy and no building cuts it; up to `gap` stations
    without vegetation are bridged.
    """
    length = d_km[-1]
    z = (h[0] + ha) + ((h[-1] + hb) - (h[0] + ha)) * d_km / length
    inveg = (rv > 2.5) & (z < h + rv)
    blocked = (rb > 2.5) & (z < h + rb)
    n = len(d_km)
    skip = max(1, int(round(8.0 / step_m)))  # skip the node's own building

    def walk(indices):
        t = 0
        miss = 0
        for i in indices:
            if blocked[i]:
                break
            if inveg[i]:
                t += 1
                miss = 0
            else:
                miss += 1
                if miss > gap:
                    break
        return t

    total = walk(range(skip, n - 1)) + walk(range(n - 1 - skip, 0, -1))
    return float(total * step_m)


def sigma_loc(f_ghz, h_ant, r_local, wa):
    """Location variability, P.1812-6 eq. (66)."""
    s = (0.52 + 0.024 * f_ghz) * wa**0.28
    if h_ant < r_local:
        uh = 1.0
    elif h_ant >= r_local + 10:
        uh = 0.0
    else:
        uh = 1 - (h_ant - r_local) / 10
    return s * uh


def run_link(
    d,
    h,
    r,
    ct,
    zone,
    htg_ab,
    hrg_ab,
    n,
    rng,
    r_tx=0.0,
    r_rx=0.0,
    cheap_grid=9,
    rb=None,
    rv=None,
    leaf="belaubt",
):
    """Basic transmission loss Lb per model family, arrays of length n.

    P.1812 is expensive, so it is evaluated on a grid of antenna heights (htg_ab, hrg_ab are
    uniform prior ranges in m above ground) and interpolated bilinearly (error < 0.1 dB).
    r_tx/r_rx are the local clutter heights at the terminals.
    """
    dtot = d[-1] - d[0]
    ht = _u(rng, htg_ab, n)
    hr = _u(rng, hrg_ab, n)
    gt = np.linspace(htg_ab[0], htg_ab[1], cheap_grid)
    gr = np.linspace(hrg_ab[0], hrg_ab[1], cheap_grid)

    def interp(m):
        fi = np.interp(ht, gt, np.arange(cheap_grid))
        fj = np.interp(hr, gr, np.arange(cheap_grid))
        i0 = np.clip(fi.astype(int), 0, cheap_grid - 2)
        j0 = np.clip(fj.astype(int), 0, cheap_grid - 2)
        a = fi - i0
        b = fj - j0
        return (
            m[i0, j0] * (1 - a) * (1 - b)
            + m[i0 + 1, j0] * a * (1 - b)
            + m[i0, j0 + 1] * (1 - a) * b
            + m[i0 + 1, j0 + 1] * a * b
        )

    def grid(fn):
        out = np.empty((cheap_grid, cheap_grid))
        for i, a in enumerate(gt):
            for j, b in enumerate(gr):
                out[i, j] = fn(a, b)
        return out

    def p1812(profile_r, a, b, full=False):
        return tl_p1812(
            F_GHZ, 50.0, d, h, profile_r, ct, zone, htg=a, hrg=b, pol=2, phi_path=PHI, full=full
        )

    # M1: P.1812 over the full surface profile
    lb_grid = np.empty((cheap_grid, cheap_grid))
    lfs_grid = np.empty_like(lb_grid)
    for i, a in enumerate(gt):
        for j, b in enumerate(gr):
            o = p1812(r, a, b, full=True)
            lb_grid[i, j] = o["Lb"]
            lfs_grid[i, j] = o["Lbfs"]
    lb_p1812 = interp(lb_grid)
    lfs = interp(lfs_grid)

    # M2: terrain only + statistical clutter P.2108
    r0 = np.zeros_like(r)
    lb2 = grid(lambda a, b: p1812(r0, a, b))
    pl = rng.uniform(5, 95, n)
    lb_bare = interp(lb2) + cl_p2108_terrestrial(F_GHZ, dtot, pl)

    # M3: free space + pure delta-Bullington diffraction over the surface
    ae, _ = earth_rad_eff(45.0)
    g = (h + r).copy()
    g[0] = h[0]
    g[-1] = h[-1]

    def bullington(a, b):
        s = smooth_earth_heights(d, h, r, a, b, ae, F_GHZ)
        ld = dl_delta_bull(d, g, h[0] + a, h[-1] + b, s["hstd"], s["hsrd"], ae, F_GHZ, 0.0)[0]
        return ld[1]

    lb_bull = lfs + interp(grid(bullington))

    # M1b: clutter within 50 m of the ends as terminal clutter loss eq. (64), not as edges
    near = (d - d[0] < 0.050) | (d[-1] - d < 0.050)
    rc = r.copy()
    rc[near] = 0.0
    lb1b = grid(lambda a, b: p1812(rc, a, b))
    ah_t = np.array([cl_loss_p1812(a, r_tx, 5, F_GHZ) for a in gt])
    ah_r = np.array([cl_loss_p1812(b, r_rx, 5, F_GHZ) for b in gr])
    lb_clut = interp(lb1b) + np.interp(ht, gt, ah_t) + np.interp(hr, gr, ah_r)

    # M6: buildings as diffraction edges, vegetation as attenuation (P.833-10). For a ray below
    # the canopy through the trunk space the edge is the wrong picture: the crown attenuates.
    lb_veg = None
    if rv is not None:
        lb6 = np.empty((cheap_grid, cheap_grid))
        dv_grid = np.empty((cheap_grid, cheap_grid))
        step_m = (d[1] - d[0]) * 1000.0
        for i, a in enumerate(gt):
            for j, b in enumerate(gr):
                lb6[i, j] = p1812(rb, a, b)
                dv_grid[i, j] = veg_path_len(d, h, rb, rv, a, b, step_m)
        gmin, gmax = P833_GAMMA[leaf]
        gam = rng.uniform(gmin, gmax, n)
        lb_veg = interp(lb6) + veg_loss_p833(interp(dv_grid), F_MHZ * 1.0, gamma=gam)

    # M4: P.1411-12 (valid < 660 m)
    lb_1411 = tl_p1411_lowheight(F_MHZ, dtot * 1000.0, 3, rng.uniform(5, 95, n))

    # M5: log-distance
    nexp = _u(rng, PRIORS["n_logdist"], n)
    d0 = 1.0
    lb_log = (
        20 * np.log10(4 * np.pi * d0 / (2.998e8 / (F_MHZ * 1e6)))
        + 10 * nexp * np.log10(dtot * 1000.0 / d0)
        + rng.normal(0, PRIORS["sigma_logdist_db"], n)
    )

    # Location variability, P.1812 eq. (66), on all physical models
    sl_t = sigma_loc(F_GHZ, ht.mean(), r_tx, PRIORS["sigmaL_wa_m"])
    sl_r = sigma_loc(F_GHZ, hr.mean(), r_rx, PRIORS["sigmaL_wa_m"])
    s_l = np.hypot(sl_t, sl_r)
    dl = rng.normal(0, s_l, n)
    out = dict(
        M1_P1812=lb_p1812 + dl,
        M1b_P1812_clut=lb_clut + dl,
        M2_P1812_bare=lb_bare + dl,
        M3_Bullington=lb_bull + dl,
        M4_P1411=lb_1411,
        M5_LogDist=lb_log,
        _ht=ht,
        _hr=hr,
        _Lfs=lfs,
        _sigmaL=s_l,
        _dtot_km=dtot,
    )
    if lb_veg is not None:
        out["M6_P1812_P833"] = lb_veg + dl
        out["_d_veg_m"] = float(np.median(interp(dv_grid)))
    return out


def add_ensemble(models: dict) -> list[str]:
    """Add the ENS entry (per-draw median of the valid families); return the valid list."""
    valid = valid_models(models["_dtot_km"])
    models["ENS"] = np.median(np.vstack([models[m] for m in valid]), axis=0)
    return valid


def budget(lb, rng, n, dev="p1pro", preset=DEFAULT_PRESET, tx_indoor=None, rx_indoor=None, npkt=10):
    """Received level, SNR and delivery from the basic transmission loss.

    dev: "t1000e" uses the T1000-E antenna prior on the second terminal.
    tx_indoor / rx_indoor: None (outdoor/antenna outside), "open" (open window),
    "trad" (closed, traditional glazing), "lowe" (closed, low-E glazing).
    """
    p = MESHTASTIC_PRESETS[preset]
    ptx = _u(rng, PRIORS["p_tx_dbm"], n)
    g1 = _u(rng, PRIORS["g_ant_dbi"], n)
    g2 = _u(rng, PRIORS["g_t1000e_dbi" if dev == "t1000e" else "g_ant_dbi"], n)
    lf = _u(rng, PRIORS["loss_feed_db"], n) + _u(rng, PRIORS["loss_feed_db"], n)
    lin = np.zeros(n)
    for side in (tx_indoor, rx_indoor):
        if side is None:
            continue
        if side == "open":
            lin += _u(rng, PRIORS["window_open_db"], n)
        else:
            cl = 1 if side == "trad" else 2
            lin += bel_p2109(F_GHZ, _u(rng, PRIORS["p2109_loc_pct"], n), cl, theta_deg=0.0)
    prx_mean = ptx + g1 + g2 - lf - lb - lin
    nf = _u(rng, PRIORS["nf_db"], n) + _u(rng, PRIORS["noise_rise_db"], n)
    noise = noise_floor_dbm(p["bw"], nf)
    fad = rng.normal(0, PRIORS["fading_sigma_db"], (npkt, n))
    prx = prx_mean[None, :] + fad
    snr = prx - noise[None, :]
    per = per_from_snr(snr, SNR_MIN_DB[p["sf"]])
    ok = rng.random((npkt, n)) > per
    return dict(
        prx=prx_mean,
        prx_pkt=prx,
        snr=snr.mean(axis=0),
        snr_pkt=snr,
        ok_pkt=ok,
        N=noise,
        delivered=ok.sum(axis=0),
        p_any=(ok.any(axis=0)).mean(),
        p_all=(ok.all(axis=0)).mean(),
        sens=noise + SNR_MIN_DB[p["sf"]],
    )
