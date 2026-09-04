"""Exact raw-frame normalization for imported PCAP and PCAPNG captures."""

from __future__ import annotations

import importlib
import struct
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryFile
from time import monotonic
from typing import BinaryIO, Literal, Protocol, Self, cast

from trafficlab.common.errors import DeadlineExceededError, TrafficlabError

_UINT32_MAX = 2**32 - 1
_UINT64_MAX = 2**64 - 1
_MALFORMED_ACTION = "replace the source with a complete valid Ethernet PCAP or PCAPNG capture"
_DEADLINE_ACTION = "increase capture.total_timeout_seconds and retry import-run"
_READ_ACTION = "verify the source capture exists and is readable"
_WRITE_ACTION = "verify free space and permissions for the run directory, then retry import-run"

type RawCaptureFormat = Literal["pcap", "pcapng"]


@dataclass(frozen=True, slots=True)
class RawNormalizationResult:
    """Facts about one canonicalized raw capture."""

    input_format: RawCaptureFormat
    packet_count: int
    observation_window_seconds: float
    reordered: bool


@dataclass(frozen=True, slots=True)
class _RawPacketIndex:
    timestamp: Fraction
    ordinal: int
    offset: int
    captured_length: int
    wire_length: int


class _PcapMetadata(Protocol):
    @property
    def sec(self) -> int: ...

    @property
    def usec(self) -> int: ...

    @property
    def wirelen(self) -> int: ...

    @property
    def caplen(self) -> int: ...


class _PcapngMetadata(Protocol):
    @property
    def linktype(self) -> int: ...

    @property
    def tsresol(self) -> int: ...

    @property
    def tshigh(self) -> int | None: ...

    @property
    def tslow(self) -> int | None: ...

    @property
    def wirelen(self) -> int: ...


class _RawReader(Protocol):
    linktype: int
    nano: bool
    f: BinaryIO

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object: ...

    def _read_packet(self, size: int) -> tuple[bytes, object]: ...


class _RawReaderFactory(Protocol):
    def __call__(self, filename: str) -> _RawReader: ...


class _RawWriter(Protocol):
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


class _RawWriterFactory(Protocol):
    def __call__(self, filename: str) -> _RawWriter: ...


class _TimestampFactory(Protocol):
    def __call__(self, value: str) -> object: ...


def _reader_boundary(input_format: RawCaptureFormat) -> _RawReaderFactory:
    utils = importlib.import_module("scapy.utils")
    reader = utils.RawPcapReader if input_format == "pcap" else utils.RawPcapNgReader
    return cast(_RawReaderFactory, reader)


def _writer_boundary() -> tuple[_RawWriterFactory, _TimestampFactory]:
    utils = importlib.import_module("scapy.utils")
    return cast(_RawWriterFactory, utils.PcapNgWriter), cast(_TimestampFactory, utils.EDecimal)


def _check_deadline(deadline: float | None, clock: Callable[[], float], boundary: str) -> None:
    if deadline is not None and clock() >= deadline:
        raise DeadlineExceededError(
            f"raw capture normalization exceeded its absolute deadline {boundary}",
            corrective_action=_DEADLINE_ACTION,
        )


def _detect_format(source: Path) -> RawCaptureFormat:
    try:
        with source.open("rb") as stream:
            magic = stream.read(4)
    except OSError as error:
        raise TrafficlabError(
            f"could not read raw capture {source}: {error}",
            corrective_action=_READ_ACTION,
        ) from error
    if magic in {
        b"\xa1\xb2\xc3\xd4",
        b"\xd4\xc3\xb2\xa1",
        b"\xa1\xb2\x3c\x4d",
        b"\x4d\x3c\xb2\xa1",
    }:
        return "pcap"
    if magic == b"\x0a\x0d\x0d\x0a":
        return "pcapng"
    raise TrafficlabError(
        f"unsupported raw capture magic {magic.hex() or '<empty>'}; expected classic PCAP or PCAPNG",
        corrective_action=_MALFORMED_ACTION,
    )


