# CLAUDE.md

Entry point for coding agents working in this repository. Read this first, then the doc for the
area you touch: [README.md](README.md) (setup, scripts, walks), [docs/mapapp.md](docs/mapapp.md)
(map app), [docs/simulation.md](docs/simulation.md) (propagation models).

## What this is

`meshplay`: Python tools around a Meshtastic node on USB. Three parts that share `src/meshplay/`:

1. **Device scripts** (`scripts/*.py`): node info, packet log, node export, send text.
2. **Coverage walks**: the home node traceroutes a walking tracker (`probe.py`,
   `scripts/probe_walk.py`) or logs its positions (`scripts/listen.py`); a phone GPX track gives
   the route; `walk.py` and `scripts/coverage_map.py` turn both into maps.
3. **Coverage simulation** (`src/meshplay/sim/`): seven ITU-R model families over a 1 m LiDAR
   scene (NRW laser scan; named scenes of up to 5 × 5 km in `data/sim/scenes/`, one in use),
   scored against measurements.

The **map app** (`src/meshplay/mapapp/` server + `webmap/` page) brings them together: layers,
link calculator, background tasks (walks, simulations), sites editor, messaging pane and node
list. Solo project of the owner;
public on GitHub.

## Environment and commands

Windows 11, PowerShell (Git Bash also available). Python venv in `.venv/`; call its interpreter
directly (`.venv/Scripts/python.exe`) instead of relying on activation.

```powershell
python -m pip install -e ".[sim,dev]"   # everything; the map app needs the sim extras
pytest                                   # unit tests, ~35 s, no device needed
pytest -m data                           # regression test against the local reference scene
pytest -m hardware                       # needs the device on USB
ruff check . ; ruff format .             # line length 100, py310; Markdown code blocks too
python scripts/mapapp.py --open          # map app on http://localhost:8770
node --check webmap/js/<file>.js         # quick syntax check for the page's ES modules
```

Run lint and tests before calling a change done. Python changes in the map app need a server
restart; the page's files are served fresh (Ctrl+F5).

## Layout

```
src/meshplay/config.py      settings from .env; DEFAULT_PRESET; PROJECT_ROOT
src/meshplay/device.py      connect()/find_port() for the serial device
src/meshplay/packets.py     protobuf/packet dicts -> plain JSON
src/meshplay/walk.py        walk data: load positions, probes, GPX; place probes on the track
src/meshplay/probe.py       traceroute probes (shared by probe_walk.py and the map app task)
src/meshplay/sim/           itu.py, p1812.py (verbatim ITU port), models.py, scene.py, link.py,
                            predictor.py, compare.py, walkcompare.py, sites.py, view3d.py,
                            scenes.py (named scenes, the active one), lidar.py (tile download)
src/meshplay/mapapp/        server.py (HTTP + API), registry.py (Layer, Setting, Context),
                            layers/ (one module per layer), tools/link.py, jobs.py (background
                            tasks), sites_store.py (sites.json editing), device.py (live USB link,
                            sending texts, packet listeners), fake_device.py (--simulate),
                            messages.py (message store for the pane), airtime.py (airtime
                            panel), i18n.py (translations),
                            tiles.py (map tile cache), scenes.py (scene manager + build
                            task), coord/ (coordination mode: missions.py
                            decisions + API, phrases.py radio texts, routing.py + osm.py road
                            graph, areas.py, paths.py, settings.py, store.py)
webmap/js/                  main.js (wiring), forms.js, tasks.js, sites.js, scenes.js, messages.js,
                            nodelist.js, coord.js, panels.js, map2d.js (Leaflet), map3d.js
                            (three.js), util.js, icons.js, i18n.js, export.js (GPX/CSV)
webmap/vendor/, i18n/       served libraries and fonts (offline use); translation catalogues
tests/                      pytest; markers `hardware` and `data` are opt-in; conftest.py has
                            the Coordinator fixture over a fake radio
data/                       local only, never committed (see below)
```

Extension points are documented in docs/mapapp.md: new layer = `Layer` subclass in `layers/`
listed in `layers/__init__.py`; new background task = `JobKind` listed in `all_kinds()` in
`jobs.py`. The page builds forms from the server's `Setting` declarations, so neither needs
page changes. The coordination mode's design is in docs/coordination-design.md; its radio
texts have their own catalogue in `coord/phrases.py` (per mission language, not UI language).

## Rules

**Privacy.** This repository is public. Never commit personal data: no real coordinates,
addresses, street names, node IDs, names of people or their nodes. Use placeholders
(`!abcd1234`, sites `HOME`/`ROOF`, public places for example coordinates). Everything personal
lives in `data/` and `.env`, which are git-ignored: packet and probe logs, GPX tracks,
`data/sim/sites.json` (holds addresses), exports.

