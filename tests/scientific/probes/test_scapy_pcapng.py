"""Scientific gates for the development-only typed Scapy PCAPNG probe."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Literal, cast

import pytest

from tests.scientific.probes import scapy_pcapng as probe
from tests.scientific.probes.scapy_pcapng import (
    BENCHMARK_FRAME_SHAPES,
    BENCHMARK_REPETITIONS,
    DEADLINE_CASE_NAMES,
    DIFFERENTIAL_CASE_NAMES,
    GATE_NAMES,
    MALFORMED_CASE_NAMES,
    MAX_MATERIAL_RATIO,
    READER_CASE_NAMES,
    SCAPY_VERSION,
    WRITER_CASE_NAMES,
    BenchmarkComparison,
    BenchmarkSample,
    CaseEvidence,
    CaseResult,
    NormalizedOutcome,
    ProbeEvidence,
    benchmark_evidence_command,
    build_probe_evidence,
    decide_probe,
    derive_gates,
    read_with_scapy,
    render_probe_evidence,
    validate_probe_evidence,
    write_probe_evidence,
    write_with_scapy,
)
from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.pcapng import parse_pcapng_trace
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace

Endian = Literal["<", ">"]

_TARGET = "02:42:ac:11:00:02"
_TARGET_BYTES = bytes.fromhex("0242ac110002")
_PEER_BYTES = bytes.fromhex("020000000001")
_BROADCAST_BYTES = b"\xff" * 6


def _metadata() -> CaptureMetadata:
    return CaptureMetadata(interface="eth0", target_mac=_TARGET)


def _option(code: int, value: bytes, endian: Endian = "<") -> bytes:
    return struct.pack(f"{endian}HH", code, len(value)) + value + b"\x00" * (-len(value) % 4)


def _end_options(endian: Endian = "<") -> bytes:
    return struct.pack(f"{endian}HH", 0, 0)


def _block(block_type: int, body: bytes, endian: Endian = "<", *, trailer: int | None = None) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    total = 12 + len(padded)
    return (
        struct.pack(f"{endian}II", block_type, total)
        + padded
        + struct.pack(f"{endian}I", total if trailer is None else trailer)
    )


def _section(endian: Endian = "<") -> bytes:
    return _block(0x0A0D0D0A, struct.pack(f"{endian}IHHq", 0x1A2B3C4D, 1, 0, -1), endian)


def _interface(
    endian: Endian = "<",
    *,
    linktype: int = 1,
    snaplen: int = 0,
    options: bytes = b"",
) -> bytes:
    return _block(1, struct.pack(f"{endian}HHI", linktype, 0, snaplen) + options, endian)


def _packet(
    frame: bytes,
    ticks: int,
    endian: Endian = "<",
    *,
    interface_id: int = 0,
    original_length: int | None = None,
    options: bytes = b"",
) -> bytes:
    captured = len(frame)
    original = captured if original_length is None else original_length
    body = struct.pack(
        f"{endian}IIIII",
        interface_id,
        ticks >> 32,
        ticks & 0xFFFFFFFF,
        captured,
        original,
    )
    return _block(6, body + frame + b"\x00" * (-captured % 4) + options, endian)


def _ethernet(source: bytes, destination: bytes, ethertype: int, payload: bytes, frame_length: int) -> bytes:
    prefix = destination + source + ethertype.to_bytes(2, "big") + payload
    assert len(prefix) <= frame_length
    return prefix + b"\x00" * (frame_length - len(prefix))


def _protocol_frames() -> tuple[bytes, bytes, bytes]:
    ipv4 = bytes.fromhex("4500002e00004000401100000a0000010a000002")
    ipv6 = bytes.fromhex("600000000008114020010db800000000000000000000000120010db8000000000000000000000002")
    arp = (
        bytes.fromhex("0001080006040001")
        + _TARGET_BYTES
        + bytes.fromhex("0a000001")
        + b"\x00" * 6
        + bytes.fromhex("0a000002")
    )
    return (
        _ethernet(_TARGET_BYTES, _PEER_BYTES, 0x0800, ipv4, 60),
        _ethernet(_PEER_BYTES, _TARGET_BYTES, 0x86DD, ipv6, 78),
        _ethernet(_BROADCAST_BYTES, _TARGET_BYTES, 0x0806, arp, 64),
    )


def _capture(
    frames: tuple[bytes, ...],
    ticks: tuple[int, ...],
    *,
    endian: Endian = "<",
    resolution: bytes | None = None,
    packet_options: bytes = b"",
    original_lengths: tuple[int, ...] | None = None,
) -> bytes:
    idb_options = b"" if resolution is None else _option(9, resolution, endian) + _end_options(endian)
    originals = (None,) * len(frames) if original_lengths is None else original_lengths
    packets = b"".join(
        _packet(frame, tick, endian, original_length=original, options=packet_options)
        for frame, tick, original in zip(frames, ticks, originals, strict=True)
    )
    return _section(endian) + _interface(endian, options=idb_options) + packets


def _write(tmp_path: Path, content: bytes, name: str = "capture.pcapng") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_probe_policy_predeclares_exact_cases_shapes_thresholds_and_gates() -> None:
    """Changing policy after observing Scapy would invalidate the adoption decision."""
    assert SCAPY_VERSION == "2.7.0"
    assert READER_CASE_NAMES == (
        "ethernet_ipv4_ipv6_arp_little_endian_default_microseconds",
        "ethernet_ipv4_ipv6_arp_big_endian_decimal_nanoseconds",
        "binary_2^-10_timestamp_resolution",
        "epb_padding_options_original_length",
        "source_mac_outbound_peer_inbound_broadcast_inbound",
        "closed_observation_window_and_frame_validation",
    )
    assert WRITER_CASE_NAMES == (
        "scapy_writer_microsecond_trace_round_trip",
        "scapy_writer_nanosecond_trace_round_trip",
    )
    assert DIFFERENTIAL_CASE_NAMES == (*READER_CASE_NAMES, *WRITER_CASE_NAMES)
    assert MALFORMED_CASE_NAMES == (
        "truncated_section_header",
        "truncated_block_body",
        "bad_trailing_block_length",
        "multiple_interfaces",
        "unsupported_linktype",
        "unknown_interface_id",
        "simple_packet_block",
        "obsolete_packet_block",
        "nonzero_packet_padding",
        "nonzero_option_padding",
        "malformed_epb_options",
        "decreasing_timestamps",
    )
    assert DEADLINE_CASE_NAMES == (
        "expired_before_io",
        "expired_after_reader_setup",
        "expires_after_first_scapy_packet",
        "expired_after_candidate_postprocessing",
    )
    assert BENCHMARK_FRAME_SHAPES == {
        "frames_100000": {
            "frame_count": 100_000,
            "protocol_cycle": ["ethernet_ipv4_60", "ethernet_ipv6_78", "ethernet_arp_64"],
            "source_cycle": ["target", "peer", "broadcast"],
            "timestamp_step_nanoseconds": 1_000,
        },
        "frames_1000000": {
            "frame_count": 1_000_000,
            "protocol_cycle": ["ethernet_ipv4_60", "ethernet_ipv6_78", "ethernet_arp_64"],
            "source_cycle": ["target", "peer", "broadcast"],
            "timestamp_step_nanoseconds": 1_000,
        },
    }
    assert BENCHMARK_REPETITIONS == 5
    assert MAX_MATERIAL_RATIO == 1.5
    assert GATE_NAMES == (
        "reader_trace_equivalence",
        "writer_trace_equivalence",
        "malformed_failures",
        "deadline_semantics",
        "strict_typing",
        "benchmark_100000_time",
        "benchmark_100000_memory",
        "benchmark_1000000_time",
        "benchmark_1000000_memory",
        "license_compatibility",
    )
    policy = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False)).policy
    assert policy.benchmark_runner_command == (
        "scripts/run_bounded.sh",
        "--memory-high",
        "6G",
        "--memory-max",
        "8G",
        "--swap-max",
        "1G",
        "--wall-time",
        "15m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "python",
        "scripts/run_scientific_stack_probes.py",
        "--probe",
        "scapy",
    )


@pytest.mark.parametrize(
    ("endian", "resolution", "ticks", "expected_timestamps"),
    [
        ("<", None, (0, 1_250_000, 2_500_000), (0.0, 1.25, 2.5)),
        (">", b"\x09", (0, 1_234_567_890, 2_500_000_000), (0.0, 1.23456789, 2.5)),
    ],
    ids=["little-default-microseconds", "big-decimal-nanoseconds"],
)
def test_reader_matches_canonical_trace_for_protocols_endian_resolution_and_directions(
    tmp_path: Path,
    endian: Endian,
    resolution: bytes | None,
    ticks: tuple[int, int, int],
    expected_timestamps: tuple[float, float, float],
) -> None:
    """Wrong Scapy packet conversion would change a canonical trace column."""
    path = _write(tmp_path, _capture(_protocol_frames(), ticks, endian=endian, resolution=resolution))

    production = parse_pcapng_trace(path, _metadata())
    candidate = read_with_scapy(path, _metadata())

    assert candidate == production
    assert candidate.to_events() == (
        TraceEvent(expected_timestamps[0], Direction.OUTBOUND, 60),
        TraceEvent(expected_timestamps[1], Direction.INBOUND, 78),
        TraceEvent(expected_timestamps[2], Direction.INBOUND, 64),
    )


def test_reader_matches_binary_timestamp_padding_options_and_captured_lengths(tmp_path: Path) -> None:
    """Using wire length, packet padding, or options would corrupt captured frame semantics."""
    frame = _protocol_frames()[0][:-1]
    options = _option(1, b"note") + _end_options()
    content = _capture(
        (frame, frame),
        (1_536, 2_048),
        resolution=b"\x8a",
        packet_options=options,
        original_lengths=(1_500, 1_500),
    )
    path = _write(tmp_path, content)

    candidate = read_with_scapy(path, _metadata())

    assert candidate == parse_pcapng_trace(path, _metadata())
    assert candidate.to_events() == (
        TraceEvent(1.5, Direction.OUTBOUND, 59),
        TraceEvent(2.0, Direction.OUTBOUND, 59),
    )


def test_reader_and_writer_apply_closed_window_and_frame_validation(tmp_path: Path) -> None:
    """An open endpoint or undersized Ethernet frame would violate Trafficlab-owned policy."""
    trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(1.0, Direction.INBOUND, 78),
        )
    )
    destination = tmp_path / "scapy-output.pcapng"

    written = write_with_scapy(destination, trace, _metadata(), observation_window_seconds=1.0)

    assert written == trace
    assert read_with_scapy(destination, _metadata(), observation_window_seconds=1.0) == trace

    outside = TrafficTrace.from_events((*trace.to_events(), TraceEvent(1.000001, Direction.OUTBOUND, 64)))
    with pytest.raises(TrafficlabError, match="closed observation window"):
        write_with_scapy(tmp_path / "outside.pcapng", outside, _metadata(), observation_window_seconds=1.0)
    with pytest.raises(TrafficlabError, match="finite positive float"):
        read_with_scapy(destination, _metadata(), observation_window_seconds=0.0)
    undersized = TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 13),))
    with pytest.raises(TrafficlabError, match="at least 14"):
        write_with_scapy(tmp_path / "short.pcapng", undersized, _metadata(), observation_window_seconds=1.0)


def test_writer_rejects_silent_loss_of_nanosecond_trace_semantics(tmp_path: Path) -> None:
    """Scapy's public microsecond writer must not silently alter a valid nanosecond trace."""
    trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(0.000000001, Direction.INBOUND, 78),
        )
    )

    with pytest.raises(TrafficlabError, match="does not preserve the canonical TrafficTrace"):
        write_with_scapy(tmp_path / "nanosecond.pcapng", trace, _metadata(), observation_window_seconds=1.0)


