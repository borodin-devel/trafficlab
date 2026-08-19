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

from tests.scientific.probes.scapy_pcapng import (
    BENCHMARK_FRAME_SHAPES,
    BENCHMARK_REPETITIONS,
    DIFFERENTIAL_CASE_NAMES,
    GATE_NAMES,
    MALFORMED_CASE_NAMES,
    MAX_MATERIAL_RATIO,
    SCAPY_VERSION,
    BenchmarkComparison,
    CaseEvidence,
    ProbeEvidence,
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
    assert DIFFERENTIAL_CASE_NAMES == (
        "ethernet_ipv4_ipv6_arp_little_endian_default_microseconds",
        "ethernet_ipv4_ipv6_arp_big_endian_decimal_nanoseconds",
        "binary_2^-10_timestamp_resolution",
        "epb_padding_options_original_length",
        "source_mac_outbound_peer_inbound_broadcast_inbound",
        "closed_observation_window_and_frame_validation",
        "scapy_writer_canonical_trace_round_trip",
    )
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
        "trace_equivalence",
        "malformed_failures",
        "deadline_semantics",
        "strict_typing",
        "benchmark_100000",
        "benchmark_1000000",
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
def test_reader_normalizes_every_predeclared_malformed_failure(tmp_path: Path, name: str, content: bytes) -> None:
    """Scapy permissiveness or exception text must not weaken the production parser contract."""
    path = _write(tmp_path, content, f"{name}.pcapng")
    with pytest.raises(TrafficlabError) as production_error:
        parse_pcapng_trace(path, _metadata())

    with pytest.raises(type(production_error.value)) as candidate_error:
        read_with_scapy(path, _metadata())

    assert str(candidate_error.value) == str(production_error.value)
    assert candidate_error.value.corrective_action == production_error.value.corrective_action


def test_reader_checks_deadline_before_validation_and_after_each_scapy_packet(tmp_path: Path) -> None:
    """Expiry after packet one must win before Scapy accepts packet two."""
    frames = (_protocol_frames()[0],) * 2
    path = _write(tmp_path, _capture(frames, (0, 1)))
    values = iter((0.0, 0.0, 2.0))

    def clock() -> float:
        return next(values)

    with pytest.raises(DeadlineExceededError, match="deadline") as error:
        read_with_scapy(path, _metadata(), deadline=1.0, clock=clock)

    assert error.value.corrective_action == "increase the total run timeout and retry capture"


def test_reader_checks_expired_deadline_before_touching_a_missing_path(tmp_path: Path) -> None:
    """No I/O may begin once the absolute deadline has already expired."""
    with pytest.raises(DeadlineExceededError, match="deadline"):
        read_with_scapy(tmp_path / "missing.pcapng", _metadata(), deadline=1.0, clock=lambda: 1.0)


def _passing_comparison(frame_count: int) -> BenchmarkComparison:
    return BenchmarkComparison.model_validate(
        {
            "frame_count": frame_count,
            "warmup_runs_per_adapter": 1,
            "measured_runs_per_adapter": 5,
            "production": {
                "command": ["python", "-m", "tests.scientific.probes.scapy_pcapng", "benchmark-child"],
                "samples": [
                    {
                        "frame_count": frame_count,
                        "trace_digest": "a" * 64,
                        "wall_seconds": value,
                        "peak_rss_kib": 100_000,
                    }
                    for value in (1.0, 1.2, 1.1, 1.3, 0.9)
                ],
                "median_wall_seconds": 1.1,
                "median_peak_rss_kib": 100_000,
            },
            "scapy": {
                "command": ["python", "-m", "tests.scientific.probes.scapy_pcapng", "benchmark-child"],
                "samples": [
                    {
                        "frame_count": frame_count,
                        "trace_digest": "a" * 64,
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
            "passed": True,
        }
    )


def test_gate_derivation_is_fail_closed_for_measurements_typing_and_license() -> None:
    """Missing, malformed, or threshold-equal evidence must never authorize production adoption."""
    evidence = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False))
    passing_benchmarks = (_passing_comparison(100_000), _passing_comparison(1_000_000))
    technical = evidence.model_copy(
        update={
            "differential": evidence.differential.model_copy(update={"passed": True}),
            "malformed": evidence.malformed.model_copy(update={"passed": True}),
            "deadline": evidence.deadline.model_copy(update={"passed": True}),
            "typing": evidence.typing.model_copy(update={"passed": True}),
            "benchmarks": passing_benchmarks,
        }
    )

    gates = derive_gates(technical)

    assert gates.model_dump(mode="json") == {
        "trace_equivalence": True,
        "malformed_failures": True,
        "deadline_semantics": True,
        "strict_typing": True,
        "benchmark_100000": True,
        "benchmark_1000000": True,
        "license_compatibility": False,
    }
    assert decide_probe(gates).model_dump(mode="json") == {
        "technical_outcome": "pass",
        "production_adoption": "blocked",
        "failed_technical_gates": [],
        "blocking_gates": ["license_compatibility"],
        "production_changed": False,
    }

    threshold = passing_benchmarks[0].model_copy(update={"wall_ratio": 1.5, "passed": True})
    bad_benchmarks = technical.model_copy(update={"benchmarks": (threshold, passing_benchmarks[1])})
    assert derive_gates(bad_benchmarks).benchmark_100000 is False
    assert decide_probe(derive_gates(bad_benchmarks)).technical_outcome == "reject"

    changed_digest = passing_benchmarks[0].model_dump(mode="python")
    changed_digest["scapy"]["samples"][-1]["trace_digest"] = "b" * 64
    with pytest.raises(ValueError, match="every measured trace digest"):
        BenchmarkComparison.model_validate(changed_digest)


def test_case_evidence_names_the_exact_failed_differential_case() -> None:
    """An aggregate false flag without the failing case would not be machine-auditable."""
    differential = ProbeEvidence.model_validate(build_probe_evidence(run_benchmarks=False)).differential

    assert differential.failed_case_names == ("scapy_writer_canonical_trace_round_trip",)
    assert differential.detail == "Scapy 2.7 public writer loses non-microsecond timestamp precision"
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

    changed_policy = deepcopy(evidence)
    policy = changed_policy["policy"]
    assert isinstance(policy, dict)
    shapes = cast(dict[str, object], policy["benchmark_frame_shapes"])
    shapes["frames_100000"] = {"frame_count": 1}
    with pytest.raises(ValueError, match="predeclared probe policy"):
        validate_probe_evidence(changed_policy)


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
