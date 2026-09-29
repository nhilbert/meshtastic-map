"""Routing over an OSM road graph: profiles, A*, snapping, turn instructions, progress.

The graph comes from coord/osm.py (data/osm/<name>.json.gz). Costs are edge lengths times a
profile factor, so the straight-line distance stays an admissible A* heuristic. Positions and
waypoints are snapped to the nearest edge; the route starts and ends at virtual nodes on
those edges. Geometry is in UTM (metres) for shapely, coordinates are (lat, lon) outside.
"""

from __future__ import annotations

import gzip
import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from meshplay.mapapp.coord.geo import bearing_deg, compass, fmt_dist

SNAP_MAX_M = 200.0  # further from any road: straight-line guidance for that segment
TURN_MIN_DEG = 30.0  # smaller heading changes are the road bending, not a turn
U_TURN_DEG = 150.0
NAME_MAX = 12  # street names longer than this are left out of the message
_SHORT = (("straße", "str"), ("Straße", "Str"), ("strasse", "str"), ("Strasse", "Str"))

# highway values a profile may use, with a cost factor (≥ 1 keeps the heuristic admissible)
FOOT = {
    "footway": 1.0,
    "path": 1.0,
    "pedestrian": 1.0,
    "steps": 1.2,
    "living_street": 1.0,
    "residential": 1.0,
    "service": 1.0,
    "track": 1.05,
    "cycleway": 1.05,
    "unclassified": 1.05,
    "tertiary": 1.1,
    "tertiary_link": 1.1,
    "secondary": 1.2,
    "secondary_link": 1.2,
    "primary": 1.3,
    "primary_link": 1.3,
    "road": 1.1,
    "bridleway": 1.1,
    "corridor": 1.0,
}
BIKE = {
    "cycleway": 1.0,
    "residential": 1.0,
    "living_street": 1.05,
    "service": 1.05,
    "track": 1.1,
    "unclassified": 1.0,
    "tertiary": 1.05,
    "tertiary_link": 1.05,
    "secondary": 1.15,
    "secondary_link": 1.15,
    "primary": 1.3,
    "primary_link": 1.3,
    "path": 1.2,
    "road": 1.1,
    "pedestrian": 1.5,
    "footway": 1.6,
}
CAR = {
    "motorway": 1.0,
    "motorway_link": 1.0,
    "trunk": 1.0,
    "trunk_link": 1.0,
    "primary": 1.05,
    "primary_link": 1.05,
    "secondary": 1.1,
    "secondary_link": 1.1,
    "tertiary": 1.15,
    "tertiary_link": 1.15,
    "unclassified": 1.2,
    "residential": 1.25,
    "living_street": 1.6,
    "service": 1.5,
    "road": 1.2,
}
PROFILES = {"foot": FOOT, "bike": BIKE, "car": CAR}
NO = ("no", "private")


def profile_cost(tags: dict, profile: str, forward: bool) -> float | None:
    """Cost factor for using the edge in this direction, or None when the profile may not."""
    table = PROFILES[profile]
    factor = table.get(tags.get("highway", ""))
    if factor is None:
        return None
    access = tags.get("access")
    if profile == "foot":
        if tags.get("foot") in NO or (access in NO and tags.get("foot") != "yes"):
            return None
        return factor
    oneway = tags.get("oneway")
    if profile == "bike":
        if tags.get("bicycle") in NO or (access in NO and tags.get("bicycle") != "yes"):
            return None
        if (
            tags.get("highway") in ("footway", "pedestrian", "steps")
            and tags.get("bicycle") != "yes"
        ):
            return None if tags.get("highway") == "steps" else factor
        if tags.get("oneway:bicycle") == "no":
            oneway = None
    else:  # car
        if tags.get("motor_vehicle") in NO or access in NO:
            return None
    if oneway == "yes" and not forward:
        return None
    if oneway == "-1" and forward:
        return None
    return factor


@dataclass
class Edge:
    a: int
    b: int
    length: float
    geom: list[tuple[float, float]]  # (lat, lon), from a to b
    tags: dict


@dataclass
class Snap:
    edge: int
    point: tuple[float, float]  # (lat, lon) on the edge
    along_m: float  # from edge.a
    dist_m: float  # from the snapped position


