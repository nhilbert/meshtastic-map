"""The device's own configuration for the view Gerät: what it is set to, whether that fits the
app, backups, and writing the settings of FIELDS.

What is shown comes from the configuration the library downloaded when it connected
(localConfig, moduleConfig, channels). Writing is the owner's own action from the page, never
the server's: preview() says what would change and what follows from it, write() makes a
backup, sends one settings transaction to the node (over USB or Bluetooth, not over the mesh)
and restarts the link, because the node reboots; the next view() then checks what the node
really has. A new owner name is the one thing the node then announces to the mesh by itself.

The profile and the backups are YAML files in the format of the `meshtastic` command line, so
`meshtastic --configure <file>` restores one without this app:

  data/device/<node id>/profile.yaml         the wanted state; the check compares against it
  data/device/<node id>/backups/<time>.yaml  backups (<time>_auto.yaml: before a write)

They hold the channel keys (inside the channel URL), which is why they stay in data/. The
private key is only in a backup that asks for it. Secrets never go to the page: of a channel
key it learns the kind, of passwords that there is one.
"""

from __future__ import annotations

import base64
import time
from datetime import datetime
from pathlib import Path

import yaml
from google.protobuf.json_format import MessageToDict
from meshtastic.protobuf import apponly_pb2, config_pb2

from meshplay.config import DEFAULT_PRESET
from meshplay.mapapp.coord.settings import DEFAULTS as COORD_DEFAULTS
from meshplay.mapapp.i18n import N_, _
from meshplay.mapapp.registry import Context, Setting
from meshplay.probe import modem_preset

SECRET = {"privateKey", "wifiPsk", "password"}  # shown as dots, left out of differences
QUIET = SECRET | {"publicKey", "adminKey"}  # a difference is named without the values
MASK = "•••"
DEFAULT_HOPS = 3  # what the firmware takes for hop_limit 0, and what Meshtastic recommends
MAX_DIFFERENCES = 12  # listed in the check; the rest is counted
KEY_LABELS = {
    "none": N_("unverschlüsselt"),
    "default": N_("Standardschlüssel, öffentlich bekannt"),
    "simple": N_("einfacher Schlüssel, öffentlich bekannt"),
    "aes128": "AES-128",
    "aes256": "AES-256",
}
PRIVATE_KEYS = ("aes128", "aes256")
Role = config_pb2.Config.DeviceConfig.Role

# The settings the page can write: name -> (where, section, attribute); where is "config"
# (localConfig), "module" (moduleConfig) or "owner" (the node's names).
FIELDS = {
    "hop_limit": ("config", "lora", "hop_limit"),
    "tx_power": ("config", "lora", "tx_power"),
    "telemetry_device": ("module", "telemetry", "device_telemetry_enabled"),
    "telemetry_device_s": ("module", "telemetry", "device_update_interval"),
    "telemetry_environment": ("module", "telemetry", "environment_measurement_enabled"),
    "telemetry_environment_s": ("module", "telemetry", "environment_update_interval"),
    "telemetry_power": ("module", "telemetry", "power_measurement_enabled"),
    "telemetry_power_s": ("module", "telemetry", "power_update_interval"),
    "long_name": ("owner", "", "longName"),
    "short_name": ("owner", "", "shortName"),
    "role": ("config", "device", "role"),
}
INTERVALS = (900, 1800, 3600, 7200, 21600, 43200, 86400)  # offered for telemetry [s]
MAX_INTERVAL_S = 7 * 86400
NAME_BYTES = {"long_name": 39, "short_name": 4}  # what the firmware's fields hold
ROUTER_ROLES = {"ROUTER", "ROUTER_LATE", "ROUTER_CLIENT", "REPEATER"}
RESTART_S = 20.0  # the firmware reboots 7 s after the commit; then it has to boot
PAUSE_S = 0.5  # between the admin messages of a write, as the command line does
AUTO_BACKUPS = 20  # backups made before a write that are kept


def key_kind(psk: bytes) -> str:
    """What a channel key is, without the key: none, default (the key every node ships
    with), simple (that key with another last byte, just as public), aes128, aes256."""
    if psk in (b"", b"\x00"):
        return "none"
    if len(psk) == 1:
        return "default" if psk == b"\x01" else "simple"
    return "aes128" if len(psk) == 16 else "aes256"


