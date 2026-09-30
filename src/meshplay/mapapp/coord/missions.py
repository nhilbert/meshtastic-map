"""The Coordinator: one mission per field node, decisions on every position, the API.

Packets arrive on the serial reader thread and are only queued; a worker thread updates the
missions and sends (through DeviceLink.send_text, so every message is in the pane). A tick
thread handles the time-based parts: hold times, stale positions, retries, saving.
Nothing is sent while the mode is off. See docs/coordination-design.md §4 and §5.
"""

from __future__ import annotations

import logging
import queue
import statistics
import threading
import time
from collections import deque

from meshplay.mapapp.coord import phrases
from meshplay.mapapp.coord.areas import (
    Area,
    AreaSet,
    AreaState,
    Place,
    circle,
    parse_area,
    parse_place,
)
from meshplay.mapapp.coord.geo import (
    bearing_deg,
    compass,
    distance_m,
    fmt_dist,
    fmt_eta,
    hhmm,
)
from meshplay.mapapp.coord.paths import Waypoint, parse_path, stops
from meshplay.mapapp.coord.routing import RoadGraph, Route, legs_text, route_path
from meshplay.mapapp.coord.settings import (
    CHANNEL_PROOF_S,
    DEFAULTS,
    clean,
    declarations,
    default_speed_ms,
)
from meshplay.mapapp.coord.store import CoordStore
from meshplay.mapapp.i18n import _
from meshplay.mapapp.sites_store import NAME_RE

log = logging.getLogger(__name__)

ASSIGNED, UNDERWAY, HOLDING, ARRIVED, ABORTED, ENDED = (
    "zugewiesen",
    "unterwegs",
    "wartet",
    "erreicht",
    "abgebrochen",
    "beendet",
)
ACTIVE = (ASSIGNED, UNDERWAY, HOLDING)
PRIORITY_GAP_S = 30  # warnings and arrivals may follow each other this closely
RETRY_S = 60  # an undelivered assignment / arrival / next-leg message is sent once more
RETRY_KINDS = ("assign", "reached", "next", "changed")
MOVE_MIN_M = 15  # smaller steps are GPS noise: not counted, not used for the speed
SPEED_DT_S = (20, 300)  # a leg between two positions counts for the speed only in this range
OFF_STEP_M = 50  # straight-line off-course: the distance grew by this on three positions in a row
TURN_AHEAD_M = 40  # instruction mode "turns": announce the next legs this close to the turn
INTERVAL_M = 500  # instruction mode "interval": a route message every this many metres


class Mission:
    def __init__(
        self,
        node: str,
        path: list[Waypoint],
        profile: str,
        lang: str,
        now: float,
        label: str = "",
        origin: str = "page",
    ):
        self.node = node
        self.path = path
        self.profile = profile
        self.lang = lang
        self.label = label  # long name of a marker target, said with the assignment
        self.origin = origin  # "page" (the coordinator) or "radio" (the node asked with ?D)
        self.index = 0  # current waypoint
        self.state = ASSIGNED
        self.held = False
        self.created = now
        self.assigned_at = now
        self.arrived_at: float | None = None
        self.hold_arrived_at: float | None = None
        self.positions: deque[dict] = deque(maxlen=50)
        self.travelled_m = 0.0
        self.metrics: dict = {}
        self.sent: dict[str, float] = {}  # last send per kind
        self.last_proactive = 0.0
        self.flags: set[str] = set()  # armed warnings: "offcourse", "late", "early"
        self.messages: deque[dict] = deque(maxlen=50)
        self.events: deque[dict] = deque(maxlen=200)
        self.retry: tuple[float, str, str] | None = None  # (when, kind, text)
        self.awaiting_position = False  # assigned without a position: the leg follows the first
        self.offcourse_from_m = 0.0  # distance to the stop when the off-course warning went out
        self.legend_sent = False  # the legend of the codes goes out once per mission
        self.route: Route | None = None  # from the last position to the current stop
        self.route_ahead: list[list] = []  # coordinates of the later segments, for the map
        self.off_count = 0  # positions in a row off the route
        self.area_state = AreaState()  # entered areas and announced places (not persisted)
        self.announced = 0  # legs left when the last turn instruction went out
        self.instr_at_m = 0.0  # travelled distance at the last interval instruction

    @property
    def current(self) -> Waypoint:
        return self.path[self.index]

    @property
    def stop_index(self) -> int:
        """The next stop at or after the current waypoint (the last waypoint is always one)."""
        return next(
            (i for i in range(self.index, len(self.path)) if self.path[i].kind == "stop"),
            len(self.path) - 1,
        )

    @property
    def stop(self) -> Waypoint:
        return self.path[self.stop_index]

    @property
    def guide(self) -> Waypoint:
        """What the guidance points at: with a route the next stop (the route runs through the
        vias), without one the current waypoint, via or stop."""
        return self.stop if self.route is not None else self.current

    @property
    def active(self) -> bool:
        return self.state in ACTIVE

    @property
    def last(self) -> dict | None:
        return self.positions[-1] if self.positions else None

    def to_json(self, with_events: bool = False) -> dict:
        out = {
            "node": self.node,
            "path": [w.to_json() for w in self.path],
            "profile": self.profile,
            "lang": self.lang,
            "label": self.label,
            "origin": self.origin,
            "index": self.index,
            "state": self.state,
            "held": self.held,
            "created": self.created,
            "assigned_at": self.assigned_at,
            "arrived_at": self.arrived_at,
            "hold_arrived_at": self.hold_arrived_at,
            "positions": list(self.positions),
            "travelled_m": round(self.travelled_m),
            "metrics": self.metrics,
            "flags": sorted(self.flags),
            "messages": list(self.messages),
            "last_proactive": self.last_proactive,
            "awaiting_position": self.awaiting_position,
            "legend_sent": self.legend_sent,
            "offcourse_from_m": self.offcourse_from_m,
            "route": self.route.to_json() if self.route else None,
            "route_ahead": self.route_ahead,
            "legs": legs_text(self.route.legs(self.metrics.get("along_m", 0.0)), 6)
            if self.route
            else None,
        }
        if with_events:
            out["events"] = list(self.events)
        return out

    @classmethod
    def from_json(cls, d: dict) -> Mission:
        m = cls(
            d["node"],
            [Waypoint.from_json(w) for w in d["path"]],
            d.get("profile", "foot"),
            d.get("lang", "de"),
            d.get("created", time.time()),
            d.get("label", ""),
            d.get("origin", "page"),
        )
        for k in (
            "index",
            "state",
            "held",
            "assigned_at",
            "arrived_at",
            "hold_arrived_at",
            "travelled_m",
            "metrics",
            "last_proactive",
            "awaiting_position",
            "legend_sent",
            "offcourse_from_m",
        ):
            if k in d:
                setattr(m, k, d[k])
        m.positions.extend(d.get("positions", []))
        m.flags = set(d.get("flags", []))
        m.messages.extend(d.get("messages", []))
        if d.get("route"):
            m.route = Route.from_json(d["route"])
        m.route_ahead = d.get("route_ahead") or []
        return m


