# Coordination mode – implementation plan

Companion to [coordination-design.md](coordination-design.md). Nothing here is built yet.
The plan is ordered so that every step leaves `main` working, passes `pytest` and `ruff`, and
can be tried in the page with the simulated radio before anything touches the mesh.

## 0. Working without disturbing the running app

The map app the owner runs for experiments imports `meshplay` from this checkout (editable
install) and serves `webmap/` from it. Editing files here changes what the next server start
does, and a bad edit breaks a restart mid-experiment. So:

- Develop in a **git worktree** on a branch: `git worktree add ../meshtastic-coord -b coord`.
  The worktree has its own `src/` and `webmap/`; the checkout the experiments use stays as it
  is. (The usual rule "commit straight to main" is set aside for this feature at the owner's
  request; the branch is merged fast-forward, step by step, when the owner says the
  experiments allow a restart.)
- Run the development server from the worktree with the existing venv:
  `$env:PYTHONPATH = "..\meshtastic-coord\src"` puts the worktree's package ahead of the
  editable install; `WEB_DIR` follows `__file__`, so it serves the worktree's page too.
  `--port 8772` and `MESHPLAY_DATA_DIR=data-dev` (a copy of `sites.json`, `.env` home, later the
  road graph) keep it away from the live server's port, packet logs, message store and task
  files. `--simulate` (step 1) means it never opens the serial port.
- Steps are merged in the order below; each is a commit that works on its own. The first
  three touch shared files (`device.py`, `server.py`, `main.js`) in small, additive ways; the
  rest is new files.

## 1. Simulated radio (`--simulate`)

Closes the gap CLAUDE.md assumes exists, and every later step is tried with it.

- `src/meshplay/mapapp/fake_device.py`: `FakeInterface` modelled on the `FakeIface` in
  `tests/test_mapapp_messages.py`, extended:
  - `nodes` with two fake nodes (`!fa4e0001` "SIM tracker", `!fa4e0002` "SIM client"),
    `localNode.channels` (0 primary, 1 "Privat"), `getMyNodeInfo()` = `!fa4e0000` "SIM home",
    `myInfo.my_node_num`.
  - `sendData(...)` stores the packet, acks it after 0.5 s on a timer thread (calls
    `onResponse` like the firmware; `onResponseAckPermitted` respected), and if the text is a
    coordination command echo (`?`, `?r`, …) it is *received* again from the tracker 1 s later
    so command handling can be tried by typing in the pane's DM conversation.
  - `replay(track: list[dict], speed: float)`: a thread that publishes `POSITION_APP` packets
    of `!fa4e0001` from a GPX track (`meshplay.walk.load_gpx`) at the track's own pace × speed,
    through the same `pubsub` topic `meshtastic.receive` the real interface uses, so
    `DeviceLink._on_receive` needs no change. Precision bits 32, `groundSpeed` from the track.
  - `close()` stops the threads.
- `DeviceLink.connect(port)` accepts `port="sim"` (and `_connect` builds a `FakeInterface`
  instead of a `SerialInterface`); `status()` shows `port: "sim"`.
- `scripts/mapapp.py --simulate [track.gpx]`: sets the device to `sim` and passes the track to
  the fake interface (`run(..., device="sim", simulate_track=path)`). Without a track the
  fake tracker sits at home + 300 m.
- Tests: `tests/test_fake_device.py` (ack arrives, replay publishes positions with the track's
  timing, commands echo back).
- UI: the device section shows "Simulation" instead of a port; the pane works unchanged.

## 2. Hooks in existing code (small, additive)

- `device.py`
  - `self.listeners: list[Callable[[dict], None]]`; `_on_receive` calls each with the plain
    packet after `_record`, exceptions logged and swallowed (the logging below must go on).
  - `send_text(text, to, channel, *, on_status=None, tag=None)`: `on_status(status: str)` is
    called from `on_ack` after `set_status`; `tag` (e.g. `"coord"`) is stored in the message
    record so the page can mark it.
  - `MessageStore.add` keeps unknown keys as they are (already does).
- `layers/nodes.py`: `node_row` gets `"favorite": bool(node.get("isFavorite"))`.
- `server.py`: `ctx.coord = Coordinator(ctx)` after the job manager; `do_GET`/`do_POST`
  branch `parts[:2] == ["api", "coord"]` → `ctx.coord.api(method, parts[2:], query, body)`,
  which returns the JSON to send (raises `ValueError`/`KeyError` like everything else);
  `shutdown()` on exit. `messages_since` passes `tag` through (it already sends whole records).
