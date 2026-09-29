"""The simulated radio (--simulate): positions, acknowledgements, spoken commands, traceroutes."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from meshplay.mapapp.device import DeviceLink, Simulation
from meshplay.mapapp.fake_device import TRACKER_NUM, FakeInterface, node_id

HOME = (50.7374, 7.0982)


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def track(n: int, step_s: float = 1.0) -> list[dict]:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {"time": t0 + timedelta(seconds=i * step_s), "lat": HOME[0] + i * 1e-4, "lon": HOME[1]}
        for i in range(n)
    ]


@pytest.fixture
def dev(tmp_path):
    from pubsub import pub

    d = DeviceLink(tmp_path, log_packets=True, simulate=Simulation(HOME))
    assert not d.log_packets  # simulated packets never reach data/packets/
    pub.subscribe(d._on_receive, "meshtastic.receive")  # what connect() does
    d.iface, d.state, d.port = FakeInterface(HOME, track(4), speed=10), "verbunden", "sim"
    yield d
    d.disconnect()


def test_track_replay_reaches_the_link(dev):
    wait_for(lambda: dev.packets >= 3)
    positions = [p for p in dev.messages.traffic_since(0) if p["port"] == "POSITION_APP"]
    assert positions and all(p["from"] == node_id(TRACKER_NUM) for p in positions)
    nodes, me = dev.nodes()
    assert me == 0xFA4E0000
    assert nodes[node_id(TRACKER_NUM)]["position"]["latitude"] > HOME[0]
    assert nodes[node_id(TRACKER_NUM)]["isFavorite"] is True
    assert dev.messages.path.name == "messages-sim.jsonl"


def test_sent_texts_are_acknowledged_and_spoken(dev):
    msg = dev.send_text(">?", node_id(TRACKER_NUM), 0)
    wait_for(lambda: dev.messages.since(msg["rev"]) and dev.messages.since(0)[-1]["dir"] == "in")
    by_dir = {m["dir"]: m for m in dev.messages.since(0)}
    assert by_dir["out"]["status"] == "zugestellt"
    assert (by_dir["in"]["from"], by_dir["in"]["text"]) == (node_id(TRACKER_NUM), "?")
    plain = dev.send_text("hallo", "^all", 1)
    wait_for(lambda: plain["status"] != "gesendet")  # the record is updated in place
    assert plain["status"] == "im Netz"


def test_traceroute_is_answered(dev):
    from meshplay.probe import probe_once

    rec = probe_once(dev.iface, node_id(TRACKER_NUM), 1, timeout=5)
    assert rec["result"] == "ok"
    assert rec["snrTowards"] is not None and rec["relays"] == 0
    assert not dev.iface.responseHandlers


def test_connect_builds_the_fake_interface(tmp_path):
    d = DeviceLink(tmp_path, simulate=Simulation(HOME, None, 1.0))
    d.connect("COM99")  # the port is ignored in a simulation
    wait_for(lambda: d.state == "verbunden")
    assert d.port == "sim" and isinstance(d.iface, FakeInterface)
    assert d.channels()[1]["name"] == "Privat"
    d.disconnect()
    assert d.state == "getrennt"