def _invalid_capture(message: str) -> TrafficlabError:
    return TrafficlabError(f"invalid raw capture: {message}", corrective_action=_MALFORMED_ACTION)


def _validate_pcapng_structure(
    source: Path,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> None:
    """Reject structural truncation Scapy otherwise reports as an ordinary EOF."""
    try:
        size = source.stat().st_size
        with source.open("rb") as stream:
            endian: Literal["<", ">"] | None = None
            offset = 0
            while offset < size:
                stream.seek(offset)
                header = stream.read(8)
                if len(header) != 8:
                    raise _invalid_capture("truncated PCAPNG block header")
                raw_type, raw_length = header[:4], header[4:]
                if raw_type == b"\x0a\x0d\x0d\x0a":
                    byte_order = stream.read(4)
                    if byte_order == b"\x4d\x3c\x2b\x1a":
                        endian = "<"
                    elif byte_order == b"\x1a\x2b\x3c\x4d":
                        endian = ">"
                    else:
                        raise _invalid_capture("invalid PCAPNG section byte-order magic")
                    block_type = 0x0A0D0D0A
                else:
                    if endian is None:
                        raise _invalid_capture("first PCAPNG block must be a section header")
                    block_type = struct.unpack(f"{endian}I", raw_type)[0]
                block_length = struct.unpack(f"{endian}I", raw_length)[0]
                minimum_length = 28 if block_type == 0x0A0D0D0A else 12
                if block_length < minimum_length or block_length % 4:
                    raise _invalid_capture(f"invalid PCAPNG block length {block_length}")
                if block_length > size - offset:
                    raise _invalid_capture("truncated PCAPNG block body")
                stream.seek(offset + block_length - 4)
                tail = stream.read(4)
                if len(tail) != 4 or struct.unpack(f"{endian}I", tail)[0] != block_length:
                    raise _invalid_capture("PCAPNG block length trailer does not match its header")
                if block_type in {2, 6}:
                    body_length = block_length - 12
                    if body_length < 20:
                        raise _invalid_capture("PCAPNG packet block is shorter than its fixed header")
                    stream.seek(offset + 20)
                    captured_length = struct.unpack(f"{endian}I", stream.read(4))[0]
                    padded_length = captured_length + (-captured_length % 4)
                    available_length = body_length - 20
                    if padded_length > available_length:
                        raise _invalid_capture(
                            f"PCAPNG captured length {captured_length} exceeds {available_length} available packet bytes"
                        )
                offset += block_length
                _check_deadline(deadline, clock, "after validating a PCAPNG block")
    except TrafficlabError:
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not read raw capture {source}: {error}",
            corrective_action=_READ_ACTION,
        ) from error