- `messages.js`: skip the toast for incoming DMs when a coordination mission for the sender is
  active (`M.coordNodes`, set from `/api/coord` by `coord.js`); bubbles of tagged messages get
  a small "⌖" prefix.
- `util.js` `featureHTML`: a `data-set="coord"` button *Ziel zuweisen* when `_node_id` and not
  own; `main.js` `onFeatureAction` routes `"coord"` to `coord.js`.
- `main.js`: `pickOnMap(label, cb, {multi: true})` keeps collecting clicks and shows *Fertig*
  in the banner; `cb(points)` on finish, Esc cancels. `map2d.setTemp` grows a `setTempPath(points)`
  that draws the points and the connecting line while picking.
- Tests: `test_mapapp_messages.py` gains `listeners` and `on_status` cases; `test_i18n` needs
  the new strings in `en.json`/`fr.json`.

## 3. Package `coord/`, phase "core" (single stop, straight-line guidance)

### 3.1 `coord/store.py` – files under `data/coord/`

```python
class CoordStore:
    def __init__(self, data_dir: Path)          # data/coord/, created on first write
    settings: dict                              # settings.json, merged over DEFAULTS
    targets: dict[str, dict]                    # targets.json
    paths: dict[str, list[dict]]                # paths.json (step 4)
    areas: list[dict]; places: list[dict]       # step 6
    def load(self) -> None; def save(self, name: str) -> None   # atomic write + .bak like sites_store
    def save_missions(self, missions: dict) -> None; def load_missions(self) -> dict
    def log_event(self, node: str, kind: str, **fields) -> None  # events-<date>.jsonl
```

`DEFAULTS` = the settings of design §7 minus the ones that come in later steps; each has a
`Setting` declaration in `coord/settings.py` (`SETTINGS: list[Setting]`) so the page builds the
form with `forms.js` and the server validates with `Setting.parse` + min/max like `JobManager.create`.

### 3.2 `coord/geo.py` – small geometry helpers (no shapely needed here)

`distance_m` (reuse `meshplay.walk.distance_m`), `bearing_deg(a, b)`, `compass(bearing) -> "NE"`,
`fmt_dist(m) -> "350m" | "1.2km"`, `fmt_eta(s) -> "~11min"`, `local_hhmm(ts)`, `parse_hhmm(text, now)`
(accepts `12:55` and `+15`).

### 3.3 `coord/phrases.py` – radio texts and commands

```python
PHRASES = {"de": {...}, "en": {...}}   # keys: assign, assign_route, assign_nopos, status, route,
                                       # offcourse, late, early, nogo_in, nogo_ahead, place, confirm,
                                       # reached_hold, reached_next, reached_final, resume, changed,
                                       # ended, halt_ok, help, aborted
def phrase(lang: str, key: str, **params) -> str      # fills, then check_text() (≤ 200 bytes)
def fit(lang, key, params, legs: list[str]) -> list[str]  # drops legs / splits into two texts to stay ≤ 80 bytes
def parse_command(text: str) -> str | None          # "?"→"status", "?r"→"route", "?z"→"target",
                                                    # "?p"→"path", "?h"/"?…"→"help", "ok", "halt", "go", "x"; else None
```

Tests: every key in both languages with 24-char names stays ≤ 80 bytes (or is split), no
missing placeholder, `parse_command` on whitespace/case variants and on plain chat.

### 3.4 `coord/paths.py` – waypoints (single stop in this step, the model is final)

```python
@dataclass
class Waypoint: name: str; lat: float; lon: float; kind: str = "stop"; radius_m: float = 30
                arrive_by: float | None = None; hold_until: float | None = None
def parse_path(raw: list[dict], now: float, defaults: dict) -> list[Waypoint]   # ValueError with a sentence
def current_stop(path, index) -> Waypoint
```

Validation: names by `sites_store.NAME_RE`, last waypoint is a stop, `hold_until ≥ arrive_by`,
radius 5–500 m, at least one waypoint.

### 3.5 `coord/missions.py` – the `Coordinator`

