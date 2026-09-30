"""Markers: field nodes set targets by radio (+D) and take one as their mission (?D)."""

import pytest

from meshplay.mapapp.coord import phrases
from meshplay.mapapp.coord.missions import ARRIVED, Coordinator
from tests.test_coord import HOME, NODE, feed, north, position, sent_texts, text

OTHER = "!abcd5678"
OTHER_NUM = 0xABCD5678


def seen(coord, lat, lon, bits=32, channel=1):
    """A position broadcast of NODE without a mission (on the private channel by default):
    only remembered, nothing queued."""
    dev = coord.ctx.device
    dev._on_receive({**position(lat, lon, bits), "channel": channel}, dev.iface)


def on_channel(coord, node=NODE, num=None):
    """A node info broadcast with the private channel's key, without a position."""
    dev = coord.ctx.device
    packet = {
        "id": 7,
        "from": num or int(node[1:], 16),
        "fromId": node,
        "to": 0xFFFFFFFF,
        "channel": 1,
        "decoded": {"portnum": "NODEINFO_APP", "user": {"id": node}},
    }
    dev._on_receive(packet, dev.iface)


def test_parse_marker():
    assert phrases.parse_marker("+d s1 Storage  Box") == ("set", "S1", "Storage Box")
    assert phrases.parse_marker(" +D kiste ") == ("set", "KISTE", "")
    assert phrases.parse_marker("?d s1") == ("goto", "S1", "")
    assert phrases.parse_marker("?D") == ("goto", "", "")
    assert phrases.parse_marker("+d") == ("set", "", "")
    assert phrases.parse_marker("?dx") is None  # stays the help of the mission commands
    assert phrases.parse_marker("?") is None and phrases.parse_marker("dies und das") is None
    assert len(phrases.parse_marker("+d a " + "x" * 80)[2]) == phrases.LABEL_MAX


def test_set_marker_from_a_node_without_mission(coord):
    seen(coord, *north(200))
    feed(coord, text("+d s1 Storage Box"))
    assert sent_texts(coord) == ["D S1 gesetzt Storage Box"]
    t = coord.targets["S1"]
    assert (t["lat"], t["lon"], t["note"], t["by"]) == (
        pytest.approx(north(200)[0]),
        pytest.approx(HOME[1]),
        "Storage Box",
        NODE,
    )
    assert NODE not in coord.missions  # setting a marker is not a mission
    feed(coord, text("+D S1 other"))
    assert sent_texts(coord)[-1] == "D S1 gibt es schon"
    feed(coord, text("+d mit/strich"))
    assert sent_texts(coord)[-1].startswith("+D NAME Text")
    stored = Coordinator(coord.ctx)  # on disk, for the page and a restart
    assert stored.targets["S1"]["by"] == NODE
    stored.shutdown()
    # the page edits it like any target and keeps who set it
    coord.api("POST", ["targets", "update"], {}, {"name": "S1", "note": "Kiste"})
    assert coord.targets["S1"]["by"] == NODE and coord.targets["S1"]["note"] == "Kiste"


def test_set_marker_needs_a_fresh_precise_position(coord):
    on_channel(coord)
    feed(coord, text("+d s1"))
    assert sent_texts(coord) == ["+D S1: keine Position von dir"]
    seen(coord, *north(200), bits=13)  # reduced precision: kilometres
    feed(coord, text("+d s1"))
    assert sent_texts(coord)[-1] == "+D S1: keine Position von dir" and not coord.targets
    coord._seen[NODE] = {**coord._seen[NODE], "bits": 32, "time": 0}  # hours old
    feed(coord, text("+d s1"))
    assert not coord.targets


