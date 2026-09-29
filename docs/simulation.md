# Coverage simulation

Propagation models for 868 MHz Meshtastic links over a LiDAR scene, and tools to score them
against measurements. The scene is built from the open laser-scan data of Geobasis NRW, so it
covers North Rhine-Westphalia (Germany); elsewhere `Scene` would need rasters from another
LiDAR source (terrain, surface height, building/vegetation masks at 1 m). Ported from the "Mesh Bonn" project (state 2026-09-20:
`11_Simulation_ITU/`, `geo/`, `20_Messung/`). Background, priors and findings are in that
project's notes, kept locally in `data/sim/reference/2026-09-20/` (`PROJEKT.md`,
`BEFUNDE_2026-09-20.md`, `ANLEITUNG_START.md`) together with its frozen outputs
(`predictions.json`, `relay_candidates.json`, `selftest.txt`).

## Pipeline

```
NRW laser-scan tiles (LAZ)     sim_fetch_tiles.py
        │
        ▼
scene: terrain, surface,       sim_build_scene.py      data/sim/scene/
buildings / vegetation
        │
        ├──► sim_predict.py        fixed links, frozen     data/sim/predictions/
        │         ▲                                        predictions-<time>.json/.npz/.sha256
        │   measure_logger.py ──► Messprotokoll.csv ──► sim_score.py
        │
        ├──► sim_compare_walk.py   walk log (+GPX) vs. models   data/sim/compare/
        ├──► sim_coverage_map.py   predicted coverage grids     data/sim/maps/
        ├──► mapapp.py             2D/3D map app, link tool,    see docs/mapapp.md
        │                          coverage as background task
        └──► sim_relay_search.py   best relay roofs             data/sim/relay-*.json/.html
```

Sites and scenarios live in `data/sim/sites.json` (not committed, it holds addresses); start
from [config/sites.example.json](../config/sites.example.json) or add sites in the map app's
sites editor. A site has a position, the antenna height as a range (`height_m`, the models
draw from it), the clutter height around it (`clutter_m`: roofs or trees next to the
antenna) and optional variants (`same_as`: same place, other height). Scenarios name two
sites and the placement at each end (antenna outside, open window, closed window with old or
low-E glazing).

The modem preset defaults to ShortSlow (`DEFAULT_PRESET` in `src/meshplay/config.py`); walk
logs that record the preset override it.

## Models

Seven families on identical Monte Carlo draws, plus their per-draw median (ENS):

| | model | notes |
|---|---|---|
| M1 | ITU-R P.1812-6 over the LiDAR profile | buildings and trees as diffraction edges |
| M1b | M1 with the first/last 50 m as terminal clutter loss, eq. (64) | pessimistic bracket |
| M2 | P.1812-6 over bare terrain + P.2108-1 statistical clutter | |
| M3 | free space + delta-Bullington diffraction | |
| M4 | ITU-R P.1411-12 street-level | only up to 660 m |
| M5 | log-distance, n in [2.6, 3.8] | empirical reference |
| M6 | buildings as edges, vegetation as P.833-10 attenuation | |

The link budget adds transmit power, antenna gains, feeder loss, window/building entry loss
(P.2109-2), noise figure and urban noise, per-packet fading (σ 4 dB) and a soft LoRa decoding
threshold per preset. All priors are in `meshplay/sim/models.py`; values marked `[BELEG?]`
have no verified source.

## Direct links in the map app

`python scripts/mapapp.py --open` ([docs/mapapp.md](mapapp.md)) computes any direct link on
demand: pick A and B on the 2D or 3D map, or take a site, a Meshtastic node or a packet of your
walk. `meshplay.sim.link.predict_link` returns the profile, the geometry (line of sight, Fresnel
zone, strongest diffraction edges) and all model families for a single packet; for a walk
packet the measured signal is shown next to them.

The 3D view is the viewer from the Mesh Bonn session. Its data export
(`meshplay.sim.view3d`) reproduces the original body extraction exactly (4202 buildings, 3754
vegetation bodies on the original scene) and the link loss `Lb_ref` to within 0.01 dB; the list
of strongest edges can differ slightly because contiguous obstructions are grouped per run of
stations. Bodies inside data gaps (no terrain height) are left out.

## Comparing with measurements

**Fixed points** (scenarios A1 … and corridor K100 …): run `measure_logger.py send` on one
device and `measure_logger.py listen` on the other, ten packets per scenario, both devices
`CLIENT_MUTE` with hop limit 1. Then

```powershell
python scripts/sim_score.py > data/measurements/Auswertung.md
```

scores the protocol against the newest frozen prediction: log score, 80 % interval coverage,
Brier score of delivery, model weights, noise floor estimate, and a fit of the distance
exponent n over the corridor points.

**Coverage walk**: record a walk (see the README: traceroutes with `probe_walk.py` or the map
app, or position broadcasts with `listen.py`), then

