"""The device's configuration in the view Gerät: view, check, profile and backups, over the
simulated radio (nothing is written to a device)."""

import base64
import json

import pytest
import yaml

from meshplay.config import Settings
from meshplay.mapapp import device_config
from meshplay.mapapp.device import DeviceLink, Simulation
from meshplay.mapapp.fake_device import FakeInterface
from meshplay.mapapp.registry import Context

HOME = (50.7374, 7.0982)


@pytest.fixture
def ctx(tmp_path):
    c = Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=HOME))
    c.device = DeviceLink(tmp_path, log_packets=False, simulate=Simulation(HOME))
    c.device.iface, c.device.state = FakeInterface(HOME), "verbunden"
    yield c
    c.device.disconnect()


def texts(view: dict, level: str) -> list[str]:
    return [c["text"] for c in view["checks"] if c["level"] == level]


def test_key_kinds():
    kind = device_config.key_kind
    assert [kind(b""), kind(b"\x00"), kind(b"\x01"), kind(b"\x05")] == [
        "none",
        "none",
        "default",
        "simple",
    ]
    assert kind(bytes(16)) == "aes128" and kind(bytes(32)) == "aes256"


def test_view_shows_the_settings_and_keeps_the_secrets(ctx):
    node = ctx.device.iface.localNode
    node.localConfig.network.wifi_psk = "wifi-secret"
    node.moduleConfig.mqtt.password = "mqtt-secret"
    view = device_config.view(ctx)
    assert view["radio"]["preset"] == "ShortSlow" and view["radio"]["hop_limit"] == 3
    assert view["identity"]["short"] == "HOME" and view["node"] == "!fa4e0000"
    assert [(c["index"], c["key"], c["private"]) for c in view["channels"]] == [
        (0, "default", False),
        (1, "aes256", True),
    ]
    assert view["telemetry"][0] == {"kind": "device", "on": True, "interval_s": 1800}
    sent = json.dumps(view)
    secrets = [node.localConfig.security.private_key, node.channels[1].settings.psk]
    for secret in secrets:
        assert base64.b64encode(secret).decode() not in sent
    assert "wifi-secret" not in sent and "mqtt-secret" not in sent
    assert base64.b64encode(node.localConfig.security.public_key).decode() in sent
    fields = {s["name"]: dict(s["fields"]) for s in view["all"]}
    assert fields["security"]["privateKey"] == device_config.MASK
    assert fields["lora"]["modemPreset"] == "SHORT_SLOW"
    assert fields["module_config.mqtt"]["password"] == device_config.MASK


def test_check_passes_for_the_usual_setup(ctx):
    view = device_config.view(ctx)
    assert not texts(view, "warn")
    assert len(texts(view, "ok")) == 3  # preset, hop limit, private channel
    assert len(texts(view, "info")) == 1  # no profile yet


def test_check_names_what_does_not_fit(ctx):
    node = ctx.device.iface.localNode
    lora = node.localConfig.lora
    lora.hop_limit, lora.region, lora.modem_preset = 5, 0, 0  # LONG_FAST, region unset
    node.channels[1].settings.psk = b"\x01"  # the private channel with the public key
    warnings = texts(device_config.view(ctx), "warn")
    assert len(warnings) == 5
    assert any("LongFast" in w and "ShortSlow" in w for w in warnings)
    assert any("Region" in w for w in warnings)
    assert any("Hop-Limit 5" in w for w in warnings)
    assert any("Kanal 1 ist nicht privat" in w for w in warnings)
    assert any("Kanal 1 sendet die genaue Position" in w for w in warnings)

    node.channels[1].role = 0  # disabled
    assert any("Kanal 1 fehlt" in w for w in texts(device_config.view(ctx), "warn"))


def test_profile_and_what_differs_from_it(ctx, tmp_path):
    view = device_config.save_profile(ctx)
    assert view["files"]["profile"] and not texts(view, "warn")
    assert any("Entspricht dem Profil" in t for t in texts(view, "ok"))
    profile = tmp_path / "device" / "fa4e0000" / "profile.yaml"
    assert "privateKey" not in profile.read_text(encoding="utf-8")

    node = ctx.device.iface.localNode
    node.localConfig.lora.hop_limit = 2
    node.localConfig.security.public_key = bytes(32)
    node.moduleConfig.telemetry.device_update_interval = 900
    node.channels[1].settings.psk = bytes(range(32))
    warnings = texts(device_config.view(ctx), "warn")
    assert "Weicht vom Profil ab: lora.hopLimit ist 2, im Profil 3" in warnings
    assert "Weicht vom Profil ab: security.publicKey" in warnings  # a key: no values
    assert (
        "Weicht vom Profil ab: module_config.telemetry.deviceUpdateInterval ist 900, "
        "im Profil 1800" in warnings
    )
    assert "Weicht vom Profil ab: Kanal 1" in warnings
    assert len(warnings) == 4

    view = device_config.save_profile(ctx)  # the new state becomes the profile
    assert not texts(view, "warn") and profile.with_suffix(".yaml.bak").exists()

    profile.write_text("- not\n- a profile\n", encoding="utf-8")
    assert any("Profil nicht lesbar" in w for w in texts(device_config.view(ctx), "warn"))


