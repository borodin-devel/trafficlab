"""Strict, deterministic checkpoint and derived-history persistence for genetic fitting."""

from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Mapping, Sequence
from io import StringIO
from pathlib import Path
from typing import Annotated, Literal, Self, cast

import numpy as np
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from trafficlab.artifacts import atomic_replace as _atomic_replace
from trafficlab.common.compatibility import ContentIdentity, require_compatible
from trafficlab.common.config import FamilyName, FloatBounds, GenerationLimits, IntegerBounds, SimilarityConfig
from trafficlab.common.errors import EvidenceState, FailureAuthority, FailureOutcome, TrafficlabError
from trafficlab.common.scientific_schema import require_current_scientific_schema
from trafficlab.comparison.similarity.common import FrozenJsonValue
from trafficlab.fitting.genetic.coordinates import GeneCoordinate
from trafficlab.fitting.genetic.population import priority_rank_key, rank_candidates, validate_family_priority
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateFailure,
    CandidateId,
    DuplicateDiagnostic,
    FamilyPriority,
    HistoryRow,
    MethodName,
    MethodTrialResult,
    TerminalReason,
    TrialResult,
    rebuild_genetic_record,
)
from trafficlab.generation.models.common import Genes, make_rng
from trafficlab.generation.models.registry import get_family

RNG_ENGINE: Literal["numpy.random.Generator/PCG64"] = "numpy.random.Generator/PCG64"
_FAMILY_NAMES = frozenset(("markov_renewal", "mmpp", "poisson_empirical"))
_COORDINATE_KINDS = frozenset(("linear", "log", "integer"))
_DUPLICATE_OUTCOMES = frozenset(("invalid", "duplicate", "exhausted"))
_TERMINAL_REASONS = frozenset(("running", "hard_limit", "early_stop"))
_GENETIC_KEYS = (
    "master_seed",
    "final_seed",
    "population_size",
    "generation_count",
    "tournament_size",
    "elite_count",
    "duplicate_mutation_attempts",
    "early_stopping_generations",
    "early_stopping_tolerance",
    "resume",
)
_HISTORY_HEADER = (
    "generation",
    "scope",
    "family",
    "candidate_count",
    "valid_count",
    "best_fitness",
    "mean_fitness",
    "best_birth_generation",
    "best_birth_index",
)


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

    scientific_artifact_schema: Literal[4]
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


class CheckpointCorruptionError(TrafficlabError):
    """A malformed or internally inconsistent checkpoint whose bytes must be preserved."""


def _invalid(detail: str) -> CheckpointCorruptionError:
    return CheckpointCorruptionError(
        f"invalid checkpoint: {detail}",
        corrective_action="preserve the checkpoint and resume from a compatible complete generation",
    )


def _validation_error_detail(error: ValidationError) -> str:
    """Return stable Pydantic diagnostics without persisted input values or documentation URLs."""
    details: list[str] = []
    for item in error.errors(include_url=False, include_input=False):
        location = ".".join(str(component) for component in item["loc"])
        details.append(f"{location}: {item['msg']} [{item['type']}]")
    return "; ".join(details)


def _is_rng_engine_identifier(value: object) -> bool:
    """Return whether a named RNG engine uses nonempty ASCII slash-separated segments."""
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_.]+(?:/[A-Za-z0-9_.]+)+", value) is not None


def _compatibility_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"checkpoint {detail} does not match the effective experiment",
        corrective_action="resume with the exact saved experiment and runtime or start a new run directory",
    )


def atomic_replace(path: Path, content: bytes) -> None:
    """Replace rendered validated bytes after proving the persisted temporary copy is exact."""

    # Validation reads the temporary sibling back from disk before rename.  A
    # successful return therefore means the atomic replacement published the
    # exact rendered bytes, not merely that the preceding write call succeeded.

    def validate(persisted: bytes) -> None:
        if persisted != content:
            raise _invalid("persisted temporary artifact differs from the rendered content")

    _atomic_replace(path, content, validator=validate)


def _string(value: object, *, name: str, nonempty: bool = False) -> str:
    if type(value) is not str or (nonempty and not value):
        qualifier = "nonempty string" if nonempty else "string"
        raise ValueError(f"{name} must be a {qualifier}")
    return value


def _integer(value: object, *, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} must be an exact integer in {bounds}")
    return value


def _float(value: object, *, name: str, positive: bool = False, bounded: bool = False) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{name} must be an exact finite float")
    if positive and value <= 0.0:
        raise ValueError(f"{name} must be a positive exact finite float")
    if bounded and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be an exact finite float in [0, 1]")
    return value


def _family_name(value: object, *, name: str) -> FamilyName:
    result = _string(value, name=name)
    if result not in _FAMILY_NAMES:
        raise ValueError(f"{name} must be a registered family name")
    return result


