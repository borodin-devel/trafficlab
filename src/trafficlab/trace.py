"""Canonical trace values and strict capture metadata boundaries."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast, overload

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError, field_validator

from trafficlab.errors import TrafficlabError

_MAC_PATTERN = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")


class Direction(StrEnum):
    """The only packet directions supported by Trafficlab traces."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One validated packet event in the canonical trace representation."""

    timestamp: float
    direction: Direction
    frame_length: int

    def __post_init__(self) -> None:
        if type(self.timestamp) is not float:
            raise TypeError("timestamp must be a float")
        if not math.isfinite(self.timestamp) or self.timestamp < 0.0:
            raise ValueError("timestamp must be finite and nonnegative")
        if type(self.direction) is not Direction:
            raise TypeError("direction must be a Direction member")
        if type(self.frame_length) is not int:
            raise TypeError("frame_length must be an integer")
        if self.frame_length <= 0:
            raise ValueError("frame_length must be positive")


@dataclass(frozen=True, slots=True, eq=False)
class TrafficTrace(Sequence[TraceEvent]):
    """Owned, read-only numeric columns for one canonical traffic trace."""

    timestamps: NDArray[np.float64]
    directions: NDArray[np.uint8]
    frame_lengths: NDArray[np.uint32]

    def __post_init__(self) -> None:
        raw_timestamps: object = self.timestamps
        raw_directions: object = self.directions
        raw_frame_lengths: object = self.frame_lengths
        if (
            not isinstance(raw_timestamps, np.ndarray)  # pyright: ignore[reportUnnecessaryIsInstance]
            or not isinstance(raw_directions, np.ndarray)  # pyright: ignore[reportUnnecessaryIsInstance]
            or not isinstance(raw_frame_lengths, np.ndarray)  # pyright: ignore[reportUnnecessaryIsInstance]
        ):
            raise ValueError("traffic trace columns must be NumPy arrays")
        timestamps = raw_timestamps
        directions = cast(NDArray[np.unsignedinteger[Any]], raw_directions)
        frame_lengths = cast(NDArray[np.unsignedinteger[Any]], raw_frame_lengths)
        if timestamps.ndim != 1 or directions.ndim != 1 or frame_lengths.ndim != 1:
            raise ValueError("traffic trace columns must be one-dimensional")
        if len(timestamps) != len(directions) or len(timestamps) != len(frame_lengths):
            raise ValueError("traffic trace columns must have equal length")
        if timestamps.dtype != np.dtype(np.float64):
            raise ValueError("timestamps must have dtype float64")
        if not np.issubdtype(directions.dtype, np.unsignedinteger):
            raise ValueError("directions must have an unsigned integer dtype")
        if not np.issubdtype(frame_lengths.dtype, np.unsignedinteger):
            raise ValueError("frame_lengths must have an unsigned integer dtype")
        if not np.all(np.isfinite(timestamps)) or np.any(timestamps < 0.0):
            raise ValueError("timestamps must be finite and nonnegative")
        if len(timestamps) > 1 and np.any(np.diff(timestamps) < 0.0):
            raise ValueError("timestamps must be nondecreasing")
        if np.any((directions != 0) & (directions != 1)):
            raise ValueError("directions must contain only 0 (outbound) or 1 (inbound)")
        if np.any(frame_lengths == 0):
            raise ValueError("frame_lengths must be positive")
        if np.any(frame_lengths > np.iinfo(np.uint32).max):
            raise ValueError("frame_lengths must fit in uint32")

        owned_timestamps = np.frombuffer(
            np.array(timestamps, dtype=np.float64, copy=True, order="C").tobytes(), dtype=np.float64
        )
        owned_directions = np.frombuffer(
            np.array(directions, dtype=np.uint8, copy=True, order="C").tobytes(), dtype=np.uint8
        )
        owned_frame_lengths = np.frombuffer(
            np.array(frame_lengths, dtype=np.uint32, copy=True, order="C").tobytes(), dtype=np.uint32
        )
        object.__setattr__(self, "timestamps", owned_timestamps)
        object.__setattr__(self, "directions", owned_directions)
        object.__setattr__(self, "frame_lengths", owned_frame_lengths)

    @classmethod
    def from_events(cls, events: Iterable[TraceEvent]) -> TrafficTrace:
        """Convert canonical event records into one owned columnar trace."""
        materialized = tuple(events)
        for event in materialized:
            if type(event) is not TraceEvent:
                raise TypeError("traffic trace events must be TraceEvent values")
            if type(event.timestamp) is not float or not math.isfinite(event.timestamp) or event.timestamp < 0.0:
                raise ValueError("event timestamp must be finite and nonnegative")
            if type(event.direction) is not Direction:
                raise TypeError("event direction must be a Direction member")
            if type(event.frame_length) is not int or event.frame_length <= 0:
                raise ValueError("event frame_length must be positive")
            if event.frame_length > np.iinfo(np.uint32).max:
                raise ValueError("event frame_length must fit in uint32")
        return cls(
            np.array([event.timestamp for event in materialized], dtype=np.float64),
            np.array([0 if event.direction is Direction.OUTBOUND else 1 for event in materialized], dtype=np.uint8),
            np.array([event.frame_length for event in materialized], dtype=np.uint32),
        )

    def to_events(self) -> tuple[TraceEvent, ...]:
        """Convert this trace at the immutable event-record boundary."""
        return tuple(
            TraceEvent(
                timestamp=float(timestamp),
                direction=Direction.OUTBOUND if direction == 0 else Direction.INBOUND,
                frame_length=int(frame_length),
            )
            for timestamp, direction, frame_length in zip(
                self.timestamps, self.directions, self.frame_lengths, strict=True
            )
        )

    def iats(self) -> NDArray[np.float64]:
        """Return owned read-only inter-arrival times, retaining zero intervals."""
        return np.frombuffer(np.diff(self.timestamps).tobytes(), dtype=np.float64)

    def direction_mask(self, direction: Direction) -> NDArray[np.bool_]:
        """Return an owned read-only mask for one canonical direction."""
        if type(direction) is not Direction:
            raise TypeError("direction must be a Direction member")
        values = self.directions == (0 if direction is Direction.OUTBOUND else 1)
        return np.frombuffer(values.tobytes(), dtype=np.bool_)

    def __len__(self) -> int:
        return len(self.timestamps)

    def __iter__(self) -> Iterator[TraceEvent]:
        return iter(self.to_events())

    @overload
    def __getitem__(self, index: int) -> TraceEvent: ...

    @overload
    def __getitem__(self, index: slice) -> TrafficTrace: ...

    def __getitem__(self, index: int | slice) -> TraceEvent | TrafficTrace:
        if isinstance(index, slice):
            return TrafficTrace(self.timestamps[index], self.directions[index], self.frame_lengths[index])
        return TraceEvent(
            timestamp=float(self.timestamps[index]),
            direction=Direction.OUTBOUND if self.directions[index] == 0 else Direction.INBOUND,
            frame_length=int(self.frame_lengths[index]),
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TrafficTrace):
            return bool(
                np.array_equal(self.timestamps, other.timestamps)
                and np.array_equal(self.directions, other.directions)
                and np.array_equal(self.frame_lengths, other.frame_lengths)
            )
        if isinstance(other, tuple):
            return self.to_events() == cast(tuple[TraceEvent, ...], other)
        return False


