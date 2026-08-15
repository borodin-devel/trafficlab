"""Resumable generational genetic-search lifecycle orchestration."""

from __future__ import annotations

import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from random import Random
from types import MappingProxyType
from typing import Literal, cast

from trafficlab.config import ExperimentConfig, FamilyName, FamilyOperators
from trafficlab.errors import TrafficlabError, attach_failure_outcome
from trafficlab.genetic.checkpoint import (
    RNG_ENGINE,
    CheckpointCompatibility,
    CheckpointCorruptionError,
    CheckpointState,
    FamilyCheckpointSpec,
    GeneticCheckpointSettings,
    decode_rng_state,
    encode_rng_state,
    load_generation,
    publish_generation,
    summarize_generation,
)
from trafficlab.genetic.coordinates import family_coordinates
from trafficlab.genetic.evaluation import (
    EvaluationContext,
    ValidatedEvaluationContext,
    evaluate_candidate,
    evaluate_final,
    validate_evaluation_context,
)
from trafficlab.genetic.operators import ReproductionContext, fill_next_population
from trafficlab.genetic.population import initial_population, rank_candidates
from trafficlab.genetic.types import Candidate, CandidateId, TerminalReason, TrialResult
from trafficlab.models.common import FamilyBounds, ModelFamily
from trafficlab.models.registry import get_family
from trafficlab.trace import TraceEvent


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """All resolved scientific, compatibility, and filesystem inputs for one fit."""

    config: ExperimentConfig
    evaluation: EvaluationContext
    compatibility: CheckpointCompatibility
    run_directory: Path


@dataclass(frozen=True, slots=True)
class FitOutcome:
    """Stored winner plus fresh final evidence, with no fitted Python model state."""

    winner: Candidate
    final_trials: tuple[TrialResult, ...]
    generation: int
    terminal_reason: Literal["hard_limit", "early_stop"]


def _enabled_bounds(config: ExperimentConfig, families: Sequence[FamilyName]) -> dict[FamilyName, FamilyBounds]:
    bounds: dict[FamilyName, FamilyBounds] = {}
    for name in families:
        value = getattr(config.models, name)
        if value is None:
            raise ValueError(f"enabled family {name} has no configured bounds")
        bounds[name] = value
    return bounds


def _family_specs(
    families: Sequence[FamilyName],
    registered: Mapping[FamilyName, ModelFamily],
    bounds: Mapping[FamilyName, FamilyBounds],
) -> tuple[FamilyCheckpointSpec, ...]:
    specs: list[FamilyCheckpointSpec] = []
    for name in families:
        family = registered[name]
        operators = cast(FamilyOperators, bounds[name])
        specs.append(
            FamilyCheckpointSpec(
                name,
                family.gene_names,
                family_coordinates(name, bounds[name]),
                operators.crossover_probability,
                operators.mutation_probability,
                operators.mutation_scale,
            )
        )
    return tuple(specs)


def _genetic_settings(config: ExperimentConfig) -> GeneticCheckpointSettings:
    genetic = config.genetic
    return GeneticCheckpointSettings(
        master_seed=config.run.master_seed,
        final_seed=config.run.final_seed,
        population_size=genetic.population_size,
        generation_count=genetic.generation_count,
        tournament_size=genetic.tournament_size,
        elite_count=genetic.elite_count,
        duplicate_mutation_attempts=genetic.duplicate_mutation_attempts,
        early_stopping_generations=genetic.early_stopping_generations,
        early_stopping_tolerance=genetic.early_stopping_tolerance,
        resume=genetic.resume,
    )


def make_strategy_context(
    config: ExperimentConfig,
    reference: tuple[TraceEvent, ...],
    window: float,
    run_directory: Path,
    *,
    experiment_sha256: str,
    reference_sha256: str,
    capture_sha256: str,
) -> StrategyContext:
    """Resolve lexical registry, bounds, operators, and compatibility exactly once."""
    families = tuple(sorted(config.models.enabled))
    registered: dict[FamilyName, ModelFamily] = {name: get_family(name) for name in families}
    bounds = _enabled_bounds(config, families)
    evaluation = EvaluationContext(
        reference=reference,
        window=window,
        families=MappingProxyType(registered),
        bounds=MappingProxyType(bounds),
        trial_seeds=config.genetic.trial_seeds,
        trial_limits=config.generation.trial,
        similarity=config.similarity,
    )
    compatibility = CheckpointCompatibility(
        experiment_sha256=experiment_sha256,
        reference_sha256=reference_sha256,
        capture_sha256=capture_sha256,
        observation_window_seconds=window,
        trial_seeds=config.genetic.trial_seeds,
        families=_family_specs(families, registered, bounds),
        genetic=_genetic_settings(config),
        similarity=config.similarity,
        python_version=platform.python_version(),
        rng_engine=RNG_ENGINE,
    )
    return StrategyContext(config, evaluation, compatibility, run_directory)


def should_stop_early(consecutive_stagnation: int, *, early_stopping_generations: int) -> bool:
    """Return whether the positive configured stagnation limit has been reached."""
    if type(consecutive_stagnation) is not int or consecutive_stagnation < 0:
        raise ValueError("consecutive stagnation must be a nonnegative exact integer")
    if type(early_stopping_generations) is not int or early_stopping_generations < 0:
        raise ValueError("early stopping generations must be a nonnegative exact integer")
    return early_stopping_generations > 0 and consecutive_stagnation >= early_stopping_generations


