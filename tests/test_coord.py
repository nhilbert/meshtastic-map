"""Coordination mode: radio phrases, paths, and the Coordinator over a fake radio."""

import re
import time

import pytest

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
            name=name,
            text="Treffpunkt Ausgang Nord",
            avg="4.2km/h",
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
            label="L" * phrases.LABEL_MAX,
            cmd=f"+D {name}",
        )

    for lang in phrases.LANGS:
        for key in phrases.PHRASES[lang]:
            text = phrases.phrase(lang, key, **params("X" * 12))  # names longer than advised
            assert "{" not in text and "  " not in text
            long_ok = key in ("path", "legend", "help")  # listings, sent once or on request
            assert phrases.fits(text, phrases.TARGET_BYTES) or long_ok, (lang, key, text)
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


def feed(coord, packet):
    """A packet through the link, waiting until the worker has handled it."""
    dev = coord.ctx.device
    before = coord.processed
    dev._on_receive(packet, dev.iface)
    wait_for(lambda: coord.processed > before)


def sent_texts(coord):
    from meshtastic.protobuf import portnums_pb2

    text = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    return [s["data"].decode() for s in coord.ctx.device.iface.sent if s["port"] == text]


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
    assert sent_texts(coord)[-1].startswith(
        "? status ?R route ?Z target ?E arrival ?P path ?L legend HERE=at the stop HALT GO"
    )
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
    dev = coord.ctx.device
    dev._on_receive(text("?", to=0x99999999), dev.iface)  # not addressed to us: dropped early
    time.sleep(0.2)
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
    feed(coord, position(*north(190)))  # via passed: without a road graph the next leg follows
    m = coord.missions[NODE]
    assert m.index == 1 and sent_texts(coord)[-1] == "Weiter #B 210m N ~3min"
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


def test_guidance_over_the_road_graph(coord, tmp_path):
    from meshplay.mapapp.coord import osm
    from tests.test_coord_routing import grid_ways, pt

    osm.write_graph(osm.build_graph(grid_ways()), tmp_path / "osm" / "roads.json.gz")
    coord.settings["min_gap_s"] = 0
    snap = coord.api("GET", [], {}, {})
    assert snap["osm"]["name"] == "roads" and snap["osm"]["graphs"] == ["roads"]
    coord.assign(NODE, [{"name": "Z", "lat": pt(3, 0)[0], "lon": pt(3, 0)[1]}], "foot", "de")
    feed(coord, position(*pt(0, 0)))
    m = coord.missions[NODE]
    assert m.route is not None and abs(m.route.length_m - 300) < 2
    assert m.metrics["mode"] == "route" and m.metrics["route_left_m"] == 300
    assert sent_texts(coord)[-1] == "#Z 300m E ~4min R: E300m Row0str Z"
    feed(coord, text("?r"))
    assert sent_texts(coord)[-1] == "R: E300m Row0str Z"
    # walking the road: nothing to say (one straight leg), progress counts down
    feed(coord, position(*pt(1, 0)))
    assert m.metrics["route_left_m"] == 200 and m.off_count == 0
    n = len(sent_texts(coord))
    # 100 m north of the road twice in a row: off course, new route from there
    feed(coord, position(*pt(1.5, 1)))
    assert m.off_count == 1 and len(sent_texts(coord)) == n
    feed(coord, position(*pt(1.6, 1)))
    assert "offcourse" in m.flags
    assert sent_texts(coord)[-1] == "!KURS 100m ab. R: E40m Row1str R100m Col2 L100m Row0str Z"
    assert abs(m.route.length_m - 240) < 5  # east on Row1, down Col2, east on Row0
    # back on the new route: the flag re-arms; the turn 10 m ahead is announced once, and
    # the message covers the turn after it too
    feed(coord, position(*pt(1.9, 1)))
    assert "offcourse" not in m.flags and m.off_count == 0
    assert sent_texts(coord)[-1] == "R: R100m Col2 L100m Row0str Z"
    k = len(sent_texts(coord))
    feed(coord, position(*pt(1.95, 1)))  # still before the same turn: no repeat
    assert len(sent_texts(coord)) == k
    feed(coord, position(*pt(2, 0.5)))  # on Col2: the next turn was in the last message
    feed(coord, position(*pt(2, 0.3)))
    assert len(sent_texts(coord)) == k
    feed(coord, position(*pt(3, 0.2)))
    assert m.state == ARRIVED


