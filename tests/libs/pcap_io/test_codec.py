"""Tests for bounded supported-PCAPNG binary parsing."""

from __future__ import annotations

from io import BytesIO
from struct import pack

import pytest

from trafficlab.libs.pcap_io import (
    ETHERNET_LINKTYPE,
    MalformedPcapngError,
    PacketRecord,
    PcapLimitError,
    PcapPolicy,
    parse_pcapng,
)


def _block(block_type: int, body: bytes) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    length = 12 + len(padded)
    return pack("<II", block_type, length) + padded + pack("<I", length)


def _option(code: int, value: bytes) -> bytes:
    return pack("<HH", code, len(value)) + value + b"\x00" * (-len(value) % 4)


def _shb() -> bytes:
    return _block(0x0A0D0D0A, pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))


def _idb(*, resolution: int = 6, offset: int = 0) -> bytes:
    options = _option(9, bytes((resolution,))) + _option(14, pack("<q", offset))
    return _block(
        1, pack("<HHI", ETHERNET_LINKTYPE, 0, 256) + options + _option(0, b"")
    )


def _epb(
    interface: int, ticks: int, payload: bytes, original: int | None = None
) -> bytes:
    original_length = len(payload) if original is None else original
    return _block(
        6,
        pack(
            "<IIIII",
            interface,
            ticks >> 32,
            ticks & 0xFFFFFFFF,
            len(payload),
            original_length,
        )
        + payload
        + _option(0, b""),
    )


@pytest.mark.unit
def test_parse_preserves_multi_interface_packet_order_and_metadata() -> None:
    payload = (
        _shb()
        + _idb(resolution=6, offset=-2)
        + _idb(resolution=0x8A)
        + _epb(1, 7, b"two")
        + _epb(0, 3, b"one", 5)
    )

    interfaces, packets = parse_pcapng(BytesIO(payload), PcapPolicy(4096, 512, 4))

    assert tuple(interface.timestamp_resolution for interface in interfaces) == (
        1_000_000,
        1024,
    )
    assert interfaces[0].timestamp_offset == -2
    assert tuple(packet.data for packet in packets) == (b"two", b"one")
    assert packets[1].original_length == 5


@pytest.mark.unit
def test_parse_accepts_input_at_configured_byte_limit() -> None:
    payload = _shb() + _idb() + _epb(0, 1, b"one")

    interfaces, packets = parse_pcapng(
        BytesIO(payload), PcapPolicy(len(payload), len(payload), 4)
    )

    assert len(interfaces) == 1
    assert packets == (PacketRecord(0, 1, 3, 3, b"one"),)


@pytest.mark.unit
def test_parse_rejects_bytes_beyond_configured_input_limit() -> None:
    payload = _shb() + _idb() + _epb(0, 1, b"one")

    with pytest.raises(PcapLimitError):
        parse_pcapng(BytesIO(payload + b"x"), PcapPolicy(len(payload), len(payload), 4))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "error"),
    (
        pytest.param(b"not pcapng", MalformedPcapngError, id="magic"),
        pytest.param(_shb()[:-1], MalformedPcapngError, id="truncated"),
        pytest.param(
            _shb() + _idb() + _epb(2, 1, b"x"), MalformedPcapngError, id="interface"
        ),
        pytest.param(
            _shb() + _block(1, pack("<HHI", 127, 0, 1)),
            MalformedPcapngError,
            id="linktype",
        ),
        pytest.param(
            _shb() + _idb() + _epb(0, 1, b"x", 0), MalformedPcapngError, id="length"
        ),
    ),
)
def test_parse_rejects_malformed_supported_structure(
    payload: bytes, error: type[Exception]
) -> None:
    with pytest.raises(error):
        parse_pcapng(BytesIO(payload), PcapPolicy(4096, 512, 4))


@pytest.mark.unit
def test_parse_rejects_configured_size_and_packet_bounds() -> None:
    payload = _shb() + _idb() + _epb(0, 1, b"one") + _epb(0, 2, b"two")

    with pytest.raises(MalformedPcapngError):
        parse_pcapng(BytesIO(payload), PcapPolicy(100, 64, 4))
    with pytest.raises(MalformedPcapngError):
        parse_pcapng(BytesIO(payload), PcapPolicy(4096, 512, 1))
