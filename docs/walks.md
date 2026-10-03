# Coverage walks

Map where your home node can reach a node you carry. Two methods, plus a passive one that maps
what the carried node hears from the whole mesh ([below](#mesh-reception-passive)):

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

Positions are never interpolated across a pause of more than 15 min in the GPX recording
(`walk.MAX_GAP_S`): probes and packets inside such a gap are left out. Scores of walks with long
gaps made before this rule (2026-10-03) are not directly comparable with later ones.

## Mesh reception (passive)

The carried node only listens; nothing is sent. Every packet it hears is placed on the GPX track:
which nodes reached you where, and how well. The SNR belongs to the **last hop** (the node that
transmitted the packet last), and a relayed packet names that node only by the last byte of its
ID, so several nodes can fit.

1. **Carry** a node paired with the Meshtastic Android app (e.g. a Wio Tracker L1) and record a
   GPX track on the phone.
2. **Export** the app's packet log as CSV (`Meshtastic_datalog_<name>_<date>.csv`; menu path:
   _to be filled in_). The export is a ring buffer of several days; the map app keeps only the
   part that falls into the track.
3. **Import** in the map app: *Aufgaben → Rundgänge importieren → Rundgang hochladen …*, select
   the GPX and the CSV together. The import checks the receiving device (suggested: the sender
   whose rows mostly carry SNR 0.0 and no relay; change it in the list) and whether the export's
   times fit the track (CSV times are local time without zone; read in the computer's zone).
4. **Show**: layer *Mesh-Empfang (passiv)* (group *Abdeckung*): points coloured by SNR or last
   hop, the track in windows (reception · device active but nothing heard · no data). Pick one
   relay byte to see its plausible candidate nodes and lines to them; the popup grades each
   packet's last hop (unique, likely, ambiguous) and lists every candidate. Candidates further
   than the range limit (default 15 km, an assumption) are not plausible; nodes without position
   can't be ruled out. Neither the app's own guess nor the propagation models are used to pick a
   candidate.

Gaps prove nothing: without own test packets there is no denominator, only traffic that
happened to pass by. The data in `data/heard/` holds names and positions of other people's
nodes (never committed); the wording of text messages is not stored.

