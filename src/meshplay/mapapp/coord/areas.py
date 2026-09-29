"""Restricted areas, notice areas and places of the coordination mode.

Areas are polygons (lat/lon rings) of kind "nogo" (routing avoids them, the node is warned
when it is inside or about to enter) or "notice" (the node gets the area's text when it
enters). Places are points with a radius and a text the node gets when it comes near.
Geometry runs in UTM through shapely; the sets are rebuilt when the lists change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from meshplay.mapapp.i18n import _
from meshplay.mapapp.sites_store import NAME_RE

KINDS = ("nogo", "notice")
LOOK_AHEAD_M = 200.0  # a no-go area this far ahead on the route is announced


@dataclass
class Area:
    id: str
    name: str
    kind: str
    polygon: list[tuple[float, float]]  # (lat, lon) ring
    text: str = ""
    buffer_m: float = 0.0

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "polygon": [list(p) for p in self.polygon],
            "text": self.text,
            "buffer_m": self.buffer_m,
        }


@dataclass
class Place:
    id: str
    name: str
    lat: float
    lon: float
    radius_m: float
    text: str = ""

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "radius_m": self.radius_m,
            "text": self.text,
        }


def parse_area(d: dict, existing_id: str | None = None) -> Area:
    name = str(d.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ValueError(_("Name: 1–24 Zeichen, nur Buchstaben, Ziffern, _ und -"))
    kind = d.get("kind") or "nogo"
    if kind not in KINDS:
        raise ValueError(_("Art: Sperrgebiet oder Hinweisgebiet"))
    ring = []
    for p in d.get("polygon") or []:
        try:
            lat, lon = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError(_("Polygon: Liste von [Breite, Länge]")) from None
        ring.append((lat, lon))
    if len(ring) < 3:
        raise ValueError(_("Ein Gebiet braucht mindestens drei Eckpunkte"))
    buffer = float(d.get("buffer_m") or 0)
    if not 0 <= buffer <= 500:
        raise ValueError(_("Puffer: 0 bis 500 m"))
    return Area(
        existing_id or _slug(name), name, kind, ring, str(d.get("text") or "")[:120], buffer
    )


def parse_place(d: dict, existing_id: str | None = None) -> Place:
    name = str(d.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ValueError(_("Name: 1–24 Zeichen, nur Buchstaben, Ziffern, _ und -"))
    try:
        lat, lon = float(d["lat"]), float(d["lon"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(_("Ungültige Position")) from None
    radius = float(d.get("radius_m") or 100)
    if not 10 <= radius <= 2000:
        raise ValueError(_("Radius: 10 bis 2000 m"))
    return Place(existing_id or _slug(name), name, lat, lon, radius, str(d.get("text") or "")[:120])


def _slug(name: str) -> str:
    return name.lower()


class AreaSet:
    """Geometry over the current areas and places."""

    def __init__(self, areas: list[Area], places: list[Place]):
        from shapely.geometry import Polygon

        self.areas = areas
        self.places = places
        self._polys = []
        for a in areas:
            poly = Polygon([_utm(lat, lon) for lat, lon in a.polygon]).buffer(0)
            if a.buffer_m:
                poly = poly.buffer(a.buffer_m)
            self._polys.append(poly)

    def inside(self, lat: float, lon: float) -> list[Area]:
        from shapely.geometry import Point

        p = Point(*_utm(lat, lon))
        return [a for a, poly in zip(self.areas, self._polys, strict=True) if poly.covers(p)]

    def ahead(
        self, route, along_m: float, look_m: float = LOOK_AHEAD_M
    ) -> tuple[Area, float] | None:
        """The first no-go area the route enters within look_m ahead of along_m, with the
        distance to it; None when the way ahead is clear."""
        from shapely.ops import substring

        line = route.line
        piece = substring(line, along_m, min(along_m + look_m, line.length))
        best = None
        for a, poly in zip(self.areas, self._polys, strict=True):
            if a.kind != "nogo" or not piece.intersects(poly):
                continue
            hit = piece.intersection(poly)
            d = line.project(_first_point(hit)) - along_m
            if best is None or d < best[1]:
                best = (a, max(d, 0.0))
        return best

    def ahead_heading(
        self, lat: float, lon: float, bearing: float, look_m: float = LOOK_AHEAD_M
    ) -> tuple[Area, float] | None:
        """The first no-go area on a straight line look_m long in the direction of travel,
        with the distance to it: the node may be walking into it regardless of the route."""
        from shapely.geometry import LineString, Point

        start = Point(*_utm(lat, lon))
        end = Point(*_utm(*_offset(lat, lon, bearing, look_m)))
        seg = LineString([start, end])
        best = None
        for a, poly in zip(self.areas, self._polys, strict=True):
            if a.kind != "nogo" or poly.covers(start) or not seg.intersects(poly):
                continue
            d = start.distance(seg.intersection(poly))
            if best is None or d < best[1]:
                best = (a, d)
        return best

    def blocked_edges(self, graph) -> set[int]:
        """Edges of the road graph that touch a no-go area."""
        nogo = [poly for a, poly in zip(self.areas, self._polys, strict=True) if a.kind == "nogo"]
        if not nogo:
            return set()
        from shapely import STRtree

        graph._index()
        tree = STRtree(graph._lines)
        out: set[int] = set()
        for poly in nogo:
            for i in tree.query(poly, predicate="intersects"):
                out.add(int(i))
        return out

    def near_places(self, lat: float, lon: float) -> list[tuple[Place, float]]:
        out = []
        for p in self.places:
            d = _dist(lat, lon, p.lat, p.lon)
            if d <= p.radius_m:
                out.append((p, d))
        return out

    def place_distance(self, place: Place, lat: float, lon: float) -> float:
        return _dist(lat, lon, place.lat, place.lon)


@dataclass
class AreaState:
    """Per mission: which warnings are armed (entered areas, announced places)."""

    inside: set[str] = field(default_factory=set)
    announced: set[str] = field(default_factory=set)
    places: set[str] = field(default_factory=set)


def _utm(lat: float, lon: float) -> tuple[float, float]:
    from meshplay.sim.sites import to_utm

    return to_utm(lon, lat)


def _offset(lat: float, lon: float, bearing: float, dist_m: float) -> tuple[float, float]:
    """A point dist_m away in the given compass bearing (flat-earth, fine for 200 m)."""
    b = math.radians(bearing)
    return (
        lat + dist_m * math.cos(b) / 111_320,
        lon + dist_m * math.sin(b) / (111_320 * math.cos(math.radians(lat))),
    )


def _dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from meshplay.walk import distance_m

    return distance_m((lat1, lon1), (lat2, lon2))


def _first_point(geom):
    """The vertex of an intersection result closest to its start (any geometry type)."""
    from shapely.geometry import Point

    if geom.geom_type == "Point":
        return geom
    if geom.geom_type in ("LineString", "LinearRing"):
        return Point(geom.coords[0])
    parts = list(getattr(geom, "geoms", []))
    if parts:
        return _first_point(parts[0])
    return Point(geom.representative_point())


def circle(lat: float, lon: float, radius_m: float, n: int = 32) -> list[tuple[float, float]]:
    """A ring of (lon, lat) points around a position, for drawing a place's radius."""
    out = []
    for k in range(n):
        ang = 2 * math.pi * k / n
        dlat = radius_m * math.cos(ang) / 111_320
        dlon = radius_m * math.sin(ang) / (111_320 * math.cos(math.radians(lat)))
        out.append((lon + dlon, lat + dlat))
    out.append(out[0])
    return out