@dataclass
class Leg:
    turn: str  # compass for the first leg, else L / R / U / G
    dist_m: float
    name: str | None = None

    def text(self) -> str:
        s = f"{self.turn}{fmt_dist(self.dist_m)}"
        return f"{s} {self.name}" if self.name else s


@dataclass
class Route:
    coords: list[tuple[float, float]]  # (lat, lon)
    length_m: float
    names: list[str | None] = field(default_factory=list)  # per segment between coords
    _line = None
    _cum: list[float] | None = None

    def to_json(self) -> dict:
        return {"coords": self.coords, "length_m": round(self.length_m), "names": self.names}

    @classmethod
    def from_json(cls, d: dict) -> Route:
        coords = [tuple(c) for c in d["coords"]]
        return cls(coords, float(d["length_m"]), d.get("names") or [None] * (len(coords) - 1))

    @property
    def line(self):
        if self._line is None:
            from shapely.geometry import LineString

            self._line = LineString([_utm(lat, lon) for lat, lon in self.coords])
        return self._line

    @property
    def cum(self) -> list[float]:
        """Cumulative length at every vertex [m]."""
        if self._cum is None:
            out, total = [0.0], 0.0
            for (la, lo), (lb, lb2) in zip(self.coords, self.coords[1:], strict=False):
                total += _dist(la, lo, lb, lb2)
                out.append(total)
            self._cum = out
        return self._cum

    def progress(self, lat: float, lon: float) -> tuple[float, float]:
        """(distance along the route [m], distance off the route [m]) for a position."""
        from shapely.geometry import Point

        p = Point(*_utm(lat, lon))
        return self.line.project(p), self.line.distance(p)

    def legs(self, from_m: float = 0.0) -> list[Leg]:
        """Turn instructions from `from_m` along the route to its end."""
        cum = self.cum
        pts, names = [], []
        for i, c in enumerate(self.coords):
            if cum[i] >= from_m:
                if not pts and i > 0:  # start on the segment we are on
                    pts.append(
                        _interpolate(
                            self.coords[i - 1],
                            c,
                            (from_m - cum[i - 1]) / max(cum[i] - cum[i - 1], 1e-9),
                        )
                    )
                    names.append(self.names[i - 1] if self.names else None)
                pts.append(c)
                if i < len(self.coords) - 1:
                    names.append(self.names[i] if self.names else None)
        return legs_for(pts, names)


def legs_for(coords: list[tuple[float, float]], names: list[str | None]) -> list[Leg]:
    """Legs from a polyline: a new leg at every heading change of TURN_MIN_DEG or more.
    Segments shorter than 5 m don't count as a heading, so kerb wiggles don't turn."""
    segs = []  # (bearing, length, name)
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        d = _dist(*a, *b)
        if d <= 0:
            continue
        segs.append([bearing_deg(a, b), d, names[i] if i < len(names) else None])
    if not segs:
        return []
    legs: list[Leg] = []
    heading = None
    for brg, d, name in segs:
        if heading is None:
            legs.append(Leg(compass(brg), d, _short(name)))
        else:
            delta = (brg - heading + 180) % 360 - 180
            if abs(delta) >= TURN_MIN_DEG and d >= 5:
                turn = "U" if abs(delta) >= U_TURN_DEG else ("L" if delta < 0 else "R")
                legs.append(Leg(turn, d, _short(name)))
            else:
                legs[-1].dist_m += d
                if legs[-1].name is None and d >= 5:
                    legs[-1].name = _short(name)
        if d >= 5:
            heading = brg
    return legs


def legs_text(legs: list[Leg], n: int) -> str:
    """The next n legs as "N200 L300 Hauptstr R150 Z60": Z marks the stop at the end."""
    shown = legs[:n]
    parts = [leg.text() for leg in shown]
    if len(shown) == len(legs):
        parts.append("Z")
    return " ".join(parts)


