"""Laser-scan scenes in the map app: list, switch, delete, and the task that builds one.

The scenes themselves are meshplay.sim.scenes (data/sim/scenes/<name>/); where the elevation
data comes from is meshplay.sim.sources (one source per state, chosen by the scene's centre).
The plan says which tiles an area needs, which are there and what the rest costs, with each
file's link for a download by hand. The task downloads the missing ones only when the owner
ticks it, through the source's documented interface, resumable; if that fails, the plan's list
still works. The task runs scripts/sim_build_scene.py in a separate process, so its gigabyte of
rasters never sits in the server. The 3D view's data is exported per scene into
data/mapapp/scene/<name>/ when it is first needed.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from meshplay.mapapp.i18n import N_, L, _
from meshplay.mapapp.jobs import RUNNING, WAITING, Job, JobKind, run_process
from meshplay.mapapp.registry import Context, Setting
from meshplay.sim import scenes, sources

SIZES = (1, 2, 3, 4, 5)  # edge length in km; 5 km = 25 M cells, about 0.5 GB in the server
EXPORT_FILES = ("scene_meta.json", "terrain.i16", "bodies.bin")
TILE_SUFFIXES = (".laz", ".tif", ".xyz", ".part")


def laz_dir(ctx: Context) -> Path:
    return sources.by_id("nrw").tiles_dir(ctx.sim_dir)


def tile_dirs(ctx: Context) -> list[Path]:
    return [s.tiles_dir(ctx.sim_dir) for s in sources.SOURCES]


def supported() -> str:
    return ", ".join(s.state for s in sources.SOURCES)


def export_dir(ctx: Context, name: str) -> Path:
    return ctx.app_dir / "scene" / name


def ensure_export(ctx: Context, force: bool = False) -> Path | None:
    """3D view data of the active scene, exported when missing or older than the scene.

    Callers hold ctx.model_lock (the export reads the scene).
    """
    name = ctx.scene_name
    if name is None:
        return None
    from meshplay.sim.view3d import export_scene

    out = export_dir(ctx, name)
    src = scenes.scene_path(ctx.sim_dir, name) / "scene_meta.json"
    dst = out / "scene_meta.json"
    if force or not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        print(f"Exporting scene {name} for the 3D view (about a minute) ...", flush=True)
        meta = export_scene(ctx.scene, out)
        print(
            f"  {meta['n_buildings']} buildings, {meta['n_bodies'] - meta['n_buildings']} "
            "vegetation bodies",
            flush=True,
        )
    for f in EXPORT_FILES:  # the single export of earlier versions
        (ctx.app_dir / "scene" / f).unlink(missing_ok=True)
    return out


def bounds(bbox) -> list[list[float]]:
    """[[south, west], [north, east]] of an EPSG:25832 box (approximate: UTM is rotated)."""
    from meshplay.sim.sites import to_lonlat

    (w, s), (e, n) = to_lonlat(bbox[0], bbox[1]), to_lonlat(bbox[2], bbox[3])
    return [[s, w], [n, e]]


def ring(bbox) -> list[list[float]]:
    """Corners of an EPSG:25832 box as (lat, lon), for an exact outline on the map."""
    from meshplay.sim.sites import to_lonlat

    pts = ((bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[2], bbox[3]), (bbox[0], bbox[3]))
    return [[lat, lon] for lon, lat in (to_lonlat(x, y) for x, y in pts)]


def tile_files(ctx: Context) -> list[Path]:
    """The downloaded tiles of all sources (not their indexes)."""
    return [
        f
        for d in tile_dirs(ctx)
        if d.is_dir()
        for f in d.rglob("*")
        if f.suffix in TILE_SUFFIXES and f.parent.name != "index"
    ]


def tiles_bytes(ctx: Context) -> int:
    return sum(f.stat().st_size for f in tile_files(ctx))


def scene_list(ctx: Context) -> dict:
    scenes.migrate_legacy(ctx.sim_dir)
    active = scenes.active_name(ctx.sim_dir)
    out = []
    for name in scenes.names(ctx.sim_dir):
        m = scenes.meta(ctx.sim_dir, name)
        b = m["bbox"]
        out.append(
            dict(
                name=name,
                active=name == active,
                size_km=[(b[2] - b[0]) / 1000, (b[3] - b[1]) / 1000],
                bbox_utm=b,
                ring=ring(b),
                bounds=bounds(b),
                created=m.get("created", ""),
                measured=m.get("measured"),
                source=m.get("source", "nrw"),
                attribution=m.get("attribution", "Geobasis NRW"),
                mb=scenes.disk_bytes(scenes.scenes_dir(ctx.sim_dir) / name) / 1e6,
            )
        )
    return dict(scenes=out, active=active, tiles_mb=tiles_bytes(ctx) / 1e6)


def busy_with(ctx: Context, name: str) -> list[Job]:
    """Tasks that read or write the scene `name`."""
    if ctx.jobs is None:
        return []
    return [
        j
        for j in ctx.jobs.jobs.values()
        if j.state in (RUNNING, WAITING)
        and j.kind in ("coverage", "scene")
        and (j.params.get("scene") == name or j.params.get("name") == name)
    ]


def activate(ctx: Context, name: str) -> dict:
    if name not in scenes.names(ctx.sim_dir):
        raise KeyError(_("Szene „{name}“ gibt es nicht", name=name))
    scenes.set_active(ctx.sim_dir, name)
    return scene_list(ctx)


def delete(ctx: Context, name: str) -> dict:
    if name not in scenes.names(ctx.sim_dir):
        raise KeyError(_("Szene „{name}“ gibt es nicht", name=name))
    if busy_with(ctx, name):
        raise ValueError(
            _("Eine Aufgabe arbeitet mit der Szene „{name}“: erst abwarten", name=name)
        )
    ctx.forget_scene()  # so nothing holds its arrays (it is loaded again if still active)
    scenes.delete(ctx.sim_dir, name)
    shutil.rmtree(export_dir(ctx, name), ignore_errors=True)
    return scene_list(ctx)


def delete_tiles(ctx: Context) -> dict:
    if ctx.jobs is not None and any(
        j.kind == "scene" and j.state in (RUNNING, WAITING) for j in ctx.jobs.jobs.values()
    ):
        raise ValueError(_("Eine Szene wird gerade erstellt: erst abwarten"))
    for f in tile_files(ctx):
        f.unlink(missing_ok=True)
    return scene_list(ctx)


def plan(ctx: Context, lat: float, lon: float, size_km: float) -> dict:
    """What building a scene here needs: the state's source, the tiles, which are here already,
    and the missing files with their links and an estimate of their size.

    Without a source for the state, "source" is None and "state" says which one it is. When the
    source's index can't be reached (offline), only the tiles already here count and
    "index_error" says so.
    """
    from meshplay.sim.sites import to_utm

    x, y = to_utm(lon, lat)
    bbox = scenes.bbox_around(x, y, size_km * 1000)
    source, state = sources.for_point(lat, lon)
    free = shutil.disk_usage(ctx.data_dir if ctx.data_dir.exists() else Path.cwd()).free
    out = dict(
        state=state,
        source=None,
        supported=supported(),
        ring=ring(bbox),
        center=f"{lat:.5f}, {lon:.5f}",
        free_mb=free / 1e6,
        tiles=0,
        present=0,
        missing=[],
        download_mb=0.0,
        index_error=None,
    )
    if source is None:
        return out
    tiles_dir = source.tiles_dir(ctx.sim_dir)
    try:
        tiles = source.tiles(bbox, tiles_dir)
    except OSError:
        tiles = source.tiles_offline(bbox, tiles_dir)
        out["index_error"] = _(
            "Das Kachelverzeichnis von {state} ist nicht erreichbar (offline?).", state=state
        )
    files = [f for t in tiles for f in t.files if not f.present]
    out.update(
        source=source.describe(),
        folder=str(tiles_dir),
        tiles=sum(1 for t in tiles if t.files or t.present),
        present=sum(1 for t in tiles if t.present),
        missing=[dict(name=f.name, url=f.url, mb=f.mb) for f in files],
        download_mb=sum(f.mb for f in files),
    )
    return out


def default_center(ctx: Context) -> str:
    home = ctx.settings.home
    if not home and ctx.sites:
        s = next(iter(ctx.sites.values()))
        home = (s["lat"], s["lon"])
    return f"{home[0]:.5f}, {home[1]:.5f}" if home else ""


# ---------------------------------------------------------------- task kind
class SceneBuild(JobKind):
    id = "scene"
    name = N_("Laserscan-Szene erstellen")
    description = N_(
        "Baut aus den Höhendaten des Bundeslands die Szene: Gelände, Gebäude und Bäume im "
        "1-m-Raster (3 × 3 km: ein bis zwei Minuten). Fehlende Kacheln lädt sie auf Wunsch "
        "vorher herunter. Fehlende Kacheln werden interpoliert."
    )
    guided = "scene"  # started from the scene manager, which shows the tiles first

    def settings(self, ctx: Context) -> list[Setting]:
        options = [
            [f"{s['lat']:.5f}, {s['lon']:.5f}", name] for name, s in ctx.sites.items() if "lat" in s
        ]
        return [
            Setting(
                "name",
                _("Name"),
                "text",
                "home" if not scenes.names(ctx.sim_dir) else "",
                help=_("1–32 Zeichen, nur Buchstaben, Ziffern, _ und -"),
            ),
            Setting(
                "center",
                _("Mitte (Breite, Länge)"),
                "point",
                default_center(ctx),
                options=options,
                help=_("Auf der Karte wählen oder eintippen, z. B. 50.94130, 6.95828"),
                square_km_from="size",
            ),
            Setting(
                "size",
                _("Kantenlänge"),
                "select",
                3,
                options=[[k, f"{k} km"] for k in SIZES],
            ),
            Setting("download", _("Fehlende Kacheln herunterladen"), "bool", True),
            Setting("activate", _("Danach verwenden"), "bool", True),
            Setting("overwrite", _("Gleichnamige Szene ersetzen"), "bool", False),
        ]

    def validate(self, ctx: Context, params: dict) -> None:
        name = str(params["name"]).strip()
        if not scenes.NAME_RE.match(name):
            raise ValueError(_("Name: 1–32 Zeichen, nur Buchstaben, Ziffern, _ und -"))
        params["name"] = name
        if name in scenes.names(ctx.sim_dir) and not params["overwrite"]:
            raise ValueError(
                _(
                    "Die Szene „{name}“ gibt es schon: anderen Namen wählen oder „Gleichnamige "
                    "Szene ersetzen“ ankreuzen",
                    name=name,
                )
            )
        if busy_with(ctx, name):
            raise ValueError(
                _("Eine Aufgabe arbeitet mit der Szene „{name}“: erst abwarten", name=name)
            )
        try:
            lat, lon = scenes.parse_center(params["center"])
        except ValueError:
            raise ValueError(
                _("Mitte als „Breite, Länge“ angeben, z. B. 50.94130, 6.95828")
            ) from None
        if int(float(params["size"])) not in SIZES:
            raise ValueError(_("Kantenlänge: 1 bis 5 km"))
        need = plan(ctx, lat, lon, int(float(params["size"])))
        if need["source"] is None:
            raise ValueError(
                _(
                    "Für {state} gibt es noch keine Datenquelle. Unterstützt: {states}.",
                    state=need["state"] or _("diesen Ort"),
                    states=need["supported"],
                )
            )
        loadable = [f for f in need["missing"] if f["url"]]
        if params.get("download") and need["download_mb"] > 0.9 * need["free_mb"]:
            raise ValueError(
                _(
                    "Nicht genug Platz: der Download braucht etwa {need} MB, frei sind {free} MB.",
                    need=round(need["download_mb"]),
                    free=round(need["free_mb"]),
                )
            )
        if not need["present"] and not (params.get("download") and loadable):
            raise ValueError(
                _(
                    "Keine der {n} Kacheln für dieses Gebiet liegt in {folder}: "
                    "„Fehlende Kacheln herunterladen“ ankreuzen oder selbst herunterladen.",
                    n=need["tiles"],
                    folder=need["folder"],
                )
            )
        params["source"] = need["source"]["id"]

    def title(self, params: dict) -> L:
        return L("Laserscan-Szene {name}", name=params["name"])

    def run(self, ctx: Context, job: Job) -> None:
        p = job.params
        lat, lon = scenes.parse_center(p["center"])
        size_km = int(float(p["size"]))
        need = plan(ctx, lat, lon, size_km)
        download = bool(p.get("download"))
        job.add_log(
            _(
                "{n} Kacheln, {have} vorhanden; fehlende Dateien: {missing} (etwa {mb} MB)",
                n=need["tiles"],
                have=need["present"],
                missing=len(need["missing"]),
                mb=round(need["download_mb"]),
            )
        )
        job.detail = L("lädt Kacheln …") if download and need["missing"] else L("baut die Szene …")
        args = [
            "scripts/sim_build_scene.py",
            "--name",
            p["name"],
            "--center",
            f"{lat},{lon}",
            "--size",
            str(size_km * 1000),
            "--source",
            p.get("source") or "auto",
            "--force",
        ]
        args += ["--download"] if download else []
        args += ["--activate"] if p["activate"] else []
        # downloading takes the first half of the bar when there is something to fetch
        share = 0.5 if download and need["missing"] else 0.0

        def on_line(line: str) -> None:
            m = re.match(r"download (\d+)/(\d+) (\d+) %", line)
            if m:
                k, n, pct = int(m[1]), int(m[2]), int(m[3])
                job.progress = share * ((k - 1) + pct / 100) / n
                job.detail = L("lädt Datei {k}/{n} ({pct} %) …", k=k, n=n, pct=pct)
                return
            m = re.match(r"reading tile (\d+)/(\d+)", line)
            if m:
                job.progress = share + (0.85 - share) * int(m[1]) / int(m[2])
                job.detail = L("liest Kachel {k}/{n} …", k=m[1], n=m[2])
            elif line.startswith("fetching building footprints"):
                job.detail = L("holt Gebäudegrundrisse aus OpenStreetMap …")
            elif line.startswith("classifying"):
                job.progress = 0.9
                job.detail = L("trennt Gebäude und Bäume …")

        run_process(job, args, on_line)
        with ctx.model_lock:
            ctx.forget_scene()  # a rebuilt scene of the same name must be read again
            if ctx.scene_name == p["name"]:
                job.progress = 0.95
                job.detail = L("bereitet die 3D-Ansicht vor …")
                ensure_export(ctx)
        job.result = {"name": p["name"], "active": ctx.scene_name == p["name"]}
        job.progress = 1.0
        job.detail = L("fertig: {file}", file=p["name"])
