"""Coordination mode: radio phrases, paths, and the Coordinator over a fake radio."""

import re
import time

import pytest

from meshplay.config import Settings
from meshplay.mapapp.coord import phrases
from meshplay.mapapp.coord.geo import bearing_deg, compass, fmt_dist, fmt_eta, parse_time
from meshplay.mapapp.coord.missions import (
    ABORTED,
    ARRIVED,
    ASSIGNED,
    ENDED,
    HOLDING,
    UNDERWAY,
    Coordinator,
)
from meshplay.mapapp.coord.paths import parse_path
from meshplay.mapapp.device import DeviceLink
from meshplay.mapapp.registry import Context
from tests.test_mapapp_messages import FakeIface

NODE = "!abcd1234"
NUM = 0xABCD1234
HOME = (50.7374, 7.0982)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


# ---------------------------------------------------------------- geo, phrases, paths
def test_geo_helpers():
    assert compass(bearing_deg(HOME, (HOME[0] + 0.01, HOME[1]))) == "N"
    assert compass(bearing_deg(HOME, (HOME[0], HOME[1] + 0.01))) == "E"
    assert compass(bearing_deg(HOME, (HOME[0] - 0.01, HOME[1] - 0.016))) == "SW"
    assert (fmt_dist(4), fmt_dist(347), fmt_dist(994), fmt_dist(1250)) == (
        "10m",
        "350m",
        "990m",
        "1.2km",
    )
    assert (fmt_eta(20), fmt_eta(671), fmt_eta(None)) == ("~1min", "~11min", "")
    now = time.mktime((2026, 9, 29, 12, 0, 0, 0, 0, -1))
    assert parse_time("12:55", now) == now + 55 * 60
    assert parse_time("+15", now) == now + 15 * 60
    with pytest.raises(ValueError):
        parse_time("25:99", now)


def test_phrases_stay_short_in_both_languages():
    def params(name):
        return dict(
            target=name,
            next=name,
            dist="1.2km",
            dir="NW",
            eta="~12min",
            speed="4.5km/h",
            time="12:55",
            until="13:05",
            eta_time="12:58",
            margin="+2",
            off="150m",
            legs="N200 L300 Hauptstr R150 Z60",
            travelled="1.3km",
            duration="17min",
            stops=f"{name} 12:55 > {name} > {name}",
        )

    for lang in phrases.LANGS:
        for key in phrases.PHRASES[lang]:
            text = phrases.phrase(lang, key, **params("X" * 12))  # names longer than advised
            assert "{" not in text and "  " not in text
            assert phrases.fits(text, phrases.TARGET_BYTES) or key == "path", (lang, key, text)
            assert phrases.fits(phrases.phrase(lang, key, **params("X" * 24))), (lang, key)
    assert set(phrases.PHRASES["en"]) == set(phrases.PHRASES["de"])


def test_parse_command():
    assert phrases.parse_command(" ?R ") == "route"
    assert phrases.parse_command("?") == "status"
    assert phrases.parse_command("?zz") == "help"
    assert phrases.parse_command("HALT") == "halt"
    assert phrases.parse_command("x") == "abort"
    assert phrases.parse_command("bin gleich da") is None
    assert phrases.parse_command("") is None


def test_parse_path_validation():
    now = time.time()
    with pytest.raises(ValueError, match="mindestens ein Ziel"):
        parse_path([], now)
    with pytest.raises(ValueError, match="Name"):
        parse_path([{"name": "mit leerzeichen", "lat": 50, "lon": 7}], now)
    with pytest.raises(ValueError, match="zweimal"):
        parse_path([{"name": "A", "lat": 50, "lon": 7}, {"name": "a", "lat": 50, "lon": 7}], now)
    with pytest.raises(ValueError, match="letzte Wegpunkt"):
        parse_path([{"name": "A", "lat": 50, "lon": 7, "kind": "via"}], now)
    with pytest.raises(ValueError, match="Uhrzeit"):
        parse_path([{"name": "A", "lat": 50, "lon": 7, "arrive_by": "morgen"}], now)
    with pytest.raises(ValueError, match="vor Ankunft"):
        parse_path(
            [{"name": "A", "lat": 50, "lon": 7, "arrive_by": "+30", "hold_until": "+10"}], now
        )
    path = parse_path(
        [
            {"name": "B", "lat": 50.7, "lon": 7.1, "kind": "via"},
            {"name": "C", "lat": 50.7, "lon": 7.1, "arrive_by": "+30", "hold_until": "+40"},
        ],
        now,
        25,
    )
    assert [w.kind for w in path] == ["via", "stop"]
    assert path[0].radius_m == 25 and path[1].hold_until == pytest.approx(now + 2400)


