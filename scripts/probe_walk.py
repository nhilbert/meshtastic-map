"""Traceroute a walking node from the home node at a fixed interval and log each result.

python scripts/probe_walk.py --to !abcd1234 [--channel 1] [--interval 60] [--timeout 20]

Each probe is a direct traceroute (hop limit 0) on the given channel, so no other node relays
it. Results go to data/probes/<date>.jsonl: one line per probe with ok/timeout, round-trip
time and the SNR in both directions, plus the probe interval and the home node's modem preset
so that later analyses need not ask for them. Match them against a phone GPX track by time. All
received packets are also appended to data/packets/<date>.jsonl, as listen.py does.

The map app runs the same probes as a background task (Aufgaben -> Traceroute-Rundgang).
"""

import argparse
import json
import threading
from datetime import datetime

from pubsub import pub

from meshplay import connect, load_settings
from meshplay.logging_setup import setup_logging
from meshplay.packets import to_plain
from meshplay.probe import modem_preset, run_probes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", required=True, help="node ID of the walking node, e.g. !abcd1234")
    parser.add_argument("--channel", type=int, default=1, help="channel index (default: 1)")
    parser.add_argument("--interval", type=float, default=60, help="seconds between probes")
    parser.add_argument("--timeout", type=float, default=20, help="seconds to wait for a reply")
    parser.add_argument("--count", type=int, help="stop after this many probes (default: run)")
    parser.add_argument("--port", help="serial port, e.g. COM8 (default: auto-detect)")
    args = parser.parse_args()
    setup_logging()

    data_dir = load_settings().data_dir
    (data_dir / "packets").mkdir(parents=True, exist_ok=True)

    def on_receive(packet, interface):
        path = data_dir / "packets" / f"{datetime.now():%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"receivedAt": datetime.now().isoformat(), **to_plain(packet)}))
            f.write("\n")

    pub.subscribe(on_receive, "meshtastic.receive")

    counts = {"ok": 0, "total": 0}

    def on_record(record: dict) -> None:
        counts["total"] += 1
        counts["ok"] += record["result"] == "ok"
        detail = ""
        if record["result"] == "ok":
            detail = (
                f"  there {record['snrTowards']} dB, back {record['snrBack']} dB,"
                f" {record['rttS']} s"
            )
        print(
            f"{record['sentAt'][11:19]} {record['result']:<8}{detail}"
            f"  ({counts['ok']}/{counts['total']} ok)",
            flush=True,
        )

    with connect(args.port) as iface:
        print(
            f"Probing {args.to} on channel {args.channel} every {args.interval:.0f} s "
            f"({modem_preset(iface)})."
        )
        print("Ctrl+C stops.")
        stop = threading.Event()
        try:
            run_probes(
                iface,
                data_dir,
                args.to,
                args.channel,
                args.interval,
                args.timeout,
                args.count,
                stop,
                on_record,
            )
        except KeyboardInterrupt:
            stop.set()
        print(f"Stopped. {counts['ok']} of {counts['total']} probes answered.")


if __name__ == "__main__":
    main()
