"""Small Scapy-backed capture builders for tests that need PCAPNG bytes."""

from __future__ import annotations

import struct
from collections.abc import Iterable

from trafficlab.scapy_io import encode_pcapng
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, deterministic_peer_mac


def encode_events(events: Iterable[TraceEvent], metadata: CaptureMetadata) -> bytes:
    """Encode test events with a window covering their absolute timestamps."""
    trace = TrafficTrace.from_events(events)
    window = max(1.0, float(trace.timestamps[-1]))
    return encode_pcapng(trace, metadata, observation_window_seconds=window).content


def _block(block_type: int, body: bytes) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    total = 12 + len(padded)
    return struct.pack("<II", block_type, total) + padded + struct.pack("<I", total)


def encode_precise_events(
    events: Iterable[TraceEvent],
    metadata: CaptureMetadata,
    *,
    resolution: int,
) -> bytes:
    """Build the minimal valid PCAPNG needed to test non-Scapy input resolutions."""
    trace = TrafficTrace.from_events(events)
    section = _block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    options = struct.pack("<HHB3x", 9, 1, resolution) + struct.pack("<HH", 0, 0)
    interface = _block(1, struct.pack("<HHI", 1, 0, 0xFFFFFFFF) + options)
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    peer = bytes.fromhex(deterministic_peer_mac(metadata.target_mac).replace(":", ""))
    scale = 2 ** (resolution & 0x7F) if resolution & 0x80 else 10**resolution
    packets = bytearray()
    for event in trace:
        destination, source = (peer, target) if event.direction is Direction.OUTBOUND else (target, peer)
        frame = destination + source + b"\x08\x00" + b"\x00" * (event.frame_length - 14)
        ticks = round(event.timestamp * scale)
        body = struct.pack("<IIIII", 0, ticks >> 32, ticks & 0xFFFFFFFF, len(frame), len(frame)) + frame
        packets.extend(_block(6, body))
    return section + interface + bytes(packets)
