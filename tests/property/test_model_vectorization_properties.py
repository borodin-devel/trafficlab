"""Independent scalar-oracle properties for vectorized model features."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pytest
from hypothesis import assume, given
from numpy.typing import NDArray

from tests.property.strategies import trace_events
from trafficlab.models.common import MarkCount, MarkDistribution
from trafficlab.models.markov_renewal import encode_markov_states, transition_count_matrix, type7_boundaries
from trafficlab.trace import Direction, TraceEvent, TrafficTrace


def _type7(values: NDArray[np.uint32], q: float) -> float:
    """Compute Type 7 independently of the production vector kernel."""
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * q
    lower = math.floor(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return (1.0 - fraction) * ordered[lower] + fraction * ordered[upper]


def _size_bin(frame_length: int, lower: float, upper: float) -> int:
    if frame_length <= lower:
        return 0
    if frame_length <= upper:
        return 1
    return 2


def _scalar_states(trace: TrafficTrace, thresholds: NDArray[np.float64]) -> tuple[list[int], list[int]]:
    identities: list[int] = []
    positions: dict[int, int] = {}
    states: list[int] = []
    for direction, frame_length in zip(trace.directions, trace.frame_lengths, strict=True):
        identity = int(direction) * 3 + _size_bin(int(frame_length), float(thresholds[0]), float(thresholds[1]))
        if identity not in positions:
            positions[identity] = len(identities)
            identities.append(identity)
        states.append(positions[identity])
    return states, identities


def _assert_vector_features_match_scalar_oracles(trace: TrafficTrace) -> None:
    quantiles = (0.25, 0.75)
    thresholds = type7_boundaries(trace.frame_lengths, quantiles)

    assert isinstance(thresholds, np.ndarray)
    assert thresholds.dtype == np.dtype(np.float64)
    assert thresholds.tolist() == [_type7(trace.frame_lengths, quantile) for quantile in quantiles]
    assert thresholds.tolist() == np.quantile(trace.frame_lengths, quantiles, method="linear").tolist()

    states, identities = encode_markov_states(trace.directions, trace.frame_lengths, thresholds)
    scalar_states, scalar_identities = _scalar_states(trace, thresholds)
    assert isinstance(states, np.ndarray)
    assert isinstance(identities, np.ndarray)
    assert states.dtype == np.dtype(np.intp)
    assert identities.dtype == np.dtype(np.uint8)
    assert states.tolist() == scalar_states
    assert identities.tolist() == scalar_identities

    counts = transition_count_matrix(states, len(identities))
    expected_counts = Counter(zip(scalar_states[:-1], scalar_states[1:], strict=True))
    assert counts.dtype == np.dtype(np.int64)
    assert counts.tolist() == [
        [expected_counts[(source, destination)] for destination in range(len(identities))]
        for source in range(len(identities))
    ]

    marks = MarkDistribution.from_trace(trace)
    expected_marks = Counter(
        (
            Direction.OUTBOUND if int(direction) == 0 else Direction.INBOUND,
            int(frame_length),
        )
        for direction, frame_length in zip(trace.directions, trace.frame_lengths, strict=True)
    )
    assert Counter((entry.direction, entry.frame_length, entry.count) for entry in marks.entries) == Counter(
        (direction, frame_length, count) for (direction, frame_length), count in expected_marks.items()
    )
    first_indices: dict[tuple[Direction, int], int] = {}
    for index, (direction, frame_length) in enumerate(zip(trace.directions, trace.frame_lengths, strict=True)):
        mark = (Direction.OUTBOUND if int(direction) == 0 else Direction.INBOUND, int(frame_length))
        first_indices.setdefault(mark, index)
    assert marks.entries == tuple(
        MarkCount(direction, frame_length, expected_marks[(direction, frame_length)])
        for direction, frame_length in sorted(first_indices, key=first_indices.__getitem__)
    )
    assert marks.total_count == len(trace)


def test_vector_features_match_literal_scalar_oracles() -> None:
    """A tied literal trace fixes Type 7, boundary side, counts, and mark order."""
    _assert_vector_features_match_scalar_oracles(
        TrafficTrace.from_events(
            (
                TraceEvent(0.0, Direction.OUTBOUND, 40),
                TraceEvent(0.1, Direction.INBOUND, 80),
                TraceEvent(0.2, Direction.OUTBOUND, 40),
                TraceEvent(0.3, Direction.INBOUND, 120),
                TraceEvent(0.4, Direction.OUTBOUND, 80),
            )
        )
    )


@pytest.mark.parametrize(
    ("frame_lengths", "quantiles"),
    [
        (np.array([], dtype=np.uint32), (0.25, 0.75)),
        (np.array([40], dtype=np.int64), (0.25, 0.75)),
        (np.array([0], dtype=np.uint32), (0.25, 0.75)),
        (np.array([40], dtype=np.uint32), (0.0, 0.75)),
        (np.array([40], dtype=np.uint32), (0.25, 1.0)),
        (np.array([40], dtype=np.uint32), (0.75, 0.25)),
        (np.array([40], dtype=np.uint32), (0.25, 0.25)),
        (np.array([40], dtype=np.uint32), (math.nan, 0.75)),
        (np.array([40], dtype=np.uint32), (0.25, math.inf)),
        (np.array([40], dtype=np.uint32), ()),
        (np.array([40], dtype=np.uint32), (0.25, "0.75")),
    ],
)
def test_type7_boundaries_rejects_noncanonical_columns_and_quantiles(
    frame_lengths: NDArray[np.generic], quantiles: object
) -> None:
    """Loose dtypes, zero frames, or unordered levels would corrupt state boundaries."""
    with pytest.raises(ValueError):
        type7_boundaries(frame_lengths, quantiles)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("directions", "frame_lengths", "thresholds"),
    [
        (np.array([0, 1], dtype=np.int8), np.array([40, 80], dtype=np.uint32), np.array([50.0, 70.0])),
        (np.array([0, 2], dtype=np.uint8), np.array([40, 80], dtype=np.uint32), np.array([50.0, 70.0])),
        (np.array([0, 1], dtype=np.uint8), np.array([40, 80], dtype=np.int64), np.array([50.0, 70.0])),
        (np.array([0, 1], dtype=np.uint8), np.array([0, 80], dtype=np.uint32), np.array([50.0, 70.0])),
        (
            np.array([0, 1], dtype=np.uint8),
            np.array([40, 80], dtype=np.uint32),
            np.array([50.0, 70.0], dtype=np.float32),
        ),
        (np.array([0, 1], dtype=np.uint8), np.array([40, 80], dtype=np.uint32), np.array([50.0, math.inf])),
        (np.array([0, 1], dtype=np.uint8), np.array([40, 80], dtype=np.uint32), np.array([70.0, 50.0])),
        (np.array([0, 1], dtype=np.uint8), np.array([40, 80], dtype=np.uint32), np.array([50.0])),
        (np.array([0, 1], dtype=np.uint8), np.array([40, 80], dtype=np.uint32), np.array([[50.0, 70.0]])),
        (np.array([0], dtype=np.uint8), np.array([40, 80], dtype=np.uint32), np.array([50.0, 70.0])),
        (np.array([], dtype=np.uint8), np.array([], dtype=np.uint32), np.array([50.0, 70.0])),
    ],
)
def test_encode_markov_states_rejects_noncanonical_columns_and_thresholds(
    directions: NDArray[np.generic], frame_lengths: NDArray[np.generic], thresholds: NDArray[np.generic]
) -> None:
    """Invalid column domains must fail before state identity arithmetic."""
    with pytest.raises(ValueError):
        encode_markov_states(directions, frame_lengths, thresholds)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("states", "state_count"),
    [
        (np.array([0, 1], dtype=np.int8), 2),
        ([0, 1], 2),
        (np.array([[0, 1]], dtype=np.intp), 2),
        (np.array([0], dtype=np.intp), 1),
        (np.array([0, 1], dtype=np.intp), True),
        (np.array([0, 1], dtype=np.intp), 0),
        (np.array([-1, 0], dtype=np.intp), 2),
        (np.array([0, 2], dtype=np.intp), 2),
        (np.array([0, 0], dtype=np.intp), np.iinfo(np.intp).max),
    ],
)
def test_transition_count_matrix_rejects_noncanonical_or_unsafe_state_vectors(states: object, state_count: int) -> None:
    """Narrow, out-of-range, or overflowable states must not alias count cells."""
    with pytest.raises(ValueError):
        transition_count_matrix(states, state_count)  # type: ignore[arg-type]


@given(trace_events(min_size=3))
def test_vector_features_match_generated_scalar_oracles(events: tuple[TraceEvent, ...]) -> None:
    """Fit-valid generated traces preserve the scalar feature definitions exactly."""
    trace = TrafficTrace.from_events(events)
    thresholds = type7_boundaries(trace.frame_lengths, (0.25, 0.75))
    assume(thresholds[0] < thresholds[1])
    _assert_vector_features_match_scalar_oracles(trace)