# ---------------------------------------------------------------- coordinator
@pytest.fixture
def coord(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from meshplay.mapapp.coord import missions

    # the tests run in seconds: warnings and arrivals may follow each other at once
    monkeypatch.setattr(missions, "PRIORITY_GAP_S", 0)
    ctx = Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=HOME))
    dev = DeviceLink(tmp_path, log_packets=False)
    dev.iface, dev.state = FakeIface(), "verbunden"
    dev.iface.myInfo = SimpleNamespace(my_node_num=0x11112222)
    ctx.device = dev
    c = Coordinator(ctx)
    c.set_enabled(True)
    c.settings["min_gap_s"] = 120
    yield c
    c.shutdown()


def position(lat, lon, bits=32, speed=None):
    pos = {"latitude": lat, "longitude": lon, "precisionBits": bits}
    if speed is not None:
        pos["groundSpeed"] = speed
    return {
        "id": 5,
        "from": NUM,
        "fromId": NODE,
        "to": 0xFFFFFFFF,
        "channel": 1,
        "rxSnr": 3.5,
        "rxRssi": -100,
        "hopStart": 3,
        "hopLimit": 3,
        "decoded": {"portnum": "POSITION_APP", "position": pos},
    }


def text(t, to=0x11112222):
    return {
        "id": 6,
        "from": NUM,
        "fromId": NODE,
        "to": to,
        "channel": 0,
        "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": t},
    }


def north(m):  # a point m metres north of home
    return HOME[0] + m / 111_320, HOME[1]


def feed(coord, packet, count=None):
    """A packet through the link, waiting until the worker has taken it."""
    dev = coord.ctx.device
    before = len(coord.missions[NODE].positions) if count is None else count
    dev._on_receive(packet, dev.iface)
    if packet["decoded"]["portnum"] == "POSITION_APP":
        wait_for(lambda: len(coord.missions[NODE].positions) > before)
    else:
        wait_for(lambda: coord._queue.empty())
        time.sleep(0.05)


def sent_texts(coord):
    return [s["data"].decode() for s in coord.ctx.device.iface.sent]


def test_assign_guides_and_confirms_arrival(coord):
    m = coord.assign(NODE, [{"name": "ALPHA", "lat": north(800)[0], "lon": HOME[1]}], "foot", "de")
    assert m.state == ASSIGNED
    assert sent_texts(coord) == ["#ALPHA zugewiesen, keine Position von dir"]
    feed(coord, position(*HOME))
    m = coord.missions[NODE]
    assert sent_texts(coord)[-1] == "#ALPHA 800m N ~11min"  # the leg follows the first position
    assert m.state == UNDERWAY and m.metrics["compass"] == "N"
    assert abs(m.metrics["dist_m"] - 800) <= 1 and m.metrics["speed_source"] == "Standard"
    assert abs(m.metrics["eta_s"] - 640) <= 1  # 800 m at 4.5 km/h
    coord.mission_action(NODE, "status", {})
    assert sent_texts(coord)[-1] == "#ALPHA 800m N ~11min 4.5km/h"
    feed(coord, position(*north(400)))
    feed(coord, position(*north(785)))
    assert coord.missions[NODE].state == ARRIVED
    assert re.match(r"#ALPHA erreicht \d\d:\d\d 7[89]0m 1min$", sent_texts(coord)[-1])
    assert (coord.ctx.data_dir / "coord" / "missions.json").exists()


def test_off_course_and_rate_limit(coord):
    coord.settings["min_gap_s"] = 120
    coord.assign(NODE, [{"name": "Z", "lat": north(1000)[0], "lon": HOME[1]}], "foot", "de")
    feed(coord, position(*north(300)))
    for d in (240, 180, 120):  # walking away three times in a row
        feed(coord, position(*north(d)))
    msgs = sent_texts(coord)
    assert msgs[-1] == "!KURS 120m ab. N 820m"  # warned on the third position walking away
    assert "offcourse" in coord.missions[NODE].flags
    feed(coord, position(*north(60)))  # still away: no second warning
    assert sent_texts(coord) == msgs
    feed(coord, position(*north(400)))  # back on the way: the warning re-arms
    assert "offcourse" not in coord.missions[NODE].flags
    # a proactive message right after another is skipped, a reply is not
    coord.settings["confirm_every_min"] = 1
    coord.missions[NODE].assigned_at -= 120
    feed(coord, position(*north(450)))
    assert sent_texts(coord) == msgs  # confirm skipped: the !KURS was too recent
    feed(coord, text("?"))
    assert sent_texts(coord)[-1].startswith("#Z 550m N")
    events = [e["kind"] for e in coord.missions[NODE].events]
    assert "skipped" in events and "command" in events


