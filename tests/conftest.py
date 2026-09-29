"""Shared fixtures: a Coordinator over a fake radio (tests/test_coord*.py)."""

import pytest

from meshplay.config import Settings
from meshplay.mapapp.registry import Context

HOME = (50.7374, 7.0982)


@pytest.fixture
def coord(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from meshplay.mapapp.coord import missions
    from meshplay.mapapp.coord.missions import Coordinator
    from meshplay.mapapp.device import DeviceLink
    from tests.test_mapapp_messages import FakeIface

    # the tests run in seconds: warnings and arrivals may follow each other at once
    monkeypatch.setattr(missions, "PRIORITY_GAP_S", 0)
    ctx = Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=HOME))
    dev = DeviceLink(tmp_path, log_packets=False)
    dev.iface, dev.state = FakeIface(), "verbunden"
    dev.iface.myInfo = SimpleNamespace(my_node_num=0x11112222)
    ctx.device = dev
    c = Coordinator(ctx)
    c.set_enabled(True)
    c.settings["min_gap_s"] = 120
    c.settings["send_legend"] = False  # the legend test switches it on
    yield c
    c.shutdown()
