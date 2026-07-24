"""Pure preservation filtering and canonical supported-PCAPNG rendering."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from struct import Struct
from typing import cast

from .errors import PcapLimitError
from .values import InterfaceRecord, PacketRecord, PcapPolicy

_BLOCK = Struct("<II")
_LENGTH = Struct("<I")
_SHB = Struct("<IHHq")
_IDB = Struct("<HHI")
_EPB = Struct("<IIIII")
_OPTION = Struct("<HH")


def filter_packets(
    packets: Iterable[PacketRecord],
    include: Callable[[PacketRecord], bool],
) -> tuple[PacketRecord, ...]:
    """Retain selected records exactly and in their incoming order."""

    if not callable(include):
        raise ValueError("PCAPNG filter must be callable")
    materialized = tuple(packets)
    if not all(
        isinstance(cast(object, packet), PacketRecord) for packet in materialized
    ):
        raise ValueError("PCAPNG filter requires PacketRecord values")
    return tuple(packet for packet in materialized if include(packet))


def _pad(value: bytes) -> bytes:
    return value + b"\x00" * (-len(value) % 4)


def _block(block_type: int, body: bytes) -> bytes:
    padded = _pad(body)
    total = 12 + len(padded)
    return _BLOCK.pack(block_type, total) + padded + _LENGTH.pack(total)


def _option(code: int, value: bytes) -> bytes:
    return _OPTION.pack(code, len(value)) + _pad(value)


def _resolution_option(resolution: int) -> bytes:
    for base, marker in ((10, 0), (2, 0x80)):
        value = 1
        for power in range(64):
            if value == resolution:
                return bytes((marker | power,))
            value *= base
    raise ValueError("timestamp resolution is not PCAPNG-encodable")


def _interface_block(interface: InterfaceRecord) -> bytes:
    options = (
        _option(9, _resolution_option(interface.timestamp_resolution))
        + _option(14, Struct("<q").pack(interface.timestamp_offset))
        + _option(0, b"")
    )
    return _block(1, _IDB.pack(interface.linktype, 0, interface.snaplen) + options)


def _packet_block(packet: PacketRecord) -> bytes:
    return _block(
        6,
        _EPB.pack(
            packet.interface_id,
            packet.timestamp_ticks >> 32,
            packet.timestamp_ticks & 0xFFFFFFFF,
            packet.captured_length,
            packet.original_length,
        )
        + packet.data
        + _option(0, b""),
    )


def write_pcapng(
    interfaces: Iterable[InterfaceRecord],
    packets: Iterable[PacketRecord],
    policy: PcapPolicy,
) -> bytes:
    """Render deterministic supported records without sorting or mutation."""

    interface_values = tuple(interfaces)
    packet_values = tuple(packets)
    if not all(
        isinstance(cast(object, interface), InterfaceRecord)
        for interface in interface_values
    ):
        raise ValueError("PCAPNG writer requires InterfaceRecord values")
    if not all(
        isinstance(cast(object, packet), PacketRecord) for packet in packet_values
    ):
        raise ValueError("PCAPNG writer requires PacketRecord values")
    if tuple(interface.identifier for interface in interface_values) != tuple(
        range(len(interface_values))
    ):
        raise ValueError("PCAPNG interface identifiers must be canonical")
    if len(packet_values) > policy.max_packets:
        raise PcapLimitError("PCAPNG packet count exceeds configured limit")
    if any(packet.interface_id >= len(interface_values) for packet in packet_values):
        raise ValueError("PCAPNG packet references an unknown interface")
    rendered = (
        _block(0x0A0D0D0A, _SHB.pack(0x1A2B3C4D, 1, 0, -1))
        + b"".join(_interface_block(interface) for interface in interface_values)
        + b"".join(_packet_block(packet) for packet in packet_values)
    )
    if len(rendered) > policy.max_input_bytes:
        raise PcapLimitError("PCAPNG output exceeds configured byte limit")
    return rendered
