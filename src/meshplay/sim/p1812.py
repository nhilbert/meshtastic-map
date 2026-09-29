# Copied unchanged from the Mesh Bonn project (11_Simulation_ITU/rf/p1812.py, 2026-09-20).
# Validated against the 63 ITU-R WP 3K test profiles (max. deviation 5e-8 dB), see
# scripts/sim_validate_p1812.py. Keep the numerics as they are; ruff formatting is off for this file.
"""Portierung der ITU-R-Referenzimplementierung von Recommendation ITU-R P.1812-6
(Punkt-zu-Flaeche-Ausbreitung 30 MHz - 6 GHz) nach Python.

Quelle des Originals: MATLAB/Octave-Referenzcode von ITU-R WP 3K,
https://github.com/eeveetza/p1812  (Version 6.1, 25.04.23), veroeffentlicht
auf der ITU-R-SG-3-Seite "Software, Data and Validation".
Diese Portierung ist gegen die mitgelieferten Validierungsprofile geprueft
(siehe validate.py) â€” sie reproduziert die Referenzwerte.

Gleichungsnummern beziehen sich auf ITU-R P.1812-6.
"""
from __future__ import annotations
import numpy as np


# ---------------------------------------------------------------- Hilfsgroessen
def earth_rad_eff(DN):
    k50 = 157.0 / (157.0 - DN)          # (6)
    return 6371.0 * k50, 6371.0 * 3.0   # (7a), (7b)


def beta0(phi, dtm, dlm):
    tau = 1 - np.exp(-(4.12e-4 * dlm ** 2.41))                       # (3)
    mu1 = (10 ** (-dtm / (16 - 6.6 * tau)) + 10 ** (-5 * (0.496 + 0.354 * tau))) ** 0.2   # (2)
    mu1 = min(mu1, 1.0)
    if abs(phi) <= 70:
        mu4 = mu1 ** (-0.935 + 0.0176 * abs(phi))                    # (4)
        return 10 ** (-0.015 * abs(phi) + 1.67) * mu1 * mu4          # (5)
    mu4 = mu1 ** 0.3
    return 4.17 * mu1 * mu4


def _intervals(mask):
    m = np.asarray(mask).astype(int)
    if m.max(initial=0) != 1:
        return np.array([], int), np.array([], int)
    d1 = np.diff(np.concatenate(([0], m)))
    d2 = np.diff(np.concatenate((m, [0])))
    return np.where(d1 == 1)[0], np.where(d2 == -1)[0]


def longest_cont_dist(d, zone, zone_r):
    sel = ((zone == 3) | (zone == 4)) if zone_r == 34 else (zone == zone_r)
    k1, k2 = _intervals(sel)
    dm = 0.0
    for a, b in zip(k1, k2):
        delta = 0.0
        if d[b] < d[-1]:
            delta += (d[b + 1] - d[b]) / 2.0
        if d[a] > 0:
            delta += (d[a] - d[a - 1]) / 2.0
        dm = max(d[b] - d[a] + delta, dm)
    return dm


def path_fraction(d, zone, zone_r):
    k1, k2 = _intervals(zone == zone_r)
    dm = 0.0
    for a, b in zip(k1, k2):
        delta = 0.0
        if d[b] < d[-1]:
            delta += (d[b + 1] - d[b]) / 2.0
        if d[a] > 0:
            delta += (d[a] - d[a - 1]) / 2.0
        dm += d[b] - d[a] + delta
    return dm / (d[-1] - d[0])


def inv_cum_norm(x):
    x = min(max(x, 1e-6), 0.999999)
    def T(y):  return np.sqrt(-2 * np.log(y))                        # (97a)
    def Cf(z):
        t = T(z)
        return (((0.010328 * t + 0.802853) * t) + 2.515516698) / \
               (((0.001308 * t + 0.189269) * t + 1.432788) * t + 1)  # (97b)
    return T(x) - Cf(x) if x <= 0.5 else -(T(1 - x) - Cf(1 - x))     # (96a,b)


