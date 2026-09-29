"""A simulated radio for the map app (scripts/mapapp.py --simulate): nothing is transmitted.

FakeInterface stands in for meshtastic's SerialInterface: it has a node database with a fake
tracker and a fake client, acknowledges every sent packet after a moment, answers traceroutes,
and publishes packets on the same pubsub topic as the real interface, so DeviceLink, the
messaging pane, the node layer and the coordination mode work unchanged. The tracker walks a
GPX track (scripts/mapapp.py --simulate walk.gpx) or stays put near home.

A direct message to the tracker whose text starts with ">" is spoken by the tracker instead:
">?" arrives as "?" from the tracker, which is how commands are tried without a second device.
"""

from __future__ import annotations

import math
import random
import threading
import time
from pathlib import Path
from types import SimpleNamespace

HOME_NUM = 0xFA4E0000
TRACKER_NUM = 0xFA4E0001
CLIENT_NUM = 0xFA4E0002
BROADCAST = 0xFFFFFFFF
ACK_DELAY_S = 0.5
IDLE_INTERVAL_S = 30.0  # position broadcasts of a tracker that is not walking a track


def node_id(num: int) -> str:
    return f"!{num:08x}"


def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    return lat + north_m / 111_320, lon + east_m / (111_320 * math.cos(math.radians(lat)))


