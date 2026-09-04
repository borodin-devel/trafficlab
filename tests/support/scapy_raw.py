"""Deterministic binary builders and independent oracles for raw Scapy tests."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal, cast

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY / "tests" / "fixtures" / "data" / "import_run"
UINT32_MAX = 2**32 - 1

type JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class PacketFact:
    timestamp: Fraction
    ordinal: int
    frame: bytes
    captured_length: int
    wire_length: int
    microsecond_ticks: int


@dataclass(frozen=True, slots=True)
class OutputCapture:
    block_types: tuple[int, ...]
    interface_linktypes: tuple[int, ...]
    packets: tuple[PacketFact, ...]


def capture_fact(name: str) -> JsonObject:
    document = cast(JsonObject, cast(object, json.loads((FIXTURES / "expected.json").read_bytes())))
    assert document["schema_version"] == 1
    captures = cast(JsonObject, document["captures"])
    return cast(JsonObject, captures[name])


def expected_packets(fact: JsonObject) -> tuple[PacketFact, ...]:
    rows = cast(list[object], fact["ordered_packets"])
    result: list[PacketFact] = []
    for value in rows:
        row = cast(JsonObject, value)
        result.append(
            PacketFact(
                timestamp=Fraction(cast(str, row["source_timestamp_fraction"])),
                ordinal=cast(int, row["input_ordinal"]),
                frame=bytes.fromhex(cast(str, row["frame_hex"])),
                captured_length=cast(int, row["captured_length"]),
                wire_length=cast(int, row["wire_length"]),
                microsecond_ticks=cast(int, row["canonical_microsecond_ticks"]),
            )
        )
    return tuple(result)


def read_classic_fixture(path: Path) -> tuple[PacketFact, ...]:
    content = path.read_bytes()
    variants = {
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
    }
    endian, resolution = variants[content[:4]]
    _major, _minor, _zone, _accuracy, _snaplen, linktype = struct.unpack_from(f"{endian}HHIIII", content, 4)
    assert linktype == 1
    packets: list[PacketFact] = []
    offset = 24
    while offset < len(content):
        sec, fraction, captured, wire = struct.unpack_from(f"{endian}IIII", content, offset)
        offset += 16
        frame = content[offset : offset + captured]
        assert len(frame) == captured
        offset += captured
        timestamp = Fraction(sec * resolution + fraction, resolution)
        packets.append(
            PacketFact(
                timestamp,
                len(packets),
                frame,
                captured,
                wire,
                timestamp.numerator * 1_000_000 // timestamp.denominator,
            )
        )
    return tuple(sorted(packets, key=lambda packet: (packet.timestamp, packet.ordinal)))


def read_noncanonical_fixture(path: Path) -> tuple[PacketFact, ...]:
    content = path.read_bytes()
    assert content[:4] == b"\x0a\x0d\x0d\x0a"
    endian = ">" if content[8:12] == b"\x1a\x2b\x3c\x4d" else "<"
    resolutions: list[int] = []
    packets: list[PacketFact] = []
    offset = 0
    while offset < len(content):
        block_type, length = struct.unpack_from(f"{endian}II", content, offset)
        assert length >= 12
        assert struct.unpack_from(f"{endian}I", content, offset + length - 4)[0] == length
        body = content[offset + 8 : offset + length - 4]
        if block_type == 1:
            linktype = struct.unpack_from(f"{endian}H", body)[0]
            assert linktype == 1
            assert body[8:13] == b"\x00\x09\x00\x01\x07"
            resolutions.append(10_000_000)
        elif block_type == 2:
            interface, _drops, high, low, captured, wire = struct.unpack_from(f"{endian}HHIIII", body)
            frame = body[20 : 20 + captured]
            assert len(frame) == captured
            timestamp = Fraction((high << 32) | low, resolutions[cast(int, interface)])
            packets.append(
                PacketFact(
                    timestamp,
                    len(packets),
                    frame,
                    captured,
                    wire,
                    timestamp.numerator * 1_000_000 // timestamp.denominator,
                )
            )
        offset += length
    return tuple(sorted(packets, key=lambda packet: (packet.timestamp, packet.ordinal)))


def read_canonical_output(path: Path) -> OutputCapture:
    content = path.read_bytes()
    assert content[:4] == b"\x0a\x0d\x0d\x0a"
    endian = "<" if content[8:12] == b"\x4d\x3c\x2b\x1a" else ">"
    block_types: list[int] = []
    linktypes: list[int] = []
    packets: list[PacketFact] = []
    offset = 0
    while offset < len(content):
        block_type, length = struct.unpack_from(f"{endian}II", content, offset)
        assert length >= 12 and offset + length <= len(content)
        assert struct.unpack_from(f"{endian}I", content, offset + length - 4)[0] == length
        body = content[offset + 8 : offset + length - 4]
        block_types.append(block_type)
        if block_type == 1:
            linktypes.append(struct.unpack_from(f"{endian}H", body)[0])
        elif block_type == 6:
            interface, high, low, captured, wire = struct.unpack_from(f"{endian}IIIII", body)
            assert interface == 0
            frame = body[20 : 20 + captured]
            packets.append(PacketFact(Fraction((high << 32) | low, 1_000_000), -1, frame, captured, wire, 0))
        offset += length
    return OutputCapture(tuple(block_types), tuple(linktypes), tuple(packets))


def classic_capture(
    packets: tuple[tuple[int, int, bytes, int, int], ...],
    *,
    linktype: int = 1,
    nano: bool = False,
    endian: Literal["<", ">"] = "<",
    trailing: bytes = b"",
) -> bytes:
    magic = 0xA1B23C4D if nano else 0xA1B2C3D4
    content = bytearray(struct.pack(f"{endian}IHHIIII", magic, 2, 4, 0, 0, 262_144, linktype))
    for seconds, subsecond, frame, captured, wire in packets:
        content.extend(struct.pack(f"{endian}IIII", seconds, subsecond, captured, wire))
        content.extend(frame)
    content.extend(trailing)
    return bytes(content)


def pcapng_block(endian: Literal["<", ">"], block_type: int, body: bytes) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    length = 12 + len(padded)
    return struct.pack(f"{endian}II", block_type, length) + padded + struct.pack(f"{endian}I", length)


def pcapng_capture(
    endian: Literal["<", ">"],
    packets: tuple[tuple[int, bytes, int], ...],
    *,
    resolution_option: int,
    block_type: Literal[3, 6] = 6,
    linktype: int = 1,
) -> bytes:
    byte_order = b"\x4d\x3c\x2b\x1a" if endian == "<" else b"\x1a\x2b\x3c\x4d"
    section = pcapng_block(endian, 0x0A0D0D0A, byte_order + struct.pack(f"{endian}HHq", 1, 0, -1))
    options = struct.pack(f"{endian}HHB", 9, 1, resolution_option) + b"\x00" * 3 + struct.pack(f"{endian}HH", 0, 0)
    interface = pcapng_block(endian, 1, struct.pack(f"{endian}HHI", linktype, 0, 262_144) + options)
    content = bytearray(section + interface)
    for ticks, frame, wire_length in packets:
        if block_type == 6:
            body = struct.pack(f"{endian}IIIII", 0, ticks >> 32, ticks & UINT32_MAX, len(frame), wire_length) + frame
        else:
            body = struct.pack(f"{endian}I", wire_length) + frame
        content.extend(pcapng_block(endian, block_type, body))
    return bytes(content)


def write_two_packet_capture(
    path: Path,
    *,
    timestamps: tuple[tuple[int, int], tuple[int, int]] = ((1, 0), (2, 0)),
) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    path.write_bytes(
        classic_capture(
            (
                (*timestamps[0], frames[0], len(frames[0]), 60),
                (*timestamps[1], frames[1], len(frames[1]), 64),
            )
        )
    )
