"""The tile cache: serve from disk, fetch once, cope with being offline."""

import io
import urllib.error
from types import SimpleNamespace

from meshplay.mapapp import tiles


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_cache_fetches_once(tmp_path, monkeypatch):
    calls = []

    def urlopen(req, timeout):
        calls.append((req.full_url, req.headers.get("User-agent")))
        return FakeResponse(b"PNG" + req.full_url.encode())

    monkeypatch.setattr(tiles.urllib.request, "urlopen", urlopen)
    cache = tiles.TileCache(tmp_path / "tiles")
    data = cache.get(15, 17000, 11000)
    assert data.startswith(b"PNG") and b"/15/17000/11000.png" in data
    assert cache.get(15, 17000, 11000) == data  # from disk now
    assert len(calls) == 1 and "meshplay" in calls[0][1]
    assert cache.path(15, 17000, 11000).exists() and cache.count() == 1
    assert cache.get(20, 0, 0) is None and cache.get(3, 9, 0) is None  # out of range
    assert not calls[1:]


def test_offline_backs_off(tmp_path, monkeypatch):
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(tiles.urllib.request, "urlopen", urlopen)
    cache = tiles.TileCache(tmp_path / "tiles")
    assert cache.get(12, 2100, 1380) is None
    assert cache.online is False
    assert cache.get(12, 2101, 1380) is None  # no second attempt for a minute
    assert len(calls) == 1
    cache._retry_after = 0
    assert cache.get(12, 2101, 1380) is None and len(calls) == 2


def test_server_serves_tiles(tmp_path, monkeypatch):
    """The handler answers /tiles/z/x/y.png from the cache and 404 when unavailable."""
    from meshplay.config import Settings
    from meshplay.mapapp.registry import Context
    from meshplay.mapapp.server import make_handler

    ctx = Context(Settings(port=None, data_dir=tmp_path, log_level="INFO", home=None))
    ctx.tiles = tiles.TileCache(tmp_path / "tiles")
    monkeypatch.setattr(tiles.urllib.request, "urlopen", lambda req, timeout: FakeResponse(b"PNGX"))
    handler = make_handler(ctx, None)
    sent = {}

    class Probe(handler):
        def __init__(self, path):
            self.path, self.headers, self.wfile = path, {}, io.BytesIO()
            self.requestline, self.client_address, self.request_version = "", ("", 0), "HTTP/1.1"

        def send_response(self, code, message=None):
            sent["code"] = code

        def send_header(self, k, v):
            sent.setdefault("headers", {})[k] = v

        def end_headers(self):
            pass

        def send_error(self, code, message=None):
            sent["code"] = code

        def log_message(self, *a):
            pass

    p = Probe("/tiles/15/17000/11000.png")
    p.do_GET()
    assert sent["code"] == 200 and p.wfile.getvalue() == b"PNGX"
    assert sent["headers"]["Content-Type"] == "image/png"
    monkeypatch.setattr(
        tiles.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(OSError())
    )
    Probe("/tiles/15/17001/11000.png").do_GET()
    assert sent["code"] == 404
    Probe("/tiles/15/x/11000.png").do_GET()
    assert sent["code"] == 404
    assert SimpleNamespace  # keep the import used for readers of this file
