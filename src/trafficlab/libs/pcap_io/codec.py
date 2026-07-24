"""Bounded parser for the supported little-endian PCAPNG subset."""

from __future__ import annotations

from struct import Struct
from typing import BinaryIO

from .errors import (
    InvalidPcapRecordError,
    MalformedPcapngError,
    PcapLimitError,
    UnsupportedLinkTypeError,
)
from .values import InterfaceRecord, PacketRecord, PcapPolicy

_HEADER = Struct("<II")
_TRAILER = Struct("<I")
_SHB = Struct("<IHHq")
_IDB = Struct("<HHI")
_EPB = Struct("<IIIII")
_OPTION = Struct("<HH")
_SHB_TYPE = 0x0A0D0D0A
_IDB_TYPE = 1
_EPB_TYPE = 6


class _LimitedReader:
    """Read exact bounded bytes from one untrusted binary source."""

    def __init__(self, source: BinaryIO, limit: int) -> None:
        self._source = source
        self._limit = limit
        self._consumed = 0

    def read_exact(self, size: int, *, allow_eof: bool = False) -> bytes:
        if size < 0:
            raise PcapLimitError("PCAPNG input exceeds configured byte limit")
        if self._consumed == self._limit and allow_eof:
            if self._source.read(1):
                raise PcapLimitError("PCAPNG input exceeds configured byte limit")
            return b""
        if self._consumed + size > self._limit:
            raise PcapLimitError("PCAPNG input exceeds configured byte limit")
        data = self._source.read(size)
        if not data and allow_eof:
            return data
        if len(data) != size:
            raise MalformedPcapngError("PCAPNG input is truncated")
        self._consumed += size
        return data


def _options(raw: bytes) -> dict[int, bytes]:
    options: dict[int, bytes] = {}
    cursor = 0
    ended = False
    while cursor < len(raw):
        if len(raw) - cursor < _OPTION.size:
            raise MalformedPcapngError("PCAPNG option header is truncated")
        code, length = _OPTION.unpack_from(raw, cursor)
        cursor += _OPTION.size
        padded_length = length + (-length % 4)
        if len(raw) - cursor < padded_length:
            raise MalformedPcapngError("PCAPNG option value is truncated")
        value = raw[cursor : cursor + length]
        cursor += padded_length
        if code == 0:
            if length != 0 or cursor != len(raw):
                raise MalformedPcapngError("PCAPNG option terminator is invalid")
            ended = True
            break
        if code in options:
            raise MalformedPcapngError("PCAPNG repeated metadata option is unsupported")
        options[code] = value
    if raw and not ended:
        raise MalformedPcapngError("PCAPNG options lack a terminator")
    return options


def _timestamp_resolution(options: dict[int, bytes]) -> int:
    raw = options.get(9, b"\x06")
    if len(raw) != 1:
        raise MalformedPcapngError("PCAPNG timestamp resolution is invalid")
    exponent = raw[0]
    base = 2 if exponent & 0x80 else 10
    power = exponent & 0x7F
    if power > 63:
        raise MalformedPcapngError("PCAPNG timestamp resolution is unsupported")
    return base**power


def _timestamp_offset(options: dict[int, bytes]) -> int:
    raw = options.get(14)
    if raw is None:
        return 0
    if len(raw) != 8:
        raise MalformedPcapngError("PCAPNG timestamp offset is invalid")
    return Struct("<q").unpack(raw)[0]


def _block(reader: _LimitedReader, policy: PcapPolicy) -> tuple[int, bytes] | None:
    header = reader.read_exact(_HEADER.size, allow_eof=True)
    if not header:
        return None
    block_type, total_length = _HEADER.unpack(header)
    if total_length < 12 or total_length > policy.max_block_bytes or total_length % 4:
        raise MalformedPcapngError("PCAPNG block length is invalid")
    body = reader.read_exact(total_length - 12)
    tail = _TRAILER.unpack(reader.read_exact(_TRAILER.size))[0]
    if tail != total_length:
        raise MalformedPcapngError("PCAPNG block length trailer disagrees")
    return block_type, body


def parse_pcapng(
    source: BinaryIO,
    policy: PcapPolicy,
) -> tuple[tuple[InterfaceRecord, ...], tuple[PacketRecord, ...]]:
    """Parse bounded supported PCAPNG records in their exact file order."""

    reader = _LimitedReader(source, policy.max_input_bytes)
    first = _block(reader, policy)
    if first is None or first[0] != _SHB_TYPE or len(first[1]) < _SHB.size:
        raise MalformedPcapngError("PCAPNG must begin with a valid section header")
    magic, major, minor, section_length = _SHB.unpack_from(first[1])
    if magic != 0x1A2B3C4D or major != 1 or minor != 0 or section_length != -1:
        raise MalformedPcapngError("PCAPNG section header is unsupported")
    _options(first[1][_SHB.size :])
    interfaces: list[InterfaceRecord] = []
    packets: list[PacketRecord] = []
    while (next_block := _block(reader, policy)) is not None:
        block_type, body = next_block
        if block_type == _IDB_TYPE:
            if len(body) < _IDB.size:
                raise MalformedPcapngError("PCAPNG interface block is truncated")
            linktype, reserved, snaplen = _IDB.unpack_from(body)
            if reserved != 0:
                raise MalformedPcapngError("PCAPNG interface reserved field is invalid")
            try:
                interfaces.append(
                    InterfaceRecord(
                        len(interfaces),
                        linktype,
                        snaplen,
                        _timestamp_resolution(_options(body[_IDB.size :])),
                        _timestamp_offset(_options(body[_IDB.size :])),
                    )
                )
            except (InvalidPcapRecordError, UnsupportedLinkTypeError) as error:
                raise MalformedPcapngError(
                    "PCAPNG interface metadata is unsupported"
                ) from error
        elif block_type == _EPB_TYPE:
            if len(body) < _EPB.size:
                raise MalformedPcapngError("PCAPNG packet block is truncated")
            interface_id, high, low, captured, original = _EPB.unpack_from(body)
            padded = captured + (-captured % 4)
            if interface_id >= len(interfaces) or len(body) < _EPB.size + padded:
                raise MalformedPcapngError("PCAPNG packet metadata is invalid")
            _options(body[_EPB.size + padded :])
            if len(packets) >= policy.max_packets:
                raise PcapLimitError("PCAPNG packet count exceeds configured limit")
            try:
                packets.append(
                    PacketRecord(
                        interface_id,
                        (high << 32) | low,
                        captured,
                        original,
                        body[_EPB.size : _EPB.size + captured],
                    )
                )
            except InvalidPcapRecordError as error:
                raise MalformedPcapngError(
                    "PCAPNG packet lengths are invalid"
                ) from error
        else:
            raise MalformedPcapngError("PCAPNG block type is unsupported")
    return tuple(interfaces), tuple(packets)
