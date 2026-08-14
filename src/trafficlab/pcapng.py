"""Streaming parser and deterministic renderer for the supported Ethernet PCAPNG subset."""

import math
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Literal

from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, deterministic_peer_mac

_SECTION_HEADER_BLOCK = 0x0A0D0D0A
_INTERFACE_DESCRIPTION_BLOCK = 1
_OBSOLETE_PACKET_BLOCK = 2
_SIMPLE_PACKET_BLOCK = 3
_ENHANCED_PACKET_BLOCK = 6
_ETHERNET_LINK_TYPE = 1
_IF_TSRESOL_OPTION = 9
_END_OF_OPTIONS = 0
_UINT32_MAX = 2**32 - 1
_UINT64_MAX = 2**64 - 1
_MALFORMED_ACTION = "replace the PCAPNG with a complete valid Ethernet capture"

Endian = Literal["<", ">"]


@dataclass(frozen=True, slots=True)
class PacketObservation:
    """One canonical packet event paired with its exact captured Ethernet bytes."""

    event: TraceEvent
    ethernet_frame: bytes


def _malformed(detail: str) -> TrafficlabError:
    return TrafficlabError(f"invalid PCAPNG: {detail}", corrective_action=_MALFORMED_ACTION)


def _deadline_expired(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() >= deadline:
        raise DeadlineExceededError(
            "PCAPNG parsing exceeded the total-run deadline",
            corrective_action="increase the total run timeout and retry capture",
        )


def _read_exact(stream: BinaryIO, size: int, *, truncated: str) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise _malformed(truncated)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _discard_exact(stream: BinaryIO, size: int, *, truncated: str) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 64 * 1024))
        if not chunk:
            raise _malformed(truncated)
        remaining -= len(chunk)


def _read_chunked(stream: BinaryIO, size: int, *, truncated: str) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 64 * 1024))
        if not chunk:
            raise _malformed(truncated)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_zero_padding(stream: BinaryIO, size: int, *, context: str, truncated: str) -> None:
    padding = _read_exact(stream, size, truncated=truncated)
    if any(padding):
        raise _malformed(f"{context} must be zero-filled")


def _validate_block_length(total_length: int, *, minimum: int = 12) -> None:
    if total_length < minimum:
        raise _malformed(f"block length {total_length} is below the minimum {minimum}")
    if total_length % 4:
        raise _malformed(f"block length {total_length} is not four-byte aligned")


def _finish_block(stream: BinaryIO, endian: Endian, total_length: int) -> None:
    trailer = _read_exact(stream, 4, truncated="truncated block body")
    trailing_length = struct.unpack(f"{endian}I", trailer)[0]
    if trailing_length != total_length:
        raise _malformed(f"trailing block length {trailing_length} does not match leading block length {total_length}")


def _consume_options(
    stream: BinaryIO,
    byte_count: int,
    endian: Endian,
    *,
    context: str,
    read_tsresol: bool = False,
) -> int | None:
    remaining = byte_count
    timestamp_resolution: int | None = None
    while remaining:
        if remaining < 4:
            raise _malformed(f"truncated {context} options")
        option_header = _read_exact(stream, 4, truncated=f"truncated {context} options")
        option_code, option_length = struct.unpack(f"{endian}HH", option_header)
        remaining -= 4
        if option_code == _END_OF_OPTIONS:
            if option_length != 0 or remaining != 0:
                raise _malformed(f"invalid {context} end-of-options marker")
            return timestamp_resolution

        padded_length = option_length + (-option_length % 4)
        if padded_length > remaining:
            raise _malformed(f"truncated {context} options")
        value = _read_exact(stream, option_length, truncated=f"truncated {context} options")
        _read_zero_padding(
            stream,
            padded_length - option_length,
            context=f"{context} option padding",
            truncated=f"truncated {context} options",
        )
        remaining -= padded_length

        if read_tsresol and option_code == _IF_TSRESOL_OPTION:
            if option_length != 1 or timestamp_resolution is not None:
                raise _malformed("invalid or duplicate if_tsresol option")
            timestamp_resolution = value[0]

    return timestamp_resolution


