"""Immutable supported PCAPNG metadata records and limits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from .errors import InvalidPcapRecordError, UnsupportedLinkTypeError

ETHERNET_LINKTYPE = 1


def _positive(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise InvalidPcapRecordError(f"{field} must be a positive integer")
    return value


def _nonnegative(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise InvalidPcapRecordError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class InterfaceRecord:
    """One supported Ethernet interface and exact timestamp interpretation."""

    identifier: int
    linktype: int
    snaplen: int
    timestamp_resolution: int
    timestamp_offset: int

    def __post_init__(self) -> None:
        _nonnegative(cast(object, self.identifier), "interface identifier")
        if self.linktype != ETHERNET_LINKTYPE:
            raise UnsupportedLinkTypeError("only Ethernet link type 1 is supported")
        _positive(cast(object, self.snaplen), "interface snaplen")
        _positive(cast(object, self.timestamp_resolution), "timestamp resolution")
        if type(cast(object, self.timestamp_offset)) is not int:
            raise InvalidPcapRecordError("timestamp offset must be an integer")


@dataclass(frozen=True, slots=True)
class PacketRecord:
    """One preserved Enhanced Packet observation in recorded file order."""

    interface_id: int
    timestamp_ticks: int
    captured_length: int
    original_length: int
    data: bytes

    def __post_init__(self) -> None:
        _nonnegative(cast(object, self.interface_id), "packet interface identifier")
        _nonnegative(cast(object, self.timestamp_ticks), "packet timestamp ticks")
        captured = _nonnegative(cast(object, self.captured_length), "captured length")
        original = _nonnegative(cast(object, self.original_length), "original length")
        if not isinstance(cast(object, self.data), bytes) or len(self.data) != captured:
            raise InvalidPcapRecordError(
                "captured length must equal packet byte length"
            )
        if captured > original:
            raise InvalidPcapRecordError(
                "captured length cannot exceed original length"
            )


@dataclass(frozen=True, slots=True)
class PcapPolicy:
    """Explicit bounded-memory limits for one PCAPNG operation."""

    max_input_bytes: int
    max_block_bytes: int
    max_packets: int

    def __post_init__(self) -> None:
        _positive(cast(object, self.max_input_bytes), "maximum input bytes")
        _positive(cast(object, self.max_block_bytes), "maximum block bytes")
        _positive(cast(object, self.max_packets), "maximum packets")
        if self.max_block_bytes > self.max_input_bytes:
            raise InvalidPcapRecordError(
                "maximum block bytes cannot exceed input bytes"
            )