class Coordinator:
    def __init__(self, ctx):
        self.ctx = ctx
        self.store = CoordStore(ctx.data_dir / "coord")
        saved = self.store.read("settings", {})
        self.enabled = bool(saved.pop("enabled", False))
        self.settings = {**DEFAULTS, **saved}
        if self.settings["markers"] not in ("channel", "missions", "off"):
            self.settings["markers"] = DEFAULTS["markers"]  # "all" before 2026-09-30
        self.targets: dict[str, dict] = self.store.read("targets", {})
        self.paths: dict[str, list[dict]] = self.store.read("paths", {})  # templates
        self.areas: list[Area] = []
        for d in self.store.read("areas", []):
            try:
                self.areas.append(parse_area(d, d.get("id")))
            except (ValueError, KeyError) as e:
                log.warning("Skipping a stored area: %s", e)
        self.places: list[Place] = []
        for d in self.store.read("places", []):
            try:
                self.places.append(parse_place(d, d.get("id")))
            except (ValueError, KeyError) as e:
                log.warning("Skipping a stored place: %s", e)
        self._areaset: AreaSet | None = None
        self._blocked: tuple[tuple, set[int]] | None = None  # (graph key, edge ids)
        self.missions: dict[str, Mission] = {}
        for d in self.store.read("missions", []):
            try:
                self.missions[d["node"]] = Mission.from_json(d)
            except (KeyError, TypeError, ValueError) as e:
                log.warning("Skipping a stored mission: %s", e)
        self._lock = threading.RLock()
        self._queue: queue.Queue = queue.Queue()
        self._dirty = False
        self._stop = threading.Event()
        self._my_num: int | None = None
        self._seen: dict[str, dict] = {}  # last position of every node, for markers
        self._on_channel: dict[str, float] = {}  # last packet with the channel's key, per node
        self._graph: RoadGraph | None = None
        self._graph_key = None  # (file, mtime) the loaded graph came from
        self.processed = 0  # packets the worker has handled
        if ctx.device is not None:
            ctx.device.listeners.append(self._on_packet)
        threading.Thread(target=self._worker, daemon=True, name="coord-worker").start()
        threading.Thread(target=self._ticker, daemon=True, name="coord-tick").start()

    def shutdown(self) -> None:
        self._stop.set()
        self._save(force=True)

    # ------------------------------------------------------------ API
    def api(self, method: str, parts: list[str], query: dict, body: dict) -> dict:
        """/api/coord/<parts...>: everything the page needs, one dispatch instead of handler
        branches. Raises ValueError/KeyError with a sentence for the page."""
        with self._lock:
            if method == "GET" and not parts:
                return self.snapshot()
            if method == "GET" and parts[:1] == ["missions"] and len(parts) == 2:
                return self._mission(parts[1]).to_json(with_events=True)
            if method != "POST":
                raise KeyError(_("unbekannte Aktion {action}", action="/".join(parts)))
            if parts == ["mode"]:
                self.set_enabled(bool(body.get("on")))
            elif parts == ["settings"]:
                dev = self.ctx.device
                self.settings = clean(
                    {**self.settings, **body}, dev.channels() if dev else [], self.graph_files()
                )
                self._save_settings()
            elif parts[:1] == ["targets"] and len(parts) == 2:
                self.edit_target(parts[1], body)
            elif parts[:1] == ["paths"] and len(parts) == 2:
                self.edit_template(parts[1], body)
            elif parts[:1] == ["areas"] and len(parts) == 2:
                self.edit_area(parts[1], body)
            elif parts[:1] == ["places"] and len(parts) == 2:
                self.edit_place(parts[1], body)
            elif parts == ["missions"]:
                m = self.assign(
                    str(body.get("node", "")),
                    body.get("path") or [],
                    body.get("profile") or self.settings["profile"],
                    body.get("lang") or self.settings["lang"],
                )
                return m.to_json()
            elif parts[:1] == ["missions"] and len(parts) == 3:
                return self.mission_action(parts[1], parts[2], body).to_json()
            else:
                raise KeyError(_("unbekannte Aktion {action}", action="/".join(parts)))
            return self.snapshot()

    def snapshot(self) -> dict:
        dev = self.ctx.device
        graphs = self.graph_files()
        return {
            "enabled": self.enabled,
            "device_state": dev.state if dev else "getrennt",
            "settings": self.settings,
            "declarations": [
                s.to_json() for s in declarations(dev.channels() if dev else [], graphs)
            ],
            "osm": self.osm_state(graphs),
            "missions": [m.to_json() for m in self.missions.values()],
            "targets": self.targets,
            "paths": self.paths,
            "areas": [a.to_json() for a in self.areas],
            "places": [p.to_json() for p in self.places],
        }

    # ------------------------------------------------------------ road graph
    def graph_files(self) -> list[str]:
        d = self.ctx.data_dir / "osm"
        return sorted(p.name[: -len(".json.gz")] for p in d.glob("*.json.gz"))

    @property
    def graph(self) -> RoadGraph | None:
        """The road graph of the settings, loaded once per file version; None without one."""
        path = self.ctx.data_dir / "osm" / f"{self.settings.get('osm_name', 'roads')}.json.gz"
        if not path.exists():
            self._graph, self._graph_key = None, None
            return None
        key = (path.name, path.stat().st_mtime)
        if key != self._graph_key:
            try:
                self._graph = RoadGraph.load(path)
            except (OSError, EOFError, ValueError, KeyError) as e:
                log.warning("Road graph %s unusable: %s", path, e)
                self._graph = None
            self._graph_key = key
        return self._graph

    def graph_changed(self) -> None:
        with self._lock:
            self._graph_key = None
            self._blocked = None

    # ------------------------------------------------------------ areas and places
    @property
    def areaset(self) -> AreaSet:
        if self._areaset is None:
            self._areaset = AreaSet(self.areas, self.places)
        return self._areaset

    def _areas_changed(self) -> None:
        self._areaset, self._blocked = None, None
        self.store.write("areas", [a.to_json() for a in self.areas])
        self.store.write("places", [p.to_json() for p in self.places])
        for m in self.missions.values():
            if m.active and m.route is not None:
                self._route_for(m)  # a new no-go area may lie on the way

    def blocked_edges(self) -> set[int]:
        """Edges of the current graph inside no-go areas, cached per graph version."""
        graph = self.graph
        if graph is None or not any(a.kind == "nogo" for a in self.areas):
            return set()
        if self._blocked is None or self._blocked[0] != self._graph_key:
            self._blocked = (self._graph_key, self.areaset.blocked_edges(graph))
        return self._blocked[1]

    def edit_area(self, action: str, body: dict) -> None:
        aid = str(body.get("id") or "")
        current = next((a for a in self.areas if a.id == aid), None)
        if action == "add":
            area = parse_area(body)
            if any(a.id == area.id for a in self.areas):
                raise ValueError(_("Ein Gebiet {name} gibt es schon", name=area.name))
            self.areas.append(area)
        elif action == "update":
            if current is None:
                raise KeyError(_("Gebiet {id} gibt es nicht", id=aid))
            merged = {**current.to_json(), **{k: v for k, v in body.items() if k != "id"}}
            self.areas[self.areas.index(current)] = parse_area(merged, current.id)
        elif action == "delete":
            if current is None:
                raise KeyError(_("Gebiet {id} gibt es nicht", id=aid))
            self.areas.remove(current)
        else:
            raise KeyError(_("unbekannte Aktion {action}", action=action))
        self._areas_changed()

    def edit_place(self, action: str, body: dict) -> None:
        pid = str(body.get("id") or "")
        current = next((p for p in self.places if p.id == pid), None)
        if action == "add":
            place = parse_place(body)
            if any(p.id == place.id for p in self.places):
                raise ValueError(_("Einen Ort {name} gibt es schon", name=place.name))
            self.places.append(place)
        elif action == "update":
            if current is None:
                raise KeyError(_("Ort {id} gibt es nicht", id=pid))
            merged = {**current.to_json(), **{k: v for k, v in body.items() if k != "id"}}
            self.places[self.places.index(current)] = parse_place(merged, current.id)
        elif action == "delete":
            if current is None:
                raise KeyError(_("Ort {id} gibt es nicht", id=pid))
            self.places.remove(current)
        else:
            raise KeyError(_("unbekannte Aktion {action}", action=action))
        self._areas_changed()

    def osm_state(self, graphs: list[str] | None = None) -> dict:
        from meshplay.mapapp.coord.osm import graph_info

        graphs = self.graph_files() if graphs is None else graphs
        name = self.settings.get("osm_name", "roads")
        out = {"graphs": graphs, "name": name, "loaded": False}
        path = self.ctx.data_dir / "osm" / f"{name}.json.gz"
        if path.exists():
            try:
                out.update(graph_info(path))
                out["loaded"] = self.graph is not None
                if self._graph is not None:
                    out["nodes"], out["edges"] = len(self._graph.nodes), len(self._graph.edges)
            except (OSError, ValueError):
                out["error"] = _("Die Graphdatei ist nicht lesbar")
        return out

    def _route_for(self, m: Mission) -> None:
        """Route from the last position through the remaining waypoints; the first segment
        guides, the rest is drawn. Without a graph or off the roads: straight lines."""
        m.route, m.route_ahead, m.announced, m.off_count = None, [], 0, 0
        graph = self.graph
        if graph is None or m.last is None or not m.active:
            return
        try:
            routes = route_path(
                graph,
                (m.last["lat"], m.last["lon"]),
                [w.pos for w in m.path[m.index :]],
                m.profile,
                self.blocked_edges(),
            )
        except Exception as e:  # a routing bug must not stop the guidance
            log.exception("Routing failed: %s", e)
            self._event(m.node, "route_failed", error=str(e))
            return
        k = m.stop_index - m.index  # segments up to and including the next stop
        needed = routes[: k + 1]
        m.route = Route.concat(needed) if all(needed) else None
        m.route_ahead = [r.coords for r in routes[k + 1 :] if r]
        self._event(
            m.node,
            "route",
            length_m=None if m.route is None else round(m.route.length_m),
            segments=sum(1 for r in routes if r),
        )

    def set_enabled(self, on: bool) -> None:
        dev = self.ctx.device
        if on and (dev is None or dev.state != "verbunden"):
            raise ValueError(_("Gerät nicht verbunden: der Koordinationsmodus braucht es"))
        self.enabled = on
        self._save_settings()
        self._event("*", "mode", on=on)

    def edit_target(self, action: str, body: dict) -> None:
        name = str(body.get("name") or "").strip()
        if action == "delete":
            if name not in self.targets:
                raise KeyError(_("Ziel {name} gibt es nicht", name=name))
            del self.targets[name]
        elif action in ("add", "update"):
            if not NAME_RE.match(name):
                raise ValueError(_("Name: 1–24 Zeichen, nur Buchstaben, Ziffern, _ und -"))
            if action == "add" and name in self.targets:
                raise ValueError(_("Ein Ziel {name} gibt es schon", name=name))
            if action == "update" and name not in self.targets:
                raise KeyError(_("Ziel {name} gibt es nicht", name=name))
            old = self.targets.get(name, {})
            lat = float(body.get("lat", old.get("lat")))
            lon = float(body.get("lon", old.get("lon")))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(_("Ungültige Position"))
            radius = float(body.get("radius_m") or old.get("radius_m") or 0)
            self.targets[name] = {
                # a marker set by radio keeps who set it, also through a rename (add + delete)
                **{
                    k: body.get(k, old.get(k)) for k in ("by", "created") if body.get(k, old.get(k))
                },
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "radius_m": radius or None,
                "note": str(body.get("note", old.get("note", "")))[:100],
            }
        else:
            raise KeyError(_("unbekannte Aktion {action}", action=action))
        self.store.write("targets", self.targets)

    def edit_template(self, action: str, body: dict) -> None:
        """Named paths to reuse (without times; those belong to a day)."""
        name = str(body.get("name") or "").strip()
        if not NAME_RE.match(name):
            raise ValueError(_("Name: 1–24 Zeichen, nur Buchstaben, Ziffern, _ und -"))
        if action == "delete":
            if name not in self.paths:
                raise KeyError(_("Pfad {name} gibt es nicht", name=name))
            del self.paths[name]
        elif action == "save":
            path = parse_path(body.get("path") or [], time.time())
            self.paths[name] = [
                {k: v for k, v in w.to_json().items() if k not in ("arrive_by", "hold_until")}
                for w in path
            ]
        else:
            raise KeyError(_("unbekannte Aktion {action}", action=action))
        self.store.write("paths", self.paths)

    def edit_path(self, node: str, raw_path: list[dict]) -> Mission:
        """Change a running mission's path; the node hears about it only if its current leg
        changed. Passed waypoints stay passed (matched by name)."""
        m = self._mission(node)
        self._require_active(m)
        now = time.time()
        path = parse_path(raw_path, now, float(self.settings["arrive_radius_m"]))
        passed = {w.name.upper() for w in m.path[: m.index]}
        old = m.stop
        m.path = path
        m.index = next(
            (i for i, w in enumerate(path) if w.name.upper() not in passed), len(path) - 1
        )
        new = m.stop
        # times compare by the minute: the page sends them back as hh:mm
        changed = (new.name, new.lat, new.lon, _minute(new.arrive_by), _minute(new.hold_until)) != (
            old.name,
            old.lat,
            old.lon,
            _minute(old.arrive_by),
            _minute(old.hold_until),
        )
        self._event(node, "path_edited", path=[w.name for w in path], changed=changed)
        self._route_for(m)
        if changed:
            m.flags.clear()
            if m.state == HOLDING:
                m.state = UNDERWAY
            self._update_metrics(m, now)
            self._send(m, "changed", self._leg_text(m, "changed"), must=True)
        else:
            self._update_metrics(m, now)
        self._save(force=True)
        return m

    def assign(
        self,
        node: str,
        raw_path: list[dict],
        profile: str,
        lang: str,
        label: str = "",
        origin: str = "page",
    ) -> Mission:
        """A new mission for the node (replacing its old one); sends the first leg."""
        node = node.strip().lower()
        if not (node.startswith("!") and len(node) == 9):
            raise ValueError(_("Knoten: Node-ID wie !abcd1234"))
        if profile not in ("foot", "bike", "car"):
            raise ValueError(_("Fortbewegung: zu Fuß, Fahrrad oder Auto"))
        if lang not in phrases.LANGS:
            raise ValueError(_("Sprache der Funksprüche: de oder en"))
        now = time.time()
        path = parse_path(raw_path, now, float(self.settings["arrive_radius_m"]))
        m = Mission(node, path, profile, lang, now, label, origin)
        old = self.missions.get(node)
        if old is not None and old.last is not None and now - old.last["time"] < 3600:
            m.positions.append(old.last)  # keep the node's last known position
        else:
            pos = self._node_position(node)
            if pos is not None:
                m.positions.append(pos)
        self.missions[node] = m
        self._event(node, "assign", path=[w.name for w in path], profile=profile, origin=origin)
        self._route_for(m)
        self._update_metrics(m, now)
        m.awaiting_position = m.last is None
        self._send(m, "assign", self._leg_text(m, "assign"))
        if not m.awaiting_position:
            self._send_legend(m)
        self._save(force=True)
        return m

    def mission_action(self, node: str, action: str, body: dict) -> Mission:
        m = self._mission(node)
        if action == "status":
            self._require_active(m)
            self._send(m, "status", self._status_text(m), reply=True)
        elif action == "route":
            self._require_active(m)
            self._send(m, "route", self._route_text(m), reply=True)
        elif action == "next":
            self._require_active(m)
            if m.stop_index >= len(m.path) - 1 and m.current.kind != "via":
                raise ValueError(_("{name} ist schon der letzte Halt", name=m.stop.name))
            self._advance(m, time.time(), skipped=True)
        elif action == "path":
            return self.edit_path(node, body.get("path") or [])
        elif action == "end":
            if m.active and self.settings["end_message"] and body.get("notify", True):
                self._send(
                    m, "ended", phrases.phrase(m.lang, "ended", target=m.guide.name), must=True
                )
            m.state = ENDED
            self._event(node, "end")
        elif action == "remove":
            if m.active:
                raise ValueError(_("Einen laufenden Einsatz erst beenden"))
            del self.missions[node]
            self._save(force=True)
            return m
        else:
            raise KeyError(_("unbekannte Aktion {action}", action=action))
        self._save(force=True)
        return m

    def _mission(self, node: str) -> Mission:
        if node not in self.missions:
            raise KeyError(_("Kein Einsatz für {node}", node=node))
        return self.missions[node]

    @staticmethod
    def _require_active(m: Mission) -> None:
        if not m.active:
            raise ValueError(_("Der Einsatz von {node} ist nicht mehr aktiv", node=m.node))

    # ------------------------------------------------------------ layer
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for m in self.missions.values() if m.active)

    def layer_features(self) -> list[dict]:
        """Map features for layers/coord.py: stops, trails, the line to the current stop."""
        from meshplay.mapapp.registry import feature, line

        out = []
        with self._lock:
            for name, t in self.targets.items():
                out.append(
                    feature(
                        t["lon"],
                        t["lat"],
                        _title=name,
                        _icon={
                            "text": name,
                            "symbol": "target",
                            "color": "#6b7f8c",
                            "hint": t.get("note", ""),
                        },
                        _fields={
                            _("Ziel"): name,
                            _("Notiz"): t.get("note", ""),
                            _("Gesetzt per Funk von"): (
                                f"{t['by']} {hhmm(t['created'])}" if t.get("by") else "–"
                            ),
                        },
                        _target=name,
                        _z=2.0,
                    )
                )
            for a in self.areas:
                nogo = a.kind == "nogo"
                out.append(
                    _polygon(
                        [(lon, lat) for lat, lon in a.polygon],
                        _title=a.name,
                        _fields={
                            _("Art"): _("Sperrgebiet") if nogo else _("Hinweisgebiet"),
                            _("Text"): a.text or "–",
                            _("Puffer"): f"{a.buffer_m:g} m",
                        },
                        _style={
                            "color": "#b3392f" if nogo else "#2c6fd6",
                            "fillColor": "#b3392f" if nogo else "#2c6fd6",
                            "fillOpacity": 0.2,
                            "weight": 2,
                            "dash": "6 4" if nogo else None,
                        },
                    )
                )
            for p in self.places:
                out.append(
                    _polygon(
                        circle(p.lat, p.lon, p.radius_m),
                        _title=p.name,
                        _fields={_("Radius"): f"{p.radius_m:g} m", _("Text"): p.text or "–"},
                        _style={
                            "color": "#2c6fd6",
                            "fillColor": "#2c6fd6",
                            "fillOpacity": 0.1,
                            "weight": 1,
                        },
                    )
                )
            for m in self.missions.values():
                color = STATE_COLORS.get(m.state, "#6b7f8c")
                for i, w in enumerate(m.path):
                    current = m.active and i == m.index
                    out.append(
                        feature(
                            w.lon,
                            w.lat,
                            _title=f"{w.name} · {m.node}",
                            _icon={
                                "text": w.name,
                                "symbol": "target" if w.kind == "stop" else "via",
                                "color": color if i >= m.index else "#9aa6ae",
                                "own": current,
                                "hint": _("Wegpunkt {n} von {total}", n=i + 1, total=len(m.path)),
                            },
                            _fields={
                                _("Einsatz"): m.node,
                                _("Art"): _("Halt") if w.kind == "stop" else _("Durchgang"),
                                _("Radius"): f"{w.radius_m:g} m",
                                _("Ankunft bis"): hhmm(w.arrive_by) if w.arrive_by else "–",
                                _("Warten bis"): hhmm(w.hold_until) if w.hold_until else "–",
                            },
                            _style={"color": color, "fillColor": color, "radius": 7},
                            _z=2.0,
                        )
                    )
                pts = [(p["lon"], p["lat"]) for p in m.positions]
                if len(pts) > 1:
                    out.append(line(pts, _style={"color": color, "weight": 3, "opacity": 0.8}))
                if m.active and m.route is not None:
                    out.append(
                        line(
                            [(lon, lat) for lat, lon in m.route.coords],
                            _style={"color": color, "weight": 4, "opacity": 0.85},
                            _title=_("Route zu {name}", name=m.stop.name),
                            _fields={_("Länge"): f"{m.route.length_m:.0f} m"},
                        )
                    )
                    for seg in m.route_ahead:
                        out.append(
                            line(
                                [(lon, lat) for lat, lon in seg],
                                _style={"color": color, "weight": 3, "dash": "6 6", "opacity": 0.6},
                            )
                        )
                elif m.active and m.last is not None:
                    out.append(
                        line(
                            [(m.last["lon"], m.last["lat"]), (m.guide.lon, m.guide.lat)],
                            _style={"color": color, "weight": 2, "dash": "4 6", "opacity": 0.7},
                        )
                    )
        return out

    # ------------------------------------------------------------ packets
    def _on_packet(self, p: dict) -> None:
        """Reader thread: keep what concerns a mission node or is a marker command (+D, ?D,
        from any node), hand it to the worker. Every node's last position is remembered: a
        marker is set where its sender is."""
        sender = p.get("fromId") or f"!{p.get('from', 0):08x}"
        decoded = p.get("decoded", {})
        port = decoded.get("portnum")
        mission = sender in self.missions
        # decrypted with the channel's key: the sender has it. A PKI direct message arrives as
        # channel 0 whatever channel the sender picked, so it proves nothing.
        if (
            decoded
            and not p.get("pkiEncrypted")
            and p.get("channel", 0) == int(self.settings.get("channel", 0))
        ):
            self._on_channel[sender] = time.time()
        if port == "POSITION_APP" and "latitude" in decoded.get("position", {}):
            self._seen[sender] = _position_record(p, time.time())
            if mission:
                self._queue.put(("position", sender, p))
        elif port == "TEXT_MESSAGE_APP" and decoded.get("text"):
            my = self._own_num()
            if my is not None and p.get("to") != my:
                return
            if mission or phrases.parse_marker(decoded["text"]) is not None:
                self._queue.put(("text", sender, p))

    def _own_num(self) -> int | None:
        if self._my_num is None:
            dev = self.ctx.device
            iface = getattr(dev, "iface", None)
            self._my_num = getattr(getattr(iface, "myInfo", None), "my_node_num", None)
        return self._my_num

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                kind, node, p = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    m = self.missions.get(node)
                    if kind == "text":
                        self._on_text(node, p["decoded"]["text"])
                    elif m is not None:
                        self._on_position(m, p)
            except Exception as e:  # one bad packet must not stop the coordination
                log.exception("Coordination worker failed: %s", e)
            finally:
                self.processed += 1  # tests wait on this

    def _ticker(self) -> None:
        while not self._stop.wait(5.0):
            try:
                with self._lock:
                    self._tick(time.time())
                self._save()
            except Exception as e:
                log.exception("Coordination tick failed: %s", e)

    # ------------------------------------------------------------ positions
    def _on_position(self, m: Mission, p: dict) -> None:
        pos = p["decoded"]["position"]
        now = time.time()
        bits = pos.get("precisionBits", 32)
        if bits < int(self.settings["min_precision_bits"]):
            m.metrics["precision_bits"] = bits
            self._event(m.node, "position_ignored", bits=bits)
            return
        rec = _position_record(p, now)
        prev = m.last
        if prev is not None:
            step = distance_m((prev["lat"], prev["lon"]), (rec["lat"], rec["lon"]))
            if step >= MOVE_MIN_M:
                m.travelled_m += step
        m.positions.append(rec)
        self._event(m.node, "position", lat=rec["lat"], lon=rec["lon"], snr=rec["snr"])
        if not m.active:
            self._update_metrics(m, now)
            return
        if m.state == ASSIGNED:
            m.state = UNDERWAY
        if m.route is None and self.graph is not None:  # first position, or a graph since
            self._route_for(m)
        self._update_metrics(m, now)
        if m.awaiting_position:  # the assignment could not say where to go: now it can
            m.awaiting_position = False
            self._send(m, "assign", self._leg_text(m, "assign"), must=True)
            self._send_legend(m)
        self._decide(m, now)
        self._dirty = True

    def _send_legend(self, m: Mission) -> None:
        """Once per mission, right after the first real assignment: what the codes mean."""
        if m.legend_sent or not self.settings.get("send_legend", True):
            return
        m.legend_sent = True
        self._send(m, "legend", phrases.phrase(m.lang, "legend"), reply=True)

    def _update_metrics(self, m: Mission, now: float) -> None:
        last = m.last
        stop = m.guide
        out = {
            "position_age_s": None if last is None else round(now - last["time"]),
            "precision_bits": None if last is None else last["bits"],
            "snr": None if last is None else last["snr"],
            "hops": None if last is None else last["hops"],
            "travelled_m": round(m.travelled_m),
            "elapsed_s": round(now - m.assigned_at),
            "stale": last is not None and now - last["time"] > self.settings["stale_min"] * 60,
            "mode": "line",
        }
        if last is not None:
            d = distance_m((last["lat"], last["lon"]), stop.pos)
            speed, source = self._speed(m)
            to_go = d
            if m.route is not None:
                along, off = m.route.progress(last["lat"], last["lon"])
                to_go = max(m.route.length_m - along, 0.0)
                out.update(
                    mode="route",
                    along_m=round(along),
                    off_route_m=round(off),
                    route_left_m=round(to_go),
                    route_length_m=round(m.route.length_m),
                )
            out.update(
                dist_m=round(d),
                bearing=round(bearing_deg((last["lat"], last["lon"]), stop.pos)),
                compass=compass(bearing_deg((last["lat"], last["lon"]), stop.pos)),
                speed_kmh=round(speed * 3.6, 1),
                speed_source=source,
                eta_s=round(to_go / speed) if speed > 0 else None,
            )
            first = m.positions[0]
            moving_s = last["time"] - first["time"]
            if m.travelled_m >= 50 and moving_s >= 60:  # the average over the whole mission
                out["avg_speed_kmh"] = round(m.travelled_m / moving_s * 3.6, 1)
            if stop.arrive_by and out["eta_s"] is not None:
                out["margin_min"] = round((stop.arrive_by - (now + out["eta_s"])) / 60)
        m.metrics = out

    def _speed(self, m: Mission) -> tuple[float, str]:
        """m/s: median of the last three usable legs (or reported ground speeds), else the
        profile's default."""
        samples = []
        pts = list(m.positions)[-6:]
        for a, b in zip(pts, pts[1:], strict=False):
            dt = b["time"] - a["time"]
            d = distance_m((a["lat"], a["lon"]), (b["lat"], b["lon"]))
            if SPEED_DT_S[0] <= dt <= SPEED_DT_S[1] and d >= MOVE_MIN_M:
                samples.append(d / dt)
        reported = [p["speed"] for p in pts[-3:] if p.get("speed") and p["speed"] > 0.3]
        samples = (samples + reported)[-3:]
        if samples:
            return statistics.median(samples), "gemessen"
        return default_speed_ms(self.settings, m.profile), "Standard"

    # ------------------------------------------------------------ decisions
    def _decide(self, m: Mission, now: float) -> None:
        if m.route is not None and self._pass_vias(m):
            self._update_metrics(m, now)
        stop = m.guide
        d = m.metrics["dist_m"]
        if d <= stop.radius_m:
            if stop.kind == "via":  # no route: the via was a straight-line target
                m.index += 1
                m.flags.clear()
                self._event(m.node, "via", name=stop.name)
                self._update_metrics(m, now)
                self._send(m, "next", self._leg_text(m, "next"), must=True)
            elif m.state == HOLDING:
                m.flags.discard("early")  # back at the stop: a new early departure warns again
            elif m.stop_index >= len(m.path) - 1 or not stop.hold_until:
                self._advance(m, now)
            else:
                self._hold(m, now)
            return
        if m.state == HOLDING:
            if stop.hold_until and now < stop.hold_until and "early" not in m.flags:
                m.flags.add("early")
                self._send(
                    m,
                    "early",
                    phrases.phrase(m.lang, "early", target=stop.name, time=hhmm(stop.hold_until)),
                    priority=True,
                )
            return
        if m.held:
            return
        self._decide_areas(m, now)
        if m.route is not None:
            self._decide_route(m, now)
        else:
            self._decide_line(m, now, d)
        if stop.arrive_by and m.metrics.get("eta_s") is not None:
            late_s = now + m.metrics["eta_s"] - stop.arrive_by
            if late_s > self.settings["late_warn_min"] * 60 and "late" not in m.flags:
                m.flags.add("late")
                self._send(
                    m,
                    "late",
                    phrases.phrase(
                        m.lang,
                        "late",
                        target=stop.name,
                        eta_time=hhmm(now + m.metrics["eta_s"]),
                        time=hhmm(stop.arrive_by),
                    ),
                    priority=True,
                )
            elif late_s <= 0 and "late" in m.flags:
                m.flags.discard("late")
        every = self.settings["confirm_every_min"]
        if every and now - m.sent.get("confirm", m.assigned_at) >= every * 60:
            self._send(
                m,
                "confirm",
                phrases.phrase(
                    m.lang,
                    "confirm",
                    target=stop.name,
                    dist=fmt_dist(d),
                    eta=fmt_eta(m.metrics.get("eta_s")),
                ),
            )

    def _pass_vias(self, m: Mission) -> bool:
        """With a route: vias the node has passed (projection beyond them, or within their
        radius) are done silently. True if the current waypoint changed."""
        along = m.metrics.get("along_m")
        last = m.last
        moved = False
        while m.current.kind == "via" and m.index < m.stop_index:
            via = m.current
            via_along = m.route.progress(*via.pos)[0]
            near = distance_m((last["lat"], last["lon"]), via.pos) <= via.radius_m
            if along is None or (along < via_along - 10 and not near):
                break
            m.index += 1
            moved = True
            self._event(m.node, "via", name=via.name)
        return moved

    def _decide_line(self, m: Mission, now: float, d: float) -> None:
        """Off course without a road graph: the distance grew three times in a row."""
        stop = m.guide
        dists = [distance_m((p["lat"], p["lon"]), stop.pos) for p in list(m.positions)[-3:]]
        growing = len(dists) == 3 and all(
            b - a > OFF_STEP_M for a, b in zip(dists, dists[1:], strict=False)
        )
        if growing and "offcourse" not in m.flags:
            m.flags.add("offcourse")
            m.offcourse_from_m = dists[0]
            self._event(m.node, "offcourse", dist=d)
            self._send(
                m,
                "offcourse",
                phrases.phrase(
                    m.lang,
                    "offcourse",
                    off=fmt_dist(d - dists[0]),
                    dir=m.metrics["compass"],
                    dist=fmt_dist(d),
                ),
                priority=True,
            )
        elif "offcourse" in m.flags and d < m.offcourse_from_m - OFF_STEP_M:
            m.flags.discard("offcourse")
            self._event(m.node, "oncourse", dist=d)

    def _decide_areas(self, m: Mission, now: float) -> None:
        """No-go areas (inside, or ahead on the route), notice areas and places nearby: each
        once per entry."""
        if not self.areas and not self.places:
            return
        st = m.area_state
        lat, lon = m.last["lat"], m.last["lon"]
        inside = self.areaset.inside(lat, lon)
        ids = {a.id for a in inside}
        for a in inside:
            if a.id in st.inside:
                continue
            st.inside.add(a.id)
            self._event(m.node, "area_entered", area=a.name, area_kind=a.kind)
            if a.kind == "nogo":
                self._send(m, "nogo", phrases.phrase(m.lang, "nogo_in", name=a.name), priority=True)
            elif a.text:
                self._send(m, "notice", phrases.phrase(m.lang, "notice", name=a.name, text=a.text))
        for aid in list(st.inside - ids):
            st.inside.discard(aid)
            self._event(m.node, "area_left", area=aid)
        hit = None
        if m.route is not None and "along_m" in m.metrics:  # the route itself crosses one
            hit = self.areaset.ahead(m.route, m.metrics["along_m"])
        pts = list(m.positions)[-2:]
        if hit is None and len(pts) == 2:  # the node's own heading points into one
            prev, last = pts
            if distance_m((prev["lat"], prev["lon"]), (last["lat"], last["lon"])) >= MOVE_MIN_M:
                brg = bearing_deg((prev["lat"], prev["lon"]), (last["lat"], last["lon"]))
                hit = self.areaset.ahead_heading(last["lat"], last["lon"], brg)
        if hit is not None and hit[0].id not in st.announced and hit[0].id not in ids:
            st.announced.add(hit[0].id)
            self._event(m.node, "nogo_ahead", area=hit[0].name, dist=round(hit[1]))
            self._send(
                m,
                "nogo",
                phrases.phrase(m.lang, "nogo_ahead", name=hit[0].name, dist=fmt_dist(hit[1])),
                priority=True,
            )
        near = self.areaset.near_places(lat, lon)
        for place, d in near:
            if place.id in st.places:
                continue
            st.places.add(place.id)
            self._event(m.node, "place", place=place.name, dist=round(d))
            self._send(
                m,
                "place",
                phrases.phrase(m.lang, "place", name=place.name, dist=fmt_dist(d), text=place.text),
            )
        for pid in list(st.places):  # re-arm once well outside the radius
            place = next((p for p in self.places if p.id == pid), None)
            if place is None or self.areaset.place_distance(place, lat, lon) > 1.5 * place.radius_m:
                st.places.discard(pid)

    def _decide_route(self, m: Mission, now: float) -> None:
        """With a route: off course by cross-track distance (two positions in a row), then a
        new route; on course, turn instructions by the setting."""
        off = m.metrics["off_route_m"]
        limit = float(self.settings["off_route_m"])
        n = int(self.settings["legs_per_message"])
        if off > limit:
            m.off_count += 1
            if m.off_count >= 2 and "offcourse" not in m.flags:
                m.flags.add("offcourse")
                self._event(m.node, "offcourse", off=off)
                self._route_for(m)  # from where the node is now
                m.flags.add("offcourse")
                self._update_metrics(m, m.last["time"])
                if m.route is not None:
                    text = phrases.phrase(
                        m.lang,
                        "offcourse_route",
                        off=fmt_dist(off),
                        legs=legs_text(m.route.legs(), n),
                    )
                else:
                    text = phrases.phrase(
                        m.lang,
                        "offcourse",
                        off=fmt_dist(off),
                        dir=m.metrics["compass"],
                        dist=fmt_dist(m.metrics["dist_m"]),
                    )
                self._send(m, "offcourse", text, priority=True)
            return
        m.off_count = 0
        if "offcourse" in m.flags and off < limit / 2:
            m.flags.discard("offcourse")
            self._event(m.node, "oncourse", off=off)
        if m.metrics["route_left_m"] <= 15 and m.metrics["dist_m"] > m.guide.radius_m:
            # the route is used up but the stop is not reached (the node cut across, the
            # road ends short of the point): a fresh route from here
            self._event(m.node, "route_exhausted", dist=m.metrics["dist_m"])
            self._route_for(m)
            self._update_metrics(m, now)
            if m.route is None:
                return
        mode = self.settings["instructions"]
        legs = m.route.legs(m.metrics["along_m"])
        if mode == "turns" and len(legs) >= 2 and legs[0].dist_m <= TURN_AHEAD_M:
            if m.announced != len(legs):
                m.announced = len(legs)
                self._send(m, "route", phrases.phrase(m.lang, "route", legs=legs_text(legs[1:], n)))
        elif mode == "interval" and m.travelled_m - m.instr_at_m >= INTERVAL_M and len(legs) > 1:
            m.instr_at_m = m.travelled_m
            self._send(m, "route", phrases.phrase(m.lang, "route", legs=legs_text(legs, n)))

    def _advance(self, m: Mission, now: float, skipped: bool = False) -> None:
        """The next stop is done (reached, or skipped by the coordinator): the one after it
        becomes current, or the mission is over."""
        if skipped and m.current.kind == "via":  # skipping a via keeps the stop
            self._event(m.node, "skipped", name=m.current.name)
            m.index += 1
            m.flags.clear()
            self._route_for(m)
            self._update_metrics(m, now)
            self._send(m, "next", self._leg_text(m, "next"), must=True)
            return
        stop = m.stop
        m.index = m.stop_index
        m.flags.clear()
        reached_at = hhmm(now)
        self._event(m.node, "skipped" if skipped else "reached", name=stop.name)
        if m.index >= len(m.path) - 1:
            m.state, m.arrived_at = ARRIVED, now
            self._send(
                m,
                "reached",
                phrases.phrase(
                    m.lang,
                    "reached_final",
                    target=stop.name,
                    time=reached_at,
                    travelled=fmt_dist(m.travelled_m),
                    duration=fmt_eta(now - m.assigned_at)[1:],
                ),
                must=True,
            )
            return
        m.index += 1
        m.state = UNDERWAY
        self._route_for(m)
        self._update_metrics(m, now)
        if skipped:
            self._send(m, "next", self._leg_text(m, "next"), must=True)
        else:
            self._send(
                m,
                "reached",
                phrases.phrase(
                    m.lang,
                    "reached_next",
                    target=stop.name,
                    time=reached_at,
                    next=m.guide.name,
                    dist=fmt_dist(m.metrics["dist_m"]),
                    dir=m.metrics["compass"],
                    eta=fmt_eta(m.metrics.get("eta_s")),
                ),
                must=True,
            )

    def _hold(self, m: Mission, now: float) -> None:
        stop = m.stop
        m.index = m.stop_index
        m.state, m.hold_arrived_at = HOLDING, now
        m.flags.clear()
        self._event(m.node, "hold", name=stop.name, until=stop.hold_until)
        self._send(
            m,
            "reached",
            phrases.phrase(
                m.lang,
                "reached_hold",
                target=stop.name,
                time=hhmm(now),
                travelled=fmt_dist(m.travelled_m),
                duration=fmt_eta(now - m.assigned_at)[1:],
                until=hhmm(stop.hold_until),
            ),
            must=True,
        )

    def _tick(self, now: float) -> None:
        for m in self.missions.values():
            if m.retry and now >= m.retry[0]:
                _, kind, text = m.retry
                m.retry = None
                if m.active:
                    self._event(m.node, "retry", what=kind)
                    self._send(m, kind, text, priority=True, retried=True)
            if not m.active:
                continue
            self._update_metrics(m, now)
            if m.state == HOLDING and m.stop.hold_until and now >= m.stop.hold_until:
                self._event(m.node, "hold_over", name=m.stop.name)
                m.flags.clear()
                if m.stop_index >= len(m.path) - 1:  # the path was cut short meanwhile
                    m.state, m.arrived_at = ARRIVED, now
                else:
                    m.index = m.stop_index + 1
                    m.state = UNDERWAY
                    self._route_for(m)
                    self._update_metrics(m, now)
                    self._send(m, "next", self._leg_text(m, "next"), must=True)
                self._dirty = True

    # ------------------------------------------------------------ commands
    def _on_text(self, node: str, text: str) -> None:
        marker = phrases.parse_marker(text)
        if marker is not None:
            self._on_marker(node, *marker)
            return
        m = self.missions.get(node)
        cmd = phrases.parse_command(text)
        if m is None or cmd is None or not m.active:
            return
        self._event(m.node, "command", command=cmd, text=text)
        self._update_metrics(m, time.time())  # answers use the current age, speed and ETA
        if cmd == "status":
            self._send(m, "status", self._status_text(m), reply=True)
        elif cmd == "route":
            self._send(m, "route", self._route_text(m), reply=True)
        elif cmd == "target":
            self._send(m, "target", self._target_text(m), reply=True)
        elif cmd == "path":
            self._send(m, "path", self._path_text(m), reply=True)
        elif cmd == "eta":
            self._send(m, "eta", self._eta_text(m), reply=True)
        elif cmd == "help":
            self._send(m, "help", phrases.phrase(m.lang, "help"), reply=True)
        elif cmd == "legend":
            self._send(m, "legend", phrases.phrase(m.lang, "legend"), reply=True)
        elif cmd == "halt":
            m.held = True
            self._send(m, "halt", phrases.phrase(m.lang, "halt_ok"), reply=True)
        elif cmd == "go":
            m.held = False
            self._send(m, "resume", self._leg_text(m, "resume"), reply=True)
        elif cmd == "abort":
            m.state = ABORTED
            self._send(
                m, "aborted", phrases.phrase(m.lang, "aborted", target=m.guide.name), reply=True
            )
        self._dirty = True

    def _on_marker(self, node: str, action: str, name: str, label: str) -> None:
        """+D NAME label: a target at the sender's position. ?D NAME / ?D: that target, or the
        nearest one, becomes the sender's new mission. Who may is the "markers" setting: nodes
        on the private channel (default) or nodes with a mission; nobody else gets an answer.
        With the mode off nothing happens."""
        allowed = self.settings.get("markers", DEFAULTS["markers"])
        m = self.missions.get(node)
        if allowed == "missions":
            ok = m is not None and m.active
        else:
            ok = allowed == "channel" and self.on_channel(node)
        if not self.enabled or not ok:
            self._event(node, "marker_ignored", action=action, name=name)
            return
        lang = m.lang if m is not None else self.settings["lang"]
        self._event(node, "marker_command", action=action, name=name, label=label)
        pos = self._node_position(node)
        here = None if pos is None else (pos["lat"], pos["lon"])
        if action == "set":
            if not NAME_RE.match(name):
                self._reply(node, "marker", phrases.phrase(lang, "marker_usage"))
            elif self._target_key(name) is not None:
                self._reply(node, "marker", phrases.phrase(lang, "marker_exists", name=name))
            elif here is None:
                self._reply(node, "marker", phrases.phrase(lang, "marker_nopos", cmd=f"+D {name}"))
            else:
                self.targets[name] = {
                    "lat": round(here[0], 7),
                    "lon": round(here[1], 7),
                    "radius_m": None,
                    "note": label,
                    "by": node,
                    "created": round(time.time()),
                }
                self.store.write("targets", self.targets)
                self._event(node, "marker_set", name=name, label=label)
                self._reply(
                    node, "marker", phrases.phrase(lang, "marker_set", name=name, label=label)
                )
            return
        if name:
            key = self._target_key(name)
            if key is None:
                self._reply(node, "marker", phrases.phrase(lang, "marker_unknown", name=name))
                return
        elif not self.targets:
            self._reply(node, "marker", phrases.phrase(lang, "marker_none"))
            return
        elif here is None:
            self._reply(node, "marker", phrases.phrase(lang, "marker_nopos", cmd="?D"))
            return
        else:
            key = self._nearest_target(here)
        t = self.targets[key]
        waypoint = {"name": key, "lat": t["lat"], "lon": t["lon"], "radius_m": t.get("radius_m")}
        self.assign(
            node, [waypoint], self.settings["profile"], lang, t.get("note", ""), origin="radio"
        )

    def on_channel(self, node: str) -> bool:
        """The node sent a packet with the key of the messages' channel recently."""
        return time.time() - self._on_channel.get(node, 0.0) <= CHANNEL_PROOF_S

    def _target_key(self, name: str) -> str | None:
        """The stored name of a target, whatever case the field typed."""
        return next((k for k in self.targets if k.upper() == name.upper()), None)

    def _nearest_target(self, here: tuple[float, float]) -> str:
        """The nearest target the node is not already at (after arriving at one, ?D means the
        next); the nearest of all when it is at every one."""

        def dist(k: str) -> float:
            return distance_m(here, (self.targets[k]["lat"], self.targets[k]["lon"]))

        radius = float(self.settings["arrive_radius_m"])
        away = [k for k in self.targets if dist(k) > (self.targets[k].get("radius_m") or radius)]
        return min(away or self.targets, key=dist)

    # ------------------------------------------------------------ texts
    def _leg_text(self, m: Mission, key: str) -> str:
        """assign / next / changed / resume: the current stop with distance, direction, ETA."""
        mt = m.metrics
        guide = m.guide
        if "dist_m" not in mt:
            return phrases.phrase(m.lang, "assign_nopos", target=guide.name)
        params = dict(
            target=guide.name,
            dist=fmt_dist(mt["dist_m"]),
            dir=mt["compass"],
            eta=fmt_eta(mt.get("eta_s")),
        )
        if key == "assign" and guide.arrive_by:
            key = "assign_by"
            params.update(time=hhmm(guide.arrive_by), margin=_margin(mt.get("margin_min")))
        # a marker's long name goes with its assignment, but the route legs matter more
        names = [guide.name]
        if m.label and key.startswith("assign") and guide is m.path[-1]:
            names.insert(0, f"{guide.name} {m.label}")
        texts = [phrases.phrase(m.lang, key, **{**params, "target": n}) for n in names]
        if m.route is not None:  # the first legs fit in the same message
            n = int(self.settings["legs_per_message"])
            route = phrases.phrase(
                m.lang, "route", legs=legs_text(m.route.legs(mt.get("along_m", 0.0)), n)
            )
            for text in texts:
                if phrases.fits(text + " " + route, phrases.TARGET_BYTES):
                    return text + " " + route
        return next((t for t in texts if phrases.fits(t, phrases.TARGET_BYTES)), texts[-1])

    def _status_text(self, m: Mission) -> str:
        mt = m.metrics
        guide = m.guide
        if "dist_m" not in mt:
            return phrases.phrase(m.lang, "assign_nopos", target=guide.name)
        params = dict(
            target=guide.name,
            dist=fmt_dist(mt["dist_m"]),
            dir=mt["compass"],
            eta=fmt_eta(mt.get("eta_s")),
            speed=f"{mt['speed_kmh']:g}km/h",
        )
        if guide.arrive_by:
            return phrases.phrase(
                m.lang,
                "status_by",
                time=hhmm(guide.arrive_by),
                margin=_margin(mt.get("margin_min")),
                **params,
            )
        return phrases.phrase(m.lang, "status", **params)

    def _route_text(self, m: Mission) -> str:
        mt = m.metrics
        if "dist_m" not in mt:
            return phrases.phrase(m.lang, "assign_nopos", target=m.guide.name)
        if m.route is not None:
            n = int(self.settings["legs_per_message"])
            legs = legs_text(m.route.legs(mt.get("along_m", 0.0)), n)
        else:
            legs = f"{mt['compass']}{fmt_dist(mt['dist_m'])} Z"  # no road graph: as the crow flies
        return phrases.phrase(m.lang, "route", legs=legs)

    def _target_text(self, m: Mission) -> str:
        mt = m.metrics
        if "dist_m" not in mt:
            return phrases.phrase(m.lang, "assign_nopos", target=m.guide.name)
        return phrases.phrase(
            m.lang, "target", target=m.guide.name, dist=fmt_dist(mt["dist_m"]), dir=mt["compass"]
        )

    def _eta_text(self, m: Mission) -> str:
        """Arrival as a clock time from the current speed, with the average since the start."""
        mt = m.metrics
        if "dist_m" not in mt or mt.get("eta_s") is None:
            return phrases.phrase(m.lang, "assign_nopos", target=m.guide.name)
        now = time.time()
        params = dict(
            target=m.guide.name,
            time=hhmm(now + mt["eta_s"]),
            eta=fmt_eta(mt["eta_s"]),
            speed=f"{mt['speed_kmh']:g}km/h",
        )
        if mt.get("speed_source") != "gemessen":
            return phrases.phrase(m.lang, "eta_default", **params)
        avg = mt.get("avg_speed_kmh")
        return phrases.phrase(
            m.lang, "eta", avg=f"{avg:g}km/h" if avg else params["speed"], **params
        )

    def _path_text(self, m: Mission) -> str:
        parts = []
        for w in stops(m.path):
            s = w.name
            if w.arrive_by:
                s += " " + hhmm(w.arrive_by)
            parts.append(s)
        return phrases.phrase(m.lang, "path", stops=" > ".join(parts))

    # ------------------------------------------------------------ sending
    def _send(
        self,
        m: Mission,
        kind: str,
        text: str,
        priority: bool = False,
        reply: bool = False,
        retried: bool = False,
        must: bool = False,
    ) -> bool:
        """Send if the mode is on and the rate limit allows; every outcome is an event.
        priority: a warning that may follow another message after PRIORITY_GAP_S. must: a
        message the mission depends on (assignment, arrival, next leg): never rate-limited.
        reply: an answer to the node's command: never rate-limited, does not count as
        proactive."""
        now = time.time()
        if not self.enabled:
            self._event(m.node, "suppressed", what=kind, text=text, why="mode off")
            return False
        if not reply and not must:
            gap = now - m.last_proactive
            limit = PRIORITY_GAP_S if priority else float(self.settings["min_gap_s"])
            if gap < limit:
                self._event(m.node, "skipped", what=kind, text=text, gap=round(gap))
                return False
        dev = self.ctx.device
        rec = {"time": round(now, 1), "kind": kind, "text": text, "id": None, "status": "gesendet"}

        def on_status(status: str) -> None:
            rec["status"] = status
            self._event(m.node, "delivery", what=kind, status=status)
            if status.startswith("nicht") and kind in RETRY_KINDS and not retried:
                with self._lock:
                    m.retry = (time.time() + RETRY_S, kind, text)
            self._dirty = True

        try:
            sent = dev.send_text(
                text, m.node, int(self.settings.get("channel", 0)), on_status=on_status, tag="coord"
            )
            rec["id"] = sent["id"]
        except Exception as e:  # not connected, text too long: shown in the mission
            rec["status"] = f"nicht gesendet: {e}"
            self._event(m.node, "send_failed", what=kind, text=text, error=str(e))
            m.messages.append(rec)
            return False
        m.messages.append(rec)
        m.sent[kind] = now
        if not reply:
            m.last_proactive = now
        self._event(m.node, "sent", what=kind, text=text)
        self._dirty = True
        return True

    def _reply(self, node: str, kind: str, text: str) -> None:
        """The answer to a node's command; a node without a mission gets it directly."""
        m = self.missions.get(node)
        if m is not None:
            self._send(m, kind, text, reply=True)
            return
        if not self.enabled:
            self._event(node, "suppressed", what=kind, text=text, why="mode off")
            return
        try:
            self.ctx.device.send_text(text, node, int(self.settings.get("channel", 0)), tag="coord")
        except Exception as e:
            self._event(node, "send_failed", what=kind, text=text, error=str(e))
            return
        self._event(node, "sent", what=kind, text=text)

    # ------------------------------------------------------------ helpers
    def _node_position(self, node: str) -> dict | None:
        """A node's last position, if recent and precise enough: from its own broadcasts,
        else from the device's node database."""
        rec = self._seen.get(node)
        if (
            rec is not None
            and time.time() - rec["time"] <= self.settings["stale_min"] * 60
            and rec["bits"] >= int(self.settings["min_precision_bits"])
        ):
            return rec
        return self._db_position(node)

    def _db_position(self, node: str) -> dict | None:
        """Last known position of a node from the device's node database, if recent."""
        dev = self.ctx.device
        if dev is None or dev.iface is None:
            return None
        try:
            nodes, _me = dev.nodes()
        except RuntimeError:
            return None
        n = nodes.get(node)
        if not n:
            return None
        pos = n.get("position") or {}
        last = n.get("lastHeard") or 0
        if "latitude" not in pos or time.time() - last > self.settings["stale_min"] * 60:
            return None
        return {
            "time": float(last),
            "lat": pos["latitude"],
            "lon": pos["longitude"],
            "bits": pos.get("precisionBits", 32),
            "snr": n.get("snr"),
            "hops": n.get("hopsAway"),
            "speed": None,
        }

    def _event(self, node: str, event: str, **fields) -> None:
        rec = self.store.log_event(node, event, **fields)
        m = self.missions.get(node)
        if m is not None:
            m.events.append(rec)

    def _save_settings(self) -> None:
        self.store.write("settings", {**self.settings, "enabled": self.enabled})

    def _save(self, force: bool = False) -> None:
        if not (force or self._dirty):
            return
        with self._lock:
            self._dirty = False
            self.store.write("missions", [m.to_json() for m in self.missions.values()])


