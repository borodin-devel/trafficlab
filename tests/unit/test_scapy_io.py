"""Production Scapy PCAPNG boundary contracts."""

from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path
from typing import Literal, SupportsFloat, cast

import numpy as np
import pytest

from tests.support.pcapng_oracle import oracle_trace
from trafficlab import scapy_io
from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.scapy_io import EncodedPcapng, encode_pcapng, read_pcapng, read_pcapng_bytes, read_pcapng_packets
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, load_capture_metadata

_REPOSITORY = Path(__file__).resolve().parents[2]
_DATA = _REPOSITORY / "examples" / "data"
_TARGET = "02:42:ac:11:00:02"
_TARGET_BYTES = bytes.fromhex("0242ac110002")
_PEER_BYTES = bytes.fromhex("020000000001")

type Endian = Literal["<", ">"]


def _block(block_type: int, body: bytes, endian: Endian) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    total = 12 + len(padded)
    return struct.pack(f"{endian}II", block_type, total) + padded + struct.pack(f"{endian}I", total)


def _option(code: int, value: bytes, endian: Endian) -> bytes:
    return struct.pack(f"{endian}HH", code, len(value)) + value + b"\x00" * (-len(value) % 4)


def _capture(*, endian: Endian, resolution: int = 9, interfaces: int = 1, linktype: int = 1) -> bytes:
    magic = 0x1A2B3C4D
    section = _block(0x0A0D0D0A, struct.pack(f"{endian}IHHq", magic, 1, 0, -1), endian)
    options = _option(9, bytes((resolution,)), endian) + struct.pack(f"{endian}HH", 0, 0)
    interface = _block(1, struct.pack(f"{endian}HHI", linktype, 0, 65_535) + options, endian)
    frames = (
        _PEER_BYTES + _TARGET_BYTES + b"\x08\x00" + b"\x00" * 46,
        _TARGET_BYTES + _PEER_BYTES + b"\x86\xdd" + b"\x00" * 64,
    )
    packets = b""
    for index, frame in enumerate(frames, start=1):
        body = struct.pack(f"{endian}IIIII", 0, 0, index, len(frame), len(frame)) + frame
        packets += _block(6, body, endian)
    return section + interface * interfaces + packets


def test_reader_returns_owned_trace_and_exact_frames() -> None:
    source = _DATA / "reference.pcapng"
    content = source.read_bytes()
    metadata = load_capture_metadata(_DATA / "capture.json")

    packets = read_pcapng_packets(BytesIO(content), metadata, source=source)
    from_bytes = read_pcapng_bytes(content, metadata, source=source)
    from_path = read_pcapng(source, metadata)
    expected = oracle_trace(content, metadata, source=source)

    assert from_bytes == expected
    assert from_path == expected
    assert tuple(packet.event for packet in packets) == expected.to_events()
    assert tuple(len(packet.ethernet_frame) for packet in packets) == tuple(expected.frame_lengths)
    assert from_bytes.timestamps.dtype == np.dtype(np.float64)
    assert not from_bytes.timestamps.flags.writeable


def test_reader_deadline_precedes_missing_path_io(tmp_path: Path) -> None:
    metadata = load_capture_metadata(_DATA / "capture.json")

    with pytest.raises(DeadlineExceededError, match="PCAPNG parsing exceeded"):
        read_pcapng(tmp_path / "missing.pcapng", metadata, deadline=1.0, clock=lambda: 1.0)


@pytest.mark.parametrize(("endian", "resolution"), [("<", 9), (">", 6), ("<", 0x8A)])
def test_reader_matches_independent_endian_resolution_and_direction_oracle(endian: Endian, resolution: int) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    content = _capture(endian=endian, resolution=resolution)

    actual = read_pcapng_bytes(content, metadata, source=Path("literal.pcapng"))
    expected = oracle_trace(content, metadata, source=Path("literal.pcapng"))

    assert actual == expected
    assert actual.directions.tolist() == [0, 1]
    assert actual.frame_lengths.tolist() == [60, 78]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (_capture(endian="<", interfaces=2), "interfaces; expected exactly one"),
        (_capture(endian="<", linktype=101), "unsupported link type 101"),
    ],
)
def test_reader_rejects_noncanonical_interface_contract(content: bytes, message: str) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)

    with pytest.raises(TrafficlabError, match=message):
        read_pcapng_bytes(content, metadata, source=Path("interfaces.pcapng"))


