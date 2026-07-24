"""Tests for caller-driven structured-observability sink draining."""

from datetime import UTC, datetime

import pytest

from trafficlab.libs.observability import (
    EventRouter,
    ObservabilityPolicy,
    Severity,
    SinkWriteError,
    StructuredEvent,
    drain,
)


class _Clock:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return datetime(2026, 7, 24, 13, 0, tzinfo=UTC)


def _discard(_: bytes) -> None:
    return None


def _router() -> EventRouter:
    router = EventRouter(ObservabilityPolicy(2, 2, 2), "convert", "run-1")
    router.emit(
        StructuredEvent(
            datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
            Severity.INFO,
            "convert",
            "run-1",
            "input_validated",
            (),
        )
    )
    return router


@pytest.mark.unit
def test_drain_uses_clock_once_and_writes_then_flushes() -> None:
    lines: list[bytes] = []
    console: list[str] = []
    flushed: list[None] = []
    clock = _Clock()

    router = _router()
    router.emit(
        StructuredEvent(
            datetime(2026, 7, 24, 12, 1, tzinfo=UTC),
            Severity.INFO,
            "convert",
            "run-1",
            "artifact_validated",
            (),
        )
    )

    result = drain(
        router,
        clock=clock,
        jsonl_sink=lines.append,
        console_sink=console.append,
        flush=lambda: flushed.append(None),
    )

    assert result.event_count == 2
    assert clock.calls == 1
    assert len(lines) == len(console) == 2
    assert len(flushed) == 1

    without_console = _router()
    without_console.emit(
        StructuredEvent(
            datetime(2026, 7, 24, 12, 2, tzinfo=UTC),
            Severity.INFO,
            "convert",
            "run-1",
            "publication",
            (),
        )
    )
    assert (
        drain(
            without_console,
            clock=_Clock(),
            jsonl_sink=_discard,
        ).event_count
        == 2
    )


@pytest.mark.unit
def test_drain_wraps_sink_failure_without_recursive_logging() -> None:
    def failing_sink(_: bytes) -> None:
        raise RuntimeError("closed")

    with pytest.raises(SinkWriteError) as raised:
        drain(_router(), clock=_Clock(), jsonl_sink=failing_sink)

    assert isinstance(raised.value.__cause__, RuntimeError)
