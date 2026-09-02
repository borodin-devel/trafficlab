"""Immutable value contracts for genetic fitting."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from functools import total_ordering
from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_serializer,
    field_validator,
    model_validator,
)

from trafficlab.common.config import FamilyName
from trafficlab.common.errors import EvidenceState, FailureAuthority
from trafficlab.comparison.diagnostics import FITNESS_METHOD_NAMES, MethodName
from trafficlab.comparison.similarity.common import FrozenJsonValue, JsonValue, SimilarityResult
from trafficlab.generation.models.common import (
    MARKOV_MODEL_DIAGNOSTIC_KEYS,
    Genes,
    ModelDiagnostics,
    freeze_model_diagnostics,
)

type CandidateStatus = Literal["pending", "valid", "invalid"]
type FamilyPriority = tuple[str, ...]
type CandidateFailureKind = Literal[
    "repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score"
]
type DuplicateOutcome = Literal["invalid", "duplicate", "exhausted"]
type TerminalReason = Literal["running", "hard_limit", "early_stop"]

METHOD_ORDER: tuple[MethodName, ...] = FITNESS_METHOD_NAMES
_METHOD_NAMES = frozenset(METHOD_ORDER)
_FAILURE_KINDS = frozenset(
    ("repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score")
)
_EVIDENCE_STATES = frozenset(("not_published", "diagnostic_only", "preserved", "possibly_remaining"))
_FAILURE_AUTHORITIES = frozenset(("primary", "secondary"))
_DUPLICATE_OUTCOMES = frozenset(("invalid", "duplicate", "exhausted"))
_CANDIDATE_STATUSES = frozenset(("pending", "valid", "invalid"))
_FAMILY_NAMES = frozenset(
    ("poisson_empirical", "markov_renewal", "mmpp", "nhpp", "acd", "markov_packet_train", "packet_hmm")
)
_MARKOV_MODEL_DIAGNOSTIC_NAMES = frozenset(MARKOV_MODEL_DIAGNOSTIC_KEYS)


def _exact_float(value: object) -> object:
    if type(value) is not float:
        raise ValueError("value must be an exact float")
    return value


def _tuple_input(value: object) -> object:
    if type(value) is list:
        return tuple(cast(list[object], value))
    return value


type UnitFloat = Annotated[float, BeforeValidator(_exact_float), Field(ge=0.0, le=1.0)]
type NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
type NonemptyString = Annotated[str, Field(min_length=1)]


class _StrictGeneticModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


def _empty_model_diagnostics() -> dict[str, int]:
    return {}


def _is_packet_hmm_diagnostic_shape(diagnostics: ModelDiagnostics) -> bool:
    names = frozenset(diagnostics)
    state_indexes = {
        int(match.group(1)) for name in names if (match := re.fullmatch(r"hidden_state_(\d+)_count", name))
    }
    category_indexes = {int(match.group(1)) for name in names if (match := re.fullmatch(r"category_(\d+)_count", name))}
    expected_names = {
        *(f"hidden_state_{index}_count" for index in state_indexes),
        *(f"category_{index}_count" for index in category_indexes),
    }
    return (
        names == frozenset(expected_names)
        and 2 <= len(state_indexes) <= 4
        and state_indexes == set(range(len(state_indexes)))
        and 1 <= len(category_indexes) <= 24
        and category_indexes == set(range(len(category_indexes)))
        and sum(diagnostics[f"hidden_state_{index}_count"] for index in state_indexes)
        == sum(diagnostics[f"category_{index}_count"] for index in category_indexes)
    )


def _validate_model_diagnostic_shape(diagnostics: ModelDiagnostics) -> None:
    names = frozenset(diagnostics)
    if names and names != _MARKOV_MODEL_DIAGNOSTIC_NAMES and not _is_packet_hmm_diagnostic_shape(diagnostics):
        raise ValueError("model diagnostics must be empty or use one complete registered counter namespace")


def validate_model_diagnostics_for_family(family: FamilyName, diagnostics: ModelDiagnostics) -> None:
    """Require the one diagnostics namespace owned by a candidate family."""
    valid = (
        frozenset(diagnostics) == _MARKOV_MODEL_DIAGNOSTIC_NAMES
        if family in {"markov_renewal", "markov_packet_train"}
        else _is_packet_hmm_diagnostic_shape(diagnostics)
        if family == "packet_hmm"
        else not diagnostics
    )
    if not valid:
        raise ValueError(f"model diagnostics for family {family} do not match its canonical counter namespace")


@total_ordering
class CandidateId(_StrictGeneticModel):
    """Stable lexical identity assigned at candidate creation."""

    birth_generation: NonnegativeInt
    birth_index: NonnegativeInt

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CandidateId):
            return NotImplemented
        return (self.birth_generation, self.birth_index) < (other.birth_generation, other.birth_index)

    def __hash__(self) -> int:
        return hash((self.birth_generation, self.birth_index))


def validate_candidate_id(identifier: CandidateId) -> CandidateId:
    """Return one exact, nonnegative candidate identity."""
    if type(identifier) is not CandidateId:
        raise TypeError("candidate identifier must be a CandidateId")
    if type(identifier.birth_generation) is not int or type(identifier.birth_index) is not int:
        raise TypeError("candidate identifier components must be exact integers")
    if identifier.birth_generation < 0 or identifier.birth_index < 0:
        raise ValueError("candidate identifier components must be nonnegative")
    return identifier


class MethodTrialResult(_StrictGeneticModel):
    """One named similarity result retained with recursively frozen diagnostics."""

    name: MethodName
    score: UnitFloat
    diagnostics: Mapping[str, JsonValue]

    @field_validator("diagnostics", mode="before")
    @classmethod
    def diagnostics_are_finite_json(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("method diagnostics must be an object")
        frozen = SimilarityResult(0.0, cast(Mapping[str, object], value)).diagnostics
        return _thaw_json(cast(FrozenJsonValue, frozen))

    @model_validator(mode="after")
    def freeze_diagnostics(self) -> Self:
        frozen = SimilarityResult(0.0, cast(Mapping[str, object], self.diagnostics)).diagnostics
        object.__setattr__(self, "diagnostics", frozen)
        return self

    @field_serializer("diagnostics")
    def serialize_diagnostics(self, value: Mapping[str, JsonValue]) -> object:
        return _thaw_json(cast(FrozenJsonValue, value))


class TrialResult(_StrictGeneticModel):
    """All eight scores from one deterministic generated-trace seed."""

    seed: NonnegativeInt
    aggregate_score: UnitFloat
    methods: Annotated[
        tuple[
            MethodTrialResult,
            MethodTrialResult,
            MethodTrialResult,
            MethodTrialResult,
            MethodTrialResult,
            MethodTrialResult,
            MethodTrialResult,
            MethodTrialResult,
        ],
        BeforeValidator(_tuple_input),
    ]
    model_diagnostics: ModelDiagnostics = Field(default_factory=_empty_model_diagnostics)

    @field_validator("model_diagnostics", mode="before")
    @classmethod
    def model_diagnostics_are_rebuilt_from_primitives(cls, value: object) -> object:
        try:
            return dict(freeze_model_diagnostics(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f"candidate trial model diagnostics are invalid: {error}") from error

    @model_validator(mode="after")
    def validate_trial(self) -> Self:
        if len(self.methods) != len(METHOD_ORDER):
            raise ValueError("trial methods must contain every published method in published order")
        if tuple(method.name for method in self.methods) != METHOD_ORDER:
            raise ValueError("trial methods must contain every published method in published order")
        diagnostics = freeze_model_diagnostics(self.model_diagnostics)
        _validate_model_diagnostic_shape(diagnostics)
        object.__setattr__(self, "model_diagnostics", diagnostics)
        return self

    @field_serializer("model_diagnostics")
    def serialize_model_diagnostics(self, value: ModelDiagnostics) -> object:
        return dict(value)


class CandidateFailure(_StrictGeneticModel):
    """A classified mathematical evaluation failure with optional trial seed."""

    kind: CandidateFailureKind
    seed: NonnegativeInt | None
    detail: NonemptyString
    stage: NonemptyString
    affected_evidence: NonemptyString
    evidence_state: EvidenceState
    corrective_action: NonemptyString
    authority: FailureAuthority

    @model_validator(mode="after")
    def values_are_recognized_and_nonblank(self) -> Self:
        for name in ("detail", "stage", "affected_evidence", "corrective_action"):
            value = getattr(self, name)
            if not value.strip():
                raise ValueError(f"candidate failure {name} must be a nonempty string")
        return self


class DuplicateDiagnostic(_StrictGeneticModel):
    """One bounded duplicate-retry decision."""

    attempt: NonnegativeInt
    outcome: DuplicateOutcome
    detail: NonemptyString


class Candidate(_StrictGeneticModel):
    """One immutable population member and its evaluated or invalid state."""

    identifier: CandidateId
    family: FamilyName
    genes: Annotated[Genes, BeforeValidator(_tuple_input)] | None
    status: CandidateStatus
    fitness: UnitFloat
    trials: Annotated[tuple[TrialResult, ...], BeforeValidator(_tuple_input)]
    invalid: CandidateFailure | None
    duplicate_diagnostics: Annotated[tuple[DuplicateDiagnostic, ...], BeforeValidator(_tuple_input)]

    @field_validator("genes", mode="before")
    @classmethod
    def genes_are_exact_finite_numbers(cls, value: object) -> object:
        if value is None:
            return None
        values = _tuple_input(value)
        if type(values) is not tuple:
            raise ValueError("candidate genes must be a tuple or array")
        genes = cast(tuple[object, ...], values)
        if any(type(gene) not in (int, float) or not math.isfinite(cast(float, gene)) for gene in genes):
            raise ValueError("candidate genes must contain exact finite integers or floats")
        return genes

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        validate_candidate_id(self.identifier)
        for trial in self.trials:
            validate_model_diagnostics_for_family(self.family, trial.model_diagnostics)
        return self


class HistoryRow(_StrictGeneticModel):
    """A serializable family or overall generation summary."""

    generation: NonnegativeInt
    scope: Literal["family", "overall"]
    family: FamilyName | None
    candidate_count: NonnegativeInt
    valid_count: NonnegativeInt
    best_fitness: UnitFloat
    mean_fitness: UnitFloat
    best_identifier: CandidateId

    @model_validator(mode="after")
    def validate_history_row(self) -> Self:
        if (self.scope == "family") != (self.family is not None):
            raise ValueError("family history rows require a family and overall rows require none")
        if self.valid_count > self.candidate_count:
            raise ValueError("history valid count must not exceed candidate count")
        validate_candidate_id(self.best_identifier)
        return self


def _thaw_json(value: FrozenJsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return value


def rebuild_genetic_record[Record: BaseModel](record: Record, **changes: object) -> Record:
    """Reconstruct and fully revalidate one immutable genetic record."""
    values = record.model_dump(mode="python")
    values.update(changes)
    return type(record).model_validate(values)
