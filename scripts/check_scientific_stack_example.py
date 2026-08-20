#!/usr/bin/env python3
"""Record or strictly verify the retained real scientific-stack example run."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab.comparison import parse_comparison_result
from trafficlab.config_io import load_experiment
from trafficlab.genetic.checkpoint import CheckpointArtifact
from trafficlab.models.registry import load_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.trace import parse_capture_metadata

REPOSITORY = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = REPOSITORY / "examples" / "scientific_stack" / "example_run.json"
ARTIFACT_DIRECTORY = REPOSITORY / "examples" / "scientific_stack" / "example_run_artifacts"
CONFIG_PATH = REPOSITORY / "examples" / "scientific_stack" / "experiment.toml"
_ARTIFACT_NAMES = (
    "best_model.json",
    "capture.json",
    "checkpoint.json",
    "experiment.toml",
    "ga_history.csv",
    "generated.pcapng",
    "reference.pcapng",
    "run.log",
    "similarity.json",
)
_METHOD_NAMES = ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
_FAMILIES = ("poisson_empirical", "markov_renewal", "mmpp")
_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/5/5b/"
    "SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg"
)
_COMMAND = (
    "scripts/run_bounded.sh",
    "--memory-high",
    "2G",
    "--memory-max",
    "3G",
    "--swap-max",
    "512M",
    "--wall-time",
    "10m",
    "--kill-after",
    "10s",
    "--",
    "uv",
    "run",
    "--locked",
    "trafficlab",
    "run",
    "examples/scientific_stack/experiment.toml",
)


def _tuple_input(value: object) -> object:
    return tuple(cast(list[object], value)) if type(value) is list else value


type NonemptyString = Annotated[StrictStr, Field(min_length=1)]
type Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
type GitIdentity = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
type PositiveInt = Annotated[StrictInt, Field(gt=0)]
type NonnegativeFloat = Annotated[StrictFloat, Field(ge=0.0)]
type UnitFloat = Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
type Family = Literal["poisson_empirical", "markov_renewal", "mmpp"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class Identity(_StrictModel):
    sha256: Sha256
    size: Annotated[StrictInt, Field(ge=0)]


class SourceRecord(_StrictModel):
    commit: GitIdentity
    tree: GitIdentity
    source_clean: Literal[False]
    state_note: Literal[
        "production, config, and lock bytes matched the recorded commit; documentation and evidence changes were uncommitted"
    ]
    config_identity: Identity
    uv_lock_identity: Identity


class ResourceBounds(_StrictModel):
    memory_high: Literal["2G"]
    memory_max: Literal["3G"]
    swap_max: Literal["512M"]
    wall_time: Literal["10m"]
    kill_after: Literal["10s"]


class ExecutionRecord(_StrictModel):
    command: Annotated[tuple[NonemptyString, ...], BeforeValidator(_tuple_input)]
    target_argv: Annotated[tuple[NonemptyString, ...], BeforeValidator(_tuple_input)]
    exit_status: Literal[0]
    observed_completed_utc: NonemptyString
    timestamp_source: Literal["publisher run.log filesystem mtime"]
    recorded_utc: NonemptyString
    resource_bounds: ResourceBounds
    url: NonemptyString

    @model_validator(mode="after")
    def timestamps_are_utc(self) -> Self:
        if self.url != _URL:
            raise ValueError("example-run URL must match the executed endpoint")
        for value in (self.observed_completed_utc, self.recorded_utc):
            try:
                parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
            except ValueError as error:
                raise ValueError("example-run timestamps must be ISO-8601 UTC") from error
            if not value.endswith("Z") or parsed.tzinfo is None:
                raise ValueError("example-run timestamps must be ISO-8601 UTC")
        return self


class EnvironmentRecord(_StrictModel):
    python_implementation: Literal["CPython"]
    python_version: Literal["3.12.3"]
    trafficlab_version: NonemptyString
    numpy_version: NonemptyString
    scipy_version: NonemptyString
    pydantic_version: NonemptyString
    docker_engine_version: NonemptyString
    docker_compose_version: NonemptyString
    kernel_release: NonemptyString
    host_architecture: NonemptyString


class ImageRecord(_StrictModel):
    capture_reference: Literal["trafficlab-capture:local"]
    capture_image_id: Literal["sha256:d2976a55253100d3cf2382ac3a8dc9862d4457ad1397481b8e75c254ad4a858c"]
    capture_tool_version: Literal["4.0.17"]
    target_reference: Literal["curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"]
    target_image_id: Literal["sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"]


class MethodScores(_StrictModel):
    autocorrelation: UnitFloat
    frame_size_ks: UnitFloat
    iat_ks: UnitFloat
    multiscale_rate: UnitFloat


class ResultRecord(_StrictModel):
    enabled_families: Annotated[tuple[Family, ...], BeforeValidator(_tuple_input)]
    winner_family: Family
    selection_fitness: UnitFloat
    reference_packet_count: PositiveInt
    generated_packet_count: PositiveInt
    observation_window_seconds: NonnegativeFloat
    aggregate_score: UnitFloat
    method_scores: MethodScores


class CleanupRecord(_StrictModel):
    project_name: Annotated[StrictStr, Field(pattern=r"^trafficlab-capture-[0-9a-f]{32}$")]
    containers: Annotated[tuple[StrictStr, ...], BeforeValidator(_tuple_input), Field(max_length=0)]
    networks: Annotated[tuple[StrictStr, ...], BeforeValidator(_tuple_input), Field(max_length=0)]
    volumes: Annotated[tuple[StrictStr, ...], BeforeValidator(_tuple_input), Field(max_length=0)]
    verified: Literal[True]


class ExampleRunEvidence(_StrictModel):
    schema_version: Literal[1]
    source: SourceRecord
    execution: ExecutionRecord
    environment: EnvironmentRecord
    images: ImageRecord
    artifacts_directory: Literal["examples/scientific_stack/example_run_artifacts"]
    artifacts: dict[str, Identity]
    result: ResultRecord
    cleanup: CleanupRecord


def canonical_json_bytes(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _identity(path: Path) -> dict[str, int | str]:
    content = path.read_bytes()
    return {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(("git", *arguments), cwd=repository, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ValueError(f"Git command failed: {' '.join(arguments)}")
    return completed.stdout.strip()


def _command_text(repository: Path, command: tuple[str, ...]) -> str:
    completed = subprocess.run(command, cwd=repository, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ValueError(f"command failed: {' '.join(command)}")
    return completed.stdout.strip()


def _timestamp_from_mtime(path: Path) -> str:
    seconds, nanoseconds = divmod(path.stat().st_mtime_ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanoseconds:09d}Z"


def _run_log(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        value = cast(object, json.loads(raw_line))
        if not isinstance(value, dict):
            raise ValueError("example run log must contain JSON objects")
        raw_mapping = cast(dict[object, object], value)
        if not all(isinstance(key, str) for key in raw_mapping):
            raise ValueError("example run log must contain JSON objects")
        record = cast(dict[str, object], value)
        if raw_line.encode("utf-8") + b"\n" != canonical_json_bytes(record):
            raise ValueError("example run log is not canonical JSONL")
        records.append(record)
    if not records:
        raise ValueError("example run log must be nonempty")
    return records


def _one_event(records: Sequence[Mapping[str, object]], event: str) -> Mapping[str, object]:
    matches = [record for record in records if record.get("event") == event]
    if len(matches) != 1:
        raise ValueError(f"example run log must contain one {event} record")
    return matches[0]


def _derived_result(artifact_directory: Path) -> tuple[dict[str, object], str]:
    capture_content = (artifact_directory / "capture.json").read_bytes()
    metadata = parse_capture_metadata(capture_content, source=artifact_directory / "capture.json")
    reference = parse_pcapng_bytes(
        (artifact_directory / "reference.pcapng").read_bytes(),
        metadata,
        source=artifact_directory / "reference.pcapng",
    )
    generated = parse_pcapng_bytes(
        (artifact_directory / "generated.pcapng").read_bytes(),
        metadata,
        source=artifact_directory / "generated.pcapng",
    )
    best = load_best_model(
        (artifact_directory / "best_model.json").read_bytes(), source=artifact_directory / "best_model.json"
    )
    comparison = parse_comparison_result((artifact_directory / "similarity.json").read_bytes())
    checkpoint_document = cast(dict[str, object], json.loads((artifact_directory / "checkpoint.json").read_bytes()))
    CheckpointArtifact.model_validate(checkpoint_document)
    config = load_experiment(artifact_directory / "experiment.toml")
    records = _run_log(artifact_directory / "run.log")
    final_validation = _one_event(records, "final_validation_succeeded")
    completed = _one_event(records, "run_completed")
    created = _one_event(records, "capture_project_created")
    families = {cast(str, item["family"]) for item in cast(list[dict[str, object]], checkpoint_document["population"])}
    if families != set(_FAMILIES):
        raise ValueError("example checkpoint does not contain all three families")
    if cast(str, final_validation["family"]) != best.family or completed["family"] != best.family:
        raise ValueError("example winner family does not match retained artifacts")
    result: dict[str, object] = {
        "aggregate_score": comparison.aggregate_score,
        "enabled_families": list(config.models.enabled),
        "generated_packet_count": len(generated),
        "method_scores": {name: comparison.methods[name].score for name in _METHOD_NAMES},
        "observation_window_seconds": comparison.observation_window_seconds,
        "reference_packet_count": len(reference),
        "selection_fitness": cast(float, final_validation["fitness"]),
        "winner_family": best.family,
    }
    expected_completed = {
        "aggregate_score": result["aggregate_score"],
        "family": result["winner_family"],
        "fitness": result["selection_fitness"],
        "generated_packet_count": result["generated_packet_count"],
        "reference_packet_count": result["reference_packet_count"],
    }
    if any(completed.get(name) != value for name, value in expected_completed.items()):
        raise ValueError("example run-completed record does not match retained artifacts")
    return result, cast(str, created["project_name"])


def _cleanup_inventory(repository: Path, project_name: str) -> dict[str, list[str]]:
    commands = {
        "containers": (
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
        ),
        "networks": (
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
        ),
        "volumes": (
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
        ),
    }
    return {
        name: [line for line in _command_text(repository, command).splitlines() if line]
        for name, command in commands.items()
    }


def build_evidence(repository: Path, artifact_directory: Path, *, source_commit: str) -> dict[str, Any]:
    source_tree = _git(repository, "rev-parse", f"{source_commit}^{{tree}}")
    source_config = subprocess.run(
        ("git", "show", f"{source_commit}:examples/scientific_stack/experiment.toml"),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    source_lock = subprocess.run(
        ("git", "show", f"{source_commit}:uv.lock"), cwd=repository, check=True, capture_output=True
    ).stdout
    result, project_name = _derived_result(artifact_directory)
    cleanup = _cleanup_inventory(repository, project_name)
    if any(cleanup.values()):
        raise ValueError("example run cleanup label inventory is not empty")
    config = load_experiment(CONFIG_PATH)
    capture_environment = _one_event(_run_log(artifact_directory / "run.log"), "capture_environment_identity")
    evidence: dict[str, Any] = {
        "artifacts": {name: _identity(artifact_directory / name) for name in _ARTIFACT_NAMES},
        "artifacts_directory": "examples/scientific_stack/example_run_artifacts",
        "cleanup": {**cleanup, "project_name": project_name, "verified": True},
        "environment": {
            "docker_compose_version": _command_text(repository, ("docker", "compose", "version", "--short")),
            "docker_engine_version": _command_text(
                repository, ("docker", "version", "--format", "{{.Server.Version}}")
            ),
            "host_architecture": platform.machine(),
            "kernel_release": platform.release(),
            "numpy_version": importlib.metadata.version("numpy"),
            "pydantic_version": importlib.metadata.version("pydantic"),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "scipy_version": importlib.metadata.version("scipy"),
            "trafficlab_version": importlib.metadata.version("trafficlab"),
        },
        "execution": {
            "command": list(_COMMAND),
            "exit_status": 0,
            "observed_completed_utc": _timestamp_from_mtime(artifact_directory / "run.log"),
            "recorded_utc": datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "resource_bounds": {
                "kill_after": "10s",
                "memory_high": "2G",
                "memory_max": "3G",
                "swap_max": "512M",
                "wall_time": "10m",
            },
            "timestamp_source": "publisher run.log filesystem mtime",
            "target_argv": list(config.target.argv),
            "url": _URL,
        },
        "images": {
            "capture_image_id": capture_environment["capture_content_id"],
            "capture_reference": capture_environment["capture_reference"],
            "capture_tool_version": capture_environment["capture_tool_version"],
            "target_image_id": capture_environment["target_content_id"],
            "target_reference": capture_environment["target_reference"],
        },
        "result": result,
        "schema_version": 1,
        "source": {
            "commit": source_commit,
            "config_identity": {
                "sha256": hashlib.sha256(source_config).hexdigest(),
                "size": len(source_config),
            },
            "source_clean": False,
            "state_note": (
                "production, config, and lock bytes matched the recorded commit; "
                "documentation and evidence changes were uncommitted"
            ),
            "tree": source_tree,
            "uv_lock_identity": {"sha256": hashlib.sha256(source_lock).hexdigest(), "size": len(source_lock)},
        },
    }
    if config.target.argv[-1] != _URL or config.capture.network_probe_url != _URL:
        raise ValueError("checked example configuration URL does not match the executed endpoint")
    validate_evidence(evidence, repository_root=repository)
    return evidence


def validate_evidence(evidence: Mapping[str, object], *, repository_root: Path = REPOSITORY) -> None:
    try:
        model = ExampleRunEvidence.model_validate(evidence)
    except ValidationError as error:
        raise ValueError(f"invalid example-run evidence: {error}") from error
    document = model.model_dump(mode="json")
    if document != evidence:
        raise ValueError("example-run evidence is not exact strict data")
    if tuple(model.execution.command) != _COMMAND:
        raise ValueError("example-run command does not match the bounded production invocation")
    root = repository_root.resolve()
    if _git(root, "rev-parse", f"{model.source.commit}^{{tree}}") != model.source.tree:
        raise ValueError("example-run source commit does not resolve to its recorded tree")
    source_config = subprocess.run(
        ("git", "show", f"{model.source.commit}:examples/scientific_stack/experiment.toml"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    source_lock = subprocess.run(
        ("git", "show", f"{model.source.commit}:uv.lock"), cwd=root, check=True, capture_output=True
    ).stdout
    if (
        model.source.config_identity.model_dump(mode="json")
        != {
            "sha256": hashlib.sha256(source_config).hexdigest(),
            "size": len(source_config),
        }
        or source_config != CONFIG_PATH.read_bytes()
    ):
        raise ValueError("example-run config identity does not match source and checked bytes")
    if (
        model.source.uv_lock_identity.model_dump(mode="json")
        != {
            "sha256": hashlib.sha256(source_lock).hexdigest(),
            "size": len(source_lock),
        }
        or source_lock != (root / "uv.lock").read_bytes()
    ):
        raise ValueError("example-run lock identity does not match source and checked bytes")
    if set(model.artifacts) != set(_ARTIFACT_NAMES):
        raise ValueError("example-run artifact inventory is incomplete")
    artifact_directory = root / model.artifacts_directory
    for name in _ARTIFACT_NAMES:
        if model.artifacts[name].model_dump(mode="json") != _identity(artifact_directory / name):
            raise ValueError(f"example-run artifact identity does not match retained bytes: {name}")
    result, project_name = _derived_result(artifact_directory)
    if model.result.model_dump(mode="json") != result:
        raise ValueError("example-run result does not match retained artifacts")
    if model.cleanup.model_dump(mode="json") != {
        "containers": [],
        "networks": [],
        "project_name": project_name,
        "verified": True,
        "volumes": [],
    }:
        raise ValueError("example-run cleanup inventory is not the retained empty label result")
    config = load_experiment(CONFIG_PATH)
    if (
        tuple(config.models.enabled) != _FAMILIES
        or tuple(model.execution.target_argv) != config.target.argv
        or config.target.argv[-1] != _URL
    ):
        raise ValueError("example-run checked configuration does not match retained policy")


def parse_and_validate_evidence(content: bytes, *, repository_root: Path = REPOSITORY) -> dict[str, Any]:
    try:
        evidence = cast(dict[str, Any], json.loads(content))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("example-run evidence is not valid JSON") from error
    validate_evidence(evidence, repository_root=repository_root)
    if content != canonical_json_bytes(evidence):
        raise ValueError("example-run evidence is not canonical JSON")
    return evidence


def _record(repository: Path, run_directory: Path, source_commit: str) -> None:
    if ARTIFACT_DIRECTORY.exists() or EVIDENCE_PATH.exists():
        raise ValueError("refusing to replace existing checked example-run evidence")
    missing = [name for name in _ARTIFACT_NAMES if not (run_directory / name).is_file()]
    if missing:
        raise ValueError("example run is missing artifacts: " + ", ".join(missing))
    ARTIFACT_DIRECTORY.mkdir(parents=True)
    for name in _ARTIFACT_NAMES:
        shutil.copy2(run_directory / name, ARTIFACT_DIRECTORY / name)
    evidence = build_evidence(repository, ARTIFACT_DIRECTORY, source_commit=source_commit)
    EVIDENCE_PATH.write_bytes(canonical_json_bytes(evidence))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--record-run", type=Path)
    parser.add_argument("--source-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if cast(bool, arguments.check):
            if arguments.source_commit is not None:
                raise ValueError("--source-commit is valid only with --record-run")
            parse_and_validate_evidence(EVIDENCE_PATH.read_bytes(), repository_root=REPOSITORY)
            print(f"scientific-stack-example: verified {EVIDENCE_PATH}")
            return 0
        source_commit = cast(str | None, arguments.source_commit)
        if source_commit is None:
            raise ValueError("--record-run requires --source-commit")
        run_directory = cast(Path, arguments.record_run)
        if not run_directory.is_absolute():
            run_directory = REPOSITORY / run_directory
        _record(REPOSITORY, run_directory, source_commit)
        print(f"scientific-stack-example: recorded {EVIDENCE_PATH}")
        return 0
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"scientific-stack-example: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