class RoadGraph:
    def __init__(self, data: dict):
        self.bbox = data.get("bbox")
        self.meta = {k: v for k, v in data.items() if k not in ("nodes", "edges")}
        self.nodes: list[tuple[float, float]] = [tuple(n) for n in data["nodes"]]
        self.edges: list[Edge] = [
            Edge(e[0], e[1], float(e[2]), [tuple(p) for p in e[3]], e[4]) for e in data["edges"]
        ]
        self.adj: dict[int, list[tuple[int, int, bool]]] = {}  # node -> (edge, other, forward)
        for i, e in enumerate(self.edges):
            self.adj.setdefault(e.a, []).append((i, e.b, True))
            self.adj.setdefault(e.b, []).append((i, e.a, False))
        self._tree = None
        self._lines = None
        self._costs: dict[str, list[tuple[float | None, float | None]]] = {}

    @classmethod
    def load(cls, path: Path) -> RoadGraph:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------------------ geometry
    def _index(self):
        if self._tree is None:
            from shapely import STRtree
            from shapely.geometry import LineString

            self._lines = [LineString([_utm(lat, lon) for lat, lon in e.geom]) for e in self.edges]
            self._tree = STRtree(self._lines)
        return self._tree

    def nearest(
        self,
        lat: float,
        lon: float,
        max_m: float = SNAP_MAX_M,
        profile: str | None = None,
        blocked: set[int] | None = None,
    ) -> Snap | None:
        """The closest edge within max_m that the profile may use in at least one direction
        and that is not blocked (inside a no-go area)."""
        from shapely.geometry import Point

        tree = self._index()
        p = Point(*_utm(lat, lon))
        costs = self.costs(profile) if profile else None
        best = None
        for i in tree.query(p.buffer(max_m)):
            if costs is not None and costs[i] == (None, None):
                continue
            if blocked and int(i) in blocked:
                continue
            d = self._lines[i].distance(p)
            if d <= max_m and (best is None or d < best[1]):
                best = (int(i), d)
        if best is None:
            return None
        i, d = best
        line = self._lines[i]
        along = line.project(p)
        q = line.interpolate(along)
        return Snap(i, _latlon(q.x, q.y), along, d)

    def costs(self, profile: str) -> list[tuple[float | None, float | None]]:
        """(forward, backward) cost per metre for every edge, cached per profile."""
        if profile not in self._costs:
            self._costs[profile] = [
                (profile_cost(e.tags, profile, True), profile_cost(e.tags, profile, False))
                for e in self.edges
            ]
        return self._costs[profile]

    # ------------------------------------------------------------ routing
    def route(
        self, start: Snap, end: Snap, profile: str, blocked: set[int] | None = None
    ) -> Route | None:
        """Shortest route between two snapped points, or None when they are not connected."""
        blocked = blocked or set()
        costs = self.costs(profile)
        es, ee = self.edges[start.edge], self.edges[end.edge]
        if start.edge == end.edge and start.edge not in blocked:
            fwd, bwd = costs[start.edge]
            if (start.along_m <= end.along_m and fwd is not None) or (
                start.along_m > end.along_m and bwd is not None
            ):
                return self._along(start.edge, start.along_m, end.along_m)
        # virtual start: the two ends of its edge with the partial lengths as costs
        starts = []
        fwd, bwd = costs[start.edge]
        if start.edge not in blocked:
            if fwd is not None:
                starts.append((es.b, (es.length - start.along_m) * fwd))
            if bwd is not None:
                starts.append((es.a, start.along_m * bwd))
        ends = {}
        fwd, bwd = costs[end.edge]
        if end.edge not in blocked:
            if fwd is not None:
                ends[ee.a] = end.along_m * fwd
            if bwd is not None:
                ends[ee.b] = (ee.length - end.along_m) * bwd
        if not starts or not ends:
            return None
        goal = _mid(ee.geom)  # target of the heuristic
        best: dict[int, float] = {}
        prev: dict[int, tuple[int, int, bool]] = {}  # node -> (from node, edge, forward)
        heap = []
        for node, cost in starts:
            best[node] = cost
            heapq.heappush(heap, (cost + self._h(node, goal), cost, node))
        done_node = None
        done_cost = math.inf
        while heap:
            _f, g, node = heapq.heappop(heap)
            if g > best.get(node, math.inf):
                continue
            if node in ends and g + ends[node] < done_cost:
                done_cost, done_node = g + ends[node], node
            if g >= done_cost:
                break
            for ei, other, forward in self.adj.get(node, []):
                if ei in blocked:
                    continue
                c = costs[ei][0 if forward else 1]
                if c is None:
                    continue
                ng = g + self.edges[ei].length * c
                if ng < best.get(other, math.inf):
                    best[other] = ng
                    prev[other] = (node, ei, forward)
                    heapq.heappush(heap, (ng + self._h(other, goal), ng, other))
        if done_node is None:
            return None
        # walk back to a start node
        chain = []
        node = done_node
        start_nodes = {n for n, _ in starts}
        while node not in start_nodes:
            n0, ei, forward = prev[node]
            chain.append((ei, forward))
            node = n0
        chain.reverse()
        first_node = node
        coords: list[tuple[float, float]] = []
        names: list[str | None] = []
        # from the start point to the first graph node along the start edge
        seg = self._cut(start.edge, start.along_m, es.length if first_node == es.b else 0.0)
        _extend(coords, names, seg, es.tags.get("name"))
        for ei, forward in chain:
            e = self.edges[ei]
            _extend(coords, names, e.geom if forward else e.geom[::-1], e.tags.get("name"))
        seg = self._cut(end.edge, ee.length if done_node == ee.b else 0.0, end.along_m)
        _extend(coords, names, seg, ee.tags.get("name"))
        return Route(coords, _length(coords), names)

    def _h(self, node: int, goal: tuple[float, float]) -> float:
        lat, lon = self.nodes[node]
        return _dist(lat, lon, *goal)

    def _along(self, edge: int, m0: float, m1: float) -> Route:
        seg = self._cut(edge, m0, m1)
        return Route(seg, _length(seg), [self.edges[edge].tags.get("name")] * max(len(seg) - 1, 0))

    def _cut(self, edge: int, m0: float, m1: float) -> list[tuple[float, float]]:
        """The edge geometry between two distances from its start (either direction)."""
        from shapely.ops import substring

        self._index()
        line = self._lines[edge]
        piece = substring(line, m0, m1)
        pts = list(piece.coords) if piece.geom_type == "LineString" else [piece.coords[0]]
        if m0 > m1 and len(pts) > 1 and pts[0] != tuple(line.interpolate(m0).coords[0]):
            pts.reverse()
        return [_latlon(x, y) for x, y in pts]