def test_reader_rejects_a_trace_outside_the_declared_closed_window(tmp_path: Path) -> None:
    """The optional window gate must compare elapsed timestamps and retain the endpoint."""
    frames = (_protocol_frames()[0],) * 3
    path = _write(tmp_path, _capture(frames, (5_000_000, 6_000_000, 6_000_001)))

    with pytest.raises(TrafficlabError, match="closed observation window"):
        read_with_scapy(path, _metadata(), observation_window_seconds=1.0)


def _malformed_captures() -> tuple[tuple[str, bytes], ...]:
    frame = _protocol_frames()[0]
    valid_packet = _packet(frame, 1)
    nonzero_packet_padding = bytearray(_packet(frame[:-1], 1))
    nonzero_packet_padding[8 + 20 + len(frame) - 1] = 1
    nonzero_option = bytearray(_option(1, b"x") + _end_options())
    nonzero_option[4 + 1] = 1
    malformed_options = struct.pack("<HH", 1, 8) + b"only"
    simple = _block(3, struct.pack("<I", len(frame)) + frame)
    obsolete = _block(2, b"\x00" * 20)
    return (
        ("truncated_section_header", _section()[:20]),
        ("truncated_block_body", (_section() + _interface() + valid_packet)[:-8]),
        ("bad_trailing_block_length", _section() + _interface() + _packet(frame, 1)[:-4] + struct.pack("<I", 999)),
        ("multiple_interfaces", _section() + _interface() + _interface() + valid_packet),
        ("unsupported_linktype", _section() + _interface(linktype=101) + valid_packet),
        ("unknown_interface_id", _section() + _interface() + _packet(frame, 1, interface_id=1)),
        ("simple_packet_block", _section() + _interface() + simple),
        ("obsolete_packet_block", _section() + _interface() + obsolete),
        ("nonzero_packet_padding", _section() + _interface() + bytes(nonzero_packet_padding)),
        ("nonzero_option_padding", _section() + _interface() + _packet(frame, 1, options=bytes(nonzero_option))),
        ("malformed_epb_options", _section() + _interface() + _packet(frame, 1, options=malformed_options)),
        ("decreasing_timestamps", _section() + _interface() + _packet(frame, 2) + _packet(frame, 1)),
    )


