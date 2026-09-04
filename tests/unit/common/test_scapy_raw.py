# pyright: reportPrivateUsage=false
"""Scientific raw-capture normalization tests with an independent binary oracle."""

from __future__ import annotations

import json
import os
import shutil
import struct
from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Literal, cast

import pytest

from trafficlab.common.errors import DeadlineExceededError, TrafficlabError
from trafficlab.common.scapy_io import RawNormalizationResult, normalize_raw_capture
from trafficlab.common.scapy_io import raw as scapy_raw

_REPOSITORY = Path(__file__).resolve().parents[3]
_FIXTURES = _REPOSITORY / "tests" / "fixtures" / "data" / "import_run"
_UINT32_MAX = 2**32 - 1
_MALFORMED_ACTION = "replace the source with a complete valid Ethernet PCAP or PCAPNG capture"
_DEADLINE_ACTION = "increase capture.total_timeout_seconds and retry import-run"

type _JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class _PacketFact:
    timestamp: Fraction
    ordinal: int
    frame: bytes
    captured_length: int
    wire_length: int
    microsecond_ticks: int


@dataclass(frozen=True, slots=True)
class _OutputCapture:
    block_types: tuple[int, ...]
    interface_linktypes: tuple[int, ...]
    packets: tuple[_PacketFact, ...]


@dataclass(frozen=True, slots=True)
class _ClassicMetadata:
    sec: int
    usec: int
    wirelen: int = 64
    caplen: int = 14


@dataclass(frozen=True, slots=True)
class _PcapngMetadata:
    linktype: int = 1
    tsresol: int = 1_000_000
    tshigh: int | None = 0
    tslow: int | None = 0
    wirelen: int = 64


class _SequenceClock:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return next(self._values)


class _ErrorReader:
    linktype = 1
    nano = False

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.f: BinaryIO = BytesIO()

    def __enter__(self) -> _ErrorReader:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def _read_packet(self, size: int) -> tuple[bytes, object]:
        del size
        raise self._error


class _ErrorReaderFactory:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __call__(self, filename: str) -> _ErrorReader:
        del filename
        return _ErrorReader(self._error)


class _ErrorWriter:
    linktype = 1

    def __init__(self, error: Exception) -> None:
        self._error = error

    def __enter__(self) -> _ErrorWriter:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def write_header(self, packet: bytes) -> None:
        del packet

    def write_packet(
        self,
        packet: bytes,
        sec: object | None = None,
        usec: int | None = None,
        caplen: int | None = None,
        wirelen: int | None = None,
    ) -> None:
        del packet, sec, usec, caplen, wirelen
        raise self._error


class _ErrorWriterFactory:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __call__(self, filename: str) -> _ErrorWriter:
        del filename
        return _ErrorWriter(self._error)


class _TimestampFactory:
    def __call__(self, value: str) -> object:
        return value


class _ShortWriteSpool:
    def tell(self) -> int:
        return 7

    def write(self, frame: bytes) -> int:
        return len(frame) - 1


class _ErrorSpool:
    def tell(self) -> int:
        raise OSError("spool unavailable")

    def write(self, frame: bytes) -> int:
        del frame
        raise AssertionError("write must not follow a failed tell")


def _capture_fact(name: str) -> _JsonObject:
    document = cast(_JsonObject, cast(object, json.loads((_FIXTURES / "expected.json").read_bytes())))
    assert document["schema_version"] == 1
    captures = cast(_JsonObject, document["captures"])
    return cast(_JsonObject, captures[name])


def _expected_packets(fact: _JsonObject) -> tuple[_PacketFact, ...]:
    rows = cast(list[object], fact["ordered_packets"])
    result: list[_PacketFact] = []
    for value in rows:
        row = cast(_JsonObject, value)
        result.append(
            _PacketFact(
                timestamp=Fraction(cast(str, row["source_timestamp_fraction"])),
                ordinal=cast(int, row["input_ordinal"]),
                frame=bytes.fromhex(cast(str, row["frame_hex"])),
                captured_length=cast(int, row["captured_length"]),
                wire_length=cast(int, row["wire_length"]),
                microsecond_ticks=cast(int, row["canonical_microsecond_ticks"]),
            )
        )
    return tuple(result)


