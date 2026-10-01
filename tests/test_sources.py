"""Elevation sources per state: which state a point is in, the tile lists and the raster scenes.

No network: indexes are written locally or stubbed, rasters are synthetic.
"""

import json
import os
import time

import numpy as np
import pytest

pytest.importorskip("scipy")
pytest.importorskip("skimage")

from meshplay.sim import sources  # noqa: E402
from meshplay.sim.sources import ni, raster, sh  # noqa: E402

# public places as example points
COLOGNE_CATHEDRAL = (50.94130, 6.95828)
HANOVER_STATION = (52.37653, 9.74104)
KIEL_STATION = (54.31500, 10.13150)
MUNICH_MARIENPLATZ = (48.13743, 11.57549)
PARIS_NOTRE_DAME = (48.85296, 2.34990)


def test_source_follows_the_state_of_the_point():
    assert sources.for_point(*COLOGNE_CATHEDRAL)[0].id == "nrw"
    assert sources.for_point(*HANOVER_STATION)[0].id == "ni"
    assert sources.for_point(*KIEL_STATION)[0].id == "sh"
    assert sources.for_point(*MUNICH_MARIENPLATZ) == (None, "Bayern")
    assert sources.for_point(*PARIS_NOTRE_DAME) == (None, None)


def test_grid_keys_cover_the_box_north_to_south():
    keys = sources.grid_keys((550200, 5802500, 552000, 5804000))
    assert keys == [(550, 5803), (551, 5803), (550, 5802), (551, 5802)]


# ---------------------------------------------------------------- rasters
def write_geotiff(path, a, x0, y1, res):
    """An uncompressed float32 GeoTIFF with the two tags the reader uses."""
    import tifffile

    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        a.astype(np.float32),
        extratags=[(33550, "d", 3, (res, res, 0.0)), (33922, "d", 6, (0, 0, 0, x0, y1, 0))],
    )


def test_mosaic_puts_north_up_tiles_south_up_on_the_scene_grid(tmp_path):
    bbox = (1000.0, 2000.0, 1200.0, 2100.0)
    # two tiles side by side whose value is the northing of each cell centre
    for k, x0 in enumerate((1000.0, 1100.0)):
        rows = 2100.0 - 0.5 - np.arange(100)
        write_geotiff(tmp_path / f"t{k}.tif", np.tile(rows[:, None], (1, 100)), x0, 2100.0, 1.0)
    m = raster.mosaic(bbox, 1.0, [tmp_path / "t0.tif", tmp_path / "t1.tif"])
    assert m.shape == (100, 200) and np.isfinite(m).all()
    assert m[0, 0] == pytest.approx(2000.5) and m[99, 199] == pytest.approx(2099.5)


def test_surface_at_20_cm_takes_the_highest_value_per_metre(tmp_path):
    a = np.zeros((50, 50))
    a[3, 7] = 12.0  # one 20 cm pixel of a lamp post in the cell (row 0, col 1) from the top
    write_geotiff(tmp_path / "dom.tif", a, 0.0, 10.0, 0.2)
    m = raster.mosaic((0.0, 0.0, 10.0, 10.0), 1.0, [tmp_path / "dom.tif"], "max")
    assert m.shape == (10, 10) and m[9, 1] == 12.0 and m.sum() == 12.0


def test_xyz_grid_ignores_the_page_appended_by_the_server(tmp_path):
    lines = [f"{x + 0.5:.2f} {y + 0.5:.2f} {y:.2f}" for y in range(9, -1, -1) for x in range(10)]
    path = tmp_path / "dgm.xyz"
    path.write_bytes(("\r\n".join(lines) + "\r\n<!DOCTYPE html><html>Zurück</html>\n").encode())
    a, x0, y1, res = raster.read_xyz(path)
    assert (x0, y1, res) == (0.0, 10.0, 1.0) and a.shape == (10, 10)
    assert a[0, 0] == 9.0 and a[9, 0] == 0.0  # north-up


def test_footprints_separate_buildings_from_trees():
    nd = np.zeros((60, 60))
    nd[10:30, 10:30] = 12.0  # a house
    nd[40:50, 40:50] = 15.0  # a tree
    dsm = 50.0 + nd
    fp = np.zeros_like(nd, bool)
    fp[10:30, 10:30] = True
    bld, veg = raster.classify_surface(nd, dsm, fp)
    assert bld[15:25, 15:25].all() and not bld[40:50, 40:50].any()
    assert veg[42:48, 42:48].all() and not veg[15:25, 15:25].any()


