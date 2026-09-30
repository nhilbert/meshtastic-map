"""Settings of the coordination mode: defaults, the form the page builds, validation."""

from __future__ import annotations

from meshplay.mapapp.i18n import _
from meshplay.mapapp.registry import Setting

PROFILES = ["foot", "bike", "car"]
INSTRUCTIONS = ["request", "turns", "interval"]
MARKERS = ["channel", "missions", "off"]
# a node counts as on the private channel this long after a packet came with the channel's key;
# nodes broadcast their node info every 3 h by default
CHANNEL_PROOF_S = 4 * 3600
DEFAULTS = {
    "lang": "de",
    "channel": 1,
    "osm_name": "roads",
    "profile": "foot",
    "arrive_radius_m": 30,
    "off_route_m": 75,
    "min_gap_s": 120,
    "instructions": "turns",
    "legs_per_message": 3,
    "confirm_every_min": 0,
    "late_warn_min": 3,
    "stale_min": 15,
    "request_positions": True,
    "min_precision_bits": 24,
    "speed_foot_kmh": 4.5,
    "speed_bike_kmh": 15,
    "speed_car_kmh": 30,
    "end_message": True,
    "send_legend": True,
    "markers": "channel",
}


def channel_options(channels: list[dict]) -> list[list]:
    """The device's channels for the select; every index while no device is known."""
    if channels:
        return [
            [str(c["index"]), f"{c['index']} {c['name'] or (_('primär') if c['primary'] else '')}"]
            for c in channels
        ]
    return [[str(i), str(i)] for i in range(8)]