def _read_classic_fixture(path: Path) -> tuple[_PacketFact, ...]:
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
    packets: list[_PacketFact] = []
    offset = 24
    while offset < len(content):
        sec, fraction, captured, wire = struct.unpack_from(f"{endian}IIII", content, offset)
        offset += 16
        frame = content[offset : offset + captured]
        assert len(frame) == captured
        offset += captured
        timestamp = Fraction(sec * resolution + fraction, resolution)
        packets.append(
            _PacketFact(
                timestamp,
                len(packets),
                frame,
                captured,
                wire,
                timestamp.numerator * 1_000_000 // timestamp.denominator,
            )
        )
    return tuple(sorted(packets, key=lambda packet: (packet.timestamp, packet.ordinal)))


def _read_noncanonical_fixture(path: Path) -> tuple[_PacketFact, ...]:
    content = path.read_bytes()
    assert content[:4] == b"\x0a\x0d\x0d\x0a"
    endian = ">" if content[8:12] == b"\x1a\x2b\x3c\x4d" else "<"
    resolutions: list[int] = []
    packets: list[_PacketFact] = []
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
                _PacketFact(
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


def _read_canonical_output(path: Path) -> _OutputCapture:
    content = path.read_bytes()
    assert content[:4] == b"\x0a\x0d\x0d\x0a"
    endian = "<" if content[8:12] == b"\x4d\x3c\x2b\x1a" else ">"
    block_types: list[int] = []
    linktypes: list[int] = []
    packets: list[_PacketFact] = []
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
            packets.append(_PacketFact(Fraction((high << 32) | low, 1_000_000), -1, frame, captured, wire, 0))
        offset += length
    return _OutputCapture(tuple(block_types), tuple(linktypes), tuple(packets))


def _classic_capture(
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


def _pcapng_block(endian: Literal["<", ">"], block_type: int, body: bytes) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    length = 12 + len(padded)
    return struct.pack(f"{endian}II", block_type, length) + padded + struct.pack(f"{endian}I", length)


def _pcapng_capture(
    endian: Literal["<", ">"],
    packets: tuple[tuple[int, bytes, int], ...],
    *,
    resolution_option: int,
    block_type: Literal[3, 6] = 6,
    linktype: int = 1,
) -> bytes:
    byte_order = b"\x4d\x3c\x2b\x1a" if endian == "<" else b"\x1a\x2b\x3c\x4d"
    section = _pcapng_block(endian, 0x0A0D0D0A, byte_order + struct.pack(f"{endian}HHq", 1, 0, -1))
    options = struct.pack(f"{endian}HHB", 9, 1, resolution_option) + b"\x00" * 3 + struct.pack(f"{endian}HH", 0, 0)
    interface = _pcapng_block(endian, 1, struct.pack(f"{endian}HHI", linktype, 0, 262_144) + options)
    content = bytearray(section + interface)
    for ticks, frame, wire_length in packets:
        if block_type == 6:
            body = struct.pack(f"{endian}IIIII", 0, ticks >> 32, ticks & _UINT32_MAX, len(frame), wire_length) + frame
        else:
            body = struct.pack(f"{endian}I", wire_length) + frame
        content.extend(_pcapng_block(endian, block_type, body))
    return bytes(content)


def _write_two_packet_capture(
    path: Path, *, timestamps: tuple[tuple[int, int], tuple[int, int]] = ((1, 0), (2, 0))
) -> None:
    frames = (bytes.fromhex("ffffffffffff0242ac1100020806"), bytes.fromhex("0011223344550242ac110002080045000000"))
    path.write_bytes(
        _classic_capture(
            (
                (*timestamps[0], frames[0], len(frames[0]), 60),
                (*timestamps[1], frames[1], len(frames[1]), 64),
            )
        )
    )


def _assert_trafficlab_error(
    source: Path,
    destination: Path,
    *,
    message: str,
    corrective_action: str = _MALFORMED_ACTION,
) -> TrafficlabError:
    with pytest.raises(TrafficlabError, match=message) as caught:
        normalize_raw_capture(source, destination, deadline=None)
    assert caught.value.corrective_action == corrective_action
    return caught.value


@pytest.mark.parametrize("fixture_name", ["classic_pcap", "classic_nanosecond", "noncanonical_pcapng"])
def test_checked_raw_fixtures_match_the_literal_fraction_oracle(fixture_name: str) -> None:
    fact = _capture_fact(fixture_name)
    source = _FIXTURES / cast(str, fact["source_path"])

    packets = (
        _read_noncanonical_fixture(source) if fixture_name == "noncanonical_pcapng" else _read_classic_fixture(source)
    )

    assert packets == _expected_packets(fact)


@pytest.mark.parametrize("fixture_name", ["classic_pcap", "classic_nanosecond", "noncanonical_pcapng"])
def test_normalize_raw_capture_preserves_frames_lengths_and_exact_stable_order(
    fixture_name: str, tmp_path: Path
) -> None:
    fact = _capture_fact(fixture_name)
    misleading_suffix = ".pcap" if fact["input_format"] == "pcapng" else ".pcapng"
    source = tmp_path / f"source{misleading_suffix}"
    shutil.copyfile(_FIXTURES / cast(str, fact["source_path"]), source)
    destination = tmp_path / "normalized.pcapng"

    result = normalize_raw_capture(source, destination, deadline=None)

    assert result == RawNormalizationResult(
        input_format=cast(Literal["pcap", "pcapng"], fact["input_format"]),
        packet_count=cast(int, fact["packet_count"]),
        observation_window_seconds=cast(float, fact["observation_window_seconds"]),
        reordered=cast(bool, fact["reordered"]),
    )
    output = _read_canonical_output(destination)
    expected = _expected_packets(fact)
    assert output.block_types == (0x0A0D0D0A, 1, *(6 for _packet in expected))
    assert output.interface_linktypes == (1,)
    assert tuple(
        (packet.frame, packet.captured_length, packet.wire_length, int(packet.timestamp * 1_000_000))
        for packet in output.packets
    ) == tuple(
        (packet.frame, packet.captured_length, packet.wire_length, packet.microsecond_ticks) for packet in expected
    )


def test_canonical_little_endian_pcapng_can_be_normalized_again(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    first = tmp_path / "first.pcapng"
    second = tmp_path / "second.pcapng"
    normalize_raw_capture(source, first, deadline=None)

    result = normalize_raw_capture(first, second, deadline=None)

    assert result == RawNormalizationResult("pcapng", 2, 1.0, False)
    assert _read_canonical_output(first).packets == _read_canonical_output(second).packets


@pytest.mark.parametrize(
    ("endian", "nano"),
    [("<", False), (">", False), ("<", True), (">", True)],
    ids=["little-microsecond", "big-microsecond", "little-nanosecond", "big-nanosecond"],
)
def test_all_classic_byte_order_and_timestamp_resolution_variants_are_accepted(
    endian: Literal["<", ">"], nano: bool, tmp_path: Path
) -> None:
    resolution = 1_000_000_000 if nano else 1_000_000
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    source = tmp_path / "source.pcap"
    source.write_bytes(
        _classic_capture(
            (
                (1, resolution // 4, frames[0], len(frames[0]), 60),
                (2, resolution // 2, frames[1], len(frames[1]), 64),
            ),
            nano=nano,
            endian=endian,
        )
    )

    result = normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=None)

    assert result == RawNormalizationResult("pcap", 2, 1.25, False)


def test_binary_pcapng_timestamp_resolution_is_converted_exactly(tmp_path: Path) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    source = tmp_path / "source.pcapng"
    source.write_bytes(_pcapng_capture("<", ((1_024, frames[0], 60), (2_304, frames[1], 64)), resolution_option=0x8A))
    destination = tmp_path / "output.pcapng"

    result = normalize_raw_capture(source, destination, deadline=None)

    assert result == RawNormalizationResult("pcapng", 2, 1.25, False)
    assert tuple(int(packet.timestamp * 1_000_000) for packet in _read_canonical_output(destination).packets) == (
        1_000_000,
        2_250_000,
    )


def test_expected_oracle_uses_valid_uint32_timestamp_ticks() -> None:
    for fixture_name in ("classic_pcap", "classic_nanosecond", "noncanonical_pcapng"):
        for packet in _expected_packets(_capture_fact(fixture_name)):
            assert 0 <= packet.microsecond_ticks <= _UINT32_MAX * 1_000_000


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xd4\xc3\xb2\xa1\x02\x00", "truncated PCAP global header"),
        (b"\x0a\x0d\x0d\x0a\x00\x00\x00\x1c", "PCAPNG section byte-order magic"),
    ],
    ids=["pcap", "pcapng"],
)
def test_normalization_rejects_truncated_container_headers(content: bytes, message: str, tmp_path: Path) -> None:
    source = tmp_path / "source.capture"
    source.write_bytes(content)

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message=message)


def test_normalization_rejects_trailing_partial_pcapng_block(tmp_path: Path) -> None:
    source = tmp_path / "source.pcapng"
    source.write_bytes((_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes() + b"\x00\x00")

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="truncated PCAPNG block")


def test_normalization_rejects_trailing_partial_pcap_packet_record(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    source.write_bytes(source.read_bytes() + b"\x00" * 8)

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="truncated PCAP packet record")


def test_normalization_rejects_invalid_pcapng_interface_before_it_can_hide_later_packets(tmp_path: Path) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    content = bytearray(
        _pcapng_capture(
            ">",
            (
                (1_000_000, frames[0], 60),
                (2_000_000, frames[1], 64),
                (3_000_000, frames[0], 60),
                (4_000_000, frames[1], 64),
            ),
            resolution_option=6,
        )
    )
    offset = 0
    packet_offsets: list[int] = []
    while offset < len(content):
        block_type, block_length = struct.unpack_from(">II", content, offset)
        if block_type == 6:
            packet_offsets.append(offset)
        offset += block_length
    struct.pack_into(">I", content, packet_offsets[2] + 8, 99)
    source = tmp_path / "source.pcapng"
    source.write_bytes(content)
    destination = tmp_path / "output.pcapng"

    _assert_trafficlab_error(
        source,
        destination,
        message="packet block references interface 99 but the section defines 1",
    )
    assert not destination.exists()


@pytest.mark.parametrize(
    ("block_type", "name", "minimum"),
    [
        (1, "Interface Description Block", 8),
        (3, "Simple Packet Block", 4),
        (10, "Decryption Secrets Block", 8),
        (0x80000001, "Process Information Block", 4),
    ],
)
def test_normalization_rejects_short_scapy_handled_pcapng_blocks(
    block_type: int, name: str, minimum: int, tmp_path: Path
) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    content = _pcapng_capture(
        ">", ((1_000_000, frames[0], 60), (2_000_000, frames[1], 64)), resolution_option=6
    ) + _pcapng_block(">", block_type, b"")
    source = tmp_path / "source.pcapng"
    source.write_bytes(content)
    destination = tmp_path / "output.pcapng"

    _assert_trafficlab_error(source, destination, message=f"{name} body must be at least {minimum} bytes")
    assert not destination.exists()


def test_normalization_rejects_decryption_secrets_length_beyond_its_block(tmp_path: Path) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    content = _pcapng_capture(
        ">", ((1_000_000, frames[0], 60), (2_000_000, frames[1], 64)), resolution_option=6
    ) + _pcapng_block(">", 10, struct.pack(">II", 0x1234, 8) + b"\x00" * 4)
    source = tmp_path / "source.pcapng"
    source.write_bytes(content)
    destination = tmp_path / "output.pcapng"

    _assert_trafficlab_error(source, destination, message="Decryption Secrets Block data length 8 exceeds 4 bytes")
    assert not destination.exists()


@pytest.mark.parametrize(
    ("option_code", "name"),
    [(2, "flags"), (0x8001, "process index")],
)
def test_normalization_rejects_epb_options_that_make_scapy_abort_packet_decoding(
    option_code: int, name: str, tmp_path: Path
) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    content = _pcapng_capture(">", ((1_000_000, frames[0], 60), (2_000_000, frames[1], 64)), resolution_option=6)
    invalid_frame = frames[0]
    packet_body = (
        struct.pack(">IIIII", 0, 0, 3_000_000, len(invalid_frame), 60)
        + invalid_frame
        + b"\x00" * (-len(invalid_frame) % 4)
        + struct.pack(">HHB", option_code, 1, 0)
        + b"\x00" * 3
    )
    source = tmp_path / "source.pcapng"
    source.write_bytes(content + _pcapng_block(">", 6, packet_body))
    destination = tmp_path / "output.pcapng"

    _assert_trafficlab_error(source, destination, message=f"Enhanced Packet Block {name} option must contain 4 bytes")
    assert not destination.exists()


def test_valid_scapy_metadata_blocks_do_not_end_packet_decoding_early(tmp_path: Path) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    content = _pcapng_capture(">", ((1_000_000, frames[0], 60), (2_000_000, frames[1], 64)), resolution_option=6)
    metadata_blocks = _pcapng_block(">", 10, struct.pack(">II", 0x1234, 4) + b"data") + _pcapng_block(
        ">", 0x80000001, struct.pack(">I", 42)
    )
    option_frame = frames[0]
    option_packet = _pcapng_block(
        ">",
        6,
        struct.pack(">IIIII", 0, 0, 3_000_000, len(option_frame), 60)
        + option_frame
        + b"\x00" * (-len(option_frame) % 4)
        + struct.pack(">HHI", 2, 4, 1),
    )
    source = tmp_path / "source.pcapng"
    source.write_bytes(content + metadata_blocks + option_packet)

    result = normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=None)

    assert result == RawNormalizationResult("pcapng", 3, 2.0, False)


