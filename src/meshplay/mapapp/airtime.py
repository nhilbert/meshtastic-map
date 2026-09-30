"""Airtime: what the app transmits and how busy the channel is (the page's "Funklast").

The app counts every packet it sends itself (texts, position requests, traceroutes) and
estimates its time on air from the modem preset; the device reports what it measures: channel
utilisation (all traffic it hears, last minute), its own transmit share of the last hour
(airUtilTx, which includes relaying other nodes' packets) and, less often, packet counters.
Answers of the recipients (acks, position replies, traceroute replies) are their airtime and
not counted here.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from meshplay.config import DEFAULT_PRESET
from meshplay.mapapp.i18n import _
from meshplay.sim.itu import MESHTASTIC_PRESETS

PREAMBLE_SYMBOLS = 16  # Meshtastic's LoRa preamble length
HEADER_B = 16  # Meshtastic packet header in front of the encrypted payload
DATA_OVERHEAD_B = 6  # the Data protobuf around the payload: port, length, flags
WINDOW_S = 3600
# Warnings. The firmware holds back its own position broadcasts above 25 % channel
# utilisation; EU868 (869.4-869.65 MHz) allows a 10 % duty cycle per hour.
CHANNEL_UTIL_WARN = 25.0
AIR_UTIL_TX_WARN = 8.0
APP_SHARE_WARN = 2.0
CONGESTION_NODES = 40  # above this many online nodes clients stretch their intervals


def time_on_air_s(payload_bytes: int, preset: str) -> float:
    """LoRa time on air of one packet (Semtech AN1200.13): explicit header, CRC on."""
    p = MESHTASTIC_PRESETS.get(preset) or MESHTASTIC_PRESETS[DEFAULT_PRESET]
    sf, bw = p["sf"], p["bw"]
    cr = int(p["cr"].split("/")[1]) - 4  # 4/5 -> 1 ... 4/8 -> 4
    t_sym = 2**sf / bw
    de = 1 if t_sym > 0.016 else 0  # low data rate optimisation
    size = payload_bytes + DATA_OVERHEAD_B + HEADER_B
    n = 8 + max(math.ceil((8 * size - 4 * sf + 28 + 16) / (4 * (sf - 2 * de))) * (cr + 4), 0)
    return (PREAMBLE_SYMBOLS + 4.25) * t_sym + n * t_sym


class SendLog:
    """The app's own transmissions of the last hour: (time, kind, payload bytes)."""

    def __init__(self):
        self._items: deque[tuple[float, str, int]] = deque()
        self._lock = threading.Lock()

    def note(self, kind: str, payload_bytes: int) -> None:
        now = time.time()
        with self._lock:
            self._items.append((now, kind, payload_bytes))
            while self._items and self._items[0][0] < now - WINDOW_S:
                self._items.popleft()

    def summary(self, preset: str) -> dict:
        since = time.time() - WINDOW_S
        with self._lock:
            items = [i for i in self._items if i[0] >= since]
        kinds: dict[str, dict] = {}
        for _t, kind, size in items:
            k = kinds.setdefault(kind, {"packets": 0, "airtime_s": 0.0})
            k["packets"] += 1
            k["airtime_s"] += time_on_air_s(size, preset)
        total = sum(k["airtime_s"] for k in kinds.values())
        for k in kinds.values():
            k["airtime_s"] = round(k["airtime_s"], 2)
        return {
            "kinds": kinds,
            "packets": len(items),
            "airtime_s": round(total, 2),
            "share_pct": round(total / WINDOW_S * 100, 3),
        }


def report(preset: str, app: dict, own: dict | None, telemetry_age_s: float | None) -> dict:
    """The panel's numbers and the warnings that apply."""
    metrics = (own or {}).get("deviceMetrics") or {}
    stats = (own or {}).get("localStats") or {}
    util = metrics.get("channelUtilization", stats.get("channelUtilization"))
    tx = metrics.get("airUtilTx", stats.get("airUtilTx"))
    device = {
        "channel_util_pct": None if util is None else round(util, 1),
        "air_util_tx_pct": None if tx is None else round(tx, 2),
        "packets_tx": stats.get("numPacketsTx"),
        "tx_relay": stats.get("numTxRelay"),
        "online_nodes": stats.get("numOnlineNodes"),
        "noise_floor_dbm": stats.get("noiseFloor"),
        "age_s": None if telemetry_age_s is None else round(telemetry_age_s),
    }
    warnings = []
    if util is not None and util >= CHANNEL_UTIL_WARN:
        warnings.append(
            _(
                "Kanal zu {pct} % belegt: ab 25 % hält die Firmware eigene Positionen zurück.",
                pct=f"{util:.0f}",
            )
        )
    if tx is not None and tx >= AIR_UTIL_TX_WARN:
        warnings.append(
            _(
                "Eigene Sendezeit {pct} % der letzten Stunde: das EU-Limit ist 10 %.",
                pct=f"{tx:.1f}",
            )
        )
    if app["share_pct"] >= APP_SHARE_WARN:
        warnings.append(
            _(
                "Die App allein hat in der letzten Stunde {pct} % der Zeit gesendet.",
                pct=f"{app['share_pct']:.1f}",
            )
        )
    notes = []
    if (device["online_nodes"] or 0) > CONGESTION_NODES:
        notes.append(
            _(
                "{n} Knoten online: Clients strecken ihre festen Intervalle (über 40 Knoten).",
                n=device["online_nodes"],
            )
        )
    return {"preset": preset, "app": app, "device": device, "warnings": warnings, "notes": notes}
