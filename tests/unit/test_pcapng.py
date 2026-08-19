import struct
from io import BytesIO
from pathlib import Path
from typing import Literal

import pytest

from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.pcapng import (
    PacketObservation,
    encode_pcapng,
    parse_pcapng,
    parse_pcapng_bytes,
    parse_pcapng_packets,
    parse_pcapng_trace,
    write_pcapng,
)
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace

Endian = Literal["<", ">"]

_TARGET = "02:42:ac:11:00:02"
_TARGET_BYTES = bytes.fromhex("0242ac110002")
_PEER_BYTES = bytes.fromhex("020000000001")
_BROADCAST_BYTES = b"\xff" * 6


def _metadata(target_mac: str = _TARGET) -> CaptureMetadata:
    return CaptureMetadata(interface="eth0", target_mac=target_mac)


def _option(code: int, value: bytes, endian: Endian = "<") -> bytes:
    padding = b"\x00" * (-len(value) % 4)
    return struct.pack(f"{endian}HH", code, len(value)) + value + padding


def _option_with_nonzero_padding(code: int, value: bytes) -> bytes:
    option = bytearray(_option(code, value))
    assert len(value) % 4
    option[-1] = 1
    return bytes(option)


def _end_options(endian: Endian = "<") -> bytes:
    return struct.pack(f"{endian}HH", 0, 0)


def _block(block_type: int, body: bytes, endian: Endian = "<", *, trailing_length: int | None = None) -> bytes:
    padded_body = body + b"\x00" * (-len(body) % 4)
    total_length = 12 + len(padded_body)
    trailer = total_length if trailing_length is None else trailing_length
    return struct.pack(f"{endian}II", block_type, total_length) + padded_body + struct.pack(f"{endian}I", trailer)


def _section_header(endian: Endian = "<", *, version: tuple[int, int] = (1, 0)) -> bytes:
    body = struct.pack(f"{endian}IHHq", 0x1A2B3C4D, *version, -1)
    return _block(0x0A0D0D0A, body, endian)


def _interface_description(
    endian: Endian = "<",
    *,
    link_type: int = 1,
    snap_len: int = 0,
    options: bytes = b"",
) -> bytes:
    return _block(1, struct.pack(f"{endian}HHI", link_type, 0, snap_len) + options, endian)


def _ethernet_frame(source: bytes, destination: bytes = _PEER_BYTES, *, frame_length: int = 14) -> bytes:
    assert frame_length >= 14
    return destination + source + b"\x08\x00" + b"\x00" * (frame_length - 14)


def _enhanced_packet(
    frame: bytes,
    timestamp: int,
    endian: Endian = "<",
    *,
    interface_id: int = 0,
    captured_length: int | None = None,
    original_length: int | None = None,
    packet_data: bytes | None = None,
    options: bytes = b"",
) -> bytes:
    captured = len(frame) if captured_length is None else captured_length
    original = len(frame) if original_length is None else original_length
    data = frame if packet_data is None else packet_data
    packet_padding = b"\x00" * (-len(data) % 4)
    body = struct.pack(
        f"{endian}IIIII",
        interface_id,
        timestamp >> 32,
        timestamp & 0xFFFFFFFF,
        captured,
        original,
    )
    return _block(6, body + data + packet_padding + options, endian)


def _capture(*blocks: bytes, endian: Endian = "<", idb: bytes | None = None) -> bytes:
    interface = _interface_description(endian) if idb is None else idb
    return _section_header(endian) + interface + b"".join(blocks)


def _write_capture(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "capture.pcapng"
    path.write_bytes(content)
    return path


class _ObservedReader(BytesIO):
    """Expose path-parser read sizes and optionally fail after bounded reads."""

    def __init__(self, content: bytes, *, fail_after_reads: int | None = None) -> None:
        super().__init__(content)
        self.read_sizes: list[int] = []
        self.fail_after_reads = fail_after_reads

    def read(self, size: int | None = -1) -> bytes:
        if self.fail_after_reads is not None and len(self.read_sizes) >= self.fail_after_reads:
            raise OSError("injected live-stream read failure")
        self.read_sizes.append(-1 if size is None else size)
        return super().read(size)


@pytest.mark.parametrize("endian", ["<", ">"], ids=["little-endian", "big-endian"])
def test_parse_pcapng_honors_section_byte_order_and_default_microseconds(tmp_path: Path, endian: Endian) -> None:
    """Ignoring the SHB byte order or default resolution would corrupt packet timestamps."""
    frame = _ethernet_frame(_TARGET_BYTES, frame_length=18)
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, 1_250_000, endian), endian=endian))

    events = parse_pcapng(path, _metadata())

    assert events == (TraceEvent(timestamp=1.25, direction=Direction.OUTBOUND, frame_length=18),)


@pytest.mark.parametrize("endian", ["<", ">"], ids=["little-endian", "big-endian"])
def test_parse_pcapng_accepts_compatible_version_1_2_sections(tmp_path: Path, endian: Endian) -> None:
    """Rejecting minor version 2 would exclude the IETF-compatible current section format."""
    frame = _ethernet_frame(_TARGET_BYTES, frame_length=18)
    content = (
        _section_header(endian, version=(1, 2))
        + _interface_description(endian)
        + _enhanced_packet(frame, 1_250_000, endian)
    )
    path = _write_capture(tmp_path, content)

    assert parse_pcapng(path, _metadata()) == (
        TraceEvent(timestamp=1.25, direction=Direction.OUTBOUND, frame_length=18),
    )


@pytest.mark.parametrize("version", [(1, 1), (1, 3), (2, 0)], ids=["1.1", "1.3", "2.0"])
def test_parse_pcapng_rejects_unsupported_section_versions(tmp_path: Path, version: tuple[int, int]) -> None:
    """Accepting an unknown version could silently apply incompatible block semantics."""
    frame = _ethernet_frame(_TARGET_BYTES)
    content = _section_header(version=version) + _interface_description() + _enhanced_packet(frame, 0)
    path = _write_capture(tmp_path, content)

    with pytest.raises(TrafficlabError, match=rf"unsupported PCAPNG version {version[0]}\.{version[1]}"):
        parse_pcapng(path, _metadata())


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not!" + _section_header()[4:], "first block is not a Section Header Block"),
        (_section_header()[:8] + b"bad!" + _section_header()[12:], "invalid byte-order magic"),
    ],
    ids=["wrong-block-type", "wrong-byte-order-magic"],
)
def test_parse_pcapng_rejects_invalid_section_identifiers(tmp_path: Path, content: bytes, message: str) -> None:
    """Unknown section identity or byte order cannot safely frame any following blocks."""
    path = _write_capture(tmp_path, content)

    with pytest.raises(TrafficlabError, match=message):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_bytes_matches_the_path_boundary_without_reopening_it(tmp_path: Path) -> None:
    """A byte parser lets comparison evaluate the exact content used for its identity."""
    frame = _ethernet_frame(_TARGET_BYTES, frame_length=18)
    content = _capture(_enhanced_packet(frame, 1_250_000))
    source = tmp_path / "capture.pcapng"

    events = parse_pcapng_bytes(content, _metadata(), source=source)

    assert events == (TraceEvent(timestamp=1.25, direction=Direction.OUTBOUND, frame_length=18),)


def test_parse_pcapng_trace_round_trips_both_directions_at_declared_resolution(tmp_path: Path) -> None:
    """The scientific parser must retain the encoder's directions and nanosecond timestamps."""
    events = (
        TraceEvent(timestamp=0.25, direction=Direction.OUTBOUND, frame_length=18),
        TraceEvent(timestamp=1.5, direction=Direction.INBOUND, frame_length=22),
    )
    path = _write_capture(tmp_path, encode_pcapng(events, _metadata()))

    trace = parse_pcapng_trace(path, _metadata())

    assert isinstance(trace, TrafficTrace)
    assert trace.to_events() == events
    assert not trace.timestamps.flags.writeable
    assert encode_pcapng(trace.to_events(), _metadata()) == path.read_bytes()


@pytest.mark.parametrize(
    ("resolution", "ticks", "expected"),
    [(b"\x09", 1_250_000_000, 1.25), (b"\x8a", 1_536, 1.5)],
    ids=["decimal-nanoseconds", "binary-2^-10"],
)
def test_parse_pcapng_trace_reencodes_both_directions_at_declared_timestamp_resolution(
    tmp_path: Path, resolution: bytes, ticks: int, expected: float
) -> None:
    """Parsed resolution must survive trace/event conversion before canonical PCAPNG rendering."""
    idb = _interface_description(options=_option(9, resolution) + _end_options())
    frames = (
        _ethernet_frame(_TARGET_BYTES, frame_length=18),
        _ethernet_frame(_PEER_BYTES, destination=_TARGET_BYTES, frame_length=22),
    )
    source = _write_capture(
        tmp_path,
        _capture(
            _enhanced_packet(frames[0], ticks),
            _enhanced_packet(frames[1], ticks, interface_id=0),
            idb=idb,
        ),
    )

    trace = parse_pcapng_trace(source, _metadata())
    rendered = encode_pcapng(trace.to_events(), _metadata())
    reparsed = parse_pcapng_bytes(rendered, _metadata(), source=tmp_path / "reencoded.pcapng")

    assert reparsed == (
        TraceEvent(timestamp=expected, direction=Direction.OUTBOUND, frame_length=18),
        TraceEvent(timestamp=expected, direction=Direction.INBOUND, frame_length=22),
    )


def test_parse_pcapng_packets_returns_immutable_events_with_exact_ethernet_bytes(tmp_path: Path) -> None:
    """Discarding packet bytes would force capture inspection to parse the PCAPNG twice."""
    frame = _ethernet_frame(_TARGET_BYTES, destination=_PEER_BYTES, frame_length=18)
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, 1_250_000)))

    packets = parse_pcapng_packets(path, _metadata())

    assert packets == (
        PacketObservation(
            event=TraceEvent(timestamp=1.25, direction=Direction.OUTBOUND, frame_length=18),
            ethernet_frame=frame,
        ),
    )
    with pytest.raises(AttributeError):
        packets[0].ethernet_frame = b"changed"  # type: ignore[misc]


def test_parse_pcapng_projects_packet_observations_to_canonical_events(tmp_path: Path) -> None:
    """Divergent parser paths could classify or timestamp the same frame differently."""
    frames = (
        _ethernet_frame(_TARGET_BYTES, frame_length=18),
        _ethernet_frame(_PEER_BYTES, destination=_TARGET_BYTES, frame_length=22),
    )
    path = _write_capture(
        tmp_path,
        _capture(*(_enhanced_packet(frame, index * 1_000_000) for index, frame in enumerate(frames))),
    )

    packets = parse_pcapng_packets(path, _metadata())

    assert parse_pcapng(path, _metadata()) == tuple(packet.event for packet in packets)


def test_parse_pcapng_packets_deadline_aborts_before_reading_the_next_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expiry after frame one must prevent frame two from being accepted or even read."""
    first_frame = _ethernet_frame(_TARGET_BYTES)
    first_packet = _enhanced_packet(first_frame, 0)
    second_packet = _enhanced_packet(_ethernet_frame(_PEER_BYTES), 1)
    content = _capture(first_packet, second_packet)
    first_packet_end = len(_section_header()) + len(_interface_description()) + len(first_packet)
    now = [0.0]

    class DeadlineReader(_ObservedReader):
        last_position = 0

        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            self.last_position = self.tell()
            if self.last_position >= first_packet_end:
                now[0] = 1.0
            return chunk

    reader = DeadlineReader(content)
    path = tmp_path / "deadline.pcapng"

    def observed_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> _ObservedReader:
        assert self == path
        assert mode == "rb"
        return reader

    monkeypatch.setattr(Path, "open", observed_open)

    with pytest.raises(TrafficlabError, match="deadline"):
        parse_pcapng_packets(path, _metadata(), deadline=1.0, clock=lambda: now[0])

    assert reader.last_position == first_packet_end


def test_parse_pcapng_bytes_checks_the_deadline_before_parsing(tmp_path: Path) -> None:
    """The in-memory boundary must retain the path parser's pre-work deadline contract."""
    source = tmp_path / "capture.pcapng"

    with pytest.raises(TrafficlabError, match="deadline"):
        parse_pcapng_bytes(b"not parsed", _metadata(), source=source, deadline=1.0, clock=lambda: 1.0)


@pytest.mark.parametrize(
    ("resolution", "ticks", "expected"),
    [(b"\x09", 1_234_567_890, 1.23456789), (b"\x8a", 1_536, 1.5)],
    ids=["decimal-nanoseconds", "binary-2^-10"],
)
def test_parse_pcapng_honors_decimal_and_binary_timestamp_resolution(
    tmp_path: Path, resolution: bytes, ticks: int, expected: float
) -> None:
    """Treating every IDB timestamp as microseconds would move packets in time."""
    options = _option(9, resolution) + _end_options()
    idb = _interface_description(options=options)
    frame = _ethernet_frame(_TARGET_BYTES)
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, ticks), idb=idb))

    events = parse_pcapng(path, _metadata())

    assert events[0].timestamp == expected


def test_parse_pcapng_accepts_epb_packet_padding_options_and_original_length(tmp_path: Path) -> None:
    """Mistaking EPB padding or options for captured bytes would change the canonical frame length."""
    frame = _ethernet_frame(_TARGET_BYTES)
    options = _option(1, b"note") + _end_options()
    packet = _enhanced_packet(frame, 7, captured_length=14, original_length=1500, options=options)
    path = _write_capture(tmp_path, _capture(packet))

    events = parse_pcapng(path, _metadata())

    assert events == (TraceEvent(timestamp=0.000007, direction=Direction.OUTBOUND, frame_length=14),)


def test_parse_pcapng_accepts_option_lists_that_end_at_the_block_boundary(tmp_path: Path) -> None:
    """Requiring opt_endofopt would reject readers-compatible captures allowed by PCAPNG."""
    frame = _ethernet_frame(_TARGET_BYTES)
    idb = _interface_description(options=_option(9, b"\x09"))
    packet = _enhanced_packet(frame, 1_000_000_000, options=_option(1, b"note"))
    path = _write_capture(tmp_path, _capture(packet, idb=idb))

    assert parse_pcapng(path, _metadata()) == (
        TraceEvent(timestamp=1.0, direction=Direction.OUTBOUND, frame_length=14),
    )


@pytest.mark.parametrize("context", ["IDB", "EPB"])
def test_parse_pcapng_rejects_nonzero_option_padding(tmp_path: Path, context: str) -> None:
    """Accepting nonzero option padding would violate PCAPNG's zero-fill alignment rule."""
    frame = _ethernet_frame(_TARGET_BYTES)
    if context == "IDB":
        idb = _interface_description(options=_option_with_nonzero_padding(9, b"\x09") + _end_options())
        packet = _enhanced_packet(frame, 0)
    else:
        idb = _interface_description()
        packet = _enhanced_packet(frame, 0, options=_option_with_nonzero_padding(1, b"x") + _end_options())
    path = _write_capture(tmp_path, _capture(packet, idb=idb))

    with pytest.raises(TrafficlabError, match=f"{context} option padding"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_nonzero_epb_packet_padding(tmp_path: Path) -> None:
    """Accepting nonzero packet padding would treat a malformed EPB as reliable capture data."""
    frame = _ethernet_frame(_TARGET_BYTES)
    packet = bytearray(_enhanced_packet(frame, 0))
    packet[8 + 20 + len(frame)] = 1
    path = _write_capture(tmp_path, _capture(bytes(packet)))

    with pytest.raises(TrafficlabError, match="EPB packet padding"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_accepts_a_packet_truncated_to_the_declared_snaplen(tmp_path: Path) -> None:
    """Rejecting a valid snaplen truncation would make ordinary bounded captures unusable."""
    frame = _ethernet_frame(_TARGET_BYTES)
    idb = _interface_description(snap_len=14)
    packet = _enhanced_packet(frame, 0, captured_length=14, original_length=1514)
    path = _write_capture(tmp_path, _capture(packet, idb=idb))

    assert parse_pcapng(path, _metadata()) == (
        TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=14),
    )


def test_parse_pcapng_classifies_target_peer_and_broadcast_sources(tmp_path: Path) -> None:
    """Classifying by destination or peer identity would invert inbound traffic."""
    frames = (
        _ethernet_frame(_TARGET_BYTES),
        _ethernet_frame(_PEER_BYTES, destination=_TARGET_BYTES),
        _ethernet_frame(_BROADCAST_BYTES, destination=_TARGET_BYTES),
    )
    packets = tuple(_enhanced_packet(frame, index) for index, frame in enumerate(frames))
    path = _write_capture(tmp_path, _capture(*packets))

    events = parse_pcapng(path, _metadata())

    assert tuple(event.direction for event in events) == (
        Direction.OUTBOUND,
        Direction.INBOUND,
        Direction.INBOUND,
    )


def test_parse_pcapng_skips_a_well_formed_nonpacket_block(tmp_path: Path) -> None:
    """Rejecting harmless metadata blocks would exceed the documented minimal subset."""
    frame = _ethernet_frame(_TARGET_BYTES)
    path = _write_capture(tmp_path, _capture(_block(0x00000BAD, b"metadata"), _enhanced_packet(frame, 0)))

    assert len(parse_pcapng(path, _metadata())) == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (_section_header() + struct.pack("<II", 1, 14) + b"\x00" * 6, "four-byte aligned"),
        (_section_header() + _interface_description()[:-1], "truncated block body"),
        (_section_header() + _interface_description() + b"\x06\x00\x00", "truncated block header"),
    ],
    ids=["unaligned-length", "truncated-body", "truncated-header"],
)
def test_parse_pcapng_rejects_invalid_or_truncated_blocks(tmp_path: Path, content: bytes, message: str) -> None:
    """Trusting block framing would let malformed input desynchronize the stream."""
    path = _write_capture(tmp_path, content)

    with pytest.raises(TrafficlabError, match=message) as error:
        parse_pcapng(path, _metadata())

    assert error.value.corrective_action == "replace the PCAPNG with a complete valid Ethernet capture"


def test_parse_pcapng_rejects_a_mismatched_trailing_block_length(tmp_path: Path) -> None:
    """Ignoring the repeated length would miss a corrupted block boundary."""
    frame = _ethernet_frame(_TARGET_BYTES)
    packet = bytearray(_enhanced_packet(frame, 0))
    packet[-4:] = struct.pack("<I", len(packet) + 4)
    path = _write_capture(tmp_path, _capture(bytes(packet)))

    with pytest.raises(TrafficlabError, match="trailing block length"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_a_frame_shorter_than_its_captured_length(tmp_path: Path) -> None:
    """Reading past the EPB packet bytes would reinterpret absent data as a frame."""
    frame = _ethernet_frame(_TARGET_BYTES)
    packet = _enhanced_packet(frame, 0, captured_length=20, original_length=20, packet_data=frame)
    path = _write_capture(tmp_path, _capture(packet))

    with pytest.raises(TrafficlabError, match="truncated Ethernet frame"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_packets_rejects_physical_eof_inside_a_declared_frame(tmp_path: Path) -> None:
    """A partial frame read must not produce an observation with invented trailing bytes."""
    frame = _ethernet_frame(_TARGET_BYTES, frame_length=70_000)
    content = _capture(_enhanced_packet(frame, 0))
    path = _write_capture(tmp_path, content[:-20_000])

    with pytest.raises(TrafficlabError, match="truncated Ethernet frame"):
        parse_pcapng_packets(path, _metadata())


@pytest.mark.parametrize(
    ("idb", "message"),
    [
        (_interface_description(link_type=101), "unsupported link type"),
        (_interface_description(options=_option(9, b"\x09\x00") + _end_options()), "if_tsresol"),
        (
            _interface_description(options=_option(9, b"\x09") + _option(9, b"\x06") + _end_options()),
            "if_tsresol",
        ),
    ],
    ids=["unsupported-link-type", "invalid-tsresol-length", "duplicate-tsresol"],
)
def test_parse_pcapng_rejects_an_invalid_interface_description(tmp_path: Path, idb: bytes, message: str) -> None:
    """An ambiguous interface definition cannot produce a canonical Ethernet trace."""
    frame = _ethernet_frame(_TARGET_BYTES)
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, 0), idb=idb))

    with pytest.raises(TrafficlabError, match=message):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_multiple_interfaces(tmp_path: Path) -> None:
    """Accepting another IDB would make interface IDs and target metadata ambiguous."""
    frame = _ethernet_frame(_TARGET_BYTES)
    content = _capture(_interface_description(), _enhanced_packet(frame, 0))
    path = _write_capture(tmp_path, content)

    with pytest.raises(TrafficlabError, match="multiple interfaces"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_packet_before_interface_description(tmp_path: Path) -> None:
    """Accepting a packet without interface semantics would make its link type ambiguous."""
    content = _section_header() + _enhanced_packet(_ethernet_frame(_TARGET_BYTES), 0)
    path = _write_capture(tmp_path, content)

    with pytest.raises(TrafficlabError, match="before an Interface Description Block"):
        parse_pcapng_packets(path, _metadata())


def test_parse_pcapng_rejects_a_section_with_no_interface(tmp_path: Path) -> None:
    """A section without an interface cannot establish Ethernet parsing semantics."""
    path = _write_capture(tmp_path, _section_header())

    with pytest.raises(TrafficlabError, match="no Interface Description Block"):
        parse_pcapng_packets(path, _metadata())


@pytest.mark.parametrize(
    ("packet", "message"),
    [
        (_enhanced_packet(_ethernet_frame(_TARGET_BYTES), 0, interface_id=1), "interface ID"),
        (_enhanced_packet(_ethernet_frame(_TARGET_BYTES), 0, captured_length=15, original_length=14), "original"),
        (_enhanced_packet(_ethernet_frame(_TARGET_BYTES), 0, captured_length=13, original_length=14), "at least 14"),
    ],
    ids=["unknown-interface", "captured-above-original", "short-ethernet-frame"],
)
def test_parse_pcapng_rejects_invalid_epb_length_or_interface_fields(
    tmp_path: Path, packet: bytes, message: str
) -> None:
    """Invalid EPB fields cannot describe one complete captured Ethernet header."""
    path = _write_capture(tmp_path, _capture(packet))

    with pytest.raises(TrafficlabError, match=message):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_captured_length_above_nonzero_snaplen(tmp_path: Path) -> None:
    """Ignoring a nonzero snaplen would accept a self-contradictory capture."""
    frame = _ethernet_frame(_TARGET_BYTES, frame_length=15)
    path = _write_capture(
        tmp_path,
        _capture(_enhanced_packet(frame, 0), idb=_interface_description(snap_len=14)),
    )

    with pytest.raises(TrafficlabError, match="SnapLen"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_decreasing_timestamps(tmp_path: Path) -> None:
    """A decreasing canonical trace would invalidate every inter-arrival calculation."""
    frame = _ethernet_frame(_TARGET_BYTES)
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, 2), _enhanced_packet(frame, 1)))

    with pytest.raises(TrafficlabError, match="nondecreasing"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_malformed_epb_options(tmp_path: Path) -> None:
    """Trusting an option length could read beyond the EPB boundary."""
    frame = _ethernet_frame(_TARGET_BYTES)
    malformed_options = struct.pack("<HH", 1, 8) + b"only"
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, 0, options=malformed_options)))

    with pytest.raises(TrafficlabError, match="EPB options"):
        parse_pcapng(path, _metadata())


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ((_block(3, struct.pack("<I", 14) + b"\x00" * 16),), "Simple Packet Block"),
        (
            (
                _enhanced_packet(_ethernet_frame(_TARGET_BYTES), 0),
                _block(3, struct.pack("<I", 14) + b"\x00" * 16),
            ),
            "Simple Packet Block",
        ),
        ((_block(2, b"\x00" * 20),), "obsolete Packet Block"),
    ],
    ids=["all-spb", "mixed-epb-spb", "obsolete-packet-block"],
)
def test_parse_pcapng_rejects_packet_blocks_it_cannot_decode(
    tmp_path: Path, blocks: tuple[bytes, ...], message: str
) -> None:
    """Skipping any packet-bearing block would silently omit captured traffic."""
    path = _write_capture(tmp_path, _capture(*blocks))

    with pytest.raises(TrafficlabError, match=message):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_rejects_a_second_section(tmp_path: Path) -> None:
    """A second section could silently change byte order and interface semantics."""
    frame = _ethernet_frame(_TARGET_BYTES)
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, 0)) + _section_header())

    with pytest.raises(TrafficlabError, match="multiple sections"):
        parse_pcapng(path, _metadata())


def test_parse_pcapng_checks_the_deadline_before_reading(tmp_path: Path) -> None:
    """Starting I/O after expiry would violate the total-run budget."""
    path = tmp_path / "missing.pcapng"

    with pytest.raises(DeadlineExceededError, match="deadline") as error:
        parse_pcapng(path, _metadata(), deadline=1.0, clock=lambda: 1.0)

    assert error.value.corrective_action == "increase the total run timeout and retry capture"


def test_parse_pcapng_checks_the_deadline_after_each_accepted_frame(tmp_path: Path) -> None:
    """Waiting until EOF to check expiry would accept traffic beyond the run budget."""
    frame = _ethernet_frame(_TARGET_BYTES)
    malformed_second = _enhanced_packet(frame, 1, interface_id=1)
    path = _write_capture(tmp_path, _capture(_enhanced_packet(frame, 0), malformed_second))
    clock_values = iter((0.0, 0.0, 2.0))

    def clock() -> float:
        return next(clock_values)

    with pytest.raises(TrafficlabError, match="deadline"):
        parse_pcapng(path, _metadata(), deadline=1.0, clock=clock)


def test_parse_pcapng_counts_live_stream_read_time_toward_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Time spent reading the accepted frame must be visible to its immediate deadline check."""
    frame = _ethernet_frame(_TARGET_BYTES)
    first_packet = _enhanced_packet(frame, 0)
    content = _capture(first_packet, _enhanced_packet(frame, 1, interface_id=1))
    first_packet_end = len(_section_header()) + len(_interface_description()) + len(first_packet)
    now = [0.0]

    class ElapsedReader(_ObservedReader):
        last_position = 0

        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            self.last_position = self.tell()
            if self.last_position >= first_packet_end:
                now[0] = 1.0
            return chunk

    reader = ElapsedReader(content)
    path = tmp_path / "slow-read.pcapng"

    def observed_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> _ObservedReader:
        assert self == path
        assert mode == "rb"
        return reader

    monkeypatch.setattr(Path, "open", observed_open)

    with pytest.raises(TrafficlabError, match="deadline"):
        parse_pcapng(path, _metadata(), deadline=1.0, clock=lambda: now[0])

    assert reader.last_position == first_packet_end


def test_parse_pcapng_streams_incremental_bounded_reads_from_the_open_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-file read would remove the bounded streaming path needed by capture validation."""
    frame = _ethernet_frame(_TARGET_BYTES, frame_length=70_000)
    reader = _ObservedReader(_capture(_enhanced_packet(frame, 0)))
    path = tmp_path / "observed.pcapng"

    def observed_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> _ObservedReader:
        assert self == path
        assert mode == "rb"
        return reader

    monkeypatch.setattr(Path, "open", observed_open)

    assert parse_pcapng(path, _metadata()) == (
        TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=70_000),
    )
    assert len(reader.read_sizes) > 1
    assert -1 not in reader.read_sizes
    assert max(reader.read_sizes) <= 64 * 1024


def test_parse_pcapng_rechecks_real_elapsed_time_after_open_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replaying the pre-open clock would let already-expired file work enter the parser."""
    frame = _ethernet_frame(_TARGET_BYTES)
    reader = _ObservedReader(_capture(_enhanced_packet(frame, 0)))
    path = tmp_path / "slow-open.pcapng"
    now = [0.0]

    def slow_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> _ObservedReader:
        assert self == path
        assert mode == "rb"
        now[0] = 1.0
        return reader

    monkeypatch.setattr(Path, "open", slow_open)

    with pytest.raises(TrafficlabError, match="deadline"):
        parse_pcapng(path, _metadata(), deadline=1.0, clock=lambda: now[0])

    assert reader.read_sizes == []


def test_parse_pcapng_translates_a_live_stream_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read failure after opening must retain the same artifact-specific remediation as an open failure."""
    frame = _ethernet_frame(_TARGET_BYTES)
    reader = _ObservedReader(_capture(_enhanced_packet(frame, 0)), fail_after_reads=3)
    path = tmp_path / "read-error.pcapng"

    def observed_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> _ObservedReader:
        assert self == path
        assert mode == "rb"
        return reader

    monkeypatch.setattr(Path, "open", observed_open)

    with pytest.raises(TrafficlabError, match="could not read PCAPNG.*live-stream read failure") as error:
        parse_pcapng(path, _metadata())

    assert error.value.corrective_action == "verify the PCAPNG exists and is readable"


def test_parse_pcapng_translates_file_errors(tmp_path: Path) -> None:
    """Leaking an OSError would omit the artifact-specific corrective action."""
    path = tmp_path / "missing.pcapng"

    with pytest.raises(TrafficlabError, match="could not read PCAPNG") as error:
        parse_pcapng(path, _metadata())

    assert error.value.corrective_action == "verify the PCAPNG exists and is readable"


def test_encode_pcapng_emits_stable_little_endian_bytes() -> None:
    """Nondeterministic renderer bytes would make generated artifacts irreproducible."""
    events = (TraceEvent(timestamp=1.5, direction=Direction.OUTBOUND, frame_length=14),)
    expected = bytes.fromhex(
        "0a0d0d0a 1c000000 4d3c2b1a 0100 0000 ffffffffffffffff 1c000000"
        "01000000 20000000 0100 0000 ffff0000"
        "0900 0100 09 000000 00000000 20000000"
        "06000000 30000000 00000000 00000000 002f6859 0e000000 0e000000"
        "020000000001 0242ac110002 0800 0000 30000000"
    )

    assert encode_pcapng(events, _metadata()) == expected


def test_encode_pcapng_uses_directional_addresses_and_the_collision_safe_peer() -> None:
    """Using one address layout for both directions would break round-trip classification."""
    target = "02:00:00:00:00:01"
    events = (
        TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=14),
        TraceEvent(timestamp=0.0, direction=Direction.INBOUND, frame_length=14),
    )

    rendered = encode_pcapng(events, _metadata(target))
    first_frame_offset = 28 + 32 + 28
    second_frame_offset = first_frame_offset + 48

    assert rendered[first_frame_offset : first_frame_offset + 12] == bytes.fromhex("020000000002020000000001")
    assert rendered[second_frame_offset : second_frame_offset + 12] == bytes.fromhex("020000000001020000000002")


def test_encode_and_parse_pcapng_round_trip_lengths_directions_and_nanoseconds(tmp_path: Path) -> None:
    """Losing canonical fields at the file boundary would change downstream model inputs."""
    events = (
        TraceEvent(timestamp=0.000000002, direction=Direction.OUTBOUND, frame_length=14),
        TraceEvent(timestamp=1.23456789, direction=Direction.INBOUND, frame_length=65),
    )
    path = _write_capture(tmp_path, encode_pcapng(events, _metadata()))

    assert parse_pcapng(path, _metadata()) == events


def test_encode_pcapng_rounds_to_the_nearest_integer_nanosecond(tmp_path: Path) -> None:
    """Truncating fractional nanoseconds would systematically bias generated timestamps."""
    event = TraceEvent(timestamp=0.0000000016, direction=Direction.OUTBOUND, frame_length=14)
    path = _write_capture(tmp_path, encode_pcapng((event,), _metadata()))

    assert parse_pcapng(path, _metadata())[0].timestamp == 0.000000002


def test_write_pcapng_writes_the_encoder_bytes(tmp_path: Path) -> None:
    """A divergent file writer would make in-memory and persisted artifacts disagree."""
    events = (TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=14),)
    path = tmp_path / "generated.pcapng"

    write_pcapng(path, events, _metadata())

    assert path.read_bytes() == encode_pcapng(events, _metadata())


def test_write_pcapng_translates_file_errors(tmp_path: Path) -> None:
    """Leaking write failures would omit the artifact-specific remediation."""
    event = TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=14)

    with pytest.raises(TrafficlabError, match="could not write PCAPNG") as error:
        write_pcapng(tmp_path, (event,), _metadata())

    assert error.value.corrective_action == "verify the PCAPNG destination is writable"


def test_encode_pcapng_rejects_an_empty_sequence() -> None:
    """An empty generated capture cannot satisfy the nonempty artifact contract."""
    with pytest.raises(TrafficlabError, match="at least one event"):
        encode_pcapng((), _metadata())


@pytest.mark.parametrize(
    ("event", "message"),
    [
        (TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=13), "at least 14"),
        (TraceEvent(timestamp=0.0, direction=Direction.OUTBOUND, frame_length=2**32), "32-bit"),
        (TraceEvent(timestamp=20_000_000_000.0, direction=Direction.OUTBOUND, frame_length=14), "64-bit"),
    ],
    ids=["short-frame", "length-overflow", "timestamp-overflow"],
)
def test_encode_pcapng_rejects_unrepresentable_events(event: TraceEvent, message: str) -> None:
    """Serializing an unrepresentable field would wrap or allocate an invalid block."""
    with pytest.raises(TrafficlabError, match=message):
        encode_pcapng((event,), _metadata())


def test_encode_pcapng_rejects_decreasing_timestamps() -> None:
    """Rendering a decreasing trace would produce a file the parser must reject."""
    events = (
        TraceEvent(timestamp=1.0, direction=Direction.OUTBOUND, frame_length=14),
        TraceEvent(timestamp=0.0, direction=Direction.INBOUND, frame_length=14),
    )

    with pytest.raises(TrafficlabError, match="nondecreasing"):
        encode_pcapng(events, _metadata())


def test_encode_pcapng_rejects_decreasing_timestamps_that_quantize_to_one_tick() -> None:
    """Comparing only rounded ticks would hide a decreasing sub-nanosecond trace."""
    events = (
        TraceEvent(timestamp=0.0000000014, direction=Direction.OUTBOUND, frame_length=14),
        TraceEvent(timestamp=0.0000000013, direction=Direction.INBOUND, frame_length=14),
    )

    with pytest.raises(TrafficlabError, match="nondecreasing"):
        encode_pcapng(events, _metadata())


def test_encode_pcapng_rejects_an_invalid_direction_member() -> None:
    """Falling back for an unknown direction would emit a falsely classified frame."""
    event = object.__new__(TraceEvent)
    object.__setattr__(event, "timestamp", 0.0)
    object.__setattr__(event, "direction", "sideways")
    object.__setattr__(event, "frame_length", 14)

    with pytest.raises(TrafficlabError, match="direction"):
        encode_pcapng((event,), _metadata())
