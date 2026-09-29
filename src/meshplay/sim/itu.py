"""Supporting ITU-R models, noise and LoRa parameters for 868 MHz links.

Ported from the Mesh Bonn project (11_Simulation_ITU/rf/mesh.py, 2026-09-20); numerics unchanged.

  P.2109-2  building entry loss
  P.2108-1  statistical clutter loss (comparison model)
  P.1411-12 short-range street-level model (valid below ~660 m)
  P.1812-6  terminal clutter loss, eq. (64)
  P.833-10  vegetation attenuation
  noise floor (kTB + noise figure), SX1262 demodulation limits, Meshtastic presets

Values marked [BELEG?] in the original are assumptions without a verified source.
"""

from __future__ import annotations

import numpy as np
from scipy.special import erfinv

C0 = 299792458.0


def finv(p):
    """Inverse standard normal CDF for p in percent (as in ITU-R P.1057)."""
    return np.sqrt(2) * erfinv(2 * np.asarray(p, float) / 100.0 - 1)


# ---------------------------------------------------------- P.2109-2
_P2109 = {
    1: dict(r=12.64, s=3.72, t=0.96, u=9.6, v=2.0, w=9.1, x=-3.0, y=4.5, z=-2.0),
    2: dict(r=28.19, s=-3.00, t=8.48, u=13.5, v=3.8, w=27.8, x=-2.9, y=9.4, z=-2.1),
}


def bel_p2109(f_ghz, p_loc, cl, theta_deg=0.0):
    """Building entry loss [dB], ITU-R P.2109-2.

    cl=1 traditional building, cl=2 thermally efficient (low-E glazing).
    """
    c = _P2109[cl]
    lf = np.log10(f_ghz)
    le = 0.212 * np.abs(theta_deg)  # (10)
    lh = c["r"] + c["s"] * lf + c["t"] * lf**2  # (9)
    mu1 = lh + le  # (5)
    mu2 = c["w"] + c["x"] * lf  # (6)
    s1 = c["u"] + c["v"] * lf  # (7)
    s2 = c["y"] + c["z"] * lf  # (8)
    f = finv(p_loc)
    a = mu1 + s1 * f
    b = mu2 + s2 * f
    cc = -3.0  # (4)
    return 10 * np.log10(10 ** (0.1 * a) + 10 ** (0.1 * b) + 10 ** (0.1 * cc))


# ---------------------------------------------------------- P.2108-1 (terrestrial)
def cl_p2108_terrestrial(f_ghz, d_km, p_loc):
    """Statistical clutter loss for terrestrial paths, ITU-R P.2108-1 section 3.2."""
    d = max(d_km, 0.25)
    ll = -2.0 * np.log10(10 ** (-5.0 * np.log10(f_ghz) - 12.5) + 10 ** (-16.5))  # (4a)
    ls = 32.98 + 23.9 * np.log10(d) + 3 * np.log10(f_ghz)  # (5a)
    sl, ss = 4.0, 6.0
    scb = np.sqrt(
        (sl**2 * 10 ** (-0.2 * ll) + ss**2 * 10 ** (-0.2 * ls))
        / (10 ** (-0.2 * ll) + 10 ** (-0.2 * ls))
    )  # (3b)
    return -5 * np.log10(10 ** (-0.2 * ll) + 10 ** (-0.2 * ls)) - scb * finv(100 - p_loc)  # (3a)


# ---------------------------------------------------------- P.1411-12 (low antenna heights)
def tl_p1411_lowheight(f_mhz, d_m, typ, p_loc, w=20.0):
    """Propagation between street-level terminals, ITU-R P.1411-12 section 4.3.

    typ: 1 suburban, 2 urban, 3 dense urban / high-rise.
    """
    l_urban = {1: 0.0, 2: 6.8, 3: 2.3}[typ]
    l_los_m = 32.45 + 20 * np.log10(f_mhz) + 20 * np.log10(d_m / 1000.0)
    sigma = 7.0
    dl_los = 1.5624 * sigma * (np.sqrt(-2 * np.log(1 - p_loc / 100.0)) - 1.1774)
    l_los = l_los_m + dl_los
    l_nlos_m = 9.5 + 45 * np.log10(f_mhz) + 40 * np.log10(d_m / 1000.0) + l_urban
    l_nlos = l_nlos_m + finv(p_loc) * sigma
    lp = np.log10(p_loc / 100.0)
    d_los = np.where(p_loc < 45, 212 * lp**2 - 64 * lp, 79.2 - 70 * (p_loc / 100.0))
    out = np.where(d_m < d_los, l_los, l_nlos)
    mid = (d_m >= d_los) & (d_m <= d_los + w)
    if np.any(mid):
        l1 = 32.45 + 20 * np.log10(f_mhz) + 20 * np.log10(d_los / 1000.0) + dl_los
        l2 = (
            9.5
            + 45 * np.log10(f_mhz)
            + 40 * np.log10((d_los + w) / 1000.0)
            + l_urban
            + finv(p_loc) * sigma
        )
        out = np.where(mid, l1 + (l2 - l1) * (d_m - d_los) / w, out)
    return out


