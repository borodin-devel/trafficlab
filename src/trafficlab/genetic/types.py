"""Immutable value contracts for genetic fitting."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from trafficlab.config import FamilyName
from trafficlab.errors import EvidenceState, FailureAuthority
from trafficlab.models.common import (
    MARKOV_MODEL_DIAGNOSTIC_KEYS,
    Genes,
    ModelDiagnostics,
    freeze_model_diagnostics,
)
from trafficlab.similarity.common import JsonDiagnostics, SimilarityResult

type CandidateStatus = Literal["pending", "valid", "invalid"]
type FamilyPriority = tuple[str, ...]
type MethodName = Literal["autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate"]
type CandidateFailureKind = Literal[
    "repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score"
]
type DuplicateOutcome = Literal["invalid", "duplicate", "exhausted"]
type TerminalReason = Literal["running", "hard_limit", "early_stop"]

METHOD_ORDER: tuple[MethodName, ...] = ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
_METHOD_NAMES = frozenset(METHOD_ORDER)
_FAILURE_KINDS = frozenset(
    ("repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score")
)
_EVIDENCE_STATES = frozenset(("not_published", "diagnostic_only", "preserved", "possibly_remaining"))
_FAILURE_AUTHORITIES = frozenset(("primary", "secondary"))
_DUPLICATE_OUTCOMES = frozenset(("invalid", "duplicate", "exhausted"))
_CANDIDATE_STATUSES = frozenset(("pending", "valid", "invalid"))
_FAMILY_NAMES = frozenset(("poisson_empirical", "markov_renewal", "mmpp"))
_MARKOV_MODEL_DIAGNOSTIC_NAMES = frozenset(MARKOV_MODEL_DIAGNOSTIC_KEYS)


def _empty_model_diagnostics() -> dict[str, int]:
    return {}


def _validate_model_diagnostic_shape(diagnostics: ModelDiagnostics) -> None:
    names = frozenset(diagnostics)
    if names and names != _MARKOV_MODEL_DIAGNOSTIC_NAMES:
        raise ValueError("model diagnostics must be empty or contain exactly the four canonical Markov counters")


def validate_model_diagnostics_for_family(family: FamilyName, diagnostics: ModelDiagnostics) -> None:
    """Require the one diagnostics namespace owned by a candidate family."""
    expected: frozenset[str] = _MARKOV_MODEL_DIAGNOSTIC_NAMES if family == "markov_renewal" else frozenset()
    if frozenset(diagnostics) != expected:
        raise ValueError(f"model diagnostics for family {family} do not match its canonical counter namespace")


@dataclass(frozen=True, order=True, slots=True)
class CandidateId:
    """Stable lexical identity assigned at candidate creation."""

    birth_generation: int
    birth_index: int


def validate_candidate_id(identifier: CandidateId) -> CandidateId:
    """Return one exact, nonnegative candidate identity."""
    if type(identifier) is not CandidateId:
        raise TypeError("candidate identifier must be a CandidateId")
    if type(identifier.birth_generation) is not int or type(identifier.birth_index) is not int:
        raise TypeError("candidate identifier components must be exact integers")
    if identifier.birth_generation < 0 or identifier.birth_index < 0:
        raise ValueError("candidate identifier components must be nonnegative")
    return identifier


def _finite_score(value: object, *, name: str) -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a finite float in [0, 1]")
    return value


@dataclass(frozen=True, slots=True, init=False)
class MethodTrialResult:
    """One named similarity result retained with recursively frozen diagnostics."""

    name: MethodName
    score: float
    diagnostics: JsonDiagnostics

    def __init__(self, name: MethodName, score: float, diagnostics: Mapping[str, object]) -> None:
        if name not in _METHOD_NAMES:
            raise ValueError("method name must be one of the published similarity methods")
        frozen = SimilarityResult(0.0, diagnostics).diagnostics
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "score", _finite_score(score, name="method score"))
        object.__setattr__(self, "diagnostics", frozen)


@dataclass(frozen=True, slots=True)
class TrialResult:
    """All four scores from one deterministic generated-trace seed."""

    seed: int
    aggregate_score: float
    methods: tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult]
    model_diagnostics: ModelDiagnostics = field(default_factory=_empty_model_diagnostics)

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("trial seed must be a nonnegative exact integer")
        _finite_score(self.aggregate_score, name="aggregate score")
        if type(self.methods) is not tuple or len(self.methods) != len(METHOD_ORDER):
            raise ValueError("trial methods must contain every published method in published order")
        if any(type(method) is not MethodTrialResult for method in self.methods):
            raise TypeError("trial methods must be MethodTrialResult values")
        if tuple(method.name for method in self.methods) != METHOD_ORDER:
            raise ValueError("trial methods must contain every published method in published order")
        diagnostics = freeze_model_diagnostics(self.model_diagnostics)
        _validate_model_diagnostic_shape(diagnostics)
        object.__setattr__(self, "model_diagnostics", diagnostics)


@dataclass(frozen=True, slots=True)
class CandidateFailure:
    """A classified mathematical evaluation failure with optional trial seed."""

    kind: CandidateFailureKind
    seed: int | None
    detail: str
    stage: str = field(kw_only=True)
    affected_evidence: str = field(kw_only=True)
    evidence_state: EvidenceState = field(kw_only=True)
    corrective_action: str = field(kw_only=True)
    authority: FailureAuthority = field(kw_only=True)

    def __post_init__(self) -> None:
        if self.kind not in _FAILURE_KINDS:
            raise ValueError("candidate failure kind is not recognized")
        if self.seed is not None and (type(self.seed) is not int or self.seed < 0):
            raise ValueError("candidate failure seed must be a nonnegative exact integer or None")
        for name in ("detail", "stage", "affected_evidence", "corrective_action"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"candidate failure {name} must be a nonempty string")
        if self.evidence_state not in _EVIDENCE_STATES:
            raise ValueError("candidate failure evidence state is not recognized")
        if self.authority not in _FAILURE_AUTHORITIES:
            raise ValueError("candidate failure authority is not recognized")


@dataclass(frozen=True, slots=True)
class DuplicateDiagnostic:
    """One bounded duplicate-retry decision."""

    attempt: int
    outcome: DuplicateOutcome
    detail: str

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("duplicate attempt must be a nonnegative exact integer")
        if self.outcome not in _DUPLICATE_OUTCOMES:
            raise ValueError("duplicate outcome is not recognized")
        if type(self.detail) is not str or not self.detail:
            raise ValueError("duplicate detail must be a nonempty string")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One immutable population member and its evaluated or invalid state."""

    identifier: CandidateId
    family: FamilyName
    genes: Genes | None
    status: CandidateStatus
    fitness: float
    trials: tuple[TrialResult, ...]
    invalid: CandidateFailure | None
    duplicate_diagnostics: tuple[DuplicateDiagnostic, ...]

    def __post_init__(self) -> None:
        validate_candidate_id(self.identifier)
        if type(self.family) is not str or self.family not in _FAMILY_NAMES:
            raise ValueError("candidate family must be a registered family")
        if self.genes is not None:
            if type(self.genes) is not tuple:
                raise TypeError("candidate genes must be a tuple or None")
            if any(type(gene) not in (int, float) or not math.isfinite(gene) for gene in self.genes):
                raise ValueError("candidate genes must contain exact finite integers or floats")
        if self.status not in _CANDIDATE_STATUSES:
            raise ValueError("candidate status is not recognized")
        _finite_score(self.fitness, name="candidate fitness")
        if type(self.trials) is not tuple or any(type(trial) is not TrialResult for trial in self.trials):
            raise TypeError("candidate trials must be a tuple of TrialResult values")
        for trial in self.trials:
            validate_model_diagnostics_for_family(self.family, trial.model_diagnostics)
        if self.invalid is not None and type(self.invalid) is not CandidateFailure:
            raise TypeError("candidate invalid value must be a CandidateFailure or None")
        if type(self.duplicate_diagnostics) is not tuple or any(
            type(item) is not DuplicateDiagnostic for item in self.duplicate_diagnostics
        ):
            raise TypeError("candidate duplicate diagnostics must be a tuple of DuplicateDiagnostic values")


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """A serializable family or overall generation summary."""

    generation: int
    scope: Literal["family", "overall"]
    family: FamilyName | None
    candidate_count: int
    valid_count: int
    best_fitness: float
    mean_fitness: float
    best_identifier: CandidateId

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("history generation must be a nonnegative exact integer")
        if self.scope not in {"family", "overall"}:
            raise ValueError("history scope must be family or overall")
        if (self.scope == "family") != (self.family is not None):
            raise ValueError("family history rows require a family and overall rows require none")
        if any(type(value) is not int or value < 0 for value in (self.candidate_count, self.valid_count)):
            raise ValueError("history counts must be nonnegative exact integers")
        if self.valid_count > self.candidate_count:
            raise ValueError("history valid count must not exceed candidate count")
        _finite_score(self.best_fitness, name="history best fitness")
        _finite_score(self.mean_fitness, name="history mean fitness")
        validate_candidate_id(self.best_identifier)