def _read_section_header(stream: BinaryIO) -> Endian:
    prefix = _read_exact(stream, 12, truncated="truncated Section Header Block")
    if prefix[:4] != b"\x0a\x0d\x0d\x0a":
        raise _malformed("the first block is not a Section Header Block")

    magic = prefix[8:12]
    if magic == b"\x4d\x3c\x2b\x1a":
        endian: Endian = "<"
    elif magic == b"\x1a\x2b\x3c\x4d":
        endian = ">"
    else:
        raise _malformed("Section Header Block has invalid byte-order magic")

    total_length = struct.unpack(f"{endian}I", prefix[4:8])[0]
    _validate_block_length(total_length, minimum=28)
    fixed_body = _read_exact(stream, 12, truncated="truncated Section Header Block")
    major_version, minor_version, _section_length = struct.unpack(f"{endian}HHq", fixed_body)
    if major_version != 1 or minor_version not in (0, 2):
        raise _malformed(f"unsupported PCAPNG version {major_version}.{minor_version}")
    options_length = total_length - 28
    _consume_options(stream, options_length, endian, context="SHB")
    _finish_block(stream, endian, total_length)
    return endian


def _timestamp_seconds(timestamp_ticks: int, encoded_resolution: int | None) -> float:
    if encoded_resolution is None:
        return timestamp_ticks / 1_000_000
    if encoded_resolution & 0x80:
        return timestamp_ticks / 2 ** (encoded_resolution & 0x7F)
    return timestamp_ticks / 10**encoded_resolution


def _parse_interface_description(
    stream: BinaryIO,
    endian: Endian,
    total_length: int,
) -> tuple[int, int | None]:
    _validate_block_length(total_length, minimum=20)
    fixed_body = _read_exact(stream, 8, truncated="truncated block body")
    link_type, _reserved, snap_len = struct.unpack(f"{endian}HHI", fixed_body)
    if link_type != _ETHERNET_LINK_TYPE:
        raise _malformed(f"unsupported link type {link_type}; expected Ethernet link type 1")
    encoded_resolution = _consume_options(
        stream,
        total_length - 20,
        endian,
        context="IDB",
        read_tsresol=True,
    )
    _finish_block(stream, endian, total_length)
    return snap_len, encoded_resolution


def _parse_enhanced_packet(
    stream: BinaryIO,
    endian: Endian,
    total_length: int,
    *,
    snap_len: int,
    timestamp_resolution: int | None,
    target_mac: bytes,
    previous_ticks: int | None,
) -> tuple[PacketObservation, int]:
    _validate_block_length(total_length, minimum=32)
    fixed_body = _read_exact(stream, 20, truncated="truncated block body")
    interface_id, timestamp_high, timestamp_low, captured_length, original_length = struct.unpack(
        f"{endian}IIIII", fixed_body
    )
    if interface_id != 0:
        raise _malformed(f"Enhanced Packet Block references unsupported interface ID {interface_id}")
    if captured_length < 14:
        raise _malformed(f"captured Ethernet frame length must be at least 14, got {captured_length}")
    if captured_length > original_length:
        raise _malformed(f"captured length {captured_length} exceeds original length {original_length}")
    if snap_len and captured_length > snap_len:
        raise _malformed(f"captured length {captured_length} exceeds Interface SnapLen {snap_len}")

    padded_frame_length = captured_length + (-captured_length % 4)
    option_length = total_length - 32 - padded_frame_length
    if option_length < 0:
        raise _malformed("truncated Ethernet frame in Enhanced Packet Block")

    ethernet_frame = _read_chunked(
        stream,
        captured_length,
        truncated="truncated Ethernet frame in Enhanced Packet Block",
    )
    _read_zero_padding(
        stream,
        padded_frame_length - captured_length,
        context="EPB packet padding",
        truncated="truncated Ethernet frame padding in Enhanced Packet Block",
    )
    _consume_options(stream, option_length, endian, context="EPB")
    _finish_block(stream, endian, total_length)

    timestamp_ticks = (timestamp_high << 32) | timestamp_low
    if previous_ticks is not None and timestamp_ticks < previous_ticks:
        raise _malformed("Enhanced Packet Block timestamps must be nondecreasing")
    source_mac = ethernet_frame[6:12]
    direction = Direction.OUTBOUND if source_mac == target_mac else Direction.INBOUND
    event = TraceEvent(
        timestamp=_timestamp_seconds(timestamp_ticks, timestamp_resolution),
        direction=direction,
        frame_length=captured_length,
    )
    return PacketObservation(event=event, ethernet_frame=ethernet_frame), timestamp_ticks


