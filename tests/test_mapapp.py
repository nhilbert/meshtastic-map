import base64

import numpy as np
import pytest

from meshplay.mapapp.registry import Context, Setting
from meshplay.mapapp.style import png_data_url, ramp_color


def test_setting_parse():
    assert Setting("n", "N", "number", 30).parse("12.5") == 12.5
    assert Setting("n", "N", "number", 30).parse(None) == 30
    assert Setting("b", "B", "bool", False).parse("true") is True
    assert Setting("s", "S", "select", "x").parse("") == "x"


def test_sites_layer_marks_endpoints():
    from meshplay.mapapp.layers.sites import SitesLayer

    ctx = Context()
    ctx.__dict__["sites"] = {
        "HOME": {"lon": 7.1, "lat": 50.7, "height_m": (3.0, 4.0), "clutter_m": 20.0},
        "HOME_W": {
            "same_as": "HOME",
            "lon": 7.1,
            "lat": 50.7,
            "height_m": (6.0, 8.0),
            "clutter_m": 20.0,
        },
    }
    layer = SitesLayer()
    data = layer.data(ctx, layer.parse_values(ctx, {}))
    assert [f["properties"]["_title"] for f in data["features"]] == ["HOME"]
    props = data["features"][0]["properties"]
    assert props["_endpoint"]["height_m"] == [3.0, 4.0]
    assert props["_fields"]["Varianten"] == "HOME_W"


def test_png_and_colors():
    url = png_data_url(np.zeros((2, 3, 4), np.uint8))
    assert base64.b64decode(url.split(",")[1])[:8] == b"\x89PNG\r\n\x1a\n"
    assert ramp_color(0, 0, 1, ["#000000", "#ffffff"]) == "#000000"
    assert ramp_color(1, 0, 1, ["#000000", "#ffffff"]) == "#ffffff"


def test_predict_link_on_a_synthetic_scene():
    pytest.importorskip("scipy")
    from meshplay.sim.link import Endpoint, predict_link
    from meshplay.sim.scene import Scene

    ny, nx = 120, 400
    nd = np.zeros((ny, nx))
    bld = np.zeros((ny, nx), bool)
    nd[:, 190:210] = 20.0
    bld[:, 190:210] = True
    scene = Scene(
        np.full((ny, nx), 50.0),
        nd,
        bld,
        np.zeros_like(bld),
        np.ones_like(bld),
        (0.0, 0.0, 400.0, 120.0),
        1.0,
    )
    a = Endpoint(20, 60, (3.0, 4.0), 20.0, "open")
    b = Endpoint(380, 60, (1.0, 1.6), 12.0, None, "t1000e")
    res = predict_link(scene, a, b, draws=300)
    assert res["d_m"] == pytest.approx(360.0)
    assert 0.0 <= res["models"]["ENS"]["p_rx"] <= 1.0
    assert "M4_P1411" in res["models"]  # valid below 660 m
    assert res["geometry"]["edges"][0]["art"] == "Gebäude"
    with pytest.raises(ValueError):
        predict_link(scene, a, Endpoint(25, 60), draws=100)


# ---------------------------------------------------------------- without laser-scan scene
@pytest.fixture
def bare_ctx(tmp_path):
    """Context on an empty data folder: no scene, no sites, no logs."""
    from meshplay.config import Settings

    return Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=None))


def test_link_tool_without_scene(bare_ctx):
    from meshplay.mapapp.tools import link

    pt = {"lat": 50.7374, "lon": 7.0988, "height_m": [2, 3], "clutter_m": 12}
    with pytest.raises(ValueError, match="Keine Laserscan-Szene"):
        link.run(bare_ctx, {"a": pt, "b": {**pt, "lat": 50.7336}})


def test_layers_without_scene(bare_ctx):
    from meshplay.mapapp.layers import ALL

    for layer in ALL:
        data = layer.data(bare_ctx, layer.parse_values(bare_ctx, {}))
        assert "features" in data or data.get("type") == "raster", layer.id


