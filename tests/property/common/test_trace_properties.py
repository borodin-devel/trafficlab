"""Properties for canonical trace normalization and alignment."""

from __future__ import annotations

import math
from typing import Any, cast

from hypothesis import given, settings

from tests.property.strategies import trace_events
from trafficlab.common.trace import TraceEvent, align_generated, normalize_reference


def test_locked_hypothesis_profile() -> None:
    profile = cast(Any, settings.get_profile("trafficlab_locked"))
    assert profile.derandomize is True
    assert profile.database is None
    assert profile.deadline is None
    assert profile.max_examples == 100


@given(trace_events(min_size=2).filter(lambda events: events[-1].timestamp > events[0].timestamp))
def test_normalize_reference_preserves_events_and_nonnegative_non_decreasing_timestamps(
    events: tuple[TraceEvent, ...],
) -> None:
    normalized, window = normalize_reference(events)

    assert window == events[-1].timestamp - events[0].timestamp
    assert normalized[0].timestamp == 0.0
    assert tuple(event.direction for event in normalized) == tuple(event.direction for event in events)
    assert tuple(event.frame_length for event in normalized) == tuple(event.frame_length for event in events)
    assert all(math.isfinite(event.timestamp) and event.timestamp >= 0.0 for event in normalized)
    assert all(left.timestamp <= right.timestamp for left, right in zip(normalized, normalized[1:], strict=False))


@given(trace_events(), trace_events().map(lambda events: events[-1].timestamp + 0.5))
def test_align_generated_keeps_closed_window_and_non_decreasing_timestamps(
    events: tuple[TraceEvent, ...], window: float
) -> None:
    aligned = align_generated(events, window)

    assert aligned[0].timestamp == 0.0
    assert all(0.0 <= event.timestamp <= window for event in aligned)
    assert all(left.timestamp <= right.timestamp for left, right in zip(aligned, aligned[1:], strict=False))
