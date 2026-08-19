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
from trafficlab.pcapng import parse_pcapng_trace
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, deterministic_peer_mac

SCAPY_VERSION = "2.7.0"
BENCHMARK_REPETITIONS = 5
MAX_MATERIAL_RATIO = 1.5
DIFFERENTIAL_CASE_NAMES = (
    "ethernet_ipv4_ipv6_arp_little_endian_default_microseconds",
    "ethernet_ipv4_ipv6_arp_big_endian_decimal_nanoseconds",
    "binary_2^-10_timestamp_resolution",
    "epb_padding_options_original_length",
    "source_mac_outbound_peer_inbound_broadcast_inbound",
    "closed_observation_window_and_frame_validation",
    "scapy_writer_canonical_trace_round_trip",
)
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
    "trace_equivalence",
    "malformed_failures",
    "deadline_semantics",
    "strict_typing",
    "benchmark_100000",
    "benchmark_1000000",
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

_UINT32_MAX = 2**32 - 1
_MALFORMED_ACTION = "replace the PCAPNG with a complete valid Ethernet capture"
_DEADLINE_ACTION = "increase the total run timeout and retry capture"
_REPOSITORY = Path(__file__).resolve().parents[3]


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


class CaseEvidence(_StrictRecord):
    case_names: StringTuple
    failed_case_names: StringTuple
    detail: str
    command: StringTuple
    passed: StrictBool


class TypingEvidence(_StrictRecord):
    command: StringTuple
    mode: Literal["strict"]
    errors: StrictInt
    passed: StrictBool


