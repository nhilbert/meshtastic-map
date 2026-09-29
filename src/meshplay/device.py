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


def find_port() -> str | None:
    """Return the configured port, or the first serial port from a known vendor."""
    configured = load_settings().port
    if configured:
        return configured
    for p in list_ports.comports():
        if p.vid in KNOWN_VIDS:
            log.debug("Found %s device on %s", KNOWN_VIDS[p.vid], p.device)
            return p.device
    return None


@contextmanager
def connect(port: str | None = None, **kwargs) -> Iterator[SerialInterface]:
    """Open a serial connection to the device and always close it afterwards.

    Usage:
        with connect() as iface:
            print(iface.getMyNodeInfo())
    """
    port = port or find_port()
    if port is None:
        raise RuntimeError("No Meshtastic device found. Plug it in or set MESHTASTIC_PORT in .env.")
    log.info("Connecting to %s", port)
    iface = SerialInterface(devPath=port, **kwargs)
    try:
        yield iface
    finally:
        iface.close()