class FakeInterface:
    def __init__(
        self,
        home: tuple[float, float],
        track: list[dict] | None = None,
        speed: float = 1.0,
        idle_interval_s: float = IDLE_INTERVAL_S,
    ):
        self.home = home
        self.track = track or []
        self.speed = max(speed, 0.01)
        self.idle_interval_s = idle_interval_s
        self.sent: list[dict] = []
        self.responseHandlers: dict = {}
        self._next_id = 1000
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._timers: list[threading.Timer] = []
        self.myInfo = SimpleNamespace(my_node_num=HOME_NUM)
        from meshtastic.protobuf import config_pb2

        lora = SimpleNamespace(
            use_preset=True,
            modem_preset=config_pb2.Config.LoRaConfig.ModemPreset.Value("SHORT_SLOW"),
        )
        self.localNode = SimpleNamespace(
            channels=[
                SimpleNamespace(index=0, role=1, settings=SimpleNamespace(name="")),
                SimpleNamespace(index=1, role=2, settings=SimpleNamespace(name="Privat")),
            ],
            localConfig=SimpleNamespace(lora=lora),
        )
        start = self.track[0] if self.track else None
        lat, lon = (start["lat"], start["lon"]) if start else _offset(*home, 250, 150)
        self.nodes = {
            node_id(HOME_NUM): self._node(HOME_NUM, "HOME", "SIM home", "HELTEC_V3", "CLIENT", 0),
            node_id(TRACKER_NUM): self._node(
                TRACKER_NUM, "SIM", "SIM tracker", "TRACKER_T1000_E", "TRACKER", 0, True
            ),
            node_id(CLIENT_NUM): self._node(
                CLIENT_NUM, "CLI", "SIM client", "HELTEC_V3", "CLIENT", 1
            ),
        }
        self.nodes[node_id(HOME_NUM)]["position"] = {"latitude": home[0], "longitude": home[1]}
        self.nodes[node_id(CLIENT_NUM)]["position"] = dict(
            zip(("latitude", "longitude"), _offset(*home, -400, 300), strict=True)
        )
        self._set_tracker(lat, lon)
        self._thread = threading.Thread(target=self._walk, daemon=True)
        self._thread.start()

    @staticmethod
    def _node(num, short, long, hw, role, hops, favorite=False) -> dict:
        return {
            "num": num,
            "user": {
                "id": node_id(num),
                "shortName": short,
                "longName": long,
                "hwModel": hw,
                "role": role,
            },
            "hopsAway": hops,
            "snr": 6.0,
            "lastHeard": int(time.time()),
            "deviceMetrics": {"batteryLevel": 87},
            "isFavorite": favorite,
        }

    # ------------------------------------------------------------ SerialInterface API
    def getMyNodeInfo(self) -> dict:
        return self.nodes[node_id(HOME_NUM)]

    def sendData(
        self,
        data,
        destinationId="^all",
        portNum=None,
        wantAck=False,
        wantResponse=False,
        onResponse=None,
        onResponseAckPermitted=False,
        channelIndex=0,
        hopLimit=None,
    ):
        from meshtastic.protobuf import portnums_pb2

        with self._lock:
            self._next_id += 1
            packet_id = self._next_id
        direct = destinationId not in ("^all", BROADCAST, None)
        dest = int(destinationId[1:], 16) if isinstance(destinationId, str) and direct else None
        if not isinstance(data, bytes):  # a protobuf (traceroute) message
            data = data.SerializeToString()
        self.sent.append(
            dict(id=packet_id, data=data, to=destinationId, port=portNum, channel=channelIndex)
        )
        if onResponse is not None:
            self.responseHandlers[packet_id] = onResponse
            if portNum == portnums_pb2.PortNum.TRACEROUTE_APP and wantResponse:
                self._later(1.0, self._traceroute_reply, packet_id, dest)
            elif wantAck:
                ack = {
                    "from": dest if direct else HOME_NUM,
                    "decoded": {"routing": {"errorReason": "NONE"}},
                }
                self._later(ACK_DELAY_S, self._respond, packet_id, ack)
        if portNum == portnums_pb2.PortNum.TEXT_MESSAGE_APP and dest == TRACKER_NUM:
            text = data.decode("utf-8", errors="replace")
            if text.startswith(">"):
                self._later(1.0, self.receive_text, text[1:].strip(), TRACKER_NUM)
        return SimpleNamespace(id=packet_id)

    def close(self) -> None:
        self._stop.set()
        for t in self._timers:
            t.cancel()

    # ------------------------------------------------------------ simulated traffic
    def receive_text(self, text: str, sender: int = TRACKER_NUM, to: int = HOME_NUM) -> None:
        """A text from a fake node arrives (direct to us by default)."""
        self._publish(sender, to, 0, {"portnum": "TEXT_MESSAGE_APP", "text": text})

    def position_packet(self, lat: float, lon: float, speed_ms: float | None = None) -> None:
        """The tracker reports a position: node database and a POSITION_APP packet."""
        self._set_tracker(lat, lon)
        pos = {
            "latitude": lat,
            "longitude": lon,
            "time": int(time.time()),
            "precisionBits": 32,
        }
        if speed_ms is not None:
            pos["groundSpeed"] = int(round(speed_ms))
        self._publish(TRACKER_NUM, BROADCAST, 1, {"portnum": "POSITION_APP", "position": pos})

    def _set_tracker(self, lat: float, lon: float) -> None:
        node = self.nodes[node_id(TRACKER_NUM)]
        node["position"] = {"latitude": lat, "longitude": lon, "precisionBits": 32}
        node["lastHeard"] = int(time.time())
        node["snr"] = self._snr(lat, lon)

    def _snr(self, lat: float, lon: float) -> float:
        """Worse with distance from home, with some noise: a plausible-looking link."""
        d = math.hypot((lat - self.home[0]) * 111_320, (lon - self.home[1]) * 70_000)
        return round(max(-18.0, min(10.0, 9 - d / 90 + random.uniform(-2, 2))), 2)

    def _publish(self, sender: int, to: int, channel: int, decoded: dict) -> None:
        from pubsub import pub

        with self._lock:
            self._next_id += 1
            packet_id = self._next_id
        pos = (self.nodes[node_id(sender)].get("position") or {}) if sender != HOME_NUM else {}
        snr = self._snr(pos["latitude"], pos["longitude"]) if pos else 5.0
        packet = {
            "id": packet_id,
            "from": sender,
            "fromId": node_id(sender),
            "to": to,
            "toId": "^all" if to == BROADCAST else node_id(to),
            "channel": channel,
            "rxTime": int(time.time()),
            "rxSnr": snr,
            "rxRssi": int(-95 + snr * 1.5),
            "hopStart": 3,
            "hopLimit": 3,
            "decoded": decoded,
        }
        pub.sendMessage("meshtastic.receive", packet=packet, interface=self)

    def _respond(self, packet_id: int, response: dict) -> None:
        handler = self.responseHandlers.pop(packet_id, None)
        if handler is not None:
            handler(response)

    def _traceroute_reply(self, packet_id: int, dest: int | None) -> None:
        from meshtastic.protobuf import mesh_pb2

        node = self.nodes.get(node_id(dest or 0))
        if node is None:
            return
        pos = node.get("position") or {}
        snr = self._snr(pos["latitude"], pos["longitude"]) if pos else 5.0
        route = mesh_pb2.RouteDiscovery()
        route.snr_towards.append(int(snr * 4))
        self._respond(
            packet_id,
            {
                "from": dest,
                "rxSnr": self._snr(pos["latitude"], pos["longitude"]) if pos else 5.0,
                "rxRssi": -100,
                "hopStart": 0,
                "decoded": {"portnum": "TRACEROUTE_APP", "payload": route.SerializeToString()},
            },
        )

    def _later(self, delay: float, fn, *args) -> None:
        t = threading.Timer(delay, fn, args)
        t.daemon = True
        self._timers.append(t)
        self._timers = [x for x in self._timers if x.is_alive() or x is t]
        t.start()

    def _walk(self) -> None:
        """Replay the track at its own pace times `speed`, or idle near home."""
        if not self.track:
            while not self._stop.wait(self.idle_interval_s):
                pos = self.nodes[node_id(TRACKER_NUM)]["position"]
                self.position_packet(pos["latitude"], pos["longitude"], 0.0)
            return
        t0, start = self.track[0]["time"], time.monotonic()
        prev = None
        for p in self.track:
            due = start + (p["time"] - t0).total_seconds() / self.speed
            if self._stop.wait(max(0.0, due - time.monotonic())):
                return
            speed = None
            if prev is not None:
                dt = (p["time"] - prev["time"]).total_seconds()
                if dt > 0:
                    d = math.hypot(
                        (p["lat"] - prev["lat"]) * 111_320,
                        (p["lon"] - prev["lon"]) * 111_320 * math.cos(math.radians(p["lat"])),
                    )
                    speed = d / dt
            self.position_packet(p["lat"], p["lon"], speed)
            prev = p


def load_track(path: Path | None) -> list[dict]:
    """The GPX track for the fake tracker (replayed by its relative times)."""
    if path is None:
        return []
    from meshplay.walk import load_gpx

    track = load_gpx(path)
    if not track:
        raise ValueError(f"{path}: no track points with a timestamp")
    return track