# ---------------------------------------------------------------- Freiraum
def pl_los(d, hts, hrs, f, p, b0, dlt, dlr):
    dfs = np.sqrt(d ** 2 + ((hts - hrs) / 1000.0) ** 2)              # (8a)
    Lbfs = 92.4 + 20 * np.log10(f) + 20 * np.log10(dfs)              # (8)
    E = 2.6 * (1 - np.exp(-0.1 * (dlt + dlr)))
    return Lbfs, Lbfs + E * np.log10(p / 50.0), Lbfs + E * np.log10(b0 / 50.0)   # (10),(11)


# ---------------------------------------------------------------- Beugung
def _J(nu):
    return 6.9 + 20 * np.log10(np.sqrt((nu - 0.1) ** 2 + 1) + nu - 0.1) if nu > -0.78 else 0.0  # (12)


def dl_bull(d, g, hts, hrs, ap, f):
    Ce = 1.0 / ap
    lam = 0.2998 / f
    dtot = d[-1] - d[0]
    di = d[1:-1]; gi = g[1:-1]
    Stim = np.max((gi + 500 * Ce * di * (dtot - di) - hts) / di)     # (13)
    Str = (hrs - hts) / dtot                                         # (14)
    if Stim < Str:
        numax = np.max((gi + 500 * Ce * di * (dtot - di) - (hts * (dtot - di) + hrs * di) / dtot)
                       * np.sqrt(0.002 * dtot / (lam * di * (dtot - di))))   # (15)
        Luc = _J(numax)                                              # (16)
    else:
        Srim = np.max((gi + 500 * Ce * di * (dtot - di) - hrs) / (dtot - di))   # (17)
        dbp = (hrs - hts + Srim * dtot) / (Stim + Srim)              # (18)
        nub = (hts + Stim * dbp - (hts * (dtot - dbp) + hrs * dbp) / dtot) \
              * np.sqrt(0.002 * dtot / (lam * dbp * (dtot - dbp)))   # (19)
        Luc = _J(nub)                                                # (20)
    return Luc + (1 - np.exp(-Luc / 6.0)) * (10 + 0.02 * dtot)       # (21)


def dl_bull_att4(dtot, hte, hre, ap, f):
    Ce = 1.0 / ap; lam = 0.2998 / f
    dlos = np.sqrt(2 * ap) * (np.sqrt(0.001 * hte) + np.sqrt(0.001 * hre))   # (22)
    if dtot < dlos:
        c = (hte - hre) / (hte + hre)                                # (24d)
        m = 250 * dtot * dtot / (ap * (hte + hre))                   # (24e)
        b = 2 * np.sqrt((m + 1) / (3 * m)) * np.cos(np.pi / 3 + 1 / 3 * np.arccos(
            3 * c / 2 * np.sqrt(3 * m / ((m + 1) ** 3))))            # (24c)
        dse1 = dtot / 2 * (1 + b); dse2 = dtot - dse1
        hse = ((hte - 500 * dse1 ** 2 / ap) * dse2 + (hre - 500 * dse2 ** 2 / ap) * dse1) / dtot   # (23)
        numax = -hse * np.sqrt(0.002 * dtot / (lam * dse1 * (dtot - dse1)))    # (105)
        Lus = _J(numax)
    else:
        Stm = 500 * Ce * dtot - 2 * np.sqrt(500.0 * Ce * hte)        # (107)
        Srm = 500 * Ce * dtot - 2 * np.sqrt(500.0 * Ce * hre)        # (108)
        ds = (hre - hte + Srm * dtot) / (Stm + Srm)                  # (109)
        nus = (hte + Stm * ds - (hte * (dtot - ds) + hre * ds) / dtot) \
              * np.sqrt(0.002 * dtot / (lam * ds * (dtot - ds)))     # (110)
        Lus = _J(nus)
    return Lus + (1 - np.exp(-Lus / 6.0)) * (10 + 0.02 * dtot)       # (112)