def route_path(
    graph: RoadGraph,
    start: tuple[float, float],
    waypoints: list[tuple[float, float]],
    profile: str,
    blocked: set[int] | None = None,
) -> list[Route | None]:
    """One route per segment start -> wp1 -> wp2 …; None where a segment can't be routed."""
    out = []
    prev = start
    for wp in waypoints:
        a = graph.nearest(*prev, profile=profile, blocked=blocked)
        b = graph.nearest(*wp, profile=profile, blocked=blocked)
        out.append(graph.route(a, b, profile, blocked) if a and b else None)
        prev = wp
    return out


# ---------------------------------------------------------------- helpers
def _utm(lat: float, lon: float) -> tuple[float, float]:
    from meshplay.sim.sites import to_utm

    return to_utm(lon, lat)


def _latlon(x: float, y: float) -> tuple[float, float]:
    from meshplay.sim.sites import to_lonlat

    lon, lat = to_lonlat(x, y)
    return lat, lon


def _dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from meshplay.walk import distance_m

    return distance_m((lat1, lon1), (lat2, lon2))


def _length(coords: list[tuple[float, float]]) -> float:
    return sum(_dist(*a, *b) for a, b in zip(coords, coords[1:], strict=False))


def _mid(geom: list[tuple[float, float]]) -> tuple[float, float]:
    return geom[len(geom) // 2]


def _interpolate(a, b, f: float) -> tuple[float, float]:
    f = min(max(f, 0.0), 1.0)
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f


def _extend(coords: list, names: list, pts: list, name: str | None) -> None:
    for p in pts:
        if coords and _dist(*coords[-1], *p) < 0.5:
            continue
        if coords:
            names.append(name)
        coords.append(p)


def _short(name: str | None) -> str | None:
    if not name:
        return None
    for long, short in _SHORT:
        name = name.replace(long, short)
    return name if len(name) <= NAME_MAX else None