def _position_record(p: dict, now: float) -> dict:
    """What a mission keeps of a position packet."""
    pos = p["decoded"]["position"]
    return {
        "time": round(now, 1),
        "lat": pos["latitude"],
        "lon": pos["longitude"],
        "bits": pos.get("precisionBits", 32),
        "snr": p.get("rxSnr"),
        "rssi": p.get("rxRssi"),
        "hops": (p.get("hopStart") or 0) - (p.get("hopLimit") or 0)
        if p.get("hopStart") is not None
        else None,
        "speed": pos.get("groundSpeed"),
    }


def _polygon(ring: list[tuple[float, float]], **props) -> dict:
    """GeoJSON polygon from a (lon, lat) ring, closed if it isn't."""
    if ring and ring[0] != ring[-1]:
        ring = [*ring, ring[0]]
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
        "properties": props,
    }


def _minute(ts: float | None) -> int | None:
    return None if ts is None else int(ts // 60)


def _margin(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    return f"{-minutes:+d}"  # "+2" = two minutes late, "-3" = early


STATE_COLORS = {
    ASSIGNED: "#b4740e",
    UNDERWAY: "#00707f",
    HOLDING: "#7b3294",
    ARRIVED: "#2e7d53",
    ABORTED: "#b3392f",
    ENDED: "#6b7f8c",
}
