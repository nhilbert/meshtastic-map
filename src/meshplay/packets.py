"""Helpers for turning received packets into plain, JSON-friendly data."""

from __future__ import annotations

from typing import Any


def to_plain(value: Any) -> Any:
    """Recursively drop "raw" protobuf fields and make everything JSON-serializable."""
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items() if k != "raw"}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