def test_commands_halt_go_abort_and_strangers(coord):
    coord.assign(NODE, [{"name": "Z", "lat": north(500)[0], "lon": HOME[1]}], "foot", "en")
    feed(coord, position(*HOME))
    n = len(sent_texts(coord))
    feed(coord, text("?h"))
    assert sent_texts(coord)[-1] == "? status ?R route ?Z target ?P path HALT GO X"
    feed(coord, text("?z"))
    assert sent_texts(coord)[-1] == "#Z 500m N"
    feed(coord, text("?r"))
    assert sent_texts(coord)[-1] == "R: N500m Z"
    feed(coord, text("?p"))
    assert sent_texts(coord)[-1] == "Z"
    feed(coord, text("halt"))
    assert sent_texts(coord)[-1] == "HALT ok" and coord.missions[NODE].held
    feed(coord, text("go"))
    assert sent_texts(coord)[-1] == "Next #Z 500m N" and not coord.missions[NODE].held
    feed(coord, text("hello there"))  # plain chat: no reply
    assert len(sent_texts(coord)) == n + 6
    feed(coord, text("?", to=0x99999999))  # not addressed to us
    assert len(sent_texts(coord)) == n + 6
    feed(coord, text("x"))
    assert coord.missions[NODE].state == ABORTED
    assert sent_texts(coord)[-1] == "#Z aborted"
    feed(coord, text("?"))  # no mission any more: silence
    assert len(sent_texts(coord)) == n + 7
    with pytest.raises(ValueError, match="nicht mehr aktiv"):
        coord.mission_action(NODE, "status", {})


def test_mode_off_sends_nothing_and_restart_restores(coord, tmp_path):
    coord.set_enabled(False)
    coord.assign(NODE, [{"name": "Z", "lat": north(500)[0], "lon": HOME[1]}], "foot", "de")
    feed(coord, position(*HOME))
    feed(coord, text("?"))
    assert sent_texts(coord) == []
    assert coord.missions[NODE].events[-1]["kind"] == "suppressed"
    coord.shutdown()
    again = Coordinator(coord.ctx)
    m = again.missions[NODE]
    assert (m.state, m.stop.name, len(m.positions), again.enabled) == (UNDERWAY, "Z", 1, False)
    again.shutdown()


def test_precision_and_end(coord):
    coord.assign(NODE, [{"name": "Z", "lat": north(500)[0], "lon": HOME[1]}], "foot", "de")
    dev = coord.ctx.device
    dev._on_receive(position(*HOME, bits=16), dev.iface)
    wait_for(
        lambda: (
            coord.missions[NODE].events
            and coord.missions[NODE].events[-1]["kind"] == "position_ignored"
        )
    )
    assert not coord.missions[NODE].positions
    coord.mission_action(NODE, "end", {})
    assert coord.missions[NODE].state == ENDED
    assert sent_texts(coord)[-1] == "#Z aufgehoben"
    coord.mission_action(NODE, "remove", {})
    assert NODE not in coord.missions
    with pytest.raises(KeyError):
        coord.mission_action(NODE, "status", {})


def test_multi_stop_path_with_hold(coord):
    coord.settings["min_gap_s"] = 0
    path = [
        {"name": "V", "lat": north(200)[0], "lon": HOME[1], "kind": "via"},
        {"name": "B", "lat": north(400)[0], "lon": HOME[1], "hold_until": "+1"},
        {"name": "C", "lat": north(800)[0], "lon": HOME[1]},
    ]
    coord.assign(NODE, path, "foot", "de")
    feed(coord, position(*HOME))
    assert sent_texts(coord)[-1] == "#V 200m N ~3min"
    feed(coord, position(*north(190)))  # via passed silently
    m = coord.missions[NODE]
    assert m.index == 1 and sent_texts(coord)[-1] == "#V 200m N ~3min"
    feed(coord, position(*north(395)))
    assert m.state == HOLDING
    assert (
        sent_texts(coord)[-1].startswith("#B erreicht ") and "Warten bis" in sent_texts(coord)[-1]
    )
    feed(coord, position(*north(500)))  # left early
    assert sent_texts(coord)[-1].startswith("!FRUEH B")
    m.stop.hold_until = time.time() - 1  # the hold is over
    coord._tick(time.time())
    assert m.state == UNDERWAY and m.index == 2
    assert sent_texts(coord)[-1].startswith("Weiter #C 300m N")
    coord.mission_action(NODE, "status", {})
    assert "#C 300m N" in sent_texts(coord)[-1]


