"""Colour scales and PNG encoding shared by the layers."""

from __future__ import annotations

import base64
import io
import math
import struct
import zlib

import numpy as np

from meshplay.mapapp.i18n import N_, _

# SNR bands (LongFast decodes down to about -17.5 dB)
SNR_BANDS = [
    (0, "#1a9850", "> 0 dB"),
    (-7, "#91cf60", "0 … −7 dB"),
    (-13, "#fc8d59", "−7 … −13 dB"),
    (-math.inf, "#d73027", "< −13 dB"),
]
GREY = "#999999"
RED = "#d73027"
DELIVERY_RAMP = ["#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850"]
RESIDUAL_RAMP = ["#b2182b", "#ef8a62", "#f7f7f7", "#67a9cf", "#2166ac"]


def snr_color(snr: float | None) -> str:
    if snr is None:
        return GREY
    return next(color for limit, color, _label in SNR_BANDS if snr > limit)


def snr_legend(title: str = N_("SNR (direkt empfangen)")) -> dict:
    """Legend of the SNR colours; title is German source text, translated here."""
    items = [[c, label] for _limit, c, label in SNR_BANDS] + [[GREY, _("über Relais")]]
    return {"title": _(title), "items": items}


def ramp_color(value: float, vmin: float, vmax: float, ramp: list[str]) -> str:
    t = float(np.clip((value - vmin) / (vmax - vmin), 0, 1)) * (len(ramp) - 1)
    i = min(int(t), len(ramp) - 2)
    f = t - i
    a, b = (np.array([int(c[k : k + 2], 16) for k in (1, 3, 5)]) for c in (ramp[i], ramp[i + 1]))
    r, g, bl = np.round(a + (b - a) * f).astype(int)
    return f"#{r:02x}{g:02x}{bl:02x}"


def ramp_rgba(grid: np.ndarray, vmin: float, vmax: float, ramp: list[str], alpha=170) -> np.ndarray:
    """Colour a 2D array; NaN cells become transparent."""
    cols = np.array([[int(c[k : k + 2], 16) for k in (1, 3, 5)] for c in ramp], float)
    t = np.clip((np.nan_to_num(grid, nan=vmin) - vmin) / (vmax - vmin), 0, 1) * (len(ramp) - 1)
    i = np.minimum(t.astype(int), len(ramp) - 2)
    f = (t - i)[..., None]
    rgb = cols[i] + (cols[i + 1] - cols[i]) * f
    a = np.where(np.isfinite(grid), alpha, 0)[..., None]
    return np.concatenate([np.round(rgb), a], axis=-1).astype(np.uint8)


def png_data_url(rgba: np.ndarray) -> str:
    """Encode an RGBA uint8 array (rows north -> south) as a PNG data URL."""
    h, w, _depth = rgba.shape
    raw = b"".join(b"\x00" + rgba[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    buf = io.BytesIO()
    buf.write(b"\x89PNG\r\n\x1a\n")
    buf.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
    buf.write(chunk(b"IDAT", zlib.compress(raw, 6)))
    buf.write(chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
