# Backlog

Open work, roughly by value. Background for the elevation items: the research of 2026-10-01 on
which states publish what (summarised in [scenes.md](scenes.md), and in
`src/meshplay/sim/sources/`).

## Passive walks (Mesh-Empfang)

- **Range test as denominator.** A sending node's range test packets (`seq N`) would show which
  numbers were lost along a walk. Needs a real export with range test packets (transmitting needs
  the owner's OK: module on, private channel, hop limit 0) to see how the app writes them in
  `payload`; then also correct the note in `scripts/measure_logger.py` (the app's export works
  with nRF52 receivers too).
- **Debug-log import** (Android app, text export): adds RSSI, the packet ID and the role of nodes
  (router, `CLIENT_MUTE`), a further criterion for relay candidates.
- **Details tab "Mesh-Empfang"**: table of relay bytes (count, SNR median, grade, candidates);
  a click sets the byte filter.
- Lines to candidates on a click on a single packet; timeline; hexagon tiles over several walks.

## Device configuration (view Gerät)

In: the configuration, the check, profile and backups, and writing hop limit, transmit power,
telemetry, names, role and the channels with their keys (`mapapp/device_config.py`,
[mapapp.md](mapapp.md#device-configuration)). Writing to the device is the owner's own action
per change, never the app's or an agent's: every stage below goes through the same preview
(old → new, with consequences such as a reboot or leaving the mesh), one settings transaction
and a backup before it.

- **More tries on real nodes.** The owner wrote settings to the real node once (2026-10-04:
  works); the tests use the simulated radio. Still to see: the reboot wait of 20 s over
  Bluetooth and on other boards, a channel write, and whether the firmware keeps giving the
  node's own metrics to the app with device telemetry off (the airtime panel reads them).
- **More fields with the same form**: position broadcasts (interval, smart position),
  rebroadcast mode, node info interval, air quality and health telemetry.
- **Channels**: a QR code for the share URLs (needs a library, vendored or on the server);
  MQTT uplink/downlink per channel; showing a single key on request.
- **Security**: new key pair, admin keys. `serial_enabled` and managed mode stay read-only:
  both can lock the app out.
- **Restore** a profile or backup from the app (the command line's import is not callable as a
  function; about 50 lines over the library's `Node` API).
- Later: remote administration of the tracker (transmits over the mesh, needs its own OK).

## Elevation data and scenes

- **Cache the OpenStreetMap footprints per scene.** The raster states (Lower Saxony,
  Schleswig-Holstein) take their buildings from Overpass, which was busy (HTTP 504) twice in one
  day of testing. Then the scene falls back to the surface rule, which finds only about half of
  the buildings. Keep the footprints of an area under `data/sim/tiles/osm/` so a rebuild doesn't
  need Overpass, and let a later build replace a "surface" scene once Overpass answers.
- **Official LoD2 building models instead of OSM footprints** for the raster states. Both offer
  LoD2 (CityGML) for free; the footprints are more complete and exact than OSM's. Needs a
  CityGML reader (ground surfaces of each building) and the states' download interfaces
  (Schleswig-Holstein: same GeoJSON tile index as DGM1; Lower Saxony: to check).
- **Bavaria.** Classified point cloud (LAZ) free since 2023, CC BY 4.0, with its own building
  and vegetation classes, so better scenes than from NRW's two classes. Bulk download through
  Metalink lists (`geodaten.bayern.de/odd/a/.../meta/metalink/<area>.meta4`, verified for DGM1
  and LoD2); the URL of the point-cloud list is not confirmed yet. Tiles are about 1 GB per km²
  (high density). Needs a classifier for standard ASPRS classes next to NRW's.
- **East German states (UTM zone 33).** Brandenburg has a plain folder of free point clouds
  (`data.geobasis-bb.de/geobasis/daten/als/laz/`, ZIP per km², about 60 % of the state), Berlin
  ATOM feeds with district ZIPs up to 50 GB, Saxony and Saxony-Anhalt portals. All in
  EPSG:25833, while the app works in EPSG:25832 throughout: reproject on build (points or
  rasters to the 25832 grid of the scene). The largest of these steps.
- **Thuringia** (free LAZ, ATOM feeds): the feeds' tile links weren't confirmed.
