# Map app

One map with two views, switchable: **2D** (OpenStreetMap, Leaflet) and **3D** (the laser-scan
scene, three.js). Data layers are drawn on both views. Tools compute on demand; the first one
is the direct-link calculator.

```powershell
python scripts/mapapp.py --open          # http://localhost:8770
```

The map app needs the simulation extras (`pip install -e ".[sim]"`). The 3D view, the link
calculator and the coverage simulation also need a laser-scan scene (`scripts/sim_build_scene.py`,
see [simulation.md](simulation.md)); without one they stay empty and the rest works. The first
start after a new scene exports the 3D data (about a minute, `data/mapapp/scene/`). The page is
in German.

## Using it

- **Header**: 2D / 3D view, **Nachrichten** (messaging pane, shows unread messages),
  **Aufgaben** (background tasks, shows how many run), **Details** (the right panel; greyed out
  while there is nothing to show), light/dark.
- **Left column**: *Ebenen* (layers), *Aufgaben*, *3D-Darstellung* (only in 3D), *Gerät (USB)*.
  Sections fold and remember their state.
- **Ebenen**: each layer has a checkbox and a ⚙ with its settings and legend. Settings are
  remembered per browser.
- **Strecke A → B** (first layer) is the direct-link calculator. While it is on, a click in the
  map sets A or B ("Klick in die Karte setzt: A | B | aus", Esc ends it); while it is off, map
  clicks do nothing. Sites, nodes and walk points have "als A" / "als B" in their popup, which
  also switches the layer on. Each endpoint has antenna height (range), placement (outside, open
  window, closed window with old or low-E glazing), device (whip or T1000-E) and the building/tree
  height around it; the link is recomputed on every change. Preset default: ShortSlow.
- **Right panel (Details)** shows only tabs with content: *Strecke* and *Modelle* after a link
  (verdict, levels, height profile with Fresnel zone, all model families, methodology folded at
  the bottom), *Rundgang* when the walk layer compares with the models, *Knoten* (the node list,
  see below) while the node layer is on, *Aufgabe* for the log of a background task. ✕ closes it,
  **Details** brings it back.
- **3D**: drag rotates, right button or Shift+drag pans (the ground follows the cursor), wheel
  zooms; *Übersicht* and *Strecke zeigen* sit in the top right corner of the 3D view.

## Messages and node list

