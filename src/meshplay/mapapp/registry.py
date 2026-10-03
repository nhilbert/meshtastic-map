"""Layer base class, setting declarations and the shared app context.

A layer returns one of two payloads from data():

  vector  a GeoJSON FeatureCollection (WGS84). Per feature, optional properties drive display:
            _style     {"color", "fillColor", "radius", "weight", "opacity", "dash"}; a point
                       with "shape": "hex" is a hexagon of "size" px (default 18) with a ring in
                       the theme's contrast colour (or "ring") and an optional "text" inside
            _title     popup/tooltip heading
            _fields    {label: value} shown in the popup
            _z         height above ground in the 3D view [m] (default 2)
            _icon      badge marker instead of a circle: {"text", "symbol" (router, tracker,
                       client, sensor, home, target, via), "color", "own", "faded", "hint"}
            _node_id   Meshtastic node ID ("!abcd1234"): the node list finds the marker
            _ref       what the feature is, for the actions in its popup and right-click menu:
                       {"type": "node" | "site" | "target" | "area" | "place" | "suggestion"
                       | "waypoint" | "mission" | "scene", "id", ...}; the page registers the
                       actions per type (webmap/js/actions.js)
            _endpoint  makes the feature usable as start/end of the link tool:
                       {"height_m": [lo, hi], "clutter_m", "indoor", "device", "measured": {...}}
  raster  {"type": "raster", "image": <PNG data URL>, "bounds": [[south, west], [north, east]]}

Both may carry "legend": {"title", "items": [[color, label], ...]} or
{"title", "ramp": [colors...], "min", "max", "unit"}.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from meshplay.config import Settings, load_settings
from meshplay.mapapp.i18n import _


@dataclass
class Setting:
    name: str
    label: str
    type: str  # "select", "number", "bool", "text"; "point", "bbox": text picked on the map
    default: Any = None
    options: list | None = None  # [[value, label], ...] for select
    depends_on: str | None = None  # select whose options depend on another setting's value
    options_map: dict | None = None  # {other_value: [[value, label], ...]}
    min: float | None = None
    max: float | None = None  # "bbox": the largest side in degrees
    step: float | None = None
    help: str = ""
    square_km_from: str | None = None  # "point": preview a square, edge [km] from that setting

    def to_json(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    def parse(self, raw: str | None) -> Any:
        if raw is None or raw == "":
            return self.default
        if self.type == "number":
            return float(raw)
        if self.type == "bool":
            return str(raw).lower() in ("1", "true", "yes", "on")
        return raw


class Context:
    """Paths, configuration and lazily loaded heavy objects shared by layers and tools."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.data_dir: Path = self.settings.data_dir
        self.sim_dir = self.data_dir / "sim"
        self.app_dir = self.data_dir / "mapapp"
        self.cache_dir = self.app_dir / "cache"
        self.device = None  # meshplay.mapapp.device.DeviceLink, set by the server
        self.jobs = None  # meshplay.mapapp.jobs.JobManager, set by the server
        self.node_requests = None  # meshplay.mapapp.node_requests.NodeRequests, ditto
        self.coord = None  # meshplay.mapapp.coord.missions.Coordinator, set by the server
        self.tiles = None  # meshplay.mapapp.tiles.TileCache, set by the server
        # The scene, the models and their caches are not written for concurrent use: whatever
        # touches them holds this lock; everything else (node list, missions, messages) doesn't.
        self.model_lock = threading.RLock()
        self._scene = None  # (name, Scene) of the loaded scene

    @property
    def scene_name(self) -> str | None:
        """The active scene (data/sim/scenes/active.txt), None without any scene."""
        from meshplay.sim import scenes

        scenes.migrate_legacy(self.sim_dir)
        return scenes.active_name(self.sim_dir)

    @property
    def scene(self):
        """The active scene, loaded once; switching scenes loads the new one (callers hold
        model_lock, as for everything that touches the scene)."""
        from meshplay.sim import scenes
        from meshplay.sim.scene import Scene

        name = self.scene_name
        if self._scene is None or self._scene[0] != name:
            self._scene = None  # free the old one before loading the next
            self._scene = (name, Scene.load(scenes.scene_path(self.sim_dir, name)))
        return self._scene[1]

    @property
    def has_scene(self) -> bool:
        return self.scene_name is not None

    def forget_scene(self) -> None:
        """Drop the loaded scene (it was deleted or rebuilt)."""
        self._scene = None

    @cached_property
    def sites(self) -> dict:
        from meshplay.sim.sites import load_config

        path = self.sim_dir / "sites.json"
        return load_config(path)["sites"] if path.exists() else {}

    @cached_property
    def app_config(self) -> dict:
        """data/mapapp/layers.json: {"layers": {id: {"enabled": bool, "settings": {...}}}}"""
        path = self.app_dir / "layers.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"layers": {}}

    @property
    def sites_path(self) -> Path:
        return self.sim_dir / "sites.json"

    def reload_sites(self) -> None:
        """Forget the cached sites after sites.json was edited."""
        self.__dict__.pop("sites", None)

    def cache_path(self, layer_id: str, key: str, suffix: str = ".json") -> Path:
        d = self.cache_dir / layer_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}{suffix}"


class Layer:
    """Base class. Subclasses set the class attributes and implement settings() and data()."""

    id = ""
    name = ""
    group = ""
    kind = "vector"  # or "raster"
    description = ""
    enabled_by_default = False
    uses_models = False  # data() touches the scene or the models: runs under ctx.model_lock

    def settings(self, ctx: Context) -> list[Setting]:
        return []

    def data(self, ctx: Context, values: dict) -> dict:
        raise NotImplementedError

    def describe(self, ctx: Context) -> dict:
        cfg = ctx.app_config.get("layers", {}).get(self.id, {})
        overrides = cfg.get("settings", {})
        settings = []
        for s in self.settings(ctx):
            if s.name in overrides:
                s.default = overrides[s.name]
            settings.append(s.to_json())
        return dict(
            id=self.id,
            name=_(self.name),
            group=_(self.group),
            kind=self.kind,
            description=_(self.description),
            enabled=cfg.get("enabled", self.enabled_by_default),
            settings=settings,
        )

    def parse_values(self, ctx: Context, query: dict[str, str]) -> dict:
        return {s.name: s.parse(query.get(s.name)) for s in self.settings(ctx)}


def feature(lon: float, lat: float, **props) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def line(coords: list[tuple[float, float]], **props) -> dict:
    """coords as (lon, lat) pairs."""
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": props,
    }


def collection(features: list[dict], legend: dict | None = None, **extra) -> dict:
    out = {"type": "FeatureCollection", "features": features, **extra}
    if legend:
        out["legend"] = legend
    return out
