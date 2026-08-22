"""Candidate artifact, configuration, and training-record operations."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from scripts.validation_study.common import (
    FAMILY_ORDER,
    JsonObject,
    JsonValue,
    candidate_identity,
    canonical_json,
    path_entry_exists,
    repository_relative_path,
    require,
    write_candidate_bytes,
)
from scripts.validation_study.evidence import parse_run_log, read_exact_artifact_set
from scripts.validation_study.prerequisites.codec import (
    RETAINED_CAPABILITY_HEADER,
    parse_retained_prerequisites,
    retained_prerequisite_paths,
)
from scripts.validation_study.rotation.schema import collection_attempt_root
from trafficlab.capture.stage import CaptureResult
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    TrafficTrace,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.comparison.codec import parse_comparison_result
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState, parse_checkpoint
from trafficlab.fitting.genetic.strategy import make_strategy_context

if TYPE_CHECKING:
    from scripts.validation_study.common import WorkloadName
    from scripts.validation_study.workloads import WorkloadSpec


@dataclass(frozen=True, slots=True)
class CandidateTraining:
    workload: WorkloadName
    repeat: int
    directory: Path
    config: ExperimentConfig
    contents: Mapping[str, bytes]
    metadata: CaptureMetadata
    reference: TrafficTrace
    observation_window_seconds: float
    runtime_seconds: float
    checkpoint: CheckpointState
    comparison: ComparisonResult


def _candidate_root(repository_root: Path, study_id: str) -> Path:
    return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / study_id


def candidate_capture_lineage(capture: bytes, environment: Mapping[str, object]) -> JsonObject:
    return cast(
        JsonObject,
        {
            "capture_identity": candidate_identity(capture),
            "capture_image_id": cast(JsonValue, environment["capture_image_id"]),
            "capture_image_reference": cast(JsonValue, environment["capture_image_reference"]),
            "capture_tool_version": cast(JsonValue, environment["capture_tool_version"]),
            "target_image_id": cast(JsonValue, environment["target_image_id"]),
            "target_image_reference": cast(JsonValue, environment["target_image_reference"]),
        },
    )


def _candidate_portable_config(config: ExperimentConfig, destination: Path) -> ExperimentConfig:
    require(config.run.directory.is_absolute(), "collection realized run directory must be absolute")
    require(
        len(config.target.mounts) == 1 and config.target.mounts[0].source.is_absolute(), "collection must use one mount"
    )
    relative_run = Path(os.path.relpath(config.run.directory, start=destination.parent))
    relative_mount = Path(os.path.relpath(config.target.mounts[0].source, start=destination.parent))
    mount = config.target.mounts[0].model_copy(update={"source": relative_mount})
    target = config.target.model_copy(update={"mounts": (mount,)})
    return config.model_copy(
        update={"run": config.run.model_copy(update={"directory": relative_run}), "target": target}
    )


def write_candidate_config_pair(config: ExperimentConfig, portable_path: Path, realized_path: Path) -> None:
    portable = _candidate_portable_config(config, portable_path)
    write_candidate_bytes(portable_path, render_effective_config(portable))
    require(load_experiment(portable_path) == config, "candidate portable configuration must realize exactly")
    write_candidate_bytes(realized_path, render_effective_config(config))
    require(load_experiment(realized_path) == config, "candidate realized configuration must reload exactly")


def load_candidate_training(
    directory: Path, *, workload: WorkloadName, repeat: int, config: ExperimentConfig, runtime_seconds: float
) -> CandidateTraining:
    contents = read_exact_artifact_set(directory)
    metadata = parse_capture_metadata(contents["capture.json"], source=directory / "capture.json")
    reference, window = normalize_reference(
        read_pcapng_bytes(contents["reference.pcapng"], metadata, source=directory / "reference.pcapng")
    )
    context = make_strategy_context(
        config,
        reference,
        window,
        directory,
        experiment_identity=identify_bytes(contents["experiment.toml"]),
        reference_identity=identify_bytes(contents["reference.pcapng"]),
        capture_identity=identify_bytes(contents["capture.json"]),
    )
    checkpoint = parse_checkpoint(contents["checkpoint.json"], context.compatibility)
    require(
        {candidate.family for candidate in checkpoint.population} == set(FAMILY_ORDER),
        "each training run must retain all three enabled model families",
    )
    return CandidateTraining(
        workload=workload,
        repeat=repeat,
        directory=directory,
        config=config,
        contents=contents,
        metadata=metadata,
        reference=reference,
        observation_window_seconds=window,
        runtime_seconds=runtime_seconds,
        checkpoint=checkpoint,
        comparison=parse_comparison_result(contents["similarity.json"]),
    )


def candidate_training_record(training: CandidateTraining, *, environment: Mapping[str, object]) -> JsonObject:
    relative = f"training/{training.workload}/r{training.repeat}"
    portable = f"configs/training-{training.workload}-r{training.repeat}.portable.toml"
    realized = f"configs/training-{training.workload}-r{training.repeat}.realized.toml"
    root = training.directory.parents[2]
    return cast(
        JsonObject,
        {
            "capture_lineage": candidate_capture_lineage(training.contents["capture.json"], environment),
            "directory": relative,
            "portable_config": portable,
            "portable_config_identity": candidate_identity((root / portable).read_bytes()),
            "realized_config": realized,
            "realized_config_identity": candidate_identity((root / realized).read_bytes()),
            "reference_identity": candidate_identity(training.contents["reference.pcapng"]),
            "repeat": training.repeat,
            "run_config_identity": candidate_identity(training.contents["experiment.toml"]),
            "workload": training.workload,
        },
    )


def candidate_fresh_record(training: CandidateTraining) -> tuple[str, JsonObject]:
    path = f"fresh_simulation/{training.workload}/r{training.repeat}.json"
    return (
        path,
        cast(
            JsonObject,
            {
                "comparison_identity": candidate_identity(training.contents["similarity.json"]),
                "generated_identity": candidate_identity(training.contents["generated.pcapng"]),
                "path": path,
                "reference_identity": candidate_identity(training.contents["reference.pcapng"]),
                "seed": training.config.run.final_seed,
                "training_directory": f"training/{training.workload}/r{training.repeat}",
                "training_model_identity": candidate_identity(training.contents["best_model.json"]),
                "workload": training.workload,
                "repeat": training.repeat,
            },
        ),
    )


def select_candidate_training(training: Sequence[CandidateTraining]) -> tuple[JsonObject, ...]:
    selected: list[JsonObject] = []
    for workload in ("short", "streaming", "bursty"):
        candidates = [item for item in training if item.workload == workload]
        require(len(candidates) == 3, f"training selection requires three {workload} repetitions")
        winner = min(candidates, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        selected.append(
            cast(
                JsonObject,
                {
                    "best_model_identity": candidate_identity(winner.contents["best_model.json"]),
                    "repeat": winner.repeat,
                    "training_directory": f"training/{workload}/r{winner.repeat}",
                    "workload": workload,
                },
            )
        )
    return tuple(selected)


def begin_candidate_collection(
    repository_root: Path,
    *,
    attempt: Path,
    study_id: str,
    url: str,
    environment: Mapping[str, object],
    retained_prerequisites: bytes,
    configs: Mapping[WorkloadName, ExperimentConfig],
    object_size_bytes: int,
) -> tuple[Path, Path]:
    candidate = _candidate_root(repository_root, study_id)
    require(
        attempt == collection_attempt_root(repository_root, study_id),
        "collection attempt must use its exact study attempt path",
    )
    require(
        (attempt / "collection.json").is_file(), "collection attempt marker must exist before collection validation"
    )
    require(set(configs) == {"short", "streaming", "bursty"}, "collection requires exactly three workload configs")
    require(4 * 1024 * 1024 <= object_size_bytes <= 16 * 1024 * 1024, "collection object size is out of range")
    document = parse_retained_prerequisites(retained_prerequisites)
    require(
        document["study_id"] == study_id and document["url"] == url,
        "retained prerequisite study ID and URL must equal the collection request",
    )
    marker = attempt / "frozen-protocol.json"
    if path_entry_exists(marker) or path_entry_exists(candidate):
        raise TrafficlabError(
            f"Validation Study collection already began for {study_id}; use a new study ID",
            corrective_action="preserve the failed attempt and restart with a new study ID",
        )
    controls = {
        "base_config_identities": {
            workload: identify_bytes(render_effective_config(configs[workload])).as_dict()
            for workload in ("short", "streaming", "bursty")
        },
        "environment_identity": identify_bytes(canonical_json(cast(JsonObject, dict(environment)))).as_dict(),
        "prerequisites_identity": identify_bytes(retained_prerequisites).as_dict(),
        "study_id": study_id,
        "url": url,
    }
    write_candidate_bytes(marker, canonical_json(cast(JsonObject, controls)))
    candidate.mkdir(parents=True, exist_ok=False)
    return (candidate, attempt)


def stage_retained_prerequisites(candidate: Path, *, content: bytes, files: Mapping[str, bytes]) -> None:
    document = parse_retained_prerequisites(content)
    expected_paths = retained_prerequisite_paths(document)
    require(
        set(files) == {*expected_paths, RETAINED_CAPABILITY_HEADER},
        "retained prerequisite files must exactly match the frozen document and capability header",
    )
    for command in cast(list[JsonObject], document["commands"]):
        for field in ("command", "junit", "status", "stderr", "stdout"):
            record = cast(JsonObject, command[field])
            relative = cast(str, record["path"])
            identity = ContentIdentity.from_dict(record["identity"], name=f"retained prerequisite {relative}")
            require(identify_bytes(files[relative]) == identity, f"retained prerequisite {relative} has wrong bytes")
            write_candidate_bytes(candidate / relative, files[relative])
    capability = cast(JsonObject, document["capability"])
    capability_header = files[RETAINED_CAPABILITY_HEADER]
    require(
        hashlib.sha256(capability_header).hexdigest() == capability["canary_sha256"],
        "retained capability header must match its frozen prerequisite identity",
    )
    write_candidate_bytes(candidate / RETAINED_CAPABILITY_HEADER, capability_header)
    write_candidate_bytes(
        candidate / "observations/prerequisites/00-prerequisites/capability.headers.json",
        canonical_json(
            cast(
                JsonObject,
                {
                    "content_length": capability["content_length"],
                    "content_range": capability["content_range"],
                    "header_identity": candidate_identity(capability_header),
                    "requested_end": 0,
                    "requested_start": 0,
                    "run_id": "00-prerequisites",
                    "scope": "prerequisites",
                    "status": capability["status"],
                    "transfer_index": 0,
                    "workload": "prerequisites",
                },
            )
        ),
    )
    write_candidate_bytes(candidate / "prerequisites.json", content)


def stage_candidate_transfer_evidence(
    repository_root: Path,
    candidate: Path,
    responses: Sequence[JsonObject],
    *,
    scope: Literal["training", "held_out"],
    run_id: str,
    workload: WorkloadSpec,
) -> None:
    """Copy every protocol-used response into the immutable candidate by run and transfer."""
    require(len(responses) == len(workload.transfers), f"{scope} {run_id} must retain every workload transfer response")
    for transfer_index, (start, end, filename) in enumerate(workload.transfers):
        response = responses[transfer_index]
        require(
            response["transfer_index"] == transfer_index
            and response["requested_start"] == start
            and (response["requested_end"] == end)
            and (response["status"] == 206),
            f"{scope} {run_id} transfer {filename} does not match the frozen workload profile",
        )
        archive_relative = repository_relative_path(
            response["header_archive_path"], repository_root=repository_root, name=f"{scope} {run_id} header archive"
        )
        archive = repository_root / Path(*archive_relative.split("/"))
        try:
            header = archive.read_bytes()
        except OSError as error:
            raise ValueError(f"could not read retained {scope} {run_id} header {filename}: {error}") from error
        require(hashlib.sha256(header).hexdigest() == response["header_sha256"], "archived transfer header changed")
        header_relative = f"headers/{scope}/{run_id}/{filename}"
        observation_relative = f"observations/{scope}/{run_id}/{filename}.json"
        write_candidate_bytes(candidate / header_relative, header)
        write_candidate_bytes(
            candidate / observation_relative,
            canonical_json(
                cast(
                    JsonObject,
                    {
                        "content_length": response["content_length"],
                        "content_range": response["content_range"],
                        "header_identity": candidate_identity(header),
                        "requested_end": end,
                        "requested_start": start,
                        "run_id": run_id,
                        "scope": scope,
                        "status": 206,
                        "transfer_index": transfer_index,
                        "workload": workload.name,
                    },
                )
            ),
        )


def collection_capture_lifecycle_record(
    capture_result: CaptureResult, *, directory: Path, directory_relative: str, run_id: str
) -> JsonObject:
    """Bind one successful capture return to its retained project-cleanup lineage."""
    require(
        capture_result.run_directory == directory
        and capture_result.reference_path == directory / "reference.pcapng"
        and (capture_result.target_status == 0)
        and (not capture_result.reused),
        "collection capture must return one fresh successful capture pair",
    )
    records = parse_run_log((directory / "run.log").read_bytes())
    creations = [record for record in records if record.get("event") == "capture_project_created"]
    require(len(creations) == 1, "collection capture must retain one capture project creation record")
    publications = [record for record in records if record.get("event") == "capture_published"]
    require(len(publications) == 1, "collection capture must retain one capture publication record")
    created_project_name = creations[0].get("project_name")
    project_name = publications[0].get("project_name")
    require(
        creations[0].get("stage") == "capture"
        and publications[0].get("stage") == "capture"
        and (type(created_project_name) is str)
        and (type(project_name) is str)
        and created_project_name.startswith("trafficlab-capture-")
        and (created_project_name == project_name)
        and (records.index(creations[0]) < records.index(publications[0])),
        "collection capture must bind its exact created project name to publication",
    )
    return cast(
        JsonObject,
        {"cleanup_verified": True, "directory": directory_relative, "project_name": project_name, "run_id": run_id},
    )