def _dl_se_ft_inner(epsr, sigma, d, hte, hre, adft, f):
    K = np.empty(2)
    K[0] = 0.036 * (adft * f) ** (-1 / 3) * ((epsr - 1) ** 2 + (18 * sigma / f) ** 2) ** (-0.25)  # (29a)
    K[1] = K[0] * (epsr ** 2 + (18 * sigma / f) ** 2) ** 0.5                                      # (29b)
    beta = (1 + 1.6 * K ** 2 + 0.67 * K ** 4) / (1 + 4.5 * K ** 2 + 1.53 * K ** 4)                # (30)
    X = 21.88 * beta * (f / adft ** 2) ** (1 / 3) * d                                             # (31)
    Yt = 0.9575 * beta * (f ** 2 / adft) ** (1 / 3) * hte                                         # (32a)
    Yr = 0.9575 * beta * (f ** 2 / adft) ** (1 / 3) * hre                                         # (32b)
    Fx = np.where(X >= 1.6, 11 + 10 * np.log10(np.maximum(X, 1e-30)) - 17.6 * X,
                  -20 * np.log10(np.maximum(X, 1e-30)) - 5.6488 * X ** 1.425)                     # (33)
    def G(B):
        g = np.where(B > 2, 17.6 * np.sqrt(np.maximum(B - 1.1, 0)) - 5 * np.log10(np.maximum(B - 1.1, 1e-30)) - 8,
                     20 * np.log10(np.maximum(B + 0.1 * B ** 3, 1e-30)))                          # (34),(35)
        return np.maximum(g, 2 + 20 * np.log10(K))
    return -Fx - G(beta * Yt) - G(beta * Yr)                                                      # (36)


def dl_se_ft(d, hte, hre, adft, f, omega):
    land = _dl_se_ft_inner(22.0, 0.003, d, hte, hre, adft, f)
    sea = _dl_se_ft_inner(80.0, 5.0, d, hte, hre, adft, f)
    return omega * sea + (1 - omega) * land                                                       # (28)


def dl_se(d, hte, hre, ap, f, omega):
    lam = 0.2998 / f
    dlos = np.sqrt(2 * ap) * (np.sqrt(0.001 * hte) + np.sqrt(0.001 * hre))                        # (22)
    if d >= dlos:
        return dl_se_ft(d, hte, hre, ap, f, omega)
    c = (hte - hre) / (hte + hre)                                                                 # (24d)
    m = 250 * d * d / (ap * (hte + hre))                                                          # (24e)
    b = 2 * np.sqrt((m + 1) / (3 * m)) * np.cos(np.pi / 3 + 1 / 3 * np.arccos(
        3 * c / 2 * np.sqrt(3 * m / ((m + 1) ** 3))))                                             # (24c)
    dse1 = d / 2 * (1 + b); dse2 = d - dse1
    hse = ((hte - 500 * dse1 ** 2 / ap) * dse2 + (hre - 500 * dse2 ** 2 / ap) * dse1) / d         # (23)
    hreq = 17.456 * np.sqrt(dse1 * dse2 * lam / d)                                                # (25)
    if hse > hreq:
        return np.zeros(2)
    aem = 500 * (d / (np.sqrt(hte) + np.sqrt(hre))) ** 2                                          # (26)
    Ldft = dl_se_ft(d, hte, hre, aem, f, omega)
    Ldft = np.maximum(Ldft, 0)
    return (1 - hse / hreq) * Ldft                                                                # (27)


def dl_delta_bull(d, g, hts, hrs, hstd, hsrd, ap, f, omega, flag4=0):
    Lbulla = dl_bull(d, g, hts, hrs, ap, f)
    hts1 = hts - hstd                                                                             # (37a)
    hrs1 = hrs - hsrd                                                                             # (37b)
    dtot = d[-1] - d[0]
    Lbulls = dl_bull_att4(dtot, hts1, hrs1, ap, f) if flag4 else dl_bull(d, np.zeros_like(g), hts1, hrs1, ap, f)
    Ldsph = dl_se(dtot, hts1, hrs1, ap, f, omega)
    return Lbulla + np.maximum(Ldsph - Lbulls, 0), Lbulla, Lbulls, Ldsph                          # (39)


def dl_p(d, g, hts, hrs, hstd, hsrd, f, omega, p, b0, DN, flag4=0):
    ae, ab = earth_rad_eff(DN)
    Ld50, Lbulla50, Lbulls50, Ldsph50 = dl_delta_bull(d, g, hts, hrs, hstd, hsrd, ae, f, omega, flag4)
    Ldb = dl_delta_bull(d, g, hts, hrs, hstd, hsrd, ab, f, omega, flag4)[0]
    if p == 50:
        return Ld50, Ldb, Ld50, Lbulla50, Lbulls50, Ldsph50
    Fi = inv_cum_norm(p / 100) / inv_cum_norm(b0 / 100) if p > b0 else 1.0                        # (40a)
    return Ld50 + Fi * (Ldb - Ld50), Ldb, Ld50, Lbulla50, Lbulls50, Ldsph50                       # (41)


