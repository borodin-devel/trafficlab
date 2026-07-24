"""Tests for bounded nonblocking structured-event routing."""

from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread

import pytest

from trafficlab.libs.observability import (
    EmitResult,
    EventRouter,
    InvalidEventError,
    ObservabilityPolicy,
    Severity,
    StructuredEvent,
)


def _event(
    severity: Severity,
    name: str,
    *,
    seconds: int = 0,
    application: str = "convert",
    run_id: str = "run-1",
) -> StructuredEvent:
    return StructuredEvent(
        datetime(2026, 7, 24, 12, 0, tzinfo=UTC) + timedelta(seconds=seconds),
        severity,
        application,
        run_id,
        name,
        (),
    )


@pytest.mark.unit
def test_router_filters_and_counts_full_normal_queue_exactly() -> None:
    router = EventRouter(
        ObservabilityPolicy(1, 1, 1, Severity.INFO), "convert", "run-1"
    )

    assert router.emit(_event(Severity.DEBUG, "debug")) is EmitResult.FILTERED
    assert router.emit(_event(Severity.INFO, "first")) is EmitResult.ADMITTED
    assert router.emit(_event(Severity.INFO, "second")) is EmitResult.DROPPED
    assert router.low_drop_counts() == ((Severity.INFO, 1),)


@pytest.mark.unit
def test_router_coalesces_repeated_severe_event_then_overflows_new_key() -> None:
    router = EventRouter(ObservabilityPolicy(1, 1, 1), "convert", "run-1")

    assert router.emit(_event(Severity.ERROR, "first")) is EmitResult.ADMITTED
    assert (
        router.emit(_event(Severity.ERROR, "repeat", seconds=1)) is EmitResult.COALESCED
    )
    assert (
        router.emit(_event(Severity.ERROR, "repeat", seconds=2)) is EmitResult.COALESCED
    )
    assert (
        router.emit(_event(Severity.CRITICAL, "novel", seconds=3))
        is EmitResult.OVERFLOWED
    )
    assert router.severe_overflow_count() == 1
    assert router.coalesced_counts() == ((Severity.ERROR, "repeat", 2),)


@pytest.mark.unit
def test_router_rejects_event_for_different_run_identity() -> None:
    router = EventRouter(ObservabilityPolicy(1, 1, 1), "convert", "run-1")

    with pytest.raises(InvalidEventError):
        router.emit(_event(Severity.INFO, "start", run_id="run-2"))


@pytest.mark.unit
def test_concurrent_producers_preserve_exact_normal_and_severe_accounting() -> None:
    normal = EventRouter(ObservabilityPolicy(1, 1, 1), "convert", "run-1")
    severe = EventRouter(ObservabilityPolicy(1, 1, 1), "convert", "run-1")
    start = Barrier(12)
    normal_results: list[EmitResult] = []
    severe_results: list[EmitResult] = []

    def produce(index: int) -> None:
        start.wait()
        normal_results.append(normal.emit(_event(Severity.INFO, f"normal-{index}")))
        severe_results.append(
            severe.emit(_event(Severity.ERROR, "severe", seconds=index))
        )

    threads = [Thread(target=produce, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert normal_results.count(EmitResult.ADMITTED) == 1
    assert normal_results.count(EmitResult.DROPPED) == 11
    assert normal.low_drop_counts() == ((Severity.INFO, 11),)
    assert severe_results.count(EmitResult.ADMITTED) == 1
    assert severe_results.count(EmitResult.COALESCED) == 11
    assert severe.coalesced_counts() == ((Severity.ERROR, "severe", 11),)
