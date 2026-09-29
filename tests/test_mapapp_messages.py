"""Messaging pane backend: receiving, sending with delivery state, persistence (fake radio)."""

from types import SimpleNamespace

import pytest

from meshplay.mapapp.device import DeviceLink
from meshplay.mapapp.messages import MAX_TEXT_BYTES, MessageStore


class FakeIface:
    """Stores sent packets; ack() answers the last one like the firmware would."""

    def __init__(self):
        chans = [
            SimpleNamespace(index=0, role=1, settings=SimpleNamespace(name="")),
            SimpleNamespace(index=1, role=2, settings=SimpleNamespace(name="Privat")),
            SimpleNamespace(index=2, role=0, settings=SimpleNamespace(name="")),
        ]
        self.localNode = SimpleNamespace(channels=chans)
        self.nodes = {
            "!abcd1234": {
                "num": 0xABCD1234,
                "user": {"id": "!abcd1234", "shortName": "TRK", "longName": "Tracker"},
            },
        }
        self.sent = []

    def sendData(
        self,
        data,
        destinationId,
        portNum,
        wantAck,
        onResponse,
        onResponseAckPermitted,
        channelIndex,
    ):
        self.sent.append(
            dict(
                data=data,
                to=destinationId,
                channel=channelIndex,
                ack=wantAck,
                on_response=onResponse,
            )
        )
        return SimpleNamespace(id=1000 + len(self.sent))

    def getMyNodeInfo(self):
        return {"user": {"id": "!11112222", "longName": "Home"}}

    def ack(self, sender_num: int, reason: str = "NONE"):
        self.sent[-1]["on_response"](
            {"from": sender_num, "decoded": {"routing": {"errorReason": reason}}}
        )


@pytest.fixture
def dev(tmp_path):
    d = DeviceLink(tmp_path, log_packets=False)
    d.iface, d.state = FakeIface(), "verbunden"
    return d


def packet(text=None, to=0xFFFFFFFF, channel=1, port="TEXT_MESSAGE_APP"):
    decoded = {"portnum": port}
    if text:
        decoded["text"] = text
    return {
        "id": 77,
        "from": 0xABCD1234,
        "fromId": "!abcd1234",
        "to": to,
        "channel": channel,
        "rxSnr": -4.5,
        "rxRssi": -110,
        "hopStart": 3,
        "hopLimit": 2,
        "decoded": decoded,
    }


def test_received_text_and_traffic(dev):
    dev._on_receive(packet("hallo"), dev.iface)
    dev._on_receive(packet(port="POSITION_APP"), dev.iface)
    (msg,) = dev.messages.since(0)
    assert (msg["dir"], msg["from"], msg["to"], msg["channel"], msg["text"], msg["hops"]) == (
        "in",
        "!abcd1234",
        "^all",
        1,
        "hallo",
        1,
    )
    assert [p["port"] for p in dev.messages.traffic_since(0)] == [
        "TEXT_MESSAGE_APP",
        "POSITION_APP",
    ]
    assert dev.names({"!abcd1234"}) == {"!abcd1234": {"short": "TRK", "long": "Tracker"}}


def test_send_direct_and_delivery(dev):
    msg = dev.send_text("  hi  ", "!abcd1234", 0)
    sent = dev.iface.sent[-1]
    assert (sent["data"], sent["to"], sent["channel"], sent["ack"]) == (b"hi", "!abcd1234", 0, True)
    assert msg["status"] == "gesendet"
    rev = dev.messages.rev
    dev.iface.ack(0xABCD1234)  # the recipient confirms
    (changed,) = dev.messages.since(rev)
    assert changed["status"] == "zugestellt"


def test_send_channel_and_failures(dev):
    dev.send_text("an alle", "^all", 1)
    dev.iface.ack(0x11112222)  # heard a relay: implicit ack from our own node
    assert dev.messages.since(0)[-1]["status"] == "im Netz"
    dev.send_text("nochmal", "^all", 1)
    dev.iface.ack(0x11112222, "MAX_RETRANSMIT")
    assert dev.messages.since(0)[-1]["status"] == "nicht zugestellt (MAX_RETRANSMIT)"
    with pytest.raises(ValueError, match="Kanal 2"):
        dev.send_text("x", "^all", 2)  # disabled channel
    with pytest.raises(ValueError, match="höchstens"):
        dev.send_text("ä" * MAX_TEXT_BYTES, "^all", 0)  # 2 bytes per character
    with pytest.raises(ValueError, match="Leere"):
        dev.send_text("   ", "^all", 0)
    with pytest.raises(ValueError, match="Node-ID"):
        dev.send_text("x", "tracker", 0)
    dev.iface, dev.state = None, "getrennt"
    with pytest.raises(ValueError, match="nicht verbunden"):
        dev.send_text("x", "^all", 0)


def test_messages_survive_a_restart(dev, tmp_path):
    dev._on_receive(packet("erste"), dev.iface)
    dev.send_text("antwort", "!abcd1234", 0)
    dev.iface.ack(0xABCD1234)
    again = MessageStore(tmp_path / "messages.jsonl")
    assert [(m["text"], m.get("status")) for m in again.since(0)] == [
        ("erste", None),
        ("antwort", "zugestellt"),
    ]


def test_channels(dev):
    assert dev.channels() == [
        {"index": 0, "name": "", "primary": True},
        {"index": 1, "name": "Privat", "primary": False},
    ]


def test_node_list_in_nodes_layer(tmp_path):
    import json

    from meshplay.config import Settings
    from meshplay.mapapp.layers.nodes import NodesLayer
    from meshplay.mapapp.registry import Context

    ctx = Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=None))
    (tmp_path / "exports").mkdir()
    nodes = {
        "!abcd1234": {
            "num": 1,
            "user": {"id": "!abcd1234", "shortName": "A"},
            "position": {"latitude": 50.7, "longitude": 7.1},
            "hopsAway": 0,
        },
        "!00005678": {"num": 2, "user": {"id": "!00005678", "shortName": "B"}},
    }
    (tmp_path / "exports" / "nodes-1.json").write_text(json.dumps(nodes), encoding="utf-8")
    layer = NodesLayer()
    data = layer.data(ctx, layer.parse_values(ctx, {"source": "nodes-1.json"}))
    assert len(data["features"]) == 1  # only nodes with a position on the map
    assert data["features"][0]["properties"]["_node_id"] == "!abcd1234"
    assert [(n["id"], n["lat"]) for n in data["nodes"]] == [
        ("!abcd1234", 50.7),
        ("!00005678", None),
    ]
