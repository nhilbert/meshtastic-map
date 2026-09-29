"""Translations of the map app's texts (German source, English and French catalogues).

Texts are written in German in the code and marked with _() (translated now, in the language
of the current request) or L() (translated when shown: for texts that are stored, like task
titles, and outlive a language switch). The catalogues are webmap/i18n/<lang>.json, shared with
the page: {"German text with {placeholders}": "translation"}. A missing entry falls back to the
German text. tests/test_i18n.py checks that every marked text has a translation.

The server sets the language per request from the page's X-Lang header (set_lang); background
tasks run in the language of the request that started them.
"""

from __future__ import annotations

import contextvars
import json
import re
from functools import cache
from pathlib import Path

LANGS = ("de", "en", "fr")
DEFAULT = "de"
CATALOGUE_DIR = Path(__file__).resolve().parents[3] / "webmap" / "i18n"
_lang: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default=DEFAULT)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@cache
def catalogue(lang: str) -> dict[str, str]:
    path = CATALOGUE_DIR / f"{lang}.json"
    if lang == DEFAULT or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def set_lang(lang: str | None) -> str:
    lang = (lang or "").lower()[:2]
    lang = lang if lang in LANGS else DEFAULT
    _lang.set(lang)
    return lang


def get_lang() -> str:
    return _lang.get()


def fill(text: str, params: dict) -> str:
    """Replace {name} with params[name]; other braces stay as they are."""
    return _PLACEHOLDER.sub(lambda m: str(params[m[1]]) if m[1] in params else m[0], text)


def N_(text: str) -> str:
    """Marks a text for the catalogue without translating it (class attributes, constants);
    translate it with _() where it is shown."""
    return text


def _(text: str, **params) -> str:
    """The text in the current language, placeholders filled in (L values translated too)."""
    translated = catalogue(get_lang()).get(text, text)
    return fill(translated, {k: str(v) if isinstance(v, L) else v for k, v in params.items()})


class L:
    """A text translated when it is shown, e.g. a stored task title."""

    def __init__(self, text: str, **params):
        self.text, self.params = text, params

    def __str__(self) -> str:
        return _(self.text, **self.params)

    def to_json(self) -> dict:
        return {
            "t": self.text,
            "p": {k: v.to_json() if isinstance(v, L) else v for k, v in self.params.items()},
        }

    @classmethod
    def from_json(cls, value):
        """Inverse of to_json; plain strings (older files) stay strings."""
        if isinstance(value, dict) and "t" in value:
            return cls(value["t"], **{k: cls.from_json(v) for k, v in value.get("p", {}).items()})
        return value
