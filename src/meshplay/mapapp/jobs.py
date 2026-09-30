"""Background tasks of the map app: traceroute walks and simulations.

A task kind declares its parameters (the same Setting objects as the layers, so the page builds
the form) and a run() that does the work in a worker thread: the traceroute walk inside the
server over the USB link (ctx.device), simulations as separate processes. The manager runs one
task per kind at a time and queues the rest, keeps state, progress and log, and stores each task
under data/mapapp/jobs/ (<id>.json, <id>.log) so the list survives a restart of the server.

States: wartet, läuft, fertig, Fehler, abgebrochen.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path

from meshplay.config import DEFAULT_PRESET, PROJECT_ROOT
from meshplay.mapapp.i18n import N_, L, _, get_lang, set_lang
from meshplay.mapapp.registry import Context, Setting

RUNNING, WAITING, DONE, FAILED, CANCELLED = "läuft", "wartet", "fertig", "Fehler", "abgebrochen"
LOG_LINES = 400  # kept in memory per task; the .log file has all


class Cancelled(Exception):
    """Raised inside a task when it was asked to stop."""


def _text(value, persist: bool):
    """A task text (L or plain str): source text + parameters to store, translated to show."""
    if isinstance(value, L):
        return value.to_json() if persist else str(value)
    return value


class Job:
    TEXTS = ("title", "detail", "error")  # L texts, translated when shown

    def __init__(self, id: str, kind: str, title, params: dict):
        self.id, self.kind, self.title, self.params = id, kind, title, params
        self.lang = get_lang()  # the worker thread logs in the language of its creator
        self.state = WAITING
        self.created = time.time()
        self.started: float | None = None
        self.ended: float | None = None
        self.progress: float | None = None  # 0..1, None while unknown
        self.detail = ""  # one line: what the task is doing right now
        self.error = ""
        self.result: dict = {}
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.stop = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.log_path: Path | None = None

    def add_log(self, line: str) -> None:
        line = f"{datetime.now():%H:%M:%S}  {line.rstrip()}"
        self.log.append(line)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def check_stop(self) -> None:
        if self.stop.is_set():
            raise Cancelled()

    def to_json(self, with_log: bool = False, persist: bool = False) -> dict:
        """For the page (texts in the request's language) or, with persist, for the file."""
        out = {
            k: _text(getattr(self, k), persist) if k in self.TEXTS else getattr(self, k)
            for k in (
                "id",
                "kind",
                "title",
                "params",
                "state",
                "created",
                "started",
                "ended",
                "progress",
                "detail",
                "error",
                "result",
            )
        }
        out["last"] = self.log[-1] if self.log else ""
        if persist:
            out["lang"] = self.lang
        if with_log:
            out["log"] = list(self.log)
        return out


class JobKind:
    id = ""
    name = ""  # German source texts (N_), translated in describe()
    description = ""
    stop_label = N_("Abbrechen")
    stop_is_success = False  # e.g. a walk: stopping it is the normal end

    def settings(self, ctx: Context) -> list[Setting]:
        return []

    def validate(self, ctx: Context, params: dict) -> None:
        """Raise ValueError with a message for the page if the parameters can't work."""

    def title(self, params: dict) -> L:
        return L(self.name)

    def run(self, ctx: Context, job: Job) -> None:
        raise NotImplementedError

    def describe(self, ctx: Context) -> dict:
        return dict(
            id=self.id,
            name=_(self.name),
            description=_(self.description),
            stop_label=_(self.stop_label),
            stop_is_success=self.stop_is_success,
            settings=[s.to_json() for s in self.settings(ctx)],
        )


def run_process(job: Job, args: list[str], on_line=None) -> None:
    """Run a script of this project in a separate Python process and stream its output.

    Lines are split at \\n and \\r (progress lines overwrite themselves with \\r); each goes to
    the log and to on_line. Raises Cancelled when stopped, RuntimeError on a non-zero exit.
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    job.add_log("$ python " + " ".join(args))
    job.proc = subprocess.Popen(
        [sys.executable, "-u", *args],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=flags,
    )
    tail: deque[str] = deque(maxlen=8)
    buf = ""
    for chunk in iter(lambda: job.proc.stdout.read1(4096), b""):
        buf += chunk.decode("utf-8", errors="replace")
        *lines, buf = re.split(r"[\r\n]", buf)
        for line in filter(str.strip, lines):
            tail.append(line.strip())
            job.add_log(line)
            if on_line:
                on_line(line.strip())
    if buf.strip():
        tail.append(buf.strip())
        job.add_log(buf)
    code = job.proc.wait()
    job.check_stop()
    if code != 0:
        last = " | ".join(list(tail)[-3:])
        raise RuntimeError(_("Prozess endete mit Code {code}: {last}", code=code, last=last))


# ---------------------------------------------------------------- task kinds
class ProbeWalk(JobKind):
    id = "probe"
    name = N_("Traceroute-Rundgang")
    description = N_(
        "Der Heimknoten schickt im festen Abstand direkte Traceroutes (Hop-Limit 0) an den "
        "Tracker und protokolliert Antwort und SNR in beide Richtungen (data/probes/). Läuft "
        "über die USB-Verbindung der Karten-App, bis du ihn stoppst. Die Position kommt aus "
        "der GPX-Spur deines Handys."
    )
    stop_label = N_("Stoppen")
    stop_is_success = True

    def settings(self, ctx: Context) -> list[Setting]:
        known: dict[str, str] = {}
        probes = sorted((ctx.data_dir / "probes").glob("*.jsonl"), reverse=True)
        last = ""
        for path in probes[:5]:
            for line in path.open(encoding="utf-8"):
                to = json.loads(line).get("to")
                if to:
                    known.setdefault(to, _("{id} (früher geprobt)", id=to))
                    last = last or to
        channels = [[1, "1"]]
        dev = ctx.device
        if dev is not None and dev.state == "verbunden" and dev.iface is not None:
            try:
                nodes, my_num = dev.nodes()
                for n in sorted(nodes.values(), key=lambda n: -(n.get("lastHeard") or 0))[:60]:
                    u = n.get("user", {})
                    if u.get("id") and n.get("num") != my_num:
                        known.setdefault(u["id"], f"{u['id']} {u.get('longName', '')}".strip())
                chans = dev.iface.localNode.channels or []
                channels = [
                    [
                        c.index,
                        f"{c.index} {c.settings.name or (_('primär') if c.index == 0 else '')}",
                    ]
                    for c in chans
                    if c.role
                ] or channels
            except Exception:  # node list or channels not ready yet: keep the defaults
                pass
        return [
            Setting(
                "to",
                _("Tracker (Node-ID)"),
                "text",
                last,
                options=[[k, v] for k, v in known.items()],
                help=_("z. B. !abcd1234; Vorschläge aus früheren Rundgängen und der Knotenliste"),
            ),
            Setting(
                "channel",
                _("Kanal"),
                "select",
                1 if any(c[0] == 1 for c in channels) else channels[0][0],
                options=channels,
                help=_("Privater Kanal, damit das öffentliche Netz nichts mitbekommt"),
            ),
            Setting("interval", _("Abstand [s]"), "number", 60, min=15, max=600, step=5),
            Setting("timeout", _("Warten auf Antwort [s]"), "number", 20, min=5, max=60, step=1),
            Setting("count", _("Anzahl (0 = bis gestoppt)"), "number", 0, min=0, max=10000, step=1),
        ]

    def validate(self, ctx: Context, params: dict) -> None:
        from meshplay.walk import parse_node

        try:
            parse_node(str(params["to"]).strip())
        except ValueError:
            raise ValueError(_("Tracker: Node-ID wie !abcd1234 angeben")) from None
        if params["timeout"] >= params["interval"]:
            raise ValueError(_("Warten auf Antwort muss kürzer sein als der Abstand"))

    def title(self, params: dict) -> L:
        return L("Traceroute-Rundgang {to}", to=params["to"])

    def run(self, ctx: Context, job: Job) -> None:
        from meshplay.probe import run_probes

        dev = ctx.device
        if dev.state != "verbunden":
            job.detail = L("verbinde mit dem Gerät …")
            job.add_log(_("Gerät nicht verbunden, verbinde …"))
            dev.connect(None)
            deadline = time.time() + 45
            while dev.state == "verbinde" and time.time() < deadline:
                job.check_stop()
                time.sleep(0.5)
        if dev.state != "verbunden" or dev.iface is None:
            raise RuntimeError(_("Gerät nicht verbunden: {error}", error=dev.error or dev.state))
        iface = dev.iface
        count = int(job.params["count"]) or None
        stats = {"ok": 0, "total": 0}

        def on_record(rec: dict) -> None:
            dev.sent.note("traceroute", 10)  # the request; the answer is the tracker's airtime
            stats["total"] += 1
            stats["ok"] += rec["result"] == "ok"
            line = f"{rec['sentAt'][11:19]} {rec['result']:<8}"
            if rec["result"] == "ok":
                line += "  " + _(
                    "hin {there} dB, zurück {back} dB, {rtt} s",
                    there=rec["snrTowards"],
                    back=rec["snrBack"],
                    rtt=rec["rttS"],
                )
            job.add_log(line)
            job.detail = L(
                "{ok}/{total} beantwortet · zuletzt {result}",
                ok=stats["ok"],
                total=stats["total"],
                result=rec["result"],
            )
            job.progress = stats["total"] / count if count else None
            job.result = {
                "answered": stats["ok"],
                "sent": stats["total"],
                "date": date.today().isoformat(),
            }
            if dev.iface is not iface:
                raise RuntimeError(_("Verbindung zum Gerät verloren"))

        to = str(job.params["to"]).strip()
        job.add_log(
            _(
                "Probe {to} auf Kanal {channel} alle {s} s",
                to=to,
                channel=job.params["channel"],
                s=f"{job.params['interval']:.0f}",
            )
        )
        job.detail = L("erster Traceroute …")
        run_probes(
            iface,
            ctx.data_dir,
            to,
            int(job.params["channel"]),
            float(job.params["interval"]),
            float(job.params["timeout"]),
            count,
            job.stop,
            on_record,
        )


INDOOR = [  # German source texts, translated where shown
    ["none", N_("Antenne außen")],
    ["open", N_("Fenster offen")],
    ["trad", N_("Fenster zu, Altbau")],
    ["lowe", N_("Fenster zu, Wärmeschutz")],
]
PRESETS = [
    "ShortTurbo",
    "ShortFast",
    "ShortSlow",
    "MediumFast",
    "MediumSlow",
    "LongFast",
    "LongModerate",
    "LongSlow",
]


class CoverageSim(JobKind):
    id = "coverage"
    name = N_("Abdeckung simulieren")
    description = N_(
        "Rechnet für jede Rasterzelle um einen Standort die Empfangswahrscheinlichkeit eines "
        "Pakets mit allen Modellfamilien (scripts/sim_coverage_map.py). Ergebnis: Ebene "
        "„Simulierte Abdeckung“. 25-m-Raster über 800 m dauern etwa 10–15 Minuten."
    )

    def settings(self, ctx: Context) -> list[Setting]:
        sites = [[n, n] for n in ctx.sites]
        home = sites[0][0] if sites else ""
        if ctx.settings.home and ctx.sites:
            from meshplay.sim.walkcompare import nearest_site

            home = nearest_site(ctx.sites, *ctx.settings.home)
        return [
            Setting("site", _("Standort"), "select", home, options=sites),
            Setting(
                "site_indoor",
                _("Antenne steht"),
                "select",
                "none",
                options=[[v, _(label)] for v, label in INDOOR],
            ),
            Setting(
                "preset", _("Preset"), "select", DEFAULT_PRESET, options=[[p, p] for p in PRESETS]
            ),
            Setting("radius", _("Radius [m]"), "number", 800, min=100, max=3000, step=50),
            Setting("step", _("Raster [m]"), "number", 25, min=5, max=100, step=5),
            Setting("draws", _("Ziehungen je Zelle"), "number", 600, min=100, max=4000, step=100),
            Setting("leafless", _("Bäume ohne Laub (Winter)"), "bool", False),
        ]

    def validate(self, ctx: Context, params: dict) -> None:
        if not ctx.has_scene:
            raise ValueError(
                _(
                    "Keine Laserscan-Szene: die Simulation braucht sie "
                    "(README, Abschnitt „3D laser-scan data“)."
                )
            )
        if params["site"] not in ctx.sites:
            raise ValueError(_("Standort „{name}“ gibt es nicht", name=params["site"]))

    def title(self, params: dict) -> L:
        indoor = L(dict(INDOOR)[params["site_indoor"]])
        return L(
            "Abdeckung {site} · {preset} · {placement}",
            site=params["site"],
            preset=params["preset"],
            placement=indoor,
        )

    def run(self, ctx: Context, job: Job) -> None:
        p = job.params
        args = [
            "scripts/sim_coverage_map.py",
            "--site",
            p["site"],
            "--site-indoor",
            p["site_indoor"],
            "--preset",
            p["preset"],
            "--radius",
            f"{p['radius']:g}",
            "--step",
            f"{p['step']:g}",
            "--draws",
            str(int(p["draws"])),
        ] + (["--leafless"] if p["leafless"] else [])
        # Progress by cells done. Cells further out cost more (longer profiles), but in tests
        # no distance weighting predicted the run time better than plain counting (~25 % off).
        k = math.ceil(p["radius"] / p["step"])
        steps = [i * p["step"] for i in range(-k, k + 1)]
        expected = sum(1 for x in steps for y in steps if x * x + y * y <= p["radius"] ** 2)
        job.detail = L("lädt die Szene …")

        def on_line(line: str) -> None:
            m = re.search(r"row (\d+)/(\d+)\s+(\d+) cells\s+(\d+) s", line)
            if m:
                row, cells, secs = int(m[1]), int(m[3]), int(m[4])
                job.progress = min(cells / expected, 0.99)
                rest = secs / job.progress * (1 - job.progress) if job.progress > 0.05 else None
                done = {
                    "row": row,
                    "rows": m[2],
                    "cells": cells,
                    "total": expected,
                    "elapsed": f"{secs // 60}:{secs % 60:02d}",
                }
                if rest:
                    job.detail = L(
                        "Zeile {row}/{rows} · {cells} von {total} Zellen · {elapsed}"
                        " · noch ca. {min} min",
                        min=max(1, round(rest / 60)),
                        **done,
                    )
                else:
                    job.detail = L(
                        "Zeile {row}/{rows} · {cells} von {total} Zellen · {elapsed}", **done
                    )
            m = re.match(r"Grid: (.+\.npz)", line)
            if m:
                job.result["file"] = Path(m[1]).name

        run_process(job, args, on_line)
        job.progress = 1.0
        job.detail = L("fertig: {file}", file=job.result.get("file", ""))


def all_kinds() -> dict[str, JobKind]:
    """Every task kind. The road-graph download lives with the coordination mode and builds
    on JobKind, so it is imported here, when the manager is created, not at module level."""
    from meshplay.mapapp.coord.osm import OsmAreas, OsmDownload

    return {k.id: k for k in (ProbeWalk(), CoverageSim(), OsmDownload(), OsmAreas())}


# ---------------------------------------------------------------- manager
class JobManager:
    def __init__(self, ctx: Context, kinds: dict[str, JobKind] | None = None):
        self.ctx = ctx
        self.kinds = kinds or all_kinds()
        self.dir = ctx.app_dir / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        for path in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            job = Job(d["id"], d["kind"], L.from_json(d["title"]), d["params"])
            job.lang = d.get("lang", job.lang)
            for k in (
                "state",
                "created",
                "started",
                "ended",
                "progress",
                "detail",
                "error",
                "result",
            ):
                value = d.get(k, getattr(job, k))
                setattr(job, k, L.from_json(value) if k in Job.TEXTS else value)
            self._translatable(job)
            if job.state in (RUNNING, WAITING):  # the server stopped while it ran
                job.state, job.error = CANCELLED, L("Server wurde beendet")
                job.ended = job.ended or time.time()
            job.log_path = self.dir / f"{job.id}.log"
            if job.log_path.exists():
                lines = job.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                job.log.extend(lines[-LOG_LINES:])
            self.jobs[job.id] = job
            self._save(job)

    def _translatable(self, job: Job) -> None:
        """Tasks stored before the translation have plain German texts: rebuild the title from
        the parameters and turn the usual end states into translatable texts."""
        if isinstance(job.title, str) and job.kind in self.kinds:
            try:
                job.title = self.kinds[job.kind].title(job.params)
            except (KeyError, ValueError):
                pass
        if isinstance(job.detail, str):
            if job.detail.startswith("fertig: "):
                job.detail = L("fertig: {file}", file=job.detail.removeprefix("fertig: "))
            elif job.detail == "abgebrochen":
                job.detail = L("abgebrochen")
        if job.error == "Server wurde beendet":
            job.error = L("Server wurde beendet")

    def _save(self, job: Job) -> None:
        (self.dir / f"{job.id}.json").write_text(
            json.dumps(job.to_json(persist=True), ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # ------------------------------------------------------------ api
    def describe_kinds(self) -> list[dict]:
        return [k.describe(self.ctx) for k in self.kinds.values()]

    def list(self) -> list[dict]:
        return [j.to_json() for j in sorted(self.jobs.values(), key=lambda j: -j.created)]

    def get(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise KeyError(_("Aufgabe {id} gibt es nicht", id=job_id))
        return self.jobs[job_id]

    def running(self, kind: str | None = None) -> list[Job]:
        return [j for j in self.jobs.values() if j.state == RUNNING and kind in (None, j.kind)]

    def create(self, kind_id: str, raw: dict) -> Job:
        if kind_id not in self.kinds:
            raise KeyError(_("unbekannte Aufgabe {kind}", kind=kind_id))
        kind = self.kinds[kind_id]
        params = {}
        for s in kind.settings(self.ctx):
            v = raw.get(s.name)
            params[s.name] = s.parse(None if v is None else str(v))
            if s.type == "number" and params[s.name] is not None:
                if s.min is not None and params[s.name] < s.min:
                    raise ValueError(_("{label}: mindestens {n}", label=s.label, n=f"{s.min:g}"))
                if s.max is not None and params[s.name] > s.max:
                    raise ValueError(_("{label}: höchstens {n}", label=s.label, n=f"{s.max:g}"))
        kind.validate(self.ctx, params)
        with self._lock:
            job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{kind_id}"
            while job_id in self.jobs:
                job_id += "x"
            job = Job(job_id, kind_id, kind.title(params), params)
            job.log_path = self.dir / f"{job_id}.log"
            self.jobs[job_id] = job
            self._save(job)
        self._schedule()
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state == WAITING:
            job.state, job.ended = CANCELLED, time.time()
            self._save(job)
        elif job.state == RUNNING:
            job.stop.set()
            job.detail = L("wird beendet …")
            if job.proc and job.proc.poll() is None:
                job.proc.terminate()
        return job

    def remove(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.state in (RUNNING, WAITING):
            raise ValueError(_("Laufende oder wartende Aufgaben erst abbrechen"))
        with self._lock:
            del self.jobs[job_id]
        for suffix in (".json", ".log"):
            (self.dir / f"{job_id}{suffix}").unlink(missing_ok=True)

    def shutdown(self) -> None:
        for job in list(self.jobs.values()):
            if job.state in (RUNNING, WAITING):
                self.cancel(job_id=job.id)

    # ------------------------------------------------------------ worker
    def _schedule(self) -> None:
        with self._lock:
            busy = {j.kind for j in self.jobs.values() if j.state == RUNNING}
            for job in sorted(self.jobs.values(), key=lambda j: j.created):
                if job.state == WAITING and job.kind not in busy:
                    job.state, job.started = RUNNING, time.time()
                    busy.add(job.kind)
                    threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def _run(self, job: Job) -> None:
        kind = self.kinds[job.kind]
        set_lang(job.lang)  # log lines and errors in the language of whoever started it
        self._save(job)
        job.add_log(_("Start: {title}", title=job.title))
        try:
            kind.run(self.ctx, job)
            job.state = DONE
            if job.stop.is_set():
                job.state = DONE if kind.stop_is_success else CANCELLED
        except Cancelled:
            job.state = DONE if kind.stop_is_success else CANCELLED
        except Exception as e:  # report to the page, keep the server running
            if job.stop.is_set():
                job.state = DONE if kind.stop_is_success else CANCELLED
            else:
                # our own errors carry a message for the user; others keep their type
                own = isinstance(e, (ValueError, RuntimeError))
                job.state, job.error = FAILED, str(e) if own else f"{type(e).__name__}: {e}"
                job.add_log(_("Fehler: {error}", error=job.error))
        finally:
            job.ended = time.time()
            job.proc = None
            if job.state == CANCELLED:
                job.detail = L("abgebrochen")
            job.add_log(_("Ende: {state}", state=_(job.state)))
            self._save(job)
            self._schedule()
