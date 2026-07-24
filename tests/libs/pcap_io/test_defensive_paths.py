# pyright: reportArgumentType=false, reportPrivateUsage=false
"""Tests for explicit malformed and defensive supported-PCAPNG paths."""

from io import BytesIO
from struct import pack

import pytest

from trafficlab.libs.pcap_io import (
    ETHERNET_LINKTYPE,
    InterfaceRecord,
    InvalidPcapRecordError,
    MalformedPcapngError,
    PacketRecord,
    PcapLimitError,
    PcapPolicy,
    codec,
    service,
)


def _policy() -> PcapPolicy:
    return PcapPolicy(4096, 512, 8)


def _prefix() -> bytes:
    return service._block(
        0x0A0D0D0A, pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    ) + service._block(1, pack("<HHIHH", ETHERNET_LINKTYPE, 0, 64, 0, 0))


@pytest.mark.unit
def test_codec_defensive_helpers_reject_invalid_metadata() -> None:
    with pytest.raises(PcapLimitError):
        codec._LimitedReader(BytesIO(), 1).read_exact(-1)
    for raw in (b"x", pack("<HH", 0, 1), pack("<HHI", 1, 1, 0)):
        with pytest.raises(MalformedPcapngError):
            codec._options(raw)
    with pytest.raises(MalformedPcapngError):
        codec._options(pack("<HH", 1, 0) + pack("<HH", 1, 0))
    with pytest.raises(MalformedPcapngError):
        codec._options(pack("<HH", 0, 1) + b"x" + b"\x00" * 3)
    with pytest.raises(MalformedPcapngError):
        codec._timestamp_resolution({9: b""})
    with pytest.raises(MalformedPcapngError):
        codec._timestamp_resolution({9: b"\x7f"})
    with pytest.raises(MalformedPcapngError):
        codec._timestamp_offset({14: b"x"})


@pytest.mark.unit
def test_codec_rejects_remaining_malformed_blocks() -> None:
    reader = codec._LimitedReader(BytesIO(pack("<II", 1, 12) + pack("<I", 16)), 64)
    with pytest.raises(MalformedPcapngError):
        codec._block(reader, _policy())
    for payload in (
        b"",
        pack("<II", 0x0A0D0D0A, 12) + pack("<I", 12),
        service._block(0x0A0D0D0A, pack("<IHHq", 0, 1, 0, -1)),
        _prefix() + service._block(1, b"x"),
        _prefix() + service._block(1, pack("<HHIHH", ETHERNET_LINKTYPE, 1, 64, 0, 0)),
        _prefix() + service._block(0xDEADBEEF, b""),
        _prefix() + service._block(6, b"x"),
    ):
        with pytest.raises(MalformedPcapngError):
            codec.parse_pcapng(BytesIO(payload), _policy())


@pytest.mark.unit
def test_writer_and_value_defensive_paths() -> None:
    interface = InterfaceRecord(0, ETHERNET_LINKTYPE, 64, 1, 0)
    packet = PacketRecord(0, 0, 1, 1, b"x")
    with pytest.raises(ValueError):
        service.filter_packets((packet,), object())
    with pytest.raises(ValueError):
        service.filter_packets((object(),), lambda _: True)
    with pytest.raises(ValueError):
        service._resolution_option(3)
    with pytest.raises(ValueError):
        service.write_pcapng((interface,), (object(),), _policy())
    with pytest.raises(ValueError):
        service.write_pcapng((object(),), (), _policy())
    with pytest.raises(ValueError):
        service.write_pcapng(
            (InterfaceRecord(2, ETHERNET_LINKTYPE, 64, 1, 0),), (), _policy()
        )
    with pytest.raises(InvalidPcapRecordError):
        InterfaceRecord(0, ETHERNET_LINKTYPE, 1, 1, "wrong")
    with pytest.raises(InvalidPcapRecordError):
        PcapPolicy(1, 2, 1)
    with pytest.raises(PcapLimitError):
        service.write_pcapng((interface,), (packet, packet), PcapPolicy(4096, 512, 1))
