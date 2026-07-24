"""Tests for deterministic observability drain rendering."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from trafficlab.libs.observability import (
    EventRouter,
    ObservabilityPolicy,
    Severity,
    StructuredEvent,
    plan_drain,
    render_console,
    render_jsonl,
)


def _event(severity: Severity, name: str, seconds: int = 0) -> StructuredEvent:
    return StructuredEvent(
        datetime(2026, 7, 24, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds),
        severity,
        "convert",
        "run-1",
        name,
        (("count", 1),),
    )


@pytest.mark.unit
def test_plan_drain_prioritizes_reserved_and_exact_summaries() -> None:
    router = EventRouter(
        ObservabilityPolicy(1, 1, 1, Severity.DEBUG), "convert", "run-1"
    )
    router.emit(_event(Severity.DEBUG, "debug"))
    router.emit(_event(Severity.DEBUG, "dropped"))
    router.emit(_event(Severity.ERROR, "error"))
    router.emit(_event(Severity.ERROR, "coalesced", 1))
    router.emit(_event(Severity.CRITICAL, "overflow", 2))

    batch = plan_drain(router, datetime(2026, 7, 24, 13, 0, tzinfo=UTC))

    assert [event.event_name for event in batch.events] == [
        "error",
        "debug",
        "observability.severe_coalesced",
        "observability.low_severity_dropped",
        "observability.severe_overflow",
    ]
    assert batch.events[2].fields == (
        ("count", 1),
        ("event_name", "coalesced"),
        ("first_timestamp", "2026-07-24T12:00:01.000000Z"),
        ("last_timestamp", "2026-07-24T12:00:01.000000Z"),
        ("severity", "ERROR"),
    )


@pytest.mark.unit
def test_render_jsonl_is_one_canonical_json_object_and_console_is_single_line() -> None:
    event = _event(Severity.WARNING, "input_validated")

    rendered = render_jsonl(event)

    assert rendered.endswith(b"\n")
    assert list(json.loads(rendered)) == [
        "timestamp",
        "severity",
        "application",
        "run_id",
        "event_name",
        "fields",
    ]
    assert render_console(event).count("\n") == 0
