"""3D scene from the NRW laser scan (3D-Messdaten, product 3dm_l_las): terrain, surface,
building/vegetation masks, and path profiles for the propagation models.

Ported from the Mesh Bonn project (geo/scene.py, geo/classify.py, geo/profil3.py and
11_Simulation_ITU/site/search.py, 2026-09-20).

LAS classes in this product (Geobasis NRW, "Nutzerinformationen 3D-Messdaten"):
  1 unclassified (incl. vegetation) | 2 ground | 18 noise | 20 last return not ground
  24 cellar point | 26 synthetic ground point
There is NO building or vegetation class. Buildings and trees are separated per 1 m cell by
two physical features (see classify()).

A scene is a directory with scene_raw.npz, scene_cls2.npz and scene_meta.json (the layout of
the original export/ folder, so an existing export can be used as is).
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

GROUND = (2, 26)
ABOVE = (1, 20)
H_MAX_TREE = 32.0  # above this height a cell is always a building [BELEG? - local knowledge]
TILE_URL = "https://www.opengeodata.nrw.de/produkte/geobasis/hm/3dm_l_las/3dm_l_las/"


def tile_name(e_km: int, n_km: int) -> str:
    return f"3dm_32_{e_km}_{n_km}_1_nw.laz"


def tiles_for_bbox(bbox) -> list[str]:
    """Names of the 1 km UTM32 tiles covering bbox = (xmin, ymin, xmax, ymax) in EPSG:25832."""
    e0, n0 = math.floor(bbox[0] / 1000), math.floor(bbox[1] / 1000)
    e1, n1 = math.floor((bbox[2] - 1e-6) / 1000), math.floor((bbox[3] - 1e-6) / 1000)
    return [tile_name(e, n) for n in range(n1, n0 - 1, -1) for e in range(e0, e1 + 1)]


# ---------------------------------------------------------------- building from LAZ
def rasterize_laz(bbox, res, laz_dir, chunk=2_000_000) -> dict:
    """Per-cell DSM (max), DTM (min of ground points) and point counts from LAZ tiles.

    nab: points above ground (classes 1, 20); n20: of those class 20; nmul: of those with more
    than one return. Noise (18) and cellar points (24) are dropped.
    """
    import laspy

    nx = int(np.ceil((bbox[2] - bbox[0]) / res))
    ny = int(np.ceil((bbox[3] - bbox[1]) / res))
    dsm = np.full(nx * ny, -1e9)
    dtm = np.full(nx * ny, 1e9)
    n20 = np.zeros(nx * ny, np.int32)
    nab = np.zeros(nx * ny, np.int32)
    nmul = np.zeros(nx * ny, np.int32)
    total = 0
    for path in sorted(Path(laz_dir).glob("*.laz")):
        with laspy.open(path) as f:
            h = f.header
            if (
                h.maxs[0] < bbox[0]
                or h.mins[0] > bbox[2]
                or h.maxs[1] < bbox[1]
                or h.mins[1] > bbox[3]
            ):
                continue
            for ch in f.chunk_iterator(chunk):
                x = np.asarray(ch.x)
                y = np.asarray(ch.y)
                z = np.asarray(ch.z)
                c = np.asarray(ch.classification)
                nr = np.asarray(ch.number_of_returns)
                m = (
                    (x >= bbox[0])
                    & (x < bbox[2])
                    & (y >= bbox[1])
                    & (y < bbox[3])
                    & (c != 18)
                    & (c != 24)
                )
                if not m.any():
                    continue
                x, y, z, c, nr = x[m], y[m], z[m], c[m], nr[m]
                total += len(x)
                idx = ((y - bbox[1]) / res).astype(np.int64) * nx + ((x - bbox[0]) / res).astype(
                    np.int64
                )
                g = np.isin(c, GROUND)
                a = np.isin(c, ABOVE)
                np.maximum.at(dsm, idx, z)
                if g.any():
                    np.minimum.at(dtm, idx[g], z[g])
                np.add.at(nab, idx[a], 1)
                s = c == 20
                if s.any():
                    np.add.at(n20, idx[s], 1)
                mm = a & (nr > 1)
                if mm.any():
                    np.add.at(nmul, idx[mm], 1)
    dsm = np.where(dsm < -1e8, np.nan, dsm).reshape(ny, nx)
    dtm = np.where(dtm > 1e8, np.nan, dtm).reshape(ny, nx)
    return dict(
        dsm=dsm,
        dtm=dtm,
        n20=n20.reshape(ny, nx),
        nab=nab.reshape(ny, nx),
        nmul=nmul.reshape(ny, nx),
        n_points=total,
    )


def fill_nan(a, iters=200):
    """Fill gaps by repeatedly averaging the 4 neighbours (up to `iters` cells deep)."""
    a = a.copy()
    for _ in range(iters):
        m = ~np.isfinite(a)
        if not m.any():
            break
        p = np.pad(a, 1, constant_values=np.nan)
        st = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN neighbourhoods
            nb = np.nanmean(st, axis=0)
        upd = m & np.isfinite(nb)
        if not upd.any():
            a[m] = np.nanmedian(a)
            break
        a[upd] = nb[upd]
    return a


def classify(nd, nab, n20, nmul, hmin=2.5):
    """Separate buildings from vegetation per 1 m cell.

    fm: share of points with more than one return. A pulse hitting a tree crown is reflected
        several times by leaves and branches; a roof usually gives exactly one return.
    f2: share of class 20 (last return not ground). Roofs give almost only last returns.
    Checked against ALKIS footprints (buildings) and a tree-lined street strip (trees).
    """
    above = nd > hmin
    fm = np.where(nab > 0, nmul / np.maximum(nab, 1), 0.5)
    f2 = np.where(nab > 0, n20 / np.maximum(nab, 1), 0.5)
    score = 2.0 * (0.55 - fm) + 1.2 * (f2 - 0.35)
    bld = above & (score > 0)
    bld = ndimage.uniform_filter(bld.astype(np.float32), 5) > 0.5
    bld &= above
    bld |= above & (nd > H_MAX_TREE)
    bld = ndimage.binary_closing(bld, np.ones((3, 3)))
    bld = ndimage.binary_opening(bld, np.ones((3, 3)))
    bld &= above
    return bld, above & ~bld


# ---------------------------------------------------------------- scene
@dataclass
class Scene:
    dtm: np.ndarray  # terrain height [m NHN]
    nd: np.ndarray  # height of the surface above terrain [m] (nDOM)
    bld: np.ndarray  # building mask
    veg: np.ndarray  # vegetation mask
    measured: np.ndarray  # cells with laser points (False = interpolated or outside tiles)
    bbox: tuple[float, float, float, float]  # EPSG:25832
    res: float

    @property
    def shape(self):
        return self.dtm.shape

    @classmethod
    def build(cls, laz_dir, bbox, res=1.0) -> Scene:
        raw = rasterize_laz(bbox, res, laz_dir)
        measured = np.isfinite(raw["dsm"])
        dtm = fill_nan(raw["dtm"])
        dsm = fill_nan(raw["dsm"])
        nd = np.nan_to_num(np.maximum(dsm - dtm, 0.0), nan=0.0)
        bld, veg = classify(nd, raw["nab"], raw["n20"], raw["nmul"])
        scene = cls(dtm, nd, bld, veg, measured, tuple(bbox), res)
        scene._raw = dict(dsm=dsm, n20=raw["n20"], nab=raw["nab"], nmul=raw["nmul"])
        scene.n_points = raw["n_points"]
        return scene

    @classmethod
    def load(cls, directory) -> Scene:
        directory = Path(directory)
        raw = np.load(directory / "scene_raw.npz")
        cls2 = np.load(directory / "scene_cls2.npz")
        meta = json.loads((directory / "scene_meta.json").read_text(encoding="utf-8"))
        dtm = raw["dtm"].astype(float)
        # The original export has no coverage mask; there, NaN marks cells never reached.
        measured = raw["measured"] if "measured" in raw.files else np.isfinite(dtm)
        nd = np.nan_to_num(raw["nd"].astype(float), nan=0.0)
        return cls(dtm, nd, cls2["bld"], cls2["veg"], measured, tuple(meta["bbox"]), meta["res"])

    def save(self, directory) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        extra = getattr(self, "_raw", {})
        np.savez_compressed(
            directory / "scene_raw.npz",
            dtm=self.dtm.astype(np.float32),
            nd=self.nd.astype(np.float32),
            measured=self.measured,
            **{k: (v.astype(np.float32) if v.dtype.kind == "f" else v) for k, v in extra.items()},
        )
        np.savez_compressed(directory / "scene_cls2.npz", bld=self.bld, veg=self.veg)
        meta = dict(bbox=list(self.bbox), res=self.res, nx=self.shape[1], ny=self.shape[0])
        if hasattr(self, "n_points"):
            meta["n_points"] = self.n_points
        (directory / "scene_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def contains(self, x, y, margin=0.0) -> bool:
        b = self.bbox
        return b[0] + margin <= x <= b[2] - margin and b[1] + margin <= y <= b[3] - margin

    def _cell(self, x, y):
        i = int((y - self.bbox[1]) / self.res)
        j = int((x - self.bbox[0]) / self.res)
        return i, j

    def ground_at(self, x, y) -> float:
        return float(self.dtm[self._cell(x, y)])

    def max_height_near(self, x, y, radius=6.0) -> float:
        """Highest surface above terrain within `radius` m (e.g. the roof around a node)."""
        i, j = self._cell(x, y)
        k = int(np.ceil(radius / self.res))
        win = self.nd[max(i - k, 0) : i + k + 1, max(j - k, 0) : j + k + 1]
        return float(win.max())

    def measured_fraction(self, a_utm, b_utm, step=5.0) -> float:
        """Share of the straight path a->b over cells that have laser points."""
        a, b = np.asarray(a_utm, float), np.asarray(b_utm, float)
        n = max(2, int(np.hypot(*(b - a)) / step) + 1)
        pts = a[None, :] + np.linspace(0, 1, n)[:, None] * (b - a)[None, :]
        ii = np.clip(((pts[:, 1] - self.bbox[1]) / self.res).astype(int), 0, self.shape[0] - 1)
        jj = np.clip(((pts[:, 0] - self.bbox[0]) / self.res).astype(int), 0, self.shape[1] - 1)
        return float(self.measured[ii, jj].mean())

    def profile(self, a_utm, b_utm, step=2.0, corr=6.0, core=3.0) -> dict:
        """Path profile with separate building and vegetation channels (geo/profil3.py).

        Per station: ground (median DTM across +-corr m), surface (highest object within
        +-core m), surface_bld / surface_veg (highest building / vegetation), clutter
        (0 clear, 1 building, 2 vegetation). d in m from a.
        """
        a = np.asarray(a_utm, float)
        b = np.asarray(b_utm, float)
        length = float(np.hypot(*(b - a)))
        u = (b - a) / length
        q = np.array([-u[1], u[0]])
        ny, nx = self.shape
        bbox, res = self.bbox, self.res
        dtm, nd, bld, veg = self.dtm, self.nd, self.bld, self.veg
        n = int(length // step) + 1
        dd = np.arange(n) * step
        p = a[None, :] + dd[:, None] * u[None, :]
        off = np.arange(-corr, corr + 0.5, 1.0)
        offc = np.arange(-core, core + 0.5, 1.0)
        g = np.empty(n)
        sa = np.empty(n)
        sb = np.empty(n)
        sv = np.empty(n)
        cl = np.zeros(n, int)
        for t in range(n):
            for kind, o in ((0, off), (1, offc)):
                pts = p[t][None, :] + o[:, None] * q[None, :]
                ii = ((pts[:, 1] - bbox[1]) / res).astype(int)
                jj = ((pts[:, 0] - bbox[0]) / res).astype(int)
                ok = (ii >= 0) & (ii < ny) & (jj >= 0) & (jj < nx)
                ii, jj = ii[ok], jj[ok]
                if kind == 0:
                    g[t] = np.median(dtm[ii, jj])
                    continue
                hb = np.where(bld[ii, jj], nd[ii, jj], 0.0)
                hv = np.where(veg[ii, jj], nd[ii, jj], 0.0)
                ha = nd[ii, jj]
                kb, kv, ka = int(np.argmax(hb)), int(np.argmax(hv)), int(np.argmax(ha))
                sb[t] = dtm[ii[kb], jj[kb]] + hb[kb] if hb[kb] > 0 else g[t]
                sv[t] = dtm[ii[kv], jj[kv]] + hv[kv] if hv[kv] > 0 else g[t]
                sa[t] = max(dtm[ii[ka], jj[ka]] + ha[ka], g[t])
                cl[t] = 1 if hb[kb] > 2.5 else (2 if hv[kv] > 2.5 else 0)
            sb[t] = max(sb[t], g[t])
            sv[t] = max(sv[t], g[t])
        if not np.isfinite(g).all():
            raise ValueError("path leaves the scene data (no terrain heights); add LAZ tiles")
        return dict(
            L=length,
            az=float(np.degrees(np.arctan2(u[0], u[1])) % 360),
            d=dd.tolist(),
            ground=g.tolist(),
            surface=sa.tolist(),
            surface_bld=sb.tolist(),
            surface_veg=sv.tolist(),
            clutter=cl.tolist(),
            a_utm=list(map(float, a)),
            b_utm=list(map(float, b)),
        )

    def quick_profile(self, a_utm, b_utm, step=5.0, corr=3.0):
        """Coarse profile for large searches (site/search.py): 3 samples across the path.

        Returns (length m, d km, ground m, clutter height above ground m).
        """
        ax, ay = a_utm
        bx, by = b_utm
        length = float(np.hypot(bx - ax, by - ay))
        n = max(6, int(length / step) + 1)
        t = np.linspace(0, 1, n)
        px = ax + (bx - ax) * t
        py = ay + (by - ay) * t
        ux, uy = (bx - ax) / length, (by - ay) / length
        qx, qy = -uy, ux
        offs = np.array([-corr, 0.0, corr])
        x = px[:, None] + qx * offs[None, :]
        y = py[:, None] + qy * offs[None, :]
        ny, nx = self.shape
        jj = np.clip(((x - self.bbox[0]) / self.res).astype(np.int32), 0, nx - 1)
        ii = np.clip(((y - self.bbox[1]) / self.res).astype(np.int32), 0, ny - 1)
        g = np.median(self.dtm[ii, jj], axis=1)
        s = np.max(self.dtm[ii, jj] + self.nd[ii, jj], axis=1)
        r = np.maximum(s - g, 0.0)
        return length, t * length / 1000.0, g.astype(float), r.astype(float)