```powershell
python scripts/sim_compare_walk.py --tracker !<id> --probes --gpx data/tracks/walk.gpx --home-indoor none --open
python scripts/sim_coverage_map.py --walk data/sim/compare/probes-<id>-<date>.csv --open
```

The first scores every directly received packet or answered traceroute (signal level of the
tracker → home direction) and, with the GPX track, every sending interval of the walk
(received or not, Brier score and calibration table). Without `--probes` it reads position
packets. Interval and preset come from the probe log when it records them. The second draws
the predicted delivery probability per model as switchable map layers with the walk on top.
The map app's walk layer does the same comparison per point (colour *Messung − Modell*).

A traceroute is only answered if both directions work, while the models predict one
direction; and the models assume a tracker held in the open, not in a pocket (body loss of
several dB) or indoors. Keep both in mind when reading the bias.

Predictions for walk positions are computed on the spot, so they are not pre-registered. The
report records seed, draws, settings and the git commit instead; don't tune priors on the same
walk you score.

## Review of the original code

What was carried over unchanged, what was changed, and why.

**Unchanged and verified**

- `p1812.py` is copied verbatim. The original port reproduced all 63 ITU validation cases
  (5e-8 dB); re-check with `sim_validate_p1812.py` and the reference data.
- The model ensemble and link budget reproduce the frozen `11_Simulation_ITU/out/predictions.json`
  to 1e-5 dB over every published number when run on the original scene with seed 20260920
  (`tests/test_sim_regression.py`, `pytest -m data`).
- Profile extraction (`profil3`) reproduces `export/profiles.json` exactly from the scene.

**Fixed**

- *Hardcoded sandbox paths* (`/mnt/user-data/...`, `/home/claude/...`, `/tmp/p1812`): all paths
  now come from settings and arguments.
- *Relayed packets counted as direct* in `mess_logger.py`: the Python API leaves `hopLimit` out
  when it is 0, which is exactly a relayed packet with hop limit 1. The old code then left the
  hop count empty and treated the packet as direct. Now a missing `hopLimit` means 0.
- *RSSI vs. signal power*: the models predict the wanted signal, the radio reports signal plus
  noise. Near the noise floor that makes observations look up to ~10 dB better than they are,
  in favour of the optimistic models. Scoring now uses RSSI corrected by SNR (switch off with
  `--raw-rssi`).
- *Unfair model weights*: M4 is only valid up to 660 m, so its summed log score covers fewer
  observations. Weights now compare only models scored on the same observations.
- *Missing pipeline step*: nothing in the original produced `export/` from the LAZ tiles (it
  was done interactively). `sim_build_scene.py` does it with the original building blocks. It
  reproduces the surface raster and the point counts exactly, but not the terrain raster: the
  export's terrain differs from the minimum of the ground points even in measured cells, and
  neither of the original gap fillers reproduces it. Terrain can therefore differ by a few
  metres, mostly under buildings. The original export in `data/sim/scene/` stays the reference
  for the frozen predictions; `--import` restores it.
- *Scoring the ITU predictions was not possible*: `run.py` only stored percentiles and used
  scenario IDs (A1 …) that the logger and `score.py` (S1 … from v3) didn't know. Predictions now
  also store samples (.npz) and share IDs with the logger.
- *Gap in the scene*: the original scene had no LAZ data for tiles 366_5622 and 366_5623; that
  strip was interpolated. Scenes now carry a `measured` mask, the relay search skips unmeasured
  roofs and the walk report flags paths through interpolated cells.

**Not carried over**

- `10_Simulation_v3` (log-distance/COST/Deygout ensemble with its own frozen prediction and
  `rfsim/score.py`): superseded by the ITU models per BEFUNDE §5; it still runs in its folder.
- `geo/profil2.py`, `geo/laz_raster.py`, `geo/beugung.py`: earlier versions of profile
  extraction and diffraction, replaced by `profil3` and P.1812.
- The viewer's `geometry.json` generator was missing (done interactively);
  `meshplay.sim.view3d.link_geometry` recomputes it. `geo/extract_polys.py` lives on in
  `meshplay.sim.view3d`, with the parameters that reproduce the original export (they weren't
  recorded either). The viewer's fixed scenarios, corridor and preset tabs were replaced by the
  on-demand link tool of the map app.

## Data (not committed)

| path | content |
|---|---|
| `data/sim/sites.json` | sites and scenarios |
| `data/sim/laz/` | NRW laser-scan tiles (`sim_fetch_tiles.py --download`) |
| `data/sim/reference/2026-09-20/` | Mesh Bonn notes, coordinates, frozen outputs, PDFs |
| `data/sim/scene/` | scene rasters (`sim_build_scene.py`) |
| `data/sim/predictions/` | frozen predictions |
| `data/sim/maps/` | coverage grids `coverage-<site>-<preset>-<placement>-<radius>m-<step>m.npz` and maps |
| `data/sim/compare/` | walk comparisons (`sim_compare_walk.py`) |
| `data/sim/itu-p1812/` | ITU validation data for `sim_validate_p1812.py` |
| `data/measurements/` | measurement protocol |
