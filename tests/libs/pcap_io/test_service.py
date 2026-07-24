"""Tests for deterministic supported-PCAPNG preservation and writing."""

from io import BytesIO
from pathlib import Path

import pytest
from scapy.utils import RawPcapNgReader

from trafficlab.libs.pcap_io import (
    ETHERNET_LINKTYPE,
    InterfaceRecord,
    PacketRecord,
    PcapLimitError,
    PcapPolicy,
    filter_packets,
    parse_pcapng,
    write_pcapng,
)


def _records() -> tuple[tuple[InterfaceRecord, ...], tuple[PacketRecord, ...]]:
    return (
        (
            InterfaceRecord(0, ETHERNET_LINKTYPE, 256, 1_000_000, -1),
            InterfaceRecord(1, ETHERNET_LINKTYPE, 256, 1024, 0),
        ),
        (
            PacketRecord(1, 9, 3, 5, b"two"),
            PacketRecord(0, 2, 3, 3, b"one"),
        ),
    )


@pytest.mark.unit
def test_filter_preserves_retained_packet_values_and_order() -> None:
    _, packets = _records()

    retained = filter_packets(packets, lambda packet: packet.interface_id == 0)

    assert retained == (packets[1],)


@pytest.mark.unit
def test_writer_round_trips_exact_supported_metadata_and_is_deterministic() -> None:
    interfaces, packets = _records()
    policy = PcapPolicy(4096, 512, 8)

    rendered = write_pcapng(interfaces, packets, policy)

    assert rendered == write_pcapng(interfaces, packets, policy)
    assert parse_pcapng(BytesIO(rendered), policy) == (interfaces, packets)


@pytest.mark.unit
def test_writer_output_is_accepted_by_locked_scapy(tmp_path: Path) -> None:
    interfaces, packets = _records()
    path = tmp_path / "records.pcapng"
    path.write_bytes(write_pcapng(interfaces, packets, PcapPolicy(4096, 512, 8)))

    reader = RawPcapNgReader(str(path))
    try:
        decoded = tuple(reader)
    finally:
        reader.close()

    assert tuple(packet for packet, _ in decoded) == tuple(
        packet.data for packet in packets
    )


@pytest.mark.unit
def test_writer_rejects_unbound_interface_and_output_limit() -> None:
    interfaces, packets = _records()

    with pytest.raises(PcapLimitError):
        write_pcapng(interfaces, packets, PcapPolicy(64, 64, 8))
    with pytest.raises(ValueError):
        write_pcapng(interfaces[:1], packets, PcapPolicy(4096, 512, 8))
