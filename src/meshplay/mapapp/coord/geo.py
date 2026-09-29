"""Bearings, compass letters and the short number formats used in radio messages."""

from __future__ import annotations

import math
import re
import time
from datetime import datetime

from meshplay.walk import distance_m

COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]  # English in every language
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DELTA_RE = re.compile(r"^\+(\d{1,4})$")


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial bearing from a to b (lat, lon), 0 … 360."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    x = math.sin(lon2 - lon1) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.degrees(math.atan2(x, y)) % 360


def compass(bearing: float) -> str:
    return COMPASS[int((bearing + 22.5) // 45) % 8]


def fmt_dist(m: float) -> str:
    """Metres rounded to 10 below a kilometre, else kilometres with one decimal."""
    if m < 995:
        return f"{max(10, round(m / 10) * 10)}m"
    return f"{m / 1000:.1f}km"


def fmt_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return f"~{max(1, round(seconds / 60))}min"


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def parse_time(text: str, now: float | None = None) -> float:
    """ "12:55" (today, local time), "+15" (minutes from now) or a timestamp -> timestamp."""
    now = time.time() if now is None else now
    text = (text or "").strip()
    m = _TIME_RE.match(text)
    if m:
        h, mi = int(m[1]), int(m[2])
        if h > 23 or mi > 59:
            raise ValueError(text)
        today = datetime.fromtimestamp(now)
        return today.replace(hour=h, minute=mi, second=0, microsecond=0).timestamp()
    m = _DELTA_RE.match(text)
    if m:
        return now + int(m[1]) * 60
    return float(text)  # ValueError for anything else


__all__ = [
    "bearing_deg",
    "compass",
    "distance_m",
    "fmt_dist",
    "fmt_eta",
    "hhmm",
    "parse_time",
]