def test_vias_with_a_route_are_silent(coord, tmp_path):
    from meshplay.mapapp.coord import osm
    from tests.test_coord_routing import grid_ways, pt

    osm.write_graph(osm.build_graph(grid_ways()), tmp_path / "osm" / "roads.json.gz")
    coord.settings["min_gap_s"] = 0
    path = [
        {"name": "V", "lat": pt(2, 0)[0], "lon": pt(2, 0)[1], "kind": "via"},
        {"name": "Z", "lat": pt(2, 2)[0], "lon": pt(2, 2)[1]},
    ]
    coord.assign(NODE, path, "foot", "de")
    feed(coord, position(*pt(0, 0)))
    m = coord.missions[NODE]
    # the route runs through the via to the stop, the message names the stop
    assert abs(m.route.length_m - 400) < 3
    assert sent_texts(coord)[-1] == "#Z 280m NE ~5min R: E200m Row0str L200m Col2 Z"
    feed(coord, position(*pt(1, 0)))  # the turn at the via 100 m ahead: said with the distance
    assert sent_texts(coord)[-1] == "R: E100m Row0str L200m Col2 Z"
    n = len(sent_texts(coord))
    feed(coord, position(*pt(1.7, 0)))  # 30 m before it: already said
    assert len(sent_texts(coord)) == n
    feed(coord, position(*pt(2.1, 0.1)))  # past the via: silently done, same route
    assert m.index == 1 and len(sent_texts(coord)) == n
    assert [e["kind"] for e in m.events if e["kind"] == "via"] == ["via"]
    assert abs(m.route.length_m - 400) < 3  # not rebuilt at the via


def test_essential_messages_beat_the_rate_limit(coord, monkeypatch):
    from meshplay.mapapp.coord import missions

    monkeypatch.setattr(missions, "PRIORITY_GAP_S", 30)
    coord.settings["min_gap_s"] = 120
    coord.assign(NODE, [{"name": "Z", "lat": north(300)[0], "lon": HOME[1]}], "foot", "de")
    assert sent_texts(coord) == ["#Z zugewiesen, keine Position von dir"]
    feed(coord, position(*HOME))  # a second later: the real assignment still goes out
    assert sent_texts(coord)[-1] == "#Z 300m N ~4min"
    for d in (60, 120, 180):  # walking away: the warning is rate-limited (30 s gap)
        feed(coord, position(*north(-d)))
    m = coord.missions[NODE]
    assert not any(t.startswith("!KURS") for t in sent_texts(coord))
    assert any(e["kind"] == "skipped" and e["what"] == "offcourse" for e in m.events)
    feed(coord, position(*north(290)))  # arrival is essential
    assert m.state == ARRIVED and sent_texts(coord)[-1].startswith("#Z erreicht")


def test_hold_over_on_a_shortened_path(coord):
    coord.settings["min_gap_s"] = 0
    path = [
        {"name": "B", "lat": north(200)[0], "lon": HOME[1], "hold_until": "+1"},
        {"name": "C", "lat": north(600)[0], "lon": HOME[1]},
    ]
    coord.assign(NODE, path, "foot", "de")
    feed(coord, position(*HOME))
    feed(coord, position(*north(195)))
    m = coord.missions[NODE]
    assert m.state == HOLDING
    # C removed; B re-sent as the page would (its time as hh:mm): still the same leg
    kept = {**path[0], "hold_until": time.strftime("%H:%M", time.localtime(m.stop.hold_until))}
    coord.api("POST", ["missions", NODE, "path"], {}, {"path": [kept]})
    assert m.state == HOLDING and m.events[-1]["changed"] is False
    m.stop.hold_until = time.time() - 1
    coord._tick(time.time())
    assert m.state == ARRIVED and m.index == 0
    coord._tick(time.time())  # a second tick is harmless
    feed(coord, position(*north(250)))
    assert coord.layer_features()  # the layer still renders