@pytest.mark.parametrize("block_type", [3, 6], ids=["simple", "enhanced"])
def test_normalization_rejects_packet_block_before_the_first_interface(block_type: int, tmp_path: Path) -> None:
    frame = bytes.fromhex("ffffffffffff0242ac1100020806")
    section = _pcapng_capture(">", (), resolution_option=6)[:28]
    body = (
        struct.pack(">I", len(frame)) + frame
        if block_type == 3
        else struct.pack(">IIIII", 0, 0, 1_000_000, len(frame), 60) + frame
    )
    packet = _pcapng_block(">", block_type, body)
    source = tmp_path / "source.pcapng"
    source.write_bytes(section + packet)

    _assert_trafficlab_error(
        source,
        tmp_path / "output.pcapng",
        message="packet block references interface 0 but the section defines 0",
    )


@pytest.mark.parametrize(
    "second_section",
    [
        _pcapng_capture(
            "<",
            (
                (3_072, bytes.fromhex("ffffffffffff0242ac1100020806"), 60),
                (4_096, bytes.fromhex("0011223344550242ac110002080045000000"), 64),
            ),
            resolution_option=0x8A,
        ),
        _pcapng_capture(
            ">",
            (
                (3_000_000, bytes.fromhex("ffffffffffff0242ac1100020806"), 60),
                (4_000_000, bytes.fromhex("0011223344550242ac110002080045000000"), 64),
            ),
            resolution_option=6,
            linktype=101,
        ),
    ],
    ids=["mixed-endian-resolution", "changed-linktype"],
)
def test_normalization_rejects_repeated_pcapng_section_headers(second_section: bytes, tmp_path: Path) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    first_section = _pcapng_capture(">", ((1_000_000, frames[0], 60), (2_000_000, frames[1], 64)), resolution_option=6)
    source = tmp_path / "source.pcapng"
    source.write_bytes(first_section + second_section)
    destination = tmp_path / "output.pcapng"

    _assert_trafficlab_error(source, destination, message="multiple PCAPNG sections are unsupported")
    assert not destination.exists()


