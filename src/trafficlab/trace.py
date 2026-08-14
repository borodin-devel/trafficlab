"""Canonical trace values and strict capture metadata boundaries."""

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

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


def normalize_reference(events: Iterable[TraceEvent]) -> tuple[tuple[TraceEvent, ...], float]:
    """Normalize a complete reference trace to its closed observation window."""
    reference = _validated_events(events, minimum_events=2, trace_name="reference")
    start = reference[0].timestamp
    window = reference[-1].timestamp - start
    if not math.isfinite(window) or window <= 0.0:
        raise TrafficlabError(
            "invalid reference observation window: it must be finite and positive",
            corrective_action="provide finite nondecreasing canonical reference events",
        )
    return (
        tuple(
            TraceEvent(
                timestamp=event.timestamp - start,
                direction=event.direction,
                frame_length=event.frame_length,
            )
            for event in reference
        ),
        window,
    )


def align_generated(events: Iterable[TraceEvent], W: float) -> tuple[TraceEvent, ...]:
    """Shift a complete generated trace and retain its events in the closed window."""
    generated = _validated_events(events, minimum_events=1, trace_name="generated")
    if type(W) is not float or not math.isfinite(W) or W <= 0.0:
        raise TrafficlabError(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive observation window",
        )
    start = generated[0].timestamp
    return tuple(
        TraceEvent(
            timestamp=event.timestamp - start,
            direction=event.direction,
            frame_length=event.frame_length,
        )
        for event in generated
        if event.timestamp - start <= W
    )


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