def test_edit_path_while_running(coord):
    coord.settings["min_gap_s"] = 0
    path = [
        {"name": "A", "lat": north(200)[0], "lon": HOME[1]},
        {"name": "B", "lat": north(600)[0], "lon": HOME[1]},
    ]
    coord.assign(NODE, path, "foot", "de")
    feed(coord, position(*HOME))
    feed(coord, position(*north(195)))  # A reached, B is current
    m = coord.missions[NODE]
    assert m.index == 1 and sent_texts(coord)[-1].startswith("#A erreicht")
    n = len(sent_texts(coord))
    # a later waypoint added: the current leg is unchanged, the node hears nothing
    coord.api(
        "POST",
        ["missions", NODE, "path"],
        {},
        {"path": path + [{"name": "C", "lat": north(900)[0], "lon": HOME[1]}]},
    )
    assert m.index == 1 and m.stop.name == "B" and len(sent_texts(coord)) == n
    # the current stop moved: the node gets the new leg
    moved = [path[0], {"name": "B", "lat": north(700)[0], "lon": HOME[1]}]
    coord.api("POST", ["missions", NODE, "path"], {}, {"path": moved})
    assert sent_texts(coord)[-1] == "#B neu 500m N ~7min"
    # the current stop removed: the next one becomes current; passed ones stay passed
    coord.api(
        "POST",
        ["missions", NODE, "path"],
        {},
        {"path": [path[0], {"name": "D", "lat": north(400)[0], "lon": HOME[1]}]},
    )
    assert m.index == 1 and m.stop.name == "D" and sent_texts(coord)[-1] == "#D neu 200m N ~3min"
    assert m.events[-1]["kind"] == "sent"
    with pytest.raises(ValueError, match="letzte Wegpunkt"):
        coord.api(
            "POST",
            ["missions", NODE, "path"],
            {},
            {"path": [{"name": "E", "lat": 50.7, "lon": 7.1, "kind": "via"}]},
        )


def test_path_templates(coord):
    with pytest.raises(ValueError, match="Name"):
        coord.api("POST", ["paths", "save"], {}, {"name": "", "path": []})
    coord.api(
        "POST",
        ["paths", "save"],
        {},
        {
            "name": "RUNDE",
            "path": [
                {"name": "A", "lat": 50.7, "lon": 7.1, "kind": "via"},
                {"name": "B", "lat": 50.71, "lon": 7.1, "arrive_by": "+10", "radius_m": 40},
            ],
        },
    )
    saved = coord.api("GET", [], {}, {})["paths"]["RUNDE"]
    assert [w["name"] for w in saved] == ["A", "B"] and "arrive_by" not in saved[1]
    assert saved[1]["radius_m"] == 40
    coord.api("POST", ["paths", "delete"], {}, {"name": "RUNDE"})
    assert coord.api("GET", [], {}, {})["paths"] == {}
    with pytest.raises(KeyError):
        coord.api("POST", ["paths", "delete"], {}, {"name": "RUNDE"})


def test_api_dispatch_and_targets(coord):
    snap = coord.api("GET", [], {}, {})
    assert snap["enabled"] and snap["missions"] == [] and snap["declarations"]
    coord.api(
        "POST", ["targets", "add"], {}, {"name": "ALPHA", "lat": 50.7, "lon": 7.1, "note": "x"}
    )
    assert coord.api("GET", [], {}, {})["targets"]["ALPHA"]["note"] == "x"
    with pytest.raises(ValueError, match="gibt es schon"):
        coord.api("POST", ["targets", "add"], {}, {"name": "ALPHA", "lat": 50.7, "lon": 7.1})
    coord.api("POST", ["targets", "delete"], {}, {"name": "ALPHA"})
    assert coord.api("GET", [], {}, {})["targets"] == {}
    with pytest.raises(ValueError, match="Node-ID"):
        coord.api(
            "POST",
            ["missions"],
            {},
            {"node": "tracker", "path": [{"name": "A", "lat": 50, "lon": 7}]},
        )
    coord.api("POST", ["settings"], {}, {"min_gap_s": 60, "channel": "0"})
    assert coord.settings["min_gap_s"] == 60 and coord.settings["channel"] == 0
    with pytest.raises(ValueError, match="mindestens"):
        coord.api("POST", ["settings"], {}, {"min_gap_s": 5})
    with pytest.raises(KeyError):
        coord.api("POST", ["nothing"], {}, {})
    coord.ctx.device.state = "getrennt"
    with pytest.raises(ValueError, match="nicht verbunden"):
        coord.api("POST", ["mode"], {}, {"on": True})