@pytest.mark.parametrize(
    ("endian", "major", "minor"),
    [("<", 3, 4), (">", 2, 3)],
    ids=["unsupported-major", "unsupported-minor"],
)
def test_normalization_rejects_unsupported_classic_pcap_version(
    endian: Literal["<", ">"], major: int, minor: int, tmp_path: Path
) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    content = bytearray(
        _classic_capture(
            ((1, 0, frames[0], len(frames[0]), 60), (2, 0, frames[1], len(frames[1]), 64)),
            endian=endian,
        )
    )
    struct.pack_into(f"{endian}HH", content, 4, major, minor)
    source = tmp_path / "source.pcap"
    source.write_bytes(content)
    destination = tmp_path / "output.pcapng"

    _assert_trafficlab_error(source, destination, message=f"unsupported PCAP version {major}.{minor}; expected 2.4")
    assert not destination.exists()


def test_normalization_rejects_pcapng_captured_length_mismatch(tmp_path: Path) -> None:
    content = bytearray((_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes())
    first_packet_offset = 28 + 32 + 32
    struct.pack_into(">I", content, first_packet_offset + 8 + 12, 40)
    source = tmp_path / "source.pcapng"
    source.write_bytes(content)

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="captured length 40")


def test_normalization_rejects_pcapng_wire_length_below_captured_length(tmp_path: Path) -> None:
    content = bytearray((_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes())
    first_packet_offset = 28 + 32 + 32
    struct.pack_into(">I", content, first_packet_offset + 8 + 16, 1)
    source = tmp_path / "source.pcapng"
    source.write_bytes(content)

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="wire length 1 is below captured length 18")


