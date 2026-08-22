"""Accepted validation-study identity and protocol schemas."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)


def tuple_input(value: object) -> object:
    return tuple(cast(list[object], value)) if type(value) is list else value


def _exact_float_input(value: object) -> object:
    if type(value) is not float:
        raise ValueError("value must be an exact float")
    return value


def _relative_path(value: str) -> str:
    path = Path(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or value != path.as_posix()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ValueError("value must be a normalized relative POSIX path")
    return value


type ExactFloat = Annotated[StrictFloat, BeforeValidator(_exact_float_input)]
type NonnegativeFloat = Annotated[ExactFloat, Field(ge=0.0)]
type PositiveFloat = Annotated[ExactFloat, Field(gt=0.0)]
type UnitFloat = Annotated[ExactFloat, Field(ge=0.0, le=1.0)]
type NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
type PositiveInt = Annotated[StrictInt, Field(gt=0)]
type NonemptyString = Annotated[StrictStr, Field(min_length=1, pattern=r"\S")]
type Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
type GitIdentity = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
type ImageIdentity = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
type RelativePath = Annotated[StrictStr, AfterValidator(_relative_path)]
type Workload = Literal["short", "streaming", "bursty"]
type Repeat = Annotated[StrictInt, Field(ge=1, le=3)]
type ExactNumber = StrictInt | ExactFloat


class StrictStudyModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


def validate_study_model[ModelT: BaseModel](
    model: type[ModelT],
    value: object,
    *,
    name: str,
) -> ModelT:
    """Validate primitives with stable diagnostics that omit persisted input values."""

    try:
        return model.model_validate(value)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        location = ".".join(str(part) for part in first["loc"]) or "root"
        raise ValueError(f"{name} has invalid {location}: {first['msg']} [{first['type']}]") from error


class StudyContentIdentity(StrictStudyModel):
    sha256: Sha256
    size: NonnegativeInt


class StudyCompatibilityDecision(StrictStudyModel):
    reason: NonemptyString
    status: Literal["compatible"]


class ValidationStudyEnvironment(StrictStudyModel):
    """Checked source, runtime, and image identity record for one accepted study."""

    capture_image_id: ImageIdentity
    capture_image_reference: NonemptyString
    capture_tool_version: NonemptyString
    compatibility_decision: StudyCompatibilityDecision
    docker_compose_version: NonemptyString
    docker_engine_version: NonemptyString
    host_architecture: NonemptyString
    kernel_release: NonemptyString
    python_implementation: Literal["CPython"]
    python_version: Literal["3.12.3"]
    scientific_artifact_schema: Literal[4]
    source_commit: GitIdentity
    source_tree: GitIdentity
    target_image_id: ImageIdentity
    target_image_reference: NonemptyString
    uv_lock_identity: StudyContentIdentity


class StudyPrerequisiteEnvironment(StrictStudyModel):
    capture_image_id: ImageIdentity
    capture_image_reference: NonemptyString
    capture_tool_version: NonemptyString
    source_commit: GitIdentity
    source_tree: GitIdentity
    target_image_id: ImageIdentity
    target_image_reference: NonemptyString
    uv_lock_identity: StudyContentIdentity


class StudyCapability(StrictStudyModel):
    canary_sha256: Sha256
    content_length: PositiveInt
    content_range: NonemptyString
    object_size_bytes: PositiveInt
    status: PositiveInt


class StudyRetainedOutput(StrictStudyModel):
    identity: StudyContentIdentity
    path: RelativePath


class StudyTestCounts(StrictStudyModel):
    errors: NonnegativeInt
    failed: NonnegativeInt
    passed: PositiveInt
    skipped: NonnegativeInt
    total: PositiveInt

    @model_validator(mode="after")
    def successful_counts_are_consistent(self) -> Self:
        if self.failed != 0 or self.errors != 0 or self.skipped != 0 or self.passed != self.total:
            raise ValueError("prerequisite test counts must describe a non-skipped successful selection")
        return self


class StudyPrerequisiteCommand(StrictStudyModel):
    argv: Annotated[tuple[NonemptyString, ...], Field(min_length=1), BeforeValidator(tuple_input)]
    command: StudyRetainedOutput
    exit_status: Annotated[StrictInt, Field(ge=0, le=0)]
    junit: StudyRetainedOutput
    kind: Literal["docker_matrix", "internet_smoke"]
    status: StudyRetainedOutput
    stderr: StudyRetainedOutput
    stdout: StudyRetainedOutput
    tests: StudyTestCounts


class ValidationStudyPrerequisite(StrictStudyModel):
    """Retained successful Docker and Internet prerequisite evidence."""

    capability: StudyCapability
    commands: Annotated[
        tuple[StudyPrerequisiteCommand, ...], Field(min_length=2, max_length=2), BeforeValidator(tuple_input)
    ]
    environment: StudyPrerequisiteEnvironment
    schema_version: Literal[4]
    study_id: NonemptyString
    url: Annotated[StrictStr, Field(pattern=r"^https://")]

    @model_validator(mode="after")
    def command_kinds_are_unique(self) -> Self:
        if tuple(command.kind for command in self.commands) != ("docker_matrix", "internet_smoke"):
            raise ValueError("prerequisite commands must be ordered Docker matrix then Internet smoke")
        return self


class SimpleStudyLineage(StrictStudyModel):
    relation: Literal[
        "study-index",
        "protocol",
        "environment",
        "prerequisites",
        "report_inputs",
        "report",
        "lifecycle",
    ]


class PrerequisiteStudyLineage(StrictStudyModel):
    relation: Literal["prerequisite"]
    record: NonemptyString


class ConfigurationStudyLineage(StrictStudyModel):
    relation: Literal["configuration"]
    name: NonemptyString


class TransferStudyLineage(StrictStudyModel):
    filename: NonemptyString
    relation: Literal["transfer-header", "external-observation"]
    requested_end: NonnegativeInt
    requested_start: NonnegativeInt
    run_id: NonemptyString
    scope: Literal["prerequisites", "training", "held_out"]
    transfer_index: NonnegativeInt
    workload: Workload | Literal["prerequisites"]


class RepeatedStudyLineage(StrictStudyModel):
    relation: Literal[
        "best_model.json",
        "capture.json",
        "checkpoint.json",
        "experiment.toml",
        "fresh_simulation",
        "ga_history.csv",
        "generated.pcapng",
        "reference.pcapng",
        "run.log",
        "similarity.json",
    ]
    repeat: Repeat
    workload: Workload


class HeldOutStudyLineage(StrictStudyModel):
    relation: Literal[
        "capture.json",
        "generated.pcapng",
        "portable.toml",
        "realized.toml",
        "record.json",
        "reference.pcapng",
        "run.log",
        "similarity.json",
    ]
    workload: Workload


type StudyLineage = (
    SimpleStudyLineage
    | PrerequisiteStudyLineage
    | ConfigurationStudyLineage
    | TransferStudyLineage
    | RepeatedStudyLineage
    | HeldOutStudyLineage
)


class StudyManifestEntry(StrictStudyModel):
    lineage: StudyLineage
    owner: NonemptyString
    path: RelativePath
    sha256: Sha256
    size: NonnegativeInt


class ValidationStudyManifest(StrictStudyModel):
    """Canonical inventory root; file bytes and modes remain auditor-owned policy."""

    files: Annotated[tuple[StudyManifestEntry, ...], Field(min_length=1), BeforeValidator(tuple_input)]
    schema_version: Literal[2]

    @model_validator(mode="after")
    def paths_are_unique_sorted_and_exclude_manifest(self) -> Self:
        paths = tuple(entry.path for entry in self.files)
        if "manifest.json" in paths or len(paths) != len(set(paths)):
            raise ValueError("manifest paths must be unique and exclude manifest.json")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("manifest paths must be ordered by UTF-8 bytes")
        return self


class StudyCaptureLineage(StrictStudyModel):
    capture_identity: StudyContentIdentity
    capture_image_id: ImageIdentity
    capture_image_reference: NonemptyString
    capture_tool_version: NonemptyString
    target_image_id: ImageIdentity
    target_image_reference: NonemptyString


class StudyTrainingLineage(StrictStudyModel):
    capture_lineage: StudyCaptureLineage
    directory: RelativePath
    portable_config: RelativePath
    portable_config_identity: StudyContentIdentity
    realized_config: RelativePath
    realized_config_identity: StudyContentIdentity
    reference_identity: StudyContentIdentity
    repeat: Repeat
    run_config_identity: StudyContentIdentity
    workload: Workload


class StudyFreshSimulationLineage(StrictStudyModel):
    comparison_identity: StudyContentIdentity
    generated_identity: StudyContentIdentity
    path: RelativePath
    reference_identity: StudyContentIdentity
    repeat: Repeat
    seed: NonnegativeInt
    training_directory: RelativePath
    training_model_identity: StudyContentIdentity
    workload: Workload


class StudyHeldOutLineage(StrictStudyModel):
    capture_lineage: StudyCaptureLineage
    directory: RelativePath
    training_directory: RelativePath
    workload: Workload


class ValidationStudyLineage(StrictStudyModel):
    """Accepted evidence index and its complete typed lineage maps."""

    environment: RelativePath
    fresh_simulation: Annotated[
        tuple[StudyFreshSimulationLineage, ...], Field(min_length=9, max_length=9), BeforeValidator(tuple_input)
    ]
    held_out: Annotated[
        tuple[StudyHeldOutLineage, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)
    ]
    lifecycle: RelativePath
    lineage: dict[RelativePath, StudyLineage]
    ownership: dict[RelativePath, NonemptyString]
    prerequisites: RelativePath
    protocol: RelativePath
    report: RelativePath
    report_inputs: RelativePath
    schema_version: Literal[4]
    training: Annotated[
        tuple[StudyTrainingLineage, ...], Field(min_length=9, max_length=9), BeforeValidator(tuple_input)
    ]


class StudyLifecycleRow(StrictStudyModel):
    cleanup_verified: StrictBool
    directory: RelativePath
    project_name: NonemptyString
    run_id: NonemptyString


class StudyPhaseImageLifecycle(StrictStudyModel):
    capture_image_id: ImageIdentity
    cleanup_verified: StrictBool
    post_cleanup_inspect_exit_status: StrictInt
    tag: NonemptyString


class ValidationStudyLifecycle(StrictStudyModel):
    """Complete cleanup proof for training, held-out, and phase image resources."""

    held_out: Annotated[tuple[StudyLifecycleRow, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)]
    phase_capture_image: StudyPhaseImageLifecycle
    schema_version: Literal[1]
    study_id: NonemptyString
    training: Annotated[tuple[StudyLifecycleRow, ...], Field(min_length=9, max_length=9), BeforeValidator(tuple_input)]


class StudySelectedModel(StrictStudyModel):
    best_model_identity: StudyContentIdentity
    repeat: Repeat
    training_directory: RelativePath
    workload: Workload


class StudyModelSelection(StrictStudyModel):
    rule: Literal["highest_best_fitness_then_lowest_repeat"]
    selected: Annotated[tuple[StudySelectedModel, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)]


class ValidationStudyProtocol(StrictStudyModel):
    """Frozen study identity, seed, repetition, and model-selection protocol."""

    candidate_id: NonemptyString
    destination_id: NonemptyString
    final_seed: NonnegativeInt
    model_selection: StudyModelSelection
    prerequisite_path: RelativePath
    schema_version: Literal[4]
    selection_seeds: Annotated[tuple[NonnegativeInt, ...], Field(min_length=1), BeforeValidator(tuple_input)]
    study_id: NonemptyString
    training_repetitions: PositiveInt
    workloads: Annotated[tuple[Workload, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)]


# Explicit plural alias matches the persisted filename without another model path.
ValidationStudyPrerequisites = ValidationStudyPrerequisite
