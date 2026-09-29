"""A mission's path: waypoints the field node passes (via) or is guided to and confirmed at (stop).

The last waypoint is always a stop. Stops can carry an arrival deadline and a hold time
(docs/coordination-design.md §2).
"""

from __future__ import annotations

from dataclasses import dataclass

from meshplay.mapapp.coord.geo import parse_time
from meshplay.mapapp.i18n import _
from meshplay.mapapp.sites_store import NAME_RE

KINDS = ("stop", "via")


@dataclass
class Waypoint:
    name: str
    lat: float
    lon: float
    kind: str = "stop"
    radius_m: float = 30.0
    arrive_by: float | None = None
    hold_until: float | None = None

    @property
    def pos(self) -> tuple[float, float]:
        return self.lat, self.lon

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "kind": self.kind,
            "radius_m": self.radius_m,
            "arrive_by": self.arrive_by,
            "hold_until": self.hold_until,
        }

    @classmethod
    def from_json(cls, d: dict) -> Waypoint:
        return cls(
            d["name"],
            float(d["lat"]),
            float(d["lon"]),
            d.get("kind", "stop"),
            float(d.get("radius_m", 30.0)),
            d.get("arrive_by"),
            d.get("hold_until"),
        )


def _time(value, now: float, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return parse_time(str(value), now)
    except ValueError:
        raise ValueError(_("{label}: Uhrzeit wie 12:55 oder +15 (Minuten)", label=label)) from None


def parse_path(raw: list[dict], now: float, default_radius_m: float = 30.0) -> list[Waypoint]:
    """Waypoints from the page's rows; ValueError with a sentence for anything unusable."""
    if not raw:
        raise ValueError(_("Der Pfad braucht mindestens ein Ziel"))
    path, names = [], set()
    for i, d in enumerate(raw, 1):
        name = str(d.get("name") or "").strip()
        if not NAME_RE.match(name):
            raise ValueError(
                _("Wegpunkt {n}: Name 1–24 Zeichen, nur Buchstaben, Ziffern, _ und -", n=i)
            )
        if name.upper() in names:
            raise ValueError(_("Der Name {name} kommt zweimal vor", name=name))
        names.add(name.upper())
        try:
            lat, lon = float(d["lat"]), float(d["lon"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(_("Wegpunkt {name} hat keine Position", name=name)) from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(_("Wegpunkt {name} hat keine Position", name=name))
        kind = d.get("kind") or "stop"
        if kind not in KINDS:
            raise ValueError(_("Wegpunkt {name}: Art muss stop oder via sein", name=name))
        radius = float(d.get("radius_m") or default_radius_m)
        if not 5 <= radius <= 500:
            raise ValueError(_("Wegpunkt {name}: Radius 5 bis 500 m", name=name))
        arrive_by = _time(d.get("arrive_by"), now, _("Ankunft bis"))
        hold_until = _time(d.get("hold_until"), now, _("Warten bis"))
        if kind == "via" and (arrive_by or hold_until):
            raise ValueError(_("Wegpunkt {name}: Zeiten nur bei Halten", name=name))
        if arrive_by and hold_until and hold_until < arrive_by:
            raise ValueError(_("Wegpunkt {name}: Warten bis liegt vor Ankunft bis", name=name))
        path.append(Waypoint(name, lat, lon, kind, radius, arrive_by, hold_until))
    if path[-1].kind != "stop":
        raise ValueError(_("Der letzte Wegpunkt muss ein Halt sein"))
    return path


def stops(path: list[Waypoint]) -> list[Waypoint]:
    return [w for w in path if w.kind == "stop"]
