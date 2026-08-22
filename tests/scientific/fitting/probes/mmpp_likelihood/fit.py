"""Fit owner for Validation Study tooling."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from scipy import optimize as scipy_optimize  # pyright: ignore[reportMissingTypeStubs]

from tests.scientific.fitting.probes.mmpp_likelihood.likelihood import (
    build_observations,
    decode_rates,
    likelihood_evaluation_count,
    mmpp_log_likelihood,
    simulation_evaluation_count,
)
from tests.scientific.fitting.probes.mmpp_likelihood.schema import (
    AGGREGATE_GATE_NAMES,
    GENERATION_LIMITS,
    HELD_OUT_WINDOW_SECONDS,
    INVALID_OBJECTIVE,
    LIKELIHOOD_ATOL,
    LIKELIHOOD_GENERATIONS,
    LIKELIHOOD_POLISH,
    LIKELIHOOD_TOL,
    LIKELIHOOD_UPDATING,
    LIKELIHOOD_WORKERS,
    MARKS,
    OPTIMIZER_STARTS,
    PRODUCTION_BOUNDS,
    PRODUCTION_DUPLICATE_MUTATION_ATTEMPTS,
    PRODUCTION_ELITE_COUNT,
    PRODUCTION_GENERATIONS,
    PRODUCTION_POPULATION_SIZE,
    PRODUCTION_TOURNAMENT_SIZE,
    SIMILARITY,
    TRAINING_WINDOW_SECONDS,
    Coordinates,
    LikelihoodEvaluation,
    LikelihoodFit,
    Rates,
    SimulationCandidateHistory,
    SimulationFit,
    SimulationGenerationHistory,
)
from trafficlab.common.config import (
    FamilyName,
)
from trafficlab.common.trace import TrafficTrace
from trafficlab.fitting.genetic.evaluation import (
    EvaluationContext,
    ValidatedEvaluationContext,
    evaluate_candidate,
    validate_evaluation_context,
)
from trafficlab.fitting.genetic.operators import ReproductionContext, fill_next_population
from trafficlab.fitting.genetic.population import rank_candidates
from trafficlab.fitting.genetic.types import Candidate, CandidateId, FamilyPriority
from trafficlab.generation.models.common import FamilyBounds, make_rng
from trafficlab.generation.models.mmpp import MmppModel
from trafficlab.generation.models.registry import MMPP_FAMILY

if TYPE_CHECKING:
    from tests.scientific.fitting.probes.mmpp_likelihood.schema import (
        DecisionRecord,
        LikelihoodHistoryRecord,
        LikelihoodOptimizerPolicy,
        ProbeRateBounds,
        SeedLimitRecord,
        SimulationGenerationRecord,
        SimulationOptimizerPolicy,
        TrialPlan,
    )


class _DifferentialEvolutionResult(Protocol):
    x: NDArray[np.float64]
    fun: float
    nfev: int
    message: str


class _DifferentialEvolution(Protocol):
    def __call__(
        self,
        objective: object,
        bounds: tuple[tuple[float, float], ...],
        *,
        maxiter: int,
        tol: float,
        atol: float,
        init: NDArray[np.float64],
        polish: bool,
        updating: str,
        workers: int,
        rng: np.random.Generator,
    ) -> _DifferentialEvolutionResult: ...


differential_evolution = cast(_DifferentialEvolution, cast(Any, scipy_optimize).differential_evolution)


def fit_mmpp_likelihood(
    iats: Iterable[float], terminal_silence: float, bounds: ProbeRateBounds, *, seed: int
) -> LikelihoodFit:
    """Fit rates with deterministic PCG64 differential evolution and a hard budget."""
    intervals, terminal = build_observations(iats, terminal_silence)
    if type(seed) is not int or seed < 0:
        raise ValueError("optimizer seed must be a nonnegative exact integer")
    history: list[LikelihoodEvaluation] = []

    def objective(raw: NDArray[np.float64]) -> float:
        coordinates = cast(Coordinates, tuple(float(item) for item in raw))
        evaluation_index = len(history) + 1
        rates: Rates | None = None
        try:
            rates = decode_rates(coordinates, bounds)
            log_likelihood = mmpp_log_likelihood(intervals, terminal, rates)
        except (ArithmeticError, ValueError) as error:
            history.append(
                LikelihoodEvaluation(
                    evaluation_index=evaluation_index,
                    coordinates=coordinates,
                    rates=rates,
                    objective=INVALID_OBJECTIVE,
                    log_likelihood=None,
                    status="invalid",
                    failure=f"{type(error).__name__}: {error}",
                )
            )
            return INVALID_OBJECTIVE
        objective_value = -log_likelihood
        history.append(
            LikelihoodEvaluation(
                evaluation_index=evaluation_index,
                coordinates=coordinates,
                rates=rates,
                objective=objective_value,
                log_likelihood=log_likelihood,
                status="valid",
                failure=None,
            )
        )
        return objective_value

    result = differential_evolution(
        objective,
        bounds=((0.0, 1.0),) * 4,
        maxiter=LIKELIHOOD_GENERATIONS,
        tol=LIKELIHOOD_TOL,
        atol=LIKELIHOOD_ATOL,
        init=np.asarray(OPTIMIZER_STARTS, dtype=np.float64),
        polish=LIKELIHOOD_POLISH,
        updating=LIKELIHOOD_UPDATING,
        workers=LIKELIHOOD_WORKERS,
        rng=np.random.Generator(np.random.PCG64(seed)),
    )
    retained_history = tuple(history)
    evaluations = likelihood_evaluation_count(retained_history)
    if int(result.nfev) != evaluations:
        raise AssertionError("SciPy evaluation count does not match the retained likelihood history")
    coordinates = cast(Coordinates, tuple(float(item) for item in result.x))
    starts = tuple(cast(Rates, item.rates) for item in retained_history[: len(OPTIMIZER_STARTS)])
    return LikelihoodFit(
        rates=decode_rates(coordinates, bounds),
        log_likelihood=-float(result.fun),
        evaluations=evaluations,
        termination=str(result.message),
        starts=starts,
        history=retained_history,
    )


def generate_trace(rates: Rates, *, seed: int, window: float) -> TrafficTrace:
    model = MmppModel(*rates, marks=MARKS)
    result = MMPP_FAMILY.generate(model, seed, window, GENERATION_LIMITS, clock=lambda: 0.0)
    return result.require_complete()


def _evaluate_population(
    candidates: Sequence[Candidate], context: ValidatedEvaluationContext, *, next_evaluation_index: int
) -> tuple[tuple[Candidate, ...], tuple[int | None, ...]]:
    evaluation_index = next_evaluation_index
    output: list[Candidate] = []
    events: list[int | None] = []
    for candidate in candidates:
        if candidate.status == "pending":
            candidate = evaluate_candidate(candidate, context)
            events.append(evaluation_index)
            evaluation_index += 1
        else:
            events.append(None)
        output.append(candidate)
    return (tuple(output), tuple(events))


def _production_initial_population(reference: TrafficTrace, starts: Sequence[Rates]) -> tuple[Candidate, ...]:
    return tuple(
        (
            Candidate(
                identifier=CandidateId(birth_generation=0, birth_index=index),
                family="mmpp",
                genes=cast(Rates, MMPP_FAMILY.repair(rates, PRODUCTION_BOUNDS, reference)),
                status="pending",
                fitness=0.0,
                trials=(),
                invalid=None,
                duplicate_diagnostics=(),
            )
            for index, rates in enumerate(starts)
        )
    )


def _simulation_generation_history(
    generation: int, candidates: Sequence[Candidate], evaluation_indexes: Sequence[int | None]
) -> SimulationGenerationHistory:
    if len(candidates) != len(evaluation_indexes):
        raise ValueError("simulation candidates and evaluation events must have equal length")
    records: list[SimulationCandidateHistory] = []
    for candidate, evaluation_index in zip(candidates, evaluation_indexes, strict=True):
        failure = (
            None if candidate.invalid is None else cast(dict[str, object], candidate.invalid.model_dump(mode="json"))
        )
        records.append(
            SimulationCandidateHistory(
                generation=generation,
                candidate_id=(candidate.identifier.birth_generation, candidate.identifier.birth_index),
                genes=None if candidate.genes is None else cast(Rates, candidate.genes),
                status=candidate.status,
                fitness=candidate.fitness,
                failure=failure,
                evaluation_index=evaluation_index,
            )
        )
    return SimulationGenerationHistory(generation=generation, candidates=tuple(records))


def simulation_distance_fit(
    reference: TrafficTrace, *, window: float, seed: int, trial_seeds: tuple[int, ...], starts: tuple[Rates, ...]
) -> SimulationFit:
    bounds: dict[FamilyName, FamilyBounds] = {"mmpp": PRODUCTION_BOUNDS}
    priority: FamilyPriority = ("mmpp",)
    rng = make_rng(seed)
    context = validate_evaluation_context(
        EvaluationContext(
            reference=reference,
            window=window,
            families={"mmpp": MMPP_FAMILY},
            bounds=bounds,
            trial_seeds=trial_seeds,
            trial_limits=GENERATION_LIMITS,
            similarity=SIMILARITY,
        )
    )
    population = _production_initial_population(reference, starts)
    retained_starts = tuple(cast(Rates, candidate.genes) for candidate in population)
    population, evaluation_indexes = _evaluate_population(population, context, next_evaluation_index=1)
    history = [_simulation_generation_history(0, population, evaluation_indexes)]
    for generation in range(1, PRODUCTION_GENERATIONS + 1):
        population = fill_next_population(
            population,
            generation=generation,
            population_size=PRODUCTION_POPULATION_SIZE,
            elite_count=PRODUCTION_ELITE_COUNT,
            tournament_size=PRODUCTION_TOURNAMENT_SIZE,
            context=ReproductionContext(
                reference=reference,
                family_bounds=bounds,
                family_priority=priority,
                duplicate_mutation_attempts=PRODUCTION_DUPLICATE_MUTATION_ATTEMPTS,
            ),
            rng=rng,
        )
        next_index = simulation_evaluation_count(history) + 1
        population, evaluation_indexes = _evaluate_population(population, context, next_evaluation_index=next_index)
        history.append(_simulation_generation_history(generation, population, evaluation_indexes))
    retained_history = tuple(history)
    evaluations = simulation_evaluation_count(retained_history)
    winner = rank_candidates(population, family_priority=priority)[0]
    if winner.status != "valid" or winner.genes is None:
        raise AssertionError("bounded production MMPP search produced no valid winner")
    return SimulationFit(
        rates=cast(Rates, winner.genes),
        fitness=winner.fitness,
        evaluations=evaluations,
        termination="hard_limit",
        starts=retained_starts,
        history=retained_history,
    )


def trace_observations(trace: TrafficTrace, window: float) -> tuple[tuple[float, ...], float]:
    iats = tuple(float(value) for value in trace.iats())
    terminal = window - float(trace.timestamps[-1])
    if terminal < 0.0 and math.isclose(terminal, 0.0, rel_tol=0.0, abs_tol=1e-12):
        terminal = 0.0
    return build_observations(iats, terminal)


def rates_list(rates: Rates) -> list[float]:
    return list(rates)


def likelihood_history_records(history: Sequence[LikelihoodEvaluation]) -> list[LikelihoodHistoryRecord]:
    return [
        {
            "evaluation_index": item.evaluation_index,
            "coordinates": list(item.coordinates),
            "rates": None if item.rates is None else rates_list(item.rates),
            "objective": item.objective,
            "log_likelihood": item.log_likelihood,
            "status": item.status,
            "failure": item.failure,
        }
        for item in history
    ]


def simulation_history_records(history: Sequence[SimulationGenerationHistory]) -> list[SimulationGenerationRecord]:
    return [
        {
            "generation": generation.generation,
            "candidates": [
                {
                    "generation": candidate.generation,
                    "candidate_id": list(candidate.candidate_id),
                    "genes": None if candidate.genes is None else rates_list(candidate.genes),
                    "status": candidate.status,
                    "fitness": candidate.fitness,
                    "failure": candidate.failure,
                    "evaluation_index": candidate.evaluation_index,
                }
                for candidate in generation.candidates
            ],
        }
        for generation in history
    ]


def seed_limit_record(plan: TrialPlan) -> SeedLimitRecord:
    return {
        "training_data_seed": plan.training_data_seed,
        "likelihood_search_seed": plan.likelihood_search_seed,
        "production_search_seed": plan.production_search_seed,
        "production_selection_trial_seeds": list(plan.production_selection_trial_seeds),
        "production_final_seed": None,
        "held_out_data_seed": plan.held_out_data_seed,
        "training_observation_window_seconds": TRAINING_WINDOW_SECONDS,
        "held_out_observation_window_seconds": HELD_OUT_WINDOW_SECONDS,
        "generation_limits": {
            "max_packets": GENERATION_LIMITS.max_packets,
            "max_output_bytes": GENERATION_LIMITS.max_output_bytes,
            "max_wall_seconds": GENERATION_LIMITS.max_wall_seconds,
        },
    }


def likelihood_optimizer_policy() -> LikelihoodOptimizerPolicy:
    return {
        "method": "scipy.optimize.differential_evolution",
        "population_size": len(OPTIMIZER_STARTS),
        "generations": LIKELIHOOD_GENERATIONS,
        "tol": LIKELIHOOD_TOL,
        "atol": LIKELIHOOD_ATOL,
        "polish": LIKELIHOOD_POLISH,
        "updating": LIKELIHOOD_UPDATING,
        "workers": LIKELIHOOD_WORKERS,
    }


def simulation_optimizer_policy() -> SimulationOptimizerPolicy:
    return {
        "method": "trafficlab production genetic operators and similarity",
        "population_size": PRODUCTION_POPULATION_SIZE,
        "generations": PRODUCTION_GENERATIONS,
        "elite_count": PRODUCTION_ELITE_COUNT,
        "tournament_size": PRODUCTION_TOURNAMENT_SIZE,
        "duplicate_mutation_attempts": PRODUCTION_DUPLICATE_MUTATION_ATTEMPTS,
        "crossover_probability": PRODUCTION_BOUNDS.crossover_probability,
        "mutation_probability": PRODUCTION_BOUNDS.mutation_probability,
        "mutation_scale": PRODUCTION_BOUNDS.mutation_scale,
    }


def decide_probe(gates: Mapping[str, bool]) -> DecisionRecord:
    """Pass only an exact complete mapping of mandated true aggregate gates."""
    failed = [name for name in AGGREGATE_GATE_NAMES if type(gates.get(name)) is not bool or not gates[name]]
    failed.extend(f"unknown:{name}" for name in sorted(set(gates) - set(AGGREGATE_GATE_NAMES)))
    return {"outcome": "pass" if not failed else "reject", "failed_gates": failed, "production_changed": False}