# ---------------------------------------------------------------- Gelaendegroessen
def smooth_earth_heights(d, h, R, htg, hrg, ae, f):
    n = len(d); dtot = d[-1]
    hts = h[0] + htg; hrs = h[-1] + hrg
    dd = np.diff(d)
    v1 = np.sum(dd * (h[1:] + h[:-1]))                                                            # (85)
    v2 = np.sum(dd * (h[1:] * (2 * d[1:] + d[:-1]) + h[:-1] * (d[1:] + 2 * d[:-1])))              # (86)
    hst = (2 * v1 * dtot - v2) / dtot ** 2                                                        # (87)
    hsr = (v2 - v1 * dtot) / dtot ** 2                                                            # (88)
    hst_n, hsr_n = hst, hsr
    HH = h - (hts * (dtot - d) + hrs * d) / dtot                                                  # (89d)
    hobs = np.max(HH[1:n - 1])                                                                    # (89a)
    a_obt = np.max(HH[1:n - 1] / d[1:n - 1])                                                      # (89b)
    a_obr = np.max(HH[1:n - 1] / (dtot - d[1:n - 1]))                                             # (89c)
    gt = a_obt / (a_obt + a_obr); gr = a_obr / (a_obt + a_obr)                                    # (90e,f)
    if hobs <= 0:
        hstp, hsrp = hst, hsr                                                                     # (90a,b)
    else:
        hstp = hst - hobs * gt; hsrp = hsr - hobs * gr                                            # (90c,d)
    hstd = h[0] if hstp >= h[0] else hstp                                                         # (91a,b)
    hsrd = h[-1] if hsrp > h[-1] else hsrp                                                        # (91c,d)
    ii = np.arange(1, n - 1)
    theta = 1000 * np.arctan((h[ii] - hts) / (1000 * d[ii]) - d[ii] / (2 * ae))                   # (77)
    theta_td = 1000 * np.arctan((hrs - hts) / (1000 * dtot) - dtot / (2 * ae))                    # (78)
    theta_rd = 1000 * np.arctan((hts - hrs) / (1000 * dtot) - dtot / (2 * ae))                    # (81)
    theta_max = np.max(theta)                                                                     # (76)
    pathtype = 2 if theta_max > theta_td else 1                                                   # (150)
    theta_t = max(theta_max, theta_td)                                                            # (79)
    if pathtype == 2:
        lt = int(np.where(theta == theta_max)[0][0]) + 1
        dlt = d[lt]                                                                               # (80)
        th2 = 1000 * np.arctan((h[ii] - hrs) / (1000 * (dtot - d[ii])) - (dtot - d[ii]) / (2 * ae))  # (82a)
        theta_r = np.max(th2)
        lr = int(np.where(th2 == theta_r)[0][-1]) + 1
        dlr = dtot - d[lr]                                                                        # (83)
    else:
        theta_r = theta_rd
        lam = 0.2998 / f; Ce = 1.0 / ae
        nu = (h[ii] + 500 * Ce * d[ii] * (dtot - d[ii]) - (hts * (dtot - d[ii]) + hrs * d[ii]) / dtot) \
             * np.sqrt(0.002 * dtot / (lam * d[ii] * (dtot - d[ii])))
        numax = np.max(nu)
        lt = int(np.where(nu == numax)[0][-1]) + 1
        dlt = d[lt]; dlr = dtot - dlt; lr = lt
    theta_tot = 1e3 * dtot / ae + theta_t + theta_r                                               # (84)
    hst = min(hst, h[0]); hsr = min(hsr, h[-1])                                                   # (92a,b)
    m = (hsr - hst) / dtot                                                                        # (93)
    hte = htg + h[0] - hst                                                                        # (94a)
    hre = hrg + h[-1] - hsr                                                                       # (94b)
    jj = np.arange(lt, lr + 1)
    hm = np.max(h[jj] - (hst + m * d[jj]))                                                        # (95)
    return dict(hst_n=hst_n, hsr_n=hsr_n, hst=hst, hsr=hsr, hstd=hstd, hsrd=hsrd, hte=hte,
                hre=hre, hm=hm, dlt=dlt, dlr=dlr, theta_t=theta_t, theta_r=theta_r,
                theta=theta_tot, pathtype=pathtype)


