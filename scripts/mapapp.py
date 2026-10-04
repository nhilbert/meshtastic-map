"""Map app: OpenStreetMap (2D) and laser-scan scene (3D) with data layers and the link tool.

python scripts/mapapp.py [--port 8770] [--open] [--device [PORT]] [--no-log] [--export-scene]
                         [--simulate [TRACK.gpx]] [--sim-speed 4]

Layers: direct link A -> B (computed with all model families), own sites (editable), Meshtastic
nodes (live from the device or from a node export), coverage walk (positions or traceroutes +
GPX, optionally scored against the models), simulated coverage, scene extent. Background tasks
(traceroute walk, coverage simulation) are started and followed in the page. See docs/mapapp.md.

--device connects to the node at start: on USB (auto-detect, or give the port, e.g. COM8) or
over Bluetooth (ble:<address or name>, e.g. ble:Meshtastic_1234; pair it with the computer
first); the page also has a connect button. While connected, every received packet is appended
to data/packets/<date>.jsonl like scripts/listen.py does (switch off with --no-log). Only one
program can use the serial port: close the web client and listen.py first.

--simulate replaces the device by a simulated radio: nothing is transmitted, a fake tracker
(!fa4e0001) walks the given GPX track (at --sim-speed times its pace) or stays near home,
broadcasting like smart position and answering position requests, sent messages are
acknowledged, and a direct message to the tracker starting with ">" is spoken by
the tracker (">?" arrives as "?"). For trying the messaging pane and the coordination mode.

Needs the simulation extras: pip install -e ".[sim]". The 3D view and the link calculator also
need a scene (scripts/sim_build_scene.py).
Layer defaults can be set in data/mapapp/layers.json (see config/mapapp.example.json).
"""

import argparse
from pathlib import Path

from meshplay.mapapp.server import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--open", action="store_true", help="open the browser")
    parser.add_argument(
        "--device",
        nargs="?",
        const="auto",
        help="connect to the node (optionally: serial port, or ble:<address or name>)",
    )
    parser.add_argument("--no-log", action="store_true", help="don't log packets while connected")
    parser.add_argument("--export-scene", action="store_true", help="re-export the 3D scene data")
    parser.add_argument(
        "--simulate",
        nargs="?",
        const="",
        metavar="TRACK.gpx",
        help="simulated radio instead of the device (optionally a GPX track to walk)",
    )
    parser.add_argument(
        "--sim-speed", type=float, default=1.0, help="replay the track this many times faster"
    )
    args = parser.parse_args()
    simulate = None
    if args.simulate is not None:
        simulate = (Path(args.simulate) if args.simulate else None, args.sim_speed)
    run(args.port, args.open, args.export_scene, args.device, not args.no_log, simulate)


if __name__ == "__main__":
    main()
