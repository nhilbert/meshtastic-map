"""Layer base class, setting declarations and the shared app context.

A layer returns one of two payloads from data():

  vector  a GeoJSON FeatureCollection (WGS84). Per feature, optional properties drive display:
            _style     {"color", "fillColor", "radius", "weight", "opacity", "dash"}
            _title     popup/tooltip heading
            _fields    {label: value} shown in the popup
            _z         height above ground in the 3D view [m] (default 2)
            _icon      badge marker instead of a circle: {"text", "symbol" (router, tracker,
                       client, sensor, home), "color", "own", "faded", "hint"}
            _endpoint  makes the feature usable as start/end of the link tool:
                       {"height_m": [lo, hi], "clutter_m", "indoor", "device", "measured": {...}}
  raster  {"type": "raster", "image": <PNG data URL>, "bounds": [[south, west], [north, east]]}

Both may carry "legend": {"title", "items": [[color, label], ...]} or
{"title", "ramp": [colors...], "min", "max", "unit"}.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from meshplay.config import Settings, load_settings


@dataclass
class Setting:
    name: str
    label: str
    type: str  # "select", "number", "bool", "text"
    default: Any = None
    options: list | None = None  # [[value, label], ...] for select
    depends_on: str | None = None  # select whose options depend on another setting's value
    options_map: dict | None = None  # {other_value: [[value, label], ...]}
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str = ""

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

    @cached_property
    def scene(self):
        from meshplay.sim.scene import Scene

        return Scene.load(self.sim_dir / "scene")

    @property
    def has_scene(self) -> bool:
        return (self.sim_dir / "scene" / "scene_meta.json").exists()

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
            name=self.name,
            group=self.group,
            kind=self.kind,
            description=self.description,
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
