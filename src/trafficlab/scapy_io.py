"""Typed production Scapy boundary for PCAPNG packet I/O."""

from __future__ import annotations

import importlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import BinaryIO, Protocol, Self, SupportsFloat, cast

from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, deterministic_peer_mac

_UINT32_MAX = 2**32 - 1
_MALFORMED_ACTION = "replace the PCAPNG with a complete valid Ethernet capture"
_DEADLINE_ACTION = "increase the total run timeout and retry capture"

type _ReaderInput = str | BinaryIO


@dataclass(frozen=True, slots=True)
class PcapngPacket:
    """One canonical event paired with its captured Ethernet bytes."""

    event: TraceEvent
    ethernet_frame: bytes


@dataclass(frozen=True, slots=True)
class EncodedPcapng:
    """Exact Scapy output and the trace reparsed from those bytes."""

    content: bytes
    trace: TrafficTrace


@dataclass(frozen=True, slots=True)
class _DecodedPcapng:
    packets: tuple[PcapngPacket, ...]
    trace: TrafficTrace


class _ScapyPacket(Protocol):
    time: SupportsFloat
    wirelen: int

    def __bytes__(self) -> bytes: ...


class _ScapyReader(Protocol):
    interfaces: list[tuple[int, int, dict[str, object]]]
    blocktypes: dict[int, Callable[[bytes, int], object]]

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object: ...

    def read_packet(self, size: int = 65_535) -> _ScapyPacket: ...


class _ScapyReaderFactory(Protocol):
    def __call__(self, filename: _ReaderInput) -> _ScapyReader: ...


class _ScapyWriter(Protocol):
    linktype: int

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object: ...

    def write_header(self, packet: bytes) -> None: ...

    def write_packet(
        self,
        packet: bytes,
        sec: object | None = None,
        usec: int | None = None,
        caplen: int | None = None,
        wirelen: int | None = None,
    ) -> None: ...


class _ScapyWriterFactory(Protocol):
    def __call__(self, filename: str) -> _ScapyWriter: ...


class _TimestampFactory(Protocol):
    def __call__(self, value: str) -> object: ...


def _reader_boundary() -> tuple[_ScapyReaderFactory, type[SupportsFloat]]:
    utils = importlib.import_module("scapy.utils")
    return cast(_ScapyReaderFactory, utils.PcapNgReader), cast(type[SupportsFloat], utils.EDecimal)


def _writer_boundary() -> tuple[_ScapyWriterFactory, _TimestampFactory]:
    utils = importlib.import_module("scapy.utils")
    return (
        cast(_ScapyWriterFactory, utils.PcapNgWriter),
        cast(_TimestampFactory, utils.EDecimal),
    )


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


def _reject_non_enhanced_packet_block(_block: bytes, _size: int) -> object:
    raise TrafficlabError(
        "invalid PCAPNG: only Enhanced Packet Blocks are accepted; obsolete and Simple Packet Blocks are forbidden",
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
) -> _DecodedPcapng:
    _deadline_expired(deadline, clock)
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    packets: list[PcapngPacket] = []
    previous_timestamp: float | None = None
    try:
        with reader_factory(source_input) as reader:
            reader.blocktypes[2] = _reject_non_enhanced_packet_block
            reader.blocktypes[3] = _reject_non_enhanced_packet_block
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
    materialized_packets = tuple(packets)
    trace = TrafficTrace.from_events(packet.event for packet in materialized_packets)
    _deadline_expired(deadline, clock)
    return _DecodedPcapng(packets=materialized_packets, trace=trace)


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
    ).packets


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
    reader_factory, timestamp_type = _reader_boundary()
    return _read_scapy_packets(
        BytesIO(content),
        metadata,
        source=source,
        deadline=deadline,
        clock=clock,
        reader_factory=reader_factory,
        timestamp_type=timestamp_type,
    ).trace


