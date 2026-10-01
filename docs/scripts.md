# Command-line scripts

Everything the map app does is also available as a script, plus a few tools only used from
the command line. All scripts are in `scripts/`, print their options with `--help`, and take
`--port COM8` where they talk to the device (otherwise `MESHTASTIC_PORT` from `.env` or
auto-detection; see [setup.md](setup.md)).

| Script | What it does |
|---|---|
| **Device** | |
| `node_info.py` | summary of the connected node |
| `listen.py` | prints received packets and appends them to `data/packets/<date>.jsonl` (`--text-only`) |
| `export_nodes.py` | node list of the device → `data/exports/nodes-<time>.json` and `.csv` |
| `send_text.py` | send a text: `send_text.py "hello"` broadcasts on channel 0; `--to !1234abcd`, `--channel 1` |
| **Walks and measurements** ([walks.md](walks.md)) | |
| `probe_walk.py` | traceroutes a walking node every 60 s and logs the answers → `data/probes/` |
| `coverage_map.py` | map of a walk (positions, or traceroutes with `--probes`) and the phone's GPX track → `data/maps/` |
| `measure_logger.py` | fixed-point link test: one device sends numbered packets, the other logs them |
| **Map app** ([mapapp.md](mapapp.md)) | |
| `mapapp.py` | the browser map app (`--open`, `--device`, `--port 8770`; `--simulate [track.gpx]` for a fake radio) |
| `coord_import_osm.py` | road graph for the coordination mode from an `.osm` file instead of the Overpass download |
| `make_basemap.py` | rebuilds the offline overview map (`webmap/vendor/basemap/`) from Natural Earth |
| **Simulation** ([scenes.md](scenes.md), [simulation.md](simulation.md)) | |
| `sim_fetch_tiles.py` | lists and downloads the elevation tiles of an area |
| `sim_build_scene.py` | builds a named 3D scene (terrain, buildings, trees), `--download` fetches the tiles; lists and switches scenes |
| `sim_coverage_map.py` | predicted coverage around a site, per model |
| `sim_compare_walk.py` | scores a walk against the models |
| `sim_predict.py`, `sim_score.py` | frozen predictions for fixed links, scored against `measure_logger.py` results |
| `sim_relay_search.py` | searches roofs for the best relay between two sites |
| `sim_validate_p1812.py` | checks the ITU-R P.1812 port against the official validation data |

The official Meshtastic CLI is installed as well: `meshtastic --port COM8 --info`.

## Watching the mesh

```powershell
python scripts/listen.py          # every received packet; Ctrl+C stops
python scripts/export_nodes.py    # the node's node list -> data/exports/
```

## Writing your own code

```python
from meshplay import connect

with connect() as iface:  # finds the port like the scripts do
    print(iface.getMyNodeInfo())
```

`iface` is a `meshtastic.serial_interface.SerialInterface` from the
[Meshtastic Python library](https://python.meshtastic.org). Anything you send reaches other
people's devices; test on a private channel. For a quick exploration, copy
`experiments/_template/` to `experiments/<date>-<name>/` (see
[experiments/README.md](../experiments/README.md)).
