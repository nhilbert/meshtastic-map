"""Road graph building and routing on a synthetic street grid (no download)."""

import json

import pytest

from meshplay.mapapp.coord import osm
from meshplay.mapapp.coord.routing import Leg, RoadGraph, Route, legs_for, legs_text, route_path

pytest.importorskip("shapely")
pytest.importorskip("pyproj")

LAT0, LON0 = 50.7374, 7.0982
STEP = 100.0  # m


def pt(i, j):
    """Grid point i east, j north of the origin."""
    return LAT0 + j * STEP / 111_320, LON0 + i * STEP / 70_400


def grid_ways(n=5):
    """Streets every 100 m: rows are named, columns too; the middle row is one-way east."""
    ways = []
    ref = lambda i, j: j * 100 + i  # noqa: E731
    for j in range(n):
        tags = {"highway": "residential", "name": f"Row{j}straße"}
        if j == 2:
            tags["oneway"] = "yes"
        ways.append((1000 + j, tags, [pt(i, j) for i in range(n)], [ref(i, j) for i in range(n)]))
    for i in range(n):
        tags = {"highway": "residential", "name": f"Col{i}"}
        if i == 4:
            tags = {"highway": "footway"}
        ways.append((2000 + i, tags, [pt(i, j) for j in range(n)], [ref(i, j) for j in range(n)]))
    # a private service road that would be a shortcut
    ways.append(
        (
            3000,
            {"highway": "service", "access": "private"},
            [pt(0, 0), pt(1, 1)],
            [ref(0, 0), ref(1, 1)],
        )
    )
    return ways


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    g = osm.build_graph(grid_ways(), (50.73, 7.09, 50.74, 7.10))
    path = tmp_path_factory.mktemp("osm") / "roads.json.gz"
    osm.write_graph(g, path)
    return RoadGraph.load(path)


def test_build_graph_splits_at_junctions(graph):
    assert len(graph.nodes) == 25
    # 5 rows + 5 columns of 4 pieces each, plus the private shortcut
    assert len(graph.edges) == 41
    assert graph.meta["n_ways"] == 11 and graph.meta["bbox"] == [50.73, 7.09, 50.74, 7.1]


def test_graph_info_reads_the_header(tmp_path):
    g = osm.build_graph(grid_ways(), (50.73, 7.09, 50.74, 7.10))
    path = tmp_path / "x.json.gz"
    osm.write_graph(g, path)
    info = osm.graph_info(path)
    assert (
        info["name"] == "x" and info["n_ways"] == 11 and info["bbox"] == [50.73, 7.09, 50.74, 7.1]
    )


def test_shortest_route_and_legs(graph):
    a, b = graph.nearest(*pt(0, 0)), graph.nearest(*pt(3, 3))
    assert a.dist_m < 1 and b.dist_m < 1
    r = graph.route(a, b, "foot")
    assert r is not None and abs(r.length_m - 600) < 2  # manhattan distance on the grid
    legs = r.legs()
    assert legs[0].turn in ("N", "E") and legs[-1].dist_m > 0
    assert 2 <= len(legs) <= 6  # a grid has many equally short staircases
    assert all(leg.turn in ("L", "R") for leg in legs[1:])
    assert abs(sum(leg.dist_m for leg in legs) - 600) < 2
    text = legs_text(legs, 6)
    assert text.endswith(" Z") and ("Row" in text or "Col" in text)
    assert not legs_text(legs, 1).endswith(" Z")  # more legs follow
    assert legs_text(legs, 1) == legs[0].text()


def test_private_and_oneway_are_respected(graph):
    a, b = graph.nearest(*pt(0, 0)), graph.nearest(*pt(1, 1))
    foot = graph.route(a, b, "foot")
    assert abs(foot.length_m - 200) < 2  # not the private diagonal
    # the one-way row 2 runs east; a car going west must use another row
    a, b = graph.nearest(*pt(3, 2), profile="car"), graph.nearest(*pt(0, 2), profile="car")
    assert graph.edges[a.edge].tags.get("highway") != "footway"  # snapped to a usable edge
    car = graph.route(a, b, "car")
    assert car is not None and car.length_m > 402  # detour via row 1 or 3
    foot = graph.route(a, b, "foot")
    assert abs(foot.length_m - 300) < 2
    # a car can't use the footway column
    a, b = graph.nearest(*pt(4, 0)), graph.nearest(*pt(4, 4))
    assert graph.route(a, b, "foot").length_m < 402
    a, b = graph.nearest(*pt(4, 0), profile="car"), graph.nearest(*pt(4, 4), profile="car")
    assert graph.route(a, b, "car").length_m > 402


