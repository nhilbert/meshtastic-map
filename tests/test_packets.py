import json

from meshplay.packets import to_plain


def test_to_plain_drops_raw_and_is_json_serializable():
    packet = {
        "fromId": "!1234abcd",
        "raw": object(),
        "decoded": {
            "portnum": "NODEINFO_APP",
            "payload": b"\x01\xff",
            "user": {"id": "!5678ef90", "raw": object()},
        },
    }
    plain = to_plain(packet)
    assert "raw" not in plain
    assert "raw" not in plain["decoded"]["user"]
    assert plain["decoded"]["payload"] == "01ff"
    json.dumps(plain)
