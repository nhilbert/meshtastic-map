"""Finding and connecting to a USB-attached Meshtastic device."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from meshtastic.serial_interface import SerialInterface
from serial.tools import list_ports

from meshplay.config import load_settings

log = logging.getLogger(__name__)

# USB vendor IDs of common Meshtastic boards; Seeed (Wio Tracker L1) first.
KNOWN_VIDS = {
    0x2886: "Seeed",
    0x239A: "Adafruit / nRF52 bootloader",
    0x303A: "Espressif",
    0x1A86: "WCH CH340/CH9102",
    0x10C4: "Silicon Labs CP210x",
}


def list_serial_ports() -> list[dict]:
    """Serial ports of the system, likely Meshtastic devices first, Bluetooth last.

    kind: "known" (a USB vendor from KNOWN_VIDS), "usb" (another USB device), "bluetooth"
    (a virtual Bluetooth serial port: never a Meshtastic node on USB, and opening one can block
    for a long time) or "other".
    """
    out = []
    for p in list_ports.comports():
        hwid = (p.hwid or "").upper()
        if p.vid in KNOWN_VIDS:
            kind, vendor = "known", KNOWN_VIDS[p.vid]
        elif hwid.startswith("BTHENUM") or "BLUETOOTH" in hwid:
            kind, vendor = "bluetooth", None
        elif p.vid is not None:
            kind, vendor = "usb", p.manufacturer
        else:
            kind, vendor = "other", p.manufacturer
        out.append(
            {
                "device": p.device,
                "kind": kind,
                "vendor": vendor,
                "description": p.description,
                "vid": p.vid,
                "pid": p.pid,
            }
        )
    order = {"known": 0, "usb": 1, "other": 2, "bluetooth": 3}
    return sorted(out, key=lambda d: (order[d["kind"]], d["device"]))


def find_port(ports: list[dict] | None = None) -> str | None:
    """The port to use: MESHTASTIC_PORT if that port exists, else the first port from a known
    vendor, else the only other USB serial port. None if there is no clear choice."""
    ports = list_serial_ports() if ports is None else ports
    configured = load_settings().port
    if configured:
        if any(p["device"].lower() == configured.lower() for p in ports):
            return configured
        log.warning("MESHTASTIC_PORT=%s is not present; looking for a device", configured)
    known = [p["device"] for p in ports if p["kind"] == "known"]
    if known:
        return known[0]
    usb = [p["device"] for p in ports if p["kind"] == "usb"]
    return usb[0] if len(usb) == 1 else None


@contextmanager
def connect(port: str | None = None, **kwargs) -> Iterator[SerialInterface]:
    """Open a serial connection to the device and always close it afterwards.

    Usage:
        with connect() as iface:
            print(iface.getMyNodeInfo())
    """
    port = port or find_port()
    if port is None:
        raise RuntimeError(
            "No Meshtastic device found on USB. Plug it in (a data cable, not a charging cable) "
            "or give the port (--port, MESHTASTIC_PORT in .env)."
        )
    log.info("Connecting to %s", port)
    iface = SerialInterface(devPath=port, **kwargs)
    try:
        yield iface
    finally:
        iface.close()