def test_walk_comparison_falls_back_without_scene(bare_ctx):
    import json

    from meshplay.mapapp.layers.walk import WalkLayer

    probes = bare_ctx.data_dir / "probes"
    probes.mkdir()
    rec = {
        "sentAt": "2026-09-29T12:00:00",
        "to": "!abcd1234",
        "channel": 1,
        "result": "ok",
        "snrTowards": 1.0,
        "snrBack": -3.0,
        "rssiBack": -110,
        "relays": 0,
        "interval": 60,
    }
    (probes / "2026-09-29.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    tracks = bare_ctx.data_dir / "tracks"
    tracks.mkdir()
    (tracks / "w.gpx").write_text(
        '<gpx><trk><trkseg><trkpt lat="50.7" lon="7.1"><time>2026-09-29T09:59:00Z</time></trkpt>'
        '<trkpt lat="50.71" lon="7.1"><time>2026-09-29T10:01:00Z</time></trkpt>'
        "</trkseg></trk></gpx>",
        encoding="utf-8",
    )
    layer = WalkLayer()
    values = layer.parse_values(
        bare_ctx,
        {"date": "2026-09-29", "tracker": "probe:!abcd1234", "gpx": "w.gpx", "color": "residual"},
    )
    data = layer.data(bare_ctx, values)
    assert "Laserscan-Szene" in data["note"]
    assert data["legend"]["title"].startswith("SNR")
    points = [f["properties"] for f in data["features"] if f["geometry"]["type"] == "Point"]
    assert points and all(p["_panel"] for p in points)  # details in the panel, no popup


def test_active_walks_list_days_trackers_and_tracks(bare_ctx):
    """The page's list of active walks: a day per log that has a tracker, with the trackers of
    the walk layer's setting and the GPX tracks recorded on that day."""
    import json

    from meshplay.mapapp.layers.walk import WalkLayer
    from meshplay.mapapp.walks_store import delete_active, list_active

    assert list_active(bare_ctx) == []  # no data folder yet
    for folder in ("probes", "packets", "tracks"):
        (bare_ctx.data_dir / folder).mkdir()
    probe = {"sentAt": "2026-09-29T12:00:00", "to": "!abcd1234", "result": "ok"}
    (bare_ctx.data_dir / "probes" / "2026-09-29.jsonl").write_text(
        json.dumps(probe) + "\n" + json.dumps(probe) + "\n", encoding="utf-8"
    )
    position = {
        "from": 0x11223344,
        "decoded": {"portnum": "POSITION_APP", "position": {"latitude": 50.7, "longitude": 7.1}},
    }
    packets = bare_ctx.data_dir / "packets"
    (packets / "2026-09-29.jsonl").write_text(json.dumps(position) + "\n", encoding="utf-8")
    telemetry = {"from": 0x11223344, "decoded": {"portnum": "TELEMETRY_APP"}}
    (packets / "2026-09-28.jsonl").write_text(json.dumps(telemetry) + "\n", encoding="utf-8")
    tracks = bare_ctx.data_dir / "tracks"
    gpx = (
        '<gpx><trk><trkseg><trkpt lat="50.7" lon="7.1"><time>{day}T10:00:00Z</time></trkpt>'
        '<trkpt lat="50.71" lon="7.12"><time>{day}T10:30:00Z</time></trkpt></trkseg></trk></gpx>'
    )
    (tracks / "walk.gpx").write_text(gpx.format(day="2026-09-29"), encoding="utf-8")
    (tracks / "earlier.gpx").write_text(gpx.format(day="2026-09-20"), encoding="utf-8")
    (tracks / "broken.gpx").write_text("<gpx", encoding="utf-8")

    (walk,) = list_active(bare_ctx)  # the day without a position packet is no walk
    assert walk["date"] == "2026-09-29" and (walk["probes"], walk["positions"]) == (2, 1)
    assert [v for v, _label in walk["trackers"]] == ["probe:!abcd1234", "!11223344"]
    track = {"name": "walk.gpx", "bbox": [[50.7, 7.1], [50.71, 7.12]], "shared": False}
    assert walk["tracks"] == [track]
    # the layer's settings offer the same days and trackers, the day without a tracker too
    settings = {s.name: s for s in WalkLayer().settings(bare_ctx)}
    assert [d for d, _label in settings["date"].options] == ["2026-09-29", "2026-09-28"]
    assert settings["tracker"].options_map["2026-09-29"] == walk["trackers"]
    assert settings["tracker"].default == "probe:!abcd1234"
    # a day comes with its own track, so it shows without choosing one
    assert settings["gpx"].default == "walk.gpx"
    assert settings["gpx"].options_map["2026-09-29"] == [["walk.gpx", "walk.gpx"], ["", "—"]]
    assert settings["gpx"].options_map["2026-09-28"] == [["", "—"]]

    # deleting the walk removes both logs of the day and its track, nothing else
    with pytest.raises(ValueError, match="Unbekannter Rundgang"):
        delete_active(bare_ctx, "2026-09-28")  # not a walk: its packet log stays
    with pytest.raises(ValueError, match="Unbekannter Rundgang"):
        delete_active(bare_ctx, "../tracks/earlier")
    done = delete_active(bare_ctx, "2026-09-29")
    assert sorted(done["deleted"]) == [
        "packets/2026-09-29.jsonl",
        "probes/2026-09-29.jsonl",
        "tracks/walk.gpx",
    ]
    assert list_active(bare_ctx) == []
    left = sorted(p.name for p in bare_ctx.data_dir.rglob("*") if p.is_file())
    assert left == ["2026-09-28.jsonl", "broken.gpx", "earlier.gpx"]


def test_todays_walk_is_not_deleted_while_its_logs_are_written(bare_ctx):
    import json
    from datetime import date
    from types import SimpleNamespace

    from meshplay.mapapp.walks_store import delete_active

    today = date.today().isoformat()
    probes = bare_ctx.data_dir / "probes"
    probes.mkdir()
    log = probes / f"{today}.jsonl"
    log.write_text(json.dumps({"to": "!abcd1234"}) + "\n", encoding="utf-8")
    bare_ctx.device = SimpleNamespace(state="verbunden", log_packets=True)
    with pytest.raises(ValueError, match="erst trennen"):
        delete_active(bare_ctx, today)
    bare_ctx.device.state = "getrennt"
    bare_ctx.jobs = SimpleNamespace(running=lambda kind: [object()])
    with pytest.raises(ValueError, match="erst stoppen"):
        delete_active(bare_ctx, today)
    assert log.is_file()
    bare_ctx.jobs = SimpleNamespace(running=lambda kind: [])
    assert delete_active(bare_ctx, today)["deleted"] == [f"probes/{today}.jsonl"]


def test_coverage_task_refused_without_scene(bare_ctx):
    from meshplay.mapapp.jobs import JobManager

    jobs = JobManager(bare_ctx)
    with pytest.raises(ValueError, match="Keine Laserscan-Szene"):
        jobs.create("coverage", {})


def test_walk_comparison_runs_in_the_background(bare_ctx, monkeypatch):
    """A comparison not cached yet is computed in a thread under the model lock: the layer
    answers at once (None = pending), later calls get the cached result."""
    import threading

    from meshplay.mapapp.layers import walk
    from meshplay.mapapp.layers.walk import WalkLayer

    log = bare_ctx.data_dir / "packets" / "2026-09-29.jsonl"
    log.parent.mkdir()
    log.write_text("", encoding="utf-8")
    meta = bare_ctx.sim_dir / "scene" / "scene_meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text("{}", encoding="utf-8")
    release, calls = threading.Event(), []

    def slow_score(self, ctx, *args):
        calls.append(ctx.model_lock._is_owned())  # the thread holds the model lock
        release.wait(5)
        return {"scores": {"t": {"pred": -100.0, "resid": 1.0}}, "summary": {"packets": 1}}

    monkeypatch.setattr(WalkLayer, "_score", slow_score)
    layer, values = WalkLayer(), {"gpx": "", "date": "2026-09-29", "color": "residual"}
    args = (bare_ctx, values, log, [], [], 30.0, "ShortSlow")
    assert layer.scores(*args) is None
    assert layer.scores(*args) is None  # still running: no second thread
    with bare_ctx.model_lock:  # the layer request itself did not need the lock
        pass
    release.set()
    for _ in range(100):
        if not walk._running:
            break
        threading.Event().wait(0.05)
    assert calls == [True]
    assert layer.scores(*args) == ({"t": {"pred": -100.0, "resid": 1.0}}, {"packets": 1})
