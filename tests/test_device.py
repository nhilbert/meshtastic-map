"""Port detection (no device needed) and a real connection (marker hardware)."""

import time
from types import SimpleNamespace

import pytest

from meshplay import connect, device

BT = r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000\7&0&000000000000_00000005"


def port(dev, vid=None, hwid="", manufacturer=None):
    return SimpleNamespace(
        device=dev, vid=vid, pid=None, hwid=hwid, manufacturer=manufacturer, description=dev
    )


@pytest.fixture
def system(monkeypatch):
    """Set the system's serial ports and MESHTASTIC_PORT."""

    def setup(ports, configured=None):
        monkeypatch.setattr(device.list_ports, "comports", lambda: ports)
        monkeypatch.setattr(device, "load_settings", lambda: SimpleNamespace(port=configured))

    return setup


def test_known_vendor_first_bluetooth_last(system):
    system(
        [
            port("COM3", hwid=BT),
            port("COM9", vid=0x1234, hwid="USB VID:PID=1234:0001", manufacturer="Other"),
            port("COM7", vid=0x2886, hwid="USB VID:PID=2886:0059"),
        ]
    )
    ports = device.list_serial_ports()
    assert [(p["device"], p["kind"]) for p in ports] == [
        ("COM7", "known"),
        ("COM9", "usb"),
        ("COM3", "bluetooth"),
    ]
    assert ports[0]["vendor"] == "Seeed"
    assert device.find_port() == "COM7"


def test_configured_port_only_if_present(system):
    usb = port("COM7", vid=0x2886, hwid="USB VID:PID=2886:0059")
    system([usb], configured="COM8")  # the .env names a port that is gone
    assert device.find_port() == "COM7"
    system([usb, port("COM8", vid=0x1A86, hwid="USB")], configured="com8")
    assert device.find_port() == "com8"


def test_unknown_usb_only_when_unambiguous(system):
    other = port("COM9", vid=0x1234, hwid="USB VID:PID=1234:0001")
    system([other, port("COM3", hwid=BT)])
    assert device.find_port() == "COM9"  # the only USB serial port
    system([other, port("COM10", vid=0x4321, hwid="USB VID:PID=4321:0001")])
    assert device.find_port() is None  # two unknown ones: the owner chooses
    system([port("COM3", hwid=BT), port("COM4", hwid=BT)])
    assert device.find_port() is None  # Bluetooth ports are never taken


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


class FakeSerial:
    """Stands in for meshtastic's SerialInterface: records opens and closes."""

    opened: list = []

    def __init__(self, devPath):
        self.devPath, self.closed = devPath, False
        FakeSerial.opened.append(self)

    def close(self):
        self.closed = True


@pytest.fixture
def link(system, tmp_path, monkeypatch):
    import meshtastic.serial_interface

    from meshplay.mapapp import device as mapdevice

    monkeypatch.setattr(meshtastic.serial_interface, "SerialInterface", FakeSerial)
    monkeypatch.setattr(mapdevice, "RETRY_S", 0.1)
    FakeSerial.opened = []
    d = mapdevice.DeviceLink(tmp_path, log_packets=False)
    yield d
    d.disconnect()


def test_link_refuses_a_missing_port_then_retries(system, link):
    system([port("COM3", hwid=BT)])
    assert link.ports()["auto"] is None
    link.connect("COM8")  # the owner's choice: a missing port is an error
    wait_for(lambda: link.state == "Fehler")
    assert "COM8" in link.error and link.status()["retrying"]
    wait_for(lambda: link.retries >= 1)  # retries fall back to detection: still nothing
    assert "USB" in link.error
    system([port("COM3", hwid=BT), port("COM9", vid=0x2886, hwid="USB")])  # plugged in
    wait_for(lambda: link.state == "verbunden")
    assert link.port == "COM9" and not link.status()["retrying"]


def test_lost_connection_is_released_and_retried(system, link):
    system([port("COM7", vid=0x2886, hwid="USB")])
    link.connect()
    wait_for(lambda: link.state == "verbunden")
    first = link.iface
    link._on_lost(interface=first)
    assert link.state == "Fehler" and link.lost_at is not None
    wait_for(lambda: first.closed)  # the port is released
    wait_for(lambda: link.state == "verbunden")  # and opened again
    assert link.iface is not first and link.lost_at is None
    link._on_lost(interface=first)  # an old interface's late notice changes nothing
    assert link.state == "verbunden"
    link.disconnect()
    assert not link.wanted and not link.status()["retrying"]


@pytest.mark.hardware
def test_can_connect_and_read_node_info():
    with connect() as iface:
        assert iface.getMyNodeInfo()["user"]["id"].startswith("!")
