"""Traceroute probes from the home node to a walking node (scripts/probe_walk.py, map app task).

Each probe is a direct traceroute (hop limit 0) on one channel, so no other node relays it. A
record per probe goes to data/probes/<date>.jsonl: ok/timeout, round-trip time, the SNR in both
directions, the probe interval and the home node's modem preset.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from meshtastic.protobuf import config_pb2, mesh_pb2, portnums_pb2

UNKNOWN_SNR = -128  # firmware marker for "no SNR recorded"


def snr_db(values: list[int], index: int) -> float | None:
    """SNR entry from a RouteDiscovery list (stored as dB * 4), or None."""
    if len(values) > index and values[index] != UNKNOWN_SNR:
        return values[index] / 4
    return None


def modem_preset(iface) -> str:
    """Home node's modem preset as used in the simulation (e.g. ShortSlow), or "custom"."""
    lora = iface.localNode.localConfig.lora
    if not lora.use_preset:
        return "custom"
    name = config_pb2.Config.LoRaConfig.ModemPreset.Name(lora.modem_preset)
    return "".join(w.capitalize() for w in name.split("_"))


def probe_once(iface, to: str, channel: int, timeout: float) -> dict:
    """Send one direct traceroute and wait for the reply; returns the result fields."""
    reply: dict = {}
    done = threading.Event()

    def on_response(p):
        reply["packet"] = p
        done.set()

    started = time.monotonic()
    sent = iface.sendData(
        mesh_pb2.RouteDiscovery(),
        destinationId=to,
        portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
        wantResponse=True,
        onResponse=on_response,
        channelIndex=channel,
        hopLimit=0,
    )
    done.wait(timeout)
    # The handler stays registered after a timeout; drop it so a late reply is ignored.
    iface.responseHandlers.pop(sent.id, None)

    p = reply.get("packet")
    if p is None:
        return {"result": "timeout"}
    if p["decoded"].get("portnum") == "ROUTING_APP":
        return {"result": p["decoded"]["routing"].get("errorReason", "ERROR")}
    route = mesh_pb2.RouteDiscovery()
    route.ParseFromString(p["decoded"]["payload"])
    return {
        "result": "ok",
        "rttS": round(time.monotonic() - started, 2),
        # snrTowards[-1]: how the walking node heard us; rxSnr: how we heard it.
        "snrTowards": snr_db(list(route.snr_towards), len(route.route)),
        "snrBack": p.get("rxSnr"),
        "rssiBack": p.get("rxRssi"),
        "relays": len(route.route) + len(route.route_back),
        "hopStart": p.get("hopStart"),
    }


def append_record(data_dir: Path, record: dict) -> None:
    path = data_dir / "probes" / f"{datetime.now():%Y-%m-%d}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def run_probes(
    iface,
    data_dir: Path,
    to: str,
    channel: int = 1,
    interval: float = 60,
    timeout: float = 20,
    count: int | None = None,
    stop: threading.Event | None = None,
    on_record: Callable[[dict], None] | None = None,
) -> tuple[int, int]:
    """Probe `to` every `interval` s until `count` probes are sent or `stop` is set.

    Every record is appended to data/probes/<date>.jsonl and passed to on_record.
    Returns (answered, sent).
    """
    stop = stop or threading.Event()
    preset = modem_preset(iface)
    ok = total = 0
    next_at = time.monotonic()
    while not stop.is_set() and (count is None or total < count):
        sent_at = datetime.now()
        record = {
            "sentAt": sent_at.isoformat(),
            "to": to,
            "channel": channel,
            "interval": interval,
            "preset": preset,
            **probe_once(iface, to, channel, timeout),
        }
        total += 1
        ok += record["result"] == "ok"
        append_record(data_dir, record)
        if on_record:
            on_record(record)
        next_at += interval
        stop.wait(max(0.0, next_at - time.monotonic()))
    return ok, total


def describe(record: dict) -> str:
    """One line per probe for logs: time, result and, if answered, both SNRs."""
    line = f"{record['sentAt'][11:19]} {record['result']:<8}"
    if record["result"] == "ok":
        line += (
            f"  hin {record['snrTowards']} dB, zurück {record['snrBack']} dB, {record['rttS']} s"
        )
    return line