# ---------------------------------------------------------------- Ducting / Troposcatter
def tl_anomalous(dtot, dlt, dlr, dct, dcr, dlm, hts, hrs, hte, hre, hm,
                 theta_t, theta_r, f, p, omega, ae, b0):
    Alf = 45.375 - 137.0 * f + 92.5 * f * f if f < 0.5 else 0.0
    tt2 = theta_t - 0.1 * dlt; tr2 = theta_r - 0.1 * dlr                                          # (48a)
    Ast = 20 * np.log10(1 + 0.361 * tt2 * np.sqrt(f * dlt)) + 0.264 * tt2 * f ** (1 / 3) if tt2 > 0 else 0.0
    Asr = 20 * np.log10(1 + 0.361 * tr2 * np.sqrt(f * dlr)) + 0.264 * tr2 * f ** (1 / 3) if tr2 > 0 else 0.0
    Act = Acr = 0.0
    if dct <= 5 and dct <= dlt and omega >= 0.75:
        Act = -3 * np.exp(-0.25 * dct * dct) * (1 + np.tanh(0.07 * (50 - hts)))
    if dcr <= 5 and dcr <= dlr and omega >= 0.75:
        Acr = -3 * np.exp(-0.25 * dcr * dcr) * (1 + np.tanh(0.07 * (50 - hrs)))
    gamma_d = 5e-5 * ae * f ** (1 / 3)
    tt1 = min(theta_t, 0.1 * dlt); tr1 = min(theta_r, 0.1 * dlr)
    theta1 = 1e3 * dtot / ae + tt1 + tr1
    dI = min(dtot - dlt - dlr, 40)                                                                # (56a)
    mu3 = np.exp(-4.6e-5 * (hm - 10) * (43 + 6 * dI)) if hm > 10 else 1.0                         # (56)
    tau = 1 - np.exp(-(4.12e-4 * dlm ** 2.41))                                                    # (3)
    alpha = max(-0.6 - 3.5e-9 * dtot ** 3.1 * tau, -3.4)                                          # (55a)
    mu2 = min((500 / ae * dtot ** 2 / (np.sqrt(hte) + np.sqrt(hre)) ** 2) ** alpha, 1.0)          # (55)
    beta = b0 * mu2 * mu3                                                                         # (54)
    Gamma = 1.076 / (2.0058 - np.log10(beta)) ** 1.012 * np.exp(
        -(9.51 - 4.8 * np.log10(beta) + 0.198 * np.log10(beta) ** 2) * 1e-6 * dtot ** 1.13)       # (53a)
    Ap = -12 + (1.2 + 3.7e-3 * dtot) * np.log10(p / beta) + 12 * (p / beta) ** Gamma              # (53)
    Adp = gamma_d * theta1 + Ap                                                                   # (50)
    Af = 102.45 + 20 * np.log10(f) + 20 * np.log10(dlt + dlr) + Alf + Ast + Asr + Act + Acr       # (49)
    return Af + Adp


def tl_tropo(dtot, theta, f, p, N0):
    Lf = 25 * np.log10(f) - 2.5 * (np.log10(f / 2)) ** 2                                          # (45)
    return 190.1 + Lf + 20 * np.log10(dtot) + 0.573 * theta - 0.15 * N0 - 10.125 * (np.log10(50 / p)) ** 0.7  # (44)


