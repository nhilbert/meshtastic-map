"""Map app: OpenStreetMap (2D) and laser-scan scene (3D) with data layers and the link tool.

python scripts/mapapp.py [--port 8770] [--open] [--device [PORT]] [--no-log] [--export-scene]

Layers: direct link A -> B (computed with all model families), own sites (editable), Meshtastic
nodes (live from the device or from a node export), coverage walk (positions or traceroutes +
GPX, optionally scored against the models), simulated coverage, scene extent. Background tasks
(traceroute walk, coverage simulation) are started and followed in the page. See docs/mapapp.md.

--device connects to the USB node at start (auto-detect, or give the port, e.g. COM8); the page
also has a connect button. While connected, every received packet is appended to
data/packets/<date>.jsonl like scripts/listen.py does (switch off with --no-log). Only one
program can use the serial port: close the web client and listen.py first.

Needs the simulation extras: pip install -e ".[sim]". The 3D view and the link calculator also
need a scene (scripts/sim_build_scene.py).
Layer defaults can be set in data/mapapp/layers.json (see config/mapapp.example.json).
"""

import argparse

from meshplay.mapapp.server import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--open", action="store_true", help="open the browser")
    parser.add_argument(
        "--device", nargs="?", const="auto", help="connect to the USB node (optionally: port)"
    )
    parser.add_argument("--no-log", action="store_true", help="don't log packets while connected")
    parser.add_argument("--export-scene", action="store_true", help="re-export the 3D scene data")
    args = parser.parse_args()
    run(args.port, args.open, args.export_scene, args.device, not args.no_log)


if __name__ == "__main__":
    main()