def test_goto_named_and_nearest_marker(coord):
    coord.targets.update(
        S1={"lat": north(800)[0], "lon": HOME[1], "radius_m": None, "note": "Storage Box"},
        S2={"lat": north(300)[0], "lon": HOME[1], "radius_m": None, "note": ""},
    )
    seen(coord, *HOME)
    feed(coord, text("?d s1"))
    m = coord.missions[NODE]
    assert (m.stop.name, m.label, m.origin) == ("S1", "Storage Box", "radio")
    assert sent_texts(coord)[-1] == "#S1 Storage Box 800m N ~11min"
    feed(coord, text("?"))  # now an ordinary mission
    assert sent_texts(coord)[-1] == "#S1 800m N ~11min 4.5km/h"
    feed(coord, text("?d"))  # the nearest replaces it
    m = coord.missions[NODE]
    assert m.stop.name == "S2" and sent_texts(coord)[-1] == "#S2 300m N ~4min"
    feed(coord, position(*north(290)))
    assert m.state == ARRIVED
    feed(coord, text("?d"))  # at S2 already: the next one
    assert coord.missions[NODE].stop.name == "S1"
    assert sent_texts(coord)[-1] == "#S1 Storage Box 510m N ~7min"
    feed(coord, text("?d s9"))
    assert sent_texts(coord)[-1] == "D S9 unbekannt. ?D fuehrt zur naechsten"


def test_marker_setting_and_mode(coord):
    on_channel(coord)
    coord.settings["lang"] = "en"
    feed(coord, text("?d"))
    assert sent_texts(coord) == ["No markers. Set one with +D NAME text"]
    coord.settings["markers"] = "missions"
    n = len(sent_texts(coord))
    feed(coord, text("?d"))  # a node without a mission is not answered
    coord.assign(NODE, [{"name": "Z", "lat": north(500)[0], "lon": HOME[1]}], "foot", "de")
    feed(coord, text("?d"))  # with one it is, in its mission's language
    assert sent_texts(coord)[n:] == [
        "#Z zugewiesen, keine Position von dir",
        "Keine Marken. Setzen mit +D NAME Text",
    ]
    coord.settings["markers"] = "off"
    feed(coord, text("?d"))
    coord.settings["markers"] = "all"
    coord.set_enabled(False)
    feed(coord, text("?d"))
    assert len(sent_texts(coord)) == n + 2


def test_markers_from_two_nodes(coord):
    """Replies go to the sender; another node's position is not used."""
    dev = coord.ctx.device
    other = {**position(*north(100)), "from": OTHER_NUM, "fromId": OTHER}
    dev._on_receive(other, dev.iface)
    feed(coord, {**text("+d box"), "from": OTHER_NUM, "fromId": OTHER})
    assert coord.targets["BOX"]["by"] == OTHER
    assert dev.iface.sent[-1]["to"] == OTHER
    on_channel(coord)
    feed(coord, text("+d box2"))  # NODE sent no position
    assert "BOX2" not in coord.targets and dev.iface.sent[-1]["to"] == NODE


def test_only_nodes_on_the_private_channel_get_answers(coord):
    """A PKI direct message arrives as channel 0 whatever the sender picked: it does not show
    the channel. A packet decrypted with the channel's key does."""
    seen(coord, *north(200), channel=0)  # position on the public channel
    feed(coord, {**text("+d s1"), "pkiEncrypted": True})
    assert sent_texts(coord) == [] and not coord.targets
    assert not coord.on_channel(NODE)
    feed(coord, {**text("?d"), "channel": 1, "pkiEncrypted": True})  # PKI: channel meaningless
    assert sent_texts(coord) == []
    feed(coord, {**text("?d"), "channel": 1})  # a DM with the channel's key
    assert sent_texts(coord) == ["Keine Marken. Setzen mit +D NAME Text"]
    coord._on_channel[NODE] -= 5 * 3600  # long ago: not any more
    feed(coord, text("?d"))
    assert len(sent_texts(coord)) == 1
    seen(coord, *north(200))  # its position broadcast on the private channel
    feed(coord, {**text("+d s1 Kiste"), "pkiEncrypted": True})
    assert sent_texts(coord)[-1] == "D S1 gesetzt Kiste"
    coord.settings["channel"] = 2  # another channel for the messages: its key counts
    feed(coord, text("?d s1"))
    assert len(sent_texts(coord)) == 2


def test_old_setting_all_becomes_channel(coord):
    coord.settings["markers"] = "all"
    coord._save_settings()
    again = Coordinator(coord.ctx)
    assert again.settings["markers"] == "channel"
    again.shutdown()