A comfort add-on for simple messaging and watching the traffic while using the map; for anything
more, use a Meshtastic app. Needs the device connected (**Verbinden** under *Gerät (USB)*, in the
pane's bar, or `--device`).

- **Messaging pane** under the map (**Nachrichten** in the header, or click its bar). Left the
  conversations: the device's channels (`0 · Primär`, `1 · Privat`, …), direct conversations with
  nodes, and *Alle Pakete*. Right the messages of the selected one and the input: **Enter** sends,
  Shift+Enter starts a new line; the counter shows the bytes (at most 200).
- **Sent messages** show their state: *gesendet*, then *zugestellt* (a direct message the recipient
  confirmed), *im Netz* (a channel message another node was heard relaying) or *nicht zugestellt*
  with the firmware's reason. Received ones show sender, time, SNR and hops; the sender's name
  shows the node on the map.
- **Direct messages** go to a node with channel 0 (the firmware encrypts them for the recipient
  when it knows its key). Start one with **Nachricht** in a node's popup on the map or ✉ in the node
  list. A new direct message pops up as a notice; unread counts are in the header, the pane's bar
  and the conversation list.
- **Alle Pakete** lists the packets received since the map app started (time, sender, recipient,
  type, channel, SNR, hops): the traffic around your node.
- Messages are kept in `data/messages.jsonl`; the traffic list only in memory.

The **Knoten** tab in the right panel lists the same nodes as the layer *Meshtastic-Knoten* (same
source, live or export, and the same age filter), including those without a position: short and
long name, ID, hardware, battery, hops, SNR, last heard. Filter by name or ID, sort by last heard,
hops, SNR or name. A click on a node with a position centres it on the map and opens its popup;
✉ opens a direct conversation.

## Background tasks (Aufgaben)

Long jobs run in the server, not in the page: closing or reloading the page doesn't stop them.
**＋ Traceroute-Rundgang** and **＋ Abdeckung simulieren** open a form (defaults and last used
values); **Starten** checks the input and starts the task. The list shows state (wartet, läuft,
fertig, Fehler, abgebrochen), progress, what the task is doing and how long it runs, with
*Stoppen/Abbrechen*, *Protokoll* (live log in the right panel), *Entfernen* for finished tasks and
follow-ups (*Anzeigen* for a coverage grid, *GPX-Spur hochladen …* after a walk). A notice pops up
when a task ends or fails.

- One task per kind runs at a time; more of the same kind wait in line.
- Tasks are stored in `data/mapapp/jobs/` (`<id>.json`, `<id>.log`); after a restart of the
  server the list is back, and tasks that were running are marked *abgebrochen*.
- **Traceroute-Rundgang** is `scripts/probe_walk.py` inside the server: it uses the map app's USB
  connection (connecting if needed), so the node layer and packet logging keep working. Stopping
  it is its normal end. The device can't be disconnected while it runs. Afterwards upload the
  phone's GPX track (*GPX-Spur hochladen …*, stored in `data/tracks/`): the walk layer then shows
  the probes on the track.
- **Abdeckung simulieren** runs `scripts/sim_coverage_map.py` as a separate process (progress
  from its row counter, *Abbrechen* ends the process). The grid is named after its setup,
  `coverage-<site>-<preset>-<placement>-<radius>m-<step>m[-winter].npz`, and carries it as
  metadata, which the layer *Simulierte Abdeckung* shows in its selection.

## Own sites (Eigene Standorte)

The ⚙ of the layer *Eigene Standorte* has an editor for `data/sim/sites.json`: **＋ Neuer
Standort** and a click in the map place a site (the clutter height is suggested from the laser
scan: highest surface within 10 m), ✎ edits name, description, antenna height and clutter, ⌖
moves a site (click in the map), ✕ deletes it. A rename also renames the references in variants
(`same_as`), scenarios and the corridor; a site that is still referenced can't be deleted, the
editor lists what uses it. Each change keeps the previous file as `sites.json.bak`.

## Live device

```powershell
python scripts/mapapp.py --open --device          # or --device COM8
```

With `--device` (or **Verbinden** in the sidebar) the server keeps the USB connection to your
node open. Then:

- the node layer's source **Live vom Gerät** shows the device's node list, refreshed every
  15 s (setting), with your own node highlighted;
- every received packet is appended to `data/packets/<date>.jsonl`, exactly like
  `scripts/listen.py` (`--no-log` switches this off), and the walk layer for today refreshes
  itself every 20 s, so you can watch a walk come in.

Only one program can use the serial port. If the web client (Edge, Web Serial), `listen.py` or
another script is connected, the sidebar shows "Zugriff verweigert"; disconnect the other
program and press **Verbinden** again.

## Layers

| Layer | Source | Settings |
|---|---|---|
| Strecke A → B | computed on demand (`/api/tools/link`) | pick A/B, endpoints, preset, trees |
| Eigene Standorte | `data/sim/sites.json` (editable, see above) | labels |
| Meshtastic-Knoten | live from the connected device, or `data/exports/nodes-*.json` (`scripts/export_nodes.py`) | source, refresh interval, colour by hops/SNR, max. age, badge or dot |
| Rundgang (Messung) | position packets `data/packets/<date>.jsonl` (`scripts/listen.py`) or traceroutes `data/probes/<date>.jsonl`, `data/tracks/*.gpx` | date, tracker (positions or traceroutes), GPX track, colour by SNR or measured − model, home site and placement, preset (from the log) |
| Simulierte Abdeckung | `data/sim/maps/coverage-*.npz` (task or `scripts/sim_coverage_map.py`) | calculation (newest first), model, opacity |
| Laserscan-Szene | `data/sim/scene/` | show unmeasured areas |

Default on/off state and default settings per layer: copy
[config/mapapp.example.json](../config/mapapp.example.json) to `data/mapapp/layers.json`.

In comparison mode the walk layer scores every direct packet (and with a GPX track every time
slot) against all model families, like `scripts/sim_compare_walk.py`. The first run takes about
a second per packet; results are cached in `data/mapapp/cache/walk/`.

## Structure

```
src/meshplay/mapapp/
  server.py        HTTP server: page, /scene/* (3D data), /api/app, /api/layers/<id>,
                   /api/tools/<name>, /api/device, /api/jobs, /api/sites, /api/tracks,
                   /api/messages
  device.py        live USB connection: node list, packet logging
  jobs.py          background tasks: manager, task kinds (traceroute walk, coverage simulation)
  sites_store.py   editing data/sim/sites.json
  messages.py      message store for the messaging pane (data/messages.jsonl, traffic list)
  registry.py      Layer base class, Setting, Context (paths, scene, sites), GeoJSON helpers
  style.py         colour scales, PNG encoding
  layers/          one module per data layer, registered in layers/__init__.py
  tools/link.py    direct-link calculator
webmap/
  index.html, css/app.css
  js/main.js       wiring: layer panel, link layer, inspector, notices, map picks
  js/forms.js      forms built from the server's Setting declarations (layers, tasks)
  js/tasks.js      task forms, list, log view
  js/sites.js      sites editor
  js/messages.js   messaging pane
  js/nodelist.js   node list (inspector tab Knoten)
  js/map2d.js      Leaflet view
  js/map3d.js      three.js view (terrain, bodies, draped layers, link, picking)
  js/panels.js     result panel
```

## Adding a layer

Write a module in `src/meshplay/mapapp/layers/` and add an instance to `ALL` in
`layers/__init__.py`:

```python
from meshplay.mapapp.registry import Context, Layer, Setting, collection, feature


class GatewaysLayer(Layer):
    id = "gateways"
    name = "Gateways"
    group = "Meshtastic"
    description = "Where the gateways are."

    def settings(self, ctx: Context) -> list[Setting]:
        return [Setting("min_snr", "Min. SNR", "number", -10, min=-20, max=10)]

    def data(self, ctx: Context, values: dict) -> dict:
        return collection(
            [
                feature(
                    7.10,
                    50.73,
                    _title="GW 1",
                    _fields={"SNR": "3 dB"},
                    _style={"fillColor": "#7b3294", "radius": 7},
                ),
            ]
        )
```

The page builds the settings form from `settings()` (types `select`, `number`, `bool`, `text`;
a select can take its options from another setting via `depends_on` / `options_map`) and draws
the result on both views. Feature conventions (`_style`, `_title`, `_fields`, `_label`, `_z`,
`_icon`, `_endpoint`) and raster payloads are documented in `registry.py`. `_icon` draws a badge
with a symbol (router, tracker, client, sensor, home) and a short text instead of a circle, in
2D as a marker and in 3D as a label at fixed screen size; nodes use it with the short name,
coloured by hops or SNR, faded when not heard for 2 h, your own node with a turquoise ring.

## Adding a task kind

Subclass `JobKind` in `jobs.py` and add it to `KINDS`: `settings()` declares the form (same
`Setting` objects as the layers), `validate()` raises `ValueError` with a message for the page,
`run(ctx, job)` does the work in a worker thread. In `run`, set `job.progress` (0–1, or leave it
`None`), `job.detail` (one line), `job.result` (e.g. a file name) and call `job.add_log()`; check
`job.check_stop()` in loops. For a script, `run_process(job, ["scripts/x.py", ...], on_line)`
streams its output and handles cancelling and exit codes. The page picks the kind up
automatically; follow-up buttons per kind live in `taskActions()` in `main.js`.
