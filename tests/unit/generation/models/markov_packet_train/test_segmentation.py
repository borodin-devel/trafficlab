"""Packet-train segmentation boundary tests."""

from __future__ import annotations

import pytest

from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.markov_packet_train.segmentation import (
    PacketTrain,
    position_class,
    segment_trains,
)


def _trace(timestamps: tuple[float, ...]) -> TrafficTrace:
    return TrafficTrace.from_events(
        tuple(TraceEvent(timestamp, Direction.OUTBOUND, 60 + index) for index, timestamp in enumerate(timestamps))
    )


def test_gap_equal_to_threshold_remains_inside_train_and_final_train_is_retained() -> None:
    """Changing <= to < would split the equality packet and losing the tail would omit the final train."""
    trains = segment_trains(_trace((0.0, 1.0, 3.0, 6.0)), gap_threshold=2.0)

    assert trains == (PacketTrain(0, 3), PacketTrain(3, 4))
    assert tuple(train.length for train in trains) == (3, 1)


def test_consecutive_long_gaps_produce_single_packet_trains() -> None:
    """Treating empty intervals as trains would corrupt actual-length and mark reservoirs."""
    assert segment_trains(_trace((0.0, 5.0, 10.0)), gap_threshold=1.0) == (
        PacketTrain(0, 1),
        PacketTrain(1, 2),
        PacketTrain(2, 3),
    )


@pytest.mark.parametrize(
    ("index", "length", "expected"),
    ((0, 1, "first"), (0, 4, "first"), (1, 4, "interior"), (2, 4, "interior"), (3, 4, "last")),
)
def test_position_classes_are_disjoint_with_singletons_owned_by_first(index: int, length: int, expected: str) -> None:
    """Double-counting a singleton as first and last would break the fitted packet-count identities."""
    assert position_class(index, length) == expected


@pytest.mark.parametrize(
    ("index", "length"),
    ((-1, 2), (2, 2), (0, 0), (True, 2), (0, True)),
)
def test_position_class_rejects_noncanonical_indices(index: object, length: object) -> None:
    """Invalid indices must fail before selecting a position-conditioned reservoir."""
    with pytest.raises((TypeError, ValueError), match="position|length"):
        position_class(index, length)  # type: ignore[arg-type]
