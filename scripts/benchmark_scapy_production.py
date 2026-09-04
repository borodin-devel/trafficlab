#!/usr/bin/env python3
"""Generate or verify non-gating production Scapy performance evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import statistics
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Annotated, Literal, Self, cast

import numpy as np
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictFloat, StrictInt, model_validator

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab.common.json import render_json_document
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng
from trafficlab.common.trace import CaptureMetadata, TrafficTrace

_REPOSITORY = Path(__file__).resolve().parents[1]
_OUTPUT = _REPOSITORY / "examples" / "scientific_stack" / "scapy_production_benchmark.json"
_FRAME_COUNTS = (100_000, 1_000_000)
_REPETITIONS = 5
_WARMUPS = 1
_COMMAND = (
    "scripts/run_bounded.sh",
    "--memory-high",
    "6G",
    "--memory-max",
    "8G",
    "--swap-max",
    "1G",
    "--wall-time",
    "20m",
    "--kill-after",
    "10s",
    "--",
    "uv",
    "run",
    "--locked",
    "python",
    "scripts/benchmark_scapy_production.py",
)
_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_SOURCE_PATHS = (
    Path(__file__),
    _REPOSITORY / "src" / "trafficlab" / "common" / "scapy_io" / "trace.py",
    _REPOSITORY / "src" / "trafficlab" / "common" / "trace.py",
)


def _list_to_tuple(value: object) -> object:
    return tuple(cast(list[object], value)) if type(value) is list else value


type StrictTuple[T] = Annotated[tuple[T, ...], BeforeValidator(_list_to_tuple)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class SampleRecord(_StrictModel):
    encode_wall_seconds: Annotated[StrictFloat, Field(gt=0.0)]
    frame_count: Annotated[StrictInt, Field(gt=0)]
    input_trace_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    output_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    output_size_bytes: Annotated[StrictInt, Field(gt=0)]
    peak_rss_kib: Annotated[StrictInt, Field(gt=0)]
    read_wall_seconds: Annotated[StrictFloat, Field(gt=0.0)]
    trace_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CaseRecord(_StrictModel):
    frame_count: Annotated[StrictInt, Field(gt=0)]
    warmup_runs: Annotated[StrictInt, Field(ge=0)]
    measured_runs: Annotated[StrictInt, Field(gt=0)]
    samples: StrictTuple[SampleRecord]
    median_encode_wall_seconds: Annotated[StrictFloat, Field(gt=0.0)]
    median_read_wall_seconds: Annotated[StrictFloat, Field(gt=0.0)]
    median_peak_rss_kib: Annotated[StrictInt, Field(gt=0)]

    @classmethod
    def from_samples(cls, frame_count: int, samples: tuple[SampleRecord, ...], *, warmup_runs: int) -> CaseRecord:
        return cls(
            frame_count=frame_count,
            warmup_runs=warmup_runs,
            measured_runs=len(samples),
            samples=samples,
            median_encode_wall_seconds=float(statistics.median(sample.encode_wall_seconds for sample in samples)),
            median_read_wall_seconds=float(statistics.median(sample.read_wall_seconds for sample in samples)),
            median_peak_rss_kib=int(statistics.median(sample.peak_rss_kib for sample in samples)),
        )

    @model_validator(mode="after")
    def measurements_are_self_consistent(self) -> Self:
        expected_encode = float(statistics.median(sample.encode_wall_seconds for sample in self.samples))
        expected_read = float(statistics.median(sample.read_wall_seconds for sample in self.samples))
        expected_rss = int(statistics.median(sample.peak_rss_kib for sample in self.samples))
        if (
            self.measured_runs != len(self.samples)
            or self.median_encode_wall_seconds != expected_encode
            or self.median_read_wall_seconds != expected_read
            or self.median_peak_rss_kib != expected_rss
        ):
            raise ValueError("case medians and run counts must derive from raw samples")
        if any(sample.frame_count != self.frame_count for sample in self.samples):
            raise ValueError("every sample must match the case frame count")
        if (
            len(
                {
                    (
                        sample.input_trace_sha256,
                        sample.output_sha256,
                        sample.output_size_bytes,
                        sample.trace_digest,
                    )
                    for sample in self.samples
                }
            )
            != 1
        ):
            raise ValueError("every repeated sample must produce identical input, bytes, and trace columns")
        return self


class EnvironmentRecord(_StrictModel):
    implementation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    machine: str
    platform: str
    python: str
    scapy: Literal["2.7.0"]
    source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    source_tree: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    uv_lock_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DiagnosticRecord(_StrictModel):
    schema_version: Literal[2]
    codec: Literal["scapy-2.7.0"]
    production: Literal[True]
    command: StrictTuple[str]
    environment: EnvironmentRecord
    cases: StrictTuple[CaseRecord]


def _canonical_json(document: Mapping[str, object]) -> bytes:
    return render_json_document(document)


def _canonical_json_line(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def render_diagnostic(
    document: Mapping[str, object],
    *,
    expected_frame_counts: tuple[int, ...] = _FRAME_COUNTS,
    expected_repetitions: int = _REPETITIONS,
) -> bytes:
    """Strictly validate and canonically render retained diagnostic evidence."""
    record = DiagnosticRecord.model_validate(document)
    if record.command != _COMMAND:
        raise ValueError("diagnostic command must be the canonical bounded command")
    if tuple(case.frame_count for case in record.cases) != expected_frame_counts:
        raise ValueError("diagnostic frame counts must match the declared order")
    if any(case.measured_runs != expected_repetitions for case in record.cases):
        raise ValueError("diagnostic cases must retain the declared repetition count")
    return _canonical_json(cast(dict[str, object], record.model_dump(mode="json")))


def _trace(frame_count: int) -> TrafficTrace:
    timestamps = np.arange(frame_count, dtype=np.float64) / 1_000_000
    directions = (np.arange(frame_count, dtype=np.uint64) % 2).astype(np.uint8)
    lengths = np.resize(np.asarray((60, 78, 64), dtype=np.uint32), frame_count)
    return TrafficTrace(timestamps, directions, lengths)


def _trace_digest(trace: TrafficTrace) -> str:
    digest = hashlib.sha256()
    digest.update(trace.timestamps.tobytes())
    digest.update(trace.directions.tobytes())
    digest.update(trace.frame_lengths.tobytes())
    return digest.hexdigest()


def _sample(frame_count: int) -> SampleRecord:
    trace = _trace(frame_count)
    window = max(1.0, float(trace.timestamps[-1]))
    started = perf_counter()
    encoded = encode_pcapng(trace, _METADATA, observation_window_seconds=window)
    encode_wall = perf_counter() - started
    with TemporaryDirectory(prefix="trafficlab-scapy-diagnostic-") as temporary:
        capture = Path(temporary) / "diagnostic.pcapng"
        capture.write_bytes(encoded.content)
        started = perf_counter()
        parsed = read_pcapng(capture, _METADATA)
        read_wall = perf_counter() - started
    if parsed != encoded.trace:
        raise RuntimeError("diagnostic read does not match encoded Scapy output")
    return SampleRecord(
        encode_wall_seconds=encode_wall,
        frame_count=frame_count,
        input_trace_sha256=_trace_digest(trace),
        output_sha256=hashlib.sha256(encoded.content).hexdigest(),
        output_size_bytes=len(encoded.content),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        read_wall_seconds=read_wall,
        trace_digest=_trace_digest(parsed),
    )


def _child_command(frame_count: int) -> tuple[str, ...]:
    return (
        "uv",
        "run",
        "--locked",
        "python",
        "scripts/benchmark_scapy_production.py",
        "--child-frame-count",
        str(frame_count),
    )


def _run_child(frame_count: int) -> SampleRecord:
    completed = subprocess.run(
        _child_command(frame_count),
        cwd=_REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return SampleRecord.model_validate(cast(object, json.loads(completed.stdout)))


def _implementation_sha256() -> str:
    digest = hashlib.sha256()
    for path in _SOURCE_PATHS:
        digest.update(path.relative_to(_REPOSITORY).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def _current_lock_sha256() -> str:
    return hashlib.sha256((_REPOSITORY / "uv.lock").read_bytes()).hexdigest()


def _git_output(*arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=_REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity() -> tuple[str, str]:
    if _git_output("status", "--porcelain=v1", "--untracked-files=all", "--no-renames"):
        raise RuntimeError("production Scapy diagnostic requires a clean source checkout")
    return _git_output("rev-parse", "HEAD"), _git_output("rev-parse", "HEAD^{tree}")


def _recorded_source_matches(environment: EnvironmentRecord) -> bool:
    try:
        recorded_tree = _git_output("rev-parse", f"{environment.source_commit}^{{tree}}")
        changed = subprocess.run(
            (
                "git",
                "diff",
                "--quiet",
                environment.source_commit,
                "HEAD",
                "--",
                *(path.relative_to(_REPOSITORY).as_posix() for path in _SOURCE_PATHS),
            ),
            cwd=_REPOSITORY,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return recorded_tree == environment.source_tree and changed.returncode == 0


def _deterministic_identities(frame_count: int) -> tuple[str, str, int, str]:
    trace = _trace(frame_count)
    window = max(1.0, float(trace.timestamps[-1]))
    encoded = encode_pcapng(trace, _METADATA, observation_window_seconds=window)
    with TemporaryDirectory(prefix="trafficlab-scapy-diagnostic-check-") as temporary:
        capture = Path(temporary) / "diagnostic.pcapng"
        capture.write_bytes(encoded.content)
        parsed = read_pcapng(capture, _METADATA)
    if parsed != encoded.trace:
        raise RuntimeError("diagnostic path read does not match encoded Scapy output")
    return (
        _trace_digest(trace),
        hashlib.sha256(encoded.content).hexdigest(),
        len(encoded.content),
        _trace_digest(parsed),
    )


def _environment() -> EnvironmentRecord:
    source_commit, source_tree = _source_identity()
    return EnvironmentRecord(
        implementation_sha256=_implementation_sha256(),
        machine=platform.machine(),
        platform=platform.platform(),
        python=platform.python_version(),
        scapy=cast(Literal["2.7.0"], importlib.metadata.version("scapy")),
        source_commit=source_commit,
        source_tree=source_tree,
        uv_lock_sha256=_current_lock_sha256(),
    )


def build_diagnostic() -> dict[str, object]:
    """Run fresh subprocess samples for both retained production sizes."""
    cases: list[CaseRecord] = []
    for frame_count in _FRAME_COUNTS:
        for _ in range(_WARMUPS):
            _run_child(frame_count)
        samples = tuple(_run_child(frame_count) for _ in range(_REPETITIONS))
        cases.append(CaseRecord.from_samples(frame_count, samples, warmup_runs=_WARMUPS))
    record = DiagnosticRecord(
        schema_version=2,
        codec="scapy-2.7.0",
        production=True,
        command=_COMMAND,
        environment=_environment(),
        cases=tuple(cases),
    )
    return cast(dict[str, object], record.model_dump(mode="python"))


def _load_checked(path: Path) -> dict[str, object]:
    try:
        value = cast(object, json.loads(path.read_bytes()))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not load production Scapy diagnostic: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("production Scapy diagnostic must be one JSON object")
    raw_mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in raw_mapping):
        raise ValueError("production Scapy diagnostic must have string keys")
    return cast(dict[str, object], value)


def check_diagnostic(
    path: Path,
    *,
    expected_frame_counts: tuple[int, ...] = _FRAME_COUNTS,
    expected_repetitions: int = _REPETITIONS,
) -> bool:
    document = _load_checked(path)
    rendered = render_diagnostic(
        document,
        expected_frame_counts=expected_frame_counts,
        expected_repetitions=expected_repetitions,
    )
    record = DiagnosticRecord.model_validate(document)
    static_identity_matches = (
        rendered == path.read_bytes()
        and record.environment.implementation_sha256 == _implementation_sha256()
        and record.environment.uv_lock_sha256 == _current_lock_sha256()
        and record.environment.python == platform.python_version()
        and record.environment.scapy == importlib.metadata.version("scapy")
        and _recorded_source_matches(record.environment)
    )
    if not static_identity_matches:
        return False
    for case in record.cases:
        expected = _deterministic_identities(case.frame_count)
        if any(
            (
                sample.input_trace_sha256,
                sample.output_sha256,
                sample.output_size_bytes,
                sample.trace_digest,
            )
            != expected
            for sample in case.samples
        ):
            return False
    return True


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=_OUTPUT)
    parser.add_argument("--child-frame-count", type=int)
    parsed = parser.parse_args(arguments)
    output = cast(Path, parsed.output)
    child_frame_count = cast(int | None, parsed.child_frame_count)
    if child_frame_count is not None:
        print(
            _canonical_json_line(cast(dict[str, object], _sample(child_frame_count).model_dump(mode="json"))).decode(),
            end="",
        )
        return 0
    if cast(bool, parsed.check):
        matched = check_diagnostic(output)
        print(f"production-scapy-diagnostic: {'verified' if matched else 'stale'} {output}")
        return int(not matched)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(render_diagnostic(build_diagnostic()))
    print(f"production-scapy-diagnostic: wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