def test_snapping_and_same_edge(graph):
    lat, lon = pt(0.5, 0)  # midway on Row0, 40 m south of it
    off = lat - 40 / 111_320, lon
    s = graph.nearest(*off)
    assert 39 < s.dist_m < 41 and abs(s.along_m - 50) < 1
    assert graph.nearest(LAT0 - 0.01, LON0) is None  # more than 200 m from any road
    r = graph.route(graph.nearest(*pt(0.2, 0)), graph.nearest(*pt(0.8, 0)), "foot")
    assert abs(r.length_m - 60) < 2 and len(r.legs()) == 1
    r = graph.route(graph.nearest(*pt(0.8, 0)), graph.nearest(*pt(0.2, 0)), "foot")
    assert abs(r.length_m - 60) < 2 and r.legs()[0].turn == "W"


def test_blocked_edges_and_route_path(graph):
    a, b = graph.nearest(*pt(0, 0)), graph.nearest(*pt(2, 0))
    direct = graph.route(a, b, "foot")
    blocked = {direct and graph.nearest(*pt(1.5, 0)).edge}
    detour = graph.route(a, b, "foot", blocked)
    assert detour.length_m > direct.length_m + 100
    routes = route_path(graph, pt(0, 0), [pt(2, 0), pt(2, 2), (LAT0 - 0.02, LON0)], "foot")
    assert [r is None for r in routes] == [False, False, True]
    assert abs(routes[1].length_m - 200) < 2


def test_progress_and_legs_from_mid_route(graph):
    r = graph.route(graph.nearest(*pt(0, 0)), graph.nearest(*pt(3, 0)), "foot")
    along, off = r.progress(*pt(1, 0))
    assert abs(along - 100) < 1 and off < 1
    along, off = r.progress(LAT0 + 30 / 111_320, pt(1, 0)[1])
    assert abs(along - 100) < 1 and abs(off - 30) < 1
    rest = r.legs(from_m=120)
    assert len(rest) == 1 and abs(rest[0].dist_m - 180) < 2
    again = Route.from_json(json.loads(json.dumps(r.to_json())))
    assert abs(again.length_m - r.length_m) < 1 and again.progress(*pt(2, 0))[0] == pytest.approx(
        200, abs=1
    )


def test_legs_merge_bends_and_shorten_names():
    coords = [pt(0, 0), pt(1, 0), pt(2, 0.1), pt(3, 0.1), pt(3, 1), pt(3, 2), pt(2, 2)]
    names = ["Hauptstraße", "Hauptstraße", "Hauptstraße", "Am langen Gartenweg", None, "Kurz"]
    legs = legs_for(coords, names)
    assert [leg.turn for leg in legs] == ["E", "L", "L"]
    assert legs[0].name == "Hauptstr" and legs[1].name is None and legs[2].name == "Kurz"
    assert abs(sum(leg.dist_m for leg in legs) - 600) < 30
    assert Leg("U", 12, None).text() == "U10m"


def test_bbox_helpers():
    assert osm.snap_bbox(50.7374, 7.0982, 50.7375, 7.0983) == (50.73, 7.09, 50.74, 7.1)
    box = osm.default_bbox((50.7374, 7.0982), 3000)
    assert box[0] <= 50.71 and box[2] >= 50.76 and box[1] < 7.06 and box[3] > 7.13
    with pytest.raises(ValueError, match="vier Zahlen"):
        osm.parse_bbox("50.7, 7.0, x")
    with pytest.raises(ValueError, match="Süd < Nord"):
        osm.parse_bbox("50.8, 7.0, 50.7, 7.2")
    with pytest.raises(ValueError, match="zu groß"):
        osm.parse_bbox("50.0, 7.0, 51.0, 7.2")
    assert osm.parse_bbox("50.63 7.02 50.77 7.21") == (50.63, 7.02, 50.77, 7.21)
    assert 'way["highway"]' in osm.overpass_query(
        (1, 2, 3, 4)
    ) and "(1,2,3,4)" in osm.overpass_query((1, 2, 3, 4))


def test_osm_xml_import(tmp_path):
    xml = """<?xml version="1.0"?><osm version="0.6">
      <node id="1" lat="50.7" lon="7.1"/><node id="2" lat="50.7" lon="7.101"/>
      <node id="3" lat="50.701" lon="7.101"/>
      <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/>
        <tag k="name" v="A"/></way>
      <way id="11"><nd ref="2"/><nd ref="3"/><tag k="highway" v="footway"/></way>
      <way id="12"><nd ref="1"/><nd ref="3"/><tag k="highway" v="construction"/></way>
      <way id="13"><nd ref="1"/><nd ref="3"/><tag k="building" v="yes"/></way>
    </osm>"""
    path = tmp_path / "x.osm"
    path.write_text(xml, encoding="utf-8")
    ways = list(osm.ways_from_osm_xml(path))
    assert [w[0] for w in ways] == [10, 11]
    g = osm.build_graph(ways, source="x.osm")
    assert (
        len(g["nodes"]) == 3
        and len(g["edges"]) == 2
        and g["edges"][0][4] == {"highway": "residential", "name": "A"}
    )