def test_reader_rejects_empty_or_wrong_typed_content() -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)

    with pytest.raises(TrafficlabError, match="could not decode|no packet records"):
        read_pcapng_bytes(b"", metadata, source=Path("empty.pcapng"))
    with pytest.raises(TypeError, match="content must be bytes"):
        read_pcapng_bytes(cast(bytes, bytearray()), metadata, source=Path("wrong.pcapng"))


class _Packet:
    wirelen = 60

    def __init__(self, *, time: object = 0.0, frame: bytes | None = None) -> None:
        self.time = cast(SupportsFloat, time)
        self.frame = frame or (_PEER_BYTES + _TARGET_BYTES + b"\x08\x00" + b"\x00" * 46)

    def __bytes__(self) -> bytes:
        return self.frame


class _Reader:
    interfaces: list[tuple[int, int, dict[str, object]]] = [(1, 65_535, {})]

    def __init__(self, packets: tuple[_Packet, ...] = (_Packet(),)) -> None:
        self.packets = iter(packets)

    def __enter__(self) -> _Reader:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    def read_packet(self, size: int = 65_535) -> _Packet:
        del size
        try:
            return next(self.packets)
        except StopIteration as error:
            raise EOFError from error


def _fake_reader_factory(source: object) -> _Reader:
    del source
    return _Reader()


def _fake_reader_boundary() -> tuple[object, type[float]]:
    return _fake_reader_factory, float


class _ReaderFactory:
    def __init__(self, reader: _Reader) -> None:
        self.reader = reader

    def __call__(self, source: object) -> _Reader:
        del source
        return self.reader


def _install_reader(monkeypatch: pytest.MonkeyPatch, reader: _Reader) -> None:
    factory = _ReaderFactory(reader)

    def boundary() -> tuple[object, type[float]]:
        return factory, float

    monkeypatch.setattr(scapy_io, "_reader_boundary", boundary)


def test_reader_checks_deadline_after_each_packet(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(scapy_io, "_reader_boundary", _fake_reader_boundary)

    with pytest.raises(DeadlineExceededError, match="PCAPNG parsing exceeded"):
        read_pcapng_bytes(
            b"unused",
            metadata,
            source=Path("deadline.pcapng"),
            deadline=1.0,
            clock=ticks.__next__,
        )


@pytest.mark.parametrize(
    ("packets", "message"),
    [
        ((_Packet(frame=b"x" * 13),), "frame length must be at least 14"),
        ((_Packet(time=None),), "no explicit timestamp"),
        ((_Packet(time=float("nan")),), "timestamp must be finite and nonnegative"),
        ((_Packet(time=-1.0),), "timestamp must be finite and nonnegative"),
        ((_Packet(time=1.0), _Packet(time=0.5)), "timestamps must be nondecreasing"),
        ((), "no packet records"),
    ],
)
def test_reader_normalizes_invalid_dynamic_packet_values(
    monkeypatch: pytest.MonkeyPatch, packets: tuple[_Packet, ...], message: str
) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    _install_reader(monkeypatch, _Reader(packets))

    with pytest.raises(TrafficlabError, match=message):
        read_pcapng_bytes(b"dynamic", metadata, source=Path("dynamic.pcapng"))


def test_reader_normalizes_missing_path_error(tmp_path: Path) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)

    with pytest.raises(TrafficlabError, match="could not read PCAPNG") as caught:
        read_pcapng(tmp_path / "missing.pcapng", metadata)
    assert caught.value.corrective_action == "verify the PCAPNG exists and is readable"


