"""Typed production Scapy boundary for PCAPNG packet I/O."""

from __future__ import annotations

import importlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Protocol, Self, SupportsFloat, cast

from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace

_UINT32_MAX = 2**32 - 1
_MALFORMED_ACTION = "replace the PCAPNG with a complete valid Ethernet capture"
_DEADLINE_ACTION = "increase the total run timeout and retry capture"

type _ReaderInput = str | BinaryIO


@dataclass(frozen=True, slots=True)
class PcapngPacket:
    """One canonical event paired with its captured Ethernet bytes."""

    event: TraceEvent
    ethernet_frame: bytes


class _ScapyPacket(Protocol):
    time: SupportsFloat
    wirelen: int

    def __bytes__(self) -> bytes: ...


class _ScapyReader(Protocol):
    interfaces: list[tuple[int, int, dict[str, object]]]

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object: ...

    def read_packet(self, size: int = 65_535) -> _ScapyPacket: ...


class _ScapyReaderFactory(Protocol):
    def __call__(self, filename: _ReaderInput) -> _ScapyReader: ...


def _reader_boundary() -> tuple[_ScapyReaderFactory, type[SupportsFloat]]:
    utils = importlib.import_module("scapy.utils")
    return cast(_ScapyReaderFactory, utils.PcapNgReader), cast(type[SupportsFloat], utils.EDecimal)


def _deadline_expired(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() >= deadline:
        raise DeadlineExceededError(
            "Scapy PCAPNG parsing exceeded the total-run deadline",
            corrective_action=_DEADLINE_ACTION,
        )


def _validate_reader(reader: _ScapyReader) -> None:
    if len(reader.interfaces) != 1:
        raise TrafficlabError(
            f"invalid PCAPNG: Scapy observed {len(reader.interfaces)} interfaces; expected exactly one",
            corrective_action=_MALFORMED_ACTION,
        )
    linktype = reader.interfaces[0][0]
    if linktype != 1:
        raise TrafficlabError(
            f"invalid PCAPNG: unsupported link type {linktype}; expected Ethernet link type 1",
            corrective_action=_MALFORMED_ACTION,
        )


def _read_scapy_packets(
    source_input: _ReaderInput,
    metadata: CaptureMetadata,
    *,
    source: Path,
    deadline: float | None,
    clock: Callable[[], float],
    reader_factory: _ScapyReaderFactory,
    timestamp_type: type[SupportsFloat],
) -> tuple[PcapngPacket, ...]:
    _deadline_expired(deadline, clock)
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    packets: list[PcapngPacket] = []
    previous_timestamp: float | None = None
    try:
        with reader_factory(source_input) as reader:
            _deadline_expired(deadline, clock)
            while True:
                try:
                    packet = reader.read_packet(size=_UINT32_MAX)
                except EOFError:
                    break
                _validate_reader(reader)
                frame = bytes(packet)
                if len(frame) < 14:
                    raise TrafficlabError(
                        f"invalid PCAPNG: captured Ethernet frame length must be at least 14, got {len(frame)}",
                        corrective_action=_MALFORMED_ACTION,
                    )
                if not isinstance(packet.time, timestamp_type):
                    raise TrafficlabError(
                        "invalid PCAPNG: Scapy packet record has no explicit timestamp",
                        corrective_action=_MALFORMED_ACTION,
                    )
                timestamp = float(packet.time)
                if not math.isfinite(timestamp) or timestamp < 0.0:
                    raise TrafficlabError(
                        "invalid PCAPNG: Scapy packet timestamp must be finite and nonnegative",
                        corrective_action=_MALFORMED_ACTION,
                    )
                if previous_timestamp is not None and timestamp < previous_timestamp:
                    raise TrafficlabError(
                        "invalid PCAPNG: Scapy packet timestamps must be nondecreasing",
                        corrective_action=_MALFORMED_ACTION,
                    )
                direction = Direction.OUTBOUND if frame[6:12] == target else Direction.INBOUND
                event = TraceEvent(timestamp, direction, len(frame))
                packets.append(PcapngPacket(event=event, ethernet_frame=frame))
                previous_timestamp = timestamp
                _deadline_expired(deadline, clock)
            _validate_reader(reader)
            _deadline_expired(deadline, clock)
    except (DeadlineExceededError, TrafficlabError):
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not read PCAPNG {source}: {error}",
            corrective_action="verify the PCAPNG exists and is readable",
        ) from error
    except Exception as error:
        raise TrafficlabError(
            f"invalid PCAPNG: Scapy could not decode the capture ({type(error).__name__})",
            corrective_action=_MALFORMED_ACTION,
        ) from error
    if not packets:
        raise TrafficlabError(
            "invalid PCAPNG: Scapy capture has no packet records",
            corrective_action=_MALFORMED_ACTION,
        )
    TrafficTrace.from_events(packet.event for packet in packets)
    _deadline_expired(deadline, clock)
    return tuple(packets)


def read_pcapng_packets(
    source_input: Path | BinaryIO,
    metadata: CaptureMetadata,
    *,
    source: Path,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[PcapngPacket, ...]:
    """Read exact PCAPNG packet observations through Scapy."""
    reader_factory, timestamp_type = _reader_boundary()
    resolved_input: _ReaderInput = str(source_input) if isinstance(source_input, Path) else source_input
    return _read_scapy_packets(
        resolved_input,
        metadata,
        source=source,
        deadline=deadline,
        clock=clock,
        reader_factory=reader_factory,
        timestamp_type=timestamp_type,
    )


def read_pcapng_bytes(
    content: bytes,
    metadata: CaptureMetadata,
    *,
    source: Path,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> TrafficTrace:
    """Read owned PCAPNG bytes into a columnar TrafficTrace."""
    if type(content) is not bytes:
        raise TypeError("PCAPNG content must be bytes")
    packets = read_pcapng_packets(BytesIO(content), metadata, source=source, deadline=deadline, clock=clock)
    return TrafficTrace.from_events(packet.event for packet in packets)


def read_pcapng(
    path: Path,
    metadata: CaptureMetadata,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> TrafficTrace:
    """Read one PCAPNG path into a columnar TrafficTrace."""
    packets = read_pcapng_packets(path, metadata, source=path, deadline=deadline, clock=clock)
    return TrafficTrace.from_events(packet.event for packet in packets)
