"""Properties for owned, read-only columnar traffic traces."""

from __future__ import annotations

import numpy as np
from hypothesis import given

from tests.property.strategies import trace_events
from trafficlab.trace import TraceEvent, TrafficTrace


@given(trace_events())
def test_traffic_trace_round_trips_canonical_events_without_writable_aliases(events: tuple[TraceEvent, ...]) -> None:
    """Valid event traces retain every value while neither side aliases writable storage."""
    trace = TrafficTrace.from_events(events)

    assert trace.to_events() == events
    assert trace.timestamps.dtype == np.dtype(np.float64)
    assert trace.directions.dtype == np.dtype(np.uint8)
    assert trace.frame_lengths.dtype == np.dtype(np.uint32)
    assert not trace.timestamps.flags.writeable
    assert not trace.directions.flags.writeable
    assert not trace.frame_lengths.flags.writeable