@pytest.mark.parametrize(("name", "content"), _malformed_captures(), ids=MALFORMED_CASE_NAMES)
def test_reader_exposes_each_predeclared_malformed_difference(tmp_path: Path, name: str, content: bytes) -> None:
    """Candidate-visible policy rejects what it can observe and exposes every Scapy mismatch."""
    path = _write(tmp_path, content, f"{name}.pcapng")
    with pytest.raises(TrafficlabError):
        parse_pcapng_trace(path, _metadata())

    accepted_by_scapy = {
        "obsolete_packet_block",
        "nonzero_packet_padding",
        "nonzero_option_padding",
        "malformed_epb_options",
    }
    if name in accepted_by_scapy:
        assert len(read_with_scapy(path, _metadata())) == 1
    else:
        with pytest.raises(TrafficlabError):
            read_with_scapy(path, _metadata())


def test_reader_checks_deadline_before_validation_and_after_each_scapy_packet(tmp_path: Path) -> None:
    """Expiry after packet one must win before Scapy accepts packet two."""
    frames = (_protocol_frames()[0],) * 2
    path = _write(tmp_path, _capture(frames, (0, 1)))
    calls = [0]

    def clock() -> float:
        calls[0] += 1
        return 1.0 if calls[0] >= 6 else 0.0

    with pytest.raises(DeadlineExceededError, match="deadline") as error:
        read_with_scapy(path, _metadata(), deadline=1.0, clock=clock)

    assert error.value.corrective_action == "increase the total run timeout and retry capture"


def test_reader_checks_expired_deadline_before_touching_a_missing_path(tmp_path: Path) -> None:
    """No I/O may begin once the absolute deadline has already expired."""
    with pytest.raises(DeadlineExceededError, match="deadline"):
        read_with_scapy(tmp_path / "missing.pcapng", _metadata(), deadline=1.0, clock=lambda: 1.0)