def advance_termination_state(
    generation: int,
    *,
    generation_count: int,
    consecutive_stagnation: int,
    early_stopping_generations: int,
) -> TerminalReason:
    """Choose hard termination before early stop at a simultaneous boundary."""
    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a nonnegative exact integer")
    if type(generation_count) is not int or generation_count < 0 or generation > generation_count:
        raise ValueError("generation count must be an exact bound at least as large as generation")
    if generation == generation_count:
        return "hard_limit"
    if should_stop_early(
        consecutive_stagnation,
        early_stopping_generations=early_stopping_generations,
    ):
        return "early_stop"
    return "running"


def initialize_or_resume(context: StrategyContext) -> CheckpointState | None:
    """Load one compatible generation or select a fresh start under the resume policy."""
    checkpoint_path = context.run_directory / "checkpoint.json"
    if not checkpoint_path.exists():
        return None
    if not context.compatibility.genetic.resume:
        raise TrafficlabError(
            f"checkpoint already exists at {checkpoint_path} while resume is disabled",
            corrective_action="enable genetic resume or choose a new run directory",
        )
    try:
        return load_generation(context.run_directory, context.compatibility)
    except TrafficlabError as error:
        if error.failure_outcome is None:
            attach_failure_outcome(
                error,
                kind=(
                    "artifact_corrupt"
                    if isinstance(error, CheckpointCorruptionError)
                    else "scientific_semantics_incompatible"
                ),
                stage="fit",
                affected_evidence="checkpoint.json",
                evidence_state="preserved",
            )
        raise


def _evaluate_population(
    population: Sequence[Candidate],
    evaluation: ValidatedEvaluationContext,
) -> tuple[Candidate, ...]:
    return tuple(
        candidate if candidate.status == "valid" else evaluate_candidate(candidate, evaluation)
        for candidate in population
    )


def _finish_evaluated_generation(
    population: Sequence[Candidate],
    rng: Random,
    *,
    generation: int,
    previous: CheckpointState | None,
    context: StrategyContext,
    evaluation: ValidatedEvaluationContext,
) -> CheckpointState:
    evaluated = _evaluate_population(population, evaluation)
    family_names = cast(tuple[FamilyName, ...], tuple(family.name for family in context.compatibility.families))
    history = (() if previous is None else previous.history) + summarize_generation(
        generation,
        evaluated,
        family_names,
    )
    generation_best = rank_candidates(evaluated)[0]
    if previous is None:
        best_identifier = generation_best.identifier
        best_fitness = generation_best.fitness
        consecutive_stagnation = 0
    else:
        improvement = generation_best.fitness - previous.best_fitness
        if improvement > 0.0:
            best_identifier = generation_best.identifier
            best_fitness = generation_best.fitness
        else:
            best_identifier = previous.best_identifier
            best_fitness = previous.best_fitness
        consecutive_stagnation = (
            0
            if improvement > context.compatibility.genetic.early_stopping_tolerance
            else previous.consecutive_stagnation + 1
        )
    genetic = context.compatibility.genetic
    terminal_reason = advance_termination_state(
        generation,
        generation_count=genetic.generation_count,
        consecutive_stagnation=consecutive_stagnation,
        early_stopping_generations=genetic.early_stopping_generations,
    )
    return CheckpointState(
        compatibility=context.compatibility,
        generation=generation,
        population=evaluated,
        history=history,
        rng_state=encode_rng_state(rng.getstate()),
        best_identifier=best_identifier,
        best_fitness=best_fitness,
        consecutive_stagnation=consecutive_stagnation,
        terminal_reason=terminal_reason,
    )


def _reproduce_then_evaluate(
    state: CheckpointState,
    context: StrategyContext,
    evaluation: ValidatedEvaluationContext,
    rng: Random,
) -> CheckpointState:
    genetic = context.compatibility.genetic
    generation = state.generation + 1
    population = fill_next_population(
        state.population,
        generation=generation,
        population_size=genetic.population_size,
        elite_count=genetic.elite_count,
        tournament_size=genetic.tournament_size,
        context=ReproductionContext(
            reference=context.evaluation.reference,
            family_bounds=context.evaluation.bounds,
            duplicate_mutation_attempts=genetic.duplicate_mutation_attempts,
        ),
        rng=rng,
    )
    return _finish_evaluated_generation(
        population,
        rng,
        generation=generation,
        previous=state,
        context=context,
        evaluation=evaluation,
    )


def _candidate_by_id(population: Sequence[Candidate], identifier: CandidateId) -> Candidate:
    try:
        return next(candidate for candidate in population if candidate.identifier == identifier)
    except StopIteration as error:
        raise AssertionError("validated checkpoint winner must occur in the current population") from error


def run_strategy(context: StrategyContext) -> FitOutcome:
    """Run or exactly resume the bounded GA and freshly validate its stored winner."""
    evaluation = validate_evaluation_context(context.evaluation)
    state = initialize_or_resume(context)
    rng: Random | None = None
    if state is None:
        rng = Random(context.compatibility.genetic.master_seed)
        population = initial_population(
            tuple(family.name for family in context.compatibility.families),
            population_size=context.compatibility.genetic.population_size,
            bounds=context.evaluation.bounds,
            reference=context.evaluation.reference,
            rng=rng,
        )
        state = _finish_evaluated_generation(
            population,
            rng,
            generation=0,
            previous=None,
            context=context,
            evaluation=evaluation,
        )
        publish_generation(context.run_directory, state)
    while state.terminal_reason == "running":
        if rng is None:
            rng = Random()
            rng.setstate(decode_rng_state(state.rng_state))
        state = _reproduce_then_evaluate(state, context, evaluation, rng)
        publish_generation(context.run_directory, state)
    winner = _candidate_by_id(state.population, state.best_identifier)
    final_trials = evaluate_final(winner, evaluation, context.compatibility.genetic.final_seed)
    return FitOutcome(
        winner,
        final_trials,
        state.generation,
        state.terminal_reason,
    )
