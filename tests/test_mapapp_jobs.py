"""Background tasks and the sites editor of the map app (no device, no scene needed)."""

import json
import time

import pytest

from meshplay.config import Settings
from meshplay.mapapp import sites_store
from meshplay.mapapp.jobs import (
    CANCELLED,
    DONE,
    FAILED,
    RUNNING,
    WAITING,
    Job,
    JobKind,
    JobManager,
    run_process,
)
from meshplay.mapapp.registry import Context, Setting


@pytest.fixture
def ctx(tmp_path):
    return Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=None))


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


# ---------------------------------------------------------------- sites
def sites_file(tmp_path):
    path = tmp_path / "sites.json"
    path.write_text(
        json.dumps(
            {
                "sites": {
                    "HOME": {"lon": 7.1, "lat": 50.7, "height_m": [3, 4], "clutter_m": 20},
                    "HOME_W": {"same_as": "HOME", "height_m": [6, 8]},
                    "ROOF": {"lon": 7.2, "lat": 50.8, "height_m": [15, 19], "clutter_m": 17},
                },
                "scenarios": [{"id": "A1", "a": "HOME", "b": "ROOF"}],
                "corridor": {"from": "HOME", "towards": "ROOF"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_add_and_update_site(tmp_path):
    path = sites_file(tmp_path)
    cfg = sites_store.add_site(path, "NEU", 50.75, 7.09, [1.5, 3], 11.2, "Balkon")
    assert list(cfg["sites"])[-1] == "NEU"
    assert cfg["sites"]["NEU"] == {
        "lon": 7.09,
        "lat": 50.75,
        "height_m": [1.5, 3.0],
        "clutter_m": 11.2,
        "description": "Balkon",
    }
    assert (tmp_path / "sites.json.bak").exists()
    cfg = sites_store.update_site(path, "NEU", {"lat": 50.76, "lon": 7.1, "height_m": [2, 4]})
    assert cfg["sites"]["NEU"]["lat"] == 50.76 and cfg["sites"]["NEU"]["height_m"] == [2.0, 4.0]


def test_site_validation(tmp_path):
    path = sites_file(tmp_path)
    with pytest.raises(ValueError, match="gibt es schon"):
        sites_store.add_site(path, "HOME", 50.7, 7.1, [1, 2], 10)
    with pytest.raises(ValueError, match="Name"):
        sites_store.add_site(path, "mit leerzeichen", 50.7, 7.1, [1, 2], 10)
    with pytest.raises(ValueError, match="Antennenhöhe"):
        sites_store.add_site(path, "X", 50.7, 7.1, [5, 2], 10)
    with pytest.raises(ValueError, match="Variante"):
        sites_store.update_site(path, "HOME_W", {"lat": 50.0, "lon": 7.0})


def test_rename_updates_references(tmp_path):
    path = sites_file(tmp_path)
    cfg = sites_store.rename_site(path, "HOME", "BASE")
    assert list(cfg["sites"]) == ["BASE", "HOME_W", "ROOF"]  # order kept: first = home
    assert cfg["sites"]["HOME_W"]["same_as"] == "BASE"
    assert cfg["scenarios"][0]["a"] == "BASE"
    assert cfg["corridor"]["from"] == "BASE"


def test_delete_refused_while_referenced(tmp_path):
    path = sites_file(tmp_path)
    with pytest.raises(ValueError, match="Variante HOME_W, Szenario A1, Korridor"):
        sites_store.delete_site(path, "HOME")
    cfg = sites_store.delete_site(path, "HOME_W")
    assert "HOME_W" not in cfg["sites"]


# ---------------------------------------------------------------- jobs
class Sleeper(JobKind):
    """Test kind: runs until stopped or `ticks` are done; fails on request."""

    id = "sleep"
    name = "Schlafen"

    def settings(self, ctx):
        return [
            Setting("ticks", "Ticks", "number", 3, min=1, max=1000),
            Setting("fail", "Fehler", "bool", False),
        ]

    def run(self, ctx, job):
        n = int(job.params["ticks"])
        for i in range(n):
            job.check_stop()
            job.progress = (i + 1) / n
            time.sleep(0.02)
        if job.params["fail"]:
            raise RuntimeError("kaputt")


class Walk(Sleeper):
    id = "walk"
    stop_is_success = True


def manager(ctx):
    return JobManager(ctx, {"sleep": Sleeper(), "walk": Walk()})


def test_job_runs_and_is_stored(ctx):
    jobs = manager(ctx)
    job = jobs.create("sleep", {"ticks": 2})
    wait_for(lambda: job.state == DONE)
    assert job.progress == 1.0 and job.ended >= job.started
    stored = json.loads((ctx.app_dir / "jobs" / f"{job.id}.json").read_text(encoding="utf-8"))
    assert stored["state"] == DONE
    assert "Ende: fertig" in (ctx.app_dir / "jobs" / f"{job.id}.log").read_text(encoding="utf-8")


def test_job_error_and_validation(ctx):
    jobs = manager(ctx)
    job = jobs.create("sleep", {"ticks": 1, "fail": True})
    wait_for(lambda: job.state == FAILED)
    assert job.error == "RuntimeError: kaputt"
    with pytest.raises(ValueError, match="höchstens"):
        jobs.create("sleep", {"ticks": 5000})
    with pytest.raises(KeyError):
        jobs.create("nope", {})


def test_one_job_per_kind_and_cancel(ctx):
    jobs = manager(ctx)
    first = jobs.create("sleep", {"ticks": 1000})
    second = jobs.create("sleep", {"ticks": 1})
    wait_for(lambda: first.state == RUNNING)
    assert second.state == WAITING  # queued behind the first of its kind
    with pytest.raises(ValueError, match="erst abbrechen"):
        jobs.remove(first.id)
    jobs.cancel(first.id)
    wait_for(lambda: second.state == DONE)
    assert first.state == CANCELLED
    jobs.remove(first.id)
    assert first.id not in [j["id"] for j in jobs.list()]


def test_stopping_a_walk_counts_as_done(ctx):
    jobs = manager(ctx)
    job = jobs.create("walk", {"ticks": 1000})
    wait_for(lambda: job.state == RUNNING)
    jobs.cancel(job.id)
    wait_for(lambda: job.state == DONE)


def test_interrupted_jobs_are_marked_after_restart(ctx):
    jobs = manager(ctx)
    job = jobs.create("sleep", {"ticks": 1000})
    wait_for(lambda: job.state == RUNNING)
    again = manager(ctx)  # as if the server had been restarted meanwhile
    assert again.get(job.id).state == CANCELLED
    assert again.get(job.id).error == "Server wurde beendet"
    jobs.cancel(job.id)


def test_run_process_streams_progress_lines(ctx, tmp_path):
    job = Job("t", "x", "t", {})
    job.log_path = tmp_path / "t.log"
    seen = []
    code = "import sys; print('row 1/2', end='\\r'); print('row 2/2'); print('Grid: x.npz')"
    run_process(job, ["-c", code], seen.append)
    assert seen == ["row 1/2", "row 2/2", "Grid: x.npz"]
    with pytest.raises(RuntimeError, match="Code 3: boom"):
        run_process(job, ["-c", "import sys; print('boom'); sys.exit(3)"])


# ---------------------------------------------------------------- traceroute walk (fake radio)
class FakeIface:
    """Answers traceroutes like the radio: snr_towards in dB*4, reply SNR/RSSI; or stays silent."""

    def __init__(self, answer=True):
        from types import SimpleNamespace

        self.answer, self.sent, self.responseHandlers = answer, [], {}
        lora = SimpleNamespace(use_preset=True, modem_preset=5)  # 5 = SHORT_SLOW
        self.localNode = SimpleNamespace(localConfig=SimpleNamespace(lora=lora), channels=[])

    def sendData(
        self, payload, destinationId, portNum, wantResponse, onResponse, channelIndex, hopLimit
    ):
        from types import SimpleNamespace

        from meshtastic.protobuf import mesh_pb2

        self.sent.append((destinationId, channelIndex, hopLimit))
        pid = len(self.sent)
        self.responseHandlers[pid] = onResponse
        if self.answer:
            route = mesh_pb2.RouteDiscovery()
            route.snr_towards.append(-10)  # -2.5 dB
            onResponse(
                {
                    "decoded": {"portnum": "TRACEROUTE_APP", "payload": route.SerializeToString()},
                    "rxSnr": -7.25,
                    "rxRssi": -116,
                }
            )
        return SimpleNamespace(id=pid)


class FakeDevice:
    def __init__(self, iface):
        self.iface, self.state, self.error = iface, "verbunden", ""


def test_probe_walk_task_logs_probes(ctx):
    from meshplay.mapapp.jobs import ProbeWalk

    iface = FakeIface()
    ctx.device = FakeDevice(iface)
    jobs = JobManager(ctx, {"probe": ProbeWalk()})
    params = {"to": "!abcd1234", "channel": 1, "interval": 15, "timeout": 5, "count": 2}
    job = jobs.create("probe", params)
    wait_for(lambda: job.state == DONE, timeout=40)
    assert iface.sent == [("!abcd1234", 1, 0), ("!abcd1234", 1, 0)]  # direct only, channel 1
    assert job.result["answered"] == 2 and job.progress == 1.0
    logged = [json.loads(line) for line in next((ctx.data_dir / "probes").glob("*.jsonl")).open()]
    assert logged[0]["preset"] == "ShortSlow" and logged[0]["interval"] == 15
    assert logged[0]["snrTowards"] == -2.5 and logged[0]["snrBack"] == -7.25
    assert not iface.responseHandlers or len(iface.responseHandlers) == 0


def test_probe_walk_needs_a_device_and_valid_ids(ctx):
    from meshplay.mapapp.jobs import ProbeWalk

    class Offline:
        iface, state, error = None, "Fehler", "Port belegt"

        def connect(self, port):
            pass

    ctx.device = Offline()
    jobs = JobManager(ctx, {"probe": ProbeWalk()})
    with pytest.raises(ValueError, match="Node-ID"):
        jobs.create("probe", {"to": "tracker", "interval": 60, "timeout": 20})
    with pytest.raises(ValueError, match="kürzer"):
        jobs.create("probe", {"to": "!abcd1234", "interval": 20, "timeout": 20})
    job = jobs.create("probe", {"to": "!abcd1234", "interval": 60, "timeout": 20})
    wait_for(lambda: job.state == FAILED)
    assert "Port belegt" in job.error