def test_normalization_rejects_simple_packet_blocks_without_timestamps(tmp_path: Path) -> None:
    frames = (
        bytes.fromhex("ffffffffffff0242ac1100020806"),
        bytes.fromhex("0011223344550242ac110002080045000000"),
    )
    source = tmp_path / "source.pcapng"
    source.write_bytes(
        _pcapng_capture(
            "<",
            ((0, frames[0], len(frames[0])), (0, frames[1], len(frames[1]))),
            resolution_option=6,
            block_type=3,
        )
    )

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="PCAPNG timestamp high field")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (_pcapng_block(">", 1, b"\x00" * 8), "first PCAPNG block must be a section header"),
        (
            b"\x0a\x0d\x0d\x0a" + struct.pack(">I", 24) + b"\x1a\x2b\x3c\x4d" + b"\x00" * 12,
            "invalid PCAPNG block length 24",
        ),
        (
            b"\x0a\x0d\x0d\x0a" + struct.pack(">I", 26) + b"\x1a\x2b\x3c\x4d" + b"\x00" * 14,
            "invalid PCAPNG block length 26",
        ),
        (
            (_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes() + struct.pack(">II", 1, 12),
            "truncated PCAPNG block body",
        ),
        (
            (_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes()[:-4] + b"\x00\x00\x00\x00",
            "block length trailer does not match",
        ),
        (
            (_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes()[:28]
            + _pcapng_block(">", 2, b"\x00" * 4),
            "packet block is shorter than its fixed header",
        ),
    ],
    ids=["missing-section", "short-section", "unaligned-length", "truncated-body", "bad-trailer", "short-packet"],
)
def test_pcapng_structural_validator_rejects_invalid_block_boundaries(
    content: bytes, message: str, tmp_path: Path
) -> None:
    source = tmp_path / "source.pcapng"
    source.write_bytes(content)

    with pytest.raises(TrafficlabError, match=message) as caught:
        scapy_raw._validate_pcapng_structure(source)

    assert caught.value.corrective_action == _MALFORMED_ACTION


@pytest.mark.parametrize("linktype", [0, 101])
def test_normalization_rejects_non_ethernet_classic_capture(linktype: int, tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    frame = bytes.fromhex("ffffffffffff0242ac1100020806")
    source.write_bytes(_classic_capture(((1, 0, frame, len(frame), len(frame)),), linktype=linktype))

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message=f"unsupported link type {linktype}")


def test_normalization_rejects_non_ethernet_packet_on_second_pcapng_interface(tmp_path: Path) -> None:
    content = bytearray((_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes())
    second_interface_offset = 28 + 32
    struct.pack_into(">H", content, second_interface_offset + 8, 101)
    source = tmp_path / "source.pcapng"
    source.write_bytes(content)

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="unsupported link type 101")


@pytest.mark.parametrize(
    ("frame", "captured", "wire", "message"),
    [
        (b"\x00" * 13, 13, 13, "Ethernet frame length must be at least 14"),
        (b"\x00" * 14, 15, 15, "captured length 15 does not match 14 frame bytes"),
        (b"\x00" * 14, 14, 13, "wire length 13 is below captured length 14"),
    ],
    ids=["short-frame", "captured-length-mismatch", "wire-length-mismatch"],
)
def test_normalization_rejects_invalid_classic_packet_lengths(
    frame: bytes, captured: int, wire: int, message: str, tmp_path: Path
) -> None:
    source = tmp_path / "source.pcap"
    source.write_bytes(_classic_capture(((1, 0, frame, captured, wire),)))

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message=message)


