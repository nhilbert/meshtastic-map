# Coordination mode – design

Status: implemented 2026-09-29 (phases 1–4 of the plan: core, paths, routing, areas). Still
open: the extras of section 11 phase 5 (periodic confirmation exists; OSM-derived no-go
suggestions and position requests do not). Where this text and the code differ, the code and
docs/mapapp.md are current; notable differences: the direct messages use a channel setting
(default 1, the private channel) instead of channel 0, and "heading into a no-go area" is
also checked along the node's own direction of travel, not only along the route.

## 1. Use case

**Who.** The *coordinator* sits at the map app; the USB node is the *server node*. One or more
*coordinated nodes* are people in the field carrying a Meshtastic node with GPS (typically a
T1000-E, paired to the Meshtastic phone app so they can read and type direct messages). The
mesh (ShortSlow, private channel) carries everything.

**What.** The coordinator gives each field node a *path*: one target, or a sequence of
waypoints with optional times (be at B by 12:55, don't leave B before 13:05). The server node
tells the field node where to go by short text messages, answers its questions, keeps distance,
speed, ETA and schedule, corrects it when it strays, warns about restricted areas and points of
interest, and confirms every stop. All of that works while the coordinator keeps using the map.

**Typical run.**

1. The coordinator turns coordination mode on (device connected). The page states plainly that
   the server node will now send messages on its own to coordinated nodes.
2. They pick a node (favourites first) and build its path: an existing named target, an own
   site, or clicks on the map, each point named (`ALPHA`, `BRIDGE`, …), with times where needed.
3. The server creates a *mission*, computes the route from the node's last known position
   through the waypoints over the OSM road graph (or falls back to straight lines) and sends the
   assignment for the first stop.
4. The field node moves; its position broadcasts arrive at the server node. The mission updates
   its metrics on every position and decides whether to say something: next turn, off course,
   running late, restricted area ahead, place nearby, periodic confirmation.
5. The field node can ask for status, route or target at any time with short commands, hold
   the guidance, or abort.
6. Within a stop's radius the server confirms arrival with time, distance and duration; if the
   stop has a hold time it says how long to wait, then sends the next leg. At the last stop the
   mission is *arrived*. The coordinator ends it, edits the path or assigns a new one.

**Preconditions and limits.**

- The coordinated node shares its position on a channel the server node has, with *precise
  location* and a short interval (README, "Position broadcasts"; 30 s is right). Reduced
  precision (< 24 bits ≈ coarser than 3 m … 90 m) makes guidance meaningless; the mission shows
  the precision and refuses off-course logic below the threshold.
- The field person needs a screen: T1000-E users read and type in the phone app.
- Routing needs an OSM extract of the area, downloaded once (section 6). Without it the mode
  still works with compass direction and straight-line distance.
- Out of scope: broadcasts to whole channels, several coordinators, changing device settings
  over the air, voice.

## 2. Paths, stops and timing

A mission's path is an ordered list of waypoints. Each waypoint is

```
{"name": "B", "lat", "lon", "kind": "stop" | "via", "radius_m": 30,
 "arrive_by": "12:55" | null, "hold_until": "13:05" | null}
```

- **stop**: announced. Arrival is confirmed, times are enforced. The last waypoint is always a
  stop (the target).
- **via**: shapes the route only (go through B, not around). Passed silently when the walker
  comes within its radius or the projection along the route moves beyond it.
- **arrive_by**: the server compares the ETA with the deadline on every position. Status
  messages show the margin (`+2` late, `-3` early, minutes). When the ETA is later than
  `arrive_by` by more than `late_warn_min` (default 3) the field node gets `!SPAET` once; the
  warning re-arms when the ETA is back within the deadline.
- **hold_until**: at arrival the confirmation says how long to wait. Leaving the radius before
  the time → `!FRUEH` (come back, wait until …), once per early departure. When the time is
  reached the next leg is sent automatically (`weiter #C …`).
- Times are local `HH:MM` of today; the form also accepts `+15` (minutes from now). The mission
  stores them as timestamps.

The *current stop* is the first stop not yet reached; routing, off-course checks, ETA and
instructions all refer to it. Via points between the walker and the current stop are part of
the current route segment.

**Editing while the mission runs.** The coordinator can add, move, remove and re-time
waypoints at any time (mission card → *Pfad bearbeiten*; markers are draggable, a click adds a
point, each row has kind, radius and times). The server recomputes the route. The field node is
told only when its current leg changed: the current stop, its time, or the route to it
(`#C neu 600m NE ~8min`); later waypoints stay silent until they become current. Removing the
current stop makes the next one current. Reassigning a node replaces its whole path.

A path can be saved as a named *route template* (`data/coord/paths.json`) and reused with
fresh times. Named targets (`targets.json`) and own sites can be used as waypoints.

## 3. Radio protocol

Every message is a direct text (DM) between server node and field node, sent exactly like the
messaging pane does (channel 0, PKI-encrypted when the key is known, `wantAck`, the device's
hop limit), so it shows up in the pane with its delivery state. The field node's commands are
DMs too. No callsign: the app shows the sender.

**Budget.** ShortSlow carries about 6 kbit/s; a DM costs the packet, its ack and every relay
hop. The design keeps unsolicited messages to one per `min_gap_s` (default 120 s) per node,
combines several pending things into one message, and keeps texts under ~80 bytes (hard limit
200, the existing `check_text`). Replies to commands are always answered. Messages the mission
depends on (assignment, arrival, next leg, a changed leg, end) are never rate-limited; no-go,
off-course and schedule warnings jump the queue but stay ≥ 30 s apart from the last
unrequested message.

**Codes.** Compass letters are English in every language (`N NE E SE S SW W NW`). Turns are
relative to the route's own direction, which is well defined whatever the walker's heading:
`L` left, `R` right, `G` straight on, `U` turn back. Distances are metres rounded to 10
(`350m`) or `1.2km`. ETA is `~11min`, clock times `12:55`, schedule margin `+2`/`-3` minutes.
`#NAME` is the current stop, `!` a warning, `i` an information, `R:` a route, `Z` the stop in a
leg list.

**Server → node** (German phrases shown; English catalogue alongside):

| Event | Message | Notes |
|---|---|---|
| Assignment | `#ALPHA 850m NE ~11min` | straight-line distance and bearing from the last position |
| Assignment with route | `#ALPHA 850m NE ~11min R: N200 L300 Hauptstr R150 Z60` | first `legs_per_message` legs; two messages if it doesn't fit |
| Assignment with deadline | `#B 850m NE ~11min bis 12:55 (-4)` | margin in minutes |
| Assignment, no position | `#ALPHA zugewiesen, keine Position von dir` | the mission waits for the first position |
| Route (on request, at a turn) | `R: L120 Hauptstr R300 Z60` | street name only when ≤ 12 chars, `straße` → `str` |
| Status | `#ALPHA 420m NE ~6min 4.2km/h` (+ `bis 12:55 (+2)` when timed) | |
| Off course | `!KURS 90m ab. R: E90 N300 Z60` | new route from the current position |
| Running late | `!SPAET B ~12:58 statt 12:55` | once, re-armed when back on time |
| Left a hold early | `!FRUEH B zurück, warten bis 13:05` | |
| Inside / heading into a no-go area | `!SPERR Kaserne 80m voraus` / `!SPERR Kaserne verlassen` | |
| Place nearby | `i Bahnhof 100m: Treffpunkt Ausgang Nord` | free text from the place |
| Confirmation (optional) | `#ALPHA ok 420m ~6min` | `confirm_every_min` |
| Stop reached, hold | `#B erreicht 12:41 1.3km 17min. Warten bis 13:05` | |
| Stop reached, continue | `#B erreicht 12:41. Weiter #C 600m NE ~8min` | combined when it fits |
| Hold over | `Weiter #C 600m NE ~8min R: …` | sent at `hold_until` |
| Final stop reached | `#ALPHA erreicht 12:41 1.3km 17min` | mission *arrived* |
| Leg changed by the coordinator | `#C neu 600m NE ~8min bis 13:30` | only for the current leg |
| Ended by coordinator | `#ALPHA aufgehoben` | optional (setting) |
| Held / resumed | `HALT ok` / `Weiter #ALPHA 420m NE` | answers to `halt` / `go` |
| Help | `? Status ?R Route ?Z Ziel ?P Pfad HALT GO X` | on any unknown `?…` |

**Node → server** (case-insensitive, trimmed; only nodes with a mission get answers):

| Command | Meaning | Reply |
|---|---|---|
| `?` | status | status message |
| `?r` | route | next legs from the current position |
| `?z` | current stop | `#B 850m NE bis 12:55` |
| `?p` | path | `B 12:55 > C 13:30 > ALPHA` (stops only, with times) |
| `?h` or unknown `?…` | help | help line |
| `ok` | acknowledged | logged, no reply |
| `halt` | hold: no proactive messages until `go` | `HALT ok` |
| `go` | resume | status message |
| `x` | abort the mission | `#ALPHA abgebrochen`; the coordinator gets a notice |

Anything else from a coordinated node is an ordinary DM and stays in the pane without a reply.
Nodes without a mission are never answered (no unsolicited radio).

**Language.** Radio phrases follow a per-mission language (default from the settings, `de` or
`en`), not the page's UI language: the field person may not be the coordinator. The phrases
live in their own small catalogue (`coord/phrases.py`), separate from the UI catalogues, so
`tests/test_i18n.py` stays as it is.

## 4. Metrics the server keeps per mission

| Metric | Source |
|---|---|
| Straight distance and bearing to the current stop | last position |
| Route distance remaining to the current stop and to the end, progress % | projection of the position onto the route |
| Off-route distance (cross-track) | distance to the current route segment |
| Speed: current (median of the last 3 legs with Δt 20 s … 5 min and Δd > 15 m) and average | positions; `groundSpeed` when the tracker sends it |
| ETA per stop, schedule margin per timed stop | remaining distance ÷ speed (profile default when no valid speed: foot 4.5 km/h, bike 15, car 30) |
| Hold: arrival time, time left, dwell time | mission log |
| Elapsed time, distance travelled | since assignment |
| Position age, precision bits | last position packet |
| Link: SNR, RSSI, hops of the last packet; messages sent / delivered / failed | packets and `send_text` acks |
| Battery | telemetry packets or node database |
| State, last command, last message | mission log |

## 5. Decision logic

On every accepted position (in this order; each check has its own rate limit, then one
combined message goes out if anything is due):

1. **Stop reached**: distance to the current stop ≤ its radius → confirmation; with
   `hold_until` the mission is *holding*, otherwise the next stop becomes current and its leg
   is sent (combined with the confirmation when it fits). Last stop → *arrived*.
2. **Via passed**: within its radius or projected beyond it → silently advance.
3. **Holding**: outside the radius before `hold_until` → `!FRUEH` once per departure.
4. **No-go**: position inside a no-go polygon → warning once per entry; the route ahead within
   200 m crosses a no-go polygon (only possible when the walker left the route) → warning and
   re-route.
5. **Off course**: `off_route_m` (default 75) exceeded on two consecutive positions → re-route
   from the current position through the remaining via points, send the correction with the
   first legs. Without a road graph: straight-line distance grew on three consecutive positions
   by > 50 m → `!KURS` with bearing.
6. **Schedule**: ETA later than `arrive_by` + `late_warn_min` → `!SPAET` once, re-armed when
   the ETA is back within the deadline.
7. **Places**: entering `radius_m` of a place → its text once; re-armed after leaving 1.5 × radius.
8. **Leg instructions** by the `instructions` setting: `request` (only on `?r`), `turns`
   (default: when passing within 40 m of the next turn point, send the next legs), `interval`
   (every N metres travelled).
9. **Confirmation**: `confirm_every_min` (default 0 = off) → status message while on course.

Time-based, from a 10 s tick thread: `hold_until` reached → next leg; position older than
`stale_min` (default 5) → notice on the page, none on the radio; an undelivered assignment,
arrival or next-leg message is retried once after 60 s; `halt` suppresses everything proactive
until `go`.

State machine: `assigned` → `underway` (first position) → `holding` ⇄ `underway` → `arrived`
| `aborted` (by `x`) | `ended` (by the coordinator); `held` is a flag on top. One mission per
node; assigning a new path replaces it. Turning the mode off keeps the missions but sends
nothing; turning it on again resumes them.

## 6. Routing over OSM

**What the Overpass download is.** OpenStreetMap's full data set is far too big to fetch; the
Overpass API (overpass-api.de, a public read-only query service run by the OSM community, no
account needed) answers queries like "all ways tagged `highway` inside this bounding box" with
the matching ways and their node coordinates as JSON. For a 3 km radius of city that is a few
megabytes and a few seconds. The map app would run this query once as a background task, turn
the answer into a road graph and store it under `data/osm/`; afterwards routing is offline and
nothing leaves the machine. What the query reveals is the bounding box, i.e. roughly where you
live, to a server you already trust with that: the browser's map tiles come from OSM's tile
servers and show the same area. To blur it a little the bounding box is snapped outward to a
0.01° grid (~1 km) instead of being centred on the home coordinates. The offline alternative is
a regional extract (Geofabrik, NRW ≈ 600 MB `.osm.pbf`) imported with an optional `osmium`
dependency; supported by `scripts/coord_import_osm.py` for people who don't want the query.

- **Graph.** `data/osm/<name>.json`: nodes (lat, lon) and edges (length, highway tag, access,
  name, oneway, geometry). Built by the background task **＋ Straßennetz laden** (`JobKind`
  `osm`, progress and log like the other tasks) for `radius_m` (default 3 km) around home, or
  by the import script. Three kilometres of city are ~20 000 nodes; Dijkstra with `heapq`
  answers in well under a second, no new dependency (shapely for the geometry is already
  required by the map app).
- **Profiles.** `foot` (everything except motorway/trunk and `foot=no`; one-way ignored), `bike`
  (no steps or footways unless `bicycle=yes`; one-way respected), `car` (roads only, one-way,
  `access`). Default per settings, overridable per mission.
- **No-go areas** remove every edge that touches a polygon (plus `buffer_m`). If no route
  remains, the route goes through and the mission is flagged.
- **Snapping.** Position and waypoints are projected onto the nearest edge; further than 200 m
  from any edge → straight-line fallback for that segment (`Luftlinie` in the mission).
- **Path route** = concatenation of segment routes waypoint to waypoint; the segment from the
  walker to the current stop is recomputed on every re-route, the rest when the path is edited.
- **Instructions.** Walk the path, compute the heading change at every graph node; < 30°
  merges into the current leg, 30–150° is `L`/`R`, ≥ 150° `U`. The first leg carries the
  compass direction. A leg is turn code, distance to the next turn, optional short street name.
- **Progress** is the projected distance along the current segment (UTM, shapely `project`);
  remaining = length − progress; off-route = `distance` to the line.

## 7. Data (all under `data/`, git-ignored)

```
data/coord/settings.json     radio language, profile, thresholds (section below)
data/coord/targets.json      {"ALPHA": {"lat", "lon", "radius_m"?, "note"}}
data/coord/paths.json        named route templates: {"RUNDE1": [waypoints without times]}
data/coord/areas.json        [{"id", "name", "kind": "nogo"|"notice", "polygon": [[lat, lon], …],
                              "text", "buffer_m"}]
data/coord/places.json       [{"id", "name", "lat", "lon", "radius_m", "text"}]
data/coord/missions.json     current missions (path, current stop, routes, last 50 positions, metrics)
data/coord/events-<date>.jsonl  every decision, message and accepted position
data/osm/<name>.json         road graph
```

Waypoint and target names follow the sites rule (`NAME_RE`, ≤ 24 chars, no spaces); the form
suggests ≤ 8 because the name goes into every message. Ad-hoc clicks get `P1`, `P2`, … .

**Settings** (edited in the page, `Setting` declarations like a layer): `lang` (de/en),
`profile`, `arrive_radius_m` 30 (default for new waypoints), `off_route_m` 75, `min_gap_s`
120, `instructions` (request/turns/interval), `legs_per_message` 3, `confirm_every_min` 0,
`late_warn_min` 3, `stale_min` 5, `min_precision_bits` 24, `speed_kmh` per profile,
`end_message` on/off, `channel` (the device channel the direct messages use; default 1, the
private channel, so the public mesh sees nothing). The hop limit is the device's.

## 8. Server side

New package `src/meshplay/mapapp/coord/`:

| Module | Job |
|---|---|
| `missions.py` | `Coordinator`: state, packet listener, worker thread, tick thread, persistence, API dispatch |
| `paths.py` | waypoint model, current stop, timing (deadlines, holds), path editing |
| `phrases.py` | message formatting per language, byte check, command parsing |
| `routing.py` | graph loading, Dijkstra, profiles, no-go filtering, snapping, legs, progress |
| `osm.py` | Overpass download and file import → graph JSON (also the `osm` `JobKind`) |
| `areas.py` | polygons, places, point-in-polygon, entry/exit hysteresis |
| `layers/coord.py` | layer *Koordination*: waypoints, routes, trails, areas, places; `refresh_s` 10 while on |

The `Coordinator` is created in `server.run()` like the `JobManager` (`ctx.coord`). Its packet
listener only enqueues; a worker thread does decisions and sends, so the serial reader thread
never blocks on the radio. Sending goes through `DeviceLink.send_text`, so every message is in
the pane with its delivery state.

**Changes to existing code.**

- `device.py`: a `listeners` list called from `_on_receive` after `_record` (the `Coordinator`
  subscribes); `send_text` gets an `on_status` callback so a mission learns about delivery; a
  `coord` marker on stored messages so the pane can show them differently.
- `layers/nodes.py`: expose `isFavorite` in the node rows so the form sorts favourites first.
- `server.py`: the `/api/coord/*` routes go into one dispatch function in `coord/missions.py`
  instead of more branches in the handler's if-chain.
- `messages.js`: no toast for DMs that are coordination commands (they are answered by the
  server, the coordinator sees them in the mission).
- `util.js` `featureHTML`: a `Ziel zuweisen` button on node popups next to `Nachricht`.
- `main.js`: `pickOnMap` gets a multi-click variant (polygons, paths) with a banner *Fertig*.
- `map2d.js`: draggable markers for waypoints while a path is being edited.

**API.**

```
GET  /api/coord                        mode, settings, missions, targets, paths, areas, places, osm state
POST /api/coord/mode                   {"on": bool}          the owner's authorisation to transmit
POST /api/coord/settings               {...}
POST /api/coord/targets/<add|update|delete>,  /api/coord/paths/<…>
POST /api/coord/areas/<add|update|delete>,    /api/coord/places/<…>
POST /api/coord/missions               {"node", "path": [waypoints], "profile"}  creates, sends the first leg
GET  /api/coord/missions/<node>        mission with events, routes, legs
POST /api/coord/missions/<node>/path   {"path": [...]}       edit; tells the node if its current leg changed
POST /api/coord/missions/<node>/<status|route|next|end>   send now / skip to the next stop / end
GET  /api/coord/route?node=&path=…     preview: routes, legs, the message text (no radio)
POST /api/jobs {"kind": "osm", ...}    road graph download (existing task API)
GET  /api/layers/coord                 map features
```

Every endpoint that transmits is either an explicit owner action or covered by the mode switch;
mode off, device not connected, unknown node, no position, a `hold_until` before its
`arrive_by` → `ValueError` with a plain sentence.

## 9. Page

- **Rail section "Koordination"** (between *Aufgaben* and *3D-Darstellung*): the mode switch
  with a one-line state ("aus" / "an · 2 Einsätze"), ⚙ settings form built from the `Setting`
  declarations, road graph state with **Straßennetz laden …** (with the explanation from
  section 6 in one sentence), **＋ Einsatz** (node select, favourites first; path builder:
  add a target, a site, a saved path or map clicks; per row name, kind, radius, `arrive_by`,
  `hold_until`; profile), and a card per mission: node, current stop and the stops after it,
  distance, bearing, ETA, schedule margin, speed, position age, state chip, last message with
  delivery state, buttons *Status senden*, *Route senden*, *Pfad bearbeiten*, *Nächster Halt*,
  *Beenden*, *Details*. Folded below: the targets and paths editors and the areas/places editor
  (like the sites editor; polygons drawn by clicks).
- **Node popup and node list**: *Ziel zuweisen* opens the mission form with the node preset.
- **Inspector tab "Einsatz"**: metrics table, the path with per-stop ETA vs deadline, route
  legs with the message preview, event log.
- **Layer "Koordination"** in the layer list: stops as numbered flags (via points small), the
  route per mission coloured by state, the node's trail, no-go areas hatched red, notice areas
  blue, places as circles.
- **Header**: the *Aufgaben* badge pattern reused as "Koordination · 2" while the mode is on.
- **Toasts**: stop reached, hold over, running late, abort by the node, off-course message
  sent, delivery failure, stale position.

## 10. Testing

- `tests/test_coord_phrases.py`: every phrase in both languages under 80 bytes with long names,
  command parsing, help on unknown `?…`.
- `tests/test_coord_paths.py`: current stop, via passing, deadlines and margins, holds, early
  departure, editing while running (what changes the current leg, what doesn't).
- `tests/test_coord_routing.py`: a synthetic grid graph: shortest paths per profile, no-go edge
  removal, leg generation (turn codes, merging, street names), snapping, multi-segment path
  routes, progress/off-route.
- `tests/test_coord_missions.py`: `Coordinator` with the `FakeIface` from
  `test_mapapp_messages.py`: feed position packets → assigned/underway/holding/arrived,
  off-course re-route, late warning, rate limit, `halt`/`go`/`x`, no reply to nodes without a
  mission, restart restores the missions, mode off sends nothing.
- **Simulated radio for the page.** CLAUDE.md refers to one, but the server has no such option
  yet. Proposal: `scripts/mapapp.py --simulate <track.gpx>` installs a fake interface that
  replays the GPX as position packets of a fake node `!fa4e0001` and echoes typed commands, so
  the whole page can be tried without transmitting. This closes a gap the messaging pane
  already has.
- Degrade-gracefully checks: empty data folder (no settings, no graph, no targets) → the section
  explains what is missing; no device → mode switch disabled with the reason.

## 11. Phases

1. **Core, no OSM**: settings, targets, missions with a single stop, straight-line guidance,
   commands, arrival, rail section, mission cards, inspector tab, layer, tests, simulated radio.
2. **Paths**: waypoints, via points, deadlines and holds, editing while running, path
   templates, the path builder in the page.
3. **Routing**: Overpass task and import script, graph, profiles, legs, progress, off-course
   re-route, instruction modes.
4. **Areas and places**: editors, no-go avoidance, warnings, proximity texts.
5. **Extras**: periodic confirmations, OSM-derived no-go suggestions (`landuse=military`,
   `access=no`), position requests from the server (off by default; the firmware's behaviour
   with an empty position needs a test first).

## 12. Decisions taken (owner, 2026-09-29)

- Compass letters English (`N E S W`) in every language.
- No callsign; the app shows the sender.
- Hop limit: the device's default, no setting.
- Overpass download: explained in section 6; the import script is the offline alternative.
- Position requests from the server: not now, kept as a phase 5 option.
- One mission per node, but a mission carries an editable path: waypoints with via/stop kinds,
  `arrive_by` and `hold_until` times.
