"""Deterministic rendering for standalone JSON documents."""

from __future__ import annotations

import json


def render_json_document(value: object, *, ensure_ascii: bool = True) -> bytes:
    """Render one readable, sorted UTF-8 JSON document with a final newline."""
    return (json.dumps(value, ensure_ascii=ensure_ascii, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def render_json_line(value: object, *, ensure_ascii: bool = True) -> bytes:
    """Render one compact, sorted JSON record with a final newline."""
    return (
        json.dumps(value, ensure_ascii=ensure_ascii, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")