def _passing_comparison(frame_count: int) -> BenchmarkComparison:
    exact_frame_count = cast(Literal[100_000, 1_000_000], frame_count)
    return BenchmarkComparison.model_validate(
        {
            "frame_count": frame_count,
            "warmup_runs_per_adapter": 1,
            "measured_runs_per_adapter": 5,
            "production": {
                "command": benchmark_evidence_command("production", exact_frame_count),
                "samples": [
                    {
                        "frame_count": frame_count,
                        "trace_digest": "a" * 64,
                        "input_sha256": "b" * 64,
                        "input_size_bytes": 9_600_048 if frame_count == 100_000 else 96_000_048,
                        "wall_seconds": value,
                        "peak_rss_kib": 100_000,
                    }
                    for value in (1.0, 1.2, 1.1, 1.3, 0.9)
                ],
                "median_wall_seconds": 1.1,
                "median_peak_rss_kib": 100_000,
            },
            "scapy": {
                "command": benchmark_evidence_command("scapy", exact_frame_count),
                "samples": [
                    {
                        "frame_count": frame_count,
                        "trace_digest": "a" * 64,
                        "input_sha256": "b" * 64,
                        "input_size_bytes": 9_600_048 if frame_count == 100_000 else 96_000_048,
                        "wall_seconds": value,
                        "peak_rss_kib": 140_000,
                    }
                    for value in (1.3, 1.4, 1.2, 1.5, 1.1)
                ],
                "median_wall_seconds": 1.3,
                "median_peak_rss_kib": 140_000,
            },
            "trace_identity": True,
            "wall_ratio": 1.3 / 1.1,
            "rss_ratio": 1.4,
            "material_threshold_ratio": 1.5,
            "time_passed": True,
            "memory_passed": True,
        }
    )


def test_gate_derivation_is_fail_closed_for_measurements_typing_and_license() -> None:
    """Missing, malformed, or threshold-equal evidence must never authorize production adoption."""
    evidence = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False))
    passing_benchmarks = (_passing_comparison(100_000), _passing_comparison(1_000_000))
    technical = evidence.model_copy(
        update={
            "reader_differential": evidence.reader_differential.model_copy(update={"passed": True}),
            "writer_differential": evidence.writer_differential.model_copy(update={"passed": True}),
            "malformed": evidence.malformed.model_copy(update={"passed": True}),
            "deadline": evidence.deadline.model_copy(update={"passed": True}),
            "typing": evidence.typing.model_copy(update={"passed": True}),
            "benchmarks": passing_benchmarks,
        }
    )

    gates = derive_gates(technical)

    assert gates.model_dump(mode="json") == {
        "reader_trace_equivalence": True,
        "writer_trace_equivalence": True,
        "malformed_failures": True,
        "deadline_semantics": True,
        "strict_typing": True,
        "benchmark_100000_time": True,
        "benchmark_100000_memory": True,
        "benchmark_1000000_time": True,
        "benchmark_1000000_memory": True,
        "license_compatibility": False,
    }
    assert decide_probe(gates).model_dump(mode="json") == {
        "technical_outcome": "pass",
        "production_adoption": "blocked",
        "failed_technical_gates": [],
        "blocking_gates": ["license_compatibility"],
        "production_changed": False,
    }

    threshold = passing_benchmarks[0].model_copy(update={"wall_ratio": 1.5, "time_passed": True})
    bad_benchmarks = technical.model_copy(update={"benchmarks": (threshold, passing_benchmarks[1])})
    assert derive_gates(bad_benchmarks).benchmark_100000_time is False
    assert derive_gates(bad_benchmarks).benchmark_100000_memory is True
    assert decide_probe(derive_gates(bad_benchmarks)).technical_outcome == "reject"

    memory_threshold = passing_benchmarks[0].model_copy(update={"rss_ratio": 1.5, "memory_passed": True})
    bad_memory = technical.model_copy(update={"benchmarks": (memory_threshold, passing_benchmarks[1])})
    assert derive_gates(bad_memory).benchmark_100000_time is True
    assert derive_gates(bad_memory).benchmark_100000_memory is False
    assert decide_probe(derive_gates(bad_memory)).failed_technical_gates == ("benchmark_100000_memory",)

    changed_digest = passing_benchmarks[0].model_dump(mode="python")
    changed_digest["scapy"]["samples"][-1]["trace_digest"] = "b" * 64
    with pytest.raises(ValueError, match="every measured trace digest"):
        BenchmarkComparison.model_validate(changed_digest)

    changed_input = passing_benchmarks[0].model_dump(mode="python")
    for sample in changed_input["scapy"]["samples"]:
        sample["input_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="identical input bytes"):
        BenchmarkComparison.model_validate(changed_input)

    too_few_samples = passing_benchmarks[0].model_dump(mode="python")
    too_few_samples["production"]["samples"] = too_few_samples["production"]["samples"][:-1]
    with pytest.raises(ValueError, match="exactly 5 measured samples"):
        BenchmarkComparison.model_validate(too_few_samples)

    wrong_median = passing_benchmarks[0].model_dump(mode="python")
    wrong_median["production"]["median_wall_seconds"] = 99.0
    with pytest.raises(ValueError, match="medians must be recomputed"):
        BenchmarkComparison.model_validate(wrong_median)

    mixed_adapter_input = passing_benchmarks[0].model_dump(mode="python")
    mixed_adapter_input["production"]["samples"][-1]["input_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="one identical declared input"):
        BenchmarkComparison.model_validate(mixed_adapter_input)

    for field, value, message in (
        ("material_threshold_ratio", 1.6, "threshold must remain predeclared"),
        ("wall_ratio", 99.0, "wall ratio must be recomputed"),
        ("rss_ratio", 99.0, "RSS ratio must be recomputed"),
        ("time_passed", False, "gates must derive independently"),
    ):
        mutation = passing_benchmarks[0].model_dump(mode="python")
        mutation[field] = value
        with pytest.raises(ValueError, match=message):
            BenchmarkComparison.model_validate(mutation)


def test_case_evidence_names_the_exact_failed_differential_case() -> None:
    """An aggregate false flag without the failing case would not be machine-auditable."""
    differential = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False)).writer_differential

    assert differential.failed_case_names == ("scapy_writer_nanosecond_trace_round_trip",)
    assert differential.results[-1].candidate_outcome.status == "rejected"
    assert differential.results[-1].oracle_outcome == differential.results[-1].expected_outcome
    assert CaseEvidence.model_validate(differential.model_dump(mode="python")) == differential


