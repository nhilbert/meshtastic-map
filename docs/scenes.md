# 3D laser-scan scenes

The 3D view, the link calculator, the coverage simulation and the comparison of walks with the
models all work on a **scene**: 1 m rasters of terrain, surface height, buildings and trees,
built from an airborne laser scan. A scene is a square of 1–5 km; you can keep several (home
town, holiday area) and switch between them. Without any scene the map app still runs — the 2D
map, nodes, walks, sites, messages and coordination work; the parts that need the scene say so.

## The data

Each German state publishes its own elevation data, in its own form. Three are supported, one
module each in `src/meshplay/sim/sources/`; the scene's centre decides which one is used:

| State | Data | Licence | Download interface | Per km² |
|---|---|---|---|---|
| North Rhine-Westphalia | classified laser-scan points (Geobasis NRW, 3D-Messdaten) | dl-de/zero-2-0 | folder with fixed file names | ~95 MB |
| Lower Saxony | DGM1 and DOM1, 1 m rasters from the laser scan (LGLN) | CC BY 4.0 | STAC API | ~8 MB |
| Schleswig-Holstein | DGM1 (1 m) and image-based surface bDOM (20 cm) (LVermGeo SH) | CC BY 4.0 | published GeoJSON tile index | ~130 MB |

From the points (NRW), the share of last and multiple returns separates roofs from trees. The
raster states have no points, so their buildings come from OpenStreetMap footprints (Overpass,
only the area's box is sent); if Overpass can't be reached, roofs are told from trees by the
smoothness of the surface, which finds only about half of the buildings (the scene records
which way was used). Schleswig-Holstein's surface comes from aerial images, so trees are less
exact than from a laser scan. The licences ask for attribution; the scene list and the scene's
popup show it.

Other states publish their data only through portals, shops or for a fee, or in UTM zone 33,
which the app doesn't handle yet; each needs its own source module (see
[backlog.md](backlog.md)). Tiles are downloaded only through an interface the state's office
documents for programs, never by imitating a portal page.

Choose the area generously: coverage simulations, links and walks can only be computed where the
scene has data. 3 × 3 km is a good start for a town; 5 × 5 km is the most the map app offers
(about 0.5 GB of memory in the server).

## In the map app (recommended)

1. *Aufgaben* → **Laserscan-Szene erstellen** (or right click on the map → *Szene hier
   erstellen*, or *Simulation* → *Szenen* → **＋ Neue Szene**), click the centre on the map,
   give it a name and an edge length. The form draws the square and names the state's source,
   how many tiles the area needs, how many are here, and the size of the rest against the free
   disk space.
2. Leave **Fehlende Kacheln herunterladen** ticked and press *Herunterladen und erstellen*: a
   background task (*Aufgaben*) fetches the missing files through the state's interface
   (resumable: a cancelled or broken download continues next time), builds the scene in a
   separate process and prepares the 3D view. Tiles still missing are interpolated (the layer
   shows those areas). With *Danach verwenden* the new scene is used right away.
3. If a download fails (servers change), *Selbst herunterladen* lists the files with their links
   (*Liste kopieren*) and the folder to put them in unchanged; *Erneut prüfen* updates the
   count and *Erstellen* builds from what is there.

The same list shows all scenes: *Verwenden* switches (the page reloads, the 3D view is built for
one scene), ✕ deletes one, *Kacheln löschen* frees the space of the downloaded tiles.

## With the scripts

Set `MESHPLAY_HOME` in `.env` first (or pass `--center LAT,LON`); the area is a square around
it.

```powershell
python scripts/sim_fetch_tiles.py --radius 1500              # list: names, sizes, what you have
python scripts/sim_fetch_tiles.py --radius 1500 --download   # download (data/sim/laz/ or tiles/)
python scripts/sim_build_scene.py --name home --radius 1500  # build data/sim/scenes/home/
python scripts/sim_build_scene.py --name kiel --center 54.315,10.1315 --size 3000 --download
python scripts/sim_build_scene.py --list                     # scenes; * = the one in use
python scripts/sim_build_scene.py --use home                 # switch
```

The list shows each file with its link and marks those you already have; tiles the source
doesn't offer (outside its state) are named as such. The download skips files that are present,
continues a leftover `*.part` file and reports a file the server doesn't have instead of
stopping. `sim_build_scene.py --download` fetches and builds in one go.

The build writes `data/sim/scenes/<name>/` (`scene_raw.npz`, `scene_cls2.npz`,
`scene_meta.json`): terrain, surface above ground, and per 1 m cell whether it is a building or
vegetation. Missing tiles are reported and their area interpolated; cells without any data are
marked as not measured. A 3 × 3 km scene takes about a minute and about 1 GB of RAM. The first
scene is used automatically, later ones with `--activate` or `--use`; `--force` replaces a
scene of the same name. A scene in the old single folder `data/sim/scene/` is moved to
`data/sim/scenes/default/` on first use.

The simulation scripts (`sim_coverage_map.py`, `sim_predict.py`, `sim_compare_walk.py`,
`sim_relay_search.py`) use the scene in use, or the one named with `--scene`.

## By hand

The map app's form and `sim_fetch_tiles.py` (without `--download`) list every file you need
with its link. Download them with a browser or any download manager and put them unchanged
into the folder the list names (`data/sim/laz/` for NRW, `data/sim/tiles/<state>/` for the
raster states), then build the scene in the map app (it finds them) or with
`sim_build_scene.py`.

The North Rhine-Westphalia tiles in detail:

- **Tiles:** 1 km × 1 km, compressed LAS (`.laz`), about 60–130 MB each, named
  `3dm_32_<E>_<N>_1_nw.laz`. `<E>` and `<N>` are the easting and northing of the tile's
  south-west corner in kilometres, in UTM zone 32N (ETRS89, EPSG:25832).
- **Download folder:** <https://www.opengeodata.nrw.de/produkte/geobasis/hm/3dm_l_las/3dm_l_las/>.
  Opened in a browser it lists every tile with size and date; the tile itself is that address
  plus the file name. The same folder has `3dm_meta.zip` with the official documentation.
- **How much:** a scene of 3 × 3 km usually touches 16 tiles (the square rarely lines up with
  the kilometre grid), so 1–1.6 GB to download. The scene itself is much smaller (about 70 MB
  for 3 × 4 km); the tiles are only needed to build it and can be deleted afterwards.

## Use it

- the 3D view shows terrain, buildings and trees of the scene in use; the layer
  *Laserscan-Szene* shows the outlines of all scenes and the unmeasured areas;
- *Simulation* → *Strecke A → B* computes links;
- *Simulation* → *Abdeckung* computes coverage maps (on any scene that contains the site), and
  the walk layer can compare measurements with the models (*Messung − Modell*). Models and
  pipeline: [simulation.md](simulation.md).