def _thaw_json(value: FrozenJsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return value


def _duplicate_free_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def _load_json(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_free_object,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"nonfinite JSON number {token}")),
        )
        if type(value) is not dict:
            raise ValueError("checkpoint root must be an object")
        return cast(dict[str, object], value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as error:
        raise _invalid(str(error)) from error


def _validate_coordinate(coordinate: GeneCoordinate, *, family: FamilyName) -> None:
    if type(coordinate) is not GeneCoordinate:
        raise ValueError(f"coordinates for family {family} must contain GeneCoordinate values")
    _string(coordinate.name, name=f"coordinate name for family {family}", nonempty=True)
    if coordinate.kind not in _COORDINATE_KINDS:
        raise ValueError(f"invalid coordinate kind for family {family}")
    if coordinate.kind == "integer":
        if type(coordinate.bounds) is not IntegerBounds:
            raise ValueError(f"integer coordinate bounds for family {family} must be exact integers")
    elif type(coordinate.bounds) is not FloatBounds:
        raise ValueError(f"continuous coordinate bounds for family {family} must be exact floats")
    if coordinate.kind == "log" and coordinate.bounds.lower <= 0:
        raise ValueError(f"log coordinate lower bound for family {family} must be positive")


def _validate_family_spec(spec: FamilyCheckpointSpec) -> None:
    if type(spec) is not FamilyCheckpointSpec:
        raise TypeError("families must contain FamilyCheckpointSpec values")
    family = _family_name(spec.name, name="family name")
    if type(spec.gene_order) is not tuple or not spec.gene_order:
        raise ValueError(f"gene order for family {spec.name} must be a nonempty tuple")
    if any(type(name) is not str or not name for name in spec.gene_order):
        raise ValueError(f"gene order for family {spec.name} must contain nonempty strings")
    if len(spec.gene_order) != len(set(spec.gene_order)):
        raise ValueError(f"duplicate gene name for family {spec.name}")
    if spec.gene_order != get_family(family).gene_names:
        raise ValueError(f"gene order for family {spec.name} must equal the exact registered gene order")
    if type(spec.coordinates) is not tuple:
        raise TypeError(f"coordinates for family {spec.name} must be a tuple")
    for coordinate in spec.coordinates:
        _validate_coordinate(coordinate, family=spec.name)
    if tuple(coordinate.name for coordinate in spec.coordinates) != spec.gene_order:
        raise ValueError(f"coordinate order for family {spec.name} must equal gene order")
    _float(spec.crossover_probability, name=f"crossover probability for family {spec.name}", bounded=True)
    _float(spec.mutation_probability, name=f"mutation probability for family {spec.name}", bounded=True)
    mutation_scale = _float(spec.mutation_scale, name=f"mutation scale for family {spec.name}", bounded=True)
    if mutation_scale <= 0.0:
        raise ValueError(f"mutation scale for family {spec.name} must be positive")


def _validate_genetic(settings: GeneticCheckpointSettings, *, family_count: int, trial_seeds: tuple[int, ...]) -> None:
    if type(settings) is not GeneticCheckpointSettings:
        raise TypeError("genetic settings must be GeneticCheckpointSettings")
    _integer(settings.master_seed, name="genetic master_seed")
    _integer(settings.final_seed, name="genetic final_seed")
    population_size = _integer(settings.population_size, name="genetic population_size", minimum=2)
    generation_count = _integer(settings.generation_count, name="genetic generation_count")
    tournament_size = _integer(settings.tournament_size, name="genetic tournament_size", minimum=2)
    elite_count = _integer(settings.elite_count, name="genetic elite_count", minimum=1)
    _integer(settings.duplicate_mutation_attempts, name="genetic duplicate_mutation_attempts")
    early_limit = _integer(settings.early_stopping_generations, name="genetic early_stopping_generations")
    _float(settings.early_stopping_tolerance, name="genetic early_stopping_tolerance", bounded=True)
    if type(settings.resume) is not bool:
        raise ValueError("genetic resume must be a boolean")
    if tournament_size > population_size:
        raise ValueError("genetic tournament_size must not exceed population_size")
    if elite_count >= population_size:
        raise ValueError("genetic elite_count must be less than population_size")
    if population_size < elite_count + family_count:
        raise ValueError("genetic population_size must include elites and every family")
    if early_limit > generation_count:
        raise ValueError("genetic early_stopping_generations must not exceed generation_count")
    if settings.final_seed in trial_seeds:
        raise ValueError("genetic final_seed must not be a selection trial seed")


def _validate_compatibility_shape(value: CheckpointCompatibility, *, require_current_rng_engine: bool = True) -> None:
    if type(value) is not CheckpointCompatibility:
        raise TypeError("compatibility must be CheckpointCompatibility")
    require_current_scientific_schema(value.scientific_artifact_schema, artifact="checkpoint")
    for name, identity in (
        ("experiment", value.experiment_identity),
        ("reference", value.reference_identity),
        ("capture", value.capture_identity),
    ):
        if type(identity) is not ContentIdentity:
            raise TypeError(f"{name}_identity must be a ContentIdentity")
    _float(value.observation_window_seconds, name="observation_window_seconds", positive=True)
    if type(value.trial_seeds) is not tuple or not value.trial_seeds:
        raise ValueError("trial_seeds must be a nonempty tuple")
    for seed in value.trial_seeds:
        _integer(seed, name="trial seed")
    if len(value.trial_seeds) != len(set(value.trial_seeds)):
        raise ValueError("trial_seeds must be unique")
    if type(value.trial_limits) is not GenerationLimits:
        raise TypeError("trial_limits must be GenerationLimits")
    if type(value.families) is not tuple or not value.families:
        raise ValueError("families must be a nonempty tuple")
    for family in value.families:
        _validate_family_spec(family)
    family_names = tuple(family.name for family in value.families)
    if family_names != tuple(sorted(family_names)):
        raise ValueError("families must be in lexical order")
    if len(family_names) != len(set(family_names)):
        raise ValueError("families contain a duplicate family name")
    validate_family_priority(value.family_priority, enabled_families=family_names)
    _validate_genetic(value.genetic, family_count=len(value.families), trial_seeds=value.trial_seeds)
    if type(value.similarity) is not SimilarityConfig:
        raise TypeError("similarity must be SimilarityConfig")
    _string(value.python_version, name="python_version", nonempty=True)
    _string(value.rng_engine, name="rng_engine", nonempty=True)
    if require_current_rng_engine and value.rng_engine != RNG_ENGINE:
        raise ValueError(f"rng_engine must be {RNG_ENGINE}")


def validate_compatibility(stored: CheckpointCompatibility, expected: CheckpointCompatibility) -> None:
    """Reject the first compatibility difference in the architecture-defined order."""
    try:
        _validate_compatibility_shape(stored, require_current_rng_engine=False)
        _validate_compatibility_shape(expected)
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error
    ordered_expected: dict[str, object] = {
        "experiment snapshot SHA-256/size identity": expected.experiment_identity,
        "reference SHA-256/size identity": expected.reference_identity,
        "capture SHA-256/size identity": expected.capture_identity,
        "observation window": expected.observation_window_seconds,
        "trial seeds": expected.trial_seeds,
        "trial generation limits": expected.trial_limits,
    }
    ordered_stored: dict[str, object] = {
        "experiment snapshot SHA-256/size identity": stored.experiment_identity,
        "reference SHA-256/size identity": stored.reference_identity,
        "capture SHA-256/size identity": stored.capture_identity,
        "observation window": stored.observation_window_seconds,
        "trial seeds": stored.trial_seeds,
        "trial generation limits": stored.trial_limits,
    }
    try:
        require_compatible(ordered_expected, ordered_stored)
    except TrafficlabError as error:
        if stored.reference_identity != expected.reference_identity:
            raise TrafficlabError(
                f"checkpoint is incompatible: reference SHA-256/size identity differs: {error}",
                corrective_action="recreate the capture pair in a new matching run",
                failure_outcome=FailureOutcome(
                    kind="artifact_changed",
                    stage="fit",
                    detail="reference.pcapng changed during fit resume",
                    affected_evidence="reference.pcapng",
                    evidence_state="preserved",
                    corrective_action="recreate the capture pair in a new matching run",
                    authority="primary",
                ),
            ) from error
        raise _compatibility_error(str(error)) from error
    stored_names = tuple(family.name for family in stored.families)
    expected_names = tuple(family.name for family in expected.families)
    if stored_names != expected_names:
        raise _compatibility_error("lexical family names")
    if stored.family_priority != expected.family_priority:
        raise _compatibility_error("family priority")
    for stored_family, expected_family in zip(stored.families, expected.families, strict=True):
        name = stored_family.name
        if stored_family.gene_order != expected_family.gene_order:
            raise _compatibility_error(f"gene order for family {name}")
        if stored_family.coordinates != expected_family.coordinates:
            raise _compatibility_error(f"coordinate metadata for family {name}")
        stored_operators = (
            stored_family.crossover_probability,
            stored_family.mutation_probability,
            stored_family.mutation_scale,
        )
        expected_operators = (
            expected_family.crossover_probability,
            expected_family.mutation_probability,
            expected_family.mutation_scale,
        )
        if stored_operators != expected_operators:
            raise _compatibility_error(f"operator values for family {name}")
    for field_name in _GENETIC_KEYS:
        if getattr(stored.genetic, field_name) != getattr(expected.genetic, field_name):
            raise _compatibility_error(f"genetic setting {field_name}")
    if stored.similarity != expected.similarity:
        raise _compatibility_error("similarity settings and weights")
    if stored.python_version != expected.python_version:
        raise _compatibility_error("Python version")
    if stored.rng_engine != expected.rng_engine:
        raise _compatibility_error("RNG engine")


def _validate_rng_state(value: RngState) -> None:
    if type(value) is not RngState:
        raise TypeError("rng state must be RngState")
    RngState.model_validate(value.model_dump(mode="python"))


def encode_rng_state(rng: object) -> RngState:
    """Validate and detach the exact JSON-compatible state of one PCG64 generator."""
    try:
        if type(rng) is not np.random.Generator or type(rng.bit_generator) is not np.random.PCG64:
            raise ValueError("RNG must be numpy.random.Generator with PCG64")
        result = RngState.model_validate(rng.bit_generator.state)
        _validate_rng_state(result)
        return result
    except (TypeError, ValueError, ValidationError) as error:
        if isinstance(error, ValidationError):
            raise _invalid(_validation_error_detail(error)) from error
        raise _invalid(str(error)) from error


def decode_rng_state(state: RngState) -> np.random.Generator:
    """Restore one explicit PCG64 generator from its exact validated state."""
    try:
        _validate_rng_state(state)
        validated = RngState.model_validate(state.model_dump(mode="python"))
        rng = make_rng(0)
        rng.bit_generator.state = validated.model_dump(mode="python")
        return rng
    except ValidationError as error:
        raise _invalid(_validation_error_detail(error)) from error
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error


def _parse_gene(value: object, coordinate: GeneCoordinate, *, family: FamilyName) -> float | int:
    if coordinate.kind == "integer":
        gene = _integer(value, name=f"{coordinate.name} gene for family {family}", minimum=-(2**63))
    else:
        gene = _float(value, name=f"{coordinate.name} gene for family {family}")
    if not coordinate.bounds.lower <= gene <= coordinate.bounds.upper:
        raise ValueError(f"{coordinate.name} gene for family {family} is outside its coordinate bounds")
    return gene


def _method_weights(similarity: SimilarityConfig) -> dict[MethodName, float]:
    weights = similarity.method_weights
    return {
        "autocorrelation": weights.autocorrelation,
        "frame_size_ks": weights.frame_size_ks,
        "iat_ks": weights.iat_ks,
        "multiscale_rate": weights.multiscale_rate,
    }


def _weighted_score(methods: Sequence[MethodTrialResult], similarity: SimilarityConfig) -> float:
    weights = _method_weights(similarity)
    score = math.fsum(weights[method.name] * method.score for method in methods)
    if -1e-12 <= score < 0.0:
        return 0.0
    if 1.0 < score <= 1.0 + 1e-12:
        return 1.0
    return score


def _validate_candidate(
    candidate: Candidate, state: CheckpointState, specs: Mapping[FamilyName, FamilyCheckpointSpec]
) -> None:
    if type(candidate) is not Candidate:
        raise TypeError("population must contain Candidate values")
    if candidate.family not in specs:
        raise ValueError(f"candidate family {candidate.family} is not enabled")
    if candidate.identifier.birth_generation > state.generation:
        raise ValueError("candidate identifier birth generation exceeds checkpoint generation")
    if candidate.status not in {"valid", "invalid"}:
        raise ValueError("checkpoint population contains a pending candidate")
    if candidate.genes is not None:
        coordinates = specs[candidate.family].coordinates
        if len(candidate.genes) != len(coordinates):
            raise ValueError(f"candidate genes for family {candidate.family} have the wrong arity")
        for gene, coordinate in zip(candidate.genes, coordinates, strict=True):
            _parse_gene(gene, coordinate, family=candidate.family)
        if candidate.family == "markov_renewal" and not cast(float, candidate.genes[0]) < cast(
            float, candidate.genes[1]
        ):
            raise ValueError("candidate markov_renewal genes must preserve canonical q1 strictly less than q2")
        if candidate.family == "mmpp" and not cast(float, candidate.genes[2]) < cast(float, candidate.genes[3]):
            raise ValueError("candidate mmpp genes must preserve canonical lambda0 strictly less than lambda1")
    if candidate.status == "valid":
        if candidate.genes is None:
            raise ValueError("valid candidate genes must not be null")
        if candidate.invalid is not None:
            raise ValueError("valid candidate invalid diagnostic must be null")
        if tuple(trial.seed for trial in candidate.trials) != state.compatibility.trial_seeds:
            raise ValueError("valid candidate trials must contain all configured trial seeds in order")
    else:
        if candidate.fitness != 0.0:
            raise ValueError("invalid candidate fitness must be exactly 0.0")
        if candidate.invalid is None:
            raise ValueError("invalid candidate must contain an invalid diagnostic")
    seen_seeds: set[int] = set()
    for trial in candidate.trials:
        if trial.seed in seen_seeds:
            raise ValueError("candidate contains a duplicate trial seed")
        seen_seeds.add(trial.seed)
        expected_aggregate = _weighted_score(trial.methods, state.compatibility.similarity)
        if trial.aggregate_score != expected_aggregate:
            raise ValueError("candidate trial aggregate_score does not equal the recomputed weighted score")
    if candidate.status == "valid":
        expected_fitness = math.fsum(trial.aggregate_score for trial in candidate.trials) / len(candidate.trials)
        if candidate.fitness != expected_fitness:
            raise ValueError("candidate fitness does not equal the recomputed trial mean")


def summarize_generation(
    generation: int,
    population: Sequence[Candidate],
    families: Sequence[FamilyName],
    *,
    family_priority: FamilyPriority,
) -> tuple[HistoryRow, ...]:
    """Derive lexical family rows followed by one overall row from an evaluated population."""
    _integer(generation, name="history generation")
    if not population:
        raise _invalid("cannot summarize an empty population")
    family_names = tuple(families)
    if family_names != tuple(sorted(family_names)) or len(family_names) != len(set(family_names)):
        raise _invalid("history families must be unique and lexical")
    try:
        priority = validate_family_priority(family_priority, enabled_families=family_names)
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error

    def make_row(candidates: tuple[Candidate, ...], family: FamilyName | None) -> HistoryRow:
        if not candidates:
            raise _invalid(f"history family {family} has no candidate")
        best = (
            rank_candidates(candidates, family_priority=priority)[0]
            if family is None
            else rank_candidates(candidates, family_priority=(family,))[0]
        )
        return HistoryRow(
            generation=generation,
            scope="overall" if family is None else "family",
            family=family,
            candidate_count=len(candidates),
            valid_count=sum(candidate.status == "valid" for candidate in candidates),
            best_fitness=best.fitness,
            mean_fitness=math.fsum(candidate.fitness for candidate in candidates) / len(candidates),
            best_identifier=best.identifier,
        )

    complete = tuple(population)
    rows = [
        make_row(tuple(candidate for candidate in complete if candidate.family == family), family)
        for family in family_names
    ]
    overall = make_row(complete, None)
    grouped_mean = math.fsum(row.mean_fitness * row.candidate_count for row in rows) / len(complete)
    rows.append(rebuild_genetic_record(overall, mean_fitness=grouped_mean))
    return tuple(rows)


def _history_winner(rows: Sequence[HistoryRow], family_priority: FamilyPriority) -> HistoryRow:
    """Choose one family-row winner through the shared scientific ranking key."""
    return min(
        rows,
        key=lambda row: priority_rank_key(
            row.best_fitness,
            cast(FamilyName, row.family),
            row.best_identifier,
            family_priority=family_priority,
        ),
    )


def _validate_history(state: CheckpointState, family_names: tuple[FamilyName, ...]) -> None:
    # Every generation is a fixed lexical block: one row per enabled family,
    # then the overall winner.  Enforcing the shape makes CSV projection and
    # resume selection deterministic instead of trusting stored row order.
    block_size = len(family_names) + 1
    expected_length = (state.generation + 1) * block_size
    if len(state.history) != expected_length:
        raise ValueError("history must contain one complete block for every generation")
    priority = validate_family_priority(state.family_priority, enabled_families=family_names)
    for generation in range(state.generation + 1):
        block = state.history[generation * block_size : (generation + 1) * block_size]
        expected_shape = tuple((generation, "family", family) for family in family_names) + (
            (generation, "overall", None),
        )
        if tuple((row.generation, row.scope, row.family) for row in block) != expected_shape:
            raise ValueError("history rows must be ascending lexical family rows followed by overall")
        family_rows = block[:-1]
        overall = block[-1]
        for row in block:
            candidate_count = _integer(row.candidate_count, name="history candidate_count", minimum=1)
            valid_count = _integer(row.valid_count, name="history valid_count")
            best_fitness = _float(row.best_fitness, name="history best_fitness", bounded=True)
            mean_fitness = _float(row.mean_fitness, name="history mean_fitness", bounded=True)
            if valid_count > candidate_count:
                raise ValueError("history valid_count must not exceed candidate_count")
            if valid_count == 0 and (best_fitness != 0.0 or mean_fitness != 0.0):
                raise ValueError("history row with zero valid_count must have zero best_fitness and mean_fitness")
            mean_numerator, mean_denominator = mean_fitness.as_integer_ratio()
            best_numerator, best_denominator = best_fitness.as_integer_ratio()
            if mean_numerator * candidate_count * best_denominator > best_numerator * valid_count * mean_denominator:
                raise ValueError("history mean_fitness is not feasible for valid_count")
            if row.best_identifier.birth_generation > generation:
                raise ValueError("history best identifier birth generation exceeds row generation")
        if sum(row.candidate_count for row in family_rows) != overall.candidate_count:
            raise ValueError("history overall candidate_count does not equal family counts")
        if sum(row.valid_count for row in family_rows) != overall.valid_count:
            raise ValueError("history overall valid_count does not equal family counts")
        if overall.candidate_count != state.compatibility.genetic.population_size:
            raise ValueError("history overall candidate_count does not equal population_size")
        family_best = _history_winner(family_rows, priority)
        if (overall.best_fitness, overall.best_identifier) != (
            family_best.best_fitness,
            family_best.best_identifier,
        ):
            raise ValueError("history overall best does not equal the recomputed family best")
        expected_mean = (
            math.fsum(row.mean_fitness * row.candidate_count for row in family_rows) / overall.candidate_count
        )
        if overall.mean_fitness != expected_mean:
            raise ValueError("history overall mean does not equal the recomputed family mean")
    current = summarize_generation(
        state.generation,
        state.population,
        family_names,
        family_priority=priority,
    )
    if state.history[-block_size:] != current:
        raise ValueError("last history block does not equal the current population summary")


def _history_progress(
    state: CheckpointState,
    *,
    block_size: int,
    family_priority: FamilyPriority,
) -> tuple[CandidateId, float, int]:
    """Recompute the retained winner and exact stagnation counter from overall history rows."""
    retained = _history_winner(state.history[: block_size - 1], family_priority)
    consecutive_stagnation = 0
    genetic = state.compatibility.genetic
    for generation in range(1, state.generation + 1):
        block = state.history[generation * block_size : (generation + 1) * block_size]
        current = _history_winner(block[:-1], family_priority)
        improvement = current.best_fitness - retained.best_fitness
        if priority_rank_key(
            current.best_fitness,
            cast(FamilyName, current.family),
            current.best_identifier,
            family_priority=family_priority,
        ) < priority_rank_key(
            retained.best_fitness,
            cast(FamilyName, retained.family),
            retained.best_identifier,
            family_priority=family_priority,
        ):
            retained = current
        consecutive_stagnation = 0 if improvement > genetic.early_stopping_tolerance else consecutive_stagnation + 1
        historical_terminal: TerminalReason
        if generation == genetic.generation_count:
            historical_terminal = "hard_limit"
        elif genetic.early_stopping_generations > 0 and consecutive_stagnation >= genetic.early_stopping_generations:
            historical_terminal = "early_stop"
        else:
            historical_terminal = "running"
        if generation < state.generation and historical_terminal == "early_stop":
            raise ValueError(f"history continues after early_stop at generation {generation}")
    return retained.best_identifier, retained.best_fitness, consecutive_stagnation


def _validate_state(state: CheckpointState) -> None:
    if type(state) is not CheckpointState:
        raise TypeError("checkpoint state must be CheckpointState")
    _validate_compatibility_shape(state.compatibility)
    generation = _integer(state.generation, name="generation")
    if generation > state.compatibility.genetic.generation_count:
        raise ValueError("generation exceeds configured generation_count")
    _validate_rng_state(state.rng_state)
    if type(state.population) is not tuple:
        raise TypeError("population must be a tuple")
    if len(state.population) != state.compatibility.genetic.population_size:
        raise ValueError("population must contain exactly population_size candidates")
    family_names: tuple[FamilyName, ...] = tuple(family.name for family in state.compatibility.families)
    priority = validate_family_priority(state.family_priority, enabled_families=family_names)
    if priority != state.compatibility.family_priority:
        raise ValueError("state family_priority must equal compatibility family_priority")
    specs: dict[FamilyName, FamilyCheckpointSpec] = {family.name: family for family in state.compatibility.families}
    for candidate in state.population:
        _validate_candidate(candidate, state, specs)
    identifiers = tuple(candidate.identifier for candidate in state.population)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("population contains a duplicate candidate identifier")
    if {candidate.family for candidate in state.population} != set(family_names):
        raise ValueError("population must represent every configured family")
    if type(state.history) is not tuple or any(type(row) is not HistoryRow for row in state.history):
        raise TypeError("history must be a tuple of HistoryRow values")
    _validate_history(state, family_names)
    candidates_by_id = {candidate.identifier: candidate for candidate in state.population}
    if state.best_identifier not in candidates_by_id:
        raise ValueError("best identifier must occur in the current population")
    best = candidates_by_id[state.best_identifier]
    if best.fitness != state.best_fitness:
        raise ValueError("best fitness must equal the identified current candidate fitness")
    current_best = rank_candidates(state.population, family_priority=priority)[0]
    if (state.best_fitness, state.best_identifier) != (current_best.fitness, current_best.identifier):
        raise ValueError("best must equal the stable current population winner")
    _float(state.best_fitness, name="best fitness", bounded=True)
    retained_identifier, retained_fitness, expected_stagnation = _history_progress(
        state,
        block_size=len(family_names) + 1,
        family_priority=priority,
    )
    if (state.best_fitness, state.best_identifier) != (retained_fitness, retained_identifier):
        raise ValueError("best does not equal the retained history winner")
    stagnation = _integer(state.consecutive_stagnation, name="consecutive_stagnation")
    if stagnation > generation:
        raise ValueError("consecutive_stagnation cannot exceed generation")
    if stagnation != expected_stagnation:
        raise ValueError("consecutive_stagnation does not equal the value recomputed from history")
    if state.terminal_reason not in _TERMINAL_REASONS:
        raise ValueError("terminal_reason is not recognized")
    genetic = state.compatibility.genetic
    hard = generation == genetic.generation_count
    early = genetic.early_stopping_generations > 0 and stagnation >= genetic.early_stopping_generations
    if state.terminal_reason == "hard_limit" and not hard:
        raise ValueError("hard_limit requires generation equal to generation_count")
    if state.terminal_reason == "early_stop" and (hard or not early):
        raise ValueError("early_stop requires a pre-limit generation and the configured stagnation count")
    if state.terminal_reason == "running" and (hard or early):
        raise ValueError("running checkpoint already satisfies a terminal condition")


def _coordinate_document(coordinate: GeneCoordinate) -> dict[str, object]:
    return {
        "name": coordinate.name,
        "kind": coordinate.kind,
        "lower": coordinate.bounds.lower,
        "upper": coordinate.bounds.upper,
    }


def _family_document(family: FamilyCheckpointSpec) -> dict[str, object]:
    return {
        "name": family.name,
        "gene_order": list(family.gene_order),
        "coordinates": [_coordinate_document(coordinate) for coordinate in family.coordinates],
        "operators": {
            "crossover_probability": family.crossover_probability,
            "mutation_probability": family.mutation_probability,
            "mutation_scale": family.mutation_scale,
        },
    }


def _genetic_document(genetic: GeneticCheckpointSettings) -> dict[str, object]:
    return {name: getattr(genetic, name) for name in _GENETIC_KEYS}


def _similarity_document(similarity: SimilarityConfig) -> dict[str, object]:
    return similarity.model_dump(mode="python")


def _method_document(method: MethodTrialResult) -> dict[str, object]:
    return {
        "name": method.name,
        "score": method.score,
        "diagnostics": _thaw_json(cast(FrozenJsonValue, method.diagnostics)),
    }


def _trial_document(trial: TrialResult) -> dict[str, object]:
    return {
        "seed": trial.seed,
        "aggregate_score": trial.aggregate_score,
        "methods": [_method_document(method) for method in trial.methods],
        "model_diagnostics": dict(trial.model_diagnostics),
    }


def _identifier_document(identifier: CandidateId) -> list[int]:
    return [identifier.birth_generation, identifier.birth_index]


def _candidate_document(candidate: Candidate) -> dict[str, object]:
    invalid = candidate.invalid
    return {
        "identifier": _identifier_document(candidate.identifier),
        "family": candidate.family,
        "genes": None if candidate.genes is None else list(candidate.genes),
        "status": candidate.status,
        "fitness": candidate.fitness,
        "trials": [_trial_document(trial) for trial in candidate.trials],
        "invalid": None
        if invalid is None
        else {
            "kind": invalid.kind,
            "seed": invalid.seed,
            "detail": invalid.detail,
            "stage": invalid.stage,
            "affected_evidence": invalid.affected_evidence,
            "evidence_state": invalid.evidence_state,
            "corrective_action": invalid.corrective_action,
            "authority": invalid.authority,
        },
        "duplicate_diagnostics": [
            {"attempt": item.attempt, "outcome": item.outcome, "detail": item.detail}
            for item in candidate.duplicate_diagnostics
        ],
    }


def _history_document(row: HistoryRow) -> dict[str, object]:
    return {
        "generation": row.generation,
        "scope": row.scope,
        "family": row.family,
        "candidate_count": row.candidate_count,
        "valid_count": row.valid_count,
        "best_fitness": row.best_fitness,
        "mean_fitness": row.mean_fitness,
        "best_identifier": _identifier_document(row.best_identifier),
    }


def _checkpoint_document(state: CheckpointState) -> dict[str, object]:
    compatibility = state.compatibility
    rng = state.rng_state
    return {
        "scientific_artifact_schema": compatibility.scientific_artifact_schema,
        "experiment_identity": compatibility.experiment_identity.as_dict(),
        "reference_identity": compatibility.reference_identity.as_dict(),
        "capture_identity": compatibility.capture_identity.as_dict(),
        "observation_window_seconds": compatibility.observation_window_seconds,
        "trial_seeds": list(compatibility.trial_seeds),
        "trial_limits": compatibility.trial_limits.model_dump(mode="python"),
        "families": [_family_document(family) for family in compatibility.families],
        "family_priority": list(state.family_priority),
        "genetic": _genetic_document(compatibility.genetic),
        "similarity": _similarity_document(compatibility.similarity),
        "rng": {
            "engine": compatibility.rng_engine,
            "python_version": compatibility.python_version,
            "state": {
                "bit_generator": rng.bit_generator,
                "state": {"state": rng.state.state, "inc": rng.state.inc},
                "has_uint32": rng.has_uint32,
                "uinteger": rng.uinteger,
            },
        },
        "generation": state.generation,
        "population": [_candidate_document(candidate) for candidate in state.population],
        "history": [_history_document(row) for row in state.history],
        "best": {"identifier": _identifier_document(state.best_identifier), "fitness": state.best_fitness},
        "consecutive_stagnation": state.consecutive_stagnation,
        "terminal_reason": state.terminal_reason,
    }


def _coordinate_from_record(record: CoordinateRecord) -> GeneCoordinate:
    bounds: FloatBounds | IntegerBounds
    if record.kind == "integer":
        bounds = IntegerBounds(lower=record.lower, upper=record.upper)
    else:
        bounds = FloatBounds(lower=record.lower, upper=record.upper)
    return GeneCoordinate(record.name, record.kind, bounds)


def _family_from_record(record: FamilyCheckpointRecord) -> FamilyCheckpointSpec:
    operators = record.operators
    return FamilyCheckpointSpec(
        name=record.name,
        gene_order=record.gene_order,
        coordinates=tuple(_coordinate_from_record(coordinate) for coordinate in record.coordinates),
        crossover_probability=operators.crossover_probability,
        mutation_probability=operators.mutation_probability,
        mutation_scale=operators.mutation_scale,
    )


def _identifier_from_record(record: CandidateIdentifierRecord) -> CandidateId:
    return CandidateId(birth_generation=record[0], birth_index=record[1])


def _candidate_from_record(record: CandidateRecord) -> Candidate:
    invalid = (
        None if record.invalid is None else CandidateFailure.model_validate(record.invalid.model_dump(mode="python"))
    )
    return Candidate(
        identifier=_identifier_from_record(record.identifier),
        family=record.family,
        genes=record.genes,
        status=record.status,
        fitness=record.fitness,
        trials=record.trials,
        invalid=invalid,
        duplicate_diagnostics=record.duplicate_diagnostics,
    )


def _history_from_record(record: HistoryRecord) -> HistoryRow:
    return HistoryRow(
        generation=record.generation,
        scope=record.scope,
        family=record.family,
        candidate_count=record.candidate_count,
        valid_count=record.valid_count,
        best_fitness=record.best_fitness,
        mean_fitness=record.mean_fitness,
        best_identifier=_identifier_from_record(record.best_identifier),
    )


def _compatibility_from_artifact(artifact: CheckpointArtifact) -> CheckpointCompatibility:
    return CheckpointCompatibility(
        scientific_artifact_schema=artifact.scientific_artifact_schema,
        experiment_identity=artifact.experiment_identity.to_runtime(),
        reference_identity=artifact.reference_identity.to_runtime(),
        capture_identity=artifact.capture_identity.to_runtime(),
        observation_window_seconds=artifact.observation_window_seconds,
        trial_seeds=artifact.trial_seeds,
        trial_limits=artifact.trial_limits,
        families=tuple(_family_from_record(family) for family in artifact.families),
        family_priority=artifact.family_priority,
        genetic=artifact.genetic,
        similarity=artifact.similarity,
        python_version=artifact.rng.python_version,
        rng_engine=artifact.rng.engine,
    )


def render_checkpoint(state: CheckpointState) -> bytes:
    """Render one validated checkpoint as sorted compact finite JSON with a trailing newline."""
    try:
        _validate_state(state)
        document = _checkpoint_document(state)
        wire_document = json.loads(json.dumps(document, allow_nan=False))
        artifact = CheckpointArtifact.model_validate(wire_document)
        validated_document = artifact.model_dump(mode="json")
        if validated_document != wire_document:
            raise ValueError("checkpoint schema validation changed the canonical document")
        text = json.dumps(validated_document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except ValidationError as error:
        raise _invalid(_validation_error_detail(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        raise _invalid(str(error)) from error
    return f"{text}\n".encode()


def parse_checkpoint(content: bytes, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Parse strict checkpoint bytes and reject incompatibility before RNG/state reconstruction."""
    if type(content) is not bytes:
        raise TypeError("checkpoint content must be bytes")
    document = _load_json(content)
    try:
        require_current_scientific_schema(document.get("scientific_artifact_schema"), artifact="checkpoint")
        experiment_identity = ContentIdentity.from_dict(document["experiment_identity"], name="experiment")
        _validate_compatibility_shape(compatibility)
        if experiment_identity != compatibility.experiment_identity:
            raise _compatibility_error("experiment snapshot SHA-256/size identity")
        raw_rng = document.get("rng")
        if type(raw_rng) is dict:
            engine = cast(dict[str, object], raw_rng).get("engine")
            if _is_rng_engine_identifier(engine) and engine != compatibility.rng_engine:
                raise _compatibility_error("RNG engine")
        artifact = CheckpointArtifact.model_validate(document)
        stored_compatibility = _compatibility_from_artifact(artifact)
        validate_compatibility(stored_compatibility, compatibility)
        state = CheckpointState(
            compatibility=stored_compatibility,
            generation=artifact.generation,
            population=tuple(_candidate_from_record(candidate) for candidate in artifact.population),
            history=tuple(_history_from_record(row) for row in artifact.history),
            rng_state=artifact.rng.state,
            best_identifier=_identifier_from_record(artifact.best.identifier),
            best_fitness=artifact.best.fitness,
            consecutive_stagnation=artifact.consecutive_stagnation,
            terminal_reason=artifact.terminal_reason,
            family_priority=stored_compatibility.family_priority,
        )
        _validate_state(state)
        if render_checkpoint(state) != content:
            raise ValueError("checkpoint JSON must use the canonical sorted compact encoding with one final newline")
        return state
    except TrafficlabError:
        raise
    except ValidationError as error:
        raise _invalid(_validation_error_detail(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        raise _invalid(str(error)) from error


def publish_checkpoint(path: Path, state: CheckpointState) -> None:
    """Atomically replace the canonical checkpoint with one complete validated generation."""
    content = render_checkpoint(state)
    atomic_replace(path, content)


def load_checkpoint(path: Path, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Read and validate a compatible authoritative checkpoint without changing it."""
    try:
        content = path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read checkpoint {path}: {error}",
            corrective_action="verify checkpoint.json is readable before resuming",
        ) from error
    state = parse_checkpoint(content, compatibility)
    validate_compatibility(state.compatibility, compatibility)
    return state


def _parse_decimal(value: str, *, name: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be a canonical nonnegative decimal integer")
    result = int(value)
    if str(result) != value:
        raise ValueError(f"{name} must be a canonical nonnegative decimal integer")
    return result


def _parse_repr_float(value: str, *, name: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a finite Python float repr") from error
    if not math.isfinite(result) or repr(result) != value or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be a finite Python float repr in [0, 1]")
    return result


def _parse_history_csv(content: bytes, family_names: frozenset[FamilyName]) -> tuple[HistoryRow, ...]:
    try:
        text = content.decode("utf-8")
        rows = list(csv.reader(StringIO(text, newline="")))
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError(f"history CSV is invalid: {error}") from error
    if not rows or tuple(rows[0]) != _HISTORY_HEADER:
        raise ValueError("history CSV has the wrong header")
    parsed: list[HistoryRow] = []
    for fields in rows[1:]:
        if len(fields) != len(_HISTORY_HEADER):
            raise ValueError("history CSV row has the wrong field count")
        generation, scope, family_field, candidate_count, valid_count, best, mean, birth_generation, birth_index = (
            fields
        )
        if scope not in {"family", "overall"}:
            raise ValueError("history CSV scope must be family or overall")
        if scope == "overall":
            if family_field:
                raise ValueError("overall history CSV family must be empty")
            family = None
        else:
            family = _family_name(family_field, name="history CSV family")
            if family not in family_names:
                raise ValueError("history CSV family is not enabled")
        parsed.append(
            HistoryRow(
                generation=_parse_decimal(generation, name="history CSV generation"),
                scope=cast(Literal["family", "overall"], scope),
                family=family,
                candidate_count=_parse_decimal(candidate_count, name="history CSV candidate_count"),
                valid_count=_parse_decimal(valid_count, name="history CSV valid_count"),
                best_fitness=_parse_repr_float(best, name="history CSV best_fitness"),
                mean_fitness=_parse_repr_float(mean, name="history CSV mean_fitness"),
                best_identifier=CandidateId(
                    birth_generation=_parse_decimal(birth_generation, name="history CSV best_birth_generation"),
                    birth_index=_parse_decimal(birth_index, name="history CSV best_birth_index"),
                ),
            )
        )
    return tuple(parsed)


def render_history_csv(state: CheckpointState) -> bytes:
    """Render and reparse the exact CSV projection derived solely from checkpoint history."""
    try:
        _validate_state(state)
        stream = StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(_HISTORY_HEADER)
        for row in state.history:
            writer.writerow(
                (
                    str(row.generation),
                    row.scope,
                    "" if row.family is None else row.family,
                    str(row.candidate_count),
                    str(row.valid_count),
                    repr(row.best_fitness),
                    repr(row.mean_fitness),
                    str(row.best_identifier.birth_generation),
                    str(row.best_identifier.birth_index),
                )
            )
        content = stream.getvalue().encode("utf-8")
        family_names: frozenset[FamilyName] = frozenset(family.name for family in state.compatibility.families)
        if _parse_history_csv(content, family_names) != state.history:
            raise ValueError("history CSV did not reconstruct the exact checkpoint rows")
        return content
    except (TypeError, ValueError) as error:
        raise _invalid(str(error)) from error


def publish_history_csv(path: Path, state: CheckpointState) -> None:
    """Atomically replace derived history after validating its exact scalar reconstruction."""
    content = render_history_csv(state)
    atomic_replace(path, content)


def publish_generation(run_directory: Path, state: CheckpointState) -> None:
    """Publish authoritative checkpoint first and derived history second."""
    publish_checkpoint(run_directory / "checkpoint.json", state)
    publish_history_csv(run_directory / "ga_history.csv", state)


def load_generation(run_directory: Path, compatibility: CheckpointCompatibility) -> CheckpointState:
    """Load authoritative checkpoint and repair only a missing or stale derived history projection."""
    state = load_checkpoint(run_directory / "checkpoint.json", compatibility)
    expected = render_history_csv(state)
    history_path = run_directory / "ga_history.csv"
    try:
        existing = history_path.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError as error:
        raise TrafficlabError(
            f"could not read derived history {history_path}: {error}",
            corrective_action="verify ga_history.csv is readable before resuming",
        ) from error
    if existing != expected:
        publish_history_csv(history_path, state)
    return state