def test_without_footprints_a_flat_roof_still_counts_as_building():
    rng = np.random.default_rng(1)
    nd = np.zeros((60, 60))
    nd[10:30, 10:30] = 12.0
    nd[40:50, 40:50] = 15.0 + rng.normal(0, 2.0, (10, 10))  # a rough crown
    bld, veg = raster.classify_surface(nd, 50.0 + nd, None)
    assert bld[14:26, 14:26].all()
    assert veg[40:50, 40:50].mean() > 0.8


def test_rasterize_rings_fills_the_cells_inside():
    ring = np.array([[1002.0, 2002.0], [1012.0, 2002.0], [1012.0, 2008.0], [1002.0, 2008.0]])
    m = raster.rasterize_rings([ring], (1000.0, 2000.0, 1020.0, 2010.0), 1.0, (10, 20))
    assert m.sum() == 60 and m[2, 2] and m[7, 11] and not m[8, 12]


# ---------------------------------------------------------------- the sources' tile lists
def test_lower_saxony_lists_the_newest_tile_files_from_its_catalogue(tmp_path, monkeypatch):
    def search(product, *box):
        base = f"https://example.org/{product}"
        return {(550, 5803): (f"{product}_32_550_5803_1_ni_2016.tif", f"{base}/a.tif", 2016)}

    monkeypatch.setattr(ni, "_search", search)
    bbox = (550000, 5803000, 551000, 5804000)
    tiles = sources.by_id("ni").tiles(bbox, tmp_path)
    assert [t.key for t in tiles] == [(550, 5803)]
    assert [f.path.parent.name for f in tiles[0].files] == ["dgm1", "dom1"]
    assert not tiles[0].present
    for f in tiles[0].files:
        write_geotiff(f.path, np.full((10, 10), 50.0), 550000.0, 5804000.0, 1.0)
    assert sources.by_id("ni").tiles_offline(bbox, tmp_path)[0].present


def test_schleswig_holstein_reads_its_published_index(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("a fresh index needs no download")

    monkeypatch.setattr(sh, "download_url", no_network)
    for product, pattern in (
        ("dgm1", "dgm1_32_574_6020_1_sh_2023.xyz"),
        ("bdom", "bdom20nc_32_574_6020_1_sh_2023.tif"),
    ):
        url = f"https://example.org/massen.php?file={pattern}&id=2"
        ring = [
            [574000.0, 6020000.0],
            [575000.0, 6020000.0],
            [575000.0, 6021000.0],
            [574000.0, 6020000.0],
        ]
        index = {
            "features": [
                {
                    "properties": {"link_data": url},
                    "geometry": {"type": "MultiPolygon", "coordinates": [[ring]]},
                }
            ]
        }
        path = sh._index_path(tmp_path, product)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index), encoding="utf-8")
        os.utime(path, (time.time(), time.time()))
    tiles = sources.by_id("sh").tiles((574000, 6020000, 575000, 6021000), tmp_path)
    assert [f.name for f in tiles[0].files] == [
        "dgm1_32_574_6020_1_sh_2023.xyz",
        "bdom20nc_32_574_6020_1_sh_2023.tif",
    ]
    assert tiles[0].files[1].url.startswith("https://example.org/")


def test_raster_source_builds_a_scene(tmp_path):
    bbox = (550000, 5803000, 551000, 5804000)
    ground = np.full((1000, 1000), 50.0)
    surface = ground.copy()
    surface[100:200, 100:200] = 62.0  # a block 12 m high near the north-west corner
    write_geotiff(
        tmp_path / "dgm1" / "dgm1_32_550_5803_1_ni_2016.tif", ground, 550000.0, 5804000.0, 1.0
    )
    write_geotiff(
        tmp_path / "dom1" / "dom1_32_550_5803_1_ni_2016.tif", surface, 550000.0, 5804000.0, 1.0
    )
    seen = []
    scene = sources.by_id("ni").build(
        bbox, tmp_path, 1.0, on_file=lambda name, k, n: seen.append((k, n))
    )
    assert seen == [(1, 2), (2, 2)]
    assert scene.measured.all() and scene.dtm.mean() == pytest.approx(50.0)
    assert scene.nd[850, 150] == pytest.approx(
        12.0
    )  # row 850 from the south = row 150 from the north
    # without footprints only the block's steep edge reads as rough (vegetation)
    assert scene.bld[820:880, 120:180].all() and scene.veg.sum() < 0.1 * scene.bld.sum()
