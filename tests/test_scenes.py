"""Named laser-scan scenes, the scene task of the map app and the tile download of the script.

No network: the map app never downloads; the script's download is tested against a local HTTP
server.
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

pytest.importorskip("scipy")

from meshplay.config import Settings  # noqa: E402
from meshplay.mapapp import scenes as scenes_api  # noqa: E402
from meshplay.mapapp.jobs import DONE, FAILED, JobManager  # noqa: E402
from meshplay.mapapp.registry import Context  # noqa: E402
from meshplay.sim import scenes  # noqa: E402
from meshplay.sim.scene import Scene, tile_name  # noqa: E402
from meshplay.sim.sites import to_lonlat  # noqa: E402

CATHEDRAL = (50.94130, 6.95828)  # Cologne Cathedral, a public place


def small_scene(bbox=(356000.0, 5645000.0, 356100.0, 5645060.0), height=50.0) -> Scene:
    ny, nx = int(bbox[3] - bbox[1]), int(bbox[2] - bbox[0])
    z = np.zeros((ny, nx), bool)
    return Scene(np.full((ny, nx), height), np.zeros((ny, nx)), z, z, ~z, bbox, 1.0)


@pytest.fixture
def ctx(tmp_path):
    return Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=CATHEDRAL))


def wait_for(cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


# ---------------------------------------------------------------- named scenes
def test_old_single_scene_becomes_the_default_scene(ctx):
    small_scene().save(ctx.sim_dir / "scene")
    assert ctx.scene_name == "default"
    assert not (ctx.sim_dir / "scene").exists()
    assert (ctx.sim_dir / "scenes" / "default" / "scene_meta.json").exists()
    assert ctx.scene.shape == (60, 100)


def test_switching_scenes_loads_the_other_one(ctx):
    root = scenes.scenes_dir(ctx.sim_dir)
    small_scene(height=50.0).save(root / "a", {"name": "a"})
    small_scene(height=80.0).save(root / "b", {"name": "b"})
    assert ctx.scene_name == "a"  # no active.txt: the first one
    assert ctx.scene.dtm[0, 0] == 50.0
    scenes_api.activate(ctx, "b")
    assert ctx.scene_name == "b" and ctx.scene.dtm[0, 0] == 80.0
    with pytest.raises(KeyError):
        scenes_api.activate(ctx, "c")


def test_deleting_the_active_scene_falls_back_to_another(ctx):
    root = scenes.scenes_dir(ctx.sim_dir)
    for name in ("a", "b"):
        small_scene().save(root / name, {"name": name})
    scenes_api.activate(ctx, "b")
    assert ctx.scene.shape == (60, 100)  # loaded
    listing = scenes_api.delete(ctx, "b")
    assert [s["name"] for s in listing["scenes"]] == ["a"]
    assert listing["active"] == "a" and ctx.scene_name == "a"


def test_scene_in_use_by_a_task_is_not_deleted(ctx):
    small_scene().save(scenes.scenes_dir(ctx.sim_dir) / "a")
    ctx.jobs = JobManager(ctx)
    job = ctx.jobs.jobs.setdefault("x", type("J", (), {})())
    job.state, job.kind, job.params = "läuft", "coverage", {"scene": "a"}
    with pytest.raises(ValueError, match="arbeitet mit der Szene"):
        scenes_api.delete(ctx, "a")


def test_plan_counts_tiles_without_network(ctx):
    plan = scenes_api.plan(ctx, *CATHEDRAL, 3)
    assert plan["tiles"] in (9, 12, 16) and plan["present"] == 0
    assert plan["download_mb"] == plan["tiles"] * scenes_api.TILE_MB
    assert len(plan["ring"]) == 4


# ---------------------------------------------------------------- task checks
def test_scene_task_checks_its_parameters(ctx):
    jobs = JobManager(ctx)
    good = {"name": "dom", "center": "50.94130, 6.95828", "size": "2"}
    with pytest.raises(ValueError, match="Name"):
        jobs.create("scene", {**good, "name": "zu lang und mit Leerzeichen"})
    with pytest.raises(ValueError, match="Mitte"):
        jobs.create("scene", {**good, "center": "Dom"})
    small_scene().save(scenes.scenes_dir(ctx.sim_dir) / "dom")
    with pytest.raises(ValueError, match="gibt es schon"):
        jobs.create("scene", good)


def test_coverage_task_needs_the_site_inside_the_scene(ctx):
    (ctx.sim_dir).mkdir(parents=True, exist_ok=True)
    (ctx.sim_dir / "sites.json").write_text(
        '{"sites": {"DOM": {"lat": 50.9413, "lon": 6.95828, "height_m": [3, 4], "clutter_m": 10}}}',
        encoding="utf-8",
    )
    small_scene().save(scenes.scenes_dir(ctx.sim_dir) / "far")
    with pytest.raises(ValueError, match="liegt nicht in der Szene"):
        JobManager(ctx).create("coverage", {"site": "DOM", "scene": "far"})


# ---------------------------------------------------------------- download (script) and build
class TileServer:
    """Serves {name: bytes} with HEAD, 404 and Range requests, like the Geobasis server."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.requests = files, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _head(self, body: bytes | None) -> bytes | None:
                name = self.path.rsplit("/", 1)[-1]
                outer.requests.append((self.command, name, self.headers.get("Range")))
                data = outer.files.get(name)
                if data is None:
                    self.send_error(404)
                    return None
                start = 0
                rng = self.headers.get("Range")
                if rng:
                    start = int(rng.removeprefix("bytes=").split("-")[0])
                    self.send_response(206)
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                return data[start:]

            def do_HEAD(self):
                self._head(None)

            def do_GET(self):
                data = self._head(b"")
                if data is not None:
                    self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def test_download_continues_a_part_file(tmp_path, monkeypatch):
    from meshplay.sim import lidar

    # small: some Windows network filters (seen with Norton) drop the end of large loopback sends
    data = bytes(range(256)) * 400
    server = TileServer({"t.laz": data})
    monkeypatch.setattr(lidar, "TILE_URL", server.url)
    try:
        (tmp_path / "t.part").write_bytes(data[:1000])
        seen = []
        path = lidar.download_tile("t.laz", tmp_path, lambda done, total: seen.append(total))
        assert path.read_bytes() == data
        assert ("GET", "t.laz", "bytes=1000-") in server.requests
        assert seen[-1] == len(data)
        assert lidar.remote_size("t.laz") == len(data)
        assert lidar.remote_size("other.laz") is None
    finally:
        server.close()


