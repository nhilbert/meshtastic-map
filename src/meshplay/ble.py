"""Bluetooth (BLE) connection to a Meshtastic device.

Kept apart from device.py so that the Bluetooth stack (bleak) is loaded only when a Bluetooth
device is asked for: device.open_interface() comes here for ports written
"ble:<address or name>".
"""

from __future__ import annotations

import logging
import threading
from contextlib import suppress

from meshtastic.ble_interface import BLEClient, BLEInterface

from meshplay.device import BLE_PREFIX

log = logging.getLogger(__name__)


class Interface(BLEInterface):
    """meshtastic's BLEInterface with a close that cannot hang.

    The library closes the interface from Bluetooth's own event thread when the link ends, and
    bleak reports that end also during the library's own close(): the nested close then waits
    for the thread it runs on. Here a link that dropped is closed on a thread of its own and a
    wanted close is not answered at all. An attempt that fails halfway also gives back its
    receive thread and the link, instead of leaving them behind at every retry.
    """

    def __init__(self, address: str, **kwargs):
        self._closing = False
        self._exit_handler = None  # close() unregisters it, also before the library has set it
        try:
            super().__init__(address, **kwargs)
        except BaseException:
            with suppress(Exception):
                self.close()
            raise

    def connect(self, address: str | None = None) -> BLEClient:
        device = self.find_device(address)  # the library's 10 s scan
        client = BLEClient(device.address, disconnected_callback=self._on_link_closed)
        try:
            client.connect()
        except BaseException:
            client.close()
            raise
        return client

    def _on_link_closed(self, _client) -> None:
        """Called on Bluetooth's event thread, which must not be kept waiting."""
        if not self._closing:
            log.info("Bluetooth link closed by the device")
            threading.Thread(target=self.close, name="BLEClose", daemon=True).start()

    def close(self) -> None:
        self._closing = True
        super().close()


def refused_unpaired(error: BaseException | None) -> bool:
    """Whether an attempt failed because the node only talks to a paired computer: one of the
    GATT errors "insufficient authentication / authorization / encryption", possibly behind
    the library's own error. By their text, which every bleak version carries."""
    marks = ("insufficient authentication", "insufficient authorization", "insufficient encr")
    while error is not None:
        if any(m in str(error).lower() for m in marks):
            return True
        error = error.__cause__ or error.__context__
    return False


def scan() -> list[dict]:
    """Meshtastic devices advertising over Bluetooth right now, as port entries like those of
    device.list_serial_ports(). Takes 10 s. A device that is connected to a phone does not
    advertise and is not found."""
    found = [
        {
            "device": BLE_PREFIX + d.address,
            "kind": "ble",
            "vendor": None,
            "description": d.name or d.address,
            "vid": None,
            "pid": None,
        }
        for d in BLEInterface.scan()
    ]
    return sorted(found, key=lambda p: p["description"])
