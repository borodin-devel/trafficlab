"""Strict persisted checkpoint schema and wire records."""

from __future__ import annotations

from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import FamilyName, GenerationLimits, SimilarityConfig
from trafficlab.common.errors import EvidenceState, FailureAuthority
from trafficlab.fitting.genetic.coordinates import GeneCoordinate
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateId,
    DuplicateDiagnostic,
    FamilyPriority,
    HistoryRow,
    TerminalReason,
    TrialResult,
)
from trafficlab.generation.models.common import Genes


def _exact_float_input(value: object) -> object:
    if type(value) is not float:
        raise ValueError("value must be an exact float")
    return value


def _tuple_input(value: object) -> object:
    if type(value) is list:
        return tuple(cast(list[object], value))
    return value


type ExactFloat = Annotated[float, BeforeValidator(_exact_float_input)]
type PositiveFloat = Annotated[ExactFloat, Field(gt=0.0)]
type UnitFloat = Annotated[ExactFloat, Field(ge=0.0, le=1.0)]
type NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
type PositiveInt = Annotated[StrictInt, Field(gt=0)]
type NonemptyString = Annotated[str, Field(min_length=1)]


class _StrictCheckpointModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class FamilyCheckpointSpec(_StrictCheckpointModel):
    """Resolved chromosome and operator metadata for one enabled family."""

    name: FamilyName
    gene_order: Annotated[tuple[NonemptyString, ...], BeforeValidator(_tuple_input)]
    coordinates: Annotated[tuple[GeneCoordinate, ...], BeforeValidator(_tuple_input)]
    crossover_probability: UnitFloat
    mutation_probability: UnitFloat
    mutation_scale: Annotated[UnitFloat, Field(gt=0.0)]


class GeneticCheckpointSettings(_StrictCheckpointModel):
    """All genetic settings that can alter selection, reproduction, or termination."""

    master_seed: NonnegativeInt
    final_seed: NonnegativeInt
    population_size: Annotated[StrictInt, Field(ge=2)]
    generation_count: NonnegativeInt
    tournament_size: Annotated[StrictInt, Field(ge=2)]
    elite_count: PositiveInt
    duplicate_mutation_attempts: NonnegativeInt
    early_stopping_generations: NonnegativeInt
    early_stopping_tolerance: UnitFloat
    resume: StrictBool


class Pcg64CoreState(_StrictCheckpointModel):
    """The two exact unsigned 128-bit PCG64 state integers."""

    state: Annotated[StrictInt, Field(ge=0, le=2**128 - 1)]
    inc: Annotated[StrictInt, Field(ge=0, le=2**128 - 1)]


class RngState(_StrictCheckpointModel):
    """Exact JSON-compatible state returned by NumPy's PCG64 bit generator."""

    bit_generator: Literal["PCG64"]
    state: Pcg64CoreState
    has_uint32: Annotated[StrictInt, Field(ge=0, le=1)]
    uinteger: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)]


