import math
from collections.abc import Sequence

import numpy as np
import pytest

from trafficlab.errors import TrafficlabError
from trafficlab.trace import Direction, TraceEvent, TrafficTrace, align_generated, normalize_reference


def _invalid_event(**fields: object) -> TraceEvent:
    """Create deliberately malformed data to verify boundary validation."""
    event = object.__new__(TraceEvent)
    for name, value in fields.items():
        object.__setattr__(event, name, value)
    return event


def test_trace_event_preserves_a_canonical_event() -> None:
    """Changing canonical event fields would corrupt every downstream metric."""
    event = TraceEvent(timestamp=1.25, direction=Direction.OUTBOUND, frame_length=128)

    assert event.timestamp == 1.25
    assert event.direction is Direction.OUTBOUND
    assert event.frame_length == 128


def test_trace_event_is_immutable_and_slotted() -> None:
    """Allowing a fitted trace event to change would break reproducibility."""
    event = TraceEvent(timestamp=0.0, direction=Direction.INBOUND, frame_length=1)

    with pytest.raises(AttributeError):
        event.frame_length = 2  # type: ignore[reportAttributeAccessIssue]
    assert not hasattr(event, "__dict__")


def test_traffic_trace_owns_read_only_typed_columns_and_converts_events() -> None:
    """A caller mutation must not alter the canonical scientific trace."""
    timestamps = np.array([0.0, 0.5], dtype=np.float64)
    directions = np.array([0, 1], dtype=np.uint8)
    frame_lengths = np.array([64, 128], dtype=np.uint32)

    trace = TrafficTrace(timestamps, directions, frame_lengths)
    timestamps[0] = 9.0
    directions[0] = 1
    frame_lengths[0] = 1

    assert trace.timestamps.dtype == np.dtype(np.float64)
    assert trace.directions.dtype == np.dtype(np.uint8)
    assert trace.frame_lengths.dtype == np.dtype(np.uint32)
    assert trace.timestamps.flags.c_contiguous
    assert trace.directions.flags.c_contiguous
    assert trace.frame_lengths.flags.c_contiguous
    assert not trace.timestamps.flags.writeable
    assert not trace.directions.flags.writeable
    assert not trace.frame_lengths.flags.writeable
    assert trace.to_events() == (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(0.5, Direction.INBOUND, 128),
    )
    assert trace == TrafficTrace.from_events(trace.to_events())
    assert isinstance(trace, Sequence)

    with pytest.raises(ValueError):
        trace.timestamps[0] = 1.0


def test_traffic_trace_iats_masks_and_slices_do_not_expose_writable_aliases() -> None:
    """Derived numerical views must not provide a route to mutate trace columns."""
    trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 64),
            TraceEvent(0.25, Direction.INBOUND, 128),
            TraceEvent(1.0, Direction.OUTBOUND, 256),
        )
    )

    sliced = trace[1:]
    iats = trace.iats()
    outbound = trace.direction_mask(Direction.OUTBOUND)

    assert len(trace) == 3
    assert trace[0] == TraceEvent(0.0, Direction.OUTBOUND, 64)
    assert isinstance(sliced, TrafficTrace)
    assert sliced.to_events() == (
        TraceEvent(0.25, Direction.INBOUND, 128),
        TraceEvent(1.0, Direction.OUTBOUND, 256),
    )
    assert np.array_equal(iats, np.array([0.25, 0.75], dtype=np.float64))
    assert np.array_equal(outbound, np.array([True, False, True], dtype=np.bool_))
    assert not iats.flags.writeable
    assert not outbound.flags.writeable


