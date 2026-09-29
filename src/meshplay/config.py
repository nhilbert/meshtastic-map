"""Settings loaded from environment variables and the project's .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Modem preset of the local mesh (Mesh Rheinland); the default wherever a preset is needed.
DEFAULT_PRESET = "ShortSlow"


@dataclass(frozen=True)
class Settings:
    port: str | None
    data_dir: Path
    log_level: str
    home: tuple[float, float] | None


def _parse_latlon(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    lat, lon = (float(x) for x in value.split(","))
    return lat, lon


def load_settings() -> Settings:
    """Read settings; values already in the environment win over .env."""
    load_dotenv(PROJECT_ROOT / ".env")
    data_dir = Path(os.getenv("MESHPLAY_DATA_DIR", PROJECT_ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    return Settings(
        port=os.getenv("MESHTASTIC_PORT") or None,
        data_dir=data_dir,
        log_level=os.getenv("MESHPLAY_LOG_LEVEL", "INFO").upper(),
        home=_parse_latlon(os.getenv("MESHPLAY_HOME")),
    )