class CheckpointCompatibility(_StrictCheckpointModel):
    """Exact inputs and effective settings that must match before resume."""

    scientific_artifact_schema: int
    experiment_identity: ContentIdentity
    reference_identity: ContentIdentity
    capture_identity: ContentIdentity
    observation_window_seconds: PositiveFloat
    trial_seeds: Annotated[tuple[NonnegativeInt, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    trial_limits: GenerationLimits
    families: Annotated[tuple[FamilyCheckpointSpec, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    family_priority: Annotated[FamilyPriority, BeforeValidator(_tuple_input)]
    genetic: GeneticCheckpointSettings
    similarity: SimilarityConfig
    python_version: NonemptyString
    rng_engine: Literal["numpy.random.Generator/PCG64"]

    @field_validator("experiment_identity", "reference_identity", "capture_identity", mode="before")
    @classmethod
    def identities_are_rebuilt_from_primitives(cls, value: object) -> object:
        raw = value.as_dict() if type(value) is ContentIdentity else value
        return ContentIdentity.from_dict(raw)

    @field_validator("trial_limits", mode="before")
    @classmethod
    def limits_are_rebuilt_from_primitives(cls, value: object) -> object:
        raw = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
        return GenerationLimits.model_validate(raw)

    @field_validator("similarity", mode="before")
    @classmethod
    def similarity_is_rebuilt_from_primitives(cls, value: object) -> object:
        raw = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
        return SimilarityConfig.model_validate(raw)

    @property
    def experiment_sha256(self) -> str:
        return self.experiment_identity.sha256

    @property
    def reference_sha256(self) -> str:
        return self.reference_identity.sha256

    @property
    def capture_sha256(self) -> str:
        return self.capture_identity.sha256


class CheckpointState(_StrictCheckpointModel):
    """One complete evaluated generation and every value needed for exact continuation."""

    compatibility: CheckpointCompatibility
    generation: NonnegativeInt
    population: Annotated[tuple[Candidate, ...], BeforeValidator(_tuple_input)]
    history: Annotated[tuple[HistoryRow, ...], BeforeValidator(_tuple_input)]
    rng_state: RngState
    best_identifier: CandidateId
    best_fitness: UnitFloat
    consecutive_stagnation: NonnegativeInt
    terminal_reason: TerminalReason
    family_priority: Annotated[FamilyPriority, BeforeValidator(_tuple_input)]


class ContentIdentityRecord(_StrictCheckpointModel):
    size: NonnegativeInt
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    def to_runtime(self) -> ContentIdentity:
        return ContentIdentity(size=self.size, sha256=self.sha256)


class LinearCoordinateRecord(_StrictCheckpointModel):
    name: NonemptyString
    kind: Literal["linear"]
    lower: ExactFloat
    upper: ExactFloat


class LogCoordinateRecord(_StrictCheckpointModel):
    name: NonemptyString
    kind: Literal["log"]
    lower: PositiveFloat
    upper: PositiveFloat


class IntegerCoordinateRecord(_StrictCheckpointModel):
    name: NonemptyString
    kind: Literal["integer"]
    lower: StrictInt
    upper: StrictInt


type CoordinateRecord = Annotated[
    LinearCoordinateRecord | LogCoordinateRecord | IntegerCoordinateRecord,
    Field(discriminator="kind"),
]


class FamilyOperatorsRecord(_StrictCheckpointModel):
    crossover_probability: UnitFloat
    mutation_probability: UnitFloat
    mutation_scale: Annotated[UnitFloat, Field(gt=0.0)]


class FamilyCheckpointRecord(_StrictCheckpointModel):
    name: FamilyName
    gene_order: Annotated[tuple[NonemptyString, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    coordinates: Annotated[tuple[CoordinateRecord, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    operators: FamilyOperatorsRecord


class _CandidateFailureRecord(_StrictCheckpointModel):
    seed: NonnegativeInt | None
    detail: NonemptyString
    stage: NonemptyString
    affected_evidence: NonemptyString
    evidence_state: EvidenceState
    corrective_action: NonemptyString
    authority: FailureAuthority


class RepairFailure(_CandidateFailureRecord):
    kind: Literal["repair"]


class FitFailure(_CandidateFailureRecord):
    kind: Literal["fit"]


class GenerationFailure(_CandidateFailureRecord):
    kind: Literal["generation"]


class IncompleteGenerationFailure(_CandidateFailureRecord):
    kind: Literal["incomplete_generation"]


class SimilarityPreconditionFailure(_CandidateFailureRecord):
    kind: Literal["similarity_precondition"]


class NonfiniteScoreFailure(_CandidateFailureRecord):
    kind: Literal["nonfinite_score"]


type CandidateFailureRecord = Annotated[
    RepairFailure
    | FitFailure
    | GenerationFailure
    | IncompleteGenerationFailure
    | SimilarityPreconditionFailure
    | NonfiniteScoreFailure,
    Field(discriminator="kind"),
]
type CandidateIdentifierRecord = Annotated[
    tuple[NonnegativeInt, NonnegativeInt],
    BeforeValidator(_tuple_input),
]


class _CandidateRecord(_StrictCheckpointModel):
    identifier: CandidateIdentifierRecord
    family: FamilyName
    fitness: UnitFloat
    trials: Annotated[tuple[TrialResult, ...], BeforeValidator(_tuple_input)]
    duplicate_diagnostics: Annotated[tuple[DuplicateDiagnostic, ...], BeforeValidator(_tuple_input)]


class PendingCandidateRecord(_CandidateRecord):
    genes: Annotated[Genes, BeforeValidator(_tuple_input)] | None
    status: Literal["pending"]
    invalid: None


class ValidCandidateRecord(_CandidateRecord):
    genes: Annotated[Genes, BeforeValidator(_tuple_input)]
    status: Literal["valid"]
    invalid: None


class InvalidCandidateRecord(_CandidateRecord):
    genes: Annotated[Genes, BeforeValidator(_tuple_input)] | None
    status: Literal["invalid"]
    fitness: Annotated[ExactFloat, Field(ge=0.0, le=0.0)]
    invalid: CandidateFailureRecord


type CandidateRecord = Annotated[
    PendingCandidateRecord | ValidCandidateRecord | InvalidCandidateRecord,
    Field(discriminator="status"),
]


class HistoryRecord(_StrictCheckpointModel):
    generation: NonnegativeInt
    scope: Literal["family", "overall"]
    family: FamilyName | None
    candidate_count: PositiveInt
    valid_count: NonnegativeInt
    best_fitness: UnitFloat
    mean_fitness: UnitFloat
    best_identifier: CandidateIdentifierRecord

    @model_validator(mode="after")
    def scope_matches_family(self) -> Self:
        if (self.scope == "family") != (self.family is not None):
            raise ValueError("family history rows require a family and overall rows require none")
        if self.valid_count > self.candidate_count:
            raise ValueError("history valid_count must not exceed candidate_count")
        return self


class NamedRngState(_StrictCheckpointModel):
    engine: Literal["numpy.random.Generator/PCG64"]
    python_version: NonemptyString
    state: RngState


class BestCandidateRecord(_StrictCheckpointModel):
    identifier: CandidateIdentifierRecord
    fitness: UnitFloat


class CheckpointArtifact(_StrictCheckpointModel):
    """Exact public checkpoint JSON root before cross-artifact compatibility checks."""

    scientific_artifact_schema: Literal[5]
    experiment_identity: ContentIdentityRecord
    reference_identity: ContentIdentityRecord
    capture_identity: ContentIdentityRecord
    observation_window_seconds: PositiveFloat
    trial_seeds: Annotated[tuple[NonnegativeInt, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    trial_limits: GenerationLimits
    families: Annotated[tuple[FamilyCheckpointRecord, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    family_priority: Annotated[FamilyPriority, Field(min_length=1), BeforeValidator(_tuple_input)]
    genetic: GeneticCheckpointSettings
    similarity: SimilarityConfig
    rng: NamedRngState
    generation: NonnegativeInt
    population: Annotated[tuple[CandidateRecord, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    history: Annotated[tuple[HistoryRecord, ...], Field(min_length=1), BeforeValidator(_tuple_input)]
    best: BestCandidateRecord
    consecutive_stagnation: NonnegativeInt
    terminal_reason: TerminalReason

    @field_validator("trial_limits", mode="before")
    @classmethod
    def limits_are_rebuilt_from_primitives(cls, value: object) -> object:
        raw = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
        return GenerationLimits.model_validate(raw)

    @field_validator("similarity", mode="before")
    @classmethod
    def similarity_is_rebuilt_from_primitives(cls, value: object) -> object:
        raw = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
        return SimilarityConfig.model_validate(raw)
