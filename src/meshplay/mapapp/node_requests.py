"""Traceroutes and position requests from the page's node list (the owner's own clicks).

Both go to one node on the channel we heard it on. The latest result per node and kind is kept
in memory for the page: the route with the SNR of every hop in both directions, or whether a
position came back. One request per node and kind runs at a time, and a node is not asked
again sooner than it can answer, so a click too many does not add traffic to the mesh.
"""

from __future__ import annotations

import threading
import time

from meshplay.mapapp.i18n import _
from meshplay.mapapp.style import snr_color
from meshplay.probe import snr_db

TRACE_WAIT_S = 20.0  # per hop and one more, like the Python API's traceroute
TRACE_GAP_S = 30.0  # between traceroutes to one node: each one floods the mesh
POSITION_WAIT_S = 60.0
POSITION_GAP_S = 180.0  # the firmware answers a position request at most every 3 min
UNKNOWN_NODE = 0xFFFFFFFF  # a relay that does not record itself in the route
KINDS = ("traceroute", "position")


class NodeRequests:
    def __init__(self, device):
        self.device = device
        # node id -> kind -> {"state", "at", "done", ...}; state is a German code:
        # läuft, ok, keine Antwort, Fehler (translated on the page)
        self._items: dict[str, dict[str, dict]] = {}
        self._lock = threading.Lock()
        device.listeners.append(self._on_packet)

    def get(self) -> dict:
        """All results (expiring the requests that waited too long)."""
        now = time.time()
        expired = []
        with self._lock:
            for kinds in self._items.values():
                for item in kinds.values():
                    if item["state"] == "läuft" and now > item["at"] + item["wait_s"]:
                        item.update(state="keine Antwort", done=now)
                        expired.append(item.get("packet"))
            out = {n: {k: dict(v) for k, v in kinds.items()} for n, kinds in self._items.items()}
        for packet_id in expired:
            if packet_id is not None:
                self.device.forget_response(packet_id)
        return out

    def traceroute(self, node: str) -> dict:
        self._check(node, "traceroute", TRACE_GAP_S)
        item = self._start(node, "traceroute", TRACE_WAIT_S * 8)

        def on_response(p: dict) -> None:
            route = self._route(p)
            with self._lock:
                if self._items.get(node, {}).get("traceroute") is item:
                    item.update(route, done=time.time())

        try:
            packet, hop_limit = self.device.send_traceroute(
                node, self.device.node_channel(node), on_response
            )
        except Exception:
            self._drop(node, "traceroute", item)
            raise
        with self._lock:
            item.update(packet=packet, wait_s=TRACE_WAIT_S * (hop_limit + 1))
            return dict(item)

    def position(self, node: str) -> dict:
        self._check(node, "position", POSITION_GAP_S)
        item = self._start(node, "position", POSITION_WAIT_S)
        try:
            self.device.request_position(node, self.device.node_channel(node))
        except Exception:
            self._drop(node, "position", item)
            raise
        with self._lock:
            return dict(item)

    def _start(self, node: str, kind: str, wait_s: float) -> dict:
        """The running request, stored before sending: the answer can come before sendData
        returns."""
        item = {"state": "läuft", "at": time.time(), "wait_s": wait_s}
        with self._lock:
            self._items.setdefault(node, {})[kind] = item
        return item

    def _drop(self, node: str, kind: str, item: dict) -> None:
        """Forget a request that could not be sent."""
        with self._lock:
            if self._items.get(node, {}).get(kind) is item:
                del self._items[node][kind]
                if not self._items[node]:
                    del self._items[node]

    def _check(self, node: str, kind: str, gap_s: float) -> None:
        """Refuse a request while one runs or the node could not answer another one yet."""
        prev = self.get().get(node, {}).get(kind)
        if prev is None:
            return
        if prev["state"] == "läuft":
            raise ValueError(_("Für {id} läuft schon eine Anfrage.", id=node))
        # a position request without an answer may be repeated; the node may just have missed it
        if kind == "position" and prev["state"] != "ok":
            return
        wait = prev["at"] + gap_s - time.time()
        if wait > 0:
            raise ValueError(
                _(
                    "{id} erst in {s} s wieder anfragen: das schont das Netz.",
                    id=node,
                    s=round(wait),
                )
            )

    def _on_packet(self, p: dict) -> None:
        """A position from a node we asked: the answer (or its broadcast, which says the same)."""
        decoded = p.get("decoded", {})
        pos = decoded.get("position") or {}
        if decoded.get("portnum") != "POSITION_APP" or "latitude" not in pos:
            return
        node = p.get("fromId") or f"!{p.get('from', 0):08x}"
        with self._lock:
            item = self._items.get(node, {}).get("position")
            if item is not None and item["state"] == "läuft":
                item.update(state="ok", done=time.time(), lat=pos["latitude"], lon=pos["longitude"])

    def _route(self, p: dict) -> dict:
        """The traceroute reply as hops with names and SNR, there and (if known) back."""
        from meshtastic.protobuf import mesh_pb2

        decoded = p.get("decoded", {})
        if decoded.get("portnum") == "ROUTING_APP":
            return {"state": "Fehler", "error": decoded.get("routing", {}).get("errorReason")}
        r = mesh_pb2.RouteDiscovery()
        r.ParseFromString(decoded["payload"])
        me, target = p.get("to", 0), p.get("from", 0)
        towards = [(me, None)] + [
            (n, snr_db(list(r.snr_towards), i)) for i, n in enumerate([*r.route, target])
        ]
        back = None
        # without hopStart (old firmware) or the SNR list, the way back was not recorded
        if "hopStart" in p and len(r.snr_back) == len(r.route_back) + 1:
            back = [(target, None)] + [
                (n, snr_db(list(r.snr_back), i)) for i, n in enumerate([*r.route_back, me])
            ]
        try:
            nodes, _me = self.device.nodes()
        except RuntimeError:  # the connection dropped meanwhile: the route without names
            nodes = {}

        def hop(num: int, snr: float | None) -> dict:
            # color: of the leg that ends here, by the SNR it was heard with (as the node layer)
            out = {"id": None, "snr": snr, "color": snr_color(snr)}
            if num == UNKNOWN_NODE:
                return out
            out["id"] = f"!{num:08x}"
            entry = nodes.get(out["id"]) or {}
            user, pos = entry.get("user") or {}, entry.get("position") or {}
            out.update(short=user.get("shortName", ""), long=user.get("longName", ""))
            if "latitude" in pos and "longitude" in pos:  # where it was when the reply came
                out.update(lat=pos["latitude"], lon=pos["longitude"])
            return out

        return {
            "state": "ok",
            "towards": [hop(*h) for h in towards],
            "back": [hop(*h) for h in back] if back else None,
        }
