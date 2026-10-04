"""Port detection and the Bluetooth connection (no device needed, the radio libraries are
faked) and a real connection (marker hardware)."""

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


class FakeBle:
    """Stands in for ble.Interface: only the devices named Meshtastic_1234 and Meshtastic_5678
    are in range, and the second one is not paired with this computer."""

    opened: list = []

    def __init__(self, address):
        from meshtastic.ble_interface import BLEInterface

        FakeBle.opened.append(address)
        if address == "Meshtastic_5678":  # what bleak raises on Windows
            raise OSError(5, "GATT Protocol Error: Insufficient Authentication")
        if address != "Meshtastic_1234":
            raise BLEInterface.BLEError("not found", BLEInterface.BLEError.DEVICE_NOT_FOUND)
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def link(system, tmp_path, monkeypatch):
    from meshplay import ble
    from meshplay.mapapp import device as mapdevice

    monkeypatch.setattr(device, "SerialInterface", FakeSerial)
    monkeypatch.setattr(ble, "Interface", FakeBle)
    monkeypatch.setattr(mapdevice, "RETRY_S", 0.1)
    FakeSerial.opened, FakeBle.opened = [], []
    d = mapdevice.DeviceLink(tmp_path, log_packets=False)
    yield d
    d.disconnect()


def test_link_refuses_a_missing_port_then_retries(system, link):
    system([port("COM3", hwid=BT)])
    assert link.ports()["auto"] is None
    link.connect("COM8")  # the owner's choice: a missing port is an error
    wait_for(lambda: link.state == "Fehler")
    status = link.status()
    assert "COM8" in status["error"] and status["error_kind"] == "no_port" and status["retrying"]
    wait_for(lambda: link.retries >= 1)  # retries fall back to detection: still nothing
    assert "USB" in link.status()["error"]
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


def test_bluetooth_only_when_asked_for(system):
    usb = port("COM7", vid=0x2886, hwid="USB VID:PID=2886:0059")
    system([usb], configured="ble:Meshtastic_1234")
    assert device.find_port() == "ble:Meshtastic_1234"  # taken as it is, USB or not
    assert device.is_ble("BLE:AA:BB:CC:DD:EE:FF") and not device.is_ble("COM7")
    assert not device.is_ble(None)
    with pytest.raises(ValueError, match="address or name"):
        device.open_interface("ble:")


def test_link_over_bluetooth(system, link):
    system([port("COM7", vid=0x2886, hwid="USB")])
    link.connect("ble:Meshtastic_1234")
    wait_for(lambda: link.state == "verbunden")
    assert link.port == "ble:Meshtastic_1234" and isinstance(link.iface, FakeBle)
    assert not FakeSerial.opened
    first = link.iface
    link._on_lost(interface=first)  # out of range
    wait_for(lambda: first.closed and link.state == "verbunden")
    assert link.iface is not first and link.port == "ble:Meshtastic_1234"


def test_link_keeps_trying_a_missing_bluetooth_device(system, link):
    system([port("COM7", vid=0x2886, hwid="USB")])
    link.connect("ble:AA:BB:CC:DD:EE:FF")
    wait_for(lambda: link.retries >= 2)
    status = link.status()
    assert status["state"] == "Fehler" and status["error_kind"] == "not_found"
    assert status["trying"] == "ble:AA:BB:CC:DD:EE:FF"
    assert "AA:BB:CC:DD:EE:FF" in status["error"]
    assert "Handy" in status["error"]  # the plain message, not the library's
    assert set(FakeBle.opened) == {"AA:BB:CC:DD:EE:FF"} and not FakeSerial.opened  # no USB instead


def test_link_stops_at_an_unpaired_bluetooth_device(system, link):
    system([])
    link.connect("ble:Meshtastic_5678")
    wait_for(lambda: link.state == "Fehler")
    status = link.status()
    assert status["error_kind"] == "unpaired" and "gekoppelt" in status["error"]
    assert not status["retrying"]  # pairing is the owner's step; no attempts meanwhile
    time.sleep(0.3)  # RETRY_S is 0.1 here
    assert FakeBle.opened == ["Meshtastic_5678"]


