"""Data for the 3D view: terrain raster, building/vegetation bodies and link geometry.

Ported from the Mesh Bonn session (geo/extract_polys.py and the 3D view's data export). The body
parameters reproduce the original export exactly (4202 buildings, 3754 vegetation bodies on the
2026-09-20 scene).
"""

from __future__ import annotations

import json
import struct
import warnings
from pathlib import Path

import numpy as np

from meshplay.sim.models import PHI, prep_channels, prep_profile, veg_path_len
from meshplay.sim.p1812 import _J, tl_p1812
from meshplay.sim.scene import Scene, fill_nan

TERRAIN_RES = 4.0  # m
CLASS_NAMES = {1: "Gebäude", 2: "Bewuchs"}
# Parameters that reproduce the original export (4202 building, 3754 vegetation bodies):
# height band, minimum area m², simplification tolerance m.
BODY_PARAMS = {1: dict(step=3.0, min_area=30.0, tol=1.2), 2: dict(step=5.0, min_area=40.0, tol=2.0)}


# ---------------------------------------------------------------- scene bodies
def extract_bodies(nd, dtm, sel, bbox, res, step=3.0, min_area=25.0, tol=1.2, hmin=2.5):
    """LoD1-like bodies from the nDOM: per object and height band (step m), outline by marching
    squares, simplified. Returns dicts with xy (UTM), h (75th percentile height above
    terrain) and g (median terrain height). From geo/extract_polys.py."""
    from scipy import ndimage
    from shapely import simplify
    from shapely.geometry import Polygon
    from skimage import measure

    lev = np.where(sel, np.floor(np.maximum(nd, hmin) / step).astype(np.int16), -1)
    out = []
    for level in range(int(lev.max()) + 1):
        m = lev == level
        if m.sum() < min_area / (res * res):
            continue
        m = ndimage.binary_closing(m, np.ones((3, 3)))
        lab, n = ndimage.label(m)
        if n == 0:
            continue
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        slices = ndimage.find_objects(lab)
        for i in range(1, n + 1):
            if sizes[i - 1] * res * res < min_area:
                continue
            sl = slices[i - 1]
            pad = 2
            y0, y1 = max(sl[0].start - pad, 0), min(sl[0].stop + pad, m.shape[0])
            x0, x1 = max(sl[1].start - pad, 0), min(sl[1].stop + pad, m.shape[1])
            reg = lab[y0:y1, x0:x1] == i
            contours = measure.find_contours(reg.astype(float), 0.5)
            if not contours:
                continue
            c = max(contours, key=len)
            if len(c) < 4:
                continue
            xy = np.c_[(x0 + c[:, 1] + 0.5) * res + bbox[0], (y0 + c[:, 0] + 0.5) * res + bbox[1]]
            p = Polygon(xy)
            if not p.is_valid:
                p = p.buffer(0)
            if p.is_empty or p.area < min_area:
                continue
            p = simplify(p, tol, preserve_topology=True)
            if p.geom_type != "Polygon" or p.area < min_area:
                continue
            g = float(np.median(dtm[y0:y1, x0:x1][reg]))
            if not np.isfinite(g):
                continue  # inside a data gap without terrain heights: not a real body
            out.append(
                dict(
                    xy=np.asarray(p.exterior.coords)[:-1],
                    h=float(np.percentile(nd[y0:y1, x0:x1][reg], 75)),
                    g=g,
                )
            )
    return out


def write_bodies(path: Path, bodies: list[tuple[int, dict]], bbox, z0: float) -> None:
    """Binary layout read by the viewer: header uint32 n, uint32 n_buildings; per body
    uint16 n_vertices, uint8 class, uint8 pad, int16 base, int16 top (dm above z0), then
    n_vertices x (int32, int32) in dm relative to the scene origin."""
    n_bld = sum(1 for cls, _ in bodies if cls == 1)
    parts = [struct.pack("<II", len(bodies), n_bld)]
    for cls, b in bodies:
        xy = np.round((b["xy"] - np.array(bbox[:2])) * 10).astype(np.int32)
        base = int(round((b["g"] - z0) * 10))
        top = int(round((b["g"] + b["h"] - z0) * 10))
        parts.append(struct.pack("<HBBhh", len(xy), cls, 0, base, top))
        parts.append(xy.astype("<i4").tobytes())
    path.write_bytes(b"".join(parts))