def _validated_events(events: Iterable[TraceEvent], *, minimum_events: int, trace_name: str) -> tuple[TraceEvent, ...]:
    """Return one complete canonical trace after validating its research preconditions."""
    corrective_action = f"provide finite nondecreasing canonical {trace_name} events"
    try:
        trace = tuple(events)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {trace_name} trace: events must be an iterable of canonical events",
            corrective_action=corrective_action,
        ) from error

    if len(trace) < minimum_events:
        minimum_label = "one" if minimum_events == 1 else "two"
        raise TrafficlabError(
            f"invalid {trace_name} trace: at least {minimum_label} event{'s' if minimum_events != 1 else ''} are required",
            corrective_action=corrective_action,
        )

    previous_timestamp: float | None = None
    for event in trace:
        if type(event) is not TraceEvent:
            raise TrafficlabError(
                f"invalid {trace_name} trace: every event must be a TraceEvent",
                corrective_action=corrective_action,
            )
        try:
            timestamp = event.timestamp
            direction = event.direction
            frame_length = event.frame_length
        except AttributeError as error:
            raise TrafficlabError(
                f"invalid {trace_name} trace: event data is incomplete",
                corrective_action=corrective_action,
            ) from error
        if type(timestamp) is not float or not math.isfinite(timestamp) or timestamp < 0.0:
            raise TrafficlabError(
                f"invalid {trace_name} trace: timestamps must be finite nonnegative floats",
                corrective_action=corrective_action,
            )
        if type(direction) is not Direction:
            raise TrafficlabError(
                f"invalid {trace_name} trace: every event direction must be a Direction member",
                corrective_action=corrective_action,
            )
        if type(frame_length) is not int or frame_length <= 0:
            raise TrafficlabError(
                f"invalid {trace_name} trace: frame lengths must be positive integers",
                corrective_action=corrective_action,
            )
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise TrafficlabError(
                f"invalid {trace_name} trace: timestamps must be nondecreasing",
                corrective_action=corrective_action,
            )
        previous_timestamp = timestamp

    return trace


