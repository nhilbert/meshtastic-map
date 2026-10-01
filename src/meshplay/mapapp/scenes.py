"""Laser-scan scenes in the map app: list, switch, delete, and the task that creates one.

The scenes themselves are meshplay.sim.scenes (data/sim/scenes/<name>/). The task downloads the
missing NRW tiles in the server (resumable, see meshplay.sim.lidar) and builds the scene in a
separate process (scripts/sim_build_scene.py), so its gigabyte of rasters never sits in the
server. The 3D view's data is exported per scene into data/mapapp/scene/<name>/ when it is first
needed.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from meshplay.mapapp.i18n import N_, L, _
from meshplay.mapapp.jobs import RUNNING, WAITING, Job, JobKind, run_process
from meshplay.mapapp.registry import Context, Setting
from meshplay.sim import scenes

TILE_MB = 95  # typical size of an NRW tile (60-130 MB) for the estimate before the download
SIZES = (1, 2, 3, 4, 5)  # edge length in km; 5 km = 25 M cells, about 0.5 GB in the server
EXPORT_FILES = ("scene_meta.json", "terrain.i16", "bodies.bin")


def laz_dir(ctx: Context) -> Path:
    return ctx.sim_dir / "laz"


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


def tiles_bytes(ctx: Context) -> int:
    d = laz_dir(ctx)
    return sum(f.stat().st_size for f in d.glob("*.laz")) if d.is_dir() else 0


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
    d = laz_dir(ctx)
    if d.is_dir():
        for f in [*d.glob("*.laz"), *d.glob("*.part")]:
            f.unlink(missing_ok=True)
    return scene_list(ctx)


def plan(ctx: Context, lat: float, lon: float, size_km: float) -> dict:
    """What creating a scene here would take: tiles, tiles already there, download estimate."""
    from meshplay.sim.scene import tiles_for_bbox
    from meshplay.sim.sites import to_utm

    x, y = to_utm(lon, lat)
    bbox = scenes.bbox_around(x, y, size_km * 1000)
    tiles = tiles_for_bbox(bbox)
    have = sum((laz_dir(ctx) / t).exists() for t in tiles)
    free = shutil.disk_usage(ctx.data_dir if ctx.data_dir.exists() else Path.cwd()).free
    return dict(
        tiles=len(tiles),
        present=have,
        download_mb=(len(tiles) - have) * TILE_MB,
        free_mb=free / 1e6,
        ring=ring(bbox),
        center=f"{lat:.5f}, {lon:.5f}",
    )


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
        "Lädt die fehlenden Laserscan-Kacheln von Geobasis NRW (je 1 km², 60–130 MB; ein "
        "Abbruch setzt beim nächsten Mal fort) und baut daraus die Szene: Gelände, Gebäude und "
        "Bäume im 1-m-Raster. 3 × 3 km: etwa 1,5 GB Download, danach ein bis zwei Minuten. "
        "Nur für Nordrhein-Westfalen."
    )

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
                "text",
                default_center(ctx),
                options=options,
                help=_("z. B. 50.94130, 6.95828; Vorschläge: die eigenen Standorte"),
            ),
            Setting(
                "size",
                _("Kantenlänge"),
                "select",
                3,
                options=[[k, f"{k} km"] for k in SIZES],
            ),
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
            scenes.parse_center(params["center"])
        except ValueError:
            raise ValueError(
                _("Mitte als „Breite, Länge“ angeben, z. B. 50.94130, 6.95828")
            ) from None
        if int(float(params["size"])) not in SIZES:
            raise ValueError(_("Kantenlänge: 1 bis 5 km"))

    def title(self, params: dict) -> L:
        return L("Laserscan-Szene {name}", name=params["name"])

    def run(self, ctx: Context, job: Job) -> None:
        import urllib.error

        try:
            self._run(ctx, job)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise RuntimeError(
                _("Der Server von Geobasis NRW ist nicht erreichbar: {error}", error=e)
            ) from None

    def _run(self, ctx: Context, job: Job) -> None:
        from meshplay.sim.lidar import download_tile, remote_size
        from meshplay.sim.scene import tiles_for_bbox
        from meshplay.sim.sites import to_utm

        p = job.params
        lat, lon = scenes.parse_center(p["center"])
        size_m = int(float(p["size"])) * 1000
        x, y = to_utm(lon, lat)
        tiles = tiles_for_bbox(scenes.bbox_around(x, y, size_m))
        folder = laz_dir(ctx)

        # 1. which tiles are missing, and does the server have them
        job.detail = L("prüft die Kacheln beim Server …")
        todo: dict[str, int] = {}
        for t in tiles:
            job.check_stop()
            if (folder / t).exists():
                continue
            size = remote_size(t)
            if size is None:
                job.add_log(_("{tile}: gibt es nicht (außerhalb von NRW?)", tile=t))
            else:
                todo[t] = size
        have = sum((folder / t).exists() for t in tiles)
        if not todo and not have:
            raise RuntimeError(
                _("Für dieses Gebiet gibt es keine Laserscan-Kacheln (nur Nordrhein-Westfalen).")
            )
        total = sum(todo.values())
        job.add_log(
            _(
                "{n} Kacheln, {have} vorhanden, {todo} zu laden ({mb} MB)",
                n=len(tiles),
                have=have,
                todo=len(todo),
                mb=f"{total / 1e6:.0f}",
            )
        )

        # 2. download (70 % of the progress bar: it is the slow part)
        done_before = 0
        for k, (t, size) in enumerate(todo.items(), 1):

            def on_progress(done: int, _total: int, k=k, before=done_before) -> None:
                job.check_stop()
                got = before + done
                job.progress = 0.7 * got / total if total else None
                job.detail = L(
                    "lädt Kachel {k}/{n} · {mb} von {total} MB",
                    k=k,
                    n=len(todo),
                    mb=f"{got / 1e6:.0f}",
                    total=f"{total / 1e6:.0f}",
                )

            download_tile(t, folder, on_progress)
            job.add_log(_("{tile} geladen", tile=t))
            done_before += size

        # 3. build in a separate process
        job.progress = 0.7
        job.detail = L("baut die Szene …")
        args = [
            "scripts/sim_build_scene.py",
            "--name",
            p["name"],
            "--center",
            f"{lat},{lon}",
            "--size",
            str(size_m),
            "--force",
        ] + (["--activate"] if p["activate"] else [])

        def on_line(line: str) -> None:
            m = re.match(r"reading tile (\d+)/(\d+)", line)
            if m:
                job.progress = 0.7 + 0.25 * int(m[1]) / int(m[2])
                job.detail = L("liest Kachel {k}/{n} …", k=m[1], n=m[2])
            elif line.startswith("classifying"):
                job.detail = L("trennt Gebäude und Bäume …")

        run_process(job, args, on_line)
        with ctx.model_lock:
            ctx.forget_scene()  # a rebuilt scene of the same name must be read again
            if ctx.scene_name == p["name"]:
                job.progress = 0.97
                job.detail = L("bereitet die 3D-Ansicht vor …")
                ensure_export(ctx)
        job.result = {"name": p["name"], "active": ctx.scene_name == p["name"]}
        job.progress = 1.0
        job.detail = L("fertig: {file}", file=p["name"])