def test_eta_command(coord):
    coord.assign(NODE, [{"name": "Z", "lat": north(1000)[0], "lon": HOME[1]}], "foot", "de")
    feed(coord, text("?e"))
    assert sent_texts(coord)[-1] == "#Z zugewiesen, keine Position von dir"
    feed(coord, position(*HOME))
    feed(coord, text("?e"))
    t = sent_texts(coord)[-1]
    assert re.match(r"#Z Ankunft \d\d:\d\d \(~13min\) bei 4\.5km/h angenommen$", t)  # default
    m = coord.missions[NODE]
    # a measured speed: three legs of 100 m in 60 s each (6 km/h), backdated
    base = time.time() - 240
    m.positions.clear()
    for i in range(4):
        m.positions.append(
            {
                "time": base + i * 60,
                "lat": north(i * 100)[0],
                "lon": HOME[1],
                "bits": 32,
                "snr": 1,
                "rssi": -90,
                "hops": 0,
                "speed": None,
            }
        )
    m.travelled_m = 300
    feed(coord, text("?e"))
    t = sent_texts(coord)[-1]
    assert re.match(r"#Z Ankunft \d\d:\d\d \(~7min\) 6km/h jetzt, 6km/h Schnitt$", t), t
    feed(coord, text("?h"))
    assert "?E Ankunft" in sent_texts(coord)[-1]


def test_legend_once_and_on_request(coord):
    coord.settings["send_legend"] = True
    coord.assign(NODE, [{"name": "Z", "lat": north(300)[0], "lon": HOME[1]}], "foot", "de")
    assert sent_texts(coord) == ["#Z zugewiesen, keine Position von dir"]  # nothing to explain yet
    feed(coord, position(*HOME))
    texts = sent_texts(coord)
    assert texts[-2] == "#Z 300m N ~4min" and texts[-1].startswith("Legende: #Ziel")
    assert len(texts[-1].encode()) <= 200
    feed(coord, text("?l"))
    assert sent_texts(coord)[-1].startswith("Legende:")
    feed(coord, text("?h"))
    assert "?L Legende" in sent_texts(coord)[-1]
    m = coord.missions[NODE]
    assert m.legend_sent
    # a new mission for the same node explains again only if the setting says so
    coord.settings["send_legend"] = False
    coord.assign(NODE, [{"name": "Y", "lat": north(400)[0], "lon": HOME[1]}], "foot", "en")
    assert sent_texts(coord)[-1] == "#Y 400m N ~5min"


def test_exhausted_route_is_rebuilt(coord, tmp_path):
    from meshplay.mapapp.coord import osm
    from tests.test_coord_routing import grid_ways, pt

    osm.write_graph(osm.build_graph(grid_ways()), tmp_path / "osm" / "roads.json.gz")
    coord.settings["min_gap_s"] = 0
    # the stop is 60 m off the end of the road grid: the route ends at its snap point
    target = pt(4, 0)[0], pt(4.6, 0)[1]
    coord.assign(NODE, [{"name": "Z", "lat": target[0], "lon": target[1]}], "foot", "de")
    feed(coord, position(*pt(3, 0)))
    m = coord.missions[NODE]
    assert m.route is not None and abs(m.route.length_m - 100) < 3
    feed(coord, position(*pt(4, 0)))  # at the end of the route, 60 m from the stop
    assert m.state == UNDERWAY
    assert any(e["kind"] == "route_exhausted" for e in m.events)
    assert m.metrics["dist_m"] == pytest.approx(60, abs=2)


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


