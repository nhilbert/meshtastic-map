"""Live connection to the Meshtastic device for the map app.

The server keeps one connection open in a background thread: serial (USB), or Bluetooth for a
port "ble:<address or name>", which is only ever the owner's choice, never detected. While
connected it provides the device's node database (live node layer) and appends every received
packet to data/packets/<date>.jsonl in the same format as scripts/listen.py, so a walk can be
recorded with the map app open (only one program can hold the serial port). It also feeds the
messaging pane: received texts and recent packets go to a MessageStore, texts are sent from
here.

With a Simulation (scripts/mapapp.py --simulate) connect() builds a FakeInterface instead of
opening a port: nothing is transmitted, and the messages go to a separate store.
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

from meshplay.config import DEFAULT_PRESET
from meshplay.mapapp import airtime
from meshplay.mapapp.i18n import L, _
from meshplay.mapapp.messages import MessageStore, check_text, now
from meshplay.packets import to_plain

log = logging.getLogger(__name__)
BROADCAST = 0xFFFFFFFF  # "to" of a message for everyone on the channel
RETRY_S = 5.0  # a wanted connection that dropped or could not be made is tried again this often


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
        self.sent = airtime.SendLog()  # our own transmissions, for the airtime panel
        self._telemetry_at: float | None = None  # last device metrics of our own node
        # The owner asked for a connection: it is kept up, retried after a drop or a failure,
        # until disconnect(). lost_at: when a working connection dropped (None while it works).
        self.wanted = False
        self._want_port: str | None = None
        self.lost_at: float | None = None
        self.retries = 0
        self._busy = False  # a connection attempt is running
        self._ble: list[dict] = []  # Bluetooth devices of the last search, for the port list
        self._lock = threading.Lock()
        self._subscribed = False
        name = "messages-sim.jsonl" if simulate else "messages.jsonl"
        self.messages = MessageStore(data_dir / name)
        # Called with every received packet (plain dict) on the reader thread; must be quick.
        self.listeners: list[Callable[[dict], None]] = []

    # ------------------------------------------------------------ connection
    def connect(self, port: str | None = None) -> None:
        """Connect in the background (downloading the node database takes a few seconds) and
        keep the connection up. port: None = automatic detection."""
        with self._lock:
            self.wanted, self._want_port, self.retries = True, port, 0
            if self._busy or self.state == "verbunden":
                return
            self._busy = True
            self.state, self.error = "verbinde", ""
        threading.Thread(target=self._connect, args=(True,), daemon=True).start()

    def _connect(self, first: bool) -> None:
        """One attempt. first: the owner's own attempt, where a chosen port that is missing is
        an error; a retry falls back to automatic detection (the node may come back on another
        port after being plugged in again). A Bluetooth device keeps its address and is
        tried as it is."""
        from pubsub import pub

        from meshplay.device import find_port, is_ble, list_serial_ports

        port = self._want_port
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
                if not is_ble(port):
                    ports = list_serial_ports()
                    present = port and any(p["device"].lower() == port.lower() for p in ports)
                    if port and not present:
                        if first:
                            raise RuntimeError(L("Port {port} gibt es nicht (mehr)", port=port))
                        port = None
                    port = port or find_port(ports)
                if not port:
                    raise RuntimeError(
                        L(
                            "kein Meshtastic-Gerät an USB gefunden: einstecken (Datenkabel, "
                            "kein Ladekabel) oder den Port auswählen"
                        )
                    )
                iface = _open(port)
            with self._lock:
                self._busy = False
                if not self.wanted:  # disconnected while this attempt ran
                    _close_quietly(iface)
                    return
                self.iface, self.port, self.state = iface, port, "verbunden"
                self.error, self.lost_at, self.retries = "", None, 0
            log.info("Device connected on %s", port)
        except Exception as e:  # port busy, no device, ...
            with self._lock:
                self._busy = False
                self.state, self.error = "Fehler", f"{type(e).__name__}: {e}"
            if first or self.retries % 12 == 0:  # once a minute while retrying
                log.warning("Device connection failed: %s", e)
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        if not self.wanted or self.simulate is not None:
            return
        t = threading.Timer(RETRY_S, self._retry)
        t.daemon = True
        t.start()

    def _retry(self) -> None:
        with self._lock:
            if not self.wanted or self._busy or self.state == "verbunden":
                return
            self._busy = True
            self.retries += 1
        self._connect(False)

    def ports(self) -> dict:
        """The system's serial ports, the Bluetooth devices of the last search and the port
        automatic detection would take."""
        from meshplay.device import find_port, list_serial_ports

        ports = list_serial_ports()
        return {
            "ports": ports + self._ble,
            "auto": find_port(ports),
            "simulated": self.simulate is not None,
        }

    def scan_bluetooth(self) -> dict:
        """Search for Meshtastic devices over Bluetooth (takes 10 s); returns ports()."""
        if self.simulate is None:
            from meshplay import ble

            try:
                self._ble = ble.scan()
            except Exception as e:  # no adapter, Bluetooth switched off
                error = str(e) or type(e).__name__
                raise ValueError(_("Bluetooth-Suche nicht möglich: {error}", error=error)) from e
        return self.ports()

    def disconnect(self) -> None:
        with self._lock:
            iface, self.iface, self.state = self.iface, None, "getrennt"
            self.wanted, self.lost_at, self.error = False, None, ""
        if iface:
            _close_quietly(iface)

    def _on_lost(self, interface=None, **_):
        """The connection dropped (cable, reset, out of Bluetooth range): release the port and
        try again."""
        with self._lock:
            if interface is None or interface is not self.iface:
                return
            self.iface, self.state, self.error = None, "Fehler", L("Verbindung verloren")
            self.lost_at, self.retries = time.time(), 0
        log.warning("Device connection lost; retrying every %.0f s", RETRY_S)
        # close() joins the reader thread, which may be the one calling here
        threading.Thread(target=_close_quietly, args=(interface,), daemon=True).start()
        self._schedule_retry()

    # ------------------------------------------------------------ packets
    def _on_receive(self, packet, interface=None):
        if interface is not self.iface:
            return
        self.packets += 1
        self.last_packet = time.time()
        plain = to_plain(packet)
        if plain.get("decoded", {}).get("portnum") == "TELEMETRY_APP" and plain.get(
            "from"
        ) == getattr(getattr(interface, "myInfo", None), "my_node_num", None):
            self._telemetry_at = self.last_packet
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
            raise ValueError(_("Gerät nicht verbunden: unter „Gerät“ verbinden"))
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
            self.sent.note("coord" if tag == "coord" else "text", len(text.encode("utf-8")))
            return self.messages.add(rec)
        finally:
            stored.release()

    def request_position(self, to: str, channel: int) -> int:
        """Ask a node for its position: a position packet with want_response, which the node's
        firmware answers by itself with its current one (at most once per 3 min). The packet
        carries our own position, as the phone apps do: the firmware also takes a position
        sent over the API as its own, so an empty one could clear it. Returns the packet id."""
        from meshtastic.protobuf import mesh_pb2, portnums_pb2

        iface = self._ready(to, channel)
        own = (iface.getMyNodeInfo() or {}).get("position") or {}
        pos = mesh_pb2.Position()
        if "latitude" in own and "longitude" in own:
            pos.latitude_i = round(own["latitude"] * 1e7)
            pos.longitude_i = round(own["longitude"] * 1e7)
        sent = iface.sendData(
            pos,
            destinationId=to,
            portNum=portnums_pb2.PortNum.POSITION_APP,
            wantResponse=True,
            channelIndex=int(channel),
        )
        self.sent.note("position_request", pos.ByteSize())
        return sent.id

    def send_traceroute(
        self, to: str, channel: int, on_response: Callable[[dict], None]
    ) -> tuple[int, int]:
        """Traceroute to a node over the mesh with the device's hop limit (unlike the walk
        probes, which stay at hop limit 0). on_response gets the reply or a routing error on
        the reader thread. Returns the packet id and the hop limit used."""
        from meshtastic.protobuf import mesh_pb2, portnums_pb2

        iface = self._ready(to, channel)
        hop_limit = getattr(iface.localNode.localConfig.lora, "hop_limit", 0) or 3  # 0: default
        sent = iface.sendData(
            mesh_pb2.RouteDiscovery(),
            destinationId=to,
            portNum=portnums_pb2.PortNum.TRACEROUTE_APP,
            wantResponse=True,
            onResponse=on_response,
            channelIndex=int(channel),
            hopLimit=hop_limit,
        )
        self.sent.note("traceroute", 10)  # the request; the answer is the other nodes' airtime
        return sent.id, hop_limit

    def forget_response(self, packet_id: int) -> None:
        """Drop the handler of a request that timed out, so a late reply is ignored."""
        iface = self.iface
        if iface is not None:
            iface.responseHandlers.pop(packet_id, None)

    def node_channel(self, node: str) -> int:
        """Our channel index the node was heard on (the node database leaves out 0)."""
        iface = self.iface
        entry = (iface.nodes or {}).get(node) if iface is not None else None
        return int((entry or {}).get("channel", 0))

    def _ready(self, to: str, channel: int):
        """The interface, if a direct packet to `to` on `channel` can be sent now."""
        iface = self.iface
        if iface is None or self.state != "verbunden":
            raise ValueError(_("Gerät nicht verbunden: unter „Gerät“ verbinden"))
        if int(channel) not in {c["index"] for c in self.channels()}:
            raise ValueError(_("Kanal {n} gibt es auf dem Gerät nicht", n=int(channel)))
        if not (to.startswith("!") and len(to) == 9):
            raise ValueError(_("Empfänger: Node-ID wie !abcd1234"))
        return iface

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
            retrying=self.wanted and self.state != "verbunden",
            retry_s=RETRY_S,
            lost_at=self.lost_at,
            packets=self.packets,
            last_packet=self.last_packet,
            logging=self.log_packets,
            me=me,
            airtime=self.airtime() if self.state == "verbunden" else None,
        )

    def airtime(self) -> dict:
        """What we sent in the last hour and what the device measures (airtime.py)."""
        from meshplay.probe import modem_preset

        iface, preset, own = self.iface, DEFAULT_PRESET, None
        if iface is not None:
            try:
                preset = modem_preset(iface)
            except AttributeError:  # no config (yet)
                pass
            try:
                own = iface.getMyNodeInfo()
            except Exception as e:  # the node database is not loaded yet
                log.debug("No own node info: %s", e)
        if preset not in airtime.MESHTASTIC_PRESETS:
            preset = DEFAULT_PRESET  # "custom": estimate with the default
        age = None if self._telemetry_at is None else time.time() - self._telemetry_at
        return airtime.report(preset, self.sent.summary(preset), own, age)

    def nodes(self) -> tuple[dict, int | None]:
        """Snapshot of the node database (plain dicts) and the own node number."""
        iface = self.iface
        if iface is None:
            raise RuntimeError(_("Gerät nicht verbunden"))
        nodes = to_plain(dict(iface.nodes or {}))
        my_num = getattr(getattr(iface, "myInfo", None), "my_node_num", None)
        return nodes, my_num


def _open(port: str):
    """open_interface(), with the Bluetooth errors the owner can do something about as plain
    messages (the library's own point to its command line)."""
    from meshplay.device import BLE_PREFIX, open_interface

    try:
        return open_interface(port)
    except Exception as e:
        kind = getattr(e, "kind", None)  # of meshtastic's BLEError
        if kind == "device_not_found":
            raise RuntimeError(
                L(
                    "Bluetooth-Gerät {name} nicht gefunden: eingeschaltet, in Reichweite und "
                    "nicht mit dem Handy verbunden?",
                    name=port[len(BLE_PREFIX) :],
                )
            ) from e
        if kind == "write_error":
            raise RuntimeError(
                L(
                    "Bluetooth-Gerät nimmt keine Daten an: erst in den Bluetooth-Einstellungen "
                    "des Rechners koppeln (PIN des Geräts)"
                )
            ) from e
        raise


def _close_quietly(iface) -> None:
    try:
        iface.close()
    except Exception as e:  # the port is gone already
        log.debug("Closing the old connection: %s", e)