def test_encode_returns_exact_bytes_and_reparsed_authoritative_trace() -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    original = TrafficTrace.from_events(
        (
            TraceEvent(0.000000123, Direction.OUTBOUND, 64),
            TraceEvent(0.250000499, Direction.INBOUND, 78),
        )
    )

    encoded = encode_pcapng(original, metadata, observation_window_seconds=1.0)
    repeated = encode_pcapng(original, metadata, observation_window_seconds=1.0)

    assert isinstance(encoded, EncodedPcapng)
    assert encoded.content.startswith(b"\x0a\x0d\x0d\x0a")
    assert encoded.content == repeated.content
    assert encoded.trace == repeated.trace
    assert encoded.trace == read_pcapng_bytes(encoded.content, metadata, source=Path("generated.pcapng"))
    assert encoded.trace.directions.tolist() == [0, 1]
    assert encoded.trace.frame_lengths.tolist() == [64, 78]
    assert encoded.trace.timestamps.tolist() != original.timestamps.tolist()


@pytest.mark.parametrize(
    ("trace", "window", "expected_error", "message"),
    [
        (cast(TrafficTrace, object()), 1.0, TypeError, "trace must be a TrafficTrace"),
        (TrafficTrace.from_events(()), 1.0, TrafficlabError, "empty traffic trace"),
        (TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 13),)), 1.0, TrafficlabError, "at least 14"),
        (TrafficTrace.from_events((TraceEvent(1.1, Direction.OUTBOUND, 64),)), 1.0, TrafficlabError, "outside"),
        (
            TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 64),)),
            0.0,
            TrafficlabError,
            "finite positive",
        ),
    ],
)
def test_encode_rejects_invalid_frame_or_window(
    trace: TrafficTrace, window: float, expected_error: type[Exception], message: str
) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)

    with pytest.raises(expected_error, match=message):
        encode_pcapng(trace, metadata, observation_window_seconds=window)


@pytest.mark.parametrize(
    ("failure", "message"),
    [(OSError("disk"), "could not write PCAPNG"), (RuntimeError("dynamic"), "could not encode")],
)
def test_encode_normalizes_writer_failures(monkeypatch: pytest.MonkeyPatch, failure: Exception, message: str) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    trace = TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 64),))

    class FailingFactory:
        def __call__(self, filename: str) -> object:
            del filename
            raise failure

    def boundary() -> tuple[object, object]:
        return FailingFactory(), object()

    monkeypatch.setattr(scapy_io, "_writer_boundary", boundary)

    with pytest.raises(TrafficlabError, match=message):
        encode_pcapng(trace, metadata, observation_window_seconds=1.0)


def test_encode_normalizes_emitted_file_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    trace = TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 64),))

    def fail_read(path: Path) -> bytes:
        if path.name == "generated.pcapng":
            raise OSError("unreadable")
        return b""

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    with pytest.raises(TrafficlabError, match="could not read emitted PCAPNG"):
        encode_pcapng(trace, metadata, observation_window_seconds=1.0)


def _install_encoded_candidate(
    monkeypatch: pytest.MonkeyPatch,
    candidate: TrafficTrace,
    *,
    content: bytes = b"pcapng",
) -> None:
    def write(path: Path, *_args: object, **_kwargs: object) -> None:
        path.write_bytes(content)

    def read(*_args: object, **_kwargs: object) -> TrafficTrace:
        return candidate

    monkeypatch.setattr(scapy_io, "_write_scapy_path", write)
    monkeypatch.setattr(scapy_io, "read_pcapng_bytes", read)


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (TrafficTrace.from_events((TraceEvent(0.0, Direction.INBOUND, 64),)), "changed packet directions"),
        (TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 65),)), "changed frame lengths"),
        (
            TrafficTrace.from_events((TraceEvent(1.1, Direction.OUTBOUND, 64),)),
            "timestamp outside the closed observation window",
        ),
    ],
)
def test_encode_rejects_reparsed_semantic_drift(
    monkeypatch: pytest.MonkeyPatch, candidate: TrafficTrace, message: str
) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    trace = TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 64),))
    _install_encoded_candidate(monkeypatch, candidate)

    with pytest.raises(TrafficlabError, match=message):
        encode_pcapng(trace, metadata, observation_window_seconds=1.0)


def test_encode_rejects_empty_emitted_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = CaptureMetadata(interface="eth0", target_mac=_TARGET)
    trace = TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 64),))
    _install_encoded_candidate(monkeypatch, trace, content=b"")

    with pytest.raises(TrafficlabError, match="emitted an empty PCAPNG"):
        encode_pcapng(trace, metadata, observation_window_seconds=1.0)
