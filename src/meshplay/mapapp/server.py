"""HTTP server for the map app: static page (webmap/), 3D scene files, layer and tool API.

GET  /                        webmap/index.html (and css/, js/)
GET  /scene/<file>            scene_meta.json, terrain.i16, bodies.bin for the 3D view
GET  /api/app                 home position, scene extent, layer list with settings
GET  /api/layers/<id>?...     layer data for the given settings (GeoJSON or raster)
POST /api/tools/<name>        run a tool, e.g. link
GET  /api/device              connection status of the USB device
POST /api/device/connect      {"port": "COM8"} or {} for auto-detect; /api/device/disconnect
GET  /api/jobs                background tasks; /api/jobs/kinds: task kinds with their forms
GET  /api/jobs/<id>           one task with its log
POST /api/jobs                {"kind", "params"} starts a task; /api/jobs/<id>/cancel, /remove
GET  /api/sites               own sites for the editor; /api/sites/suggest?lat=&lon= clutter
POST /api/sites/<action>      add, update, rename, delete (data/sim/sites.json)
POST /api/tracks?name=x.gpx   upload a GPX track (raw body) to data/tracks/
GET  /api/messages?rev=N      messages (and with traffic=1 recent packets) newer than revision N
POST /api/messages            {"text", "to": "!id" or "^all", "channel"} sends a text
GET/POST /api/coord/...       coordination mode (dispatched in coord/missions.py)
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from meshplay.config import DEFAULT_PRESET
from meshplay.mapapp import sites_store
from meshplay.mapapp.coord.missions import Coordinator
from meshplay.mapapp.device import DeviceLink, Simulation
from meshplay.mapapp.i18n import _, set_lang
from meshplay.mapapp.jobs import JobManager
from meshplay.mapapp.layers import ALL, BY_ID
from meshplay.mapapp.registry import Context
from meshplay.mapapp.tiles import TileCache
from meshplay.mapapp.tools import link as link_tool

WEB_DIR = Path(__file__).resolve().parents[3] / "webmap"
TOOLS = {"link": link_tool.run}
PRESETS = ["LongFast", "MediumSlow", "MediumFast", "ShortSlow", "ShortFast", "LongSlow"]
SCENE_FILES = ("scene_meta.json", "terrain.i16", "bodies.bin")


def ensure_scene_export(ctx: Context, force: bool = False) -> Path | None:
    """Export the 3D view data when the scene is newer than the last export."""
    if not ctx.has_scene:
        return None
    from meshplay.sim.view3d import export_scene

    out = ctx.app_dir / "scene"
    src_meta = ctx.sim_dir / "scene" / "scene_meta.json"
    dst_meta = out / "scene_meta.json"
    if force or not dst_meta.exists() or dst_meta.stat().st_mtime < src_meta.stat().st_mtime:
        print("Exporting the scene for the 3D view (about a minute) ...", flush=True)
        meta = export_scene(ctx.scene, out)
        print(
            f"  {meta['n_buildings']} buildings, {meta['n_bodies'] - meta['n_buildings']} "
            "vegetation bodies",
            flush=True,
        )
    return out


def app_info(ctx: Context) -> dict:
    from meshplay.sim.sites import to_lonlat

    info = dict(
        presets=PRESETS,
        default_preset=DEFAULT_PRESET,
        layers=[layer.describe(ctx) for layer in ALL],
        scene=None,
    )
    if ctx.settings.home:
        info["home"] = {"lat": ctx.settings.home[0], "lon": ctx.settings.home[1]}
    elif ctx.sites:
        s = next(iter(ctx.sites.values()))
        info["home"] = {"lat": s["lat"], "lon": s["lon"]}
    if ctx.has_scene:
        meta = json.loads((ctx.sim_dir / "scene" / "scene_meta.json").read_text(encoding="utf-8"))
        b = meta["bbox"]
        (w, s_), (e, n) = to_lonlat(b[0], b[1]), to_lonlat(b[2], b[3])
        info["scene"] = {"bbox_utm": b, "bounds": [[s_, w], [n, e]]}
    return info


def edit_sites(ctx: Context, action: str, body: dict) -> dict:
    path = ctx.sites_path
    if action == "add":
        cfg = sites_store.add_site(
            path,
            body.get("name", ""),
            body["lat"],
            body["lon"],
            body.get("height_m", [3, 6]),
            body.get("clutter_m", 12),
            body.get("description", ""),
        )
    elif action == "update":
        cfg = sites_store.update_site(path, body["name"], body.get("fields") or {})
    elif action == "rename":
        cfg = sites_store.rename_site(path, body["name"], body.get("new_name", ""))
    elif action == "delete":
        cfg = sites_store.delete_site(path, body["name"])
    else:
        raise KeyError(_("unbekannte Aktion {action}", action=action))
    ctx.reload_sites()
    return cfg


def messages_since(ctx: Context, rev: int, traffic: str | None) -> dict:
    """Everything the messaging pane needs that changed after revision `rev`."""
    dev = ctx.device
    store = dev.messages
    messages = store.since(rev)
    packets = store.traffic_since(rev) if traffic else []
    ids = {m["from"] for m in messages + packets} | {m["to"] for m in messages + packets}
    status = dev.status()
    return {
        "rev": store.rev,
        "state": status["state"],
        "me": status.get("me"),
        "channels": dev.channels(),
        "messages": messages,
        "traffic": packets,
        "names": dev.names(ids),
    }


def save_track(ctx: Context, name: str, data: bytes) -> dict:
    """Store an uploaded GPX track in data/tracks/ after checking it has timestamped points."""
    from meshplay.walk import load_gpx

    stem = re.sub(r"[^\w .()-]", "_", Path(name).stem).strip() or "track"
    if Path(name).suffix.lower() != ".gpx":
        raise ValueError(_("Nur .gpx-Dateien"))
    out = ctx.data_dir / "tracks" / f"{stem}.gpx"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".upload")
    tmp.write_bytes(data)
    try:
        track = load_gpx(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise ValueError(_("Keine lesbare GPX-Datei: {error}", error=e)) from None
    if not track:
        tmp.unlink(missing_ok=True)
        raise ValueError(_("Die GPX-Datei hat keine Punkte mit Zeitstempel"))
    tmp.replace(out)
    return {
        "name": out.name,
        "points": len(track),
        "start": track[0]["time"].isoformat(),
        "end": track[-1]["time"].isoformat(),
    }


def suggest_site(ctx: Context, lat: float, lon: float) -> dict:
    """Defaults for a new site at lat/lon: clutter = highest surface within 10 m (the roof)."""
    out = {"in_scene": False, "clutter_m": 12.0, "ground_m": None}
    if ctx.has_scene:
        from meshplay.sim.sites import to_utm

        x, y = to_utm(lon, lat)
        if ctx.scene.contains(x, y, margin=15):
            out.update(
                in_scene=True,
                clutter_m=round(ctx.scene.max_height_near(x, y, radius=10.0), 1),
                ground_m=round(ctx.scene.ground_at(x, y), 1),
            )
    return out


def make_handler(ctx: Context, scene_dir: Path | None):
    lock = threading.Lock()  # the models and caches are not written for concurrent use

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1 with Content-Length on every response: keep-alive instead of closing the
        # socket after each file, which on Windows can reset large transfers.
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if not self.path.startswith(("/js/", "/css/", "/scene/", "/tiles/", "/vendor/")):
                super().log_message(fmt, *args)

        def send_json(self, obj, status=200):
            body = json.dumps(obj, allow_nan=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path: Path):
            if not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                ctype = "text/javascript"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def send_tile(self, parts: list[str]):
            """/tiles/z/x/y.png from the cache (or the tile server, once); 404 when neither."""
            try:
                z, x, y = int(parts[0]), int(parts[1]), int(parts[2].removesuffix(".png"))
            except ValueError:
                self.send_error(404)
                return
            data = ctx.tiles.get(z, x, y) if ctx.tiles is not None else None
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=2592000")  # the browser too
            self.end_headers()
            self.wfile.write(data)

        def fail(self, e: Exception):
            if isinstance(e, (ValueError, KeyError)):  # bad input: the message is for the user
                msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
                self.send_json({"error": str(msg)}, 400)
                return
            traceback.print_exc()
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            set_lang(self.headers.get("X-Lang"))  # texts and messages in the page's language
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            try:
                if parts[:2] == ["api", "app"]:
                    self.send_json(app_info(ctx))
                elif parts == ["api", "device"]:
                    self.send_json(ctx.device.status())
                elif parts == ["api", "jobs"]:
                    self.send_json({"jobs": ctx.jobs.list()})
                elif parts == ["api", "jobs", "kinds"]:
                    self.send_json({"kinds": ctx.jobs.describe_kinds()})
                elif parts[:2] == ["api", "jobs"] and len(parts) == 3:
                    self.send_json(ctx.jobs.get(parts[2]).to_json(with_log=True))
                elif parts == ["api", "messages"]:
                    q = dict(parse_qsl(url.query))
                    self.send_json(messages_since(ctx, int(q.get("rev", 0)), q.get("traffic")))
                elif parts[:2] == ["api", "coord"]:
                    q = dict(parse_qsl(url.query))
                    self.send_json(ctx.coord.api("GET", parts[2:], q, {}))
                elif parts == ["api", "sites"]:
                    cfg = sites_store.load_raw(ctx.sites_path)
                    self.send_json({"sites": sites_store.site_list(cfg)})
                elif parts == ["api", "sites", "suggest"]:
                    q = dict(parse_qsl(url.query))
                    with lock:
                        self.send_json(suggest_site(ctx, float(q["lat"]), float(q["lon"])))
                elif parts[:2] == ["api", "layers"] and len(parts) == 3:
                    layer = BY_ID[parts[2]]
                    with lock:
                        values = layer.parse_values(ctx, dict(parse_qsl(url.query)))
                        self.send_json(layer.data(ctx, values))
                elif parts[:1] == ["scene"] and len(parts) == 2 and parts[1] in SCENE_FILES:
                    if scene_dir is None:
                        self.send_error(404, "no scene")
                    else:
                        self.send_file(scene_dir / parts[1])
                elif parts[:1] == ["tiles"] and len(parts) == 4:
                    self.send_tile(parts[1:])
                else:
                    rel = Path(*parts) if parts else Path("index.html")
                    path = (WEB_DIR / rel).resolve()
                    if WEB_DIR not in path.parents and path != WEB_DIR:
                        self.send_error(403)
                    else:
                        self.send_file(path)
            except Exception as e:  # report to the page instead of dropping the connection
                self.fail(e)

        def do_POST(self):
            set_lang(self.headers.get("X-Lang"))
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                length = int(self.headers.get("Content-Length", 0))
                if parts == ["api", "tracks"]:
                    if length > 30_000_000:
                        raise ValueError(_("GPX-Datei zu groß (max. 30 MB)"))
                    name = dict(parse_qsl(urlparse(self.path).query)).get("name", "")
                    self.send_json(save_track(ctx, name, self.rfile.read(length)))
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
                if parts[:2] == ["api", "device"] and len(parts) == 3:
                    if parts[2] == "connect":
                        ctx.device.connect(body.get("port") or None)
                    elif parts[2] == "disconnect":
                        if ctx.jobs.running("probe"):
                            raise ValueError(_("Ein Traceroute-Rundgang läuft: erst stoppen"))
                        ctx.device.disconnect()
                    self.send_json(ctx.device.status())
                    return
                if parts[:2] == ["api", "coord"]:
                    self.send_json(ctx.coord.api("POST", parts[2:], {}, body))
                    return
                if parts == ["api", "messages"]:
                    msg = ctx.device.send_text(
                        body.get("text", ""), body.get("to") or "^all", body.get("channel", 0)
                    )
                    self.send_json(msg, 201)
                    return
                if parts == ["api", "jobs"]:
                    job = ctx.jobs.create(body.get("kind", ""), body.get("params") or {})
                    self.send_json(job.to_json(), 201)
                    return
                if parts[:2] == ["api", "jobs"] and len(parts) == 4:
                    if parts[3] == "cancel":
                        self.send_json(ctx.jobs.cancel(parts[2]).to_json())
                    elif parts[3] == "remove":
                        ctx.jobs.remove(parts[2])
                        self.send_json({"removed": parts[2]})
                    else:
                        self.send_error(404)
                    return
                if parts[:2] == ["api", "sites"] and len(parts) == 3:
                    with lock:
                        cfg = edit_sites(ctx, parts[2], body)
                    self.send_json({"sites": sites_store.site_list(cfg)})
                    return
                if parts[:2] != ["api", "tools"] or len(parts) != 3 or parts[2] not in TOOLS:
                    self.send_error(404)
                    return
                with lock:
                    self.send_json(TOOLS[parts[2]](ctx, body))
            except Exception as e:
                self.fail(e)

    return Handler


def run(
    port: int = 8770,
    open_browser: bool = False,
    force_export: bool = False,
    device: str | None = None,
    log_packets: bool = True,
    simulate: tuple[Path | None, float] | None = None,
) -> None:
    """device: None = don't connect at start, "auto" = find the port, else a port like COM8.
    simulate: (GPX track or None, speed factor) replaces the device by a simulated radio."""
    ctx = Context()
    sim = None
    if simulate is not None:
        home = ctx.settings.home
        if not home and ctx.sites:
            s = next(iter(ctx.sites.values()))
            home = (s["lat"], s["lon"])
        sim = Simulation(home or (50.7374, 7.0982), *simulate)
        device = "auto"
    ctx.device = DeviceLink(ctx.data_dir, log_packets, sim)
    ctx.jobs = JobManager(ctx)
    ctx.coord = Coordinator(ctx)
    ctx.tiles = TileCache(ctx.data_dir / "tiles")
    if device:
        ctx.device.connect(None if device == "auto" else device)
    scene_dir = ensure_scene_export(ctx, force_export)
    if scene_dir is None:
        print("No scene in data/sim/scene: the 3D view and the link tool are unavailable.")
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctx, scene_dir))
    url = f"http://localhost:{port}/"
    if sim:
        print("Simulated radio: nothing is transmitted; the fake tracker is !fa4e0001.")
    print(f"Map app on {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctx.jobs.shutdown()  # stops walks and ends simulation processes
        ctx.coord.shutdown()
        ctx.device.disconnect()
        server.server_close()
