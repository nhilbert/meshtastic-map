"""Restricted areas, notice areas and places: geometry and the warnings a mission sends."""

import pytest

from meshplay.mapapp.coord import osm
from meshplay.mapapp.coord.areas import AreaSet, circle, parse_area, parse_place
from meshplay.mapapp.coord.routing import RoadGraph
from tests.test_coord import NODE, feed, position, sent_texts
from tests.test_coord_routing import grid_ways, pt

pytest.importorskip("shapely")


def square(i0, j0, i1, j1):
    return [pt(i0, j0), pt(i1, j0), pt(i1, j1), pt(i0, j1)]


def test_parse_and_geometry():
    with pytest.raises(ValueError, match="drei Eckpunkte"):
        parse_area({"name": "X", "polygon": [pt(0, 0), pt(1, 0)]})
    with pytest.raises(ValueError, match="Art"):
        parse_area({"name": "X", "kind": "bad", "polygon": square(0, 0, 1, 1)})
    with pytest.raises(ValueError, match="Radius"):
        parse_place({"name": "P", "lat": 50.7, "lon": 7.1, "radius_m": 5})
    a = parse_area(
        {"name": "Kaserne", "kind": "nogo", "polygon": square(1, 1, 2, 2), "buffer_m": 10}
    )
    n = parse_area(
        {"name": "Park", "kind": "notice", "polygon": square(3, 3, 4, 4), "text": "leise"}
    )
    p = parse_place(
        {
            "name": "Bahnhof",
            "lat": pt(0, 3)[0],
            "lon": pt(0, 3)[1],
            "radius_m": 80,
            "text": "Treffpunkt",
        }
    )
    s = AreaSet([a, n], [p])
    assert [x.name for x in s.inside(*pt(1.5, 1.5))] == ["Kaserne"]
    assert [x.name for x in s.inside(*pt(2.05, 1.5))] == ["Kaserne"]  # within the 10 m buffer
    assert s.inside(*pt(2.3, 1.5)) == []
    assert [(x.name, round(d)) for x, d in s.near_places(*pt(0.5, 3))] == [("Bahnhof", 50)]
    assert s.near_places(*pt(1, 3)) == []
    ring = circle(50.7, 7.1, 100)
    assert len(ring) == 33 and ring[0] == ring[-1]


def test_blocked_edges_and_route_ahead(tmp_path):
    osm.write_graph(osm.build_graph(grid_ways()), tmp_path / "g.json.gz")
    graph = RoadGraph.load(tmp_path / "g.json.gz")
    a = parse_area({"name": "Sperr", "kind": "nogo", "polygon": square(1.5, -0.5, 2.5, 0.5)})
    s = AreaSet([a], [])
    blocked = s.blocked_edges(graph)
    assert blocked  # the pieces of Row0 and Col2 that cross the square
    free = graph.route(graph.nearest(*pt(0, 0)), graph.nearest(*pt(3, 0)), "foot")
    around = graph.route(
        graph.nearest(*pt(0, 0), blocked=blocked),
        graph.nearest(*pt(3, 0), blocked=blocked),
        "foot",
        blocked,
    )
    assert abs(free.length_m - 300) < 2 and around.length_m > 480  # up, across, down
    hit = s.ahead(free, 0.0)
    assert hit is not None and hit[0].name == "Sperr" and 140 < hit[1] < 160
    assert s.ahead(free, 0.0, look_m=100) is None
    assert s.ahead(around, 0.0) is None


def test_mission_warnings(coord, tmp_path):
    coord.settings["min_gap_s"] = 0
    coord.api(
        "POST",
        ["areas", "add"],
        {},
        {"name": "Kaserne", "kind": "nogo", "polygon": square(1.5, 0.5, 2.5, 1.5)},
    )
    coord.api(
        "POST",
        ["areas", "add"],
        {},
        {"name": "Park", "kind": "notice", "polygon": square(0.5, 2.5, 1.5, 3.5), "text": "leise"},
    )
    coord.api(
        "POST",
        ["places", "add"],
        {},
        {
            "name": "Bahnhof",
            "lat": pt(0, 2)[0],
            "lon": pt(0, 2)[1],
            "radius_m": 60,
            "text": "Treffpunkt",
        },
    )
    with pytest.raises(ValueError, match="gibt es schon"):
        coord.api("POST", ["places", "add"], {}, {"name": "bahnhof", "lat": 50.7, "lon": 7.1})
    snap = coord.api("GET", [], {}, {})
    assert [a["name"] for a in snap["areas"]] == ["Kaserne", "Park"] and snap["places"][0][
        "id"
    ] == "bahnhof"
    coord.assign(NODE, [{"name": "Z", "lat": pt(2, 4)[0], "lon": pt(2, 4)[1]}], "foot", "de")
    feed(coord, position(*pt(0, 0)))
    feed(coord, position(*pt(0, 1.7)))  # within 60 m of the place at (0, 2)
    assert sent_texts(coord)[-1] == "i Bahnhof 30m: Treffpunkt"
    n = len(sent_texts(coord))
    feed(coord, position(*pt(0, 2.2)))  # still near: no repeat
    assert len(sent_texts(coord)) == n
    feed(coord, position(*pt(1, 3)))  # inside the notice area
    assert sent_texts(coord)[-1] == "i Park: leise"
    feed(coord, position(*pt(2, 1)))  # inside the no-go area
    assert sent_texts(coord)[-1] == "!SPERR Kaserne verlassen"
    m = coord.missions[NODE]
    assert "kaserne" in m.area_state.inside
    feed(coord, position(*pt(2, 0)))  # left it
    assert "kaserne" not in m.area_state.inside
    events = [e["kind"] for e in m.events]
    assert "area_entered" in events and "area_left" in events and "place" in events
    # the map layer draws both kinds and the place
    kinds = [f["geometry"]["type"] for f in coord.layer_features()]
    assert kinds.count("Polygon") == 3
    coord.api("POST", ["areas", "delete"], {}, {"id": "kaserne"})
    coord.api("POST", ["places", "update"], {}, {"id": "bahnhof", "radius_m": 120})
    snap = coord.api("GET", [], {}, {})
    assert [a["name"] for a in snap["areas"]] == ["Park"] and snap["places"][0]["radius_m"] == 120
    with pytest.raises(KeyError):
        coord.api("POST", ["areas", "delete"], {}, {"id": "kaserne"})


def test_route_avoids_nogo_and_warns_ahead(coord, tmp_path):
    osm.write_graph(osm.build_graph(grid_ways()), tmp_path / "osm" / "roads.json.gz")
    coord.settings["min_gap_s"] = 0
    coord.assign(NODE, [{"name": "Z", "lat": pt(3, 0)[0], "lon": pt(3, 0)[1]}], "foot", "de")
    feed(coord, position(*pt(0, 0)))
    m = coord.missions[NODE]
    assert abs(m.route.length_m - 300) < 2
    # a no-go square across the direct road: the route is rebuilt around it
    coord.api(
        "POST",
        ["areas", "add"],
        {},
        {"name": "Sperr", "kind": "nogo", "polygon": square(1.5, -0.5, 2.5, 0.5)},
    )
    assert m.route.length_m > 480
    # the node ignores the route and walks straight on: warned before entering
    feed(coord, position(*pt(0.5, 0)))
    feed(coord, position(*pt(1.2, 0)))  # 30 m before the square, off the detour route
    assert any(t.startswith("!SPERR Sperr") and "voraus" in t for t in sent_texts(coord))
