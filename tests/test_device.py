"""Port detection (no device needed) and a real connection (marker hardware)."""

from types import SimpleNamespace

import pytest

from meshplay import connect, device

BT = "BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000\7&0&000000000000_00000005"


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


def test_link_refuses_a_missing_port(system, tmp_path):
    from meshplay.mapapp.device import DeviceLink

    system([port("COM3", hwid=BT)])
    link = DeviceLink(tmp_path, log_packets=False)
    assert link.ports()["auto"] is None
    link._connect("COM8")
    assert link.state == "Fehler" and "COM8" in link.error
    link._connect(None)
    assert link.state == "Fehler" and "USB" in link.error


@pytest.mark.hardware
def test_can_connect_and_read_node_info():
    with connect() as iface:
        assert iface.getMyNodeInfo()["user"]["id"].startswith("!")