@pytest.mark.parametrize(
    ("timestamps", "directions", "frame_lengths", "message"),
    [
        (
            np.array([0.0], dtype=np.float64),
            np.array([0, 1], dtype=np.uint8),
            np.array([1], dtype=np.uint32),
            "equal length",
        ),
        (
            np.array([[0.0]], dtype=np.float64),
            np.array([0], dtype=np.uint8),
            np.array([1], dtype=np.uint32),
            "one-dimensional",
        ),
        (
            np.array([0.0], dtype=np.float32),
            np.array([0], dtype=np.uint8),
            np.array([1], dtype=np.uint32),
            "timestamps.*float64",
        ),
        (
            np.array([math.nan], dtype=np.float64),
            np.array([0], dtype=np.uint8),
            np.array([1], dtype=np.uint32),
            "finite",
        ),
        (
            np.array([math.inf], dtype=np.float64),
            np.array([0], dtype=np.uint8),
            np.array([1], dtype=np.uint32),
            "finite",
        ),
        (
            np.array([1.0, 0.0], dtype=np.float64),
            np.array([0, 1], dtype=np.uint8),
            np.array([1, 1], dtype=np.uint32),
            "nondecreasing",
        ),
        (np.array([0.0], dtype=np.float64), np.array([2], dtype=np.uint8), np.array([1], dtype=np.uint32), "direction"),
        (np.array([0.0], dtype=np.float64), np.array([0], dtype=np.uint8), np.array([0], dtype=np.uint32), "positive"),
        (
            np.array([0.0], dtype=np.float64),
            np.array([0], dtype=np.uint8),
            np.array([2**32], dtype=np.uint64),
            "uint32",
        ),
    ],
)
def test_traffic_trace_rejects_invalid_columns_before_narrowing(
    timestamps: object, directions: object, frame_lengths: object, message: str
) -> None:
    """Invalid scientific columns must fail before an unsafe representation can escape."""
    with pytest.raises(ValueError, match=message):
        TrafficTrace(timestamps, directions, frame_lengths)  # type: ignore[arg-type]


def test_traffic_trace_from_events_revalidates_corrupted_event_fields() -> None:
    """A bypassed event record must not be silently reclassified during column conversion."""
    corrupted = _invalid_event(timestamp=0.0, direction="outbound", frame_length=64)

    with pytest.raises(TypeError, match="direction"):
        TrafficTrace.from_events((corrupted,))


@pytest.mark.parametrize("timestamp", [-0.1, math.inf, -math.inf, math.nan])
def test_trace_event_rejects_nonfinite_or_negative_timestamps(timestamp: float) -> None:
    """Accepting an invalid timestamp would make trace-window calculations undefined."""
    with pytest.raises(ValueError, match="timestamp"):
        TraceEvent(timestamp=timestamp, direction=Direction.OUTBOUND, frame_length=1)


@pytest.mark.parametrize("timestamp", [0, "0", True])
def test_trace_event_does_not_coerce_timestamp_types(timestamp: object) -> None:
    """Coercing timestamp input would conceal malformed parser output."""
    with pytest.raises(TypeError, match="timestamp"):
        TraceEvent(timestamp=timestamp, direction=Direction.OUTBOUND, frame_length=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("direction", ["outbound", "inbound", "sideways"])
def test_trace_event_requires_a_direction_member(direction: object) -> None:
    """Accepting values outside the enum would invalidate direction-separated metrics."""
    with pytest.raises(TypeError, match="direction"):
        TraceEvent(timestamp=0.0, direction=direction, frame_length=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("frame_length", [0, -1])
def test_trace_event_rejects_nonpositive_frame_lengths(frame_length: int) -> None:
    """A nonpositive captured frame length cannot represent an Ethernet frame."""
    with pytest.raises(ValueError, match="frame_length"):
        TraceEvent(timestamp=0.0, direction=Direction.INBOUND, frame_length=frame_length)


@pytest.mark.parametrize("frame_length", [1.0, "1", True])
def test_trace_event_does_not_coerce_frame_length_types(frame_length: object) -> None:
    """Coercing a frame length would hide an invalid PCAPNG field."""
    with pytest.raises(TypeError, match="frame_length"):
        TraceEvent(timestamp=0.0, direction=Direction.INBOUND, frame_length=frame_length)  # type: ignore[arg-type]


def test_normalize_reference_derives_the_published_closed_window() -> None:
    """A wrong origin or window would give every similarity method different timing data."""
    reference = (
        TraceEvent(timestamp=10.0, direction=Direction.OUTBOUND, frame_length=100),
        TraceEvent(timestamp=11.0, direction=Direction.INBOUND, frame_length=200),
        TraceEvent(timestamp=13.0, direction=Direction.OUTBOUND, frame_length=300),
    )

    normalized, window = normalize_reference(reference)

    assert normalized == (
        TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=100),
        TraceEvent(timestamp=1.0, direction=Direction.INBOUND, frame_length=200),
        TraceEvent(timestamp=3.0, direction=Direction.OUTBOUND, frame_length=300),
    )
    assert window == 3.0
    assert isinstance(normalized, TrafficTrace)


def test_align_generated_shifts_then_keeps_both_window_endpoints() -> None:
    """Dropping either endpoint would compare models over an open rather than closed interval."""
    generated = (
        TraceEvent(timestamp=7.0, direction=Direction.INBOUND, frame_length=10),
        TraceEvent(timestamp=8.0, direction=Direction.OUTBOUND, frame_length=20),
        TraceEvent(timestamp=10.0, direction=Direction.INBOUND, frame_length=30),
        TraceEvent(timestamp=10.1, direction=Direction.OUTBOUND, frame_length=40),
    )

    aligned = align_generated(generated, 3.0)

    assert aligned == (
        TraceEvent(timestamp=0.0, direction=Direction.INBOUND, frame_length=10),
        TraceEvent(timestamp=1.0, direction=Direction.OUTBOUND, frame_length=20),
        TraceEvent(timestamp=3.0, direction=Direction.INBOUND, frame_length=30),
    )


def test_align_generated_preserves_naturally_early_completion() -> None:
    """Padding a short generated trace would invent packets instead of exposing trailing silence."""
    generated = (
        TraceEvent(timestamp=2.0, direction=Direction.OUTBOUND, frame_length=100),
        TraceEvent(timestamp=2.5, direction=Direction.INBOUND, frame_length=101),
    )

    assert align_generated(generated, 3.0) == (
        TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=100),
        TraceEvent(timestamp=0.5, direction=Direction.INBOUND, frame_length=101),
    )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ((), "at least two events"),
        ((TraceEvent(timestamp=1.0, direction=Direction.OUTBOUND, frame_length=1),), "at least two events"),
        (
            (
                TraceEvent(timestamp=2.0, direction=Direction.OUTBOUND, frame_length=1),
                TraceEvent(timestamp=1.0, direction=Direction.INBOUND, frame_length=1),
            ),
            "nondecreasing",
        ),
        (
            (
                _invalid_event(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=1),
                _invalid_event(timestamp=math.nan, direction=Direction.INBOUND, frame_length=1),
            ),
            "finite",
        ),
        (
            (
                _invalid_event(timestamp=0.0, direction="outbound", frame_length=1),
                TraceEvent(timestamp=1.0, direction=Direction.INBOUND, frame_length=1),
            ),
            "direction",
        ),
        (
            (
                _invalid_event(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=0),
                TraceEvent(timestamp=1.0, direction=Direction.INBOUND, frame_length=1),
            ),
            "frame length",
        ),
        (
            (
                _invalid_event(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=1),
                object(),  # type: ignore[arg-type]
            ),
            "TraceEvent",
        ),
        (
            (object.__new__(TraceEvent), TraceEvent(timestamp=1.0, direction=Direction.INBOUND, frame_length=1)),
            "event data",
        ),
    ],
)
def test_normalize_reference_rejects_invalid_research_inputs(events: tuple[TraceEvent, ...], message: str) -> None:
    """Invalid reference data must fail explicitly before fitting can consume it."""
    with pytest.raises(TrafficlabError, match=message) as error:
        normalize_reference(events)

    assert error.value.corrective_action == "provide finite nondecreasing canonical reference events"


