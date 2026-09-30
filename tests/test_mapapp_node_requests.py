"""Traceroutes and position requests from the node list, over the simulated radio."""

import time

import pytest

from meshplay.mapapp.device import DeviceLink, Simulation
from meshplay.mapapp.fake_device import CLIENT_NUM, HOME_NUM, TRACKER_NUM, FakeInterface, node_id
from meshplay.mapapp.node_requests import NodeRequests

HOME = (50.7374, 7.0982)
TRACKER, CLIENT = node_id(TRACKER_NUM), node_id(CLIENT_NUM)


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


@pytest.fixture
def req(tmp_path):
    from pubsub import pub

    d = DeviceLink(tmp_path, simulate=Simulation(HOME))
    pub.subscribe(d._on_receive, "meshtastic.receive")  # what connect() does
    d.iface, d.state, d.port = FakeInterface(HOME), "verbunden", "sim"  # the tracker stays put
    yield NodeRequests(d)
    d.disconnect()


def state(req, node, kind):
    return req.get().get(node, {}).get(kind, {}).get("state")


def test_traceroute_there_and_back(req):
    assert req.traceroute(CLIENT)["state"] == "läuft"
    wait_for(lambda: state(req, CLIENT, "traceroute") == "ok")
    r = req.get()[CLIENT]["traceroute"]
    # the fake client is heard through the tracker, both ways
    assert [h["id"] for h in r["towards"]] == [node_id(HOME_NUM), TRACKER, CLIENT]
    assert [h["id"] for h in r["back"]] == [CLIENT, TRACKER, node_id(HOME_NUM)]
    assert r["towards"][0]["snr"] is None and r["towards"][1]["short"] == "SIM"
    assert all(h["snr"] is not None for h in r["towards"][1:] + r["back"][1:])
    # positions and SNR colours for the map
    assert all("lat" in h and h["color"].startswith("#") for h in r["towards"] + r["back"])
    assert r["towards"][-1]["lat"] == req.device.iface.nodes[CLIENT]["position"]["latitude"]
    sent = req.device.iface.sent[-1]
    assert sent["to"] == CLIENT and sent["channel"] == 0
    assert req.device.sent.summary("ShortSlow")["kinds"]["traceroute"]["packets"] == 1


def test_one_request_at_a_time_and_a_gap(req):
    req.traceroute(TRACKER)
    with pytest.raises(ValueError, match="läuft schon"):
        req.traceroute(TRACKER)
    wait_for(lambda: state(req, TRACKER, "traceroute") == "ok")
    with pytest.raises(ValueError, match="erst in"):  # each traceroute floods the mesh
        req.traceroute(TRACKER)
    assert len(req.device.iface.sent) == 1


def test_routing_error_and_timeout(req):
    req.traceroute(TRACKER)
    item = req._items[TRACKER]["traceroute"]
    handler = req.device.iface.responseHandlers.pop(item["packet"])
    handler(
        {
            "from": HOME_NUM,
            "decoded": {"portnum": "ROUTING_APP", "routing": {"errorReason": "NO_RESPONSE"}},
        }
    )
    r = req.get()[TRACKER]["traceroute"]
    assert (r["state"], r["error"]) == ("Fehler", "NO_RESPONSE")

    req.traceroute(CLIENT)
    item = req._items[CLIENT]["traceroute"]
    item["wait_s"] = 0
    assert state(req, CLIENT, "traceroute") == "keine Antwort"
    assert item["packet"] not in req.device.iface.responseHandlers  # a late reply is ignored


def test_position_request(req):
    req.device.iface.nodes[TRACKER]["channel"] = 1  # heard on the private channel
    assert req.position(TRACKER)["state"] == "läuft"
    assert req.device.iface.sent[-1]["channel"] == 1
    wait_for(lambda: state(req, TRACKER, "position") == "ok")
    r = req.get()[TRACKER]["position"]
    assert abs(r["lat"] - HOME[0]) < 0.01 and r["done"] >= r["at"]
    with pytest.raises(ValueError, match="erst in"):  # the firmware would not answer yet
        req.position(TRACKER)


def test_unanswered_position_request_may_be_repeated(req):
    req.position(CLIENT)  # the fake client never answers
    req._items[CLIENT]["position"]["wait_s"] = 0
    assert state(req, CLIENT, "position") == "keine Antwort"
    assert req.position(CLIENT)["state"] == "läuft"


def test_not_connected(req):
    req.device.disconnect()
    with pytest.raises(ValueError, match="nicht verbunden"):
        req.traceroute(TRACKER)
    with pytest.raises(ValueError, match="nicht verbunden"):
        req.position(TRACKER)
    assert req.get() == {}  # nothing left behind
