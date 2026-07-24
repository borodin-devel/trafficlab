"""Tests for immutable supported PCAPNG values and policies."""

from dataclasses import FrozenInstanceError

import pytest

from trafficlab.libs.pcap_io import (
    ETHERNET_LINKTYPE,
    InterfaceRecord,
    InvalidPcapRecordError,
    PacketRecord,
    PcapPolicy,
    UnsupportedLinkTypeError,
)


@pytest.mark.unit
def test_interface_and_packet_records_are_frozen_supported_values() -> None:
    interface = InterfaceRecord(
        identifier=0,
        linktype=ETHERNET_LINKTYPE,
        snaplen=262144,
        timestamp_resolution=1_000_000,
        timestamp_offset=-2,
    )
    packet = PacketRecord(
        interface_id=0,
        timestamp_ticks=3_000_000,
        captured_length=3,
        original_length=5,
        data=b"abc",
    )

    assert interface.timestamp_offset == -2
    assert packet.data == b"abc"
    assert not hasattr(packet, "__dict__")
    with pytest.raises(FrozenInstanceError):
        packet.interface_id = 1  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "error"),
    (
        pytest.param(
            lambda: InterfaceRecord(0, 127, 1, 1, 0),
            UnsupportedLinkTypeError,
            id="unsupported-linktype",
        ),
        pytest.param(
            lambda: InterfaceRecord(-1, ETHERNET_LINKTYPE, 1, 1, 0),
            InvalidPcapRecordError,
            id="negative-interface",
        ),
        pytest.param(
            lambda: InterfaceRecord(0, ETHERNET_LINKTYPE, 0, 1, 0),
            InvalidPcapRecordError,
            id="zero-snaplen",
        ),
        pytest.param(
            lambda: InterfaceRecord(0, ETHERNET_LINKTYPE, 1, 0, 0),
            InvalidPcapRecordError,
            id="zero-resolution",
        ),
        pytest.param(
            lambda: PacketRecord(0, 0, 4, 4, b"abc"),
            InvalidPcapRecordError,
            id="captured-data-disagree",
        ),
        pytest.param(
            lambda: PacketRecord(0, 0, 5, 4, b"abcde"),
            InvalidPcapRecordError,
            id="captured-exceeds-original",
        ),
    ),
)
def test_records_reject_invalid_metadata(
    factory: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        factory()  # type: ignore[operator]


@pytest.mark.unit
def test_policy_has_positive_bounded_limits() -> None:
    policy = PcapPolicy(max_input_bytes=1024, max_block_bytes=128, max_packets=3)

    assert policy.max_input_bytes == 1024
    with pytest.raises(InvalidPcapRecordError):
        PcapPolicy(max_input_bytes=0, max_block_bytes=1, max_packets=1)
