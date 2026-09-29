"""Every text marked for translation has an English and a French entry, and nothing stale.

Marked texts: _("…"), N_("…"), L("…") in src/meshplay/mapapp (Python), t("…") in webmap/js
(also inside comments, which is how codes like task states are listed), data-i18n* and <title>
in webmap/index.html. Run `python tests/test_i18n.py` to print the texts missing per language.
"""

import ast
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LANGS = ("en", "fr")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def python_texts() -> set[str]:
    out = set()
    for path in (ROOT / "src" / "meshplay" / "mapapp").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("_", "N_", "L")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                out.add(node.args[0].value)
    return out


JS_CALL = re.compile(r'(?<![\w.$])t\(\s*"((?:[^"\\]|\\.)*)"')


def js_texts() -> set[str]:
    out = set()
    for path in (ROOT / "webmap" / "js").glob("*.js"):
        for m in JS_CALL.finditer(path.read_text(encoding="utf-8")):
            out.add(json.loads(f'"{m[1]}"'))
    return out


class _Marked(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts, self._stack = set(), []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k, v in a.items():
            if k.startswith("data-i18n-") and v:
                self.texts.add(v)
        self._stack.append(tag == "title" or "data-i18n" in a)

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._stack and self._stack[-1] and data.strip():
            self.texts.add(data.strip())


def html_texts() -> set[str]:
    parser = _Marked()
    parser.feed((ROOT / "webmap" / "index.html").read_text(encoding="utf-8"))
    return parser.texts


def all_texts() -> set[str]:
    return python_texts() | js_texts() | html_texts()


def catalogue(lang: str) -> dict[str, str]:
    return json.loads((ROOT / "webmap" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def test_texts_are_found():
    texts = all_texts()
    assert len(texts) > 300
    assert {"Nachrichten", "Traceroute-Rundgang", "Eigene Standorte", "Ebenen"} <= texts


@pytest.mark.parametrize("lang", LANGS)
def test_catalogue_complete(lang):
    cat = catalogue(lang)
    missing = sorted(all_texts() - cat.keys())
    assert not missing, f"{len(missing)} texts without {lang} translation: {missing[:20]}"
    empty = [k for k, v in cat.items() if not v.strip()]
    assert not empty, f"empty {lang} translations: {empty[:10]}"


@pytest.mark.parametrize("lang", LANGS)
def test_catalogue_has_no_stale_entries(lang):
    stale = sorted(catalogue(lang).keys() - all_texts())
    assert not stale, f"{lang}.json has entries no code uses: {stale[:20]}"


@pytest.mark.parametrize("lang", LANGS)
def test_placeholders_match(lang):
    wrong = [
        (k, v)
        for k, v in catalogue(lang).items()
        if set(PLACEHOLDER.findall(k)) != set(PLACEHOLDER.findall(v))
    ]
    assert not wrong, f"placeholders differ in {lang}: {wrong[:10]}"


def test_server_translates_per_request():
    from meshplay.mapapp.i18n import L, _, set_lang

    title = L("Traceroute-Rundgang {to}", to="!abcd1234")
    set_lang("fr")
    fr = _("Nachrichten")
    shown_fr = str(title)
    set_lang("de")
    assert _("Nachrichten") == "Nachrichten"
    assert fr != "Nachrichten" and "!abcd1234" in shown_fr
    assert str(title) == "Traceroute-Rundgang !abcd1234"
    set_lang("xx")  # unknown language: German
    assert _("Nachrichten") == "Nachrichten"


if __name__ == "__main__":
    texts = all_texts()
    for lang in LANGS:
        path = ROOT / "webmap" / "i18n" / f"{lang}.json"
        cat = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        missing = sorted(texts - cat.keys())
        print(f"{lang}: {len(missing)} missing")
        for m in missing:
            print("  ", json.dumps(m, ensure_ascii=False))
