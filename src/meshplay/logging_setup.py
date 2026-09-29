"""One-line logging setup shared by all scripts."""

import logging

from meshplay.config import load_settings


def setup_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=level or load_settings().log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