def _require_uint32(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= _UINT32_MAX:
        raise TrafficlabError(
            f"invalid raw capture: {field} must be an unsigned 32-bit integer",
            corrective_action=_MALFORMED_ACTION,
        )
    return value


def _pcap_timestamp(metadata: _PcapMetadata, resolution: int) -> Fraction:
    seconds = _require_uint32(metadata.sec, "PCAP timestamp seconds")
    subsecond = _require_uint32(metadata.usec, "PCAP timestamp fraction")
    if subsecond >= resolution:
        raise TrafficlabError(
            f"invalid raw capture: PCAP timestamp fraction must be below {resolution}",
            corrective_action=_MALFORMED_ACTION,
        )
    return Fraction(seconds * resolution + subsecond, resolution)


def _pcapng_timestamp(metadata: _PcapngMetadata) -> Fraction:
    high = _require_uint32(metadata.tshigh, "PCAPNG timestamp high field")
    low = _require_uint32(metadata.tslow, "PCAPNG timestamp low field")
    resolution = metadata.tsresol
    if type(resolution) is not int or resolution <= 0:
        raise TrafficlabError(
            "invalid raw capture: PCAPNG timestamp resolution must be a positive finite integer",
            corrective_action=_MALFORMED_ACTION,
        )
    ticks = (high << 32) | low
    return Fraction(ticks, resolution)


def _microsecond_ticks(timestamp: Fraction) -> int:
    ticks = timestamp.numerator * 1_000_000 // timestamp.denominator
    if not 0 <= ticks <= _UINT64_MAX:
        raise TrafficlabError(
            "invalid raw capture: timestamp exceeds the canonical PCAPNG microsecond range",
            corrective_action=_MALFORMED_ACTION,
        )
    return ticks


def _packet_facts(
    input_format: RawCaptureFormat, reader: _RawReader, frame: bytes, metadata: object
) -> tuple[Fraction, int, int]:
    if input_format == "pcap":
        pcap_metadata = cast(_PcapMetadata, metadata)
        if reader.linktype != 1:
            linktype = reader.linktype
        else:
            linktype = 1
        captured_length = _require_uint32(pcap_metadata.caplen, "PCAP captured length")
        wire_length = _require_uint32(pcap_metadata.wirelen, "PCAP wire length")
        timestamp = _pcap_timestamp(pcap_metadata, 1_000_000_000 if reader.nano else 1_000_000)
    else:
        pcapng_metadata = cast(_PcapngMetadata, metadata)
        linktype = pcapng_metadata.linktype
        captured_length = len(frame)
        wire_length = _require_uint32(pcapng_metadata.wirelen, "PCAPNG wire length")
        timestamp = _pcapng_timestamp(pcapng_metadata)
    if linktype != 1:
        raise TrafficlabError(
            f"invalid raw capture: unsupported link type {linktype}; expected Ethernet link type 1",
            corrective_action=_MALFORMED_ACTION,
        )
    if captured_length != len(frame):
        raise TrafficlabError(
            f"invalid raw capture: captured length {captured_length} does not match {len(frame)} frame bytes",
            corrective_action=_MALFORMED_ACTION,
        )
    if captured_length < 14:
        raise TrafficlabError(
            f"invalid raw capture: Ethernet frame length must be at least 14, got {captured_length}",
            corrective_action=_MALFORMED_ACTION,
        )
    if wire_length < captured_length:
        raise TrafficlabError(
            f"invalid raw capture: wire length {wire_length} is below captured length {captured_length}",
            corrective_action=_MALFORMED_ACTION,
        )
    _microsecond_ticks(timestamp)
    return timestamp, captured_length, wire_length


def _read_to_spool(
    source: Path,
    spool: BinaryIO,
    input_format: RawCaptureFormat,
    *,
    deadline: float | None,
    clock: Callable[[], float],
) -> list[_RawPacketIndex]:
    indexes: list[_RawPacketIndex] = []
    try:
        with _reader_boundary(input_format)(str(source)) as reader:
            while True:
                packet_offset = reader.f.tell() if input_format == "pcap" else None
                try:
                    # Scapy exposes raw bytes only through this intentional raw-reader API.
                    frame, metadata = reader._read_packet(size=_UINT32_MAX)  # pyright: ignore[reportPrivateUsage]
                except EOFError:
                    if packet_offset is not None and reader.f.tell() != packet_offset:
                        raise _invalid_capture("truncated PCAP packet record") from None
                    break
                timestamp, captured_length, wire_length = _packet_facts(input_format, reader, frame, metadata)
                offset = _append_spooled_frame(spool, frame)
                indexes.append(_RawPacketIndex(timestamp, len(indexes), offset, captured_length, wire_length))
                _check_deadline(deadline, clock, "after reading an input packet")
    except (DeadlineExceededError, TrafficlabError):
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not read or spool raw capture {source}: {error}",
            corrective_action=_READ_ACTION,
        ) from error
    except Exception as error:
        raise TrafficlabError(
            f"invalid raw capture: Scapy could not decode the input ({type(error).__name__})",
            corrective_action=_MALFORMED_ACTION,
        ) from error
    return indexes