def _read_block_header(stream: BinaryIO, endian: Endian) -> tuple[int, int] | None:
    header = stream.read(8)
    if not header:
        return None
    if len(header) != 8:
        raise _malformed("truncated block header")
    block_type, total_length = struct.unpack(f"{endian}II", header)
    _validate_block_length(total_length)
    return block_type, total_length


def _parse_pcapng_stream(
    stream: BinaryIO,
    metadata: CaptureMetadata,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[PacketObservation, ...]:
    """Parse the supported Ethernet PCAPNG subset from one positioned stream."""
    _deadline_expired(deadline, clock)
    packets: list[PacketObservation] = []
    endian = _read_section_header(stream)
    interface: tuple[int, int | None] | None = None
    previous_ticks: int | None = None
    while (header := _read_block_header(stream, endian)) is not None:
        block_type, total_length = header
        if block_type == _SECTION_HEADER_BLOCK:
            raise _malformed("multiple sections are not supported")
        if block_type == _INTERFACE_DESCRIPTION_BLOCK:
            if interface is not None:
                raise _malformed("multiple interfaces are not supported")
            interface = _parse_interface_description(stream, endian, total_length)
            continue
        if block_type == _SIMPLE_PACKET_BLOCK:
            raise _malformed("Simple Packet Block input is not supported")
        if block_type == _OBSOLETE_PACKET_BLOCK:
            raise _malformed("obsolete Packet Block input is not supported")
        if block_type == _ENHANCED_PACKET_BLOCK:
            if interface is None:
                raise _malformed("Enhanced Packet Block appears before an Interface Description Block")
            snap_len, timestamp_resolution = interface
            packet, previous_ticks = _parse_enhanced_packet(
                stream,
                endian,
                total_length,
                snap_len=snap_len,
                timestamp_resolution=timestamp_resolution,
                target_mac=bytes.fromhex(metadata.target_mac.replace(":", "")),
                previous_ticks=previous_ticks,
            )
            packets.append(packet)
            _deadline_expired(deadline, clock)
            continue

        _discard_exact(stream, total_length - 12, truncated="truncated block body")
        _finish_block(stream, endian, total_length)

    if interface is None:
        raise _malformed("capture has no Interface Description Block")
    if not packets:
        raise _malformed("capture has no Enhanced Packet Blocks")
    return tuple(packets)


def parse_pcapng_bytes(
    content: bytes,
    metadata: CaptureMetadata,
    *,
    source: Path,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[TraceEvent, ...]:
    """Parse exact PCAPNG bytes without reopening their source path."""
    del source
    with BytesIO(content) as stream:
        packets = _parse_pcapng_stream(stream, metadata, deadline=deadline, clock=clock)
    return tuple(packet.event for packet in packets)


def parse_pcapng_packets(
    path: Path,
    metadata: CaptureMetadata,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[PacketObservation, ...]:
    """Parse one PCAPNG stream while retaining each exact captured Ethernet frame."""
    _deadline_expired(deadline, clock)
    try:
        with path.open("rb") as stream:
            return _parse_pcapng_stream(stream, metadata, deadline=deadline, clock=clock)
    except OSError as error:
        raise TrafficlabError(
            f"could not read PCAPNG {path}: {error}",
            corrective_action="verify the PCAPNG exists and is readable",
        ) from error


def parse_pcapng(
    path: Path,
    metadata: CaptureMetadata,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[TraceEvent, ...]:
    """Parse one supported Ethernet PCAPNG incrementally from its live file stream."""
    packets = parse_pcapng_packets(path, metadata, deadline=deadline, clock=clock)
    return tuple(packet.event for packet in packets)


def _encode_block(block_type: int, body: bytes) -> bytes:
    total_length = 12 + len(body)
    if total_length > _UINT32_MAX:
        raise TrafficlabError(
            "PCAPNG block length exceeds the 32-bit format limit",
            corrective_action="reduce generated frame lengths and retry",
        )
    return struct.pack("<II", block_type, total_length) + body + struct.pack("<I", total_length)


def _timestamp_nanoseconds(event: TraceEvent) -> int:
    scaled_timestamp = event.timestamp * 1_000_000_000
    if not math.isfinite(scaled_timestamp) or scaled_timestamp > _UINT64_MAX + 0.5:
        raise TrafficlabError(
            "event timestamp exceeds the PCAPNG 64-bit timestamp limit",
            corrective_action="use a shorter observation window and retry",
        )
    timestamp = round(scaled_timestamp)
    if timestamp > _UINT64_MAX:
        raise TrafficlabError(
            "event timestamp exceeds the PCAPNG 64-bit timestamp limit",
            corrective_action="use a shorter observation window and retry",
        )
    return timestamp


def _encode_frame(event: TraceEvent, target_mac: bytes, peer_mac: bytes) -> bytes:
    if event.frame_length < 14:
        raise TrafficlabError(
            f"Ethernet frame length must be at least 14, got {event.frame_length}",
            corrective_action="generate complete Ethernet frame lengths and retry",
        )
    if event.frame_length > _UINT32_MAX:
        raise TrafficlabError(
            "frame length exceeds the PCAPNG 32-bit length and SnapLen limit",
            corrective_action="reduce generated frame lengths and retry",
        )
    padded_length = event.frame_length + (-event.frame_length % 4)
    if 32 + padded_length > _UINT32_MAX:
        raise TrafficlabError(
            "PCAPNG block length exceeds the 32-bit format limit",
            corrective_action="reduce generated frame lengths and retry",
        )

    if event.direction is Direction.OUTBOUND:
        destination, source = peer_mac, target_mac
    elif event.direction is Direction.INBOUND:
        destination, source = target_mac, peer_mac
    else:
        raise TrafficlabError(
            f"event has unsupported direction {event.direction!r}",
            corrective_action="generate only outbound or inbound trace events",
        )
    return destination + source + b"\x08\x00" + b"\x00" * (event.frame_length - 14)


def encode_pcapng(events: Iterable[TraceEvent], metadata: CaptureMetadata) -> bytes:
    """Render canonical trace events as deterministic little-endian Ethernet PCAPNG bytes."""
    materialized_events = tuple(events)
    if not materialized_events:
        raise TrafficlabError(
            "PCAPNG rendering requires at least one event",
            corrective_action="generate a nonempty trace and retry",
        )

    target_mac = bytes.fromhex(metadata.target_mac.replace(":", ""))
    peer_mac = bytes.fromhex(deterministic_peer_mac(metadata.target_mac).replace(":", ""))
    frame_lengths = tuple(event.frame_length for event in materialized_events)
    snap_len = max(65535, max(frame_lengths))
    if snap_len > _UINT32_MAX:
        raise TrafficlabError(
            "frame length exceeds the PCAPNG 32-bit length and SnapLen limit",
            corrective_action="reduce generated frame lengths and retry",
        )

    rendered_blocks = [
        _encode_block(
            _SECTION_HEADER_BLOCK,
            struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1),
        ),
        _encode_block(
            _INTERFACE_DESCRIPTION_BLOCK,
            struct.pack("<HHI", _ETHERNET_LINK_TYPE, 0, snap_len)
            + struct.pack("<HHB3x", _IF_TSRESOL_OPTION, 1, 9)
            + struct.pack("<HH", _END_OF_OPTIONS, 0),
        ),
    ]
    previous_timestamp: float | None = None
    for event in materialized_events:
        if previous_timestamp is not None and event.timestamp < previous_timestamp:
            raise TrafficlabError(
                "event timestamps must be nondecreasing",
                corrective_action="sort generated trace events by timestamp and retry",
            )
        previous_timestamp = event.timestamp
        frame = _encode_frame(event, target_mac, peer_mac)
        timestamp = _timestamp_nanoseconds(event)
        packet_padding = b"\x00" * (-len(frame) % 4)
        body = struct.pack(
            "<IIIII",
            0,
            timestamp >> 32,
            timestamp & _UINT32_MAX,
            len(frame),
            len(frame),
        )
        rendered_blocks.append(_encode_block(_ENHANCED_PACKET_BLOCK, body + frame + packet_padding))
    return b"".join(rendered_blocks)


def write_pcapng(path: Path, events: Iterable[TraceEvent], metadata: CaptureMetadata) -> None:
    """Write deterministic Ethernet PCAPNG bytes to one destination path."""
    rendered = encode_pcapng(events, metadata)
    try:
        path.write_bytes(rendered)
    except OSError as error:
        raise TrafficlabError(
            f"could not write PCAPNG {path}: {error}",
            corrective_action="verify the PCAPNG destination is writable",
        ) from error
