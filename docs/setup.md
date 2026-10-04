# Setup, configuration and troubleshooting

The [README](../README.md) has the short version. This page has the details: requirements,
installation options, configuration, where your data goes, and what to do when something
doesn't work. Commands are for PowerShell on Windows; Linux and macOS should work the same way
with their shell's syntax, but haven't been tried.

## Requirements

- **Python 3.10 or newer** ([python.org](https://www.python.org/downloads/); on Windows tick
  "Add python.exe to PATH" during installation).
- **Git** to get the code, or download the repository as a ZIP.
- **A Meshtastic node with a USB data cable** (some cables only charge), or one paired over
  Bluetooth ([mapapp.md](mapapp.md#live-device)). For walks, a second
  node that you carry (a tracker such as the T1000-E, or any node) and a phone app that records
  a GPX track. Without a node, the map app runs with a simulated radio (`--simulate`).
- **For the simulation and the 3D view:** about 1–2 GB of disk space and 2 GB of RAM for a
  3 × 3 km scene, in North Rhine-Westphalia, Lower Saxony or Schleswig-Holstein (see
  [scenes.md](scenes.md)). Everything else works anywhere.
- Optional: **Docker Desktop** for a local copy of the official Meshtastic web client.

Tested on Windows 11 with a Seeed Wio Tracker L1 Pro as home node and a Seeed T1000-E as
walking tracker. Other Meshtastic devices with USB serial should work.

## Installation

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

What to install:

| Command | Gets you |
|---|---|
| `python -m pip install -e ".[sim,dev]"` | everything: scripts, map app, simulation, test tools |
| `python -m pip install -e ".[sim]"` | everything except the test tools |
| `python -m pip install -e .` | the device scripts only (no map app, no simulation) |

To get exactly the tested versions: `python -m pip install -r requirements.lock`, then
`python -m pip install -e . --no-deps`.

## First check

Plug the node in over USB and close every other program that uses it (the Meshtastic web client
in Chrome/Edge, other scripts). Then

```powershell
python scripts/node_info.py
```

prints your node's name, ID, hardware, firmware, battery and the number of known nodes. If it
says the port is busy or no device was found, see [Troubleshooting](#troubleshooting).

## Configuration

`.env` (copy from `.env.example`; never committed):

| Variable | Default | Meaning |
|---|---|---|
| `MESHTASTIC_PORT` | auto | serial port, e.g. `COM8` (Windows) or `/dev/ttyACM0` (Linux); used only while it exists, else auto-detection. `ble:<name or address>` (e.g. `ble:Meshtastic_1234`) connects over Bluetooth instead |
| `MESHPLAY_HOME` | – | home node position as `lat,lon`: map centre, distances, nearest site, tile area |
| `MESHPLAY_DATA_DIR` | `data` | where logs, exports and simulation data go |
| `MESHPLAY_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Also configurable:

- **Modem preset:** the simulation, the map app and the walk analyses assume **ShortSlow**
  (the preset of the author's local mesh) unless a log records the preset or `--preset` is
  given. Change `DEFAULT_PRESET` in `src/meshplay/config.py` if your mesh uses another one.
- **Sites** (your own antenna positions): `data/sim/sites.json`, from
  [config/sites.example.json](../config/sites.example.json) or the map app's sites editor.
- **Map app layer defaults:** `data/mapapp/layers.json`, from
  [config/mapapp.example.json](../config/mapapp.example.json).
- **Language** of the map app (German, English, French) and light or dark: the map app's
  settings (gear icon at the bottom left).

## Your data

Everything you measure or enter stays on your computer in `data/` (or `MESHPLAY_DATA_DIR`),
which is never committed:

| Path | Content |
|---|---|
| `data/packets/<date>.jsonl` | every received packet (`listen.py`, map app with `--device`) |
| `data/probes/<date>.jsonl` | traceroute results of walks |
| `data/tracks/*.gpx` | phone GPX tracks |
| `data/maps/` | walk maps from `coverage_map.py` |
| `data/exports/` | node list exports |
| `data/sim/` | sites, laser-scan tiles, scenes, predictions, coverage grids ([details](simulation.md#data-not-committed)) |
| `data/messages.jsonl` | messages sent and received in the map app (`messages-sim.jsonl` with `--simulate`) |
| `data/device/<node id>/` | the node's configuration: `profile.yaml` (wanted state) and `backups/`; they hold the channel keys |
| `data/coord/` | coordination mode: settings, targets, paths, areas, places, missions, event log |
| `data/osm/` | road graphs for the coordination mode (from Overpass or `coord_import_osm.py`) |
| `data/tiles/` | cached OpenStreetMap tiles for offline use |
| `data/mapapp/` | map app: 3D export per scene, caches, background task logs, layer defaults |

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

## Troubleshooting

- **"could not open port … PermissionError" (German Windows: "Zugriff verweigert"):** another
  program holds the serial port — usually the web client in Chrome/Edge (close the tab, not just
  *Disconnect*), `listen.py`, or a map app started earlier with `--device`. Only one program at a
  time can use it.
- **"No Meshtastic device found":** check the cable (some are charge-only). Auto-detection
  takes the first port of a known board vendor (Seeed, Adafruit, Espressif, WCH, Silicon Labs),
  else the only other USB serial port; it never takes Bluetooth serial ports ("Standard Serial
  over Bluetooth link"), and a `MESHTASTIC_PORT` that no longer exists is skipped. Otherwise
  pick the port in the map app (*Gerät* lists all ports) or pass `--port`. On Windows the
  port is listed in Device Manager → Ports (COM & LPT).
- **Bluetooth device not found:** the node advertises only while no other Bluetooth client
  holds it — disconnect the phone app. It must be switched on, in range and have Bluetooth
  enabled (on ESP32 boards it is off while WiFi is on).
- **Bluetooth device found, but "noch nicht mit diesem Rechner gekoppelt" ("Insufficient
  Authentication" in the scripts):** pair the node in the system's Bluetooth settings first
  (PIN from the node's display, without a display usually 123456). After a firmware update
  or a changed PIN, remove the pairing there and pair again. A one-off Windows error such as
  "Das Handle ist ungültig" goes away by itself; the map app tries again every 5 s.
- **Map background blank:** open maps with `--open` (a local web server), not as a file.
- **3D view empty or "Keine Laserscan-Szene":** create a scene ([scenes.md](scenes.md)). The
  3D view also needs WebGL in the browser.
- **"not available" or "No tiles for this area" (script or scene task):** the area is outside
  the supported states, or `MESHPLAY_HOME` / the centre is wrong (latitude first).
- **The map app doesn't show a change:** reload with Ctrl+F5; after updating the code, restart
  `mapapp.py`. On Windows two servers can hold the same port: look for an old `mapapp.py`
  process.
- **A traceroute walk gets no answers:** tracker switched on, same private channel (name and
  key) on both nodes, same LoRa region and preset, home node connected (map app: *Gerät*).