def test_backup_restores_with_the_command_line(ctx, tmp_path):
    main = pytest.importorskip("meshtastic.__main__")
    node = ctx.device.iface.localNode
    view = device_config.backup(ctx)
    assert view["files"]["backups"] == 1 and view["files"]["last_backup"]
    path = next((tmp_path / "device" / "fa4e0000" / "backups").glob("*.yaml"))
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["owner_short"] == "HOME" and saved["channel_url"] == node.getURL()
    assert "privateKey" not in saved["config"]["security"]

    with_key = device_config.export(ctx.device.iface, private_key=True)
    reader = getattr(main, "_profile_from_yaml", None)
    if reader is None:  # a private function of the command line: gone in another version
        pytest.skip("this meshtastic version reads profiles differently")
    restored = reader(yaml.safe_load(yaml.safe_dump(with_key)))
    assert restored.config == node.localConfig
    assert restored.module_config == node.moduleConfig
    assert restored.channel_url == node.getURL()


def test_without_a_device_it_says_so(ctx):
    ctx.device.disconnect()
    for call in (device_config.view, device_config.backup, device_config.save_profile):
        with pytest.raises(ValueError, match="Gerät nicht verbunden"):
            call(ctx)


# ---------------------------------------------------------------- writing
def kinds(iface) -> list[str]:
    return [m.WhichOneof("payload_variant") for m in iface.admin]


def test_form_offers_the_writable_settings_with_the_device_values(ctx):
    form = {f["id"]: {s["name"]: s for s in f["settings"]} for f in device_config.view(ctx)["form"]}
    assert set(form) == {"radio", "telemetry", "identity"}
    assert {n for f in form.values() for n in f} == set(device_config.FIELDS)
    assert form["radio"]["hop_limit"]["default"] == "3"
    assert form["telemetry"]["telemetry_device"]["default"] is True
    assert form["telemetry"]["telemetry_device_s"]["default"] == "1800"
    roles = [o[0] for o in form["identity"]["role"]["options"]]
    assert "CLIENT_MUTE" in roles and "REPEATER" not in roles  # deprecated ones are not offered


def test_preview_says_what_changes_and_what_follows(ctx):
    plan = device_config.preview(
        ctx, {"hop_limit": "5", "tx_power": 0, "role": "ROUTER", "long_name": "Neuer Name"}
    )
    assert [(c["name"], c["old"], c["new"]) for c in plan["changes"]] == [
        ("hop_limit", "3", "5"),  # tx_power 0 is what the device has: no change
        ("long_name", "SIM home", "Neuer Name"),
        ("role", "CLIENT", "ROUTER"),
    ]
    warnings = [n["text"] for n in plan["notes"] if n["level"] == "warn"]
    assert len(warnings) == 2 and "Hop-Limit 5" in warnings[0] and "Router" in warnings[1]
    infos = " ".join(n["text"] for n in plan["notes"] if n["level"] == "info")
    assert "Simulation" in infos and "Backup" in infos and "Namen" in infos
    assert not ctx.device.iface.admin  # a preview writes nothing


@pytest.mark.parametrize(
    "changes",
    [
        {"hop_limit": 8},
        {"hop_limit": "viele"},
        {"tx_power": 2.5},
        {"role": "KING"},
        {"telemetry_device": "nein"},
        {"telemetry_device_s": -1},
        {"long_name": "  "},
        {"long_name": "x" * 40},
        {"short_name": "äöü"},  # 6 bytes
        {"hop_limit": 3},  # what it has
        {},
        None,
    ],
)
def test_bad_or_empty_changes_are_refused(ctx, changes):
    for call in (device_config.preview, device_config.write):
        with pytest.raises(ValueError):
            call(ctx, changes)
    with pytest.raises(KeyError):
        device_config.preview(ctx, {"region": "US"})
    assert not ctx.device.iface.admin