@pytest.mark.parametrize(
    ("metadata", "resolution", "message"),
    [
        (_ClassicMetadata(-1, 0), 1_000_000, "PCAP timestamp seconds"),
        (_ClassicMetadata(_UINT32_MAX + 1, 0), 1_000_000, "PCAP timestamp seconds"),
        (_ClassicMetadata(0, -1), 1_000_000, "PCAP timestamp fraction"),
        (_ClassicMetadata(0, 1_000_000), 1_000_000, "timestamp fraction must be below"),
    ],
)
def test_classic_timestamp_conversion_rejects_negative_or_overflow_fields(
    metadata: _ClassicMetadata, resolution: int, message: str
) -> None:
    with pytest.raises(TrafficlabError, match=message) as caught:
        scapy_raw._pcap_timestamp(metadata, resolution)
    assert caught.value.corrective_action == _MALFORMED_ACTION


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (_PcapngMetadata(tshigh=-1), "PCAPNG timestamp high field"),
        (_PcapngMetadata(tslow=_UINT32_MAX + 1), "PCAPNG timestamp low field"),
        (_PcapngMetadata(tshigh=None), "PCAPNG timestamp high field"),
        (_PcapngMetadata(tsresol=0), "timestamp resolution"),
    ],
)
def test_pcapng_timestamp_conversion_rejects_missing_negative_or_overflow_fields(
    metadata: _PcapngMetadata, message: str
) -> None:
    with pytest.raises(TrafficlabError, match=message) as caught:
        scapy_raw._pcapng_timestamp(metadata)
    assert caught.value.corrective_action == _MALFORMED_ACTION


