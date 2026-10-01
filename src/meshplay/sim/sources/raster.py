"""Scenes from elevation rasters: a terrain model (DGM) and a surface model (DOM) per tile.

Where a state publishes no classified point cloud, the scene is built from its 1 m rasters: the
height above terrain is DOM minus DGM; buildings come from OpenStreetMap footprints, or, without
them, from the shape of the surface (roofs are smooth planes, tree crowns are rough); whatever
else stands above 2.5 m is vegetation. The rasters are EPSG:25832, north-up, on whole metres.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

import numpy as np
from scipy import ndimage

from meshplay.sim.scene import H_MAX_TREE, Scene, fill_nan

HMIN = 2.5  # above this, a cell is a building or vegetation (as for the laser scan)
# Without footprints, roofs are cells whose surface stays within ROUGH_MAX of the plane through
# its 3 x 3 neighbourhood (the 3 x 3 mean). Checked on a 1 km Hannover city tile against OSM
# footprints: finds 55 % of the building cells and takes 13 % of the tree cells for buildings,
# so footprints are the normal case and this is the fallback.
ROUGH_MAX = 0.4
OVERPASS = "https://overpass-api.de/api/interpreter"


# ---------------------------------------------------------------- reading
def read_geotiff(path: Path) -> tuple[np.ndarray, float, float, float]:
    """(values north-up with NaN for no data, x of the left edge, y of the top edge, cell size).

    The georeference comes from the GeoTIFF tags (tifffile), the pixels from Pillow, which
    decodes LZW without an extra package.
    """
    import tifffile
    from PIL import Image

    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        res = float(page.tags["ModelPixelScaleTag"].value[0])
        tie = page.tags["ModelTiepointTag"].value
        nodata = page.tags.get("GDAL_NODATA")
        nodata = float(nodata.value) if nodata is not None else None
    Image.MAX_IMAGE_PIXELS = None  # a 20 cm tile has 25 M pixels
    with Image.open(path) as im:
        a = np.asarray(im, dtype=np.float32).copy()
    if nodata is not None and np.isfinite(nodata):
        a[a == nodata] = np.nan
    a[a < -1000] = np.nan  # other no-data markers (-9999, -32767)
    return a, float(tie[3]), float(tie[4]), res


def read_xyz(path: Path) -> tuple[np.ndarray, float, float, float]:
    """The same from an ASCII grid of 'x y z' lines at the cell centres (1 m).

    Text after the numbers is cut off: Schleswig-Holstein's server appends a small HTML page
    ("Zurück zum OpenGBD-Downloadportal") to every file it delivers.
    """
    raw = Path(path).read_bytes()
    end = raw.find(b"<")
    v = np.array((raw if end < 0 else raw[:end]).split(), dtype=np.float64).reshape(-1, 3)
    res = 1.0
    x0, y1 = v[:, 0].min() - res / 2, v[:, 1].max() + res / 2
    w = int(round((v[:, 0].max() + res / 2 - x0) / res))
    h = int(round((y1 - (v[:, 1].min() - res / 2)) / res))
    a = np.full((h, w), np.nan, np.float32)
    a[((y1 - v[:, 1]) / res).astype(int), ((v[:, 0] - x0) / res).astype(int)] = v[:, 2]
    return a, float(x0), float(y1), res


def read_raster(path: Path):
    return read_xyz(path) if path.suffix.lower() == ".xyz" else read_geotiff(path)


# ---------------------------------------------------------------- mosaic
def coarsen(a: np.ndarray, k: int, how: str) -> np.ndarray:
    """Blocks of k x k cells to one: the highest for a surface, the mean for terrain."""
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    b = a[:h, :w].reshape(h // k, k, w // k, k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks stay NaN
        return np.nanmax(b, axis=(1, 3)) if how == "max" else np.nanmean(b, axis=(1, 3))


def mosaic(bbox, res: float, paths, how: str = "mean", on_file=None) -> np.ndarray:
    """The rasters in paths on the scene grid (row 0 = south, like Scene), NaN where none."""
    nx = int(round((bbox[2] - bbox[0]) / res))
    ny = int(round((bbox[3] - bbox[1]) / res))
    out = np.full((ny, nx), np.nan)
    for k, path in enumerate(paths, 1):
        if on_file:
            on_file(path, k)
        a, x0, y1, pres = read_raster(path)
        f = int(round(res / pres))
        if f > 1:
            a, pres = coarsen(a, f, how), pres * f
        a = a[::-1]  # north-up to south-up
        y0 = y1 - a.shape[0] * pres
        # overlap of the tile with the scene, in scene cells
        j0, i0 = int(round((x0 - bbox[0]) / res)), int(round((y0 - bbox[1]) / res))
        js, je = max(j0, 0), min(j0 + a.shape[1], nx)
        is_, ie = max(i0, 0), min(i0 + a.shape[0], ny)
        if js >= je or is_ >= ie:
            continue
        part = a[is_ - i0 : ie - i0, js - j0 : je - j0]
        dst = out[is_:ie, js:je]
        np.copyto(dst, part, where=np.isfinite(part))
    return out


# ---------------------------------------------------------------- buildings and vegetation
def classify_surface(nd, dsm, buildings=None, hmin=HMIN):
    """Building and vegetation masks from the height above terrain.

    buildings: footprint mask (OpenStreetMap) or None; without it, cells whose surface is
    nearly planar count as roof (see ROUGH_MAX).
    """
    above = nd > hmin
    if buildings is not None:
        bld = above & ndimage.binary_dilation(buildings, iterations=1)  # footprints vs eaves
    else:
        rough = ndimage.uniform_filter(np.abs(dsm - ndimage.uniform_filter(dsm, 3)), 3)
        bld = above & (rough < ROUGH_MAX)
        bld = ndimage.uniform_filter(bld.astype(np.float32), 5) > 0.5
    bld |= above & (nd > H_MAX_TREE)
    bld = ndimage.binary_closing(bld, np.ones((3, 3)))
    bld = ndimage.binary_opening(bld, np.ones((3, 3)))
    bld &= above
    return bld, above & ~bld


def scene_from_rasters(bbox, res, dgm_paths, dom_paths, buildings=None, on_file=None) -> Scene:
    """on_file(name, k, n) before each file is read (k from 1 over both lists)."""
    n = len(dgm_paths) + len(dom_paths)

    def counted(offset):
        return (lambda path, k: on_file(path.name, offset + k, n)) if on_file else None

    dtm_raw = mosaic(bbox, res, dgm_paths, "mean", counted(0))
    dsm_raw = mosaic(bbox, res, dom_paths, "max", counted(len(dgm_paths)))
    measured = np.isfinite(dtm_raw) & np.isfinite(dsm_raw)
    dtm = fill_nan(dtm_raw)
    dsm = fill_nan(dsm_raw)
    nd = np.nan_to_num(np.maximum(dsm - dtm, 0.0), nan=0.0)
    bld, veg = classify_surface(nd, dsm, buildings)
    scene = Scene(dtm, nd, bld, veg, measured, tuple(bbox), res)
    scene._raw = dict(dsm=dsm)
    return scene


# ---------------------------------------------------------------- OpenStreetMap footprints
def fetch_buildings(bbox, timeout: float = 120) -> list[np.ndarray]:
    """Outer rings of the OSM buildings in an EPSG:25832 box, as (x, y) arrays (Overpass).

    Only the box goes to the server; a busy server (429, 504) gets one more try after a
    minute, as for the road graph. Raises OSError when it can't be reached.
    """
    from meshplay.sim.sites import to_lonlat, to_utm

    (w, s), (e, n) = to_lonlat(bbox[0], bbox[1]), to_lonlat(bbox[2], bbox[3])
    b = f"({s:.5f},{w:.5f},{n:.5f},{e:.5f})"
    query = (
        f"[out:json][timeout:{int(timeout)}];"
        f'(way["building"]{b};relation["building"]["type"="multipolygon"]{b};);out geom;'
    )
    req = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": "meshplay/0.1 (https://github.com/nhilbert/meshtastic-map)"},
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout + 30) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 504) or attempt == 2:
                raise
            time.sleep(60)
    rings = []
    for el in data.get("elements", []):
        parts = (
            [el.get("geometry")]
            if el["type"] == "way"
            else [m.get("geometry") for m in el.get("members", []) if m.get("role") == "outer"]
        )
        for geom in parts:
            if geom and len(geom) >= 3:
                x, y = to_utm(
                    np.array([p["lon"] for p in geom]), np.array([p["lat"] for p in geom])
                )
                rings.append(np.column_stack([x, y]))
    return rings


def rasterize_rings(rings, bbox, res, shape) -> np.ndarray:
    from skimage.draw import polygon

    mask = np.zeros(shape, bool)
    for ring in rings:  # cell (i, j) has its centre at i + 0.5, j + 0.5 in these units
        rr, cc = polygon(
            (ring[:, 1] - bbox[1]) / res - 0.5, (ring[:, 0] - bbox[0]) / res - 0.5, shape
        )
        mask[rr, cc] = True
    return mask