def test_evidence_is_strict_canonical_machine_auditable_and_checkable(tmp_path: Path) -> None:
    """A stale or unlinked report must not pass merely because it is valid JSON."""
    evidence = build_probe_evidence(run_benchmarks=False)
    validated = validate_probe_evidence(evidence)
    rendered = render_probe_evidence(evidence)
    destination = tmp_path / "scapy_cases.json"

    assert json.loads(rendered) == validated.model_dump(mode="json")
    assert rendered.endswith(b"\n")
    assert write_probe_evidence(destination, evidence, check=False) is True
    assert destination.read_bytes() == rendered
    assert write_probe_evidence(destination, evidence, check=True) is True
    destination.write_bytes(b"{}\n")
    assert write_probe_evidence(destination, evidence, check=True) is False

    stale = deepcopy(evidence)
    environment = stale["environment"]
    assert isinstance(environment, dict)
    environment["implementation_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="implementation SHA-256"):
        validate_probe_evidence(stale)

    stale_lock = deepcopy(evidence)
    environment = cast(dict[str, object], stale_lock["environment"])
    environment["uv_lock_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="uv.lock SHA-256"):
        validate_probe_evidence(stale_lock)

    changed_policy = deepcopy(evidence)
    policy = changed_policy["policy"]
    assert isinstance(policy, dict)
    shapes = cast(dict[str, object], policy["benchmark_frame_shapes"])
    shapes["frames_100000"] = {"frame_count": 1}
    with pytest.raises(ValueError, match="predeclared probe policy"):
        validate_probe_evidence(changed_policy)

    unknown_case = deepcopy(evidence)
    reader = cast(dict[str, object], unknown_case["reader_differential"])
    case_names = cast(tuple[str, ...], reader["case_names"])
    results = cast(tuple[dict[str, object], ...], reader["results"])
    reader["case_names"] = ("unknown_reader_case", *case_names[1:])
    results[0]["name"] = "unknown_reader_case"
    with pytest.raises(ValueError, match="exact declared case inventory"):
        validate_probe_evidence(unknown_case)

    fake_command = deepcopy(evidence)
    reader = cast(dict[str, object], fake_command["reader_differential"])
    reader["command"] = ["true"]
    with pytest.raises(ValueError, match="canonical nontrivial command"):
        validate_probe_evidence(fake_command)

    wrong_functional_command = deepcopy(evidence)
    reader = cast(dict[str, object], wrong_functional_command["reader_differential"])
    reader["command"] = ["python", "wrong-functional-runner"]
    with pytest.raises(ValueError, match="canonical executed functional command"):
        validate_probe_evidence(wrong_functional_command)

    fake_typing = deepcopy(evidence)
    typing = cast(dict[str, object], fake_typing["typing"])
    typing["command"] = ["true"]
    with pytest.raises(ValueError, match="canonical strict Pyright command"):
        validate_probe_evidence(fake_typing)

    wrong_typing_command = deepcopy(evidence)
    typing = cast(dict[str, object], wrong_typing_command["typing"])
    typing["command"] = ["uv", "run", "wrong-pyright"]
    with pytest.raises(ValueError, match="canonical executed strict Pyright command"):
        validate_probe_evidence(wrong_typing_command)

    for group_name in ("reader_differential", "writer_differential", "malformed", "deadline"):
        fabricated = deepcopy(evidence)
        group = cast(dict[str, object], fabricated[group_name])
        results = cast(tuple[dict[str, object], ...], group["results"])
        expected = cast(dict[str, object], results[0]["expected_outcome"])
        results[0]["candidate_outcome"] = (
            {
                "status": "rejected",
                "exception_type": "TrafficlabError",
                "message": "fabricated rejection",
                "corrective_action": "fabricated action",
                "trace_digest": None,
            }
            if expected["status"] == "accepted"
            else {
                "status": "accepted",
                "exception_type": None,
                "message": None,
                "corrective_action": None,
                "trace_digest": "d" * 64,
            }
        )
        with pytest.raises(ValueError, match="pass flag must derive"):
            validate_probe_evidence(fabricated)

    blank_case = deepcopy(evidence)
    reader = cast(dict[str, object], blank_case["reader_differential"])
    results = cast(tuple[dict[str, object], ...], reader["results"])
    results[0]["detail"] = ""
    with pytest.raises(ValueError, match="text fields must be nonempty"):
        validate_probe_evidence(blank_case)

    fabricated_aggregate = deepcopy(evidence)
    writer = cast(dict[str, object], fabricated_aggregate["writer_differential"])
    writer["failed_case_names"] = ()
    with pytest.raises(ValueError, match="aggregate must derive"):
        validate_probe_evidence(fabricated_aggregate)

    fabricated_typing = deepcopy(evidence)
    typing = cast(dict[str, object], fabricated_typing["typing"])
    typing["exit_code"] = 1
    typing["passed"] = True
    with pytest.raises(ValueError, match="typing pass flag must derive"):
        validate_probe_evidence(fabricated_typing)

    fabricated_decision = deepcopy(evidence)
    decision = cast(dict[str, object], fabricated_decision["decision"])
    decision["failed_technical_gates"] = []
    with pytest.raises(ValueError, match="decision must be derived"):
        validate_probe_evidence(fabricated_decision)

    fabricated_gates = deepcopy(evidence)
    gates = cast(dict[str, object], fabricated_gates["gates"])
    gates["reader_trace_equivalence"] = False
    with pytest.raises(ValueError, match="gates must be derived"):
        validate_probe_evidence(fabricated_gates)


def test_checked_fixture_and_license_decision_match_validated_evidence() -> None:
    repository = Path(__file__).resolve().parents[3]
    fixture = repository / "examples" / "scientific_stack" / "scapy_cases.json"
    decision = repository / "examples" / "scientific_stack" / "SCAPY_LICENSE_DECISION.md"

    evidence = ProbeEvidence.model_validate_json(fixture.read_bytes())

    assert validate_probe_evidence(evidence.model_dump(mode="python")) == evidence
    assert evidence.decision.production_adoption == "blocked"
    assert evidence.decision.production_changed is False
    decision_text = decision.read_text(encoding="utf-8")
    assert "development-only" in decision_text
    assert "copies no Scapy source code" in decision_text
    assert "does not provide legal advice" in decision_text
    assert "separate compatibility decision" in decision_text


def test_runner_generates_and_checks_scapy_probe(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    runner = repository / "scripts" / "run_scientific_stack_probes.py"
    output = tmp_path / "scapy.json"
    generated = subprocess.run(
        [sys.executable, str(runner), "--probe", "scapy", "--output", str(output), "--skip-benchmarks"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    checked = subprocess.run(
        [sys.executable, str(runner), "--probe", "scapy", "--output", str(output), "--check"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert generated.returncode == 0, generated.stderr
    assert checked.returncode == 1, checked.stderr
    assert "stale" in checked.stdout


def test_production_package_has_no_scapy_import() -> None:
    repository = Path(__file__).resolve().parents[3]
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, trafficlab; print(any(name == 'scapy' or name.startswith('scapy.') for name in sys.modules))",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert imported.stdout.strip() == "False"


def test_review_candidate_does_not_delegate_unobservable_padding_to_production(tmp_path: Path) -> None:
    """The Scapy candidate must expose its own permissive padding behavior to differential evidence."""
    frame = _protocol_frames()[0][:-1]
    packet = bytearray(_packet(frame, 1))
    packet[8 + 20 + len(frame)] = 1
    path = _write(tmp_path, _section() + _interface() + bytes(packet), "nonzero-padding.pcapng")

    candidate = read_with_scapy(path, _metadata())

    assert candidate.to_events() == (TraceEvent(0.000001, Direction.OUTBOUND, 59),)
    with pytest.raises(TrafficlabError, match="packet padding"):
        parse_pcapng_trace(path, _metadata())


def test_review_scapy_benchmark_child_never_calls_production_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidate timing must contain only the independently preloaded Scapy path."""
    path = _write(tmp_path, _capture((_protocol_frames()[0],), (1,)))

    def forbidden(*args: object, **kwargs: object) -> TrafficTrace:
        raise AssertionError("production parser entered Scapy benchmark")

    monkeypatch.setattr(probe, "parse_pcapng_trace", forbidden, raising=False)

    result = probe.benchmark_child("scapy", path, _metadata())

    assert result["frame_count"] == 1


def test_review_case_evidence_contains_executed_results_and_rejects_fake_commands() -> None:
    """Declared cases and a boolean cannot substitute for per-case executed observations."""
    evidence = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False))
    assert evidence.reader_differential.results
    with pytest.raises(ValueError, match="declared case names"):
        CaseEvidence.model_validate(
            {
                "case_names": [],
                "failed_case_names": [],
                "command": ["true"],
                "passed": True,
                "results": [],
            }
        )


def test_review_benchmark_samples_require_positive_resources_and_bound_input_identity() -> None:
    """Invalid resource samples or unidentified inputs must not contribute to a median."""
    base = {
        "frame_count": 100_000,
        "trace_digest": "a" * 64,
        "input_sha256": "b" * 64,
        "input_size_bytes": 9_600_048,
        "wall_seconds": 1.0,
        "peak_rss_kib": 100_000,
    }
    sample = BenchmarkSample.model_validate(base)
    assert sample.input_sha256 == "b" * 64
    assert sample.input_size_bytes == 9_600_048
    with pytest.raises(ValueError):
        BenchmarkSample.model_validate({**base, "wall_seconds": 0.0})
    with pytest.raises(ValueError):
        BenchmarkSample.model_validate({**base, "peak_rss_kib": 0})


def test_review_deadline_is_rechecked_immediately_after_reader_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expiry during reader setup must prevent the first packet read."""
    now = [0.0]

    class Reader:
        interfaces: list[tuple[int, int, dict[str, object]]] = []
        read_called = False

        def __enter__(self) -> Reader:
            now[0] = 1.0
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def read_packet(self, size: int = 65_535) -> object:
            del size
            self.read_called = True
            raise EOFError

    reader = Reader()

    class ReaderFactory:
        def __call__(self, filename: str) -> Reader:
            del filename
            return reader

    def boundaries() -> tuple[object, object, object, object]:
        return ReaderFactory(), object(), object(), float

    monkeypatch.setattr(probe, "_scapy_boundaries", boundaries)

    with pytest.raises(DeadlineExceededError):
        read_with_scapy(Path("unused.pcapng"), _metadata(), deadline=1.0, clock=lambda: now[0])

    assert reader.read_called is False


def test_review_policy_names_independent_gates_and_equivalent_timing_boundary() -> None:
    assert GATE_NAMES == (
        "reader_trace_equivalence",
        "writer_trace_equivalence",
        "malformed_failures",
        "deadline_semantics",
        "strict_typing",
        "benchmark_100000_time",
        "benchmark_100000_memory",
        "benchmark_1000000_time",
        "benchmark_1000000_memory",
        "license_compatibility",
    )
    policy = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False)).policy
    assert policy.timing_boundary == (
        "adapter-specific imports and factories preloaded; path open through canonical count and digest timed"
    )


def test_candidate_policy_normalizes_candidate_visible_reader_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every candidate-visible interface, frame, timestamp, and I/O guard must fail closed."""

    class Packet:
        def __init__(self, frame: bytes, timestamp: float) -> None:
            self.frame = frame
            self.time = timestamp
            self.wirelen = len(frame)

        def __bytes__(self) -> bytes:
            return self.frame

    class Reader:
        def __init__(
            self,
            packets: list[Packet],
            *,
            final_interfaces: list[tuple[int, int, dict[str, object]]] | None = None,
        ) -> None:
            self.interfaces: list[tuple[int, int, dict[str, object]]] = [(1, 65_535, {})]
            self.packets = packets
            self.final_interfaces = final_interfaces

        def __enter__(self) -> Reader:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def read_packet(self, size: int = 65_535) -> Packet:
            del size
            if self.packets:
                return self.packets.pop(0)
            if self.final_interfaces is not None:
                self.interfaces = self.final_interfaces
            raise EOFError

    class ReaderFactory:
        def __init__(self, reader: Reader) -> None:
            self.reader = reader

        def __call__(self, filename: str) -> Reader:
            del filename
            return self.reader

    class ErrorFactory:
        def __call__(self, filename: str) -> Reader:
            del filename
            raise OSError("injected reader failure")

    def install(factory: object) -> None:
        def boundaries() -> tuple[object, object, object, object]:
            return factory, object(), object(), float

        monkeypatch.setattr(probe, "_scapy_boundaries", boundaries)

    install(ReaderFactory(Reader([Packet(b"\x00" * 13, 0.0)])))
    with pytest.raises(TrafficlabError, match="at least 14"):
        read_with_scapy(tmp_path / "short.pcapng", _metadata())

    install(ReaderFactory(Reader([Packet(_protocol_frames()[0], float("nan"))])))
    with pytest.raises(TrafficlabError, match="finite and nonnegative"):
        read_with_scapy(tmp_path / "nan.pcapng", _metadata())

    install(ReaderFactory(Reader([Packet(_protocol_frames()[0], 0.0)], final_interfaces=[])))
    with pytest.raises(TrafficlabError, match="0 interfaces"):
        read_with_scapy(tmp_path / "missing-interface.pcapng", _metadata())

    install(ReaderFactory(Reader([Packet(_protocol_frames()[0], 0.0)], final_interfaces=[(101, 65_535, {})])))
    with pytest.raises(TrafficlabError, match="unsupported link type 101"):
        read_with_scapy(tmp_path / "changed-linktype.pcapng", _metadata())

    install(ErrorFactory())
    with pytest.raises(TrafficlabError, match="could not read PCAPNG.*injected reader failure"):
        read_with_scapy(tmp_path / "reader-error.pcapng", _metadata())


def test_writer_normalizes_type_io_and_dynamic_boundary_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test-only writer must normalize each owned boundary failure."""
    trace = TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 60),))

    with pytest.raises(TypeError, match="TrafficTrace"):
        write_with_scapy(
            tmp_path / "type.pcapng", cast(TrafficTrace, object()), _metadata(), observation_window_seconds=1.0
        )

    class Packet:
        time = 0.0
        wirelen = 60

        def __bytes__(self) -> bytes:
            return _protocol_frames()[0]

    class EtherFactory:
        def __call__(self, raw_packet: bytes) -> Packet:
            del raw_packet
            return Packet()

    class ErrorWriterFactory:
        def __call__(self, filename: str) -> object:
            del filename
            raise OSError("injected writer failure")

    def io_boundaries() -> tuple[object, object, object, object]:
        return object(), ErrorWriterFactory(), EtherFactory(), float

    monkeypatch.setattr(probe, "_scapy_boundaries", io_boundaries)
    with pytest.raises(TrafficlabError, match="could not write PCAPNG.*injected writer failure"):
        write_with_scapy(tmp_path / "io.pcapng", trace, _metadata(), observation_window_seconds=1.0)

    class Writer:
        def __enter__(self) -> Writer:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def write_header(self, packet: Packet) -> None:
            del packet

        def write_packet(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("injected dynamic failure")

    class WriterFactory:
        def __call__(self, filename: str) -> Writer:
            del filename
            return Writer()

    def dynamic_boundaries() -> tuple[object, object, object, object]:
        return object(), WriterFactory(), EtherFactory(), float

    monkeypatch.setattr(probe, "_scapy_boundaries", dynamic_boundaries)
    with pytest.raises(TrafficlabError, match="Scapy could not encode.*RuntimeError"):
        write_with_scapy(tmp_path / "dynamic.pcapng", trace, _metadata(), observation_window_seconds=1.0)


def test_benchmark_children_parse_the_same_input_independently(tmp_path: Path) -> None:
    """Both timed adapters must return identical count/digest and bound input identity."""
    path = _write(tmp_path, _capture((_protocol_frames()[0],), (1,)))

    production = probe.benchmark_child("production", path, _metadata())
    candidate = probe.benchmark_child("scapy", path, _metadata())

    assert production["frame_count"] == candidate["frame_count"] == 1
    assert production["trace_digest"] == candidate["trace_digest"]
    assert production["input_sha256"] == candidate["input_sha256"]
    assert production["input_size_bytes"] == candidate["input_size_bytes"] == path.stat().st_size


def test_review_coherent_fabricated_technical_pass_fails_authenticity() -> None:
    """Recomputed-looking groups, gates, and decision cannot replace independent case execution."""
    evidence = build_probe_evidence(run_benchmarks=False)
    for group_name in ("reader_differential", "writer_differential", "malformed", "deadline"):
        group = cast(dict[str, object], evidence[group_name])
        results = cast(tuple[dict[str, object], ...], group["results"])
        for result in results:
            result["candidate_outcome"] = result["oracle_outcome"]
            result["passed"] = True
        group["failed_case_names"] = ()
        group["passed"] = True
    evidence["benchmarks"] = tuple(
        comparison.model_dump(mode="python")
        for comparison in (_passing_comparison(100_000), _passing_comparison(1_000_000))
    )
    gates = cast(dict[str, object], evidence["gates"])
    for name in GATE_NAMES[:-1]:
        gates[name] = True
    decision = cast(dict[str, object], evidence["decision"])
    decision["technical_outcome"] = "pass"
    decision["failed_technical_gates"] = ()

    with pytest.raises(ValueError, match="independently re-executed functional evidence"):
        validate_probe_evidence(evidence)


def test_review_functional_outcomes_retain_normalized_status_and_error_details() -> None:
    """Accepted traces and failures must retain the exact normalized fields used by the oracle comparison."""
    evidence = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False))
    accepted = evidence.reader_differential.results[0].candidate_outcome
    rejected = next(result for result in evidence.malformed.results if not result.passed).candidate_outcome

    assert accepted.status == "accepted"
    assert accepted.trace_digest is not None
    assert accepted.exception_type is None
    assert accepted.message is None
    assert accepted.corrective_action is None
    assert rejected.status in {"accepted", "rejected"}
    if rejected.status == "rejected":
        assert rejected.exception_type
        assert rejected.message
        assert rejected.corrective_action

    with pytest.raises(ValueError, match="accepted outcome requires only"):
        NormalizedOutcome.model_validate(
            {
                "status": "accepted",
                "exception_type": None,
                "message": None,
                "corrective_action": None,
                "trace_digest": None,
            }
        )
    with pytest.raises(ValueError, match="rejected outcome requires normalized"):
        NormalizedOutcome.model_validate(
            {
                "status": "rejected",
                "exception_type": "TrafficlabError",
                "message": None,
                "corrective_action": "retry",
                "trace_digest": None,
            }
        )

    result = evidence.reader_differential.results[0].model_dump(mode="python")
    result["comparison_fields"] = ()
    with pytest.raises(ValueError, match="comparison fields must be nonempty and unique"):
        CaseResult.model_validate(result)
    result["comparison_fields"] = ("status", "status")
    with pytest.raises(ValueError, match="comparison fields must be nonempty and unique"):
        CaseResult.model_validate(result)


@pytest.mark.parametrize("field", ["exception_type", "message", "corrective_action"])
def test_review_functional_error_detail_mutation_fails_authenticity(field: str) -> None:
    evidence = build_probe_evidence(run_benchmarks=False)
    malformed = cast(dict[str, object], evidence["malformed"])
    results = cast(tuple[dict[str, object], ...], malformed["results"])
    rejected = cast(dict[str, object], results[0]["candidate_outcome"])
    rejected[field] = f"fabricated-{field}"

    with pytest.raises(ValueError, match="independently re-executed functional evidence"):
        validate_probe_evidence(evidence)


@pytest.mark.parametrize(
    ("adapter", "replacement"),
    [
        ("production", ["true"]),
        (
            "production",
            ["python", "-m", "tests.scientific.probes.scapy_pcapng", "benchmark-child", "--adapter", "scapy"],
        ),
        ("scapy", ["python", "-m", "tests.scientific.probes.scapy_pcapng", "benchmark-child", "--frame-count", "1"]),
        (
            "scapy",
            ["python", "-m", "tests.scientific.probes.scapy_pcapng", "benchmark-child", "--path", "/tmp/not-canonical"],
        ),
    ],
)
def test_review_benchmark_commands_are_bound_to_policy_adapter_size_and_placeholder(
    adapter: str, replacement: list[str]
) -> None:
    comparison = _passing_comparison(100_000).model_dump(mode="python")
    comparison[adapter]["command"] = replacement

    with pytest.raises(ValueError, match="canonical benchmark command"):
        BenchmarkComparison.model_validate(comparison)


def test_review_benchmark_identity_streams_without_path_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Input authentication must not retain the whole capture before peak-RSS measurement."""
    path = _write(tmp_path, _capture((_protocol_frames()[0],), (1,)))

    def forbidden(self: Path) -> bytes:
        raise AssertionError(f"whole-file read forbidden for {self}")

    monkeypatch.setattr(Path, "read_bytes", forbidden)

    result = probe.benchmark_child("production", path, _metadata())

    assert result["input_size_bytes"] == path.stat().st_size

    actual_size = path.stat().st_size

    class ChangedStat:
        st_size = actual_size + 1

    def changed_stat(self: Path) -> ChangedStat:
        del self
        return ChangedStat()

    monkeypatch.setattr(Path, "stat", changed_stat)
    with pytest.raises(RuntimeError, match="size changed while hashing"):
        probe.benchmark_child("production", path, _metadata())