def test_pcapng_timestamp_conversion_rejects_canonical_tick_overflow() -> None:
    timestamp = scapy_raw._pcapng_timestamp(_PcapngMetadata(tsresol=1, tshigh=_UINT32_MAX, tslow=_UINT32_MAX))

    with pytest.raises(TrafficlabError, match="exceeds the canonical PCAPNG microsecond range") as caught:
        scapy_raw._microsecond_ticks(timestamp)
    assert caught.value.corrective_action == _MALFORMED_ACTION


def test_normalization_rejects_one_packet(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    frame = bytes.fromhex("ffffffffffff0242ac1100020806")
    source.write_bytes(_classic_capture(((1, 0, frame, len(frame), 60),)))

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="expected at least two packets, got 1")


def test_normalization_rejects_equal_only_canonical_timestamps(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source, timestamps=((1, 10), (1, 10)))

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="observation window must be positive")


def test_normalization_rejects_distinct_timestamps_that_truncate_to_zero_window(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    frames = (bytes.fromhex("ffffffffffff0242ac1100020806"), bytes.fromhex("0011223344550242ac110002080045000000"))
    source.write_bytes(
        _classic_capture(
            (
                (1, 100, frames[0], len(frames[0]), 60),
                (1, 999, frames[1], len(frames[1]), 64),
            ),
            nano=True,
        )
    )

    _assert_trafficlab_error(source, tmp_path / "output.pcapng", message="observation window must be positive")


def test_deadline_is_checked_before_source_open(tmp_path: Path) -> None:
    clock = _SequenceClock((5.0,))

    with pytest.raises(DeadlineExceededError, match="before reading the input") as caught:
        normalize_raw_capture(tmp_path / "missing.pcap", tmp_path / "output.pcapng", deadline=5.0, clock=clock)

    assert caught.value.corrective_action == _DEADLINE_ACTION
    assert clock.calls == 1


def test_deadline_is_checked_after_each_input_packet(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    clock = _SequenceClock((0.0, 5.0))

    with pytest.raises(DeadlineExceededError, match="after reading an input packet") as caught:
        normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=5.0, clock=clock)

    assert caught.value.corrective_action == _DEADLINE_ACTION
    assert clock.calls == 2


def test_deadline_is_checked_during_pcapng_structural_validation(tmp_path: Path) -> None:
    source = tmp_path / "source.pcapng"
    source.write_bytes((_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes())
    clock = _SequenceClock((0.0, 5.0))

    with pytest.raises(DeadlineExceededError, match="after validating a PCAPNG block") as caught:
        normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=5.0, clock=clock)

    assert caught.value.corrective_action == _DEADLINE_ACTION
    assert clock.calls == 2


def test_deadline_is_checked_after_the_last_input_packet(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    clock = _SequenceClock((0.0, 0.0, 5.0))

    with pytest.raises(DeadlineExceededError, match="after reading an input packet"):
        normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=5.0, clock=clock)

    assert clock.calls == 3


@pytest.mark.parametrize(
    ("values", "message", "calls"),
    [
        ((0.0, 0.0, 0.0, 5.0), "before sorting the packet index", 4),
        ((0.0, 0.0, 0.0, 0.0, 5.0), "after sorting the packet index", 5),
    ],
)
def test_deadline_is_checked_on_both_sides_of_sorting(
    values: tuple[float, ...], message: str, calls: int, tmp_path: Path
) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    clock = _SequenceClock(values)

    with pytest.raises(DeadlineExceededError, match=message):
        normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=5.0, clock=clock)

    assert clock.calls == calls


def test_deadline_is_checked_after_each_output_packet(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    clock = _SequenceClock((0.0, 0.0, 0.0, 0.0, 0.0, 5.0))

    with pytest.raises(DeadlineExceededError, match="after writing an output packet") as caught:
        normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=5.0, clock=clock)

    assert caught.value.corrective_action == _DEADLINE_ACTION
    assert clock.calls == 6


