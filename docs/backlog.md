# Backlog

Open work, roughly by value. Background for the elevation items: the research of 2026-10-01 on
which states publish what (summarised in [scenes.md](scenes.md), and in
`src/meshplay/sim/sources/`).

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
