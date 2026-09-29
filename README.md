# meshplay — a Meshtastic playground

Scripts, a browser map app and a radio coverage simulation around a
[Meshtastic](https://meshtastic.org) node connected over USB. It grew out of one question:
*where can my home node actually be reached?* — and answers it two ways, by measuring on a walk
and by simulating the radio links over a laser scan of the city.

What you can do with it:

- **Talk to your node from Python**: node info, node list export, live packet log, send text.
- **Measure coverage on a walk**: your home node traceroutes a tracker you carry (or listens
  to its position broadcasts); your phone records the route; the result is a map of where the
  home node was reachable, with the signal quality in both directions.
- **Use the map app** (browser, 2D OpenStreetMap or 3D laser scan): layers for your sites, the
  live node list, walks and simulated coverage; a link calculator between any two points;
  start walks and simulations as background tasks; edit your sites; send and read messages on
  your channels or directly to a node picked on the map, and watch the packet traffic.
- **Simulate coverage** with ITU-R propagation models over a 3D scene built from open
  laser-scan data, and score the models against your measurements.

The code is tested on Windows with a Seeed Wio Tracker L1 Pro as home node and a Seeed T1000-E as
walking tracker. Other Meshtastic devices with USB serial should work; Linux and macOS should
work too (the commands below are PowerShell), but haven't been tried.

## Contents

- [Requirements](#requirements)
- [Getting started](#getting-started)
- [Scripts](#scripts)
- [Coverage walks](#coverage-walks)
- [Map app](#map-app)
- [3D laser-scan data](#3d-laser-scan-data)
- [Coverage simulation](#coverage-simulation)
- [Official web client](#official-web-client)
- [Configuration](#configuration)
- [Project layout and data](#project-layout-and-data)
- [Troubleshooting](#troubleshooting)
- [Writing your own code](#writing-your-own-code)
- [Development](#development)
- [Credits and data sources](#credits-and-data-sources)
- [Licence](#licence)

## Requirements

- **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/); on Windows tick
  "Add python.exe to PATH" during installation).
- **Git** to get the code, or download the repository as a ZIP.
- **A Meshtastic node with a USB data cable.** For walks, a second node that you carry (a
  tracker such as the T1000-E, or any node) and a phone app that records a GPX track.
- **For the simulation and the 3D view:** about 1–2 GB of disk space and 2 GB of RAM for a
  3 × 3 km scene. The laser-scan tiles come from **Geobasis NRW**, so the scene can only be
  built for North Rhine-Westphalia (Germany) without extra work; see
  [3D laser-scan data](#3d-laser-scan-data). Everything else works anywhere.
- Optional: **Docker Desktop** for a local copy of the official Meshtastic web client.

## Getting started

These steps take you from nothing to a running map app. Commands are for PowerShell.

**1. Get the code and create a virtual environment**

```powershell
git clone https://github.com/nhilbert/meshtastic-map.git
cd meshtastic-map
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[sim,dev]"
```

If activation fails with "running scripts is disabled", run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again. Activate the
environment (`.\.venv\Scripts\Activate.ps1`) in every new terminal before using the scripts.

`.[sim,dev]` installs everything: the scripts, the map app, the simulation and the test tools.
The device scripts alone need only `python -m pip install -e .`. To get exactly the tested
versions: `python -m pip install -r requirements.lock`, then `python -m pip install -e . --no-deps`.

**2. Configure**

```powershell
Copy-Item .env.example .env
```

Open `.env` in an editor. Set `MESHPLAY_HOME` to your home node's position as `lat,lon` (e.g.
copied from a map), and `MESHTASTIC_PORT` if the node isn't found automatically (see
[Configuration](#configuration)).

**3. Connect the node and check**

Plug the node in over USB and close every other program that uses it (the Meshtastic web client
in Chrome/Edge, other scripts). Then

```powershell
python scripts/node_info.py
```

prints your node's name, ID, hardware, firmware, battery and the number of known nodes. If it
says the port is busy or no device was found, see [Troubleshooting](#troubleshooting).

**4. Watch the mesh**

```powershell
python scripts/listen.py          # every received packet; Ctrl+C stops
python scripts/export_nodes.py    # the node's node list -> data/exports/
```

**5. Open the map app**

```powershell
python scripts/mapapp.py --open --device
```

opens http://localhost:8770 in your browser and connects to the node, so the node layer shows
the mesh live. Until you have prepared the laser-scan data, the 3D view and the link calculator
explain what's missing; the rest works. [docs/mapapp.md](docs/mapapp.md) has a tour.

**6. Next steps**

- Measure your coverage: [Coverage walks](#coverage-walks).
- Get the laser-scan data for the 3D view and the simulation:
  [3D laser-scan data](#3d-laser-scan-data) (NRW only).

## Scripts

All scripts are in `scripts/`, print their options with `--help`, and take `--port COM8` where
they talk to the device (otherwise `MESHTASTIC_PORT` from `.env` or auto-detection).

| Script | What it does |
|---|---|
| **Device** | |
| `node_info.py` | summary of the connected node |
| `listen.py` | prints received packets and appends them to `data/packets/<date>.jsonl` (`--text-only`) |
| `export_nodes.py` | node list of the device → `data/exports/nodes-<time>.json` and `.csv` |
| `send_text.py` | send a text: `send_text.py "hello"` broadcasts on channel 0; `--to !1234abcd`, `--channel 1` |
| **Walks and measurements** | |
| `probe_walk.py` | traceroutes a walking node every 60 s and logs the answers → `data/probes/` |
| `coverage_map.py` | map of a walk (positions, or traceroutes with `--probes`) and the phone's GPX track → `data/maps/` |
| `measure_logger.py` | fixed-point link test: one device sends numbered packets, the other logs them |
| **Map app** | |
| `mapapp.py` | the browser map app (`--open`, `--device`, `--port 8770`) |
| **Simulation** | |
| `sim_fetch_tiles.py` | lists and downloads the NRW laser-scan tiles around home ([details](#3d-laser-scan-data)) |
| `sim_build_scene.py` | builds the 3D scene (terrain, buildings, trees) from the tiles |
| `sim_coverage_map.py` | predicted coverage around a site, per model |
| `sim_compare_walk.py` | scores a walk against the models |
| `sim_predict.py`, `sim_score.py` | frozen predictions for fixed links, scored against `measure_logger.py` results |
| `sim_relay_search.py` | searches roofs for the best relay between two sites |
| `sim_validate_p1812.py` | checks the ITU-R P.1812 port against the official validation data |

The official Meshtastic CLI is installed as well: `meshtastic --port COM8 --info`.

## Coverage walks

Map where your home node can reach a node you carry. Two methods:

| | Traceroutes from home (recommended) | Position broadcasts |
|---|---|---|
| Who transmits | home node asks, tracker answers | tracker only |
| Tracker needs a GPS fix | no (the phone's GPX track gives the position) | yes |
| Result | reachable or not, SNR in **both** directions | whether home heard the tracker (one direction) |
| Airtime | two short packets per minute | one position every 30 s |

**Be nice to the public mesh.** A walk sends a packet every 30–60 s for an hour or more. Use a
**private channel** (other nodes can't read it) and set the tracker's **hop limit to 0** for the
walk, so no other node relays your test traffic. Set it back afterwards (default 3).

### Traceroutes from home

1. **Tracker** (Meshtastic app): add your private channel (same name and key as on the home
   node, e.g. as channel 1) and set LoRa → hop limit **0**. Position sharing can stay off.
2. **At home:** in the map app open *Aufgaben → ＋ Traceroute-Rundgang*, enter the tracker's
   node ID and the private channel, *Starten*. Or on the command line:

   ```powershell
   python scripts/probe_walk.py --to !abcd1234 --channel 1
   ```

   Wait for the first answers before you leave (tracker next to the home node). Keep the laptop
   awake and plugged in.
3. **Walk** with the tracker in your pocket and a GPX recording on your phone.
4. **Back home:** stop the task (or Ctrl+C), then choose *GPX-Spur hochladen …* on the finished
   task in the map app: the walk layer shows the route coloured by reachability and every probe
   with both SNR values. Or save the GPX file to `data/tracks/` and run

   ```powershell
   python scripts/coverage_map.py --tracker !abcd1234 --probes --gpx data/tracks/walk.gpx --open
   ```

### Position broadcasts

1. **Tracker:** on the private channel turn position sharing on with **precise location**, on
   the primary channel off (positions go out on the first channel that shares them); smart
   position off, broadcast interval 30 s, GPS update interval 30 s; hop limit 0.
2. **At home:** `python scripts/listen.py` (or the map app with `--device`, which logs the same).
   Wait for the tracker's first position before you leave.
3. **Walk** with a GPX recording on your phone; afterwards save the file to `data/tracks/`.
4. **Map:** `python scripts/coverage_map.py --tracker !abcd1234 --gpx data/tracks/walk.gpx --open`,
   or choose the tracker and the track in the map app's walk layer.

`coverage_map.py` writes `data/maps/coverage-<id>-<date>.html` (`probes-…` with `--probes`) and a
CSV and prints the share of
the walked distance with direct coverage. `--open` serves the map on http://localhost:8765
(opened as a file, the map background stays blank: OpenStreetMap refuses `file://` pages).

To compare a walk with the simulation:
`python scripts/sim_compare_walk.py --tracker !abcd1234 --probes --gpx data/tracks/walk.gpx --home-indoor none`
(see [docs/simulation.md](docs/simulation.md)), or colour the map app's walk layer by
*Messung − Modell*.

## Map app

```powershell
python scripts/mapapp.py --open            # add --device to connect to the node
```

One map, switchable between 2D (OpenStreetMap) and 3D (laser-scan scene), with layers: a
direct-link calculator, your sites (editable), the Meshtastic nodes (live from the device or from
an export, also as a list), walks, simulated coverage and the scene extent. A messaging pane
under the map sends and shows texts on your channels and to single nodes, and lists the packet
traffic. Long jobs — a traceroute walk, a coverage simulation — run as background tasks, started
and followed in the browser. The
interface is in German. Tour, all features, and how to add layers or tasks:
[docs/mapapp.md](docs/mapapp.md).

## 3D laser-scan data

The 3D view, the link calculator, the coverage simulation and the comparison of walks with the
models all work on a **scene**: 1 m rasters of terrain, surface height, buildings and trees,
built from an airborne laser scan. The scene has to be downloaded and prepared once, by hand
with some script help. Without it the map app still runs — the 2D map, nodes, walks, sites and
traceroute walks work; the parts that need the scene say so.

### The data

- **Source:** Geobasis NRW, *3D-Messdaten Laserscanning (LAS)*: classified point clouds of the
  whole of North Rhine-Westphalia, open data under
  [dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0) (free to use without conditions).
  **Only NRW is covered.**
- **Tiles:** 1 km × 1 km, compressed LAS (`.laz`), about 60–130 MB each, named
  `3dm_32_<E>_<N>_1_nw.laz`. `<E>` and `<N>` are the easting and northing of the tile's
  south-west corner in kilometres, in UTM zone 32N (ETRS89, EPSG:25832).
- **Download folder:** <https://www.opengeodata.nrw.de/produkte/geobasis/hm/3dm_l_las/3dm_l_las/>.
  Opened in a browser it lists every tile with size and date; the tile itself is that address
  plus the file name. The same folder has `3dm_meta.zip` with the official documentation.
- **How much:** a scene of 3 × 3 km around your home usually touches 16 tiles (the square
  rarely lines up with the kilometre grid), so 1–1.6 GB to download. The scene itself is much
  smaller (about 70 MB for 3 × 4 km); the tiles can be deleted afterwards if you won't rebuild.

Choose the area generously: coverage simulations, links and walks can only be computed where the
scene has data. `--radius 1500` (3 × 3 km) is a good start for a town; the scripts take any
radius or an exact `--bbox` in EPSG:25832.

### Step 1a: download with the script (recommended)

Set `MESHPLAY_HOME` in `.env` first; the area is a square around it.

```powershell
python scripts/sim_fetch_tiles.py --radius 1500              # list: names, sizes, what you have
python scripts/sim_fetch_tiles.py --radius 1500 --download   # download into data/sim/laz/
```

The list marks tiles you already have and tiles the server doesn't have (outside NRW). The
download skips tiles that are present, so an interrupted download can simply be started again
(delete a leftover `*.part` file first).

### Step 1b: download by hand

If the script can't reach the server (proxy, firewall) or you want to pick the tiles yourself:

1. **Find the tile names.** Convert your position to UTM32, e.g. with the project's own
   converter (longitude first):

   ```powershell
   python -c "from meshplay.sim.sites import to_utm; print(to_utm(7.0988, 50.7374))"
   # (365847.2..., 5622347.1...)  ->  E = 365, N = 5622  ->  3dm_32_365_5622_1_nw.laz
   ```

   That is the tile you stand in; add its neighbours (E ± 1, N ± 1, …) until the area is
   covered. `python scripts/sim_fetch_tiles.py --radius 1500` (without `--download`) prints the
   exact list for a radius, even if you then download by hand.
2. **Download** each file from the download folder above (browser, or any download manager).
3. **Put the files** unchanged into `data/sim/laz/` (create the folder).

### Step 2: build the scene

```powershell
python scripts/sim_build_scene.py --radius 1500             # same area as the download
```

This reads the tiles and writes `data/sim/scene/` (`scene_raw.npz`, `scene_cls2.npz`,
`scene_meta.json`): terrain from the ground points, surface from the points above ground, and
per 1 m cell whether it is a building or vegetation (the laser scan has no building class; the
share of last returns and of multiple returns separates roofs from trees). Missing tiles are
reported and their area interpolated; cells without any data are marked as not measured. A
3 × 3 km scene takes about a minute and about 1 GB of RAM. To rebuild (e.g. a larger area), add
`--force`.

### Step 3: use it

Restart the map app (`python scripts/mapapp.py --open`). The first start after a new scene
prepares the 3D view (about a minute, shown in the terminal). Then:

- the 3D view shows terrain, buildings and trees; the layer *Laserscan-Szene* shows the extent
  and unmeasured areas;
- the layer *Strecke A → B* computes links;
- *Aufgaben → ＋ Abdeckung simulieren* computes coverage maps, and the walk layer can compare
  measurements with the models (*Messung − Modell*).

### Outside North Rhine-Westphalia

The code reads the NRW product only. Other airborne laser scans (several German states and many
countries publish them) can work, but need their own import: `Scene` in
`src/meshplay/sim/scene.py` expects 1 m rasters of terrain, surface height above ground,
building and vegetation masks and a measured mask, and the building/tree separation relies on
the NRW point classes (class 20 = last return, not ground). A second importer is a welcome
contribution.

## Coverage simulation

Predicts link quality with seven ITU-R model families over the scene, and scores them against
measurements. Models, pipeline and the review of the original code:
[docs/simulation.md](docs/simulation.md).

With a scene in place (see above):

```powershell
Copy-Item config\sites.example.json data\sim\sites.json     # then enter your own sites,
                                                            # or add them in the map app
python scripts/sim_coverage_map.py --site HOME              # coverage map per model
```

Coverage maps are easier from the map app: *Aufgaben → ＋ Abdeckung simulieren* (5–15 minutes
for 800 m at 25 m), then *Anzeigen*. Sites are added and edited in the map app (layer *Eigene
Standorte*, ⚙). For fixed-point measurements, frozen predictions and the relay search, see
[docs/simulation.md](docs/simulation.md).

## Official web client

The official Meshtastic web client can run locally in Docker (Docker Desktop must be running):

```powershell
docker compose up -d        # then open http://localhost:8080 in Chrome or Edge
docker compose pull; docker compose up -d   # update
docker compose down         # stop
```

In the page choose **New Connection → Serial** and pick your node's port (Web Serial works in
Chrome and Edge only). While the page is connected, the port is busy for the scripts and the
map app. The same client is hosted at https://client.meshtastic.org.

## Configuration

`.env` (copy from `.env.example`; not committed):

| Variable | Default | Meaning |
|---|---|---|
| `MESHTASTIC_PORT` | auto | serial port, e.g. `COM8` (Windows) or `/dev/ttyACM0` (Linux) |
| `MESHPLAY_HOME` | – | home node position as `lat,lon`: map centre, distances, nearest site, tile area |
| `MESHPLAY_DATA_DIR` | `data` | where logs, exports and simulation data go |
| `MESHPLAY_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Also configurable:

- **Modem preset:** the simulation, the map app and the walk analyses assume **ShortSlow**
  (the preset of the author's local mesh) unless a log records the preset or `--preset` is
  given. Change `DEFAULT_PRESET` in `src/meshplay/config.py` if your mesh uses another one.
- **Sites:** `data/sim/sites.json`, from [config/sites.example.json](config/sites.example.json)
  or the map app's sites editor.
- **Map app layer defaults:** `data/mapapp/layers.json`, from
  [config/mapapp.example.json](config/mapapp.example.json).

## Project layout and data

```
src/meshplay/          shared code: device connection, settings, packets, walks, probes
src/meshplay/sim/      coverage simulation: ITU models, LiDAR scene, link prediction
src/meshplay/mapapp/   map app server: layers/, tools/, background tasks, sites editor
webmap/                map app page (HTML, CSS, JavaScript modules)
scripts/               command-line tools (see Scripts)
config/                example configuration
docs/                  longer documentation
experiments/           throwaway explorations, one dated folder each (copy _template/)
tests/                 pytest tests
data/                  everything local (not committed, see below)
```

`data/` is never committed; it holds your measurements and everything personal:

| Path | Content |
|---|---|
| `data/packets/<date>.jsonl` | every received packet (`listen.py`, map app with `--device`) |
| `data/probes/<date>.jsonl` | traceroute results of walks |
| `data/tracks/*.gpx` | phone GPX tracks |
| `data/maps/` | walk maps from `coverage_map.py` |
| `data/exports/` | node list exports |
| `data/sim/` | sites, laser-scan tiles, scene, predictions, coverage grids ([details](docs/simulation.md#data-not-committed)) |
| `data/messages.jsonl` | messages sent and received in the map app |
| `data/mapapp/` | map app: 3D export, caches, background task logs, layer defaults |

## Troubleshooting

- **"could not open port … PermissionError" (German Windows: "Zugriff verweigert"):** another
  program holds the serial port — usually the web client in Chrome/Edge (close the tab, not just
  *Disconnect*), `listen.py`, or a map app started earlier with `--device`. Only one program at a
  time can use it.
- **"No Meshtastic device found":** check the cable (some are charge-only), then set
  `MESHTASTIC_PORT` in `.env`. On Windows the port is listed in Device Manager → Ports (COM & LPT).
- **Map background blank:** open maps with `--open` (a local web server), not as a file.
- **3D view empty or "Keine Laserscan-Szene":** download the tiles and build the scene
  ([3D laser-scan data](#3d-laser-scan-data)), then restart the map app. The 3D view also needs
  WebGL in the browser.
- **`sim_fetch_tiles.py` says "not available" or "No tiles for this area":** the area is
  outside North Rhine-Westphalia, or `MESHPLAY_HOME` is wrong (latitude first).
- **The map app doesn't show a change:** reload with Ctrl+F5; after updating the code, restart
  `mapapp.py`.
- **A traceroute walk gets no answers:** tracker switched on, same private channel (name and
  key) on both nodes, same LoRa region and preset, home node connected (map app: *Gerät (USB)*).

## Writing your own code

```python
from meshplay import connect

with connect() as iface:  # finds the port like the scripts do
    print(iface.getMyNodeInfo())
    iface.sendText("hello")
```

`iface` is a `meshtastic.serial_interface.SerialInterface` from the
[Meshtastic Python library](https://python.meshtastic.org). For a quick exploration, copy
`experiments/_template/` to `experiments/<date>-<name>/` (see
[experiments/README.md](experiments/README.md)).

## Development

```powershell
pytest                 # unit tests, no device needed
pytest -m hardware     # tests that talk to a connected device
pytest -m data         # regression test against a local reference scene (data/sim/reference/)
ruff check .           # lint
ruff format .          # format
```

After adding or upgrading dependencies in `pyproject.toml`, refresh the lock file:

```powershell
python -m pip freeze --exclude-editable | Out-File -Encoding utf8 requirements.lock
```

## Credits and data sources

- The simulation, the fixed-point logger and the 3D viewer are ported from the *Mesh Bonn*
  project (state 2026-09-20); what was changed and why is in
  [docs/simulation.md](docs/simulation.md#review-of-the-original-code).
- ITU-R P.1812-6 is a Python translation of the ITU-R WP 3K reference implementation
  ([eeveetza/p1812](https://github.com/eeveetza/p1812)) and reproduces its 63 official
  validation cases; it keeps the ITU licence.
- Laser-scan data: Geobasis NRW, *3D-Messdaten Laserscanning*,
  [dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0).
- Map tiles: © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
- [Meshtastic](https://meshtastic.org) and its Python library; Leaflet, three.js, proj4js.

## Licence

[MIT](LICENSE), with one exception: `src/meshplay/sim/p1812.py`, a Python translation of the
ITU-R P.1812 reference implementation, stays under the ITU's licence
([LICENSE-ITU-P1812.txt](LICENSE-ITU-P1812.txt)). The data you download (laser scan, map tiles)
keeps its own licence (see above). Contributions and forks are welcome; for coding agents, the entry
point is [CLAUDE.md](CLAUDE.md).
