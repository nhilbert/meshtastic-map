"""Live connection to the USB Meshtastic device for the map app.

The server keeps one serial connection open in a background thread. While connected it
provides the device's node database (live node layer) and appends every received packet to
data/packets/<date>.jsonl in the same format as scripts/listen.py, so a walk can be recorded
with the map app open (only one program can hold the serial port).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from meshplay.packets import to_plain

log = logging.getLogger(__name__)


class DeviceLink:
    def __init__(self, data_dir: Path, log_packets: bool = True):
        self.data_dir = data_dir
        self.log_packets = log_packets
        self.iface = None
        self.port: str | None = None
        self.state = "getrennt"  # getrennt, verbinde, verbunden, Fehler
        self.error = ""
        self.packets = 0
        self.last_packet: float | None = None
        self._lock = threading.Lock()
        self._subscribed = False

    # ------------------------------------------------------------ connection
    def connect(self, port: str | None = None) -> None:
        """Connect in the background (downloading the node database takes a few seconds)."""
        with self._lock:
            if self.state in ("verbinde", "verbunden"):
                return
            self.state, self.error = "verbinde", ""
        threading.Thread(target=self._connect, args=(port,), daemon=True).start()

    def _connect(self, port: str | None) -> None:
        from pubsub import pub

        from meshplay.device import find_port

        try:
            from meshtastic.serial_interface import SerialInterface

            port = port or find_port()
            if not port:
                raise RuntimeError("kein Meshtastic-Gerät gefunden (MESHTASTIC_PORT in .env?)")
            if not self._subscribed:
                pub.subscribe(self._on_receive, "meshtastic.receive")
                pub.subscribe(self._on_lost, "meshtastic.connection.lost")
                self._subscribed = True
            iface = SerialInterface(devPath=port)
            with self._lock:
                self.iface, self.port, self.state = iface, port, "verbunden"
            log.info("Device connected on %s", port)
        except Exception as e:  # port busy, no device, ...
            with self._lock:
                self.state, self.error = "Fehler", f"{type(e).__name__}: {e}"
            log.warning("Device connection failed: %s", e)

    def disconnect(self) -> None:
        with self._lock:
            iface, self.iface, self.state = self.iface, None, "getrennt"
        if iface:
            try:
                iface.close()
            except Exception:
                pass

    def _on_lost(self, interface=None, **_):
        with self._lock:
            if interface is self.iface:
                self.iface, self.state, self.error = None, "Fehler", "Verbindung verloren"

    # ------------------------------------------------------------ packets
    def _on_receive(self, packet, interface=None):
        if interface is not self.iface:
            return
        self.packets += 1
        self.last_packet = time.time()
        if not self.log_packets:
            return
        now = datetime.now()
        path = self.data_dir / "packets" / f"{now:%Y-%m-%d}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"receivedAt": now.isoformat(), **to_plain(packet)}) + "\n")
        except OSError as e:
            log.warning("Could not log packet: %s", e)

    # ------------------------------------------------------------ data
    def status(self) -> dict:
        me = None
        if self.iface is not None:
            try:
                info = self.iface.getMyNodeInfo() or {}
                me = {
                    "id": info.get("user", {}).get("id"),
                    "name": info.get("user", {}).get("longName"),
                }
            except Exception:
                pass
        return dict(
            state=self.state,
            port=self.port,
            error=self.error,
            packets=self.packets,
            last_packet=self.last_packet,
            logging=self.log_packets,
            me=me,
        )

    def nodes(self) -> tuple[dict, int | None]:
        """Snapshot of the node database (plain dicts) and the own node number."""
        iface = self.iface
        if iface is None:
            raise RuntimeError("Gerät nicht verbunden")
        nodes = to_plain(dict(iface.nodes or {}))
        my_num = getattr(getattr(iface, "myInfo", None), "my_node_num", None)
        return nodes, my_num