def declarations(
    channels: list[dict] | None = None, graphs: list[str] | None = None
) -> list[Setting]:
    names = graphs or [DEFAULTS["osm_name"]]
    return [
        Setting(
            "osm_name",
            _("Straßennetz"),
            "select",
            DEFAULTS["osm_name"] if DEFAULTS["osm_name"] in names else names[0],
            options=[[n, n] for n in names],
            help=_("Graph aus data/osm/ (Aufgabe „Straßennetz laden“); ohne: Luftlinie"),
        ),
        Setting(
            "channel",
            _("Kanal der Funksprüche"),
            "select",
            str(DEFAULTS["channel"]),
            options=channel_options(channels or []),
            help=_("Der private Kanal, damit das öffentliche Netz nichts mitbekommt"),
        ),
        Setting(
            "lang",
            _("Sprache der Funksprüche"),
            "select",
            DEFAULTS["lang"],
            options=[["de", "Deutsch"], ["en", "English"]],
            help=_("Die Sprache der Person im Feld, nicht die der Seite"),
        ),
        Setting(
            "profile",
            _("Fortbewegung"),
            "select",
            DEFAULTS["profile"],
            options=[["foot", _("zu Fuß")], ["bike", _("Fahrrad")], ["car", _("Auto")]],
        ),
        Setting(
            "arrive_radius_m",
            _("Ankunftsradius [m]"),
            "number",
            DEFAULTS["arrive_radius_m"],
            min=5,
            max=500,
            step=5,
        ),
        Setting(
            "off_route_m",
            _("Abweichung vom Weg ab [m]"),
            "number",
            DEFAULTS["off_route_m"],
            min=20,
            max=1000,
            step=5,
        ),
        Setting(
            "min_gap_s",
            _("Mindestabstand ungefragter Funksprüche [s]"),
            "number",
            DEFAULTS["min_gap_s"],
            min=30,
            max=1800,
            step=10,
            help=_("Antworten auf Anfragen zählen nicht"),
        ),
        Setting(
            "instructions",
            _("Wegbeschreibung senden"),
            "select",
            DEFAULTS["instructions"],
            options=[
                ["request", _("nur auf Anfrage (?R)")],
                ["turns", _("vor jedem Abbiegen")],
                ["interval", _("alle 500 m")],
            ],
        ),
        Setting(
            "legs_per_message",
            _("Abschnitte je Funkspruch"),
            "number",
            DEFAULTS["legs_per_message"],
            min=1,
            max=6,
            step=1,
        ),
        Setting(
            "confirm_every_min",
            _("Bestätigung alle … min (0 = aus)"),
            "number",
            DEFAULTS["confirm_every_min"],
            min=0,
            max=60,
            step=1,
        ),
        Setting(
            "late_warn_min",
            _("Verspätung melden ab [min]"),
            "number",
            DEFAULTS["late_warn_min"],
            min=1,
            max=60,
            step=1,
        ),
        Setting(
            "stale_min",
            _("Position gilt als alt nach [min]"),
            "number",
            DEFAULTS["stale_min"],
            min=1,
            max=60,
            step=1,
            help=_("Mit Smart-Position meldet sich ein stehender Knoten nur alle 10 min"),
        ),
        Setting(
            "request_positions",
            _("Position beim Knoten anfragen, wenn eine fehlt"),
            "bool",
            DEFAULTS["request_positions"],
            help=_(
                "Nur an Knoten mit Einsatz, höchstens alle 3 min: nach der Zuweisung ohne "
                "Position, wenn der Knoten am Halt sein müsste, und wenn die Position veraltet "
                "ist. Die Firmware des Knotens antwortet von selbst."
            ),
        ),
        Setting(
            "min_precision_bits",
            _("Mindestgenauigkeit der Position [Bit]"),
            "number",
            DEFAULTS["min_precision_bits"],
            min=10,
            max=32,
            step=1,
            help=_("32 = volle Genauigkeit; unter 24 Bit ist die Position gröber als 3 m"),
        ),
        Setting(
            "speed_foot_kmh",
            _("Tempo zu Fuß [km/h]"),
            "number",
            DEFAULTS["speed_foot_kmh"],
            min=1,
            max=10,
            step=0.5,
        ),
        Setting(
            "speed_bike_kmh",
            _("Tempo Fahrrad [km/h]"),
            "number",
            DEFAULTS["speed_bike_kmh"],
            min=5,
            max=40,
            step=1,
        ),
        Setting(
            "speed_car_kmh",
            _("Tempo Auto [km/h]"),
            "number",
            DEFAULTS["speed_car_kmh"],
            min=10,
            max=130,
            step=5,
        ),
        Setting(
            "end_message",
            _("Beim Beenden eines Einsatzes den Knoten benachrichtigen"),
            "bool",
            DEFAULTS["end_message"],
        ),
        Setting(
            "send_legend",
            _("Mit der ersten Zuweisung eine Legende der Kürzel schicken"),
            "bool",
            DEFAULTS["send_legend"],
            help=_("Ein zweiter Funkspruch; der Knoten bekommt sie auch mit ?L"),
        ),
        Setting(
            "markers",
            _("Markierungen per Funk (+D, ?D)"),
            "select",
            DEFAULTS["markers"],
            options=[
                ["channel", _("von Knoten auf dem Kanal der Funksprüche")],
                ["missions", _("nur von Knoten mit Einsatz")],
                ["off", _("aus")],
            ],
            help=_(
                "+D NAME Text setzt ein Ziel an der eigenen Position, ?D NAME macht es zum "
                "Einsatz, ?D das nächste. Auf dem Kanal ist ein Knoten, von dem in den letzten "
                "{hours} Stunden ein Paket mit dem Schlüssel dieses Kanals kam (Position, "
                "Knoteninfo, Text); eine PKI-verschlüsselte Direktnachricht allein zeigt den "
                "Kanal nicht. Andere bekommen keine Antwort.",
                hours=CHANNEL_PROOF_S // 3600,
            ),
        ),
    ]


def clean(raw: dict, channels: list[dict] | None = None, graphs: list[str] | None = None) -> dict:
    """The settings as stored: parsed against the declarations, ranges checked."""
    out = {}
    for s in declarations(channels, graphs):
        v = raw.get(s.name)
        value = s.parse(None if v is None else str(v))
        if s.type == "number":
            if s.min is not None and value < s.min:
                raise ValueError(_("{label}: mindestens {n}", label=s.label, n=f"{s.min:g}"))
            if s.max is not None and value > s.max:
                raise ValueError(_("{label}: höchstens {n}", label=s.label, n=f"{s.max:g}"))
            if s.step == 1:
                value = int(value)
        elif s.type == "select" and value not in {o[0] for o in s.options}:
            raise ValueError(_("{label}: unbekannter Wert {value}", label=s.label, value=value))
        out[s.name] = int(value) if s.name == "channel" else value
    return out


def default_speed_ms(settings: dict, profile: str) -> float:
    return float(settings.get(f"speed_{profile}_kmh", DEFAULTS["speed_foot_kmh"])) / 3.6
