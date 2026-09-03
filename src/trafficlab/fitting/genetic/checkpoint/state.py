"""Semantic checkpoint candidate, history, and progress invariants."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from trafficlab.common.config import FamilyName, SimilarityConfig
from trafficlab.comparison.diagnostics import FITNESS_METHOD_NAMES
from trafficlab.fitting.genetic.checkpoint.compatibility import (
    invalid_checkpoint,
    parse_float,
    parse_integer,
    validate_compatibility_shape,
    validate_rng_state,
)
from trafficlab.fitting.genetic.checkpoint.schema import CheckpointState, FamilyCheckpointSpec
from trafficlab.fitting.genetic.coordinates import GeneCoordinate
from trafficlab.fitting.genetic.population import priority_rank_key, rank_candidates, validate_family_priority
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateId,
    FamilyPriority,
    HistoryRow,
    MethodName,
    MethodTrialResult,
    TerminalReason,
    rebuild_genetic_record,
)

_DUPLICATE_OUTCOMES = frozenset(("invalid", "duplicate", "exhausted"))
_TERMINAL_REASONS = frozenset(("running", "hard_limit", "early_stop"))


def _mean_fitness_is_feasible(
    mean_fitness: float,
    *,
    candidate_count: int,
    valid_count: int,
    best_fitness: float,
) -> bool:
    mean_numerator, mean_denominator = mean_fitness.as_integer_ratio()
    best_numerator, best_denominator = best_fitness.as_integer_ratio()
    return mean_numerator * candidate_count * best_denominator <= best_numerator * valid_count * mean_denominator


def canonical_mean_fitness(
    values: Iterable[float],
    *,
    candidate_count: int,
    valid_count: int,
    best_fitness: float,
) -> float:
    """Round a derived mean without crossing its exact valid-count ceiling."""
    mean_fitness = math.fsum(values) / float(candidate_count)
    if _mean_fitness_is_feasible(
        mean_fitness,
        candidate_count=candidate_count,
        valid_count=valid_count,
        best_fitness=best_fitness,
    ):
        return mean_fitness
    adjusted = math.nextafter(mean_fitness, 0.0)
    if not _mean_fitness_is_feasible(
        adjusted,
        candidate_count=candidate_count,
        valid_count=valid_count,
        best_fitness=best_fitness,
    ):
        raise ValueError("computed history mean_fitness exceeds its valid-count ceiling")
    return adjusted


def _parse_gene(value: object, coordinate: GeneCoordinate, *, family: FamilyName) -> float | int:
    if coordinate.kind == "integer":
        gene = parse_integer(value, name=f"{coordinate.name} gene for family {family}", minimum=-(2**63))
    else:
        gene = parse_float(value, name=f"{coordinate.name} gene for family {family}")
    if not coordinate.bounds.lower <= gene <= coordinate.bounds.upper:
        raise ValueError(f"{coordinate.name} gene for family {family} is outside its coordinate bounds")
    return gene


def _method_weights(similarity: SimilarityConfig) -> dict[MethodName, float]:
    weights = similarity.method_weights
    return {name: getattr(weights, name) for name in FITNESS_METHOD_NAMES}


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
    parse_integer(generation, name="history generation")
    if not population:
        raise invalid_checkpoint("cannot summarize an empty population")
    family_names = tuple(families)
    if family_names != tuple(sorted(family_names)) or len(family_names) != len(set(family_names)):
        raise invalid_checkpoint("history families must be unique and lexical")
    try:
        priority = validate_family_priority(family_priority, enabled_families=family_names)
    except (TypeError, ValueError) as error:
        raise invalid_checkpoint(str(error)) from error

    def make_row(candidates: tuple[Candidate, ...], family: FamilyName | None) -> HistoryRow:
        if not candidates:
            raise invalid_checkpoint(f"history family {family} has no candidate")
        best = (
            rank_candidates(candidates, family_priority=priority)[0]
            if family is None
            else rank_candidates(candidates, family_priority=(family,))[0]
        )
        valid_count = sum(candidate.status == "valid" for candidate in candidates)
        return HistoryRow(
            generation=generation,
            scope="overall" if family is None else "family",
            family=family,
            candidate_count=len(candidates),
            valid_count=valid_count,
            best_fitness=best.fitness,
            mean_fitness=canonical_mean_fitness(
                (candidate.fitness for candidate in candidates),
                candidate_count=len(candidates),
                valid_count=valid_count,
                best_fitness=best.fitness,
            ),
            best_identifier=best.identifier,
        )

    complete = tuple(population)
    rows = [
        make_row(tuple(candidate for candidate in complete if candidate.family == family), family)
        for family in family_names
    ]
    overall = make_row(complete, None)
    grouped_mean = canonical_mean_fitness(
        (row.mean_fitness * row.candidate_count for row in rows),
        candidate_count=len(complete),
        valid_count=overall.valid_count,
        best_fitness=overall.best_fitness,
    )
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
            candidate_count = parse_integer(row.candidate_count, name="history candidate_count", minimum=1)
            valid_count = parse_integer(row.valid_count, name="history valid_count")
            best_fitness = parse_float(row.best_fitness, name="history best_fitness", bounded=True)
            mean_fitness = parse_float(row.mean_fitness, name="history mean_fitness", bounded=True)
            if valid_count > candidate_count:
                raise ValueError("history valid_count must not exceed candidate_count")
            if valid_count == 0 and (best_fitness != 0.0 or mean_fitness != 0.0):
                raise ValueError("history row with zero valid_count must have zero best_fitness and mean_fitness")
            if not _mean_fitness_is_feasible(
                mean_fitness,
                candidate_count=candidate_count,
                valid_count=valid_count,
                best_fitness=best_fitness,
            ):
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
        expected_mean = canonical_mean_fitness(
            (row.mean_fitness * row.candidate_count for row in family_rows),
            candidate_count=overall.candidate_count,
            valid_count=overall.valid_count,
            best_fitness=overall.best_fitness,
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


def validate_state(state: CheckpointState) -> None:
    if type(state) is not CheckpointState:
        raise TypeError("checkpoint state must be CheckpointState")
    validate_compatibility_shape(state.compatibility)
    generation = parse_integer(state.generation, name="generation")
    if generation > state.compatibility.genetic.generation_count:
        raise ValueError("generation exceeds configured generation_count")
    validate_rng_state(state.rng_state)
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
    parse_float(state.best_fitness, name="best fitness", bounded=True)
    retained_identifier, retained_fitness, expected_stagnation = _history_progress(
        state,
        block_size=len(family_names) + 1,
        family_priority=priority,
    )
    if (state.best_fitness, state.best_identifier) != (retained_fitness, retained_identifier):
        raise ValueError("best does not equal the retained history winner")
    stagnation = parse_integer(state.consecutive_stagnation, name="consecutive_stagnation")
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