def synthetic_tile(e_km: int, n_km: int) -> bytes:
    """A 1 km tile: ground at 50 m every 2 m, a 15 m flat roof (single returns, class 20)."""
    import io

    import laspy

    xs, ys = np.meshgrid(np.arange(0, 1000, 2.0), np.arange(0, 1000, 2.0))
    xs, ys = xs.ravel() + e_km * 1000, ys.ravel() + n_km * 1000
    roof = (np.abs(xs - (e_km * 1000 + 500)) < 20) & (np.abs(ys - (n_km * 1000 + 500)) < 20)
    header = laspy.LasHeader(point_format=1, version="1.2")
    header.offsets = [e_km * 1000, n_km * 1000, 0]
    header.scales = [0.01, 0.01, 0.01]
    las = laspy.LasData(header)
    las.x, las.y = xs, ys
    las.z = np.where(roof, 65.0, 50.0)
    las.classification = np.where(roof, 20, 2).astype(np.uint8)
    las.return_number = np.ones(len(xs), np.uint8)
    las.number_of_returns = np.ones(len(xs), np.uint8)
    buf = io.BytesIO()
    las.write(buf, do_compress=True)
    return buf.getvalue()


def test_scene_task_builds_from_local_tiles_and_activates(ctx, monkeypatch):
    from meshplay.sim import lidar

    def no_network(*args, **kwargs):
        raise AssertionError("the app must not download")

    monkeypatch.setattr(lidar, "download_tile", no_network)
    monkeypatch.setattr(lidar, "remote_size", no_network)
    e_km, n_km = 356, 5645
    lon, lat = to_lonlat(e_km * 1000 + 500, n_km * 1000 + 500)
    params = {"name": "dom", "center": f"{lat:.5f}, {lon:.5f}", "size": "1"}
    ctx.jobs = JobManager(ctx)
    with pytest.raises(ValueError, match="Keine der"):  # nothing downloaded yet
        ctx.jobs.create("scene", params)
    plan = scenes_api.plan(ctx, lat, lon, 1)
    assert tile_name(e_km, n_km) in plan["missing"]
    assert plan["source"].startswith("https://") and plan["folder"].endswith("laz")

    laz = ctx.sim_dir / "laz"
    laz.mkdir(parents=True)
    (laz / tile_name(e_km, n_km)).write_bytes(synthetic_tile(e_km, n_km))
    monkeypatch.setenv("MESHPLAY_DATA_DIR", str(ctx.data_dir))  # for the build process
    job = ctx.jobs.create("scene", params)
    wait_for(lambda: job.state in (DONE, FAILED), timeout=240)
    assert job.state == DONE, (job.error, list(job.log)[-8:])
    assert job.result == {"name": "dom", "active": True}
    assert ctx.scene_name == "dom"
    s = ctx.scene
    assert s.shape == (1000, 1000)
    assert s.bld.sum() > 1000  # the roof
    assert (ctx.app_dir / "scene" / "dom" / "bodies.bin").exists()  # 3D view prepared
    listing = scenes_api.scene_list(ctx)
    assert listing["tiles_mb"] > 0
    assert listing["scenes"][0]["measured"] == pytest.approx(0.25, abs=0.01)  # 2 m point grid
    assert scenes_api.delete_tiles(ctx)["tiles_mb"] == 0
