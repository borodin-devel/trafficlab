"""Shared-context validation and deterministic genetic candidate evaluation."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import cast

from pydantic import ValidationError

from trafficlab.comparison import ComparisonResult, compare_traces
from trafficlab.config import FamilyName, GenerationLimits, SimilarityConfig
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.coordinates import CandidateEvaluationError
from trafficlab.genetic.types import (
    METHOD_ORDER,
    Candidate,
    CandidateFailure,
    MethodTrialResult,
    TrialResult,
)
from trafficlab.models.common import (
    FamilyBounds,
    FittedModel,
    GenerationResult,
    Genes,
    ModelDiagnostics,
    ModelFamily,
    validate_fit_inputs,
)
from trafficlab.models.registry import REGISTRY
from trafficlab.trace import TraceEvent


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """Immutable common scientific inputs shared by every candidate trial."""

    reference: tuple[TraceEvent, ...]
    window: float
    families: Mapping[FamilyName, ModelFamily]
    bounds: Mapping[FamilyName, FamilyBounds]
    trial_seeds: tuple[int, ...]
    trial_limits: GenerationLimits
    similarity: SimilarityConfig

    def __post_init__(self) -> None:
        object.__setattr__(self, "families", MappingProxyType(dict(self.families)))
        object.__setattr__(self, "bounds", MappingProxyType(dict(self.bounds)))


@dataclass(frozen=True, slots=True)
class ValidatedEvaluationContext(EvaluationContext):
    """An evaluation context whose common checks and real self-comparison passed."""

    @classmethod
    def from_context(cls, context: EvaluationContext) -> ValidatedEvaluationContext:
        """Copy a fully checked raw context into the validated marker type."""
        return cls(
            reference=context.reference,
            window=context.window,
            families=context.families,
            bounds=context.bounds,
            trial_seeds=context.trial_seeds,
            trial_limits=context.trial_limits,
            similarity=context.similarity,
        )


def _validate_pydantic_value(value: object, expected_type: type[GenerationLimits] | type[SimilarityConfig]) -> None:
    if type(value) is not expected_type:
        raise TypeError(f"evaluation context {expected_type.__name__} must be a {expected_type.__name__}")
    try:
        expected_type.model_validate(value.model_dump())
    except ValidationError as error:
        raise ValueError(f"invalid evaluation context {expected_type.__name__}: {error}") from error


def _validate_families_and_bounds(context: EvaluationContext) -> None:
    if not context.families:
        raise ValueError("evaluation context requires at least one enabled family")
    if set(context.bounds) != set(context.families):
        raise ValueError("evaluation bounds must exactly match enabled families")
    for name, family in context.families.items():
        if REGISTRY.get(name) is not family:
            raise TrafficlabError(
                f"evaluation family {name!r} is not the exact registered family",
                corrective_action="use only enabled model families from the built-in registry",
            )
        bounds = context.bounds[name]
        if type(bounds) is not family.bounds_type:
            raise ValueError(f"evaluation bounds for {name} must be {family.bounds_type.__name__}")
        try:
            family.bounds_type.model_validate(bounds.model_dump())
        except ValidationError as error:
            raise ValueError(f"invalid evaluation bounds for {name}: {error}") from error


def _validate_trial_seeds(seeds: object) -> None:
    if type(seeds) is not tuple or not seeds:
        raise ValueError("evaluation trial seeds must be a nonempty tuple")
    seed_values = cast(tuple[object, ...], seeds)
    if any(type(seed) is not int or seed < 0 for seed in seed_values):
        raise ValueError("evaluation trial seeds must be nonnegative exact integers")
    if len(seed_values) != len(set(seed_values)):
        raise ValueError("evaluation trial seeds must be unique")


def validate_evaluation_context(context: EvaluationContext) -> ValidatedEvaluationContext:
    """Validate every shared input and prove all configured metrics on the reference once."""
    # Candidate evaluation is intentionally downstream of this boundary.  By
    # exercising metric preconditions on the reference first, configuration or
    # sample infeasibility cannot be misreported as one candidate's poor fitness.
    if type(context) is not EvaluationContext:
        raise TypeError("context must be an unvalidated EvaluationContext")
    if type(context.reference) is not tuple:
        raise TypeError("evaluation reference must be a tuple")
    validate_fit_inputs(context.reference, W=context.window)
    _validate_families_and_bounds(context)
    _validate_trial_seeds(context.trial_seeds)
    _validate_pydantic_value(context.trial_limits, GenerationLimits)
    _validate_pydantic_value(context.similarity, SimilarityConfig)
    compare_traces(context.reference, context.reference, context.window, context.similarity)
    return ValidatedEvaluationContext.from_context(context)


def _checked_context(context: object) -> ValidatedEvaluationContext:
    if type(context) is not ValidatedEvaluationContext:
        raise TypeError("candidate evaluation requires a ValidatedEvaluationContext")
    return context


def _family_and_bounds(candidate: Candidate, context: ValidatedEvaluationContext) -> tuple[ModelFamily, FamilyBounds]:
    try:
        return context.families[candidate.family], context.bounds[candidate.family]
    except KeyError as error:
        raise ValueError(f"candidate family {candidate.family} is not enabled for evaluation") from error


def _repair_candidate(candidate: Candidate, context: ValidatedEvaluationContext) -> Genes:
    family, bounds = _family_and_bounds(candidate, context)
    try:
        return family.repair(cast(Genes, candidate.genes), bounds, context.reference)
    except TrafficlabError as error:
        raise CandidateEvaluationError(
            "repair",
            None,
            str(error),
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action=error.corrective_action,
            authority="primary",
        ) from error


def _fit_candidate(candidate: Candidate, context: ValidatedEvaluationContext) -> FittedModel:
    family, bounds = _family_and_bounds(candidate, context)
    try:
        return family.fit(context.reference, cast(Genes, candidate.genes), W=context.window, bounds=bounds)
    except TrafficlabError as error:
        raise CandidateEvaluationError(
            "fit",
            None,
            str(error),
            stage="fit",
            affected_evidence="candidate model",
            evidence_state="diagnostic_only",
            corrective_action=error.corrective_action,
            authority="primary",
        ) from error


def _generate_candidate(
    candidate: Candidate,
    model: FittedModel,
    seed: int,
    context: ValidatedEvaluationContext,
) -> GenerationResult:
    family, _ = _family_and_bounds(candidate, context)
    try:
        result = family.generate(model, seed, context.window, context.trial_limits)
    except TrafficlabError as error:
        raise CandidateEvaluationError(
            "generation",
            seed,
            str(error),
            stage="fit",
            affected_evidence="candidate trace",
            evidence_state="diagnostic_only",
            corrective_action=error.corrective_action,
            authority="primary",
        ) from error
    if type(result) is not GenerationResult:
        raise TypeError("registered family generate must return a GenerationResult")
    if not result.complete:
        raise CandidateEvaluationError(
            "incomplete_generation",
            seed,
            cast(str, result.reason),
            stage="fit",
            affected_evidence="candidate trace",
            evidence_state="diagnostic_only",
            corrective_action="increase generation limits or repair the candidate model",
            authority="primary",
        )
    return result


def validate_candidate_similarity_preconditions(
    generated: tuple[TraceEvent, ...],
    similarity: SimilarityConfig,
    *,
    seed: int,
) -> None:
    """Classify only generated-trace sample-count failures for configured methods."""
    failures: list[str] = []
    event_count = len(generated)
    maximum_lag = max(similarity.acf_lags)
    if event_count - 1 <= maximum_lag:
        failures.append(f"autocorrelation requires at least {maximum_lag + 2} generated events for lag {maximum_lag}")
    if event_count < 1:
        failures.append("frame_size_ks requires at least one generated event")
    if event_count < 2:
        failures.append("iat_ks requires at least two generated events")
    if event_count < 1:
        failures.append("multiscale_rate requires at least one generated event")
    if failures:
        raise CandidateEvaluationError(
            "similarity_precondition",
            seed,
            "; ".join(failures),
            stage="fit",
            affected_evidence="candidate similarity",
            evidence_state="diagnostic_only",
            corrective_action="repair the candidate model to generate sufficient comparable events",
            authority="primary",
        )


def _score(value: object, *, name: str, seed: int) -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise CandidateEvaluationError(
            "nonfinite_score",
            seed,
            f"{name} must be a finite float in [0, 1]",
            stage="fit",
            affected_evidence="candidate similarity",
            evidence_state="diagnostic_only",
            corrective_action="repair the candidate model or similarity computation",
            authority="primary",
        )
    return value


def _trial_from_comparison(
    result: ComparisonResult,
    *,
    seed: int,
    model_diagnostics: ModelDiagnostics,
) -> TrialResult:
    aggregate_score = _score(result.aggregate_score, name="aggregate score", seed=seed)
    methods = cast(
        tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult],
        tuple(
            MethodTrialResult(
                name,
                _score(result.methods[name].score, name=f"{name} score", seed=seed),
                result.methods[name].diagnostics,
            )
            for name in METHOD_ORDER
        ),
    )
    return TrialResult(seed, aggregate_score, methods, model_diagnostics)


def _evaluate_trial(
    candidate: Candidate,
    model: FittedModel,
    seed: int,
    context: ValidatedEvaluationContext,
) -> TrialResult:
    generation = _generate_candidate(candidate, model, seed, context)
    generated = generation.events
    validate_candidate_similarity_preconditions(generated, context.similarity, seed=seed)
    try:
        comparison = compare_traces(context.reference, generated, context.window, context.similarity)
    except TrafficlabError as error:
        raise CandidateEvaluationError(
            "similarity_precondition",
            seed,
            str(error),
            stage="fit",
            affected_evidence="candidate similarity",
            evidence_state="diagnostic_only",
            corrective_action=error.corrective_action,
            authority="primary",
        ) from error
    return _trial_from_comparison(comparison, seed=seed, model_diagnostics=generation.model_diagnostics)


def _invalid_candidate(candidate: Candidate, error: CandidateEvaluationError) -> Candidate:
    return replace(
        candidate,
        status="invalid",
        fitness=0.0,
        trials=(),
        invalid=CandidateFailure(
            error.kind,
            error.seed,
            error.detail,
            stage=error.stage,
            affected_evidence=error.affected_evidence,
            evidence_state=error.evidence_state,
            corrective_action=error.corrective_action,
            authority=error.authority,
        ),
    )


def evaluate_candidate(candidate: Candidate, context: ValidatedEvaluationContext) -> Candidate:
    """Fit once and score one pending candidate across every common selection seed."""
    checked_context = _checked_context(context)
    if type(candidate) is not Candidate:
        raise TypeError("candidate must be a Candidate")
    if candidate.status == "invalid":
        return candidate
    if candidate.status != "pending":
        raise ValueError("candidate must be pending before evaluation")
    if candidate.genes is None:
        return _invalid_candidate(
            candidate,
            CandidateEvaluationError(
                "repair",
                None,
                "candidate has no genes",
                stage="fit",
                affected_evidence="candidate genes",
                evidence_state="diagnostic_only",
                corrective_action="provide canonical candidate genes",
                authority="primary",
            ),
        )
    _family_and_bounds(candidate, checked_context)
    repaired_candidate = candidate
    try:
        repaired = _repair_candidate(candidate, checked_context)
        repaired_candidate = replace(candidate, genes=repaired)
        model = _fit_candidate(repaired_candidate, checked_context)
        trials = tuple(
            _evaluate_trial(repaired_candidate, model, seed, checked_context) for seed in checked_context.trial_seeds
        )
    except CandidateEvaluationError as error:
        return _invalid_candidate(repaired_candidate, error)
    fitness = math.fsum(trial.aggregate_score for trial in trials) / len(trials)
    return replace(repaired_candidate, status="valid", fitness=fitness, trials=trials, invalid=None)


def _final_validation_error(error: CandidateEvaluationError) -> TrafficlabError:
    return TrafficlabError(
        f"final validation failed: {error.detail}",
        corrective_action="inspect the selected winner and increase trial generation limits if necessary",
    )


def evaluate_final(
    candidate: Candidate,
    context: ValidatedEvaluationContext,
    final_seed: int,
) -> tuple[TrialResult, ...]:
    """Refit the stored winner once and evaluate only its fresh final seed."""
    checked_context = _checked_context(context)
    if type(final_seed) is not int or final_seed < 0:
        raise ValueError("final seed must be a nonnegative exact integer")
    if final_seed in checked_context.trial_seeds:
        raise ValueError("final seed must be fresh and absent from selection trial seeds")
    if type(candidate) is not Candidate:
        raise TypeError("candidate must be a Candidate")
    if candidate.status != "valid" or candidate.genes is None:
        raise _final_validation_error(
            CandidateEvaluationError(
                "fit",
                None,
                "stored winner is not a valid candidate",
                stage="fit",
                affected_evidence="candidate model",
                evidence_state="diagnostic_only",
                corrective_action="select a valid stored winner",
                authority="primary",
            )
        )
    _family_and_bounds(candidate, checked_context)
    try:
        model = _fit_candidate(candidate, checked_context)
        return (_evaluate_trial(candidate, model, final_seed, checked_context),)
    except CandidateEvaluationError as error:
        raise _final_validation_error(error) from error