```python
class Mission:                      # plain object, to_json()/from_json()
    node, lang, profile, path: list[Waypoint], index: int, state: str, held: bool
    created, assigned_at, arrived_at, hold_arrived_at
    positions: deque[dict] (50)     # {time, lat, lon, bits, snr, rssi, hops, speed}
    route: Route | None             # step 5; None = straight line
    metrics: dict                   # recomputed on every position (design §4)
    sent: dict[str, float]          # last send time per message kind (rate limits)
    flags: set[str]                 # "offcourse", "late", "early", "nogo:<id>", "place:<id>" – armed warnings
    messages: list[dict]            # {time, text, id, status}
    events: deque[dict] (200)

class Coordinator:
    def __init__(self, ctx)         # store, missions from disk, dev.listeners.append(self._on_packet)
    enabled: bool                   # persisted in settings.json ("enabled"); requires dev.state == "verbunden" to turn on
    def api(self, method, parts, query, body) -> dict     # dispatch table for /api/coord/*
    def assign(self, node, path_raw, profile, lang) -> Mission   # replaces; sends the first leg (if enabled)
    def edit_path(self, node, path_raw) -> Mission               # step 4
    def send_now(self, node, what: "status"|"route") ; def next_stop(node); def end(node, notify)
    def _on_packet(self, p)         # enqueue only (queue.Queue)
    def _worker(self)               # positions → _on_position; texts → _on_command
    def _tick(self)                 # every 10 s: holds over, stale positions, retries, persistence
    def _on_position(self, m, pos)  # accept (bits ≥ min, dedupe), metrics, decisions (design §5), one combined send
    def _on_command(self, m, cmd)
    def _send(self, m, kind, text, priority=False) -> bool   # rate limit, dev.send_text(tag="coord", on_status=…), log
    def layer_features(self) -> list[dict]   # for layers/coord.py
```

Position packets: `p["decoded"]["portnum"] == "POSITION_APP"` and `p["fromId"]` (or
`!{from:08x}`) is a mission node; fields `latitude`, `longitude`, `precisionBits`,
`groundSpeed`, `time`. Texts: `TEXT_MESSAGE_APP` addressed to us (`to == my_num`).
Node database seed: on `assign`, the last known position from `dev.nodes()` if younger than
`stale_min`.

Rate limits: `_send` refuses proactive kinds when `now - max(sent.values()) < min_gap_s`
unless `priority` (then ≥ 30 s). Replies to commands bypass. Delivery: `on_status` updates the
mission's message and, for `assign`/`reached`/`next`, schedules one retry after 60 s on
"nicht zugestellt".

Threading: `_on_packet` runs on the serial reader thread and only puts on the queue. The
worker owns all mission state; API calls hand work to the worker through the same queue with a
`threading.Event`/result slot (or take the coordinator lock; the lock is simpler and the work
is short – choose the lock, keep sends outside it).

Persistence: `missions.json` written after every state change, throttled to once per 2 s from
the tick thread if positions arrive fast; restored on start with state kept (a *holding*
mission whose `hold_until` passed while the server was down gets its next leg on the first
tick if the mode is on).

Tests (`tests/test_coord_missions.py`, `FakeIface` from the messages test):
assign → one DM with the expected text; position inside the radius → *arrived* text;
three positions moving away → `!KURS`; rate limit; `?`, `halt`/`go`, `x`; a DM from a node
without a mission → no send; mode off → no send at all; restart restores; `bits < 24` refused
with an event.

### 3.6 `coord/settings.py`, API and layer

- `SETTINGS` declarations; `POST /api/coord/settings` validates and saves; `enabled` is
  toggled by `POST /api/coord/mode` only, refused without a connected device.
- `GET /api/coord` → `{enabled, device_state, settings, declarations, missions: [to_json()],
  targets, paths, areas, places, osm}`; the page polls it every 3 s while the section is open
  and the mode is on, else 10 s.
- `layers/coord.py`: `CoordLayer` (`id="coord"`, group "Meshtastic", `enabled_by_default=True`,
  no settings): stops as `_icon` flags (`symbol: "target"`, number as text), via points as
  small dots, the trail as a line, the route (step 5), areas and places (step 6); `refresh_s`
  10 while enabled; `note` when the mode is off. `icons.js` gets the `target` symbol.

### 3.7 Page: `webmap/js/coord.js`

- `initCoord({store, toast, openInspector, updateInspector, pickOnMap, tempMarker, focusNode,
  reloadLayer})`; rail section `<details data-sec="coord">` in `index.html` between *Aufgaben*
  and *3D-Darstellung*; header badge "Koordination · n" on `#btnCoord`.