def test_unpaired_is_recognised_behind_the_library_error():
    from meshplay import ble

    try:
        try:
            raise RuntimeError("(5, 'GATT Protocol Error: Insufficient Authentication')")
        except RuntimeError as e:
            raise ValueError("Error writing BLE") from e
    except ValueError as e:
        assert ble.refused_unpaired(e)
    assert not ble.refused_unpaired(OSError("Das Handle ist ungültig"))


def test_bluetooth_search_fills_the_port_list(system, link, monkeypatch):
    from meshtastic.ble_interface import BLEInterface

    system([port("COM7", vid=0x2886, hwid="USB")])
    found = [SimpleNamespace(name="Meshtastic_1234", address="AA:BB:CC:DD:EE:FF")]
    monkeypatch.setattr(BLEInterface, "scan", staticmethod(lambda: found))
    ports = link.scan_bluetooth()
    assert ports["auto"] == "COM7"  # detection stays with USB
    ble_ports = [p for p in ports["ports"] if p["kind"] == "ble"]
    assert [(p["device"], p["description"]) for p in ble_ports] == [
        ("ble:AA:BB:CC:DD:EE:FF", "Meshtastic_1234")
    ]
    assert link.ports()["ports"] == ports["ports"]  # remembered for the page's next request

    def no_adapter():
        raise OSError("Bluetooth is switched off")

    monkeypatch.setattr(BLEInterface, "scan", staticmethod(no_adapter))
    with pytest.raises(ValueError, match="switched off"):
        link.scan_bluetooth()


class FakeBleClient:
    """Stands in for meshtastic's BLEClient. Like bleak on Windows it reports the end of the
    link also when the program itself disconnects."""

    made: list = []
    fail_connect = False

    def __init__(self, address=None, disconnected_callback=None):
        self.address, self.callback = address, disconnected_callback
        self.disconnects, self.closed = 0, False
        FakeBleClient.made.append(self)

    def connect(self):
        if FakeBleClient.fail_connect:
            raise TimeoutError("no answer")

    def has_characteristic(self, uuid):
        return False

    def start_notify(self, *args):
        pass

    def disconnect(self):
        self.disconnects += 1
        self.callback(self)

    def close(self):
        self.closed = True


@pytest.fixture
def ble_client(monkeypatch):
    from meshplay import ble

    monkeypatch.setattr(ble, "BLEClient", FakeBleClient)
    monkeypatch.setattr(
        ble.Interface, "find_device", lambda self, address: SimpleNamespace(address=address)
    )
    FakeBleClient.made, FakeBleClient.fail_connect = [], False
    return ble


def test_bluetooth_close_is_not_answered_by_another_close(ble_client):
    iface = ble_client.Interface("AA:BB:CC:DD:EE:FF", noProto=True)
    client = FakeBleClient.made[0]
    iface.close()
    assert client.disconnects == 1 and client.closed and iface.client is None


def test_bluetooth_drop_closes_and_reports_lost(ble_client):
    from pubsub import pub

    lost = []

    def on_lost(interface=None):
        lost.append(interface)

    pub.subscribe(on_lost, "meshtastic.connection.lost")
    iface = ble_client.Interface("AA:BB:CC:DD:EE:FF", noProto=True)
    client = FakeBleClient.made[0]
    client.callback(client)  # the device went out of range
    wait_for(lambda: client.closed and iface in lost)
    assert iface.client is None


def test_failed_bluetooth_attempt_leaves_nothing_behind(ble_client):
    import threading

    FakeBleClient.fail_connect = True
    before = {t for t in threading.enumerate() if t.name == "BLEReceive"}
    with pytest.raises(TimeoutError):
        ble_client.Interface("AA:BB:CC:DD:EE:FF", noProto=True)
    assert FakeBleClient.made[0].closed
    wait_for(lambda: {t for t in threading.enumerate() if t.name == "BLEReceive"} <= before)


@pytest.mark.hardware
def test_can_connect_and_read_node_info():
    with connect() as iface:
        assert iface.getMyNodeInfo()["user"]["id"].startswith("!")