def precision_m(bits: int) -> float | None:
    """Roughly how exact a position sent with this many precision bits is [m]; None for the
    two ends (0: no position, 32: exact)."""
    return None if bits in (0, 32) else 23_860_000 / 2**bits


def _iface(ctx: Context):
    dev = ctx.device
    if dev is None or dev.iface is None or dev.state != "verbunden":
        raise ValueError(_("Gerät nicht verbunden"))
    return dev.iface


def _user(iface) -> dict:
    return (iface.getMyNodeInfo() or {}).get("user") or {}


def channels(node) -> list[dict]:
    """The active channels with the kind of their key."""
    out = []
    for c in node.channels or []:
        if not c.role:  # DISABLED
            continue
        s = c.settings
        kind = key_kind(s.psk)
        bits = s.module_settings.position_precision
        out.append(
            {
                "index": c.index,
                "primary": c.role == 1,
                "name": s.name,
                "key": kind,
                "key_label": _(KEY_LABELS[kind]),
                "private": kind in PRIVATE_KEYS,
                "position_bits": bits,
                "position_m": precision_m(bits),
                "uplink": s.uplink_enabled,
                "downlink": s.downlink_enabled,
            }
        )
    return out


# ---------------------------------------------------------------- export, profile, backups
def _plain(message) -> dict:
    """A config message as a dict with every field spelled out, so that a restore also
    switches off what is off."""
    d = MessageToDict(message, always_print_fields_with_no_presence=True)
    d.pop("version", None)  # the config format's version, not a setting
    return d


def export(iface, private_key: bool = False) -> dict:
    """The configuration as the command line's --export-config writes it: owner, channel URL,
    a fixed position, config and module_config. Canned messages and the ringtone are left
    out: the library asks the node for them and waits without a timeout."""
    node = iface.localNode
    info = iface.getMyNodeInfo() or {}
    user = info.get("user") or {}
    out: dict = {}
    if user.get("longName"):
        out["owner"] = user["longName"]
    if user.get("shortName"):
        out["owner_short"] = user["shortName"]
    out["channel_url"] = node.getURL()
    pos = info.get("position") or {}
    if node.localConfig.position.fixed_position and "latitude" in pos and "longitude" in pos:
        out["location"] = {"lat": pos["latitude"], "lon": pos["longitude"]}
        if pos.get("altitude"):
            out["location"]["alt"] = pos["altitude"]
    config = _plain(node.localConfig)
    security = config.get("security", {})
    if not private_key:
        security.pop("privateKey", None)
    for name in ("privateKey", "publicKey"):  # the command line wants its keys marked
        if security.get(name):
            security[name] = "base64:" + security[name]
        else:
            security.pop(name, None)
    if "adminKey" in security:
        security["adminKey"] = ["base64:" + k for k in security["adminKey"]]
    out["config"] = config
    out["module_config"] = _plain(node.moduleConfig)
    return out


def _dir(ctx: Context, iface) -> Path:
    node = _user(iface).get("id") or "unknown"
    return ctx.data_dir / "device" / node.lstrip("!")


