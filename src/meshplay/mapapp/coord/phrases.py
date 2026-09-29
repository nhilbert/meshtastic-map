"""Radio texts of the coordination mode and the commands a field node can send.

The texts go over the mesh, so they are short and coded (docs/coordination-design.md §3):
"#NAME" the current stop, "!" a warning, "i" an information, "R:" a route, compass letters in
English everywhere. They follow the mission's language, not the page's, so they have their
own catalogue here instead of the UI catalogues.
"""

from __future__ import annotations

from meshplay.mapapp.messages import MAX_TEXT_BYTES

TARGET_BYTES = 80  # what a message should stay under; MAX_TEXT_BYTES is the hard limit
LANGS = ("de", "en")

PHRASES = {
    "de": {
        "assign": "#{target} {dist} {dir} {eta}",
        "assign_by": "#{target} {dist} {dir} {eta} bis {time} ({margin})",
        "assign_nopos": "#{target} zugewiesen, keine Position von dir",
        "route": "R: {legs}",
        "status": "#{target} {dist} {dir} {eta} {speed}",
        "status_by": "#{target} {dist} {dir} {eta} {speed} bis {time} ({margin})",
        "target": "#{target} {dist} {dir}",
        "path": "{stops}",
        "offcourse": "!KURS {off} ab. {dir} {dist}",
        "offcourse_route": "!KURS {off} ab. R: {legs}",
        "late": "!SPAET {target} {eta_time} statt {time}",
        "early": "!FRUEH {target} zurück, warten bis {time}",
        "confirm": "#{target} ok {dist} {eta}",
        "reached_hold": "#{target} erreicht {time} {travelled} {duration}. Warten bis {until}",
        "reached_next": "#{target} erreicht {time}. Weiter #{next} {dist} {dir} {eta}",
        "reached_final": "#{target} erreicht {time} {travelled} {duration}",
        "next": "Weiter #{target} {dist} {dir} {eta}",
        "changed": "#{target} neu {dist} {dir} {eta}",
        "ended": "#{target} aufgehoben",
        "halt_ok": "HALT ok",
        "resume": "Weiter #{target} {dist} {dir}",
        "aborted": "#{target} abgebrochen",
        "help": "? Status ?R Route ?Z Ziel ?P Pfad HALT GO X",
    },
    "en": {
        "assign": "#{target} {dist} {dir} {eta}",
        "assign_by": "#{target} {dist} {dir} {eta} by {time} ({margin})",
        "assign_nopos": "#{target} assigned, no position from you",
        "route": "R: {legs}",
        "status": "#{target} {dist} {dir} {eta} {speed}",
        "status_by": "#{target} {dist} {dir} {eta} {speed} by {time} ({margin})",
        "target": "#{target} {dist} {dir}",
        "path": "{stops}",
        "offcourse": "!OFF {off} away. {dir} {dist}",
        "offcourse_route": "!OFF {off} away. R: {legs}",
        "late": "!LATE {target} {eta_time} not {time}",
        "early": "!EARLY {target} go back, wait until {time}",
        "confirm": "#{target} ok {dist} {eta}",
        "reached_hold": "#{target} reached {time} {travelled} {duration}. Wait until {until}",
        "reached_next": "#{target} reached {time}. Next #{next} {dist} {dir} {eta}",
        "reached_final": "#{target} reached {time} {travelled} {duration}",
        "next": "Next #{target} {dist} {dir} {eta}",
        "changed": "#{target} new {dist} {dir} {eta}",
        "ended": "#{target} cancelled",
        "halt_ok": "HALT ok",
        "resume": "Next #{target} {dist} {dir}",
        "aborted": "#{target} aborted",
        "help": "? status ?R route ?Z target ?P path HALT GO X",
    },
}

COMMANDS = {
    "?": "status",
    "?r": "route",
    "?z": "target",
    "?p": "path",
    "?h": "help",
    "ok": "ok",
    "halt": "halt",
    "go": "go",
    "x": "abort",
}


def phrase(lang: str, key: str, **params) -> str:
    """The text for `key` with the parameters filled in; empty parameters leave no gaps."""
    lang = lang if lang in PHRASES else "de"
    text = PHRASES[lang][key].format(**params)
    return " ".join(text.split())


def fits(text: str, limit: int = MAX_TEXT_BYTES) -> bool:
    return len(text.encode("utf-8")) <= limit


def parse_command(text: str) -> str | None:
    """A field node's command, or None for ordinary chat. Unknown "?…" asks for help."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in COMMANDS:
        return COMMANDS[t]
    if t.startswith("?"):
        return "help"
    return None
