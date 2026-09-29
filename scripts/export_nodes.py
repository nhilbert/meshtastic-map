"""Export the device's node database to a timestamped JSON and CSV file in data/.

python scripts/export_nodes.py [--port COM8]
"""

import argparse
import csv
import json
from datetime import datetime

from meshplay import connect, load_settings
from meshplay.logging_setup import setup_logging

CSV_FIELDS = [
    "id",
    "longName",
    "shortName",
    "hwModel",
    "role",
    "latitude",
    "longitude",
    "altitude",
    "snr",
    "hopsAway",
    "lastHeard",
    "batteryLevel",
]


def flatten(node: dict) -> dict:
    user = node.get("user", {})
    pos = node.get("position", {})
    last = node.get("lastHeard")
    return {
        "id": user.get("id"),
        "longName": user.get("longName"),
        "shortName": user.get("shortName"),
        "hwModel": user.get("hwModel"),
        "role": user.get("role", "CLIENT"),
        "latitude": pos.get("latitude"),
        "longitude": pos.get("longitude"),
        "altitude": pos.get("altitude"),
        "snr": node.get("snr"),
        "hopsAway": node.get("hopsAway"),
        "lastHeard": datetime.fromtimestamp(last).isoformat() if last else None,
        "batteryLevel": node.get("deviceMetrics", {}).get("batteryLevel"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="serial port, e.g. COM8 (default: auto-detect)")
    args = parser.parse_args()
    setup_logging()

    out_dir = load_settings().data_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    with connect(args.port) as iface:
        nodes = dict(iface.nodes or {})

    json_path = out_dir / f"nodes-{stamp}.json"
    json_path.write_text(json.dumps(nodes, indent=2, default=str), encoding="utf-8")

    csv_path = out_dir / f"nodes-{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(flatten(n) for n in nodes.values())

    print(f"Exported {len(nodes)} nodes to\n  {json_path}\n  {csv_path}")


if __name__ == "__main__":
    main()
