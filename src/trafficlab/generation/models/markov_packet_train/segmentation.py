"""Reference-only packet-train segmentation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from trafficlab.common.trace import TrafficTrace

type PacketPosition = Literal["first", "interior", "last"]


@dataclass(frozen=True, slots=True)
class PacketTrain:
    """One nonempty half-open packet-index interval in a reference trace."""

    start: int
    stop: int

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.stop) is not int:
            raise TypeError("packet-train bounds must be exact integers")
        if self.start < 0 or self.stop <= self.start:
            raise ValueError("packet-train bounds must describe a nonempty half-open interval")

    @property
    def length(self) -> int:
        return self.stop - self.start


def position_class(packet_index: int, train_length: int) -> PacketPosition:
    """Classify one packet position; a singleton belongs only to ``first``."""
    if type(packet_index) is not int or type(train_length) is not int:
        raise TypeError("packet position and train length must be exact integers")
    if train_length <= 0 or not 0 <= packet_index < train_length:
        raise ValueError("packet position must be within a positive train length")
    if packet_index == 0:
        return "first"
    if packet_index == train_length - 1:
        return "last"
    return "interior"


def segment_trains(trace: TrafficTrace, gap_threshold: float) -> tuple[PacketTrain, ...]:
    """Split where an adjacent gap is strictly greater than the frozen threshold."""
    if type(trace) is not TrafficTrace or len(trace) == 0:
        raise ValueError("packet-train segmentation requires a nonempty TrafficTrace")
    if type(gap_threshold) is not float or not math.isfinite(gap_threshold) or gap_threshold < 0.0:
        raise ValueError("packet-train gap threshold must be a finite nonnegative exact float")
    gaps = np.diff(trace.timestamps)
    split_destinations = np.flatnonzero(gaps > gap_threshold) + 1
    starts = (0, *(int(value) for value in split_destinations))
    stops = (*starts[1:], len(trace))
    return tuple(PacketTrain(start, stop) for start, stop in zip(starts, stops, strict=True))
