"""Live connection to the USB Meshtastic device for the map app.

The server keeps one serial connection open in a background thread. While connected it
provides the device's node database (live node layer) and appends every received packet to
data/packets/<date>.jsonl in the same format as scripts/listen.py, so a walk can be recorded
with the map app open (only one program can hold the serial port). It also feeds the messaging
pane: received texts and recent packets go to a MessageStore, texts are sent from here.

With a Simulation (scripts/mapapp.py --simulate) connect() builds a FakeInterface instead of
opening a serial port: nothing is transmitted, and the messages go to a separate store.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meshplay.mapapp.i18n import L, _
from meshplay.mapapp.messages import MessageStore, check_text, now
from meshplay.packets import to_plain

log = logging.getLogger(__name__)
BROADCAST = 0xFFFFFFFF  # "to" of a message for everyone on the channel


@dataclass(frozen=True)
class Simulation:
    """Settings of the simulated radio: where the fake tracker starts and what it walks."""

    home: tuple[float, float]
    track: Path | None = None
    speed: float = 1.0


class DeviceLink:
    def __init__(
        self, data_dir: Path, log_packets: bool = True, simulate: Simulation | None = None
    ):
        self.data_dir = data_dir
        self.simulate = simulate
        self.log_packets = log_packets and simulate is None  # fake packets stay out of the logs
        self.iface = None
        self.port: str | None = None
        self.state = "getrennt"  # getrennt, verbinde, verbunden, Fehler
        self.error = ""
        self.packets = 0
        self.last_packet: float | None = None
        self._lock = threading.Lock()
        self._subscribed = False
        name = "messages-sim.jsonl" if simulate else "messages.jsonl"
        self.messages = MessageStore(data_dir / name)
        # Called with every received packet (plain dict) on the reader thread; must be quick.
        self.listeners: list[Callable[[dict], None]] = []

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
            if not self._subscribed:
                pub.subscribe(self._on_receive, "meshtastic.receive")
                pub.subscribe(self._on_lost, "meshtastic.connection.lost")
                self._subscribed = True
            if self.simulate is not None:
                from meshplay.mapapp.fake_device import FakeInterface, load_track

                sim = self.simulate
                port = "sim"
                iface = FakeInterface(sim.home, load_track(sim.track), sim.speed)
            else:
                from meshtastic.serial_interface import SerialInterface

                port = port or find_port()
                if not port:
                    raise RuntimeError(
                        L("kein Meshtastic-Gerät gefunden (MESHTASTIC_PORT in .env?)")
                    )
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
                self.iface, self.state, self.error = None, "Fehler", L("Verbindung verloren")

    # ------------------------------------------------------------ packets
    def _on_receive(self, packet, interface=None):
        if interface is not self.iface:
            return
        self.packets += 1
        self.last_packet = time.time()
        plain = to_plain(packet)
        try:
            self._record(plain)
        except Exception as e:  # a malformed packet must not stop the logging below
            log.warning("Could not record packet for the messaging pane: %s", e)
        for listener in list(self.listeners):
            try:
                listener(plain)
            except Exception as e:  # one listener's bug must not stop the others or the log
                log.warning("Packet listener failed: %s", e)
        if not self.log_packets:
            return
        now = datetime.now()
        path = self.data_dir / "packets" / f"{now:%Y-%m-%d}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"receivedAt": now.isoformat(), **plain}) + "\n")
        except OSError as e:
            log.warning("Could not log packet: %s", e)

    # ------------------------------------------------------------ messages
    def _record(self, p: dict) -> None:
        """Traffic line for every packet; received texts also go to the conversation."""
        decoded = p.get("decoded", {})
        sender = p.get("fromId") or f"!{p.get('from', 0):08x}"
        dest = p.get("to", BROADCAST)
        hop_start, hop_limit = p.get("hopStart"), p.get("hopLimit") or 0
        base = {
            "time": now(),
            "from": sender,
            "to": p.get("toId") or ("^all" if dest == BROADCAST else f"!{dest:08x}"),
            "channel": p.get("channel", 0),
            "snr": p.get("rxSnr"),
            "rssi": p.get("rxRssi"),
            "hops": hop_start - hop_limit if hop_start is not None else None,
        }
        port = decoded.get("portnum", "ENCRYPTED")  # no key for it: shown as "verschlüsselt"
        self.messages.add_traffic({**base, "port": port, "text": decoded.get("text")})
        if port == "TEXT_MESSAGE_APP" and decoded.get("text"):
            self.messages.add({**base, "id": p.get("id"), "dir": "in", "text": decoded["text"]})

    def send_text(
        self,
        text: str,
        to: str,
        channel: int,
        on_status: Callable[[str], None] | None = None,
        tag: str | None = None,
    ) -> dict:
        """Send a text to a node ("!abcd1234") or to everyone on a channel ("^all").

        Asks for an acknowledgement: a direct message becomes "zugestellt" when the recipient
        confirms, a channel message "im Netz" when another node was heard relaying it.
        on_status gets the final state; tag marks the stored message (e.g. "coord").
        """
        from meshtastic.protobuf import portnums_pb2

        text = check_text(text)
        iface = self.iface
        if iface is None or self.state != "verbunden":
            raise ValueError(_("Gerät nicht verbunden: oben unter „Gerät (USB)“ verbinden"))
        channel = int(channel)
        if channel not in {c["index"] for c in self.channels()}:
            raise ValueError(_("Kanal {n} gibt es auf dem Gerät nicht", n=channel))
        direct = to not in ("", "^all", None)
        if direct and not (to.startswith("!") and len(to) == 9):
            raise ValueError(_("Empfänger: Node-ID wie !abcd1234"))
        dest_num = int(to[1:], 16) if direct else None
        # The acknowledgement can arrive on the reader thread before sendData returns; it
        # waits for this lock, which is released once the message is stored.
        stored = threading.Lock()
        stored.acquire()
        sent = None

        def on_ack(resp: dict) -> None:
            routing = resp.get("decoded", {}).get("routing", {})
            reason = routing.get("errorReason", "NONE")
            if reason != "NONE":
                status = f"nicht zugestellt ({reason})"
            elif direct and resp.get("from") == dest_num:
                status = "zugestellt"
            else:
                status = "im Netz"
            with stored:
                if sent is not None:
                    self.messages.set_status(sent.id, status)
            if on_status is not None:
                on_status(status)

        try:
            sent = iface.sendData(
                text.encode("utf-8"),
                destinationId=to if direct else "^all",
                portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP,
                wantAck=True,
                onResponse=on_ack,
                onResponseAckPermitted=True,
                channelIndex=channel,
            )
            me = self.status().get("me") or {}
            rec = {
                "id": sent.id,
                "time": now(),
                "dir": "out",
                "from": me.get("id"),
                "to": to if direct else "^all",
                "channel": channel,
                "text": text,
                "status": "gesendet",
            }
            if tag:
                rec["tag"] = tag
            return self.messages.add(rec)
        finally:
            stored.release()

    def names(self, ids) -> dict:
        """Short and long names of the given node IDs from the device's node list."""
        iface = self.iface
        if iface is None:
            return {}
        out = {}
        for n in list((iface.nodes or {}).values()):
            u = n.get("user") or {}
            if u.get("id") in ids:
                out[u["id"]] = {"short": u.get("shortName", ""), "long": u.get("longName", "")}
        return out

    def channels(self) -> list[dict]:
        """Active channels of the device: index, name, primary or secondary."""
        iface = self.iface
        if iface is None:
            return []
        out = []
        for c in iface.localNode.channels or []:
            if not c.role:  # DISABLED
                continue
            out.append({"index": c.index, "name": c.settings.name, "primary": c.role == 1})
        return out

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
            error=str(self.error),
            packets=self.packets,
            last_packet=self.last_packet,
            logging=self.log_packets,
            me=me,
        )

    def nodes(self) -> tuple[dict, int | None]:
        """Snapshot of the node database (plain dicts) and the own node number."""
        iface = self.iface
        if iface is None:
            raise RuntimeError(_("Gerät nicht verbunden"))
        nodes = to_plain(dict(iface.nodes or {}))
        my_num = getattr(getattr(iface, "myInfo", None), "my_node_num", None)
        return nodes, my_num
