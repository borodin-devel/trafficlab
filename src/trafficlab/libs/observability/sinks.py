"""Canonical JSONL and concise-console structured-event rendering."""

from __future__ import annotations

import json

from .errors import InvalidEventError
from .values import StructuredEvent


def render_jsonl(event: object) -> bytes:
    """Render one validated event as canonical UTF-8 JSON Lines."""

    if not isinstance(event, StructuredEvent):
        raise InvalidEventError("JSONL renderer requires a StructuredEvent")
    record = {
        "timestamp": event.timestamp.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "severity": event.severity.value,
        "application": event.application,
        "run_id": event.run_id,
        "event_name": event.event_name,
        "fields": dict(event.fields),
    }
    return (
        json.dumps(
            record, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )


def render_console(event: object) -> str:
    """Render one event as bounded one-line human-readable diagnostics."""

    if not isinstance(event, StructuredEvent):
        raise InvalidEventError("console renderer requires a StructuredEvent")
    fields = json.dumps(
        dict(event.fields), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    return (
        f"{event.timestamp.isoformat(timespec='microseconds').replace('+00:00', 'Z')} "
        f"{event.severity.value} {event.application}/{event.run_id} "
        f"{event.event_name} {fields}"
    )