def _append_spooled_frame(spool: BinaryIO, frame: bytes) -> int:
    try:
        offset = spool.tell()
        written = spool.write(frame)
    except OSError as error:
        raise TrafficlabError(
            f"could not write raw capture normalization spool: {error}",
            corrective_action=_WRITE_ACTION,
        ) from error
    if written != len(frame):
        raise TrafficlabError(
            f"could not write raw capture normalization spool: wrote {written} of {len(frame)} bytes",
            corrective_action=_WRITE_ACTION,
        )
    return offset


def _timestamp_text(ticks: int) -> str:
    seconds, microseconds = divmod(ticks, 1_000_000)
    return f"{seconds}.{microseconds:06d}"


def _read_spooled_frame(spool: BinaryIO, packet: _RawPacketIndex) -> bytes:
    spool.seek(packet.offset)
    frame = spool.read(packet.captured_length)
    if len(frame) != packet.captured_length:
        raise TrafficlabError(
            "raw capture normalization spool returned a short frame",
            corrective_action=_WRITE_ACTION,
        )
    return frame


def _write_canonical_capture(
    destination: Path,
    spool: BinaryIO,
    packets: list[_RawPacketIndex],
    *,
    deadline: float | None,
    clock: Callable[[], float],
) -> None:
    writer_factory, timestamp_factory = _writer_boundary()
    try:
        with writer_factory(str(destination)) as writer:
            writer.linktype = 1
            for index, packet in enumerate(packets):
                frame = _read_spooled_frame(spool, packet)
                if index == 0:
                    writer.write_header(frame)
                writer.write_packet(
                    frame,
                    sec=timestamp_factory(_timestamp_text(_microsecond_ticks(packet.timestamp))),
                    caplen=packet.captured_length,
                    wirelen=packet.wire_length,
                )
                _check_deadline(deadline, clock, "after writing an output packet")
    except (DeadlineExceededError, TrafficlabError):
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not write normalized PCAPNG {destination}: {error}",
            corrective_action=_WRITE_ACTION,
        ) from error
    except Exception as error:
        raise TrafficlabError(
            f"Scapy could not encode the normalized PCAPNG ({type(error).__name__})",
            corrective_action=_WRITE_ACTION,
        ) from error


def normalize_raw_capture(
    source: Path,
    destination: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
) -> RawNormalizationResult:
    """Normalize one raw Ethernet capture to canonical microsecond PCAPNG."""
    _check_deadline(deadline, clock, "before reading the input")
    input_format = _detect_format(source)
    if input_format == "pcapng":
        _validate_pcapng_structure(source, deadline=deadline, clock=clock)
    try:
        with TemporaryFile(mode="w+b", dir=destination.parent) as spool:
            packets = _read_to_spool(source, spool, input_format, deadline=deadline, clock=clock)
            _check_deadline(deadline, clock, "before sorting the packet index")
            ordered = sorted(packets, key=lambda packet: (packet.timestamp, packet.ordinal))
            _check_deadline(deadline, clock, "after sorting the packet index")
            if len(ordered) < 2:
                raise TrafficlabError(
                    f"invalid raw capture: expected at least two packets, got {len(ordered)}",
                    corrective_action=_MALFORMED_ACTION,
                )
            first_tick = _microsecond_ticks(ordered[0].timestamp)
            last_tick = _microsecond_ticks(ordered[-1].timestamp)
            if last_tick <= first_tick:
                raise TrafficlabError(
                    "invalid raw capture: canonical observation window must be positive",
                    corrective_action=_MALFORMED_ACTION,
                )
            _write_canonical_capture(destination, spool, ordered, deadline=deadline, clock=clock)
        _check_deadline(deadline, clock, "after closing the output")
    except (DeadlineExceededError, TrafficlabError):
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not create normalization spool beside {destination}: {error}",
            corrective_action=_WRITE_ACTION,
        ) from error
    return RawNormalizationResult(
        input_format=input_format,
        packet_count=len(ordered),
        observation_window_seconds=(last_tick - first_tick) / 1_000_000,
        reordered=any(packet.ordinal != position for position, packet in enumerate(ordered)),
    )
