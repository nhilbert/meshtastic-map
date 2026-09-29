"""Send a text message to the mesh (broadcast by default).

python scripts/send_text.py "hello mesh" [--to !1234abcd] [--channel 0] [--port COM8]
"""

import argparse

from meshplay import connect
from meshplay.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("text")
    parser.add_argument("--to", default="^all", help="node ID like !1234abcd (default: broadcast)")
    parser.add_argument("--channel", type=int, default=0, help="channel index (default: 0)")
    parser.add_argument("--port", help="serial port, e.g. COM8 (default: auto-detect)")
    args = parser.parse_args()
    setup_logging()

    with connect(args.port) as iface:
        iface.sendText(args.text, destinationId=args.to, channelIndex=args.channel)
        print(f"Sent to {args.to} on channel {args.channel}: {args.text}")


if __name__ == "__main__":
    main()