# ---------------------------------------------------------- noise and LoRa
def noise_floor_dbm(bw_hz, nf_db, t_kelvin=290.0):
    """Thermal noise at the receiver input, kTB plus noise figure."""
    k = 1.380649e-23
    return 10 * np.log10(k * t_kelvin * bw_hz * 1000.0) + nf_db


# Demodulation limits of the LoRa modem, Semtech SX1262 datasheet, table "LoRa Sensitivity".
SNR_MIN_DB = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}

# meshtastic.org/docs/overview/radio-settings
MESHTASTIC_PRESETS = {
    "ShortTurbo": dict(sf=7, bw=500e3, cr="4/5"),
    "ShortFast": dict(sf=7, bw=250e3, cr="4/5"),
    "ShortSlow": dict(sf=8, bw=250e3, cr="4/5"),
    "MediumFast": dict(sf=9, bw=250e3, cr="4/5"),
    "MediumSlow": dict(sf=10, bw=250e3, cr="4/5"),
    "LongFast": dict(sf=11, bw=250e3, cr="4/5"),
    "LongModerate": dict(sf=11, bw=125e3, cr="4/8"),
    "LongSlow": dict(sf=12, bw=125e3, cr="4/8"),
}


def per_from_snr(snr_db, snr_min_db, width_db=1.6):
    """Packet error rate as a soft threshold around the demodulation limit.

    The width is an assumption (LoRa PER curves are steep over ~2 dB) [BELEG?].
    """
    return 1.0 / (1.0 + np.exp((snr_db - snr_min_db) / (width_db / 2.2)))


def cl_loss_p1812(h_ant, r_clut, ct, f_ghz, ws=27.0):
    """Terminal clutter loss Ah, ITU-R P.1812-6 eq. (64c)-(64g).

    Applies when the antenna sits below the representative clutter height.
    """
    if h_ant >= r_clut:
        return 0.0
    if ct in (3, 4, 5):
        k_nu = 0.342 * np.sqrt(f_ghz)  # (64g)
        hdif = r_clut - h_ant  # (64d)
        th = np.degrees(np.arctan(hdif / ws))  # (64e)
        nu = k_nu * np.sqrt(hdif * th)  # (64c)
        if nu <= -0.78:
            return 0.0
        return 6.9 + 20 * np.log10(np.sqrt((nu - 0.1) ** 2 + 1) + nu - 0.1) - 6.03  # (12)
    kh2 = 21.8 + 6.2 * np.log10(f_ghz)  # (64f)
    return -kh2 * np.log10(h_ant / r_clut)


# ---------------------------------------------------------- P.833-10 vegetation
# Aev = Am [1 - exp(-d*gamma/Am)], Am = A1 * f^alpha (f in MHz), ITU-R P.833-10 eq. (1).
# Measurement campaigns in the recommendation: France (Mulhouse, 900-2200 MHz) A1=1.15,
# alpha=0.43; Russia (105.9-2117.5 MHz, mixed forest) A1=1.37, alpha=0.42.
P833_AM = {"fr": (1.15, 0.43), "ru": (1.37, 0.42)}
# Specific attenuation gamma [dB/m] near 900 MHz, read off figure 2 of the recommendation [BELEG?].
P833_GAMMA = {"belaubt": (0.14, 0.20), "unbelaubt": (0.10, 0.16)}


def veg_loss_p833(d_m, f_mhz=868.0, gamma=0.16, coef="fr"):
    a1, alpha = P833_AM[coef]
    am = a1 * f_mhz**alpha
    return am * (1.0 - np.exp(-np.asarray(d_m, float) * gamma / am))
