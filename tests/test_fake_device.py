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


def track(n: int, step_s: float = 70.0, step_m: float = 120.0) -> list[dict]:
    """A walk north from home; the defaults make every point a smart broadcast."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "time": t0 + timedelta(seconds=i * step_s),
            "lat": HOME[0] + i * step_m / 111_320,
            "lon": HOME[1],
        }
        for i in range(n)
    ]


@pytest.fixture
def dev(tmp_path):
    from pubsub import pub

    d = DeviceLink(tmp_path, log_packets=True, simulate=Simulation(HOME))
    assert not d.log_packets  # simulated packets never reach data/packets/
    pub.subscribe(d._on_receive, "meshtastic.receive")  # what connect() does
    d.iface, d.state, d.port = FakeInterface(HOME, track(4), speed=200), "verbunden", "sim"
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


def test_favorite_is_set_on_the_device(dev):
    """An admin message to the own node; the node list and the pane's list follow at once."""
    from meshplay.mapapp.fake_device import CLIENT_NUM, HOME_NUM

    client = node_id(CLIENT_NUM)
    assert dev.favorites() == [node_id(TRACKER_NUM)]
    assert dev.set_favorite(client, True) == {"id": client, "favorite": True}
    assert dev.iface.admin[-1].set_favorite_node == CLIENT_NUM
    assert dev.nodes()[0][client]["isFavorite"] is True
    dev.set_favorite(node_id(TRACKER_NUM), False)
    assert dev.iface.admin[-1].remove_favorite_node == TRACKER_NUM
    assert dev.favorites() == [client]
    with pytest.raises(ValueError, match="kennt den Knoten"):
        dev.set_favorite("!00000001", True)
    with pytest.raises(ValueError, match="eigene"):
        dev.set_favorite(node_id(HOME_NUM), True)
    dev.state = "getrennt"
    with pytest.raises(ValueError, match="nicht verbunden"):
        dev.set_favorite(client, True)


def test_smart_broadcast_and_position_request(tmp_path):
    """Every 30 m in 10 s: broadcast at the start and after 180 m (100 m and 60 s passed); a
    position request is answered with where the tracker is, once per REPLY_GAP_S."""
    from pubsub import pub

    got = []

    def on_packet(packet, interface=None):  # held here: pubsub keeps weak references
        if packet["decoded"].get("portnum") == "POSITION_APP":
            north_m = (packet["decoded"]["position"]["latitude"] - HOME[0]) * 111_320
            got.append((packet["toId"], packet["channel"], round(north_m)))

    pub.subscribe(on_packet, "meshtastic.receive")
    d = DeviceLink(tmp_path, simulate=Simulation(HOME))
    d.iface = FakeInterface(HOME, track(10, step_s=10, step_m=30), speed=50)
    d.state, d.port = "verbunden", "sim"
    tracker = node_id(TRACKER_NUM)
    wait_for(lambda: len(got) >= 2)
    time.sleep(0.8)  # the rest of the track (90 s / 50): no third broadcast
    assert got == [("^all", 1, 0), ("^all", 1, 180)]
    d.request_position(tracker, 1)
    d.request_position(tracker, 1)  # within the firmware's gap: not answered
    wait_for(lambda: len(got) >= 3)
    time.sleep(1.2)
    assert got[2:] == [("!fa4e0000", 1, 270)]  # where it is, not where it was last heard
    pub.unsubscribe(on_packet, "meshtastic.receive")
    d.disconnect()


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