def normalize_reference(events: Iterable[TraceEvent] | TrafficTrace) -> tuple[TrafficTrace, float]:
    """Normalize a complete reference trace to its closed observation window."""
    if type(events) is TrafficTrace:
        trace = events
        if len(trace) < 2:
            raise TrafficlabError(
                "invalid reference trace: at least two events are required",
                corrective_action="provide finite nondecreasing canonical reference events",
            )
    else:
        reference = _validated_events(events, minimum_events=2, trace_name="reference")
        trace = TrafficTrace.from_events(reference)
    start = trace.timestamps[0]
    window = float(trace.timestamps[-1] - start)
    if not math.isfinite(window) or window <= 0.0:
        raise TrafficlabError(
            "invalid reference observation window: it must be finite and positive",
            corrective_action="provide finite nondecreasing canonical reference events",
        )
    return TrafficTrace(trace.timestamps - start, trace.directions, trace.frame_lengths), window


def align_generated(events: Iterable[TraceEvent] | TrafficTrace, W: float) -> TrafficTrace:
    """Shift a complete generated trace and retain its events in the closed window."""
    if type(events) is TrafficTrace:
        trace = events
        if not len(trace):
            raise TrafficlabError(
                "invalid generated trace: at least one event is required",
                corrective_action="provide finite nondecreasing canonical generated events",
            )
    else:
        generated = _validated_events(events, minimum_events=1, trace_name="generated")
        trace = TrafficTrace.from_events(generated)
    if type(W) is not float or not math.isfinite(W) or W <= 0.0:
        raise TrafficlabError(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive observation window",
        )
    shifted_timestamps = trace.timestamps - trace.timestamps[0]
    mask = shifted_timestamps <= W
    return TrafficTrace(shifted_timestamps[mask], trace.directions[mask], trace.frame_lengths[mask])


class CaptureMetadata(BaseModel):
    """Strict metadata required to classify frames for one Ethernet capture."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)

    interface: Literal["eth0"]
    target_mac: StrictStr

    @field_validator("target_mac", mode="before")
    @classmethod
    def target_mac_is_a_nonzero_unicast_mac(cls, value: Any) -> Any:
        """Normalize one colon-separated MAC while rejecting ambiguous endpoints."""
        if not isinstance(value, str):
            return value
        if _MAC_PATTERN.fullmatch(value) is None:
            raise ValueError("target_mac must be a six-octet colon-separated MAC address")
        normalized = value.lower()
        if normalized == "00:00:00:00:00:00":
            raise ValueError("target_mac must not be the all-zero MAC address")
        if int(normalized[:2], 16) & 1:
            raise ValueError("target_mac must be a unicast MAC address")
        return normalized


def _format_validation_errors(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}" for detail in error.errors())


def parse_capture_metadata(content: bytes, *, source: Path) -> CaptureMetadata:
    """Parse exact capture metadata bytes while retaining their source in errors."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TrafficlabError(
            f"capture metadata {source} is not valid UTF-8: {error}",
            corrective_action="save capture.json as valid UTF-8 and retry",
        ) from error

    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise TrafficlabError(
            f"invalid JSON in capture metadata {source}: {error}",
            corrective_action="correct capture.json JSON and retry",
        ) from error

    try:
        return CaptureMetadata.model_validate(document)
    except ValidationError as error:
        raise TrafficlabError(
            f"invalid capture metadata {source}: {_format_validation_errors(error)}",
            corrective_action="correct capture.json and retry",
        ) from error


def load_capture_metadata(path: Path) -> CaptureMetadata:
    """Load strict UTF-8 JSON metadata from a capture artifact."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read capture metadata {path}: {error}",
            corrective_action="verify capture.json exists and is readable",
        ) from error
    return parse_capture_metadata(content, source=path)


def render_capture_metadata(metadata: CaptureMetadata) -> bytes:
    """Render metadata as one deterministic, human-readable UTF-8 JSON document."""
    document = {"interface": metadata.interface, "target_mac": metadata.target_mac}
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def deterministic_peer_mac(target_mac: str) -> str:
    """Return the generated Ethernet peer MAC, avoiding the target collision."""
    normalized_target = CaptureMetadata(interface="eth0", target_mac=target_mac).target_mac
    if normalized_target == "02:00:00:00:00:01":
        return "02:00:00:00:00:02"
    return "02:00:00:00:00:01"
