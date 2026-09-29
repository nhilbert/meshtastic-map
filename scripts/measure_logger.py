"""Fixed-point link measurement: send N numbered packets, log what arrives (Messprotokoll CSV).

Ported from the Mesh Bonn project (20_Messung/mess_logger.py). The Range Test module only
writes rangetest.csv on ESP32 devices; the Wio L1 Pro, P1-Pro and T1000-E are nRF52840, so the
receiver is logged over USB instead.

Sender (device A):
    python scripts/measure_logger.py send --scenario A1 --packets 10 --interval 20 --port COM4

Receiver (device B, on the laptop):
    python scripts/measure_logger.py listen --scenario A1 --packets 10 --port COM8 \
        --site-tx ROOF --site-rx HOME --window-tx open --window-rx open

The sender transmits texts like "A1 03/10"; the receiver takes the packet number from them and
adds a row with Angekommen_JN=N for every missing packet (without them the delivery rate is
too optimistic). Relayed packets (hops > 0) are logged but marked "NICHT WERTEN" and are
ignored by scripts/sim_score.py. Scenario IDs must match data/sim/sites.json (A1 ... and the
corridor points K100 ...).

Output: data/measurements/Messprotokoll.csv (semicolon-separated, column names as in the
original protocol so existing files stay compatible). Ctrl+C stops; the CSV is still written.
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from pubsub import pub

from meshplay import connect, load_settings
from meshplay.logging_setup import setup_logging

COLUMNS = [
    "Szenario",
    "Datum_Zeit",
    "Protokoll",
    "Sender",
    "Empfaenger",
    "Standort_Sender",
    "Standort_Empfaenger",
    "Fenster_Sender",
    "Fenster_Empfaenger",
    "RSSI_dBm",
    "SNR_dB",
    "Hops",
    "Nachricht_Nr",
    "Angekommen_JN",
    "Latenz_s",
    "Bemerkung",
]
PATTERN = re.compile(r"^\s*([A-Za-z0-9]+)\s+(\d+)\s*/\s*(\d+)\s*$")
NOT_SCORED = "NICHT WERTEN"


def hops_of(packet: dict) -> str:
    """Hops taken, or "" if the firmware doesn't report hopStart.

    The Python API leaves hopLimit out of the packet when it is 0, which is exactly the
    relayed case with hop_limit 1; a missing hopLimit therefore means 0.
    """
    hop_start = packet.get("hopStart")
    if hop_start is None:
        return ""
    return str(int(hop_start) - int(packet.get("hopLimit") or 0))


def send(args) -> None:
    with connect(args.port) as iface:
        for i in range(1, args.packets + 1):
            text = f"{args.scenario} {i:02d}/{args.packets:02d}"
            iface.sendText(text, wantAck=False, channelIndex=args.channel)
            print(f"sent: {text}", flush=True)
            if i < args.packets:
                time.sleep(args.interval)
    print("done.")


class Recording:
    def __init__(self, args):
        self.args = args
        self.hits: dict[int, dict] = {}
        self.start = time.time()

    def base_row(self, nr: int) -> dict:
        a = self.args
        return dict(
            Szenario=a.scenario,
            Protokoll="meshtastic",
            Standort_Sender=a.site_tx,
            Standort_Empfaenger=a.site_rx,
            Fenster_Sender=a.window_tx,
            Fenster_Empfaenger=a.window_rx,
            Nachricht_Nr=str(nr),
            Latenz_s="",
            Bemerkung=a.note,
        )

    def on_receive(self, packet, interface=None) -> None:
        try:
            dec = packet.get("decoded") or {}
            if dec.get("portnum") not in ("TEXT_MESSAGE_APP", 1):
                return
            text = dec.get("text")
            if text is None:
                raw = dec.get("payload")
                text = raw.decode("utf-8", "replace") if raw else ""
            m = PATTERN.match(text)
            if not m or m.group(1).upper() != self.args.scenario.upper():
                print(f"  (ignored: {text!r})", flush=True)
                return
            nr = int(m.group(2))
            rssi, snr, hops = packet.get("rxRssi"), packet.get("rxSnr"), hops_of(packet)
            row = self.base_row(nr)
            if hops not in ("", "0"):
                row["Bemerkung"] = (row["Bemerkung"] + " | " if row["Bemerkung"] else "") + (
                    f"ueber Relais, Hops={hops}: {NOT_SCORED}"
                )
            sender = packet.get("fromId") or f"!{packet.get('from', 0):08x}"
            row.update(
                Datum_Zeit=datetime.now().isoformat(timespec="seconds"),
                Sender=sender,
                Empfaenger=str(packet.get("toId") or packet.get("to") or ""),
                RSSI_dBm="" if rssi is None else f"{float(rssi):.0f}",
                SNR_dB="" if snr is None else f"{float(snr):.2f}",
                Hops=hops,
                Angekommen_JN="J",
            )
            self.hits[nr] = row
            print(
                f"  {nr:02d}/{self.args.packets:02d}  RSSI {rssi} dBm  SNR {snr} dB  "
                f"hops {hops or '?'}",
                flush=True,
            )
        except Exception as e:  # a broken packet must not stop the measurement
            print(f"  [error while parsing: {e}]", file=sys.stderr, flush=True)

    def rows(self) -> list[dict]:
        out = []
        for nr in range(1, self.args.packets + 1):
            if nr in self.hits:
                out.append(self.hits[nr])
            else:
                row = self.base_row(nr)
                row.update(
                    Datum_Zeit="",
                    Sender="",
                    Empfaenger="",
                    RSSI_dBm="",
                    SNR_dB="",
                    Hops="",
                    Angekommen_JN="N",
                )
                out.append(row)
        return out


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def listen(args) -> None:
    rec = Recording(args)
    pub.subscribe(rec.on_receive, "meshtastic.receive")
    with connect(args.port):
        print(
            f"listening for scenario {args.scenario}, expecting {args.packets} packets. "
            "Ctrl+C to stop.",
            flush=True,
        )
        try:
            while len(rec.hits) < args.packets:
                time.sleep(0.5)
                if args.timeout and time.time() - rec.start > args.timeout:
                    print("timeout reached.", flush=True)
                    break
        except KeyboardInterrupt:
            print("\nstopped.", flush=True)
    rows = rec.rows()
    write(args.out, rows)
    arrived = sum(1 for r in rows if r["Angekommen_JN"] == "J")
    direct = [
        float(r["RSSI_dBm"])
        for r in rows
        if r["Angekommen_JN"] == "J" and r["RSSI_dBm"] and r["Hops"] in ("", "0")
    ]
    print(f"\n{arrived}/{args.packets} arrived, written to {args.out}.")
    if direct:
        print(
            f"Mean RSSI of directly received packets: {sum(direct) / len(direct):.1f} dBm "
            f"({len(direct)} packets)"
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="mode", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--scenario", required=True, help="scenario ID, e.g. A1 or K300")
    common.add_argument("--packets", type=int, default=10, help="packets (predictions assume 10)")
    common.add_argument("--port", help="serial port, e.g. COM8 (default: auto-detect)")

    ps = sub.add_parser("send", parents=[common])
    ps.add_argument("--interval", type=float, default=20.0, help="seconds between packets")
    ps.add_argument("--channel", type=int, default=0, help="channel index")
    ps.set_defaults(func=send)

    default_out = load_settings().data_dir / "measurements" / "Messprotokoll.csv"
    pl = sub.add_parser("listen", parents=[common])
    pl.add_argument("--out", type=Path, default=default_out)
    pl.add_argument("--site-tx", default="")
    pl.add_argument("--site-rx", default="")
    pl.add_argument("--window-tx", default="", help="e.g. open / closed / outside")
    pl.add_argument("--window-rx", default="")
    pl.add_argument("--note", default="")
    pl.add_argument("--timeout", type=float, default=900.0, help="seconds, 0 = no limit")
    pl.set_defaults(func=listen)

    args = p.parse_args()
    setup_logging("WARNING")
    args.func(args)


if __name__ == "__main__":
    main()