def read_pcapng(
    path: Path,
    metadata: CaptureMetadata,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> TrafficTrace:
    """Read one PCAPNG path into a columnar TrafficTrace."""
    reader_factory, timestamp_type = _reader_boundary()
    return _read_scapy_packets(
        str(path),
        metadata,
        source=path,
        deadline=deadline,
        clock=clock,
        reader_factory=reader_factory,
        timestamp_type=timestamp_type,
    ).trace


def _validate_encoding_input(trace: TrafficTrace, observation_window_seconds: float) -> None:
    if type(trace) is not TrafficTrace:
        raise TypeError("trace must be a TrafficTrace")
    if (
        type(observation_window_seconds) is not float
        or not math.isfinite(observation_window_seconds)
        or observation_window_seconds <= 0.0
    ):
        raise TrafficlabError(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive observation window",
        )
    if not len(trace):
        raise TrafficlabError(
            "cannot encode an empty traffic trace",
            corrective_action="generate at least one complete Ethernet frame",
        )
    if float(trace.timestamps[-1]) > observation_window_seconds:
        raise TrafficlabError(
            "traffic trace contains a timestamp outside the closed observation window",
            corrective_action="retain only packets inside the closed observation window and retry",
        )
    if any(event.frame_length < 14 for event in trace):
        minimum = min(event.frame_length for event in trace)
        raise TrafficlabError(
            f"Ethernet frame length must be at least 14, got {minimum}",
            corrective_action="generate complete Ethernet frame lengths and retry",
        )


def _frame_for_event(event: TraceEvent, metadata: CaptureMetadata) -> bytes:
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    peer = bytes.fromhex(deterministic_peer_mac(metadata.target_mac).replace(":", ""))
    destination, source = (peer, target) if event.direction is Direction.OUTBOUND else (target, peer)
    return destination + source + b"\x08\x00" + b"\x00" * (event.frame_length - 14)


def _write_scapy_path(
    path: Path,
    trace: TrafficTrace,
    metadata: CaptureMetadata,
    *,
    writer_factory: _ScapyWriterFactory,
    timestamp_factory: _TimestampFactory,
) -> None:
    try:
        with writer_factory(str(path)) as writer:
            writer.linktype = 1
            for index, event in enumerate(trace):
                packet = _frame_for_event(event, metadata)
                if index == 0:
                    writer.write_header(packet)
                writer.write_packet(
                    packet,
                    sec=timestamp_factory(_microsecond_text(event.timestamp)),
                    caplen=event.frame_length,
                    wirelen=event.frame_length,
                )
    except OSError as error:
        raise TrafficlabError(
            f"could not write PCAPNG {path}: {error}",
            corrective_action="verify the PCAPNG destination is writable",
        ) from error
    except Exception as error:
        raise TrafficlabError(
            f"Scapy could not encode the validated Ethernet trace ({type(error).__name__})",
            corrective_action="report the Scapy PCAPNG writer defect",
        ) from error


def _microsecond_text(timestamp: float) -> str:
    scaled = timestamp * 1_000_000
    nearest = round(scaled)
    tolerance = 4 * math.ulp(scaled)
    ticks = nearest if abs(scaled - nearest) <= tolerance else math.floor(scaled)
    seconds, microseconds = divmod(ticks, 1_000_000)
    return f"{seconds}.{microseconds:06d}"


def encode_pcapng(
    trace: TrafficTrace,
    metadata: CaptureMetadata,
    *,
    observation_window_seconds: float,
) -> EncodedPcapng:
    """Encode through Scapy and return only reparsed emitted output."""
    _validate_encoding_input(trace, observation_window_seconds)
    writer_factory, timestamp_factory = _writer_boundary()
    with TemporaryDirectory(prefix="trafficlab-scapy-write-") as temporary:
        path = Path(temporary) / "generated.pcapng"
        _write_scapy_path(
            path,
            trace,
            metadata,
            writer_factory=writer_factory,
            timestamp_factory=timestamp_factory,
        )
        try:
            content = path.read_bytes()
        except OSError as error:
            raise TrafficlabError(
                f"could not read emitted PCAPNG {path}: {error}",
                corrective_action="verify the temporary PCAPNG output is readable",
            ) from error
    if not content:
        raise TrafficlabError(
            "Scapy emitted an empty PCAPNG",
            corrective_action="report the Scapy PCAPNG writer defect",
        )
    reparsed = read_pcapng_bytes(content, metadata, source=Path("generated.pcapng"))
    if reparsed.directions.tolist() != trace.directions.tolist():
        raise TrafficlabError(
            "Scapy PCAPNG changed packet directions",
            corrective_action="report the Scapy PCAPNG writer defect",
        )
    if reparsed.frame_lengths.tolist() != trace.frame_lengths.tolist():
        raise TrafficlabError(
            "Scapy PCAPNG changed frame lengths",
            corrective_action="report the Scapy PCAPNG writer defect",
        )
    if float(reparsed.timestamps[-1]) > observation_window_seconds:
        raise TrafficlabError(
            "Scapy PCAPNG contains a timestamp outside the closed observation window",
            corrective_action="use a shorter generated trace and retry",
        )
    return EncodedPcapng(content=content, trace=reparsed)
