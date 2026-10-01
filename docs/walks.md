# Coverage walks

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

## Traceroutes from home

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

## Position broadcasts

1. **Tracker:** on the private channel turn position sharing on with **precise location**, on
   the primary channel off (positions go out on the first channel that shares them); smart
   position off, broadcast interval 30 s, GPS update interval 30 s; hop limit 0.
2. **At home:** `python scripts/listen.py` (or the map app with `--device`, which logs the same).
   Wait for the tracker's first position before you leave.
3. **Walk** with a GPX recording on your phone; afterwards save the file to `data/tracks/`.
4. **Map:** `python scripts/coverage_map.py --tracker !abcd1234 --gpx data/tracks/walk.gpx --open`,
   or choose the tracker and the track in the map app's walk layer.

## Results

`coverage_map.py` writes `data/maps/coverage-<id>-<date>.html` (`probes-…` with `--probes`) and
a CSV and prints the share of the walked distance with direct coverage. `--open` serves the map
on http://localhost:8765 (opened as a file, the map background stays blank: OpenStreetMap
refuses `file://` pages).

To compare a walk with the simulation:
`python scripts/sim_compare_walk.py --tracker !abcd1234 --probes --gpx data/tracks/walk.gpx --home-indoor none`
(see [simulation.md](simulation.md)), or colour the map app's walk layer by
*Messung − Modell*.
