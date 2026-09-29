"""Print a short summary of the connected node.

python scripts/node_info.py [--port COM8]
"""

import argparse

from meshplay import connect
from meshplay.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="serial port, e.g. COM8 (default: auto-detect)")
    args = parser.parse_args()
    setup_logging()

    with connect(args.port) as iface:
        me = iface.getMyNodeInfo() or {}
        user = me.get("user", {})
        metrics = me.get("deviceMetrics", {})
        meta = iface.metadata
        print(f"Name:      {user.get('longName')} ({user.get('shortName')})")
        print(f"Node ID:   {user.get('id')}")
        print(f"Hardware:  {user.get('hwModel')}")
        print(f"Firmware:  {getattr(meta, 'firmware_version', '?')}")
        print(f"Battery:   {metrics.get('batteryLevel')} % ({metrics.get('voltage')} V)")
        print(f"Known nodes: {len(iface.nodes or {})}")


if __name__ == "__main__":
    main()