- Section content: mode switch + state line; ⚙ settings (`inputsHTML`/`bindInputs`, saved with
  `POST settings`); **＋ Einsatz** form: node `<select>` (live node rows, favourites first,
  then by last heard; preset from the popup/node list), path builder (this step: one stop:
  from targets, from sites, or *auf der Karte wählen* + name), profile, language; mission
  cards; targets editor (like `sites.js`).
- Inspector tab `einsatz` (`#t_coord`), added to `TABS` in `main.js`: metrics table, messages
  with delivery state, event log; refreshed with the poll while shown.
- Toasts on transitions (compare states between polls, as `tasks.js` does).
- i18n: all new strings in `en.json`/`fr.json`; the radio phrases are *not* `t()`/`_()` texts.

### 3.8 Docs

`docs/mapapp.md`: section "Koordination" (how to use, what it transmits, the simulate flag);
`README.md`: one paragraph and the tracker settings it needs (precise location, 30 s);
CLAUDE.md: the `--simulate` flag under Radio.

## 4. Paths: waypoints, times, editing (design §2)

- `paths.py`: `advance(m, pos, now) -> list[str]` returns the events (`"via"`, `"reached"`,
  `"reached_final"`, `"hold"`, `"early"`, `"hold_over"`); `schedule(m, eta_by_stop) -> dict`
  (margin per timed stop, late flag); `diff_current_leg(old, new) -> bool` for editing.
- `Coordinator.edit_path`, `next_stop`, `?p`; the tick handles `hold_until`.
- Page: path builder with rows (name, kind, radius, `arrive_by`, `hold_until`, ↑↓, ✕), map
  clicks to add (multi pick), draggable waypoint markers while editing (`map2d.editPath(points,
  onMove)`), *Pfad bearbeiten* on a running mission, path templates (save/load by name).
- Layer: numbered flags, via dots, the current stop highlighted.
- Tests: `tests/test_coord_paths.py` as in the design §10.

## 5. Routing over OSM (design §6)

### 5.1 Download task `coord/osm.py` + `JobKind` `osm`

Settings of the task: `bbox` (text `south,west,north,east`; default = home ± `radius_m`
snapped outward to 0.01°; the owner enters Bonn's box, e.g. `50.63,7.02,50.77,7.21` – every
user downloads their own area, the graph file is never committed), `name` (file stem, default
`roads`). Query (Overpass QL, `timeout` 300, `maxsize` 512 MB):

```
[out:json][timeout:300];
way["highway"]["highway"!~"^(proposed|construction|abandoned|razed|platform|raceway|bus_stop|elevator)$"]
   ["area"!="yes"](south,west,north,east);
out body geom;
```

`out body geom` returns each way with its tags and the coordinates of its nodes, so no second
query is needed. Bonn-sized: roughly 30–60 MB of JSON, one to two minutes. The task streams
the download to `data/osm/<name>.overpass.json` (progress by bytes), then builds the graph
(progress by ways) and writes `data/osm/<name>.json.gz`, deleting the raw answer. A
`User-Agent: meshplay/0.1 (coordination mode)` header and one retry after 60 s on HTTP 429/504.
`scripts/coord_import_osm.py <file.osm|.osm.pbf>` (optional `osmium`) produces the same graph
file for people who prefer an offline extract.

### 5.2 Graph file

```
{"bbox": [s, w, n, e], "source": "overpass", "downloaded": "2026-09-29T…", "n_ways": …,
 "nodes": [[lat, lon], …],                       # index = node id in the edges
 "edges": [[a, b, length_m, [[lat, lon], …], {"highway": "residential", "name": "…",
            "oneway": "yes"|null, "access": …, "foot": …, "bicycle": …, "motor_vehicle": …}], …]}
```

Build: count node uses across ways; a graph node is a way end or a node used by ≥ 2 ways;
each way is split at graph nodes into edges with their intermediate geometry. Tag subset kept
as above (`sidewalk`, `surface` not needed). Expected for Bonn: ~60 000 graph nodes,
~80 000 edges, ~10 MB gzipped, load ≈ 1 s.

### 5.3 `coord/routing.py`

