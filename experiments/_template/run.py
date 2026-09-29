"""Experiment template: copy this folder to experiments/<date>-<name>/ and edit."""

from meshplay import connect, load_settings
from meshplay.logging_setup import setup_logging


def main() -> None:
    setup_logging()
    out_dir = load_settings().data_dir / "experiments" / "template"
    out_dir.mkdir(parents=True, exist_ok=True)

    with connect() as iface:
        me = iface.getMyNodeInfo() or {}
        print("Connected to", me.get("user", {}).get("longName"))
        # Your experiment here.


if __name__ == "__main__":
    main()
