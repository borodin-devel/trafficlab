#!/usr/bin/env python3
"""Measure deterministic scalar and production NumPy scientific kernels."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import resource
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab.similarity.autocorrelation import _sample_autocorrelations  # pyright: ignore[reportPrivateUsage]
from trafficlab.similarity.multiscale import _binned_features, _snap_near_integer  # pyright: ignore[reportPrivateUsage]
from trafficlab.trace import TrafficTrace, normalize_reference

REPOSITORY = Path(__file__).resolve().parents[1]
OUTPUT = REPOSITORY / "examples" / "scientific_stack" / "benchmark.json"
SEED = 20260819
BIT_GENERATOR = "PCG64"
EVENT_COUNT = 1_000_000
WARMUP_SUBPROCESS_COUNT = 1
MEASURED_SUBPROCESS_COUNT = 5
AGREEMENT_TOLERANCE = 1e-12
MINIMUM_SPEEDUP = 3.0
MAXIMUM_PEAK_RSS_RATIO = 0.5
SELECTED_LAGS = (1, 4, 16, 64)
MULTISCALE_WIDTH_FRACTIONS = (1.0 / 1024.0, 1.0 / 128.0, 1.0 / 16.0)
_TIMING_NAMES = ("normalization", "iat", "multiscale", "selected_lag_acf", "combined_multiscale_acf")
_IMPLEMENTATIONS = ("scalar", "vector")
type _KernelValue = NDArray[np.generic] | tuple[NDArray[np.int64], NDArray[np.int64]]
_SOURCE_PATHS = (
    "scripts/benchmark_scientific_stack.py",
    "src/trafficlab/similarity/autocorrelation.py",
    "src/trafficlab/similarity/multiscale.py",
    "src/trafficlab/trace.py",
)
_PARENT_COMMAND = (
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
    "scripts/benchmark_scientific_stack.py",
)


def canonical_json_bytes(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def generate_benchmark_trace(event_count: int = EVENT_COUNT) -> TrafficTrace:
    """Generate the locked one-stream PCG64 benchmark input."""
    if type(event_count) is not int or event_count <= 0:
        raise ValueError("benchmark event count must be a positive integer")
    rng = np.random.Generator(np.random.PCG64(SEED))
    timestamps = np.cumsum(rng.exponential(scale=0.001, size=event_count), dtype=np.float64)
    directions = rng.binomial(1, 0.5, size=event_count).astype(np.uint8)
    frame_lengths = rng.integers(60, 1515, size=event_count, dtype=np.uint32, endpoint=True)
    return TrafficTrace(timestamps, directions, frame_lengths)


def _identity(values: NDArray[np.generic]) -> str:
    return hashlib.sha256(values.tobytes()).hexdigest()


def _trace_identity(trace: TrafficTrace) -> dict[str, str]:
    return {
        "directions": _identity(trace.directions),
        "frame_lengths": _identity(trace.frame_lengths),
        "timestamps": _identity(trace.timestamps),
    }


def _widths(trace: TrafficTrace) -> tuple[float, ...]:
    window = float(trace.timestamps[-1] - trace.timestamps[0])
    return tuple(window * fraction for fraction in MULTISCALE_WIDTH_FRACTIONS)


def _scalar_normalization(trace: TrafficTrace) -> NDArray[np.float64]:
    start = float(trace.timestamps[0])
    return np.asarray([float(timestamp) - start for timestamp in trace.timestamps], dtype=np.float64)


def _vector_normalization(trace: TrafficTrace) -> NDArray[np.float64]:
    normalized, _window = normalize_reference(trace)
    return normalized.timestamps


def _scalar_iat(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.asarray(
        [float(trace.timestamps[index]) - float(trace.timestamps[index - 1]) for index in range(1, len(trace))],
        dtype=np.float64,
    )


def _vector_iat(trace: TrafficTrace) -> NDArray[np.float64]:
    return trace.iats()


def _scalar_multiscale(trace: TrafficTrace) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    normalized = _scalar_normalization(trace)
    packet_parts: list[NDArray[np.int64]] = []
    byte_parts: list[NDArray[np.int64]] = []
    for width in _widths(trace):
        bin_count = math.ceil(_snap_near_integer(float(normalized[-1]) / width))
        packets = np.zeros(2 * bin_count, dtype=np.int64)
        byte_counts = np.zeros(2 * bin_count, dtype=np.int64)
        for timestamp, direction, frame_length in zip(normalized, trace.directions, trace.frame_lengths, strict=True):
            quotient = _snap_near_integer(float(timestamp) / width)
            index = min(math.floor(quotient), bin_count - 1) + int(direction) * bin_count
            packets[index] += 1
            byte_counts[index] += int(frame_length)
        packet_parts.append(packets)
        byte_parts.append(byte_counts)
    return np.concatenate(packet_parts), np.concatenate(byte_parts)


def _vector_multiscale(trace: TrafficTrace) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    normalized, _window = normalize_reference(trace)
    packet_parts: list[NDArray[np.int64]] = []
    byte_parts: list[NDArray[np.int64]] = []
    for width in _widths(trace):
        bin_count = math.ceil(_snap_near_integer(float(normalized.timestamps[-1]) / width))
        packets, byte_counts = _binned_features(normalized, width=width, bins_per_direction=bin_count)
        packet_parts.append(np.asarray(packets, dtype=np.int64))
        byte_parts.append(np.asarray(byte_counts, dtype=np.int64))
    return np.concatenate(packet_parts), np.concatenate(byte_parts)


def _scalar_acf_value(values: NDArray[np.uint32], lag: int) -> float:
    largest = max(int(value) for value in values)
    _, exponent = math.frexp(float(largest))
    scaled = tuple(math.ldexp(float(value), -exponent) for value in values)
    mean = math.fsum(scaled) / len(scaled)
    centered = tuple(value - mean for value in scaled)
    denominator = math.fsum(value * value for value in centered)
    if denominator == 0.0:
        return 0.0
    numerator = math.fsum(centered[index] * centered[index + lag] for index in range(len(centered) - lag))
    return numerator / denominator


def _scalar_selected_acf(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.asarray([_scalar_acf_value(trace.frame_lengths, lag) for lag in SELECTED_LAGS], dtype=np.float64)


def _vector_selected_acf(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.asarray(_sample_autocorrelations(trace.frame_lengths, SELECTED_LAGS), dtype=np.float64)


def _kernel_results(
    trace: TrafficTrace, implementation: Literal["scalar", "vector"]
) -> dict[str, NDArray[np.generic] | tuple[NDArray[np.int64], NDArray[np.int64]]]:
    if implementation == "scalar":
        return {
            "normalization": _scalar_normalization(trace),
            "iat": _scalar_iat(trace),
            "multiscale": _scalar_multiscale(trace),
            "selected_lag_acf": _scalar_selected_acf(trace),
        }
    return {
        "normalization": _vector_normalization(trace),
        "iat": _vector_iat(trace),
        "multiscale": _vector_multiscale(trace),
        "selected_lag_acf": _vector_selected_acf(trace),
    }


def _max_abs_error(left: NDArray[np.generic], right: NDArray[np.generic]) -> float:
    if left.shape != right.shape:
        return math.inf
    if left.size == 0:
        return 0.0
    return float(np.max(np.abs(left.astype(np.float64) - right.astype(np.float64))))


def compare_kernel_results(trace: TrafficTrace) -> dict[str, dict[str, float | bool]]:
    """Compare independent scalar results with the production vector paths."""
    scalar = _kernel_results(trace, "scalar")
    vector = _kernel_results(trace, "vector")
    result: dict[str, dict[str, float | bool]] = {}
    for name in ("normalization", "iat", "multiscale", "selected_lag_acf"):
        scalar_value = scalar[name]
        vector_value = vector[name]
        if name == "multiscale":
            scalar_packets, scalar_bytes = cast(tuple[NDArray[np.int64], NDArray[np.int64]], scalar_value)
            vector_packets, vector_bytes = cast(tuple[NDArray[np.int64], NDArray[np.int64]], vector_value)
            error = max(
                _max_abs_error(scalar_packets, vector_packets),
                _max_abs_error(scalar_bytes, vector_bytes),
            )
        else:
            error = _max_abs_error(cast(NDArray[np.generic], scalar_value), cast(NDArray[np.generic], vector_value))
        result[name] = {"max_abs_error": error, "passed": error <= AGREEMENT_TOLERANCE}
    return result


def _result_digest(value: NDArray[np.generic] | tuple[NDArray[np.int64], NDArray[np.int64]]) -> str:
    digest = hashlib.sha256()
    arrays = value if isinstance(value, tuple) else (value,)
    for array in arrays:
        digest.update(str(array.dtype).encode("ascii"))
        digest.update(b"\0")
        digest.update(array.tobytes())
    return digest.hexdigest()


def _timed(operation: Callable[[], _KernelValue]) -> tuple[float, str]:
    started = time.perf_counter()
    value = operation()
    elapsed = time.perf_counter() - started
    return elapsed, _result_digest(value)


def benchmark_child(implementation: Literal["scalar", "vector"], event_count: int) -> dict[str, Any]:
    trace = generate_benchmark_trace(event_count)
    if implementation == "scalar":
        operations: dict[str, Callable[[], _KernelValue]] = {
            "normalization": lambda: _scalar_normalization(trace),
            "iat": lambda: _scalar_iat(trace),
            "multiscale": lambda: _scalar_multiscale(trace),
            "selected_lag_acf": lambda: _scalar_selected_acf(trace),
        }
    else:
        operations = {
            "normalization": lambda: _vector_normalization(trace),
            "iat": lambda: _vector_iat(trace),
            "multiscale": lambda: _vector_multiscale(trace),
            "selected_lag_acf": lambda: _vector_selected_acf(trace),
        }
    wall: dict[str, float] = {}
    identities: dict[str, str] = {}
    for name, operation in operations.items():
        wall[name], identities[name] = _timed(operation)
    wall["combined_multiscale_acf"] = wall["multiscale"] + wall["selected_lag_acf"]
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "event_count": event_count,
        "input_identity": _trace_identity(trace),
        "peak_rss_kib": peak_rss,
        "result_identities": identities,
        "wall_seconds": wall,
    }


def _child_command(implementation: str, event_count: int) -> tuple[str, ...]:
    return (
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--implementation",
        implementation,
        "--event-count",
        str(event_count),
    )


def _run_child(implementation: str, event_count: int) -> dict[str, Any]:
    command = _child_command(implementation, event_count)
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=600)
    if completed.returncode != 0:
        raise ValueError(
            f"{implementation} benchmark child failed with status {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        document = cast(dict[str, Any], json.loads(completed.stdout))
    except json.JSONDecodeError as error:
        raise ValueError(f"{implementation} benchmark child returned invalid JSON") from error
    return document


def _sample_record(raw: dict[str, Any], ordinal: int) -> dict[str, Any]:
    return {"fresh_subprocess": True, "ordinal": ordinal, **raw}


def _measure_implementation(implementation: str, event_count: int) -> dict[str, Any]:
    warmups = [
        _sample_record(_run_child(implementation, event_count), ordinal)
        for ordinal in range(1, WARMUP_SUBPROCESS_COUNT + 1)
    ]
    samples = [
        _sample_record(_run_child(implementation, event_count), ordinal)
        for ordinal in range(1, MEASURED_SUBPROCESS_COUNT + 1)
    ]
    medians = {
        **{
            name: statistics.median(cast(float, sample["wall_seconds"][name]) for sample in samples)
            for name in _TIMING_NAMES
        },
        "peak_rss_kib": statistics.median(cast(int, sample["peak_rss_kib"]) for sample in samples),
    }
    return {"medians": medians, "samples": samples, "warmups": warmups}


def _file_identity(path: Path) -> dict[str, int | str]:
    content = path.read_bytes()
    return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def _environment(repository_root: Path) -> dict[str, Any]:
    return {
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("numpy", "pydantic", "scipy", "trafficlab")
        },
        "machine": platform.machine(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "source_files": {path: _file_identity(repository_root / path) for path in _SOURCE_PATHS},
        "uv_lock_identity": _file_identity(repository_root / "uv.lock"),
    }


def build_benchmark_evidence(repository_root: Path = REPOSITORY, *, event_count: int = EVENT_COUNT) -> dict[str, Any]:
    """Run the full differential and 1+5 fresh-subprocess protocol."""
    root = repository_root.resolve()
    trace = generate_benchmark_trace(event_count)
    agreement = compare_kernel_results(trace)
    implementations = {
        implementation: _measure_implementation(implementation, event_count) for implementation in _IMPLEMENTATIONS
    }
    scalar_medians = implementations["scalar"]["medians"]
    vector_medians = implementations["vector"]["medians"]
    speedup = scalar_medians["combined_multiscale_acf"] / vector_medians["combined_multiscale_acf"]
    peak_ratio = vector_medians["peak_rss_kib"] / scalar_medians["peak_rss_kib"]
    passed_by = [
        name
        for name, passed in (
            ("combined_multiscale_acf_speedup", speedup >= MINIMUM_SPEEDUP),
            ("peak_rss", peak_ratio <= MAXIMUM_PEAK_RSS_RATIO),
        )
        if passed
    ]
    evidence: dict[str, Any] = {
        "agreement": agreement,
        "comparison": {
            "combined_multiscale_acf_speedup": speedup,
            "peak_rss_ratio": peak_ratio,
        },
        "dataset": {
            "bit_generator": BIT_GENERATOR,
            "event_count": event_count,
            "identity": _trace_identity(trace),
            "seed": SEED,
        },
        "decision": {
            "passed": all(cast(bool, item["passed"]) for item in agreement.values()) and bool(passed_by),
            "passed_by": passed_by,
        },
        "environment": _environment(root),
        "implementations": implementations,
        "protocol": {
            "acceptance_logic": "combined_multiscale_acf_speedup >= 3.0 OR peak_rss_ratio <= 0.5",
            "agreement_tolerance": AGREEMENT_TOLERANCE,
            "child_command_template": [
                "<repository-python>",
                "scripts/benchmark_scientific_stack.py",
                "--child",
                "--implementation",
                "<scalar-or-vector>",
                "--event-count",
                str(event_count),
            ],
            "measured_subprocess_count": MEASURED_SUBPROCESS_COUNT,
            "minimum_speedup": MINIMUM_SPEEDUP,
            "multiscale_width_fractions": list(MULTISCALE_WIDTH_FRACTIONS),
            "parent_command": list(_PARENT_COMMAND),
            "peak_rss_ratio_maximum": MAXIMUM_PEAK_RSS_RATIO,
            "selected_lags": list(SELECTED_LAGS),
            "warmup_subprocess_count": WARMUP_SUBPROCESS_COUNT,
        },
        "schema_version": 1,
    }
    validate_evidence(evidence, repository_root=root, expected_event_count=event_count)
    return evidence


def _strict_number(value: object, *, name: str, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(cast(float, value))):
        raise ValueError(f"{name} must be finite")
    numeric = float(cast(float, value))
    if positive and numeric <= 0.0:
        raise ValueError(f"{name} must be positive")
    return numeric


def _validate_measurements(
    value: object,
    *,
    implementation: str,
    dataset: Mapping[str, object],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{implementation} measurements must be an object")
    measurements = cast(dict[str, Any], value)
    if set(measurements) != {"medians", "samples", "warmups"}:
        raise ValueError(f"{implementation} measurements have invalid fields")
    warmups = cast(list[dict[str, Any]], measurements["warmups"])
    samples = cast(list[dict[str, Any]], measurements["samples"])
    if len(warmups) != WARMUP_SUBPROCESS_COUNT:
        raise ValueError(f"{implementation} must retain one warmup subprocess")
    if len(samples) != MEASURED_SUBPROCESS_COUNT:
        raise ValueError(f"{implementation} must retain five measured subprocess samples")
    expected_identity = dataset["identity"]
    result_identities: dict[str, str] | None = None
    for group_name, group in (("warmup", warmups), ("measured", samples)):
        for ordinal, sample in enumerate(group, start=1):
            if sample.get("ordinal") != ordinal or sample.get("fresh_subprocess") is not True:
                raise ValueError(f"{implementation} {group_name} samples must be ordered fresh subprocesses")
            if sample.get("event_count") != dataset["event_count"] or sample.get("input_identity") != expected_identity:
                raise ValueError(f"{implementation} {group_name} sample input does not match dataset")
            peak = sample.get("peak_rss_kib")
            if type(peak) is not int or peak <= 0:
                raise ValueError(f"{implementation} {group_name} peak RSS must be positive")
            raw_wall = sample.get("wall_seconds")
            if not isinstance(raw_wall, dict):
                raise ValueError(f"{implementation} {group_name} timings are incomplete")
            wall = cast(dict[str, object], raw_wall)
            if set(wall) != set(_TIMING_NAMES):
                raise ValueError(f"{implementation} {group_name} timings are incomplete")
            for timing in _TIMING_NAMES:
                _strict_number(wall[timing], name=f"{implementation} {timing}", positive=True)
            if not math.isclose(
                cast(float, wall["combined_multiscale_acf"]),
                cast(float, wall["multiscale"]) + cast(float, wall["selected_lag_acf"]),
                rel_tol=0.0,
                abs_tol=1e-15,
            ):
                raise ValueError(f"{implementation} combined timing does not match components")
            raw_identities = sample.get("result_identities")
            if not isinstance(raw_identities, dict):
                raise ValueError(f"{implementation} result identities are invalid")
            identities = cast(dict[object, object], raw_identities)
            if (
                not all(isinstance(key, str) for key in identities)
                or set(identities)
                != {
                    "iat",
                    "multiscale",
                    "normalization",
                    "selected_lag_acf",
                }
                or any(not isinstance(item, str) or len(item) != 64 for item in identities.values())
            ):
                raise ValueError(f"{implementation} result identities are invalid")
            if result_identities is None:
                result_identities = cast(dict[str, str], identities)
            elif identities != result_identities:
                raise ValueError(f"{implementation} result identities are not deterministic")
    expected_medians = {
        **{
            name: statistics.median(cast(float, sample["wall_seconds"][name]) for sample in samples)
            for name in _TIMING_NAMES
        },
        "peak_rss_kib": statistics.median(cast(int, sample["peak_rss_kib"]) for sample in samples),
    }
    if measurements["medians"] != expected_medians:
        raise ValueError(f"{implementation} medians do not match raw samples")
    return measurements


def _validate_root(evidence: Mapping[str, object]) -> None:
    if (
        set(evidence)
        != {
            "agreement",
            "comparison",
            "dataset",
            "decision",
            "environment",
            "implementations",
            "protocol",
            "schema_version",
        }
        or evidence.get("schema_version") != 1
    ):
        raise ValueError("benchmark root is invalid")


def _validate_dataset_protocol(evidence: Mapping[str, object], expected_event_count: int) -> dict[str, object]:
    dataset = cast(dict[str, object], evidence["dataset"])
    expected_trace = generate_benchmark_trace(expected_event_count)
    if dataset != {
        "bit_generator": BIT_GENERATOR,
        "event_count": expected_event_count,
        "identity": _trace_identity(expected_trace),
        "seed": SEED,
    }:
        raise ValueError("benchmark dataset does not match locked PCG64 input")
    protocol = cast(dict[str, object], evidence["protocol"])
    if protocol != {
        "acceptance_logic": "combined_multiscale_acf_speedup >= 3.0 OR peak_rss_ratio <= 0.5",
        "agreement_tolerance": AGREEMENT_TOLERANCE,
        "child_command_template": [
            "<repository-python>",
            "scripts/benchmark_scientific_stack.py",
            "--child",
            "--implementation",
            "<scalar-or-vector>",
            "--event-count",
            str(expected_event_count),
        ],
        "measured_subprocess_count": MEASURED_SUBPROCESS_COUNT,
        "minimum_speedup": MINIMUM_SPEEDUP,
        "multiscale_width_fractions": list(MULTISCALE_WIDTH_FRACTIONS),
        "parent_command": list(_PARENT_COMMAND),
        "peak_rss_ratio_maximum": MAXIMUM_PEAK_RSS_RATIO,
        "selected_lags": list(SELECTED_LAGS),
        "warmup_subprocess_count": WARMUP_SUBPROCESS_COUNT,
    }:
        raise ValueError("benchmark protocol does not match locked policy")
    return dataset


def _validate_agreement(evidence: Mapping[str, object]) -> dict[str, dict[str, object]]:
    agreement = cast(dict[str, dict[str, object]], evidence["agreement"])
    if set(agreement) != {"normalization", "iat", "multiscale", "selected_lag_acf"}:
        raise ValueError("benchmark agreement components are incomplete")
    for name, component in agreement.items():
        error = _strict_number(component.get("max_abs_error"), name=f"{name} max error")
        if component.get("passed") is not (error <= AGREEMENT_TOLERANCE):
            raise ValueError(f"{name} agreement gate does not match its error")
    return agreement


def _validated_implementations(
    evidence: Mapping[str, object], dataset: Mapping[str, object]
) -> dict[str, dict[str, Any]]:
    implementations_value = cast(dict[str, object], evidence["implementations"])
    if tuple(implementations_value) != _IMPLEMENTATIONS:
        raise ValueError("benchmark implementations are invalid")
    return {
        name: _validate_measurements(implementations_value[name], implementation=name, dataset=dataset)
        for name in _IMPLEMENTATIONS
    }


def _validate_decision(
    evidence: Mapping[str, object],
    agreement: Mapping[str, Mapping[str, object]],
    implementations: Mapping[str, Mapping[str, Any]],
) -> None:
    scalar = implementations["scalar"]["medians"]
    vector = implementations["vector"]["medians"]
    speedup = cast(float, scalar["combined_multiscale_acf"]) / cast(float, vector["combined_multiscale_acf"])
    peak_ratio = cast(float, vector["peak_rss_kib"]) / cast(float, scalar["peak_rss_kib"])
    expected_comparison = {
        "combined_multiscale_acf_speedup": speedup,
        "peak_rss_ratio": peak_ratio,
    }
    if evidence["comparison"] != expected_comparison:
        raise ValueError("benchmark comparison does not match raw medians")
    passed_by = [
        name
        for name, passed in (
            ("combined_multiscale_acf_speedup", speedup >= MINIMUM_SPEEDUP),
            ("peak_rss", peak_ratio <= MAXIMUM_PEAK_RSS_RATIO),
        )
        if passed
    ]
    expected_decision = {
        "passed": all(cast(bool, component["passed"]) for component in agreement.values()) and bool(passed_by),
        "passed_by": passed_by,
    }
    if evidence["decision"] != expected_decision:
        raise ValueError("benchmark decision does not match measured gates")


def validate_evidence(
    evidence: Mapping[str, object],
    *,
    repository_root: Path = REPOSITORY,
    expected_event_count: int = EVENT_COUNT,
) -> None:
    """Recompute every equality, sample, median, environment, and decision gate."""
    _validate_root(evidence)
    dataset = _validate_dataset_protocol(evidence, expected_event_count)
    agreement = _validate_agreement(evidence)
    implementations = _validated_implementations(evidence, dataset)
    _validate_decision(evidence, agreement, implementations)
    if evidence["environment"] != _environment(repository_root.resolve()):
        raise ValueError("benchmark environment does not match current lock and host")


def parse_and_validate_evidence(content: bytes, *, repository_root: Path = REPOSITORY) -> dict[str, Any]:
    try:
        evidence = cast(dict[str, Any], json.loads(content))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("benchmark evidence is not valid JSON") from error
    validate_evidence(evidence, repository_root=repository_root)
    if content != canonical_json_bytes(evidence):
        raise ValueError("benchmark evidence is not canonical JSON")
    return evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--implementation", choices=_IMPLEMENTATIONS, help=argparse.SUPPRESS)
    parser.add_argument("--event-count", type=int, default=EVENT_COUNT, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    event_count = cast(int, arguments.event_count)
    if cast(bool, arguments.child):
        implementation = cast(str | None, arguments.implementation)
        if implementation not in _IMPLEMENTATIONS:
            print("benchmark: child requires --implementation", file=sys.stderr)
            return 2
        print(json.dumps(benchmark_child(implementation, event_count), allow_nan=False))
        return 0
    output = cast(Path, arguments.output)
    try:
        if cast(bool, arguments.check):
            evidence = parse_and_validate_evidence(output.read_bytes(), repository_root=REPOSITORY)
            print(f"scientific-stack-benchmark: verified passing evidence at {output}")
            return 0 if cast(bool, evidence["decision"]["passed"]) else 1
        if event_count != EVENT_COUNT:
            raise ValueError("retained benchmark generation requires exactly 1,000,000 events")
        evidence = build_benchmark_evidence(REPOSITORY, event_count=event_count)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_json_bytes(evidence))
        print(f"scientific-stack-benchmark: wrote evidence at {output}")
        return 0 if cast(bool, evidence["decision"]["passed"]) else 1
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"scientific-stack-benchmark: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