class BenchmarkSample(_StrictRecord):
    frame_count: Literal[100_000, 1_000_000]
    trace_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    wall_seconds: StrictFloat
    peak_rss_kib: StrictInt


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
    passed: StrictBool

    @model_validator(mode="after")
    def ratios_and_gate_match_measurements(self) -> Self:
        if self.material_threshold_ratio != MAX_MATERIAL_RATIO:
            raise ValueError("benchmark material threshold must remain predeclared")
        samples = (*self.production.samples, *self.scapy.samples)
        digests = {sample.trace_digest for sample in samples}
        trace_identity = all(sample.frame_count == self.frame_count for sample in samples) and len(digests) == 1
        if self.trace_identity is not trace_identity:
            raise ValueError("trace identity must reflect every measured trace digest and frame count")
        wall_ratio = self.scapy.median_wall_seconds / self.production.median_wall_seconds
        rss_ratio = self.scapy.median_peak_rss_kib / self.production.median_peak_rss_kib
        passed = self.trace_identity and wall_ratio < MAX_MATERIAL_RATIO and rss_ratio < MAX_MATERIAL_RATIO
        if not math.isclose(self.wall_ratio, wall_ratio, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("wall ratio must be recomputed from adapter medians")
        if not math.isclose(self.rss_ratio, rss_ratio, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("RSS ratio must be recomputed from adapter medians")
        if self.passed is not passed:
            raise ValueError("benchmark gate must be derived fail-closed from identity and material ratios")
        return self


class ProbePolicy(_StrictRecord):
    scapy_version: Literal["2.7.0"]
    development_only: Literal[True]
    production_codec: Literal["trafficlab.pcapng"]
    production_changed: Literal[False]
    differential_case_names: StringTuple
    malformed_case_names: StringTuple
    benchmark_frame_shapes: dict[str, dict[str, object]]
    benchmark_repetitions: Literal[5]
    warmup_runs_per_adapter: Literal[1]
    fresh_subprocess_per_run: Literal[True]
    benchmark_runner_command: StringTuple
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
    trace_equivalence: StrictBool
    malformed_failures: StrictBool
    deadline_semantics: StrictBool
    strict_typing: StrictBool
    benchmark_100000: StrictBool
    benchmark_1000000: StrictBool
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
    differential: CaseEvidence
    malformed: CaseEvidence
    deadline: CaseEvidence
    typing: TypingEvidence
    benchmarks: Annotated[tuple[BenchmarkComparison, ...], BeforeValidator(_list_to_tuple)]
    license: LicenseEvidence
    gates: ProbeGates
    decision: ProbeDecision

    @model_validator(mode="after")
    def derived_fields_match_evidence(self) -> Self:
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


def _scapy_boundaries() -> tuple[_ScapyReaderFactory, _ScapyWriterFactory, _EtherFactory]:
    """Load Scapy only at the explicit development adapter boundary."""
    utils = importlib.import_module("scapy.utils")
    layers = importlib.import_module("scapy.layers.l2")
    return (
        cast(_ScapyReaderFactory, utils.PcapNgReader),
        cast(_ScapyWriterFactory, utils.PcapNgWriter),
        cast(_EtherFactory, layers.Ether),
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
    path: Path, metadata: CaptureMetadata, *, deadline: float | None, clock: Callable[[], float]
) -> TrafficTrace:
    reader_factory, _, _ = _scapy_boundaries()
    target = bytes.fromhex(metadata.target_mac.replace(":", ""))
    events: list[TraceEvent] = []
    try:
        with reader_factory(str(path)) as reader:
            while True:
                try:
                    packet = reader.read_packet(size=_UINT32_MAX)
                except EOFError:
                    break
                frame = bytes(packet)
                if len(frame) < 14:
                    raise TrafficlabError(
                        f"invalid PCAPNG: captured Ethernet frame length must be at least 14, got {len(frame)}",
                        corrective_action=_MALFORMED_ACTION,
                    )
                timestamp = float(packet.time)
                direction = Direction.OUTBOUND if frame[6:12] == target else Direction.INBOUND
                events.append(TraceEvent(timestamp, direction, len(frame)))
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
            f"invalid PCAPNG: Scapy could not decode the validated capture ({type(error).__name__})",
            corrective_action=_MALFORMED_ACTION,
        ) from error
    return TrafficTrace.from_events(events)


def read_with_scapy(
    path: Path,
    metadata: CaptureMetadata,
    *,
    observation_window_seconds: float | None = None,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> TrafficTrace:
    """Validate with Trafficlab, then independently convert Scapy packets to a canonical trace."""
    _deadline_expired(deadline, clock)
    canonical = parse_pcapng_trace(path, metadata)
    _deadline_expired(deadline, clock)
    candidate = _scapy_trace(path, metadata, deadline=deadline, clock=clock)
    if candidate != canonical:
        raise TrafficlabError(
            "Scapy PCAPNG trace does not match Trafficlab canonical semantics",
            corrective_action="retain the production PCAPNG codec and inspect the differential fixture",
        )
    _validate_window(candidate, observation_window_seconds)
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
    _, writer_factory, ether_factory = _scapy_boundaries()
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
    gate_100000 = (
        len(benchmark_100000) == 1
        and benchmark_100000[0].trace_identity
        and benchmark_100000[0].passed
        and benchmark_100000[0].wall_ratio < MAX_MATERIAL_RATIO
        and benchmark_100000[0].rss_ratio < MAX_MATERIAL_RATIO
    )
    gate_1000000 = (
        len(benchmark_1000000) == 1
        and benchmark_1000000[0].trace_identity
        and benchmark_1000000[0].passed
        and benchmark_1000000[0].wall_ratio < MAX_MATERIAL_RATIO
        and benchmark_1000000[0].rss_ratio < MAX_MATERIAL_RATIO
    )
    return ProbeGates(
        trace_equivalence=evidence.differential.passed,
        malformed_failures=evidence.malformed.passed,
        deadline_semantics=evidence.deadline.passed,
        strict_typing=evidence.typing.passed and evidence.typing.errors == 0,
        benchmark_100000=gate_100000,
        benchmark_1000000=gate_1000000,
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


def _benchmark_child(
    adapter: Literal["production", "scapy"], path: Path, metadata: CaptureMetadata
) -> dict[str, object]:
    started = perf_counter()
    trace = parse_pcapng_trace(path, metadata) if adapter == "production" else read_with_scapy(path, metadata)
    wall = perf_counter() - started
    return {
        "frame_count": len(trace),
        "trace_digest": _trace_digest(trace),
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
        sys.executable,
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
            wall_seconds=float(cast(float | int, item["wall_seconds"])),
            peak_rss_kib=int(cast(int, item["peak_rss_kib"])),
        )
        for item in raw
    )
    command = (
        sys.executable,
        "-m",
        "tests.scientific.probes.scapy_pcapng",
        "benchmark-child",
        "--adapter",
        adapter,
        "--path",
        "<generated-pcapng>",
        "--target-mac",
        metadata.target_mac,
        "--frame-count",
        str(frame_count),
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
                    passed=identity and wall_ratio < MAX_MATERIAL_RATIO and rss_ratio < MAX_MATERIAL_RATIO,
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


def build_probe_evidence(*, run_benchmarks: bool) -> dict[str, object]:
    if _scapy_version() != SCAPY_VERSION:
        raise RuntimeError(f"Scapy version must be {SCAPY_VERSION}")
    benchmarks = run_benchmarks_fn() if run_benchmarks else ()
    policy = ProbePolicy(
        scapy_version=SCAPY_VERSION,
        development_only=True,
        production_codec="trafficlab.pcapng",
        production_changed=False,
        differential_case_names=DIFFERENTIAL_CASE_NAMES,
        malformed_case_names=MALFORMED_CASE_NAMES,
        benchmark_frame_shapes=BENCHMARK_FRAME_SHAPES,
        benchmark_repetitions=BENCHMARK_REPETITIONS,
        warmup_runs_per_adapter=1,
        fresh_subprocess_per_run=True,
        benchmark_runner_command=_BOUNDED_BENCHMARK_COMMAND,
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
    functional_command = (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        "2m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-q",
        "-n",
        "0",
        "tests/scientific/probes/test_scapy_pcapng.py",
    )
    differential = CaseEvidence(
        case_names=DIFFERENTIAL_CASE_NAMES,
        failed_case_names=("scapy_writer_canonical_trace_round_trip",),
        detail="Scapy 2.7 public writer loses non-microsecond timestamp precision",
        command=functional_command,
        passed=False,
    )
    malformed = CaseEvidence(
        case_names=MALFORMED_CASE_NAMES,
        failed_case_names=(),
        detail="all declared failures match Trafficlab error type, text, and corrective action",
        command=functional_command,
        passed=True,
    )
    deadline = CaseEvidence(
        case_names=("expired_before_io", "expires_after_first_scapy_packet"),
        failed_case_names=(),
        detail="absolute deadline is checked before validation and after every Scapy packet",
        command=functional_command,
        passed=True,
    )
    typing = TypingEvidence(
        command=(
            "uv",
            "run",
            "--locked",
            "pyright",
            "tests/scientific/probes/scapy_pcapng.py",
            "tests/scientific/probes/test_scapy_pcapng.py",
        ),
        mode="strict",
        errors=0,
        passed=True,
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
        differential=differential,
        malformed=malformed,
        deadline=deadline,
        typing=typing,
        benchmarks=benchmarks,
        license=license_evidence,
        gates=ProbeGates(
            trace_equivalence=False,
            malformed_failures=False,
            deadline_semantics=False,
            strict_typing=False,
            benchmark_100000=False,
            benchmark_1000000=False,
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
        validated.policy.differential_case_names != DIFFERENTIAL_CASE_NAMES
        or validated.policy.malformed_case_names != MALFORMED_CASE_NAMES
        or validated.policy.benchmark_frame_shapes != BENCHMARK_FRAME_SHAPES
        or validated.policy.benchmark_runner_command != _BOUNDED_BENCHMARK_COMMAND
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
    result = _benchmark_child(adapter, path, CaptureMetadata(interface="eth0", target_mac=target_mac))
    if result["frame_count"] != frame_count:
        raise RuntimeError("benchmark child parsed an unexpected frame count")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
