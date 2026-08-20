"""Development-only typed Scapy PCAPNG differential and benchmark probe."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import platform
import resource
import statistics
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from time import monotonic, perf_counter
from typing import Annotated, Literal, Protocol, Self, SupportsFloat, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator

from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, deterministic_peer_mac

SCAPY_VERSION = "2.7.0"
BENCHMARK_REPETITIONS = 5
MAX_MATERIAL_RATIO = 1.5
READER_CASE_NAMES = (
    "ethernet_ipv4_ipv6_arp_little_endian_default_microseconds",
    "ethernet_ipv4_ipv6_arp_big_endian_decimal_nanoseconds",
    "binary_2^-10_timestamp_resolution",
    "epb_padding_options_original_length",
    "source_mac_outbound_peer_inbound_broadcast_inbound",
    "closed_observation_window_and_frame_validation",
)
WRITER_CASE_NAMES = (
    "scapy_writer_microsecond_trace_round_trip",
    "scapy_writer_nanosecond_trace_round_trip",
)
DIFFERENTIAL_CASE_NAMES = (*READER_CASE_NAMES, *WRITER_CASE_NAMES)
MALFORMED_CASE_NAMES = (
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
DEADLINE_CASE_NAMES = (
    "expired_before_io",
    "expired_after_reader_setup",
    "expires_after_first_scapy_packet",
    "expired_after_candidate_postprocessing",
)
BENCHMARK_FRAME_SHAPES: dict[str, dict[str, object]] = {
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
GATE_NAMES = (
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
_BOUNDED_BENCHMARK_COMMAND = (
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
_TYPING_COMMAND = (
    "uv",
    "run",
    "--locked",
    "pyright",
    "tests/scientific/probes/scapy_pcapng.py",
    "tests/scientific/probes/test_scapy_pcapng.py",
    "scripts/run_scientific_stack_probes.py",
)
_TIMING_BOUNDARY = (
    "adapter-specific imports and factories preloaded; path open through canonical count and digest timed"
)

_UINT32_MAX = 2**32 - 1
_MALFORMED_ACTION = "replace the PCAPNG with a complete valid Ethernet capture"
_DEADLINE_ACTION = "increase the total run timeout and retry capture"
_REPOSITORY = Path(__file__).resolve().parents[3]
_CANONICAL_PYTHON_COMMAND = ("uv", "run", "--locked", "python")


def _list_to_tuple(value: object) -> object:
    return tuple(cast(list[object], value)) if type(value) is list else value


class _StrictRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


type StringTuple = Annotated[tuple[str, ...], BeforeValidator(_list_to_tuple)]
type ComparisonField = Literal["status", "exception_type", "message", "corrective_action", "trace_digest"]
type ComparisonFields = Annotated[tuple[ComparisonField, ...], BeforeValidator(_list_to_tuple)]


class NormalizedOutcome(_StrictRecord):
    status: Literal["accepted", "rejected", "deadline_exceeded"]
    exception_type: str | None
    message: str | None
    corrective_action: str | None
    trace_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None

    @model_validator(mode="after")
    def fields_match_status(self) -> Self:
        error_values = (self.exception_type, self.message, self.corrective_action)
        if self.status == "accepted":
            if self.trace_digest is None or any(value is not None for value in error_values):
                raise ValueError("accepted outcome requires only a canonical trace digest")
        elif self.trace_digest is not None or any(value is None or not value.strip() for value in error_values):
            raise ValueError("rejected outcome requires normalized exception type, message, and corrective action")
        return self

    def project(self, fields: ComparisonFields) -> tuple[str | None, ...]:
        return tuple(getattr(self, field) for field in fields)


class CaseResult(_StrictRecord):
    name: str
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    input_size_bytes: Annotated[StrictInt, Field(ge=1)]
    comparison_fields: ComparisonFields
    expected_outcome: NormalizedOutcome
    oracle_outcome: NormalizedOutcome
    candidate_outcome: NormalizedOutcome
    detail: str
    passed: StrictBool

    @model_validator(mode="after")
    def outcome_agreement_is_derived(self) -> Self:
        values = (self.name, self.detail)
        if any(not value.strip() for value in values):
            raise ValueError("executed case text fields must be nonempty")
        if not self.comparison_fields or len(set(self.comparison_fields)) != len(self.comparison_fields):
            raise ValueError("executed case comparison fields must be nonempty and unique")
        expected = self.expected_outcome.project(self.comparison_fields)
        oracle = self.oracle_outcome.project(self.comparison_fields)
        candidate = self.candidate_outcome.project(self.comparison_fields)
        passed = expected == oracle == candidate
        if self.passed is not passed:
            raise ValueError("executed case pass flag must derive from expected, oracle, and candidate outcomes")
        return self


class CaseEvidence(_StrictRecord):
    case_names: StringTuple
    command: StringTuple
    results: Annotated[tuple[CaseResult, ...], BeforeValidator(_list_to_tuple)]
    failed_case_names: StringTuple
    passed: StrictBool

    @model_validator(mode="after")
    def complete_results_are_required(self) -> Self:
        result_names = tuple(result.name for result in self.results)
        if not self.case_names or len(set(self.case_names)) != len(self.case_names) or result_names != self.case_names:
            raise ValueError("executed results must match the exact nonempty declared case names")
        if not self.command or self.command == ("true",):
            raise ValueError("executed case command must be the canonical nontrivial command")
        failed = tuple(result.name for result in self.results if not result.passed)
        if self.failed_case_names != failed or self.passed is bool(failed):
            raise ValueError("case aggregate must derive from the executed results")
        return self


class TypingEvidence(_StrictRecord):
    command: StringTuple
    mode: Literal["strict"]
    exit_code: StrictInt
    stdout: str
    stderr: str
    passed: StrictBool

    @model_validator(mode="after")
    def result_is_executed(self) -> Self:
        if not self.command or self.command == ("true",):
            raise ValueError("typing command must be the canonical strict Pyright command")
        passed = self.exit_code == 0 and "0 errors, 0 warnings, 0 informations" in self.stdout
        if self.passed is not passed:
            raise ValueError("typing pass flag must derive from the executed command result")
        return self


class BenchmarkSample(_StrictRecord):
    frame_count: Literal[100_000, 1_000_000]
    trace_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    input_size_bytes: Annotated[StrictInt, Field(gt=0)]
    wall_seconds: Annotated[StrictFloat, Field(gt=0.0)]
    peak_rss_kib: Annotated[StrictInt, Field(gt=0)]


class AdapterBenchmark(_StrictRecord):
    command: StringTuple
    samples: Annotated[tuple[BenchmarkSample, ...], BeforeValidator(_list_to_tuple)]
    median_wall_seconds: StrictFloat
    median_peak_rss_kib: StrictInt

    @model_validator(mode="after")
    def medians_match_raw_samples(self) -> Self:
        if len(self.samples) != BENCHMARK_REPETITIONS:
            raise ValueError(f"benchmark must contain exactly {BENCHMARK_REPETITIONS} measured samples")
        wall = float(statistics.median(sample.wall_seconds for sample in self.samples))
        rss = int(statistics.median(sample.peak_rss_kib for sample in self.samples))
        if self.median_wall_seconds != wall or self.median_peak_rss_kib != rss:
            raise ValueError("benchmark medians must be recomputed exactly from raw samples")
        identities = {(sample.input_sha256, sample.input_size_bytes, sample.frame_count) for sample in self.samples}
        if len(identities) != 1:
            raise ValueError("adapter benchmark samples must use one identical declared input")
        return self


class BenchmarkComparison(_StrictRecord):
    frame_count: Literal[100_000, 1_000_000]
    warmup_runs_per_adapter: Literal[1]
    measured_runs_per_adapter: Literal[5]
    production: AdapterBenchmark
    scapy: AdapterBenchmark
    trace_identity: StrictBool
    wall_ratio: StrictFloat
    rss_ratio: StrictFloat
    material_threshold_ratio: StrictFloat
    time_passed: StrictBool
    memory_passed: StrictBool

    @model_validator(mode="after")
    def ratios_and_gate_match_measurements(self) -> Self:
        if self.production.command != benchmark_evidence_command("production", self.frame_count):
            raise ValueError("production adapter must retain its canonical benchmark command")
        if self.scapy.command != benchmark_evidence_command("scapy", self.frame_count):
            raise ValueError("Scapy adapter must retain its canonical benchmark command")
        if self.material_threshold_ratio != MAX_MATERIAL_RATIO:
            raise ValueError("benchmark material threshold must remain predeclared")
        samples = (*self.production.samples, *self.scapy.samples)
        inputs = {(sample.input_sha256, sample.input_size_bytes, sample.frame_count) for sample in samples}
        if len(inputs) != 1:
            raise ValueError("production and Scapy samples must use identical input bytes")
        digests = {sample.trace_digest for sample in samples}
        trace_identity = all(sample.frame_count == self.frame_count for sample in samples) and len(digests) == 1
        if self.trace_identity is not trace_identity:
            raise ValueError("trace identity must reflect every measured trace digest and frame count")
        wall_ratio = self.scapy.median_wall_seconds / self.production.median_wall_seconds
        rss_ratio = self.scapy.median_peak_rss_kib / self.production.median_peak_rss_kib
        time_passed = self.trace_identity and wall_ratio < MAX_MATERIAL_RATIO
        memory_passed = self.trace_identity and rss_ratio < MAX_MATERIAL_RATIO
        if not math.isclose(self.wall_ratio, wall_ratio, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("wall ratio must be recomputed from adapter medians")
        if not math.isclose(self.rss_ratio, rss_ratio, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("RSS ratio must be recomputed from adapter medians")
        if self.time_passed is not time_passed or self.memory_passed is not memory_passed:
            raise ValueError("benchmark gates must derive independently from identity and material ratios")
        return self


class ProbePolicy(_StrictRecord):
    scapy_version: Literal["2.7.0"]
    development_only: Literal[True]
    production_codec: Literal["trafficlab.pcapng"]
    production_changed: Literal[False]
    reader_case_names: StringTuple
    writer_case_names: StringTuple
    malformed_case_names: StringTuple
    deadline_case_names: StringTuple
    functional_runner_command: StringTuple
    typing_command: StringTuple
    benchmark_frame_shapes: dict[str, dict[str, object]]
    benchmark_repetitions: Literal[5]
    warmup_runs_per_adapter: Literal[1]
    fresh_subprocess_per_run: Literal[True]
    benchmark_runner_command: StringTuple
    benchmark_child_module: Literal["tests.scientific.probes.scapy_pcapng"]
    benchmark_path_placeholder: Literal["<generated-pcapng>"]
    timing_boundary: Literal[
        "adapter-specific imports and factories preloaded; path open through canonical count and digest timed"
    ]
    material_regression_rule: Literal[
        "reject when Scapy median wall or peak RSS is >=1.50x production at either frame count"
    ]
    canonical_comparison: Literal["TrafficTrace columns"]


class ProbeEnvironment(_StrictRecord):
    python: str
    platform: str
    machine: str
    scapy: Literal["2.7.0"]
    uv_lock_sha256: str
    implementation_sha256: str


class LicenseEvidence(_StrictRecord):
    scapy_license_identifier: Literal["GPL-2.0-only"]
    compatibility_decision: Literal["not_made"]
    development_only: Literal[True]
    scapy_source_copied: Literal[False]
    production_import: Literal[False]
    legal_advice: Literal[False]
    production_adoption_blocked: Literal[True]


class ProbeGates(_StrictRecord):
    reader_trace_equivalence: StrictBool
    writer_trace_equivalence: StrictBool
    malformed_failures: StrictBool
    deadline_semantics: StrictBool
    strict_typing: StrictBool
    benchmark_100000_time: StrictBool
    benchmark_100000_memory: StrictBool
    benchmark_1000000_time: StrictBool
    benchmark_1000000_memory: StrictBool
    license_compatibility: StrictBool


class ProbeDecision(_StrictRecord):
    technical_outcome: Literal["pass", "reject"]
    production_adoption: Literal["blocked"]
    failed_technical_gates: StringTuple
    blocking_gates: StringTuple
    production_changed: Literal[False]


class ProbeEvidence(_StrictRecord):
    schema_version: Literal[3]
    probe: Literal["scapy_pcapng"]
    policy: ProbePolicy
    environment: ProbeEnvironment
    reader_differential: CaseEvidence
    writer_differential: CaseEvidence
    malformed: CaseEvidence
    deadline: CaseEvidence
    typing: TypingEvidence
    benchmarks: Annotated[tuple[BenchmarkComparison, ...], BeforeValidator(_list_to_tuple)]
    license: LicenseEvidence
    gates: ProbeGates
    decision: ProbeDecision

    @model_validator(mode="after")
    def derived_fields_match_evidence(self) -> Self:
        inventories = (
            (self.policy.reader_case_names, self.reader_differential.case_names, READER_CASE_NAMES),
            (self.policy.writer_case_names, self.writer_differential.case_names, WRITER_CASE_NAMES),
            (self.policy.malformed_case_names, self.malformed.case_names, MALFORMED_CASE_NAMES),
            (self.policy.deadline_case_names, self.deadline.case_names, DEADLINE_CASE_NAMES),
        )
        if any(
            policy_names != group_names or group_names != expected
            for policy_names, group_names, expected in inventories
        ):
            raise ValueError("probe evidence must retain the exact declared case inventory")
        groups = (self.reader_differential, self.writer_differential, self.malformed, self.deadline)
        if any(group.command != self.policy.functional_runner_command for group in groups):
            raise ValueError("case evidence must retain the canonical executed functional command")
        if self.typing.command != self.policy.typing_command:
            raise ValueError("typing evidence must retain the canonical executed strict Pyright command")
        gates = derive_gates(self)
        if self.gates != gates:
            raise ValueError("probe gates must be derived from measured evidence")
        if self.decision != decide_probe(gates):
            raise ValueError("probe decision must be derived from gates")
        return self


class _ScapyPacket(Protocol):
    time: SupportsFloat
    wirelen: int

    def __bytes__(self) -> bytes: ...


class _ScapyReader(Protocol):
    interfaces: list[tuple[int, int, dict[str, object]]]

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object: ...

    def read_packet(self, size: int = 65_535) -> _ScapyPacket: ...


class _ScapyReaderFactory(Protocol):
    def __call__(self, filename: str) -> _ScapyReader: ...


class _ScapyWriter(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> object: ...

    def write_header(self, packet: _ScapyPacket) -> None: ...

    def write_packet(
        self,
        packet: _ScapyPacket,
        sec: float | None = None,
        usec: int | None = None,
        caplen: int | None = None,
        wirelen: int | None = None,
    ) -> None: ...


class _ScapyWriterFactory(Protocol):
    def __call__(self, filename: str) -> _ScapyWriter: ...


class _EtherFactory(Protocol):
    def __call__(self, raw_packet: bytes) -> _ScapyPacket: ...


def _scapy_boundaries() -> tuple[
    _ScapyReaderFactory,
    _ScapyWriterFactory,
    _EtherFactory,
    type[SupportsFloat],
]:
    """Load Scapy only at the explicit development adapter boundary."""
    utils = importlib.import_module("scapy.utils")
    layers = importlib.import_module("scapy.layers.l2")
    return (
        cast(_ScapyReaderFactory, utils.PcapNgReader),
        cast(_ScapyWriterFactory, utils.PcapNgWriter),
        cast(_EtherFactory, layers.Ether),
        cast(type[SupportsFloat], utils.EDecimal),
    )


def _deadline_expired(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() >= deadline:
        raise DeadlineExceededError(
            "Scapy PCAPNG parsing exceeded the total-run deadline",
            corrective_action=_DEADLINE_ACTION,
        )


def _validate_window(trace: TrafficTrace, observation_window_seconds: float | None) -> None:
    if observation_window_seconds is None:
        return
    if (
        type(observation_window_seconds) is not float
        or not math.isfinite(observation_window_seconds)
        or observation_window_seconds <= 0.0
    ):
        raise TrafficlabError(
            "invalid closed observation window: it must be a finite positive float",
            corrective_action="provide a finite positive observation window",
        )
    if len(trace) and float(trace.timestamps[-1] - trace.timestamps[0]) > observation_window_seconds:
        raise TrafficlabError(
            "traffic trace exceeds the closed observation window",
            corrective_action="retain only packets inside the closed observation window and retry",
        )


def _scapy_trace(
    path: Path,
    metadata: CaptureMetadata,
    *,
    deadline: float | None,
    clock: Callable[[], float],
    reader_factory: _ScapyReaderFactory,
    timestamp_type: type[SupportsFloat],
) -> TrafficTrace:
    _deadline_expired(deadline, clock)
    _deadline_expired(deadline, clock)
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    events: list[TraceEvent] = []
    previous_timestamp: float | None = None
    try:
        with reader_factory(str(path)) as reader:
            _deadline_expired(deadline, clock)
            while True:
                try:
                    packet = reader.read_packet(size=_UINT32_MAX)
                except EOFError:
                    break
                if len(reader.interfaces) != 1:
                    raise TrafficlabError(
                        f"invalid PCAPNG: Scapy observed {len(reader.interfaces)} interfaces; expected exactly one",
                        corrective_action=_MALFORMED_ACTION,
                    )
                linktype = reader.interfaces[0][0]
                if linktype != 1:
                    raise TrafficlabError(
                        f"invalid PCAPNG: unsupported link type {linktype}; expected Ethernet link type 1",
                        corrective_action=_MALFORMED_ACTION,
                    )
                frame = bytes(packet)
                if len(frame) < 14:
                    raise TrafficlabError(
                        f"invalid PCAPNG: captured Ethernet frame length must be at least 14, got {len(frame)}",
                        corrective_action=_MALFORMED_ACTION,
                    )
                if not isinstance(packet.time, timestamp_type):
                    raise TrafficlabError(
                        "invalid PCAPNG: Scapy packet record has no explicit timestamp",
                        corrective_action=_MALFORMED_ACTION,
                    )
                timestamp = float(packet.time)
                if not math.isfinite(timestamp) or timestamp < 0.0:
                    raise TrafficlabError(
                        "invalid PCAPNG: Scapy packet timestamp must be finite and nonnegative",
                        corrective_action=_MALFORMED_ACTION,
                    )
                if previous_timestamp is not None and timestamp < previous_timestamp:
                    raise TrafficlabError(
                        "invalid PCAPNG: Scapy packet timestamps must be nondecreasing",
                        corrective_action=_MALFORMED_ACTION,
                    )
                direction = Direction.OUTBOUND if frame[6:12] == target else Direction.INBOUND
                events.append(TraceEvent(timestamp, direction, len(frame)))
                previous_timestamp = timestamp
                _deadline_expired(deadline, clock)
            if len(reader.interfaces) != 1:
                raise TrafficlabError(
                    f"invalid PCAPNG: Scapy observed {len(reader.interfaces)} interfaces; expected exactly one",
                    corrective_action=_MALFORMED_ACTION,
                )
            linktype = reader.interfaces[0][0]
            if linktype != 1:
                raise TrafficlabError(
                    f"invalid PCAPNG: unsupported link type {linktype}; expected Ethernet link type 1",
                    corrective_action=_MALFORMED_ACTION,
                )
            _deadline_expired(deadline, clock)
    except TrafficlabError:
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not read PCAPNG {path}: {error}",
            corrective_action="verify the PCAPNG exists and is readable",
        ) from error
    except Exception as error:
        raise TrafficlabError(
            f"invalid PCAPNG: Scapy could not decode the capture ({type(error).__name__})",
            corrective_action=_MALFORMED_ACTION,
        ) from error
    if not events:
        raise TrafficlabError(
            "invalid PCAPNG: Scapy capture has no packet records",
            corrective_action=_MALFORMED_ACTION,
        )
    trace = TrafficTrace.from_events(events)
    _deadline_expired(deadline, clock)
    return trace


def read_with_scapy(
    path: Path,
    metadata: CaptureMetadata,
    *,
    observation_window_seconds: float | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> TrafficTrace:
    """Convert Scapy packet observations to a Trafficlab-owned canonical trace."""
    _deadline_expired(deadline, clock)
    reader_factory, _, _, timestamp_type = _scapy_boundaries()
    _deadline_expired(deadline, clock)
    candidate = _scapy_trace(
        path,
        metadata,
        deadline=deadline,
        clock=clock,
        reader_factory=reader_factory,
        timestamp_type=timestamp_type,
    )
    _validate_window(candidate, observation_window_seconds)
    _deadline_expired(deadline, clock)
    return candidate


def _frame_for_event(event: TraceEvent, metadata: CaptureMetadata) -> bytes:
    if event.frame_length < 14:
        raise TrafficlabError(
            f"Ethernet frame length must be at least 14, got {event.frame_length}",
            corrective_action="generate complete Ethernet frame lengths and retry",
        )
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    peer = bytes.fromhex(deterministic_peer_mac(metadata.target_mac).replace(":", ""))
    destination, source = (peer, target) if event.direction is Direction.OUTBOUND else (target, peer)
    return destination + source + b"\x08\x00" + b"\x00" * (event.frame_length - 14)


def write_with_scapy(
    path: Path,
    trace: TrafficTrace,
    metadata: CaptureMetadata,
    *,
    observation_window_seconds: float,
) -> TrafficTrace:
    """Write one validated Ethernet trace with Scapy and return its canonical reparse."""
    if type(trace) is not TrafficTrace:
        raise TypeError("trace must be a TrafficTrace")
    _validate_window(trace, observation_window_seconds)
    _, writer_factory, ether_factory, _ = _scapy_boundaries()
    try:
        with writer_factory(str(path)) as writer:
            for index, event in enumerate(trace):
                packet = ether_factory(_frame_for_event(event, metadata))
                if index == 0:
                    writer.write_header(packet)
                writer.write_packet(
                    packet,
                    sec=event.timestamp,
                    caplen=event.frame_length,
                    wirelen=event.frame_length,
                )
    except TrafficlabError:
        raise
    except OSError as error:
        raise TrafficlabError(
            f"could not write PCAPNG {path}: {error}",
            corrective_action="verify the PCAPNG destination is writable",
        ) from error
    except Exception as error:
        raise TrafficlabError(
            f"Scapy could not encode the validated Ethernet trace ({type(error).__name__})",
            corrective_action="retain the production PCAPNG codec and inspect the differential fixture",
        ) from error
    candidate = read_with_scapy(path, metadata, observation_window_seconds=observation_window_seconds)
    if candidate != trace:
        raise TrafficlabError(
            "Scapy PCAPNG writer does not preserve the canonical TrafficTrace",
            corrective_action="retain the production PCAPNG writer for nanosecond timestamp fidelity",
        )
    return candidate


def derive_gates(evidence: ProbeEvidence) -> ProbeGates:
    benchmark_100000 = tuple(item for item in evidence.benchmarks if item.frame_count == 100_000)
    benchmark_1000000 = tuple(item for item in evidence.benchmarks if item.frame_count == 1_000_000)
    gate_100000_time = (
        len(benchmark_100000) == 1
        and benchmark_100000[0].trace_identity
        and benchmark_100000[0].time_passed
        and benchmark_100000[0].wall_ratio < MAX_MATERIAL_RATIO
    )
    gate_100000_memory = (
        len(benchmark_100000) == 1
        and benchmark_100000[0].trace_identity
        and benchmark_100000[0].memory_passed
        and benchmark_100000[0].rss_ratio < MAX_MATERIAL_RATIO
    )
    gate_1000000_time = (
        len(benchmark_1000000) == 1
        and benchmark_1000000[0].trace_identity
        and benchmark_1000000[0].time_passed
        and benchmark_1000000[0].wall_ratio < MAX_MATERIAL_RATIO
    )
    gate_1000000_memory = (
        len(benchmark_1000000) == 1
        and benchmark_1000000[0].trace_identity
        and benchmark_1000000[0].memory_passed
        and benchmark_1000000[0].rss_ratio < MAX_MATERIAL_RATIO
    )
    return ProbeGates(
        reader_trace_equivalence=evidence.reader_differential.passed,
        writer_trace_equivalence=evidence.writer_differential.passed,
        malformed_failures=evidence.malformed.passed,
        deadline_semantics=evidence.deadline.passed,
        strict_typing=evidence.typing.passed,
        benchmark_100000_time=gate_100000_time,
        benchmark_100000_memory=gate_100000_memory,
        benchmark_1000000_time=gate_1000000_time,
        benchmark_1000000_memory=gate_1000000_memory,
        license_compatibility=False,
    )


def decide_probe(gates: ProbeGates) -> ProbeDecision:
    technical_names = GATE_NAMES[:-1]
    values = gates.model_dump(mode="python")
    failed = tuple(name for name in technical_names if values[name] is not True)
    return ProbeDecision(
        technical_outcome="pass" if not failed else "reject",
        production_adoption="blocked",
        failed_technical_gates=failed,
        blocking_gates=("license_compatibility",),
        production_changed=False,
    )


def _protocol_frames(metadata: CaptureMetadata) -> tuple[bytes, bytes, bytes]:
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    peer = bytes.fromhex(deterministic_peer_mac(metadata.target_mac).replace(":", ""))
    ipv4 = bytes.fromhex("4500002e00004000401100000a0000010a000002")
    ipv6 = bytes.fromhex("600000000008114020010db800000000000000000000000120010db8000000000000000000000002")
    arp = (
        bytes.fromhex("0001080006040001") + target + bytes.fromhex("0a000001") + b"\x00" * 6 + bytes.fromhex("0a000002")
    )

    def frame(source: bytes, destination: bytes, ethertype: int, payload: bytes, length: int) -> bytes:
        prefix = destination + source + ethertype.to_bytes(2, "big") + payload
        return prefix + b"\x00" * (length - len(prefix))

    return (
        frame(target, peer, 0x0800, ipv4, 60),
        frame(peer, target, 0x86DD, ipv6, 78),
        frame(b"\xff" * 6, target, 0x0806, arp, 64),
    )


type Endian = Literal["<", ">"]


def _fixture_option(code: int, value: bytes, endian: Endian = "<") -> bytes:
    return struct.pack(f"{endian}HH", code, len(value)) + value + b"\x00" * (-len(value) % 4)


def _fixture_block(
    block_type: int,
    body: bytes,
    endian: Endian = "<",
    *,
    trailing_length: int | None = None,
) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    total = 12 + len(padded)
    trailer = total if trailing_length is None else trailing_length
    return struct.pack(f"{endian}II", block_type, total) + padded + struct.pack(f"{endian}I", trailer)


def _fixture_section(endian: Endian = "<") -> bytes:
    return _fixture_block(0x0A0D0D0A, struct.pack(f"{endian}IHHq", 0x1A2B3C4D, 1, 0, -1), endian)


def _fixture_interface(
    endian: Endian = "<",
    *,
    linktype: int = 1,
    options: bytes = b"",
) -> bytes:
    return _fixture_block(1, struct.pack(f"{endian}HHI", linktype, 0, 65_535) + options, endian)


def _fixture_packet(
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
        ticks & _UINT32_MAX,
        captured,
        original,
    )
    return _fixture_block(6, body + frame + b"\x00" * (-captured % 4) + options, endian)


def _fixture_capture(
    frames: tuple[bytes, ...],
    ticks: tuple[int, ...],
    *,
    endian: Endian = "<",
    resolution: bytes | None = None,
    packet_options: bytes = b"",
    original_lengths: tuple[int, ...] | None = None,
) -> bytes:
    options = b"" if resolution is None else _fixture_option(9, resolution, endian) + struct.pack(f"{endian}HH", 0, 0)
    originals = (None,) * len(frames) if original_lengths is None else original_lengths
    packets = b"".join(
        _fixture_packet(frame, tick, endian, original_length=original, options=packet_options)
        for frame, tick, original in zip(frames, ticks, originals, strict=True)
    )
    return _fixture_section(endian) + _fixture_interface(endian, options=options) + packets


def _input_identity(content: bytes) -> tuple[str, int]:
    return hashlib.sha256(content).hexdigest(), len(content)


def _stream_file_identity(path: Path) -> tuple[str, int]:
    expected_size = path.stat().st_size
    digest = hashlib.sha256()
    observed_size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
            observed_size += len(chunk)
    if observed_size != expected_size:
        raise RuntimeError("benchmark capture size changed while hashing")
    return digest.hexdigest(), expected_size


def _trace_input(trace: TrafficTrace) -> bytes:
    return trace.timestamps.tobytes() + trace.directions.tobytes() + trace.frame_lengths.tobytes()


def _call_outcome(operation: Callable[[], TrafficTrace]) -> NormalizedOutcome:
    try:
        return NormalizedOutcome(
            status="accepted",
            exception_type=None,
            message=None,
            corrective_action=None,
            trace_digest=_trace_digest(operation()),
        )
    except TrafficlabError as error:
        return NormalizedOutcome(
            status="deadline_exceeded" if isinstance(error, DeadlineExceededError) else "rejected",
            exception_type=type(error).__name__,
            message=str(error),
            corrective_action=error.corrective_action,
            trace_digest=None,
        )


def _case_result(
    name: str,
    content: bytes,
    *,
    comparison_fields: tuple[ComparisonField, ...],
    expected: NormalizedOutcome,
    oracle: NormalizedOutcome,
    candidate: NormalizedOutcome,
    detail: str,
) -> CaseResult:
    input_sha256, input_size_bytes = _input_identity(content)
    return CaseResult(
        name=name,
        input_sha256=input_sha256,
        input_size_bytes=input_size_bytes,
        comparison_fields=comparison_fields,
        expected_outcome=expected,
        oracle_outcome=oracle,
        candidate_outcome=candidate,
        detail=detail,
        passed=expected.project(comparison_fields)
        == oracle.project(comparison_fields)
        == candidate.project(comparison_fields),
    )


def _case_evidence(case_names: tuple[str, ...], results: tuple[CaseResult, ...]) -> CaseEvidence:
    failed = tuple(result.name for result in results if not result.passed)
    return CaseEvidence(
        case_names=case_names,
        command=_BOUNDED_BENCHMARK_COMMAND,
        results=results,
        failed_case_names=failed,
        passed=not failed,
    )


def _execute_reader_cases(directory: Path, metadata: CaptureMetadata) -> CaseEvidence:
    from trafficlab.pcapng import parse_pcapng_trace

    frames = _protocol_frames(metadata)
    definitions = (
        (
            READER_CASE_NAMES[0],
            _fixture_capture(frames, (0, 1_250_000, 2_500_000)),
            None,
        ),
        (
            READER_CASE_NAMES[1],
            _fixture_capture(frames, (0, 1_234_567_890, 2_500_000_000), endian=">", resolution=b"\x09"),
            None,
        ),
        (
            READER_CASE_NAMES[2],
            _fixture_capture((frames[0], frames[1]), (1_536, 2_048), resolution=b"\x8a"),
            None,
        ),
        (
            READER_CASE_NAMES[3],
            _fixture_capture(
                (frames[0][:-1], frames[1][:-1]),
                (1_000_000, 2_000_000),
                packet_options=_fixture_option(1, b"note") + struct.pack("<HH", 0, 0),
                original_lengths=(1_500, 1_500),
            ),
            None,
        ),
        (
            READER_CASE_NAMES[4],
            _fixture_capture(frames, (0, 1, 2)),
            None,
        ),
        (
            READER_CASE_NAMES[5],
            _fixture_capture((frames[0], frames[1]), (5_000_000, 6_000_000)),
            1.0,
        ),
    )
    results: list[CaseResult] = []
    for index, (name, content, window) in enumerate(definitions):
        path = directory / f"reader-{index}.pcapng"
        path.write_bytes(content)
        oracle = _call_outcome(lambda path=path: parse_pcapng_trace(path, metadata))
        candidate = _call_outcome(
            lambda path=path, window=window: read_with_scapy(
                path,
                metadata,
                observation_window_seconds=window,
            )
        )
        results.append(
            _case_result(
                name,
                content,
                comparison_fields=("status", "trace_digest"),
                expected=oracle,
                oracle=oracle,
                candidate=candidate,
                detail="canonical TrafficTrace digest from independent production and Scapy reads",
            )
        )
    return _case_evidence(READER_CASE_NAMES, tuple(results))


def _execute_writer_cases(directory: Path, metadata: CaptureMetadata) -> CaseEvidence:
    from trafficlab.pcapng import parse_pcapng_trace, write_pcapng

    traces = (
        TrafficTrace.from_events(
            (
                TraceEvent(0.0, Direction.OUTBOUND, 60),
                TraceEvent(1.0, Direction.INBOUND, 78),
            )
        ),
        TrafficTrace.from_events(
            (
                TraceEvent(0.0, Direction.OUTBOUND, 60),
                TraceEvent(0.000000001, Direction.INBOUND, 78),
            )
        ),
    )
    results: list[CaseResult] = []
    for index, (name, trace) in enumerate(zip(WRITER_CASE_NAMES, traces, strict=True)):
        production_path = directory / f"writer-production-{index}.pcapng"
        candidate_path = directory / f"writer-scapy-{index}.pcapng"
        write_pcapng(production_path, trace, metadata)
        oracle = _call_outcome(lambda path=production_path: parse_pcapng_trace(path, metadata))
        candidate = _call_outcome(
            lambda path=candidate_path, trace=trace: write_with_scapy(
                path,
                trace,
                metadata,
                observation_window_seconds=1.0,
            )
        )
        content = _trace_input(trace)
        results.append(
            _case_result(
                name,
                content,
                comparison_fields=("status", "trace_digest"),
                expected=_call_outcome(lambda trace=trace: trace),
                oracle=oracle,
                candidate=candidate,
                detail="canonical input trace compared with independent production and Scapy writer round trips",
            )
        )
    return _case_evidence(WRITER_CASE_NAMES, tuple(results))


def _malformed_definitions(metadata: CaptureMetadata) -> tuple[tuple[str, bytes], ...]:
    frame = _protocol_frames(metadata)[0]
    valid_packet = _fixture_packet(frame, 1)
    nonzero_packet_padding = bytearray(_fixture_packet(frame[:-1], 1))
    nonzero_packet_padding[8 + 20 + len(frame) - 1] = 1
    nonzero_option = bytearray(_fixture_option(1, b"x") + struct.pack("<HH", 0, 0))
    nonzero_option[5] = 1
    malformed_options = struct.pack("<HH", 1, 8) + b"only"
    return (
        (MALFORMED_CASE_NAMES[0], _fixture_section()[:20]),
        (MALFORMED_CASE_NAMES[1], (_fixture_section() + _fixture_interface() + valid_packet)[:-8]),
        (
            MALFORMED_CASE_NAMES[2],
            _fixture_section() + _fixture_interface() + _fixture_packet(frame, 1)[:-4] + struct.pack("<I", 999),
        ),
        (MALFORMED_CASE_NAMES[3], _fixture_section() + _fixture_interface() * 2 + valid_packet),
        (MALFORMED_CASE_NAMES[4], _fixture_section() + _fixture_interface(linktype=101) + valid_packet),
        (
            MALFORMED_CASE_NAMES[5],
            _fixture_section() + _fixture_interface() + _fixture_packet(frame, 1, interface_id=1),
        ),
        (
            MALFORMED_CASE_NAMES[6],
            _fixture_section() + _fixture_interface() + _fixture_block(3, struct.pack("<I", len(frame)) + frame),
        ),
        (MALFORMED_CASE_NAMES[7], _fixture_section() + _fixture_interface() + _fixture_block(2, b"\x00" * 20)),
        (MALFORMED_CASE_NAMES[8], _fixture_section() + _fixture_interface() + bytes(nonzero_packet_padding)),
        (
            MALFORMED_CASE_NAMES[9],
            _fixture_section() + _fixture_interface() + _fixture_packet(frame, 1, options=bytes(nonzero_option)),
        ),
        (
            MALFORMED_CASE_NAMES[10],
            _fixture_section() + _fixture_interface() + _fixture_packet(frame, 1, options=malformed_options),
        ),
        (
            MALFORMED_CASE_NAMES[11],
            _fixture_section() + _fixture_interface() + _fixture_packet(frame, 2) + _fixture_packet(frame, 1),
        ),
    )


def _execute_malformed_cases(directory: Path, metadata: CaptureMetadata) -> CaseEvidence:
    from trafficlab.pcapng import parse_pcapng_trace

    results: list[CaseResult] = []
    for index, (name, content) in enumerate(_malformed_definitions(metadata)):
        path = directory / f"malformed-{index}.pcapng"
        path.write_bytes(content)
        oracle = _call_outcome(lambda path=path: parse_pcapng_trace(path, metadata))
        candidate = _call_outcome(lambda path=path: read_with_scapy(path, metadata))
        results.append(
            _case_result(
                name,
                content,
                comparison_fields=("status",),
                expected=oracle,
                oracle=oracle,
                candidate=candidate,
                detail="production rejection oracle compared with independent Scapy candidate behavior",
            )
        )
    return _case_evidence(MALFORMED_CASE_NAMES, tuple(results))


class _CallCountClock:
    def __init__(self, expires_on_call: int) -> None:
        self.expires_on_call = expires_on_call
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return 1.0 if self.calls >= self.expires_on_call else 0.0


class _SetupExpiringFactory:
    def __init__(self, factory: _ScapyReaderFactory, now: list[float]) -> None:
        self.factory = factory
        self.now = now

    def __call__(self, filename: str) -> _ScapyReader:
        reader = self.factory(filename)
        self.now[0] = 1.0
        return reader


def _execute_deadline_cases(directory: Path, metadata: CaptureMetadata) -> CaseEvidence:
    from trafficlab.pcapng import parse_pcapng_trace

    frames = (_protocol_frames(metadata)[0],) * 2
    content = _fixture_capture(frames, (0, 1))
    path = directory / "deadline.pcapng"
    path.write_bytes(content)
    reader_factory, _, _, timestamp_type = _scapy_boundaries()

    candidate_operations: tuple[Callable[[], TrafficTrace], ...] = (
        lambda: read_with_scapy(path, metadata, deadline=1.0, clock=lambda: 1.0),
        lambda: _setup_deadline_candidate(path, metadata, reader_factory, timestamp_type),
        lambda: read_with_scapy(path, metadata, deadline=1.0, clock=_CallCountClock(6)),
        lambda: read_with_scapy(path, metadata, deadline=1.0, clock=_CallCountClock(9)),
    )
    oracle_operations: tuple[Callable[[], TrafficTrace], ...] = (
        lambda: parse_pcapng_trace(path, metadata, deadline=1.0, clock=lambda: 1.0),
        lambda: parse_pcapng_trace(path, metadata, deadline=1.0, clock=_CallCountClock(3)),
        lambda: parse_pcapng_trace(path, metadata, deadline=1.0, clock=_CallCountClock(3)),
        lambda: parse_pcapng_trace(path, metadata, deadline=1.0, clock=_CallCountClock(4)),
    )
    details = (
        "deadline expired before candidate I/O",
        "deadline expired during Scapy reader construction before packet read",
        "deadline expired after the first accepted Scapy packet",
        "deadline expired after candidate trace/window postprocessing",
    )
    results = tuple(
        _case_result(
            name,
            content,
            comparison_fields=("status", "exception_type", "corrective_action"),
            expected=NormalizedOutcome(
                status="deadline_exceeded",
                exception_type="DeadlineExceededError",
                message="expected deadline expiration",
                corrective_action=_DEADLINE_ACTION,
                trace_digest=None,
            ),
            oracle=_call_outcome(oracle),
            candidate=_call_outcome(candidate),
            detail=detail,
        )
        for name, oracle, candidate, detail in zip(
            DEADLINE_CASE_NAMES,
            oracle_operations,
            candidate_operations,
            details,
            strict=True,
        )
    )
    return _case_evidence(DEADLINE_CASE_NAMES, results)


def _setup_deadline_candidate(
    path: Path,
    metadata: CaptureMetadata,
    reader_factory: _ScapyReaderFactory,
    timestamp_type: type[SupportsFloat],
) -> TrafficTrace:
    now = [0.0]
    factory = _SetupExpiringFactory(reader_factory, now)
    return _scapy_trace(
        path,
        metadata,
        deadline=1.0,
        clock=lambda: now[0],
        reader_factory=factory,
        timestamp_type=timestamp_type,
    )


def _pcapng_block(block_type: int, body: bytes) -> bytes:
    padded = body + b"\x00" * (-len(body) % 4)
    total = 12 + len(padded)
    return struct.pack("<II", block_type, total) + padded + struct.pack("<I", total)


def _write_benchmark_capture(path: Path, frame_count: int, metadata: CaptureMetadata) -> None:
    frames = _protocol_frames(metadata)
    section = _pcapng_block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    resolution = struct.pack("<HHB3xHH", 9, 1, 9, 0, 0)
    interface = _pcapng_block(1, struct.pack("<HHI", 1, 0, 65_535) + resolution)
    with path.open("wb") as stream:
        stream.write(section)
        stream.write(interface)
        chunk = bytearray()
        for index in range(frame_count):
            frame = frames[index % len(frames)]
            ticks = index * 1_000
            body = struct.pack("<IIIII", 0, ticks >> 32, ticks & _UINT32_MAX, len(frame), len(frame))
            chunk.extend(_pcapng_block(6, body + frame))
            if len(chunk) >= 1024 * 1024:
                stream.write(chunk)
                chunk.clear()
        stream.write(chunk)


def benchmark_child(
    adapter: Literal["production", "scapy"], path: Path, metadata: CaptureMetadata
) -> dict[str, object]:
    input_sha256, input_size_bytes = _stream_file_identity(path)
    if adapter == "production":
        from trafficlab.pcapng import parse_pcapng_trace

        def operation() -> TrafficTrace:
            return parse_pcapng_trace(path, metadata)

    else:
        reader_factory, _, _, timestamp_type = _scapy_boundaries()

        def operation() -> TrafficTrace:
            return _scapy_trace(
                path,
                metadata,
                deadline=None,
                clock=monotonic,
                reader_factory=reader_factory,
                timestamp_type=timestamp_type,
            )

    started = perf_counter()
    trace = operation()
    frame_count = len(trace)
    trace_digest = _trace_digest(trace)
    wall = perf_counter() - started
    return {
        "frame_count": frame_count,
        "trace_digest": trace_digest,
        "input_sha256": input_sha256,
        "input_size_bytes": input_size_bytes,
        "wall_seconds": wall,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def _trace_digest(trace: TrafficTrace) -> str:
    digest = hashlib.sha256()
    digest.update(trace.timestamps.tobytes())
    digest.update(trace.directions.tobytes())
    digest.update(trace.frame_lengths.tobytes())
    return digest.hexdigest()


def _run_child(
    adapter: Literal["production", "scapy"], path: Path, metadata: CaptureMetadata, frame_count: int
) -> dict[str, object]:
    command = (
        *_CANONICAL_PYTHON_COMMAND,
        "-m",
        "tests.scientific.probes.scapy_pcapng",
        "benchmark-child",
        "--adapter",
        adapter,
        "--path",
        str(path),
        "--target-mac",
        metadata.target_mac,
        "--frame-count",
        str(frame_count),
    )
    completed = subprocess.run(
        command,
        cwd=_REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return cast(dict[str, object], json.loads(completed.stdout))


def benchmark_evidence_command(
    adapter: Literal["production", "scapy"], frame_count: Literal[100_000, 1_000_000]
) -> tuple[str, ...]:
    return (
        *_CANONICAL_PYTHON_COMMAND,
        "-m",
        "tests.scientific.probes.scapy_pcapng",
        "benchmark-child",
        "--adapter",
        adapter,
        "--path",
        "<generated-pcapng>",
        "--target-mac",
        "02:42:ac:11:00:02",
        "--frame-count",
        str(frame_count),
    )


def _adapter_benchmark(
    adapter: Literal["production", "scapy"],
    path: Path,
    metadata: CaptureMetadata,
    frame_count: int,
) -> AdapterBenchmark:
    _run_child(adapter, path, metadata, frame_count)
    raw = tuple(_run_child(adapter, path, metadata, frame_count) for _ in range(BENCHMARK_REPETITIONS))
    samples = tuple(
        BenchmarkSample(
            frame_count=cast(Literal[100_000, 1_000_000], item["frame_count"]),
            trace_digest=str(cast(str, item["trace_digest"])),
            input_sha256=str(cast(str, item["input_sha256"])),
            input_size_bytes=int(cast(int, item["input_size_bytes"])),
            wall_seconds=float(cast(float | int, item["wall_seconds"])),
            peak_rss_kib=int(cast(int, item["peak_rss_kib"])),
        )
        for item in raw
    )
    command = benchmark_evidence_command(
        adapter,
        cast(Literal[100_000, 1_000_000], frame_count),
    )
    return AdapterBenchmark(
        command=command,
        samples=samples,
        median_wall_seconds=float(statistics.median(sample.wall_seconds for sample in samples)),
        median_peak_rss_kib=int(statistics.median(sample.peak_rss_kib for sample in samples)),
    )


def run_benchmarks() -> tuple[BenchmarkComparison, BenchmarkComparison]:
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    comparisons: list[BenchmarkComparison] = []
    with tempfile.TemporaryDirectory(prefix="trafficlab-scapy-probe-") as temporary:
        directory = Path(temporary)
        for frame_count in (100_000, 1_000_000):
            path = directory / f"frames-{frame_count}.pcapng"
            _write_benchmark_capture(path, frame_count, metadata)
            production = _adapter_benchmark("production", path, metadata, frame_count)
            scapy = _adapter_benchmark("scapy", path, metadata, frame_count)
            wall_ratio = scapy.median_wall_seconds / production.median_wall_seconds
            rss_ratio = scapy.median_peak_rss_kib / production.median_peak_rss_kib
            identity = len({sample.trace_digest for sample in (*production.samples, *scapy.samples)}) == 1
            comparisons.append(
                BenchmarkComparison(
                    frame_count=frame_count,
                    warmup_runs_per_adapter=1,
                    measured_runs_per_adapter=BENCHMARK_REPETITIONS,
                    production=production,
                    scapy=scapy,
                    trace_identity=identity,
                    wall_ratio=wall_ratio,
                    rss_ratio=rss_ratio,
                    material_threshold_ratio=MAX_MATERIAL_RATIO,
                    time_passed=identity and wall_ratio < MAX_MATERIAL_RATIO,
                    memory_passed=identity and rss_ratio < MAX_MATERIAL_RATIO,
                )
            )
    return cast(tuple[BenchmarkComparison, BenchmarkComparison], tuple(comparisons))


def _file_sha256(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(_REPOSITORY).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def _scapy_version() -> str:
    module = importlib.import_module("scapy")
    return str(module.__version__)


def _execute_functional_evidence() -> tuple[CaseEvidence, CaseEvidence, CaseEvidence, CaseEvidence]:
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    with tempfile.TemporaryDirectory(prefix="trafficlab-scapy-functional-") as temporary:
        directory = Path(temporary)
        return (
            _execute_reader_cases(directory, metadata),
            _execute_writer_cases(directory, metadata),
            _execute_malformed_cases(directory, metadata),
            _execute_deadline_cases(directory, metadata),
        )


def build_probe_evidence(*, run_benchmarks: bool) -> dict[str, object]:
    if _scapy_version() != SCAPY_VERSION:
        raise RuntimeError(f"Scapy version must be {SCAPY_VERSION}")
    reader_differential, writer_differential, malformed, deadline = _execute_functional_evidence()
    typing_run = subprocess.run(
        _TYPING_COMMAND,
        cwd=_REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    typing = TypingEvidence(
        command=_TYPING_COMMAND,
        mode="strict",
        exit_code=typing_run.returncode,
        stdout=typing_run.stdout,
        stderr=typing_run.stderr,
        passed=typing_run.returncode == 0 and "0 errors, 0 warnings, 0 informations" in typing_run.stdout,
    )
    benchmarks = run_benchmarks_fn() if run_benchmarks else ()
    policy = ProbePolicy(
        scapy_version=SCAPY_VERSION,
        development_only=True,
        production_codec="trafficlab.pcapng",
        production_changed=False,
        reader_case_names=READER_CASE_NAMES,
        writer_case_names=WRITER_CASE_NAMES,
        malformed_case_names=MALFORMED_CASE_NAMES,
        deadline_case_names=DEADLINE_CASE_NAMES,
        functional_runner_command=_BOUNDED_BENCHMARK_COMMAND,
        typing_command=_TYPING_COMMAND,
        benchmark_frame_shapes=BENCHMARK_FRAME_SHAPES,
        benchmark_repetitions=BENCHMARK_REPETITIONS,
        warmup_runs_per_adapter=1,
        fresh_subprocess_per_run=True,
        benchmark_runner_command=_BOUNDED_BENCHMARK_COMMAND,
        benchmark_child_module="tests.scientific.probes.scapy_pcapng",
        benchmark_path_placeholder="<generated-pcapng>",
        timing_boundary=_TIMING_BOUNDARY,
        material_regression_rule="reject when Scapy median wall or peak RSS is >=1.50x production at either frame count",
        canonical_comparison="TrafficTrace columns",
    )
    source_files = (
        Path(__file__),
        _REPOSITORY / "tests" / "scientific" / "probes" / "test_scapy_pcapng.py",
        _REPOSITORY / "scripts" / "run_scientific_stack_probes.py",
    )
    environment = ProbeEnvironment(
        python=platform.python_version(),
        platform=platform.platform(),
        machine=platform.machine(),
        scapy=SCAPY_VERSION,
        uv_lock_sha256=hashlib.sha256((_REPOSITORY / "uv.lock").read_bytes()).hexdigest(),
        implementation_sha256=_file_sha256(source_files),
    )
    license_evidence = LicenseEvidence(
        scapy_license_identifier="GPL-2.0-only",
        compatibility_decision="not_made",
        development_only=True,
        scapy_source_copied=False,
        production_import=False,
        legal_advice=False,
        production_adoption_blocked=True,
    )
    provisional = ProbeEvidence.model_construct(
        schema_version=3,
        probe="scapy_pcapng",
        policy=policy,
        environment=environment,
        reader_differential=reader_differential,
        writer_differential=writer_differential,
        malformed=malformed,
        deadline=deadline,
        typing=typing,
        benchmarks=benchmarks,
        license=license_evidence,
        gates=ProbeGates(
            reader_trace_equivalence=False,
            writer_trace_equivalence=False,
            malformed_failures=False,
            deadline_semantics=False,
            strict_typing=False,
            benchmark_100000_time=False,
            benchmark_100000_memory=False,
            benchmark_1000000_time=False,
            benchmark_1000000_memory=False,
            license_compatibility=False,
        ),
        decision=ProbeDecision(
            technical_outcome="reject",
            production_adoption="blocked",
            failed_technical_gates=GATE_NAMES[:-1],
            blocking_gates=("license_compatibility",),
            production_changed=False,
        ),
    )
    gates = derive_gates(provisional)
    evidence = provisional.model_copy(update={"gates": gates, "decision": decide_probe(gates)})
    return cast(dict[str, object], evidence.model_dump(mode="python"))


# Keep the parameter name readable without shadowing the public function.
run_benchmarks_fn = run_benchmarks


def validate_probe_evidence(evidence: dict[str, object]) -> ProbeEvidence:
    validated = ProbeEvidence.model_validate(evidence)
    if (
        validated.policy.reader_case_names != READER_CASE_NAMES
        or validated.policy.writer_case_names != WRITER_CASE_NAMES
        or validated.policy.malformed_case_names != MALFORMED_CASE_NAMES
        or validated.policy.deadline_case_names != DEADLINE_CASE_NAMES
        or validated.policy.functional_runner_command != _BOUNDED_BENCHMARK_COMMAND
        or validated.policy.typing_command != _TYPING_COMMAND
        or validated.policy.benchmark_frame_shapes != BENCHMARK_FRAME_SHAPES
        or validated.policy.benchmark_runner_command != _BOUNDED_BENCHMARK_COMMAND
        or validated.policy.timing_boundary != _TIMING_BOUNDARY
    ):
        raise ValueError("probe evidence does not match the predeclared probe policy")
    source_files = (
        Path(__file__),
        _REPOSITORY / "tests" / "scientific" / "probes" / "test_scapy_pcapng.py",
        _REPOSITORY / "scripts" / "run_scientific_stack_probes.py",
    )
    if validated.environment.implementation_sha256 != _file_sha256(source_files):
        raise ValueError("probe evidence implementation SHA-256 does not match the current source")
    lock_sha256 = hashlib.sha256((_REPOSITORY / "uv.lock").read_bytes()).hexdigest()
    if validated.environment.uv_lock_sha256 != lock_sha256:
        raise ValueError("probe evidence uv.lock SHA-256 does not match the current lock")
    retained_functional = (
        validated.reader_differential,
        validated.writer_differential,
        validated.malformed,
        validated.deadline,
    )
    if retained_functional != _execute_functional_evidence():
        raise ValueError("probe results do not match independently re-executed functional evidence")
    return validated


def render_probe_evidence(evidence: dict[str, object]) -> bytes:
    validated = validate_probe_evidence(evidence)
    return (json.dumps(validated.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_probe_evidence(destination: Path, evidence: dict[str, object], *, check: bool) -> bool:
    rendered = render_probe_evidence(evidence)
    if check:
        try:
            return destination.read_bytes() == rendered
        except OSError:
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return True


def check_probe_evidence(destination: Path) -> bool:
    try:
        raw = cast(dict[str, object], json.loads(destination.read_bytes()))
        evidence = validate_probe_evidence(raw)
    except (OSError, ValueError):
        return False
    if tuple(item.frame_count for item in evidence.benchmarks) != (100_000, 1_000_000):
        return False
    return (
        render_probe_evidence(cast(dict[str, object], evidence.model_dump(mode="python"))) == destination.read_bytes()
    )


def _main(arguments: Sequence[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    child = subparsers.add_parser("benchmark-child")
    child.add_argument("--adapter", choices=("production", "scapy"), required=True)
    child.add_argument("--path", type=Path, required=True)
    child.add_argument("--target-mac", required=True)
    child.add_argument("--frame-count", type=int, required=True)
    parsed = parser.parse_args(arguments)
    if parsed.command != "benchmark-child":
        return 2
    adapter = cast(Literal["production", "scapy"], parsed.adapter)
    path = cast(Path, parsed.path)
    target_mac = cast(str, parsed.target_mac)
    frame_count = cast(int, parsed.frame_count)
    result = benchmark_child(adapter, path, CaptureMetadata(interface="eth0", target_mac=target_mac))
    if result["frame_count"] != frame_count:
        raise RuntimeError("benchmark child parsed an unexpected frame count")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
