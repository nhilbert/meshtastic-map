"""Airtime panel: time on air, the app's send log, the device's numbers and warnings."""

import pytest

from meshplay.mapapp import airtime
from meshplay.mapapp.airtime import SendLog, report, time_on_air_s


def test_time_on_air_follows_the_preset():
    short = time_on_air_s(50, "ShortSlow")
    assert 0.1 < short < 0.15  # SF8 / 250 kHz: about 120 ms for a 72-byte packet
    assert time_on_air_s(50, "LongFast") > 6 * short  # SF11: eight times the symbol time
    assert time_on_air_s(200, "ShortSlow") > 2 * short
    assert time_on_air_s(50, "custom") == short  # unknown: the default preset


def test_send_log_counts_the_last_hour(monkeypatch):
    log, now = SendLog(), [1000.0]
    monkeypatch.setattr(airtime.time, "time", lambda: now[0])
    log.note("coord", 40)
    log.note("coord", 60)
    log.note("position_request", 12)
    s = log.summary("ShortSlow")
    assert s["packets"] == 3 and s["kinds"]["coord"]["packets"] == 2
    assert s["share_pct"] == pytest.approx(s["airtime_s"] / 36, rel=0.02)
    now[0] += 3601
    log.note("text", 5)  # the old ones fall out of the window
    assert log.summary("ShortSlow")["kinds"].keys() == {"text"}


def test_report_warns_about_a_busy_channel():
    app = {"kinds": {}, "packets": 0, "airtime_s": 0.0, "share_pct": 0.0}
    quiet = {"deviceMetrics": {"channelUtilization": 3.2, "airUtilTx": 0.5}}
    r = report("ShortSlow", app, quiet, 20)
    assert r["warnings"] == [] and r["device"]["channel_util_pct"] == 3.2
    busy = {
        "deviceMetrics": {"channelUtilization": 31.0, "airUtilTx": 9.1},
        "localStats": {"numOnlineNodes": 67, "numTxRelay": 530, "numPacketsTx": 553},
    }
    r = report("ShortSlow", {**app, "share_pct": 2.5}, busy, 20)
    assert len(r["warnings"]) == 3 and "25 %" in r["warnings"][0]
    assert r["device"]["tx_relay"] == 530 and "67" in r["notes"][0]
    assert report("ShortSlow", app, None, None)["device"]["age_s"] is None


def test_sent_texts_count_in_the_device_status(tmp_path):
    from meshplay.mapapp.device import DeviceLink
    from tests.test_mapapp_messages import FakeIface

    dev = DeviceLink(tmp_path, log_packets=False)
    dev.iface, dev.state = FakeIface(), "verbunden"
    dev.send_text("hallo", "!abcd1234", 1, tag="coord")
    dev.send_text("hi", "^all", 0)
    dev.request_position("!abcd1234", 1)
    kinds = dev.status()["airtime"]["app"]["kinds"]
    assert {k: v["packets"] for k, v in kinds.items()} == {
        "coord": 1,
        "text": 1,
        "position_request": 1,
    }