**Radio.** Sending on the mesh reaches other people's devices. Never transmit (send_text,
traceroutes, starting a probe task, changing device config) without the owner's explicit OK
for that action. Test probe and messaging logic with a fake interface (`tests/test_mapapp_jobs.py`,
`tests/test_mapapp_messages.py`); for the page, run the server with the simulated radio
(`python scripts/mapapp.py --simulate [track.gpx]`, see docs/mapapp.md) instead of the real
device. Sending a text from the messaging pane is the owner's own action, and so is switching
the coordination mode on: from then on the server messages nodes with a mission by itself (texts
and position requests, `coord/missions.py`), never nodes without one; the only exception is the
answer to a node's own marker command (`+D`/`?D`, setting *Markierungen per Funk*), and only for
nodes on the private channel of its settings. Walk traffic goes on a private channel with hop
limit 0; coordination messages are direct messages on the channel of its settings (default 1,
the private channel).

**Modem preset.** The owner's mesh runs **ShortSlow**; `DEFAULT_PRESET` in `config.py` is the
single default. Never hardcode LongFast. A preset (and interval) recorded in a log wins over the
default; `probe.py` records both.

**Degrade gracefully.** The app must run without the laser-scan scene, without
`data/sim/sites.json`, without logs and without a device. Anything that needs one of them checks
first (`ctx.has_scene`, `ctx.sites`, `ctx.device.state`) and answers with a plain message (layer
`note`, `ValueError` → HTTP 400) instead of a traceback; the page hides or disables what can't
work and says why. Check with an empty data folder: `MESHPLAY_DATA_DIR=<empty dir> python
scripts/mapapp.py --port 8771` (tests: `tests/test_mapapp.py::test_*_without_scene`).

**Serial port.** Only one program can hold it. The map app's `DeviceLink` owns it while
connected; the probe task uses that connection. A browser tab with the web client blocks it
until the tab is closed.

**Measurement pitfalls** (already handled; keep them handled):
- The Python API omits `hopLimit` when it is 0: missing means 0, not "unknown".
- Radio RSSI is signal + noise; compare models with RSSI corrected by SNR (`sim/compare.py`).
- A traceroute is answered only if both directions work; models predict one direction.
- Frozen predictions (`sim_predict.py`) are never overwritten or tuned on the data they score.
- Coverage grids are named after every parameter that changes them, so runs don't collide.

**Code.**
- Match the surrounding style: short docstrings that say why, a usage line at the top of every
  script, type hints, no dead code. Comments explain non-obvious reasons, not the code.
- `src/meshplay/sim/p1812.py` is a verbatim port validated against ITU data: don't reformat or
  refactor it (it is excluded from ruff). It is not MIT but under the ITU licence
  (`LICENSE-ITU-P1812.txt`), which asks that every change be noted with date and nature in the
  file header.
- Code, comments, docs and commit messages are English.
- **UI languages: German (source), English, French.** Every user-facing text is written in
  German and marked: `t("…")` in the page, `_("…")` / `N_("…")` / `L("…")` on the server (see
  docs/mapapp.md, Languages), then added to `webmap/i18n/en.json` and `fr.json`.
  `tests/test_i18n.py` fails on any unmarked-but-listed, missing, stale or placeholder-mismatched
  text. Use whole sentences with `{placeholders}` as keys, never sentences built from fragments;
  keep markup out of the keys; pass the literal string directly to the marker (the test can't
  see `t(cond ? "a" : "b")` — write two calls). State codes stay German and are translated only
  for display. Terminal output and log records stay English.
- Server errors meant for the user: raise `ValueError`/`KeyError` with a plain message; the
  server sends it as is (HTTP 400). Other exceptions are 500 with the type name.

**Git.** Commit directly to `main` (solo project), only when the owner asks, with a message that
explains what and why. Don't commit `.vscode/settings.json` changes that only concern the
owner's machine.

## Gotchas

- Windows lets two servers bind the same port: if the map app behaves like old code, look for a
  stale `mapapp.py` process (`Get-CimInstance Win32_Process`).
- Piping script output on Windows uses cp1252; set `PYTHONIOENCODING=utf-8` for Unicode output.
- The 3D view needs WebGL. For headless UI checks, drive Chrome over the DevTools protocol with
  `--disable-gpu`; the 3D errors are expected there, the 2D map and panels must still work.
- Coverage simulations take 5–15 min for 800 m at 25 m; use `--radius 150` for quick checks and
  delete the test output afterwards.
