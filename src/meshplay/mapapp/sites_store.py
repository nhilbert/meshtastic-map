"""Editing the own sites in data/sim/sites.json from the map app.

Add, move, edit, rename and delete. The file also holds scenarios and the corridor, which refer
to sites by name: a rename updates those references, a delete is refused while any remain. Each
write keeps the previous file as sites.json.bak.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

from meshplay.mapapp.i18n import _

# Names end up in scenario references and file names (coverage-<site>-...): keep them simple.
NAME_RE = re.compile(r"^[A-Za-z0-9ÄÖÜäöüß_-]{1,24}$")


def load_raw(path: Path) -> dict:
    """The file as written (no derived fields); an empty config if it doesn't exist yet."""
    if not path.exists():
        return {"sites": {}}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("sites", {})
    return cfg


def save_raw(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_suffix(".json.bak"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def references(cfg: dict, name: str) -> list[str]:
    """Everything in the config that refers to site `name`, as readable labels."""
    refs = [
        _("Variante {name}", name=n) for n, s in cfg["sites"].items() if s.get("same_as") == name
    ]
    for sc in cfg.get("scenarios", []):
        if name in (sc.get("a"), sc.get("b")):
            refs.append(_("Szenario {id}", id=sc.get("id", "?")))
    corridor = cfg.get("corridor") or {}
    if name in (corridor.get("from"), corridor.get("towards")):
        refs.append(_("Korridor"))
    return refs


def _check_name(cfg: dict, name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError(_("Name: 1–24 Zeichen, nur Buchstaben, Ziffern, _ und -"))
    if name in cfg["sites"]:
        raise ValueError(_("Einen Standort „{name}“ gibt es schon", name=name))
    return name


def _clean_fields(fields: dict, variant: bool) -> dict:
    """Validated subset of the editable fields."""
    out = {}
    if "lat" in fields or "lon" in fields:
        if variant:
            raise ValueError(_("Eine Variante hat die Position ihres Standorts"))
        lat, lon = float(fields["lat"]), float(fields["lon"])
        if not (
            math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
        ):
            raise ValueError(_("Ungültige Position"))
        out["lat"], out["lon"] = round(lat, 7), round(lon, 7)
    if "height_m" in fields:
        lo, hi = (float(v) for v in fields["height_m"])
        if not (0 <= lo <= hi <= 300):
            raise ValueError(_("Antennenhöhe: 0 ≤ min ≤ max ≤ 300 m"))
        out["height_m"] = [lo, hi]
    if "clutter_m" in fields:
        if variant:
            raise ValueError(_("Eine Variante hat die Umgebung ihres Standorts"))
        clutter = float(fields["clutter_m"])
        if not 0 <= clutter <= 150:
            raise ValueError(_("Umgebung (Clutter): 0 bis 150 m"))
        out["clutter_m"] = round(clutter, 1)
    if "description" in fields:
        out["description"] = str(fields["description"]).strip()[:200]
    return out


def add_site(
    path: Path, name: str, lat: float, lon: float, height_m, clutter_m: float, description: str = ""
) -> dict:
    cfg = load_raw(path)
    name = _check_name(cfg, name)
    fields = dict(lat=lat, lon=lon, height_m=height_m, clutter_m=clutter_m, description=description)
    site = _clean_fields(fields, variant=False)
    cfg["sites"][name] = {
        k: site[k] for k in ("lon", "lat", "height_m", "clutter_m", "description")
    }
    save_raw(path, cfg)
    return cfg


def update_site(path: Path, name: str, fields: dict) -> dict:
    cfg = load_raw(path)
    if name not in cfg["sites"]:
        raise KeyError(_("Standort „{name}“ gibt es nicht", name=name))
    site = cfg["sites"][name]
    site.update(_clean_fields(fields, variant=bool(site.get("same_as"))))
    save_raw(path, cfg)
    return cfg


def rename_site(path: Path, old: str, new: str) -> dict:
    cfg = load_raw(path)
    if old not in cfg["sites"]:
        raise KeyError(_("Standort „{name}“ gibt es nicht", name=old))
    new = _check_name(cfg, new)
    # keep the order of the sites (the first one is the default home)
    cfg["sites"] = {(new if k == old else k): v for k, v in cfg["sites"].items()}
    for s in cfg["sites"].values():
        if s.get("same_as") == old:
            s["same_as"] = new
    for sc in cfg.get("scenarios", []):
        for key in ("a", "b"):
            if sc.get(key) == old:
                sc[key] = new
    corridor = cfg.get("corridor") or {}
    for key in ("from", "towards"):
        if corridor.get(key) == old:
            corridor[key] = new
    save_raw(path, cfg)
    return cfg


def delete_site(path: Path, name: str) -> dict:
    cfg = load_raw(path)
    if name not in cfg["sites"]:
        raise KeyError(_("Standort „{name}“ gibt es nicht", name=name))
    refs = references(cfg, name)
    if refs:
        raise ValueError(_("{name} wird noch verwendet: {refs}", name=name, refs=", ".join(refs)))
    del cfg["sites"][name]
    save_raw(path, cfg)
    return cfg


def site_list(cfg: dict) -> list[dict]:
    """Sites for the page's editor, in file order, with what refers to each."""
    return [
        {
            "name": n,
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "height_m": s.get("height_m"),
            "clutter_m": s.get("clutter_m"),
            "description": s.get("description", ""),
            "same_as": s.get("same_as"),
            "refs": references(cfg, n),
        }
        for n, s in cfg["sites"].items()
    ]