```python
class RoadGraph:
    @classmethod load(path) -> RoadGraph                  # adjacency lists, edge arrays, STRtree of edge LineStrings (UTM)
    def nearest(self, lat, lon, max_m=200) -> Snap | None  # edge index, projected point, distance along the edge
    def route(self, a: Snap, b: Snap, profile: str, blocked: set[int]) -> Route | None
class Route: coords (lat/lon), length_m, edge_ids, line_utm (shapely), legs: list[Leg]
class Leg: turn ("N".."NW" for the first, "L"/"R"/"G"/"U"), dist_m, name: str | None
def profile_cost(edge_tags, profile) -> float | None      # None = not allowed (design §6 profiles)
def legs_for(route) -> list[Leg]; def legs_text(legs, n) -> str   # "N200 L300 Hauptstr R150 Z60"
def progress(route, lat, lon) -> tuple[float, float]       # (along_m, off_m) via shapely project/distance
def route_path(graph, start, waypoints, profile, blocked) -> list[Route]   # one per segment
```

Algorithm: A* with the straight-line distance as heuristic (admissible for length costs;
profile costs are lengths × factor ≥ 1, so keep the heuristic on plain length). Virtual start
and end nodes from the snaps (connected to both edge ends with partial lengths). Per profile
the allowed-edge mask is cached on the graph. `blocked` (no-go, step 6) is a set of edge ids
computed once per areas revision.

Short street names: `name` ≤ 12 chars after `straße→str`, `Straße→Str`, `-weg` kept, else
omitted. Turn code from the heading change at the graph node between consecutive edges;
< 30° merges, `G` only when a junction with ≥ 3 edges is crossed straight.

### 5.4 Missions with routes

- `assign` and re-routes call `route_path`; `metrics` gain route remaining, progress, off-route;
  decisions 5 and 8 of design §5; instruction modes `request`/`turns`/`interval`.
- No graph or snap failure → straight line for that segment, `metrics.mode = "line"`.
- `GET /api/coord/route?node=&path=…` preview for the page (no radio).
- Layer: route per mission (colour by state), the next turn point highlighted.
- Page: the section shows the graph state (name, bbox, date, nodes) and **Straßennetz laden …**
  with the one-sentence explanation and the bbox input; the mission card shows the legs and
  the exact text the node would get.
- Tests: `tests/test_coord_routing.py` on a synthetic 6×6 grid graph written by the test
  (no download): shortest paths per profile, one-way, blocked edges, snapping and virtual
  nodes, leg merging and turn codes, `progress`, multi-segment `route_path`; a build test on a
  tiny hand-written Overpass answer (three ways, one junction).

## 6. Areas and places (design §5 items 4 and 7)

- `coord/areas.py`: `AreaSet(areas, places)` with UTM polygons (shapely), `inside(lat, lon)`,
  `blocked_edges(graph) -> set[int]` (edges whose line intersects a no-go polygon buffered by
  `buffer_m`), `ahead(route, along_m, 200) -> area | None`, `near_places(lat, lon)`, entry/exit
  hysteresis per mission (`flags`).
- Editors in the page (multi pick for polygons, drag to move vertices; places as a point with
  radius and text); layer drawing (hatched red / blue / circles).
- Tests: point-in-polygon, blocked edges on the grid graph, re-arming, message texts.

## 7. Extras (later, each small)

Periodic confirmations (`confirm_every_min`), OSM-derived no-go suggestions from a second
Overpass query (`landuse=military`, `access=no` areas), position requests (needs a test of the
firmware's reaction to a position packet without coordinates before it is offered), an
English radio catalogue review by a native speaker.

## 8. Order of merges and what to check each time

| Step | Merge when | Check |
|---|---|---|
| 1 simulate | any time (new file + one `connect` branch) | `pytest`, `mapapp.py --simulate` shows the fake node and positions |
| 2 hooks | next restart of the live server | `pytest`, pane still sends and shows acks with the real device |
| 3 core | after a full run with `--simulate` and a GPX | tests above; empty `MESHPLAY_DATA_DIR` start; `test_i18n` |
| 4 paths | same | |
| 5 routing | after the Bonn download has been done once in `data-dev/` | route preview on the map; a replayed walk gets turns and an off-course correction |
| 6 areas | same | |

First real transmission: only after the owner switches the mode on with the live device,
with one favourite node, watching the pane; `min_gap_s` at its default; the first mission a
single stop 300 m away.

## 9. Estimated size

| Step | New code | Changed code |
|---|---|---|
| 1 | ~200 lines Python | ~20 |
| 2 | ~60 | ~80 Python, ~60 JS |
| 3 | ~900 Python, ~500 JS, ~120 HTML/CSS | ~40 |
| 4 | ~250 Python, ~250 JS | – |
| 5 | ~600 Python, ~120 JS | – |
| 6 | ~250 Python, ~200 JS | – |