def test_turns_are_announced_before_the_next_position(coord, tmp_path):
    """Smart position sends every 100 m: a turn is said while it is closer than the node gets
    in NEXT_POSITION_S, with the distance to it; a rate-limited one is said at the next."""
    from meshplay.mapapp.coord import osm
    from tests.test_coord_routing import grid_ways, pt

    osm.write_graph(osm.build_graph(grid_ways()), tmp_path / "osm" / "roads.json.gz")
    coord.settings["min_gap_s"] = 0
    coord.assign(NODE, [{"name": "Z", "lat": pt(3, 0.5)[0], "lon": pt(3, 0.5)[1]}], "foot", "de")
    feed(coord, position(*pt(0, 0)))
    m = coord.missions[NODE]
    assert sent_texts(coord)[-1] == "#Z 300m E ~5min R: E300m Row0str L50m Col3 Z"
    n = len(sent_texts(coord))
    feed(coord, position(*pt(1, 0)))  # 200 m to the turn: more than 90 s at 4.5 km/h
    assert len(sent_texts(coord)) == n
    coord.settings["min_gap_s"] = 120
    feed(coord, position(*pt(2, 0)))  # 100 m: due, but the rate limit holds it back
    assert len(sent_texts(coord)) == n and m.announced == 0
    coord.settings["min_gap_s"] = 0
    feed(coord, position(*pt(2.3, 0)))
    assert sent_texts(coord)[-1] == "R: E70m Row0str L50m Col3 Z"
    feed(coord, position(*pt(2.8, 0)))  # the same turn: said once
    assert len(sent_texts(coord)) == n + 1


def test_position_requests(coord):
    """The node is asked for its position without one, when it should be at the stop, and
    when its position is stale; at most every REQUEST_GAP_S, a few times, never with the
    mode off."""
    from meshtastic.protobuf import mesh_pb2, portnums_pb2

    from meshplay.mapapp.coord.missions import REQUEST_GAP_S

    iface = coord.ctx.device.iface
    iface.getMyNodeInfo = lambda: {
        "user": {"id": "!11112222"},
        "position": dict(zip(("latitude", "longitude"), HOME, strict=True)),
    }

    def requests():
        return [s for s in iface.sent if s["port"] == portnums_pb2.PortNum.POSITION_APP]

    def tick(age_s=0.0, wait=True):
        """A tick, with the last position age_s older; wait: the request gap has passed."""
        with coord._lock:
            m = coord.missions[NODE]
            if m.positions:
                m.positions[-1]["time"] -= age_s
            if wait:
                m.requested_at -= REQUEST_GAP_S
            coord._tick(time.time())

    coord.settings["request_positions"] = True
    coord.assign(NODE, [{"name": "A", "lat": north(300)[0], "lon": HOME[1]}], "foot", "de")
    (req,) = requests()  # asked right away: no position of the node
    assert (req["to"], req["channel"], req["want_response"]) == (NODE, 1, True)
    own = mesh_pb2.Position.FromString(req["data"])  # ours goes along
    assert own.latitude_i == round(HOME[0] * 1e7) and own.longitude_i == round(HOME[1] * 1e7)
    tick(wait=False)
    assert len(requests()) == 1  # not again within the gap
    feed(coord, position(*HOME))  # 300 m to go: it broadcasts on the way, no need to ask
    tick(age_s=120)
    assert len(requests()) == 1
    feed(coord, position(*north(250)))  # 50 m to go, then silence past the expected arrival
    tick(age_s=20)
    assert len(requests()) == 1  # 40 s at 4.5 km/h plus the slack: not yet
    for _ in range(3):
        tick(age_s=60)
    assert len(requests()) == 3  # "should be there" twice per stop, not more
    tick(age_s=16 * 60)
    assert len(requests()) == 4  # stale
    tick()
    assert len(requests()) == 4  # three unanswered: wait for a position
    reasons = [e["reason"] for e in coord.missions[NODE].events if e["kind"] == "position_request"]
    assert reasons == ["assign", "arrival", "arrival", "stale"]
    feed(coord, position(*north(290)))
    assert coord.missions[NODE].state == ARRIVED
    coord.set_enabled(False)
    coord.assign(NODE, [{"name": "B", "lat": north(900)[0], "lon": HOME[1]}], "foot", "de")
    tick(age_s=16 * 60)
    assert len(requests()) == 4  # the mode is off: nothing goes out


