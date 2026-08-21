"""Single-pass validation and inspection of captured Ethernet artifacts."""

import ipaddress
import struct
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from types import MappingProxyType

from trafficlab.common.errors import DeadlineExceededError, TrafficlabError
from trafficlab.common.scapy_io import PcapngPacket, read_pcapng_packets
from trafficlab.common.trace import CaptureMetadata, Direction, TrafficTrace, load_capture_metadata

_CAPTURE_ACTION = "replace the capture output with a complete valid capture pair"


@dataclass(frozen=True, slots=True)
class CaptureInspection:
    """Immutable packet observations and aggregate capture diagnostics."""

    metadata: CaptureMetadata
    packets: tuple[PcapngPacket, ...]
    trace: TrafficTrace
    packet_count: int
    direction_counts: Mapping[Direction, int]
    source_mac_counts: Mapping[str, int]
    destination_mac_counts: Mapping[str, int]
    ethertype_counts: Mapping[int, int]
    source_address_counts: Mapping[str, int]
    destination_address_counts: Mapping[str, int]
    protocol_counts: Mapping[str, int]


def _mac_text(value: bytes) -> str:
    return ":".join(f"{octet:02x}" for octet in value)


def _network_observation(frame: bytes) -> tuple[str | None, str | None, str]:
    ethertype = struct.unpack("!H", frame[12:14])[0]
    payload = frame[14:]
    if ethertype == 0x0800:
        if len(payload) < 20 or payload[0] >> 4 != 4:
            return None, None, "other"
        header_length = (payload[0] & 0x0F) * 4
        total_length = struct.unpack("!H", payload[2:4])[0]
        if (
            header_length < 20
            or len(payload) < header_length
            or total_length < header_length
            or total_length > len(payload)
        ):
            return None, None, "other"
        source = str(ipaddress.IPv4Address(payload[12:16]))
        destination = str(ipaddress.IPv4Address(payload[16:20]))
        return source, destination, {6: "tcp", 17: "udp"}.get(payload[9], "other")
    if ethertype == 0x86DD:
        if len(payload) < 40 or payload[0] >> 4 != 6:
            return None, None, "other"
        payload_length = struct.unpack("!H", payload[4:6])[0]
        if payload_length > len(payload) - 40:
            return None, None, "other"
        source = str(ipaddress.IPv6Address(payload[8:24]))
        destination = str(ipaddress.IPv6Address(payload[24:40]))
        return source, destination, {6: "tcp", 17: "udp"}.get(payload[6], "other")
    if ethertype == 0x0806:
        if len(payload) < 8:
            return None, None, "other"
        hardware_type, protocol_type, hardware_length, protocol_length, _operation = struct.unpack(
            "!HHBBH", payload[:8]
        )
        required_length = 8 + 2 * hardware_length + 2 * protocol_length
        if (
            hardware_type != 1
            or protocol_type != 0x0800
            or hardware_length != 6
            or protocol_length != 4
            or len(payload) < required_length
        ):
            return None, None, "other"
        source_offset = 8 + hardware_length
        destination_offset = source_offset + protocol_length + hardware_length
        source = str(ipaddress.IPv4Address(payload[source_offset : source_offset + protocol_length]))
        destination = str(ipaddress.IPv4Address(payload[destination_offset : destination_offset + protocol_length]))
        return source, destination, "other"
    return None, None, "other"


def _frozen_counts[K](counts: Mapping[K, int]) -> Mapping[K, int]:
    return MappingProxyType(dict(counts))


def _inspection_deadline(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() >= deadline:
        raise DeadlineExceededError(
            "capture inspection exceeded the total-run deadline",
            corrective_action="increase the total run timeout and retry capture",
        )


def _inspect(
    metadata: CaptureMetadata,
    packets: tuple[PcapngPacket, ...],
    *,
    deadline: float | None,
    clock: Callable[[], float],
) -> CaptureInspection:
    directions = {Direction.OUTBOUND: 0, Direction.INBOUND: 0}
    source_macs: defaultdict[str, int] = defaultdict(int)
    destination_macs: defaultdict[str, int] = defaultdict(int)
    ethertypes: defaultdict[int, int] = defaultdict(int)
    source_addresses: defaultdict[str, int] = defaultdict(int)
    destination_addresses: defaultdict[str, int] = defaultdict(int)
    protocols = {"tcp": 0, "udp": 0, "other": 0}

    for packet in packets:
        _inspection_deadline(deadline, clock)
        frame = packet.ethernet_frame
        directions[packet.event.direction] += 1
        source_macs[_mac_text(frame[6:12])] += 1
        destination_macs[_mac_text(frame[:6])] += 1
        ethertype = struct.unpack("!H", frame[12:14])[0]
        ethertypes[ethertype] += 1
        source_address, destination_address, protocol = _network_observation(frame)
        protocols[protocol] += 1
        if source_address is not None:
            source_addresses[source_address] += 1
        if destination_address is not None:
            destination_addresses[destination_address] += 1
        _inspection_deadline(deadline, clock)

    return CaptureInspection(
        metadata=metadata,
        packets=packets,
        trace=TrafficTrace.from_events(packet.event for packet in packets),
        packet_count=len(packets),
        direction_counts=_frozen_counts(directions),
        source_mac_counts=_frozen_counts(source_macs),
        destination_mac_counts=_frozen_counts(destination_macs),
        ethertype_counts=_frozen_counts(ethertypes),
        source_address_counts=_frozen_counts(source_addresses),
        destination_address_counts=_frozen_counts(destination_addresses),
        protocol_counts=_frozen_counts(protocols),
    )


def inspect_capture(
    metadata_path: Path,
    pcapng_path: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
) -> CaptureInspection:
    """Load strict metadata and inspect every packet under one shared deadline."""
    try:
        if deadline is not None and clock() >= deadline:
            raise DeadlineExceededError(
                "capture inspection exceeded the total-run deadline",
                corrective_action="increase the total run timeout and retry capture",
            )
        metadata = load_capture_metadata(metadata_path)
        if deadline is not None and clock() >= deadline:
            raise DeadlineExceededError(
                "capture inspection exceeded the total-run deadline",
                corrective_action="increase the total run timeout and retry capture",
            )
        packets = read_pcapng_packets(
            pcapng_path,
            metadata,
            source=pcapng_path,
            deadline=deadline,
            clock=clock,
        )
        return _inspect(metadata, packets, deadline=deadline, clock=clock)
    except DeadlineExceededError as error:
        raise DeadlineExceededError(
            f"capture validation failed: {error}",
            corrective_action=_CAPTURE_ACTION,
        ) from error
    except TrafficlabError as error:
        raise TrafficlabError(
            f"capture validation failed: {error}",
            corrective_action=_CAPTURE_ACTION,
        ) from error


def validate_capture_pair(
    metadata_path: Path,
    pcapng_path: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
) -> CaptureInspection:
    """Validate and return one complete reusable capture pair."""
    return inspect_capture(metadata_path, pcapng_path, deadline=deadline, clock=clock)