# ---------------------------------------------------------------- Hauptfunktion
def tl_p1812(f, p, d, h, R=None, Ct=None, zone=None, htg=1.5, hrg=1.5, pol=2,
             phi_path=50.0, pL=50.0, sigmaL=0.0, DN=45.0, N0=325.0,
             dct=500.0, dcr=500.0, flag4=0, full=False):
    """Basisuebertragungsdaempfung Lb [dB] nach ITU-R P.1812-6.
    f [GHz], p [%], d [km], h [m ue. NN], R [m Bewuchshoehe], htg/hrg [m ue. Grund],
    pol: 1 horizontal, 2 vertikal."""
    d = np.asarray(d, float); h = np.asarray(h, float)
    R = np.zeros_like(h) if R is None else np.asarray(R, float)
    Ct = 2 * np.ones_like(h, int) if Ct is None else np.asarray(Ct, int)
    zone = 4 * np.ones_like(h, int) if zone is None else np.asarray(zone, int)
    if zone[0] == 1: dct = 0.0
    if zone[-1] == 1: dcr = 0.0
    dtm = longest_cont_dist(d, zone, 34)
    dlm = longest_cont_dist(d, zone, 4)
    b0 = beta0(phi_path, dtm, dlm)
    ae, ab = earth_rad_eff(DN)
    omega = path_fraction(d, zone, 1)
    S = smooth_earth_heights(d, h, R, htg, hrg, ae, f)
    dtot = d[-1] - d[0]
    hts = h[0] + htg; hrs = h[-1] + hrg
    g = h + R; g[0] = h[0]; g[-1] = h[-1]
    Fj = 1.0 - 0.5 * (1.0 + np.tanh(3.0 * 0.8 * (S['theta'] - 0.3) / 0.3))                        # (57)
    Fk = 1.0 - 0.5 * (1.0 + np.tanh(3.0 * 0.5 * (dtot - 20) / 20))                                # (58)
    Lbfs, Lb0p, Lb0b = pl_los(dtot, hts, hrs, f, p, b0, S['dlt'], S['dlr'])
    Ldp, Ldb, Ld50, Lbulla50, Lbulls50, Ldsph50 = dl_p(
        d, g, hts, hrs, S['hstd'], S['hsrd'], f, omega, p, b0, DN, flag4)
    Lbd50 = Lbfs + Ld50
    Lbd = Lb0p + Ldp
    Lminb0p = Lb0p + (1 - omega) * Ldp
    Fi = 1.0
    if p >= b0:
        Fi = inv_cum_norm(p / 100) / inv_cum_norm(b0 / 100)
        Lminb0p = Lbd50 + (Lb0b + (1 - omega) * Ldp - Lbd50) * Fi                                 # (59)
    eta = 2.5
    Lba = tl_anomalous(dtot, S['dlt'], S['dlr'], dct, dcr, dlm, hts, hrs, S['hte'], S['hre'],
                       S['hm'], S['theta_t'], S['theta_r'], f, p, omega, ae, b0)
    Lminbap = eta * np.log(np.exp(Lba / eta) + np.exp(Lb0p / eta))                                # (60)
    Lbda = np.array(Lbd, float).copy()
    for k in (0, 1):
        if Lminbap <= Lbd[k]:
            Lbda[k] = Lminbap + (Lbd[k] - Lminbap) * Fk                                           # (61)
    Lbam = Lbda + (Lminb0p - Lbda) * Fj                                                           # (62)
    Lbs = tl_tropo(dtot, S['theta'], f, p, N0)
    Lbc_pol = -5 * np.log10(10 ** (-0.2 * Lbs) + 10 ** (-0.2 * Lbam))                             # (63)
    Lbc = Lbc_pol[pol - 1]
    Lloc = 0.0 if zone[-1] == 1 else -inv_cum_norm(pL / 100) * sigmaL                             # (67a)
    Lb = max(Lb0p, Lbc + Lloc)                                                                    # (69)
    if not full:
        return Lb
    out = dict(Lb=Lb, Lbfs=Lbfs, Lb0p=Lb0p, Lb0b=Lb0b, Ld50=Ld50[pol - 1], Ldp=Ldp[pol - 1],
               Ldb=Ldb[pol - 1], Lbulla=Lbulla50, Lbulls=Lbulls50, Ldsph=Ldsph50[pol - 1],
               Lbd50=Lbd50[pol - 1], Lbd=Lbd[pol - 1], Lminb0p=Lminb0p if np.isscalar(Lminb0p) else Lminb0p[pol - 1],
               Lba=Lba, Lminbap=Lminbap, Lbda=Lbda[pol - 1], Lbam=Lbam[pol - 1], Lbs=Lbs,
               Lbc=Lbc, Fi=Fi, Fj=Fj, Fk=Fk, b0=b0, ae=ae, omega=omega, dtm=dtm, dlm=dlm, **S)
    return out