def test_normalize_reference_rejects_a_zero_observation_window() -> None:
    """A tied first and last packet cannot define a positive shared comparison boundary."""
    reference = (
        TraceEvent(timestamp=2.0, direction=Direction.OUTBOUND, frame_length=1),
        TraceEvent(timestamp=2.0, direction=Direction.INBOUND, frame_length=1),
    )

    with pytest.raises(TrafficlabError, match="observation window") as error:
        normalize_reference(reference)

    assert error.value.corrective_action == "provide finite nondecreasing canonical reference events"


@pytest.mark.parametrize("window", [0.0, -0.1, math.inf, -math.inf, math.nan, 1, True, "1"])
def test_align_generated_rejects_an_invalid_observation_window(window: object) -> None:
    """A nonpositive or nonfinite boundary makes generated-trace cropping undefined."""
    generated = (TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=1),)

    with pytest.raises(TrafficlabError, match="observation window") as error:
        align_generated(generated, window)  # type: ignore[arg-type]

    assert error.value.corrective_action == "provide a finite positive observation window"


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ((), "at least one event"),
        (
            (
                TraceEvent(timestamp=1.0, direction=Direction.OUTBOUND, frame_length=1),
                TraceEvent(timestamp=0.0, direction=Direction.INBOUND, frame_length=1),
            ),
            "nondecreasing",
        ),
        (
            (
                TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=1),
                _invalid_event(timestamp=math.inf, direction=Direction.INBOUND, frame_length=1),
            ),
            "finite",
        ),
        ((_invalid_event(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=-1),), "frame length"),
    ],
)
def test_align_generated_rejects_invalid_trace_before_cropping(events: tuple[TraceEvent, ...], message: str) -> None:
    """Malformed later events must not evade validation merely because they are outside the window."""
    with pytest.raises(TrafficlabError, match=message) as error:
        align_generated(events, 0.5)

    assert error.value.corrective_action == "provide finite nondecreasing canonical generated events"
