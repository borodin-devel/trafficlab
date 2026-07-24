# pyright: reportArgumentType=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownVariableType=false
"""Tests for defensive observability boundary paths."""

from datetime import UTC, datetime
from math import nan

import pytest

from trafficlab.libs.observability import (
    EventRouter,
    InvalidEventError,
    InvalidPolicyError,
    InvalidSeverityError,
    InvalidSinkError,
    ObservabilityPolicy,
    Severity,
    StructuredEvent,
    drain,
    plan_drain,
    render_console,
    render_jsonl,
    severity_rank,
)


def _event(fields: object = ()) -> StructuredEvent:
    return StructuredEvent(
        datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        Severity.INFO,
        "convert",
        "run-1",
        "event",
        fields,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_values_reject_remaining_runtime_boundaries() -> None:
    with pytest.raises(InvalidSeverityError):
        severity_rank("INFO")
    for application in ("", "тест"):
        with pytest.raises(InvalidEventError):
            StructuredEvent(
                datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
                Severity.INFO,
                application,
                "run-1",
                "event",
                (),
            )
    for fields in (
        (("count", 1 << 64),),
        (("fraction", nan),),
        (("text", "x" * 1025),),
        (("text", "line\nvalue"),),
        [
            (),
        ],
        ("not-a-pair",),
        (("only-name",),),
        tuple((f"field{index}", index) for index in range(33)),
    ):
        with pytest.raises(InvalidEventError):
            _event(fields)
    with pytest.raises(InvalidPolicyError):
        ObservabilityPolicy(1, 1, 1, "INFO")


@pytest.mark.unit
def test_router_and_renderers_reject_invalid_runtime_inputs() -> None:
    with pytest.raises(InvalidEventError):
        EventRouter(object(), "convert", "run-1")
    router = EventRouter(ObservabilityPolicy(1, 1, 1), "convert", "run-1")
    with pytest.raises(InvalidEventError):
        router.emit(object())
    with pytest.raises(InvalidEventError):
        plan_drain(object(), datetime(2026, 7, 24, 12, 0, tzinfo=UTC))
    with pytest.raises(InvalidEventError):
        render_jsonl(object())
    with pytest.raises(InvalidEventError):
        render_console(object())


@pytest.mark.unit
def test_drain_validates_injected_boundaries_and_skips_empty_flush() -> None:
    router = EventRouter(ObservabilityPolicy(1, 1, 1), "convert", "run-1")
    for kwargs in (
        {"clock": object(), "jsonl_sink": lambda _: None},
        {"clock": lambda: datetime.now(UTC), "jsonl_sink": object()},
        {
            "clock": lambda: datetime.now(UTC),
            "jsonl_sink": lambda _: None,
            "console_sink": object(),
        },
        {
            "clock": lambda: datetime.now(UTC),
            "jsonl_sink": lambda _: None,
            "flush": object(),
        },
    ):
        with pytest.raises(InvalidSinkError):
            drain(router, **kwargs)  # type: ignore[arg-type]
    flushed: list[None] = []
    assert (
        drain(
            router,
            clock=lambda: datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
            jsonl_sink=lambda _: None,
            flush=lambda: flushed.append(None),
        ).event_count
        == 0
    )
    assert not flushed