def test_write_is_one_transaction_with_a_backup_and_a_result(ctx, tmp_path):
    iface = ctx.device.iface
    device_config.save_profile(ctx)  # the device matches its profile: the write moves it
    res = device_config.write(
        ctx,
        {
            "hop_limit": "2",
            "telemetry_device": False,
            "telemetry_device_s": "3600",
            "short_name": "NEU",
        },
    )
    assert kinds(iface) == [
        "begin_edit_settings",
        "set_owner",
        "set_config",
        "set_module_config",
        "commit_edit_settings",
    ]
    assert iface.admin[1].set_owner.long_name == "SIM home"  # the name that stays is sent too
    assert iface.admin[2].set_config.lora.hop_limit == 2
    assert iface.admin[2].set_config.lora.region  # the whole section, not only the field
    telemetry = iface.admin[3].set_module_config.telemetry
    assert not telemetry.device_telemetry_enabled and telemetry.device_update_interval == 3600

    assert res["restart_s"] == 0  # the simulated radio does not reboot
    view = res["view"]
    assert view["result"]["ok"] and "Hop-Limit" in view["result"]["text"]
    assert view["checks"][0] == {"level": "ok", "text": view["result"]["text"]}
    assert view["radio"]["hop_limit"] == 2 and view["identity"]["short"] == "NEU"
    assert view["telemetry"][0] == {"kind": "device", "on": False, "interval_s": 3600}
    assert not texts(view, "warn")  # the profile moved with the write
    folder = tmp_path / "device" / "fa4e0000"
    before = yaml.safe_load(next((folder / "backups").glob("*_auto.yaml")).read_text("utf-8"))
    assert before["config"]["lora"]["hopLimit"] == 3 and before["owner_short"] == "HOME"


def test_write_leaves_a_profile_that_differed_before(ctx):
    device_config.save_profile(ctx)
    ctx.device.iface.localNode.localConfig.lora.tx_power = 10  # changed elsewhere
    notes = device_config.preview(ctx, {"hop_limit": 2})["notes"]
    assert any("Profil bleibt" in n["text"] for n in notes)
    warnings = texts(device_config.write(ctx, {"hop_limit": 2})["view"], "warn")
    assert any("lora.hopLimit ist 2, im Profil 3" in w for w in warnings)
    assert any("lora.txPower" in w for w in warnings)


def test_automatic_backups_are_limited(ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(device_config, "AUTO_BACKUPS", 2)
    backups = tmp_path / "device" / "fa4e0000" / "backups"
    backups.mkdir(parents=True)
    old = ("2026-01-01_000000_auto.yaml", "2026-01-02_000000_auto.yaml", "2026-01-01_000000.yaml")
    for name in old:
        (backups / name).write_text("owner: x\n", encoding="utf-8")
    device_config.write(ctx, {"hop_limit": 2})
    left = sorted(p.name for p in backups.iterdir())
    assert len(left) == 3 and "2026-01-01_000000.yaml" in left  # manual ones are never deleted
    assert "2026-01-01_000000_auto.yaml" not in left


def test_write_is_refused_in_managed_mode_and_during_a_walk(ctx):
    from types import SimpleNamespace

    ctx.jobs = SimpleNamespace(running=lambda kind=None: ["probe"])
    with pytest.raises(ValueError, match="Traceroute-Rundgang"):
        device_config.write(ctx, {"hop_limit": 2})
    ctx.jobs = None
    ctx.device.iface.localNode.localConfig.security.is_managed = True
    assert "verwalteten Modus" in device_config.view(ctx)["locked"]
    for call in (device_config.preview, device_config.write):
        with pytest.raises(ValueError, match="verwalteten Modus"):
            call(ctx, {"hop_limit": 2})
    assert not ctx.device.iface.admin


@pytest.fixture
def real(tmp_path, monkeypatch):
    """A link that is not simulated (a write restarts it), over the fake interface."""
    monkeypatch.setattr(device_config, "PAUSE_S", 0)
    c = Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=HOME))
    c.device = DeviceLink(tmp_path, log_packets=False)
    c.device.iface, c.device.state, c.device.connects = FakeInterface(HOME), "verbunden", 1
    restarts = []
    monkeypatch.setattr(c.device, "restart", restarts.append)  # the real one reconnects
    yield c, restarts
    c.device.iface.close()


def test_result_comes_with_the_configuration_read_after_the_reboot(real):
    ctx, restarts = real
    device_config.save_profile(ctx)
    notes = device_config.preview(ctx, {"hop_limit": 2})["notes"]
    assert any("startet danach neu" in n["text"] for n in notes)
    res = device_config.write(ctx, {"hop_limit": 2, "tx_power": 20})
    assert res == {"restart_s": device_config.RESTART_S} and restarts == [device_config.RESTART_S]
    assert device_config.view(ctx)["result"] is None  # not read anew yet

    ctx.device.connects += 1  # reconnected; the node took one of the two settings
    ctx.device.iface.localNode.localConfig.lora.tx_power = 0
    view = device_config.view(ctx)
    assert not view["result"]["ok"] and "Sendeleistung" in view["result"]["text"]
    assert "Hop-Limit" not in view["result"]["text"]
    assert any("im Profil 3" in w for w in texts(view, "warn"))  # so the profile stayed


def test_a_failed_write_restarts_the_link(real, monkeypatch):
    ctx, restarts = real

    def broken(section):
        raise OSError("port gone")

    monkeypatch.setattr(ctx.device.iface.localNode, "writeConfig", broken)
    with pytest.raises(ValueError, match="Schreiben fehlgeschlagen.*port gone"):
        device_config.write(ctx, {"hop_limit": 2})
    assert restarts and ctx.device.config_write is None
    assert "commit_edit_settings" not in kinds(ctx.device.iface)
