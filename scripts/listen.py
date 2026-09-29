"""Print received packets and append them to data/packets/<date>.jsonl. Stop with Ctrl+C.

python scripts/listen.py [--port COM8] [--text-only]
"""

import argparse
import json
import time
from datetime import datetime

from pubsub import pub

from meshplay import connect, load_settings
from meshplay.logging_setup import setup_logging
from meshplay.packets import to_plain


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="serial port, e.g. COM8 (default: auto-detect)")
    parser.add_argument("--text-only", action="store_true", help="only show text messages")
    args = parser.parse_args()
    setup_logging()

    out_dir = load_settings().data_dir / "packets"
    out_dir.mkdir(parents=True, exist_ok=True)

    def on_receive(packet, interface):
        plain = to_plain(packet)
        decoded = plain.get("decoded", {})
        portnum = decoded.get("portnum", "?")
        if args.text_only and portnum != "TEXT_MESSAGE_APP":
            return
        now = datetime.now()
        path = out_dir / f"{now:%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"receivedAt": now.isoformat(), **plain}) + "\n")
        detail = decoded.get("text", "")
        # fromId is None for senders not yet in the node DB; derive it from the node number.
        sender = plain.get("fromId") or f"!{plain.get('from', 0):08x}"
        print(
            f"{now:%H:%M:%S} {sender} -> {plain.get('toId')} {portnum} {detail}",
            flush=True,
        )

    pub.subscribe(on_receive, "meshtastic.receive")
    with connect(args.port):
        print("Listening... press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()