def terrain_raster(scene: Scene):
    """Terrain on a 4 m grid as int16 decimetres above z0 (block mean of the 1 m DTM)."""
    k = int(TERRAIN_RES / scene.res)
    ny, nx = scene.shape[0] // k, scene.shape[1] // k
    dtm = scene.dtm[: ny * k, : nx * k].reshape(ny, k, nx, k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # blocks without data
        t = np.nanmean(dtm, axis=(1, 3))
    t = fill_nan(t)
    t = np.where(np.isfinite(t), t, np.nanmin(t))
    z0 = float(np.floor(t.min()))
    return np.round((t - z0) * 10).astype("<i2"), z0, nx, ny


def node_info(scene: Scene, utm) -> dict:
    """Terrain height and roof height around a node (75th pct of building cells within 6 m)."""
    i, j = scene._cell(*utm)
    win = (slice(max(i - 6, 0), i + 7), slice(max(j - 6, 0), j + 7))
    roof = scene.nd[win][scene.bld[win]]
    return dict(
        grund_nhn=float(scene.dtm[i, j]),
        dach_umkreis6m=float(np.percentile(roof, 75)) if roof.size else 0.0,
    )


# ---------------------------------------------------------------- link geometry
def link_geometry(prof: dict, ha: float, hb: float, f_mhz=868.0, k=4 / 3) -> dict:
    d = np.asarray(prof["d"], float)
    ground = np.asarray(prof["ground"], float)
    surface = np.asarray(prof["surface"], float)
    clutter = np.asarray(prof["clutter"])
    length = d[-1]
    lam = 299792458.0 / (f_mhz * 1e6)
    za, zb = ground[0] + ha, ground[-1] + hb
    los = za + (zb - za) * d / length
    r1 = np.sqrt(lam * d * (length - d) / length)
    h = surface + d * (length - d) / (2 * 6371000 * k)  # earth bulge
    with np.errstate(divide="ignore", invalid="ignore"):
        clear = (los - h) / r1
        nu = (h - los) * np.sqrt(2 / lam * (1 / d + 1 / (length - d)))
    inner = np.zeros(len(d), bool)
    inner[1:-1] = True
    edges, run = [], []
    for i in range(len(d)):
        if inner[i] and nu[i] > -0.78:
            run.append(i)
        elif run:
            edges.append(max(run, key=lambda j: nu[j]))
            run = []
    edges.sort(key=lambda j: -nu[j])
    d_km, gh, r, ct, zone = prep_profile(prof)
    rb, rv = prep_channels(prof)
    lb = float(tl_p1812(0.868, 50.0, d_km, gh, r, ct, zone, htg=ha, hrg=hb, pol=2, phi_path=PHI))
    lb_bld = float(
        tl_p1812(0.868, 50.0, d_km, gh, rb, ct, zone, htg=ha, hrg=hb, pol=2, phi_path=PHI)
    )
    return dict(
        h_a_m=ha,
        h_b_m=hb,
        z_a_nhn=float(za),
        z_b_nhn=float(zb),
        edges=[
            dict(
                d_m=float(d[j]),
                z_nhn=float(surface[j]),
                h_agl=float(surface[j] - ground[j]),
                nu=float(nu[j]),
                J_dB=float(_J(nu[j])),
                art=CLASS_NAMES.get(int(clutter[j]), "Gelände"),
            )
            for j in edges[:5]
        ],
        r1_m=[float(v) for v in np.nan_to_num(r1)],
        clear_r1=[None if not np.isfinite(v) else float(v) for v in clear],
        los_nhn=los.tolist(),
        min_clear_r1=float(np.nanmin(clear[1:-1])),
        Lb_ref=lb,
        Lb_ohne_bewuchs=lb_bld,
        d_veg_m=veg_path_len(d_km, gh, rb, rv, ha, hb, float(d[1] - d[0])),
    )


def export_scene(scene: Scene, out_dir: Path) -> dict:
    """Write scene_meta.json, terrain.i16 and bodies.bin for the viewer; return the metadata."""
    out_dir.mkdir(parents=True, exist_ok=True)
    terr, z0, nx, ny = terrain_raster(scene)
    (out_dir / "terrain.i16").write_bytes(terr.tobytes())
    bodies = []
    for cls, sel in ((1, scene.bld), (2, scene.veg)):
        found = extract_bodies(scene.nd, scene.dtm, sel, scene.bbox, scene.res, **BODY_PARAMS[cls])
        bodies += [(cls, b) for b in found]
    write_bodies(out_dir / "bodies.bin", bodies, scene.bbox, z0)
    meta = dict(
        bbox=list(scene.bbox),
        res_terrain=TERRAIN_RES,
        nx=nx,
        ny=ny,
        z0=z0,
        scale_z=0.1,
        scale_xy=0.1,
        n_bodies=len(bodies),
        n_buildings=sum(1 for cls, _ in bodies if cls == 1),
        measured_share=float(scene.measured.mean()),
        quelle="NRW 3D-Messdaten Laserscanning 3dm_l_las, Geobasis NRW, dl-de/zero-2-0",
    )
    (out_dir / "scene_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta
