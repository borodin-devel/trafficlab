import ipaddress
import struct
from pathlib import Path

import pytest

import trafficlab.capture_validation as capture_validation_module
from trafficlab.capture_validation import inspect_capture
from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.pcapng import PacketObservation
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

_TARGET = bytes.fromhex("0242ac110002")
_PEER = bytes.fromhex("020000000001")
_BROADCAST = b"\xff" * 6


def _block(block_type: int, body: bytes) -> bytes:
    body += b"\x00" * (-len(body) % 4)
    total_length = 12 + len(body)
    return struct.pack("<II", block_type, total_length) + body + struct.pack("<I", total_length)


def _packet(frame: bytes, timestamp: int, *, interface_id: int = 0) -> bytes:
    body = struct.pack("<IIIII", interface_id, 0, timestamp, len(frame), len(frame))
    return _block(6, body + frame)


def _capture(*frames: bytes) -> bytes:
    section = _block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    interface = _block(1, struct.pack("<HHI", 1, 0, 65535))
    return section + interface + b"".join(_packet(frame, index) for index, frame in enumerate(frames))


def _ethernet(source: bytes, destination: bytes, ethertype: int, payload: bytes) -> bytes:
    return destination + source + struct.pack("!H", ethertype) + payload


def _ipv4(source: str, destination: str, protocol: int) -> bytes:
    return (
        b"\x45\x00"
        + struct.pack("!H", 20)
        + b"\x00\x00\x00\x00\x40"
        + bytes((protocol,))
        + b"\x00\x00"
        + ipaddress.IPv4Address(source).packed
        + ipaddress.IPv4Address(destination).packed
    )


def _ipv6(source: str, destination: str, protocol: int) -> bytes:
    return (
        b"\x60\x00\x00\x00"
        + b"\x00\x00"
        + bytes((protocol, 64))
        + ipaddress.IPv6Address(source).packed
        + ipaddress.IPv6Address(destination).packed
    )


def _arp(source: str, destination: str) -> bytes:
    return (
        struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
        + _PEER
        + ipaddress.IPv4Address(source).packed
        + _TARGET
        + ipaddress.IPv4Address(destination).packed
    )


def _write_pair(tmp_path: Path, *frames: bytes) -> tuple[Path, Path]:
    metadata_path = tmp_path / "capture.json"
    metadata_path.write_bytes(
        render_capture_metadata(CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02"))
    )
    pcapng_path = tmp_path / "reference.pcapng"
    pcapng_path.write_bytes(_capture(*frames))
    return metadata_path, pcapng_path


def test_inspect_capture_reports_ethernet_addresses_protocols_and_directions_in_one_result(tmp_path: Path) -> None:
    """Losing headers or treating non-IP traffic as invalid would hide real capture composition."""
    frames = (
        _ethernet(_TARGET, _PEER, 0x0800, _ipv4("192.0.2.10", "198.51.100.20", 6)),
        _ethernet(_PEER, _TARGET, 0x0800, _ipv4("198.51.100.20", "192.0.2.10", 17)),
        _ethernet(_PEER, _BROADCAST, 0x0806, _arp("198.51.100.1", "192.0.2.10")),
        _ethernet(_TARGET, _PEER, 0x86DD, _ipv6("2001:db8::1", "2001:db8::2", 6)),
        _ethernet(_PEER, _TARGET, 0x86DD, _ipv6("2001:db8::2", "2001:db8::1", 17)),
        _ethernet(_TARGET, _PEER, 0x0800, b"\x45\x00"),
        _ethernet(_PEER, _TARGET, 0x88B5, b"unknown"),
    )
    metadata_path, pcapng_path = _write_pair(tmp_path, *frames)

    inspection = inspect_capture(metadata_path, pcapng_path, deadline=None, clock=lambda: 0.0)

    assert inspection.packet_count == 7
    assert inspection.direction_counts == {Direction.OUTBOUND: 3, Direction.INBOUND: 4}
    assert inspection.source_mac_counts == {
        "02:42:ac:11:00:02": 3,
        "02:00:00:00:00:01": 4,
    }
    assert inspection.destination_mac_counts == {
        "02:00:00:00:00:01": 3,
        "02:42:ac:11:00:02": 3,
        "ff:ff:ff:ff:ff:ff": 1,
    }
    assert inspection.ethertype_counts == {0x0800: 3, 0x0806: 1, 0x86DD: 2, 0x88B5: 1}
    assert inspection.protocol_counts == {"tcp": 2, "udp": 2, "other": 3}
    assert inspection.source_address_counts == {
        "192.0.2.10": 1,
        "198.51.100.20": 1,
        "198.51.100.1": 1,
        "2001:db8::1": 1,
        "2001:db8::2": 1,
    }
    assert inspection.destination_address_counts == {
        "198.51.100.20": 1,
        "192.0.2.10": 2,
        "2001:db8::2": 1,
        "2001:db8::1": 1,
    }
    with pytest.raises(TypeError):
        inspection.protocol_counts["tcp"] = 0  # type: ignore[index]


def test_inspect_capture_accepts_a_nonempty_capture_with_only_one_direction(tmp_path: Path) -> None:
    """Requiring both directions would reject legitimate unidirectional workloads."""
    frame = _ethernet(_TARGET, _PEER, 0x0800, _ipv4("192.0.2.10", "198.51.100.20", 6))
    metadata_path, pcapng_path = _write_pair(tmp_path, frame)

    inspection = inspect_capture(metadata_path, pcapng_path, deadline=None, clock=lambda: 0.0)

    assert inspection.direction_counts == {Direction.OUTBOUND: 1, Direction.INBOUND: 0}


def test_inspect_capture_counts_every_unsupported_or_truncated_network_header_as_other(tmp_path: Path) -> None:
    """Malformed network payload variants are capture observations, not malformed Ethernet."""
    invalid_ipv4_headers = (
        b"\x65" + b"\x00" * 19,
        b"\x44\x00\x00\x10" + b"\x00" * 16,
        b"\x46\x00\x00\x18" + b"\x00" * 16,
        b"\x45\x00\x00\x40" + b"\x00" * 16,
    )
    invalid_ipv6_headers = (
        b"\x60" + b"\x00" * 8,
        b"\x50" + b"\x00" * 39,
        b"\x60\x00\x00\x00\x00\x01" + b"\x00" * 34,
    )
    invalid_arp_headers = (
        b"\x00" * 4,
        struct.pack("!HHBBH", 2, 0x0800, 6, 4, 1) + b"\x00" * 20,
        struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + b"\x00" * 8,
    )
    frames = tuple(
        _ethernet(_TARGET, _PEER, ethertype, payload)
        for ethertype, payloads in (
            (0x0800, invalid_ipv4_headers),
            (0x86DD, invalid_ipv6_headers),
            (0x0806, invalid_arp_headers),
        )
        for payload in payloads
    )
    metadata_path, pcapng_path = _write_pair(tmp_path, *frames)

    inspection = inspect_capture(metadata_path, pcapng_path, deadline=None, clock=lambda: 0.0)

    assert inspection.protocol_counts == {"tcp": 0, "udp": 0, "other": 10}
    assert inspection.source_address_counts == {}
    assert inspection.destination_address_counts == {}


def test_inspect_capture_rejects_strict_metadata_before_parsing_packets(tmp_path: Path) -> None:
    """Ignoring unknown capture metadata could classify packets using an ambiguous schema."""
    metadata_path, pcapng_path = _write_pair(tmp_path, _ethernet(_TARGET, _PEER, 0x88B5, b"x"))
    metadata_path.write_text(
        '{"interface":"eth0","target_mac":"02:42:ac:11:00:02","extra":true}',
        encoding="utf-8",
    )

    with pytest.raises(TrafficlabError, match="capture validation failed.*metadata") as error:
        inspect_capture(metadata_path, pcapng_path, deadline=None, clock=lambda: 0.0)

    assert error.value.corrective_action == "replace the capture output with a complete valid capture pair"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (_capture(), "no Enhanced Packet Blocks"),
        (_capture(b"too short"), "at least 14"),
        (_capture(_ethernet(_TARGET, _PEER, 0x0800, _ipv4("192.0.2.1", "192.0.2.2", 6)))[:-1], "truncated"),
    ],
    ids=["empty", "short-ethernet", "malformed-pcapng"],
)
def test_inspect_capture_translates_invalid_capture_data(tmp_path: Path, content: bytes, message: str) -> None:
    """Raw parser failures would omit the capture-stage recovery boundary."""
    metadata_path, pcapng_path = _write_pair(tmp_path, _ethernet(_TARGET, _PEER, 0x88B5, b"x"))
    pcapng_path.write_bytes(content)

    with pytest.raises(TrafficlabError, match=rf"capture validation failed.*{message}") as error:
        inspect_capture(metadata_path, pcapng_path, deadline=None, clock=lambda: 0.0)

    assert error.value.corrective_action == "replace the capture output with a complete valid capture pair"


def test_inspect_capture_shares_the_deadline_with_streaming_packet_parsing(tmp_path: Path) -> None:
    """Dropping the stage deadline would let malformed frame two outrank expiry after frame one."""
    first = _ethernet(_TARGET, _PEER, 0x88B5, b"one")
    metadata_path, pcapng_path = _write_pair(tmp_path, first)
    pcapng_path.write_bytes(_capture(first) + _packet(first, 2, interface_id=1))
    values = iter((0.0, 0.0, 0.0, 0.0, 2.0))

    with pytest.raises(DeadlineExceededError, match="capture validation failed.*deadline"):
        inspect_capture(metadata_path, pcapng_path, deadline=1.0, clock=lambda: next(values))


def test_inspect_capture_checks_deadline_before_metadata_io(tmp_path: Path) -> None:
    """An expired inspection must not replace its deadline with a missing-file error."""
    with pytest.raises(DeadlineExceededError, match="capture validation failed.*deadline"):
        inspect_capture(tmp_path / "missing.json", tmp_path / "missing.pcapng", deadline=1.0, clock=lambda: 1.0)


def test_inspect_capture_checks_deadline_after_metadata_before_pcapng_io(tmp_path: Path) -> None:
    """Metadata read time must consume the same deadline before opening PCAPNG."""
    metadata_path, _pcapng_path = _write_pair(tmp_path, _ethernet(_TARGET, _PEER, 0x88B5, b"x"))
    values = iter((0.0, 1.0))

    with pytest.raises(DeadlineExceededError, match="capture validation failed.*deadline"):
        inspect_capture(metadata_path, tmp_path / "missing.pcapng", deadline=1.0, clock=lambda: next(values))


def test_inspect_capture_checks_deadline_during_packet_aggregation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The aggregate pass must stop before processing the packet after deadline expiry."""
    frame = _ethernet(_TARGET, _PEER, 0x88B5, b"payload")
    metadata_path, pcapng_path = _write_pair(tmp_path, frame)
    packets = (
        PacketObservation(TraceEvent(0.0, Direction.OUTBOUND, len(frame)), frame),
        PacketObservation(TraceEvent(1.0, Direction.OUTBOUND, len(frame)), frame),
    )

    def parsed_packets(
        path: Path,
        metadata: CaptureMetadata,
        *,
        deadline: float | None,
        clock: object,
    ) -> tuple[PacketObservation, ...]:
        del path, metadata, deadline, clock
        return packets

    monkeypatch.setattr(capture_validation_module, "parse_pcapng_packets", parsed_packets)
    observed = 0
    original = capture_validation_module._network_observation  # pyright: ignore[reportPrivateUsage]

    def counted_network_observation(value: bytes) -> tuple[str | None, str | None, str]:
        nonlocal observed
        observed += 1
        return original(value)

    monkeypatch.setattr(capture_validation_module, "_network_observation", counted_network_observation)
    readings = iter((0.0, 0.0, 0.0, 1.0))

    with pytest.raises(DeadlineExceededError, match="capture validation failed.*deadline"):
        inspect_capture(metadata_path, pcapng_path, deadline=1.0, clock=lambda: next(readings))

    assert observed == 1