def _write(path: Path, iface, private_key: bool) -> None:
    head = (
        f"# Configuration of {_user(iface).get('id', 'the node')}, "
        f"{datetime.now():%Y-%m-%d %H:%M}. Restore: meshtastic --configure <this file>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(export(iface, private_key), sort_keys=False, allow_unicode=True)
    path.write_text(head + text, encoding="utf-8")


def backup(ctx: Context, private_key: bool = False) -> dict:
    """Write a backup of the connected device's configuration; returns view()."""
    iface = _iface(ctx)
    _write(
        _dir(ctx, iface) / "backups" / f"{datetime.now():%Y-%m-%d_%H%M%S}.yaml", iface, private_key
    )
    return view(ctx)


def _save_profile(folder: Path, iface) -> None:
    path = folder / "profile.yaml"
    if path.exists():
        path.replace(path.with_suffix(".yaml.bak"))
    _write(path, iface, private_key=False)


def save_profile(ctx: Context) -> dict:
    """Take the device's configuration as the wanted state (the previous profile stays as
    profile.yaml.bak); returns view()."""
    iface = _iface(ctx)
    _save_profile(_dir(ctx, iface), iface)
    return view(ctx)


def _auto_backup(folder: Path, iface) -> None:
    """The backup before a write; of these only the newest AUTO_BACKUPS stay."""
    backups = folder / "backups"
    _write(backups / f"{datetime.now():%Y-%m-%d_%H%M%S}_auto.yaml", iface, private_key=False)
    for old in sorted(backups.glob("*_auto.yaml"))[:-AUTO_BACKUPS]:
        old.unlink()


def _files(folder: Path) -> dict:
    profile = folder / "profile.yaml"
    backups = sorted((folder / "backups").glob("*.yaml"))
    return {
        "dir": str(folder),
        "profile": profile.stat().st_mtime if profile.exists() else None,
        "backups": len(backups),
        "last_backup": backups[-1].stat().st_mtime if backups else None,
    }


# ---------------------------------------------------------------- comparing with the profile
def _flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _url_channels(url: str) -> list[bytes]:
    """The channel settings inside a channel URL, each as its bytes (for comparing)."""
    data = url.split("#")[-1]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    return [s.SerializeToString() for s in apponly_pb2.ChannelSet.FromString(raw).settings]


def differences(profile: dict, current: dict) -> list[dict]:
    """Where the device differs from the profile: {"field", "now", "then"} for a setting
    (without values for keys), {"channel": n} for a channel. A field only one side knows is
    not a difference: a firmware update adds settings."""
    out = []
    for name in ("owner", "owner_short"):
        if profile.get(name) != current.get(name):
            out.append({"field": name, "now": current.get(name), "then": profile.get(name)})
    for part in ("config", "module_config"):
        then, now = _flat(profile.get(part) or {}), _flat(current.get(part) or {})
        for key in then.keys() & now.keys():
            if then[key] == now[key]:
                continue
            field = f"{part}.{key}" if part == "module_config" else key
            if key.rsplit(".", 1)[-1] in QUIET:
                out.append({"field": field})
            else:
                out.append({"field": field, "now": now[key], "then": then[key]})
    then = _url_channels(profile.get("channel_url") or "")
    now = _url_channels(current.get("channel_url") or "")
    for i in range(max(len(then), len(now))):
        if i >= len(then) or i >= len(now) or then[i] != now[i]:
            out.append({"channel": i})
    return sorted(out, key=lambda d: (d.get("field", "~"), d.get("channel", 0)))


def _matches_profile(folder: Path, iface) -> bool | None:
    """Is the device as its profile says? None without a (readable) profile."""
    path = folder / "profile.yaml"
    if not path.exists():
        return None
    try:
        return not differences(yaml.safe_load(path.read_text(encoding="utf-8")), export(iface))
    except Exception:  # edited by hand into something else; the check says so
        return None


def _profile_checks(folder: Path, iface) -> list[dict]:
    path = folder / "profile.yaml"
    if not path.exists():
        text = _("Noch kein Profil: „Als Profil übernehmen“ hält den jetzigen Stand als Soll fest")
        return [{"level": "info", "text": text}]
    try:
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
        diffs = differences(profile, export(iface))
    except Exception as e:  # edited by hand into something else
        text = _("Profil nicht lesbar: {error}", error=f"{type(e).__name__}: {e}")
        return [{"level": "warn", "text": text}]
    if not diffs:
        date = f"{datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M}"
        return [{"level": "ok", "text": _("Entspricht dem Profil vom {date}", date=date)}]
    out = []
    for d in diffs[:MAX_DIFFERENCES]:
        if "channel" in d:
            text = _("Weicht vom Profil ab: Kanal {n}", n=d["channel"])
        elif "now" in d:
            text = _(
                "Weicht vom Profil ab: {field} ist {now}, im Profil {then}",
                field=d["field"],
                now=d["now"],
                then=d["then"],
            )
        else:
            text = _("Weicht vom Profil ab: {field}", field=d["field"])
        out.append({"level": "warn", "text": text})
    if len(diffs) > MAX_DIFFERENCES:
        text = _("… und {n} weitere Abweichungen vom Profil", n=len(diffs) - MAX_DIFFERENCES)
        out.append({"level": "warn", "text": text})
    return out


# ---------------------------------------------------------------- the check
def checks(ctx: Context, iface, chans: list[dict]) -> list[dict]:
    """Does the device fit what the app does with it? [{"level": ok | warn | info, "text"}]"""
    out = []

    def add(level: str, text: str) -> None:
        out.append({"level": level, "text": text})

    lora = iface.localNode.localConfig.lora
    preset = modem_preset(iface)
    if preset == DEFAULT_PRESET:
        add("ok", _("Preset {preset}, wie die Simulationen es annehmen", preset=preset))
    else:
        add(
            "warn",
            _(
                "Preset {preset}: Simulationen und Streckenrechnung nehmen ohne Angabe {default}",
                preset=preset,
                default=DEFAULT_PRESET,
            ),
        )
    if not lora.region:
        add("warn", _("Keine Region eingestellt: das Gerät sendet nicht"))
    if not lora.tx_enabled:
        add("warn", _("Senden ist abgeschaltet"))
    hops = lora.hop_limit or DEFAULT_HOPS
    if hops > DEFAULT_HOPS:
        add("warn", _("Hop-Limit {n}: Meshtastic empfiehlt 3, mehr belastet das Netz", n=hops))
    else:
        add("ok", _("Hop-Limit {n}", n=hops))

    coord = getattr(ctx, "coord", None)
    n = int(coord.settings["channel"]) if coord is not None else COORD_DEFAULTS["channel"]
    chan = next((c for c in chans if c["index"] == n), None)
    if chan is None:
        add(
            "warn",
            _("Kanal {n} fehlt: Rundgänge und Koordination brauchen einen privaten Kanal", n=n),
        )
    elif chan["private"]:
        add("ok", _("Kanal {n} ist privat ({key})", n=n, key=chan["key_label"]))
    else:
        add(
            "warn",
            _(
                "Kanal {n} ist nicht privat ({key}): Rundgänge und Koordination wären mitlesbar",
                n=n,
                key=chan["key_label"],
            ),
        )
    for c in chans:
        if not c["private"] and c["position_bits"] == 32:
            add(
                "warn",
                _(
                    "Kanal {n} sendet die genaue Position, und jeder kann sie lesen ({key})",
                    n=c["index"],
                    key=c["key_label"],
                ),
            )
    return out + _profile_checks(_dir(ctx, iface), iface)


# ---------------------------------------------------------------- writing
def _values(iface) -> dict:
    """The writable settings as the device has them (the role by its name, hop limit 0 as
    the 3 the firmware then takes)."""
    node, user = iface.localNode, _user(iface)
    out = {}
    for name, (where, section, attr) in FIELDS.items():
        if where == "owner":
            out[name] = user.get(attr, "")
        else:
            root = node.localConfig if where == "config" else node.moduleConfig
            out[name] = getattr(getattr(root, section), attr)
    out["hop_limit"] = out["hop_limit"] or DEFAULT_HOPS
    out["role"] = Role.Name(out["role"])
    return out


def _span(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    return f"{seconds // 60} min" if seconds % 60 == 0 else f"{seconds} s"


def _show(name: str, value) -> str:
    """A value as the preview names it."""
    if isinstance(value, bool):
        return _("an") if value else _("aus")
    if name.endswith("_s"):
        return _span(value) if value else _("Standard der Firmware")
    if name == "tx_power":
        return f"{value} dBm" if value else _("Maximum der Region")
    return str(value)


def form(iface) -> list[dict]:
    """The writable settings as forms per section of the view, set to the device's values."""
    v = _values(iface)

    def switch(name: str, label: str) -> Setting:
        return Setting(name, label, "bool", v[name])

    def interval(name: str, label: str) -> Setting:
        seconds = sorted({*INTERVALS, v[name]} - {0})
        options = [["0", _("Standard der Firmware")]] + [[str(s), _span(s)] for s in seconds]
        return Setting(name, label, "select", str(v[name]), options=options)

    roles = [r.name for r in Role.DESCRIPTOR.values if not r.GetOptions().deprecated]
    if v["role"] not in roles:
        roles.append(v["role"])
    hops = [[str(n), _("3 (empfohlen)") if n == DEFAULT_HOPS else str(n)] for n in range(1, 8)]
    sections = {
        "radio": [
            Setting(
                "hop_limit",
                _("Hop-Limit"),
                "select",
                str(v["hop_limit"]),
                options=hops,
                help=_("Über wie viele Knoten die Pakete des Geräts höchstens weitergehen"),
            ),
            Setting(
                "tx_power",
                _("Sendeleistung [dBm]"),
                "number",
                v["tx_power"],
                min=0,
                max=30,
                step=1,
                help=_("0 = das Maximum, das die Region erlaubt"),
            ),
        ],
        "telemetry": [
            switch("telemetry_device", _("Gerätewerte ins Netz senden (Akku, Funklast)")),
            interval("telemetry_device_s", _("Abstand der Gerätewerte")),
            switch("telemetry_environment", _("Umweltwerte messen und senden")),
            interval("telemetry_environment_s", _("Abstand der Umweltwerte")),
            switch("telemetry_power", _("Strom und Spannung messen und senden")),
            interval("telemetry_power_s", _("Abstand der Stromwerte")),
        ],
        "identity": [
            Setting("long_name", _("Langname"), "text", v["long_name"]),
            Setting("short_name", _("Kurzname (höchstens 4 Zeichen)"), "text", v["short_name"]),
            Setting("role", _("Rolle"), "select", v["role"], options=[[r, r] for r in roles]),
        ],
    }
    return [{"id": k, "settings": [s.to_json() for s in ss]} for k, ss in sections.items()]


def _labels(iface) -> dict:
    return {s["name"]: s["label"] for section in form(iface) for s in section["settings"]}


def _typed(name: str, raw, label: str):
    """A value from the page in the type of its field; ValueError says what is wrong."""
    unknown = ValueError(_("{label}: unbekannter Wert {value}", label=label, value=raw))
    if name in NAME_BYTES:
        text = str(raw).strip()
        if not text:
            raise ValueError(_("{label}: darf nicht leer sein", label=label))
        if len(text.encode("utf-8")) > NAME_BYTES[name]:
            raise ValueError(
                _(
                    "{label}: zu lang (höchstens {n} Zeichen; Umlaute und Emojis zählen mehrfach)",
                    label=label,
                    n=NAME_BYTES[name],
                )
            )
        return text
    if name == "role":
        if raw not in Role.keys():
            raise unknown
        return raw
    if name.startswith("telemetry_") and not name.endswith("_s"):
        if not isinstance(raw, bool):
            raise unknown
        return raw
    try:
        value = int(raw)
        whole = value == float(raw)
    except (TypeError, ValueError):
        raise unknown from None
    low, high = {"hop_limit": (1, 7), "tx_power": (0, 30)}.get(name, (0, MAX_INTERVAL_S))
    if not whole or not low <= value <= high:
        raise unknown
    return value


def _parse(iface, changes) -> dict:
    """The wanted changes, checked and typed, in the order of FIELDS; what the device has
    already is not a change."""
    if not isinstance(changes, dict):
        raise ValueError(_("Keine Änderung: das Gerät hat diese Werte schon"))
    for name in changes:
        if name not in FIELDS:
            raise KeyError(_("unbekannte Einstellung {name}", name=name))
    now, labels = _values(iface), _labels(iface)
    out = {}
    for name in FIELDS:
        if name in changes:
            value = _typed(name, changes[name], labels[name])
            if value != now[name]:
                out[name] = value
    if not out:
        raise ValueError(_("Keine Änderung: das Gerät hat diese Werte schon"))
    return out


def locked(iface) -> str:
    """Why the device's settings can't be written from here at all, or ""."""
    if iface.localNode.localConfig.security.is_managed:
        return _(
            "Das Gerät ist im verwalteten Modus: seine Einstellungen lassen sich nur per "
            "Fernverwaltung ändern"
        )
    return ""


def _refuse(ctx: Context, iface) -> None:
    reason = locked(iface)
    if reason:
        raise ValueError(reason)
    if ctx.jobs is not None and ctx.jobs.running("probe"):
        raise ValueError(_("Ein Traceroute-Rundgang läuft: erst stoppen"))


def _notes(ctx: Context, iface, wanted: dict) -> list[dict]:
    """What follows from the write: [{"level": warn | info, "text"}]"""
    out = []

    def add(level: str, text: str) -> None:
        out.append({"level": level, "text": text})

    simulated = ctx.device.simulate is not None
    hops = wanted.get("hop_limit", 0)
    if hops > DEFAULT_HOPS:
        add("warn", _("Hop-Limit {n}: Meshtastic empfiehlt 3, mehr belastet das Netz", n=hops))
    if wanted.get("role") in ROUTER_ROLES:
        add(
            "warn",
            _(
                "Router-Rollen sind für hoch und frei gelegene Standorte gedacht; an anderen "
                "verschlechtern sie das Netz"
            ),
        )
    if ctx.coord is not None and ctx.coord.enabled and not simulated:
        add(
            "warn",
            _("Die Koordination ist an: während des Neustarts gehen keine Funksprüche raus"),
        )
    if simulated:
        add("info", _("Simulation: es wird nichts gesendet und nichts neu gestartet"))
    else:
        add(
            "info",
            _(
                "Das Gerät startet danach neu; die Verbindung kommt nach etwa {s} s von selbst "
                "wieder",
                s=int(RESTART_S),
            ),
        )
    if "long_name" in wanted or "short_name" in wanted:
        add("info", _("Den neuen Namen meldet das Gerät allen Knoten im Netz"))
    add("info", _("Vorher wird ein Backup geschrieben"))
    matches = _matches_profile(_dir(ctx, iface), iface)
    if matches:
        add("info", _("Das Profil wird auf den neuen Stand gebracht"))
    elif matches is False:
        add("info", _("Das Profil bleibt, wie es ist: das Gerät wich schon vorher davon ab"))
    return out


def preview(ctx: Context, changes) -> dict:
    """What a write of these changes would do: the changes as old and new, and the notes."""
    iface = _iface(ctx)
    _refuse(ctx, iface)
    wanted = _parse(iface, changes)
    now, labels = _values(iface), _labels(iface)
    return {
        "changes": [
            {"name": n, "label": labels[n], "old": _show(n, now[n]), "new": _show(n, v)}
            for n, v in wanted.items()
        ],
        "notes": _notes(ctx, iface, wanted),
    }


def write(ctx: Context, changes) -> dict:
    """Write the changes to the device. A real node reboots after the commit: the link is
    restarted ({"restart_s"}) and the view after it carries the result. The simulated one
    doesn't: {"restart_s": 0, "view"}."""
    iface = _iface(ctx)
    _refuse(ctx, iface)
    wanted = _parse(iface, changes)
    dev, folder, node = ctx.device, _dir(ctx, iface), iface.localNode
    simulated = dev.simulate is not None
    matched = _matches_profile(folder, iface)
    _auto_backup(folder, iface)
    user = _user(iface)
    sections = []
    try:
        node.beginSettingsTransaction()
        if "long_name" in wanted or "short_name" in wanted:
            node.setOwner(
                long_name=wanted.get("long_name", user.get("longName")),
                short_name=wanted.get("short_name", user.get("shortName")),
                is_licensed=bool(user.get("isLicensed")),  # setOwner would clear it
            )
        for name, value in wanted.items():
            where, section, attr = FIELDS[name]
            if where == "owner":
                continue
            root = node.localConfig if where == "config" else node.moduleConfig
            setattr(getattr(root, section), attr, Role.Value(value) if name == "role" else value)
            if section not in sections:
                sections.append(section)
        for section in sections:
            if not simulated:
                time.sleep(PAUSE_S)
            node.writeConfig(section)
        node.commitSettingsTransaction()
    except Exception as e:
        if not simulated:  # our copy of the configuration is no longer the node's
            dev.restart(RESTART_S)
        error = f"{type(e).__name__}: {e}"
        raise ValueError(
            _("Schreiben fehlgeschlagen ({error}): die Verbindung wird neu aufgebaut", error=error)
        ) from e
    dev.config_write = {
        "node": user.get("id"),
        "time": time.time(),
        "wanted": wanted,
        "connects": dev.connects,
        "profile": matched is True,
        "checked": False,
    }
    if simulated:
        return {"restart_s": 0, "view": view(ctx)}
    dev.restart(RESTART_S)
    return {"restart_s": RESTART_S}


def _result(ctx: Context, iface) -> dict | None:
    """Did the node take the last write? Known once its configuration was read anew (the
    simulated radio has no second reading). A write the node took also moves the profile, if
    the node matched it before."""
    dev = ctx.device
    w = dev.config_write
    if w is None or w["node"] != _user(iface).get("id"):
        return None
    if dev.simulate is None and dev.connects == w["connects"]:
        return None
    now, labels = _values(iface), _labels(iface)
    failed = [n for n, v in w["wanted"].items() if now[n] != v]
    if not w["checked"]:
        w["checked"] = True
        if not failed and w["profile"]:
            _save_profile(_dir(ctx, iface), iface)
    at = f"{datetime.fromtimestamp(w['time']):%H:%M}"
    if failed:
        text = _(
            "Um {time} geschrieben, aber vom Gerät nicht übernommen: {fields}",
            time=at,
            fields=", ".join(labels[n] for n in failed),
        )
        return {"ok": False, "text": text}
    text = _(
        "Um {time} geschrieben und vom Gerät bestätigt: {fields}",
        time=at,
        fields=", ".join(labels[n] for n in w["wanted"]),
    )
    return {"ok": True, "text": text}


# ---------------------------------------------------------------- the page's data
def _sections(config: dict) -> list[dict]:
    """Every setting of a config dict per section, secrets masked."""
    out = []
    for name, fields in config.items():
        rows = []
        for key, value in _flat(fields).items():
            if key.rsplit(".", 1)[-1] in SECRET:
                value = MASK if value else ""
            elif isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            rows.append([key, value])
        out.append({"name": name, "fields": rows})
    return out


def telemetry(module_config) -> list[dict]:
    """What the node measures and sends to the mesh by itself: kind, on, interval [s]
    (0: the firmware's default)."""
    t = module_config.telemetry
    return [
        {
            "kind": "device",
            "on": t.device_telemetry_enabled,
            "interval_s": t.device_update_interval,
        },
        {
            "kind": "environment",
            "on": t.environment_measurement_enabled,
            "interval_s": t.environment_update_interval,
        },
        {"kind": "power", "on": t.power_measurement_enabled, "interval_s": t.power_update_interval},
        {"kind": "air_quality", "on": t.air_quality_enabled, "interval_s": t.air_quality_interval},
        {
            "kind": "health",
            "on": t.health_measurement_enabled,
            "interval_s": t.health_update_interval,
        },
    ]


def view(ctx: Context) -> dict:
    """Everything the view Gerät shows of the connected device's configuration."""
    iface = _iface(ctx)
    cfg = iface.localNode.localConfig
    lora, user = cfg.lora, _user(iface)
    chans = channels(iface.localNode)
    result = _result(ctx, iface)  # first: it may move the profile the check compares with
    written = []
    if result:
        written = [{"level": "ok" if result["ok"] else "warn", "text": result["text"]}]
    return {
        "node": user.get("id"),
        "form": form(iface),
        "locked": locked(iface),
        "result": result,
        "radio": {
            "preset": modem_preset(iface),
            "region": config_pb2.Config.LoRaConfig.RegionCode.Name(lora.region),
            "hop_limit": lora.hop_limit or DEFAULT_HOPS,
            "tx_power": lora.tx_power,  # 0: the most the region allows
            "slot": lora.channel_num,  # 0: from the primary channel's name
            "tx_enabled": lora.tx_enabled,
        },
        "identity": {
            "long": user.get("longName", ""),
            "short": user.get("shortName", ""),
            "role": Role.Name(cfg.device.role),
            "rebroadcast": config_pb2.Config.DeviceConfig.RebroadcastMode.Name(
                cfg.device.rebroadcast_mode
            ),
        },
        "security": {
            "public_key": base64.b64encode(cfg.security.public_key).decode(),
            "admin_keys": len(cfg.security.admin_key),
            "managed": cfg.security.is_managed,
        },
        "channels": chans,
        "telemetry": telemetry(iface.localNode.moduleConfig),
        "checks": written + checks(ctx, iface, chans),
        "all": _sections(_plain(cfg))
        + [
            {**s, "name": f"module_config.{s['name']}"}
            for s in _sections(_plain(iface.localNode.moduleConfig))
        ],
        "files": _files(_dir(ctx, iface)),
    }