def test_essential_messages_wait_for_the_device(coord):
    """Device away: the assignment is held back, no position request is counted; when the
    device is back the next tick sends both."""
    from meshtastic.protobuf import portnums_pb2

    dev = coord.ctx.device
    coord.settings["request_positions"] = True
    dev.state = "Fehler"
    m = coord.assign(NODE, [{"name": "A", "lat": north(300)[0], "lon": HOME[1]}], "foot", "de")
    assert sent_texts(coord) == [] and m.requests_open == 0
    assert m.outbox == {"assign": "#A zugewiesen, keine Position von dir"}
    assert coord.snapshot()["device_state"] == "Fehler"
    with coord._lock:
        coord._tick(time.time())
    assert dev.iface.sent == []  # still away
    dev.state = "verbunden"
    with coord._lock:
        coord._tick(time.time())
    assert sent_texts(coord) == ["#A zugewiesen, keine Position von dir"] and not m.outbox
    ports = [s["port"] for s in dev.iface.sent]
    assert ports.count(portnums_pb2.PortNum.POSITION_APP) == 1
    assert "resend" in [e["kind"] for e in m.events]


def test_arrival_reported_by_the_node(coord):
    """DA / HERE: the stop counts as reached although the last position is 90 m before it."""
    path = [
        {"name": "A", "lat": north(300)[0], "lon": HOME[1], "hold_until": "+10"},
        {"name": "B", "lat": north(800)[0], "lon": HOME[1]},
    ]
    coord.assign(NODE, path, "foot", "de")
    feed(coord, position(*north(210)))
    m = coord.missions[NODE]
    feed(coord, text("da"))
    assert m.state == HOLDING and sent_texts(coord)[-1].startswith("#A erreicht")
    feed(coord, text("DA"))  # again while waiting: the status, not a second arrival
    assert m.state == HOLDING and sent_texts(coord)[-1].startswith("#A")
    assert [e["kind"] for e in m.events].count("hold") == 1
    m.stop.hold_until = time.time() - 1
    with coord._lock:
        coord._tick(time.time())
    feed(coord, text("here"))
    assert m.state == ARRIVED and sent_texts(coord)[-1].startswith("#B erreicht")
    reported = [e for e in m.events if e["kind"] == "arrival_reported"]
    assert [e["name"] for e in reported] == ["A", "B"] and reported[0]["dist"] == 90
    assert "DA=am Halt" in phrases.phrase("de", "help")


def test_archive_keeps_replaced_and_removed_missions(coord):
    """A replaced or removed mission goes to the archive; the detail has every event and the
    whole trail from the daily logs, beyond the 50 positions a mission keeps."""
    first = coord.assign(NODE, [{"name": "A", "lat": north(900)[0], "lon": HOME[1]}], "foot", "de")
    for i in range(55):
        feed(coord, position(*north(i * 5)))
    first.created -= 1  # the next mission must not share the second
    old_id = coord.missions[NODE].id
    second = coord.assign(NODE, [{"name": "B", "lat": north(50)[0], "lon": HOME[1]}], "foot", "de")
    rows = coord.api("GET", ["archive"], {}, {})["missions"]
    assert [(r["path"], r["current"]) for r in rows] == [(["B"], True), (["A"], False)]
    d = coord.api("GET", ["archive", old_id], {}, {})
    assert d["path"][0]["name"] == "A" and d["archived_at"] is not None
    assert len(d["trail"]) == 55 and len(d["positions"]) == 50
    assert {e["kind"] for e in d["events"]} >= {"assign", "position", "sent"}
    assert all(e["node"] == NODE for e in d["events"])
    feed(coord, position(*north(50)))  # B reached
    coord.mission_action(NODE, "remove", {})
    rows = coord.api("GET", ["archive"], {}, {})["missions"]
    assert [(r["path"], r["current"]) for r in rows] == [(["B"], False), (["A"], False)]
    assert rows[0]["id"] == second.id and rows[0]["state"] == ARRIVED
    with pytest.raises(KeyError):
        coord.api("GET", ["archive", "..\\..\\settings"], {}, {})
