"""Named scenes: data/sim/scenes/<name>/, one of them active.

Each scene is a directory as written by Scene.save(); data/sim/scenes/active.txt names the
active one (the map app, the link tool and the scripts use it unless told otherwise). A scene
in the old single location data/sim/scene/ is moved to data/sim/scenes/default/ on first use.
The laser-scan tiles in data/sim/laz/ are shared by all scenes.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
LEGACY_NAME = "default"


def scenes_dir(sim_dir: Path) -> Path:
    return Path(sim_dir) / "scenes"


def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ValueError("scene name: 1-32 letters, digits, _ or -")
    return name


def migrate_legacy(sim_dir: Path) -> None:
    """Move data/sim/scene/ to data/sim/scenes/default/ (once); keep it if that fails."""
    old = Path(sim_dir) / "scene"
    new = scenes_dir(sim_dir) / LEGACY_NAME
    if not (old / "scene_meta.json").exists() or new.exists():
        return
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except OSError as e:  # e.g. a file held open by another program on Windows
        print(f"Could not move {old} to {new}: {e}")
        return
    if not active_name(sim_dir):
        set_active(sim_dir, LEGACY_NAME)


def names(sim_dir: Path) -> list[str]:
    """Scenes on disk, sorted by name."""
    root = scenes_dir(sim_dir)
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir() if (p / "scene_meta.json").exists() and NAME_RE.match(p.name)
    )


def active_name(sim_dir: Path) -> str | None:
    """The active scene: the one in active.txt, else the first one there is, else None."""
    have = names(sim_dir)
    path = scenes_dir(sim_dir) / "active.txt"
    name = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if name in have:
        return name
    return have[0] if have else None


def set_active(sim_dir: Path, name: str) -> None:
    if name not in names(sim_dir):
        raise KeyError(f"no scene {name}")
    (scenes_dir(sim_dir) / "active.txt").write_text(name + "\n", encoding="utf-8")


def scene_path(sim_dir: Path, name: str | None = None) -> Path:
    """Directory of the named scene, or of the active one; SystemExit-friendly message if none."""
    migrate_legacy(sim_dir)
    name = name or active_name(sim_dir)
    if not name:
        raise FileNotFoundError(
            "No laser-scan scene. Create one in the map app (layer 'Laserscan-Szene') or with "
            "scripts/sim_fetch_tiles.py and scripts/sim_build_scene.py."
        )
    path = scenes_dir(sim_dir) / check_name(name)
    if not (path / "scene_meta.json").exists():
        raise FileNotFoundError(f"No scene '{name}' in {scenes_dir(sim_dir)}")
    return path


def meta(sim_dir: Path, name: str) -> dict:
    path = scenes_dir(sim_dir) / name / "scene_meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def disk_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def delete(sim_dir: Path, name: str) -> None:
    path = scenes_dir(sim_dir) / check_name(name)
    if not (path / "scene_meta.json").exists():
        raise KeyError(f"no scene {name}")
    shutil.rmtree(path)


def bbox_around(x: float, y: float, size_m: float) -> tuple[float, float, float, float]:
    """Square of size_m around (x, y) in EPSG:25832, on whole metres."""
    r = size_m / 2
    return (round(x - r), round(y - r), round(x + r), round(y + r))


def parse_center(text: str) -> tuple[float, float]:
    """'lat, lon' -> (lat, lon)."""
    try:
        lat, lon = (float(v) for v in str(text).replace(";", ",").split(","))
    except ValueError:
        raise ValueError("centre as 'lat, lon', e.g. 50.9413, 6.9583") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or math.isnan(lat + lon):
        raise ValueError("centre as 'lat, lon', e.g. 50.9413, 6.9583")
    return lat, lon


def load_for_script(sim_dir: Path, name: str | None = None):
    """(name, Scene) of the named or active scene for a script; exits with a message if none."""
    from meshplay.sim.scene import Scene

    try:
        path = scene_path(sim_dir, name)
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e)) from None
    print(f"Scene: {path.name}")
    return path.name, Scene.load(path)
