"""Audit-gated, exclusive publication of accepted validation-study evidence."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
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

from trafficlab.common.errors import FailureOutcome, TrafficlabError, attach_failure_outcome
from trafficlab.comparison.stage import (
    AutocorrelationDiagnostic,
    FrameSizeDiagnostic,
    IatDiagnostic,
    MultiscaleDiagnostic,
)

type BundleAudit = Callable[[Path], None]


def _tuple_input(value: object) -> object:
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


class _StrictStudyModel(BaseModel):
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


class StudyContentIdentity(_StrictStudyModel):
    sha256: Sha256
    size: NonnegativeInt


class StudyCompatibilityDecision(_StrictStudyModel):
    reason: NonemptyString
    status: Literal["compatible"]


class ValidationStudyEnvironment(_StrictStudyModel):
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


class StudyPrerequisiteEnvironment(_StrictStudyModel):
    capture_image_id: ImageIdentity
    capture_image_reference: NonemptyString
    capture_tool_version: NonemptyString
    source_commit: GitIdentity
    source_tree: GitIdentity
    target_image_id: ImageIdentity
    target_image_reference: NonemptyString
    uv_lock_identity: StudyContentIdentity


class StudyCapability(_StrictStudyModel):
    canary_sha256: Sha256
    content_length: PositiveInt
    content_range: NonemptyString
    object_size_bytes: PositiveInt
    status: PositiveInt


class StudyRetainedOutput(_StrictStudyModel):
    identity: StudyContentIdentity
    path: RelativePath


class StudyTestCounts(_StrictStudyModel):
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


class StudyPrerequisiteCommand(_StrictStudyModel):
    argv: Annotated[tuple[NonemptyString, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    command: StudyRetainedOutput
    exit_status: Annotated[StrictInt, Field(ge=0, le=0)]
    junit: StudyRetainedOutput
    kind: Literal["docker_matrix", "internet_smoke"]
    status: StudyRetainedOutput
    stderr: StudyRetainedOutput
    stdout: StudyRetainedOutput
    tests: StudyTestCounts


class ValidationStudyPrerequisite(_StrictStudyModel):
    """Retained successful Docker and Internet prerequisite evidence."""

    capability: StudyCapability
    commands: Annotated[
        tuple[StudyPrerequisiteCommand, ...], Field(min_length=2, max_length=2), BeforeValidator(_tuple_input)
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


class SimpleStudyLineage(_StrictStudyModel):
    relation: Literal[
        "study-index",
        "protocol",
        "environment",
        "prerequisites",
        "report_inputs",
        "report",
        "lifecycle",
    ]


class PrerequisiteStudyLineage(_StrictStudyModel):
    relation: Literal["prerequisite"]
    record: NonemptyString


class ConfigurationStudyLineage(_StrictStudyModel):
    relation: Literal["configuration"]
    name: NonemptyString


class TransferStudyLineage(_StrictStudyModel):
    filename: NonemptyString
    relation: Literal["transfer-header", "external-observation"]
    requested_end: NonnegativeInt
    requested_start: NonnegativeInt
    run_id: NonemptyString
    scope: Literal["prerequisites", "training", "held_out"]
    transfer_index: NonnegativeInt
    workload: Workload | Literal["prerequisites"]


class RepeatedStudyLineage(_StrictStudyModel):
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


class HeldOutStudyLineage(_StrictStudyModel):
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


class StudyManifestEntry(_StrictStudyModel):
    lineage: StudyLineage
    owner: NonemptyString
    path: RelativePath
    sha256: Sha256
    size: NonnegativeInt


class ValidationStudyManifest(_StrictStudyModel):
    """Canonical inventory root; file bytes and modes remain auditor-owned policy."""

    files: Annotated[tuple[StudyManifestEntry, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    schema_version: Literal[2]

    @model_validator(mode="after")
    def paths_are_unique_sorted_and_exclude_manifest(self) -> Self:
        paths = tuple(entry.path for entry in self.files)
        if "manifest.json" in paths or len(paths) != len(set(paths)):
            raise ValueError("manifest paths must be unique and exclude manifest.json")
        if paths != tuple(sorted(paths, key=lambda value: value.encode("utf-8"))):
            raise ValueError("manifest paths must be ordered by UTF-8 bytes")
        return self


class StudyCaptureLineage(_StrictStudyModel):
    capture_identity: StudyContentIdentity
    capture_image_id: ImageIdentity
    capture_image_reference: NonemptyString
    capture_tool_version: NonemptyString
    target_image_id: ImageIdentity
    target_image_reference: NonemptyString


class StudyTrainingLineage(_StrictStudyModel):
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


class StudyFreshSimulationLineage(_StrictStudyModel):
    comparison_identity: StudyContentIdentity
    generated_identity: StudyContentIdentity
    path: RelativePath
    reference_identity: StudyContentIdentity
    repeat: Repeat
    seed: NonnegativeInt
    training_directory: RelativePath
    training_model_identity: StudyContentIdentity
    workload: Workload


class StudyHeldOutLineage(_StrictStudyModel):
    capture_lineage: StudyCaptureLineage
    directory: RelativePath
    training_directory: RelativePath
    workload: Workload


class ValidationStudyLineage(_StrictStudyModel):
    """Accepted evidence index and its complete typed lineage maps."""

    environment: RelativePath
    fresh_simulation: Annotated[
        tuple[StudyFreshSimulationLineage, ...], Field(min_length=9, max_length=9), BeforeValidator(_tuple_input)
    ]
    held_out: Annotated[
        tuple[StudyHeldOutLineage, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
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
        tuple[StudyTrainingLineage, ...], Field(min_length=9, max_length=9), BeforeValidator(_tuple_input)
    ]


class StudyLifecycleRow(_StrictStudyModel):
    cleanup_verified: StrictBool
    directory: RelativePath
    project_name: NonemptyString
    run_id: NonemptyString


class StudyPhaseImageLifecycle(_StrictStudyModel):
    capture_image_id: ImageIdentity
    cleanup_verified: StrictBool
    post_cleanup_inspect_exit_status: StrictInt
    tag: NonemptyString


class ValidationStudyLifecycle(_StrictStudyModel):
    """Complete cleanup proof for training, held-out, and phase image resources."""

    held_out: Annotated[tuple[StudyLifecycleRow, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)]
    phase_capture_image: StudyPhaseImageLifecycle
    schema_version: Literal[1]
    study_id: NonemptyString
    training: Annotated[tuple[StudyLifecycleRow, ...], Field(min_length=9, max_length=9), BeforeValidator(_tuple_input)]


class StudySelectedModel(_StrictStudyModel):
    best_model_identity: StudyContentIdentity
    repeat: Repeat
    training_directory: RelativePath
    workload: Workload


class StudyModelSelection(_StrictStudyModel):
    rule: Literal["highest_best_fitness_then_lowest_repeat"]
    selected: Annotated[
        tuple[StudySelectedModel, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
    ]


class ValidationStudyProtocol(_StrictStudyModel):
    """Frozen study identity, seed, repetition, and model-selection protocol."""

    candidate_id: NonemptyString
    destination_id: NonemptyString
    final_seed: NonnegativeInt
    model_selection: StudyModelSelection
    prerequisite_path: RelativePath
    schema_version: Literal[4]
    selection_seeds: Annotated[tuple[NonnegativeInt, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    study_id: NonemptyString
    training_repetitions: PositiveInt
    workloads: Annotated[tuple[Workload, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)]


class StudyMethodValues(_StrictStudyModel):
    autocorrelation: UnitFloat
    frame_size_ks: UnitFloat
    iat_ks: UnitFloat
    multiscale_rate: UnitFloat


class StudyScore(_StrictStudyModel):
    aggregate: UnitFloat
    methods: StudyMethodValues


class StudyDiagnostics(_StrictStudyModel):
    autocorrelation: AutocorrelationDiagnostic
    frame_size_ks: FrameSizeDiagnostic
    iat_ks: IatDiagnostic
    multiscale_rate: MultiscaleDiagnostic


class StudyControlledWeightAnalysis(_StrictStudyModel):
    alternative_aggregate: UnitFloat
    alternative_weights: StudyMethodValues
    baseline_aggregate: UnitFloat
    baseline_weights: StudyMethodValues
    components: StudyMethodValues
    diagnostics: StudyDiagnostics
    executed_methods: Annotated[
        tuple[NonemptyString, ...], Field(min_length=4, max_length=4), BeforeValidator(_tuple_input)
    ]
    training_directory: RelativePath
    workload: Workload


class StudyWorkloadScore(_StrictStudyModel):
    score: StudyScore
    workload: Workload


class StudyHeldOutScore(StudyWorkloadScore):
    observation_window_seconds: PositiveFloat


class StudyCandidateIdentifier(_StrictStudyModel):
    birth_generation: NonnegativeInt
    birth_index: NonnegativeInt


class StudyInvalidCandidate(_StrictStudyModel):
    affected_evidence: NonemptyString
    authority: Literal["primary", "secondary"]
    corrective_action: NonemptyString
    detail: NonemptyString
    evidence_state: Literal["not_published", "diagnostic_only", "preserved", "possibly_remaining"]
    family: Literal["markov_renewal", "mmpp", "poisson_empirical"]
    genes: Annotated[tuple[ExactNumber, ...], BeforeValidator(_tuple_input)] | None
    identifier: StudyCandidateIdentifier
    kind: Literal["repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score"]
    seed: NonnegativeInt | None
    stage: NonemptyString


class StudyTrialLimits(_StrictStudyModel):
    max_output_bytes: PositiveInt
    max_packets: PositiveInt
    max_wall_seconds: PositiveFloat


class StudyInvalidChromosomeDiagnostics(_StrictStudyModel):
    invalid_candidates: Annotated[tuple[StudyInvalidCandidate, ...], BeforeValidator(_tuple_input)]
    repeat: Repeat
    training_directory: RelativePath
    trial_limits: StudyTrialLimits
    workload: Workload


class StudyNaturalVariationPair(_StrictStudyModel):
    forward: StudyScore
    left_repeat: Repeat
    reverse: StudyScore
    right_repeat: Repeat
    symmetric_mean: StudyScore


class StudyNaturalVariation(_StrictStudyModel):
    pairs: Annotated[
        tuple[StudyNaturalVariationPair, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
    ]
    symmetric_mean: StudyScore
    workload: Workload


class StudyPcg64CoreState(_StrictStudyModel):
    state: Annotated[StrictInt, Field(ge=0, le=2**128 - 1)]
    inc: Annotated[StrictInt, Field(ge=0, le=2**128 - 1)]


class StudyRngState(_StrictStudyModel):
    bit_generator: Literal["PCG64"]
    state: StudyPcg64CoreState
    has_uint32: Annotated[StrictInt, Field(ge=0, le=1)]
    uinteger: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)]


class StudyBootstrapInterval(_StrictStudyModel):
    confidence_level: UnitFloat
    generator: Literal["PCG64"]
    generator_state: StudyRngState
    lower_bound: ExactFloat
    method: Literal["percentile"]
    n_resamples: Literal[10_000]
    sample_size: Literal[3]
    seed: Literal[20_260_819]
    statistic: Literal["mean"]
    upper_bound: ExactFloat

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> Self:
        if self.confidence_level != 0.95:
            raise ValueError("bootstrap confidence_level must be 0.95")
        if self.lower_bound > self.upper_bound:
            raise ValueError("bootstrap lower_bound must not exceed upper_bound")
        return self


class StudyDescriptive(_StrictStudyModel):
    bootstrap: StudyBootstrapInterval
    mean: NonnegativeFloat
    sample_variance: NonnegativeFloat


class StudyWinnerCounts(_StrictStudyModel):
    markov_renewal: NonnegativeInt
    mmpp: NonnegativeInt
    poisson_empirical: NonnegativeInt


class StudyTrainingSummary(_StrictStudyModel):
    runtime_seconds: StudyDescriptive
    selection_fitness: StudyDescriptive
    winner_family_count_variance: ExactNumber
    winner_family_counts: StudyWinnerCounts
    workload: Workload


class ValidationStudyReportInput(_StrictStudyModel):
    """Typed report arithmetic inputs; the auditor still independently recomputes every value."""

    controlled_weight_analysis: Annotated[
        tuple[StudyControlledWeightAnalysis, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
    ]
    formula: Literal["arithmetic_mean"]
    fresh_simulation: Annotated[
        tuple[StudyWorkloadScore, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
    ]
    held_out: Annotated[tuple[StudyHeldOutScore, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)]
    invalid_chromosome_diagnostics: Annotated[
        tuple[StudyInvalidChromosomeDiagnostics, ...],
        Field(min_length=9, max_length=9),
        BeforeValidator(_tuple_input),
    ]
    natural_variation: Annotated[
        tuple[StudyNaturalVariation, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
    ]
    runtime_winner_variance: Annotated[
        tuple[StudyTrainingSummary, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
    ]
    training: Annotated[
        tuple[StudyTrainingSummary, ...], Field(min_length=3, max_length=3), BeforeValidator(_tuple_input)
    ]


class ValidationStudyReport(_StrictStudyModel):
    """Published report root bound to the exact report-input bytes."""

    formula: Literal["arithmetic_mean"]
    report_inputs_identity: StudyContentIdentity
    summary: ValidationStudyReportInput


# Explicit plural aliases match the persisted filenames without adding another model path.
ValidationStudyPrerequisites = ValidationStudyPrerequisite
ValidationStudyReportInputs = ValidationStudyReportInput

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_SAFE_STUDY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", flags=re.ASCII)


class AcceptedBundlePublicationError(TrafficlabError):
    """A post-rename durability failure whose accepted destination is preserved."""

    def __init__(self, destination: Path, error: OSError) -> None:
        super().__init__(
            f"accepted evidence destination was preserved after a post-rename durability failure at "
            f"{destination}: {error}",
            corrective_action=(
                "preserve and validate the accepted destination; do not retry publication under the occupied study ID"
            ),
        )
        self.destination = destination
        self.evidence_state: Literal["preserved"] = "preserved"
        attach_failure_outcome(
            self,
            kind="publication_failed",
            stage="publication",
            affected_evidence="accepted evidence bundle",
            evidence_state="preserved",
        )


def _publication_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"could not publish accepted evidence bundle: {detail}",
        corrective_action="preserve the candidate, correct the local filesystem failure, and retry publication",
    )


def _collision(destination: Path) -> TrafficlabError:
    return TrafficlabError(
        f"publication_collision: accepted evidence bundle already exists at {destination}",
        corrective_action="choose a new study ID; accepted evidence bundles are immutable",
        failure_outcome=FailureOutcome(
            kind="publication_collision",
            stage="publication",
            detail="accepted bundle already exists",
            affected_evidence="candidate accepted evidence bundle",
            evidence_state="not_published",
            corrective_action="choose a new study ID",
            authority="primary",
        ),
    )


def _validate_study_id(study_id: object) -> str:
    if type(study_id) is not str or _SAFE_STUDY_ID.fullmatch(study_id) is None:
        raise TrafficlabError(
            "invalid accepted evidence study ID: use one visible ASCII path component",
            corrective_action="use letters, digits, dots, underscores, and hyphens beginning with a letter or digit",
        )
    return study_id


def _validate_candidate(candidate: object) -> Path:
    if not isinstance(candidate, Path):
        raise TypeError("candidate must be a pathlib.Path")
    try:
        candidate_mode = candidate.lstat().st_mode
    except OSError as error:
        raise TrafficlabError(
            f"accepted evidence candidate is not a readable regular directory: {candidate}",
            corrective_action="prepare and audit a local candidate directory before publication",
        ) from error
    if not stat.S_ISDIR(candidate_mode):
        raise TrafficlabError(
            f"accepted evidence candidate must be a regular directory: {candidate}",
            corrective_action="prepare and audit a local candidate directory before publication",
        )
    return candidate


def _fsync_open_path(path: Path, *, directory: bool) -> None:
    flags = os.O_RDONLY
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    for directory, _directory_names, file_names in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for file_name in file_names:
            file_path = directory_path / file_name
            if stat.S_ISREG(file_path.lstat().st_mode):
                _fsync_open_path(file_path, directory=False)
        _fsync_open_path(directory_path, directory=True)


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish one directory without replacing an existing name.

    Trafficlab's supported execution environment is Linux. libc's renameat2
    exposes the kernel's no-replace primitive without adding a dependency.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as error:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable on this supported local filesystem") from error
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


def _cleanup_temporary(temporary: Path) -> str | None:
    try:
        shutil.rmtree(temporary)
    except FileNotFoundError:
        return None
    except OSError as error:
        return str(error)
    except BaseException as error:
        return f"{type(error).__name__}: {error}"
    return None


def _note_cleanup_failure(error: BaseException, cleanup_error: str | None) -> None:
    if cleanup_error is not None:
        error.add_note(f"temporary staging cleanup also failed: {cleanup_error}")


def publish_accepted_bundle(
    candidate: Path,
    evidence_root: Path,
    study_id: str,
    audit: BundleAudit,
) -> Path:
    """Audit, durably stage, and exclusively publish one accepted evidence bundle."""

    checked_study_id = _validate_study_id(study_id)
    checked_candidate = _validate_candidate(candidate)
    if not callable(audit):
        raise TypeError("audit must be callable")

    destination = evidence_root / checked_study_id
    audit(checked_candidate)

    try:
        evidence_root.mkdir(parents=True, exist_ok=True)
        root_mode = evidence_root.lstat().st_mode
        if not stat.S_ISDIR(root_mode):
            raise OSError(errno.ENOTDIR, "evidence root is not a regular directory", evidence_root)
        _fsync_open_path(evidence_root.parent, directory=True)
        temporary_container = Path(tempfile.mkdtemp(prefix=f".{checked_study_id}.", suffix=".tmp", dir=evidence_root))
    except OSError as error:
        raise _publication_error(str(error)) from error

    temporary = temporary_container / checked_study_id

    try:
        shutil.copytree(checked_candidate, temporary, symlinks=True)
        _fsync_tree(temporary_container)
    except OSError as error:
        cleanup_error = _cleanup_temporary(temporary_container)
        detail = str(error)
        if cleanup_error is not None:
            detail = f"{detail}; temporary cleanup also failed: {cleanup_error}"
        raise _publication_error(detail) from error
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    try:
        audit(temporary)
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    try:
        _rename_noreplace(temporary, destination)
    except OSError as error:
        cleanup_error = _cleanup_temporary(temporary_container)
        if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            collision = _collision(destination)
            _note_cleanup_failure(collision, cleanup_error)
            raise collision from error
        detail = str(error)
        if cleanup_error is not None:
            detail = f"{detail}; temporary cleanup also failed: {cleanup_error}"
        raise _publication_error(detail) from error
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    try:
        temporary_container.rmdir()
        _fsync_open_path(evidence_root, directory=True)
    except OSError as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise AcceptedBundlePublicationError(destination, error) from error
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    return destination