def test_deadline_is_checked_after_output_close(tmp_path: Path) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    clock = _SequenceClock((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0))

    with pytest.raises(DeadlineExceededError, match="after closing the output") as caught:
        normalize_raw_capture(source, tmp_path / "output.pcapng", deadline=5.0, clock=clock)

    assert caught.value.corrective_action == _DEADLINE_ACTION
    assert clock.calls == 8


def test_normalization_rejects_unsupported_or_empty_magic(tmp_path: Path) -> None:
    for content in (b"not-a-capture", b""):
        source = tmp_path / f"source-{len(content)}"
        source.write_bytes(content)
        error = _assert_trafficlab_error(
            source,
            tmp_path / "output.pcapng",
            message="unsupported raw capture magic",
        )
        assert "expected classic PCAP or PCAPNG" in str(error)


def test_normalization_wraps_source_and_destination_io_failures(tmp_path: Path) -> None:
    _assert_trafficlab_error(
        tmp_path / "missing.pcap",
        tmp_path / "output.pcapng",
        message="could not read raw capture",
        corrective_action="verify the source capture exists and is readable",
    )
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)
    destination = tmp_path / "destination-directory"
    destination.mkdir()
    _assert_trafficlab_error(
        source,
        destination,
        message="could not write normalized PCAPNG",
        corrective_action="verify free space and permissions for the run directory, then retry import-run",
    )
    _assert_trafficlab_error(
        source,
        tmp_path / "missing-parent" / "output.pcapng",
        message="could not create normalization spool",
        corrective_action="verify free space and permissions for the run directory, then retry import-run",
    )


def test_pcapng_validation_wraps_a_source_race_io_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.pcapng"
    source.write_bytes((_FIXTURES / "noncanonical-pcapng-source" / "source.pcapng").read_bytes())

    def failing_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        del self, follow_symlinks
        raise OSError("source disappeared")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", failing_stat)
        _assert_trafficlab_error(
            source,
            tmp_path / "output.pcapng",
            message="could not read raw capture.*source disappeared",
            corrective_action="verify the source capture exists and is readable",
        )


@pytest.mark.parametrize(
    ("error", "message", "corrective_action"),
    [
        (
            OSError("reader failed"),
            "could not read or spool raw capture",
            "verify the source capture exists and is readable",
        ),
        (ValueError("decoder failed"), "Scapy could not decode the input", _MALFORMED_ACTION),
    ],
)
def test_normalization_wraps_raw_reader_failures(
    error: Exception,
    message: str,
    corrective_action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)

    def reader_boundary(input_format: scapy_raw.RawCaptureFormat) -> scapy_raw._RawReaderFactory:
        del input_format
        return _ErrorReaderFactory(error)

    monkeypatch.setattr(scapy_raw, "_reader_boundary", reader_boundary)

    _assert_trafficlab_error(
        source,
        tmp_path / "output.pcapng",
        message=message,
        corrective_action=corrective_action,
    )


def test_normalization_wraps_unexpected_writer_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.pcap"
    _write_two_packet_capture(source)

    def writer_boundary() -> tuple[scapy_raw._RawWriterFactory, scapy_raw._TimestampFactory]:
        return _ErrorWriterFactory(ValueError("writer failed")), _TimestampFactory()

    monkeypatch.setattr(scapy_raw, "_writer_boundary", writer_boundary)

    _assert_trafficlab_error(
        source,
        tmp_path / "output.pcapng",
        message="Scapy could not encode the normalized PCAPNG",
        corrective_action="verify free space and permissions for the run directory, then retry import-run",
    )


def test_short_spool_read_is_an_actionable_output_failure() -> None:
    packet = scapy_raw._RawPacketIndex(Fraction(1), 0, 0, 14, 14)

    with pytest.raises(TrafficlabError, match="spool returned a short frame") as caught:
        scapy_raw._read_spooled_frame(BytesIO(b"\x00" * 13), packet)

    assert (
        caught.value.corrective_action
        == "verify free space and permissions for the run directory, then retry import-run"
    )


@pytest.mark.parametrize("spool", [_ShortWriteSpool(), _ErrorSpool()], ids=["short-write", "io-error"])
def test_spool_write_failure_is_an_actionable_output_failure(spool: object) -> None:
    with pytest.raises(TrafficlabError, match="could not write raw capture normalization spool") as caught:
        scapy_raw._append_spooled_frame(cast(BinaryIO, spool), b"\x00" * 14)

    assert (
        caught.value.corrective_action
        == "verify free space and permissions for the run directory, then retry import-run"
    )
