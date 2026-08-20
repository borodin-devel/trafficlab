"""Small independent PCAPNG oracle for valid Ethernet test captures."""

from __future__ import annotations

import struct
from pathlib import Path

from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace

_SECTION = 0x0A0D0D0A
_INTERFACE = 1
_PACKET = 6
_TSRESOL = 9


def _options(content: bytes, endian: str) -> dict[int, bytes]:
    values: dict[int, bytes] = {}
    offset = 0
    while offset < len(content):
        code, length = struct.unpack_from(f"{endian}HH", content, offset)
        offset += 4
        if code == 0:
            break
        values[code] = content[offset : offset + length]
        offset += length + (-length % 4)
    return values


def _timestamp(ticks: int, resolution: int | None) -> float:
    if resolution is None:
        return ticks / 1_000_000
    if resolution & 0x80:
        return ticks / 2 ** (resolution & 0x7F)
    return ticks / 10**resolution


def _parse_valid_enhanced_packets(content: bytes, metadata: CaptureMetadata) -> tuple[TraceEvent, ...]:
    if content[:4] != struct.pack("<I", _SECTION):
        raise ValueError("oracle requires a PCAPNG section header")
    magic = content[8:12]
    if magic == b"\x4d\x3c\x2b\x1a":
        endian = "<"
    elif magic == b"\x1a\x2b\x3c\x4d":
        endian = ">"
    else:
        raise ValueError("oracle requires valid byte-order magic")

    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    resolution: int | None = None
    events: list[TraceEvent] = []
    offset = 0
    while offset < len(content):
        block_type, total_length = struct.unpack_from(f"{endian}II", content, offset)
        if total_length < 12 or offset + total_length > len(content):
            raise ValueError("oracle requires complete blocks")
        if struct.unpack_from(f"{endian}I", content, offset + total_length - 4)[0] != total_length:
            raise ValueError("oracle requires matching block lengths")
        body = content[offset + 8 : offset + total_length - 4]
        if block_type == _INTERFACE:
            linktype = struct.unpack_from(f"{endian}H", body)[0]
            if linktype != 1:
                raise ValueError("oracle requires Ethernet")
            encoded = _options(body[8:], endian).get(_TSRESOL)
            resolution = None if encoded is None else encoded[0]
        elif block_type == _PACKET:
            interface, high, low, captured, _wire = struct.unpack_from(f"{endian}IIIII", body)
            if interface != 0:
                raise ValueError("oracle requires interface zero")
            frame = body[20 : 20 + captured]
            if len(frame) < 14:
                raise ValueError("oracle requires complete Ethernet frames")
            direction = Direction.OUTBOUND if frame[6:12] == target else Direction.INBOUND
            events.append(TraceEvent(_timestamp((high << 32) | low, resolution), direction, captured))
        offset += total_length
    return tuple(events)


def oracle_trace(content: bytes, metadata: CaptureMetadata, *, source: Path) -> TrafficTrace:
    """Decode the valid subset without Scapy or production PCAPNG code."""
    del source
    return TrafficTrace.from_events(_parse_valid_enhanced_packets(content, metadata))
