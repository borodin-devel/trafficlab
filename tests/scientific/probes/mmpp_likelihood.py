"""Test-only SciPy likelihood and equal-budget probe for the two-state MMPP."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, cast

import numpy as np
from numpy.typing import NDArray
from scipy import linalg as scipy_linalg  # pyright: ignore[reportMissingTypeStubs]
from scipy import optimize as scipy_optimize  # pyright: ignore[reportMissingTypeStubs]

from trafficlab.config import FamilyName, FloatBounds, GenerationLimits, MethodWeights, MmppConfig, SimilarityConfig
from trafficlab.genetic.evaluation import (
    EvaluationContext,
    ValidatedEvaluationContext,
    evaluate_candidate,
    validate_evaluation_context,
)
from trafficlab.genetic.operators import ReproductionContext, fill_next_population
from trafficlab.genetic.population import rank_candidates
from trafficlab.genetic.types import Candidate, CandidateId, FamilyPriority
from trafficlab.models.common import FamilyBounds, MarkCount, MarkDistribution, make_rng
from trafficlab.models.mmpp import MmppModel
from trafficlab.models.registry import MMPP_FAMILY
from trafficlab.trace import Direction, TraceEvent, TrafficTrace

type Rates = tuple[float, float, float, float]
type Coordinates = tuple[float, float, float, float]

HAND_ABSOLUTE_TOLERANCE = 1e-12
TRAINING_WINDOW_SECONDS = 180.0
HELD_OUT_WINDOW_SECONDS = 120.0
EVALUATION_BUDGET = 120
INVALID_OBJECTIVE = 1e300
LIKELIHOOD_GENERATIONS = 14
LIKELIHOOD_TOL = 0.0
LIKELIHOOD_ATOL = 0.0
LIKELIHOOD_POLISH = False
LIKELIHOOD_UPDATING = "immediate"
LIKELIHOOD_WORKERS = 1
PRODUCTION_GENERATIONS = 16
PRODUCTION_ELITE_COUNT = 1
PRODUCTION_TOURNAMENT_SIZE = 2
PRODUCTION_DUPLICATE_MUTATION_ATTEMPTS = 4
PRODUCTION_CROSSOVER_PROBABILITY = 0.9
PRODUCTION_MUTATION_PROBABILITY = 0.25
PRODUCTION_MUTATION_SCALE = 0.15
RECOVERY_SEEDS = (4101, 4201, 4301)
RECOVERY_TOLERANCES: Rates = (1.0, 1.0, 0.5, 0.35)
TRUE_RATES: Rates = (0.7, 1.9, 1.2, 7.5)

OPTIMIZER_STARTS: tuple[Coordinates, ...] = (
    (0.15, 0.15, 0.15, 0.15),
    (0.85, 0.85, 0.85, 0.85),
    (0.15, 0.85, 0.35, 0.70),
    (0.85, 0.15, 0.70, 0.35),
    (0.35, 0.65, 0.50, 0.20),
    (0.65, 0.35, 0.20, 0.50),
    (0.25, 0.45, 0.80, 0.60),
    (0.75, 0.55, 0.60, 0.80),
)
PRODUCTION_POPULATION_SIZE = len(OPTIMIZER_STARTS)

AGGREGATE_GATE_NAMES = (
    "hand_likelihood",
    "extreme_finite",
    "synthetic_recovery",
    "equal_evaluation_budget",
    "held_out_likelihood",
)


@dataclass(frozen=True, slots=True)
class TrialPlan:
    """Every independent seed used by one recovery/equal-budget trial."""

    training_data_seed: int
    likelihood_search_seed: int
    production_search_seed: int
    production_selection_trial_seeds: tuple[int, ...]
    held_out_data_seed: int


TRIAL_PLANS = (
    TrialPlan(4101, 14101, 24101, (104101,), 34101),
    TrialPlan(4201, 14201, 24201, (104201,), 34201),
    TrialPlan(4301, 14301, 24301, (104301,), 34301),
)


class HandCase(TypedDict):
    name: str
    rates: Rates
    iats: tuple[float, ...]
    terminal_silence: float
    expected_log_likelihood: float


class ExtremeCase(TypedDict):
    name: str
    rates: Rates
    iats: tuple[float, ...]
    terminal_silence: float


HAND_CASES: tuple[HandCase, ...] = (
    {
        "name": "arrival_epoch_two_iats_with_censoring",
        "rates": (1.0, 3.0, 1.0, 9.0),
        "iats": (0.2, 0.7),
        "terminal_silence": 0.4,
        "expected_log_likelihood": -2.2108555447313237991950094200043425283,
    },
    {
        "name": "zero_iat_without_terminal_silence",
        "rates": (0.5, 2.0, 3.0, 5.0),
        "iats": (0.0, 0.125),
        "terminal_silence": 0.0,
        "expected_log_likelihood": 2.0977643189333293923625447526341699828,
    },
)

EXTREME_CASES: tuple[ExtremeCase, ...] = (
    {
        "name": "small_rates_long_intervals",
        "rates": (1e-8, 2e-8, 1e-6, 5e-6),
        "iats": (0.0, 100_000.0, 200_000.0),
        "terminal_silence": 300_000.0,
    },
    {
        "name": "large_rates_short_intervals",
        "rates": (100_000.0, 200_000.0, 500_000.0, 2_000_000.0),
        "iats": (1e-7, 2e-6, 0.0),
        "terminal_silence": 1e-6,
    },
    {
        "name": "widely_separated_rates",
        "rates": (1e-3, 1e3, 1e-2, 1e4),
        "iats": (1e-4, 0.1, 1.0),
        "terminal_silence": 0.25,
    },
)


@dataclass(frozen=True, slots=True)
class ProbeRateBounds:
    """Finite named rate bounds with disjoint ordered arrival-rate intervals."""

    q01: tuple[float, float]
    q10: tuple[float, float]
    lambda0: tuple[float, float]
    lambda1: tuple[float, float]

    def __post_init__(self) -> None:
        for name in ("q01", "q10", "lambda0", "lambda1"):
            lower, upper = getattr(self, name)
            if not all(math.isfinite(value) and value > 0.0 for value in (lower, upper)) or lower >= upper:
                raise ValueError(f"{name} bounds must be finite, positive, and ordered")
        if self.lambda0[1] >= self.lambda1[0]:
            raise ValueError("probe arrival-rate bounds must preserve a strictly positive gap")


PROBE_BOUNDS = ProbeRateBounds(
    q01=(0.1, 4.0),
    q10=(0.2, 6.0),
    lambda0=(0.5, 3.0),
    lambda1=(4.0, 12.0),
)

PRODUCTION_BOUNDS = MmppConfig(
    crossover_probability=PRODUCTION_CROSSOVER_PROBABILITY,
    mutation_probability=PRODUCTION_MUTATION_PROBABILITY,
    mutation_scale=PRODUCTION_MUTATION_SCALE,
    q01=FloatBounds(lower=PROBE_BOUNDS.q01[0], upper=PROBE_BOUNDS.q01[1]),
    q10=FloatBounds(lower=PROBE_BOUNDS.q10[0], upper=PROBE_BOUNDS.q10[1]),
    lambda0=FloatBounds(lower=PROBE_BOUNDS.lambda0[0], upper=PROBE_BOUNDS.lambda0[1]),
    lambda1=FloatBounds(lower=PROBE_BOUNDS.lambda1[0], upper=PROBE_BOUNDS.lambda1[1]),
)

_GENERATION_LIMITS = GenerationLimits(max_packets=10_000, max_output_bytes=10_000_000, max_wall_seconds=5.0)
_SIMILARITY = SimilarityConfig(
    iat_diagnostic_quantile=0.75,
    acf_lags=(1,),
    acf_lag_weights=(1.0,),
    acf_iat_weight=1.0,
    acf_size_weight=0.0,
    multiscale_widths_seconds=(1.0, 10.0),
    multiscale_scale_weights=(0.5, 0.5),
    multiscale_packet_weight=1.0,
    multiscale_byte_weight=0.0,
    max_direction_bin_cells=1_000,
    method_weights=MethodWeights(
        frame_size_ks=0.0,
        iat_ks=1.0,
        autocorrelation=0.0,
        multiscale_rate=0.0,
    ),
)
_MARKS = MarkDistribution(
    (
        MarkCount(Direction.OUTBOUND, 60, 1),
        MarkCount(Direction.INBOUND, 120, 1),
    )
)


@dataclass(frozen=True, slots=True)
class LikelihoodEvaluation:
    """One complete SciPy objective evaluation retained in call order."""

    evaluation_index: int
    coordinates: Coordinates
    rates: Rates | None
    objective: float | None
    log_likelihood: float | None
    status: Literal["valid", "invalid"]
    failure: str | None


@dataclass(frozen=True, slots=True)
class SimulationCandidateHistory:
    """One production candidate in one generation, with optional evaluation event."""

    generation: int
    candidate_id: tuple[int, int]
    genes: Rates | None
    status: str
    fitness: float
    failure: dict[str, object] | None
    evaluation_index: int | None


@dataclass(frozen=True, slots=True)
class SimulationGenerationHistory:
    """The complete post-evaluation production population for one generation."""

    generation: int
    candidates: tuple[SimulationCandidateHistory, ...]


@dataclass(frozen=True, slots=True)
class LikelihoodFit:
    rates: Rates
    log_likelihood: float
    evaluations: int
    termination: str
    starts: tuple[Rates, ...]
    history: tuple[LikelihoodEvaluation, ...]


@dataclass(frozen=True, slots=True)
class SimulationFit:
    rates: Rates
    fitness: float
    evaluations: int
    termination: Literal["hard_limit"]
    starts: tuple[Rates, ...]
    history: tuple[SimulationGenerationHistory, ...]


class _DifferentialEvolutionResult(Protocol):
    x: NDArray[np.float64]
    fun: float
    nfev: int
    message: str


class _MatrixExponential(Protocol):
    def __call__(self, matrix: NDArray[np.float64]) -> NDArray[np.float64]: ...


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


_expm = cast(_MatrixExponential, cast(Any, scipy_linalg).expm)
_differential_evolution = cast(_DifferentialEvolution, cast(Any, scipy_optimize).differential_evolution)


class LikelihoodHistoryRecord(TypedDict):
    evaluation_index: int
    coordinates: list[float]
    rates: list[float] | None
    objective: float | None
    log_likelihood: float | None
    status: str
    failure: str | None


class SimulationCandidateRecord(TypedDict):
    generation: int
    candidate_id: list[int]
    genes: list[float] | None
    status: str
    fitness: float
    failure: dict[str, object] | None
    evaluation_index: int | None


class SimulationGenerationRecord(TypedDict):
    generation: int
    candidates: list[SimulationCandidateRecord]


class FitRecord(TypedDict):
    rates: list[float]
    starts: list[list[float]]
    evaluations: int
    termination: str
    training_log_likelihood: float
    history: list[LikelihoodHistoryRecord]


class SimulationRecord(TypedDict):
    rates: list[float]
    starts: list[list[float]]
    evaluations: int
    termination: str
    training_fitness: float
    history: list[SimulationGenerationRecord]


class HeldOutRecord(TypedDict):
    seed: int
    iats: list[float]
    terminal_silence: float
    likelihood_fit_log_likelihood: float
    simulation_distance_fit_log_likelihood: float


class TrialGateRecord(TypedDict):
    recovery: bool
    equal_evaluation_budget: bool
    held_out_likelihood: bool


class TrialRecord(TypedDict):
    seed: int
    true_rates: list[float]
    seed_limit_plan: SeedLimitRecord
    training_window_seconds: float
    simulation_reference_window_seconds: float
    training_event_count: int
    likelihood_fit: FitRecord
    simulation_distance_fit: SimulationRecord
    held_out: HeldOutRecord
    log_rate_errors: list[float]
    gates: TrialGateRecord


class HandRecord(TypedDict):
    name: str
    rates: list[float]
    iats: list[float]
    terminal_silence: float
    expected_log_likelihood: float
    observed_log_likelihood: float
    absolute_error: float
    passed: bool


class ExtremeRecord(TypedDict):
    name: str
    rates: list[float]
    iats: list[float]
    terminal_silence: float
    observed_log_likelihood: float
    passed: bool


type AggregateGates = dict[str, bool]


class DecisionRecord(TypedDict):
    outcome: Literal["pass", "reject"]
    failed_gates: list[str]
    production_changed: bool


class LikelihoodOptimizerPolicy(TypedDict):
    method: str
    population_size: int
    generations: int
    tol: float
    atol: float
    polish: bool
    updating: str
    workers: int


class SimulationOptimizerPolicy(TypedDict):
    method: str
    population_size: int
    generations: int
    elite_count: int
    tournament_size: int
    duplicate_mutation_attempts: int
    crossover_probability: float
    mutation_probability: float
    mutation_scale: float


class PolicyRecord(TypedDict):
    production_changed: bool
    rng: str
    true_rates: list[float]
    rate_bounds: dict[str, list[float]]
    recovery_seeds: list[int]
    recovery_log_rate_tolerances: list[float]
    evaluation_budget: int
    likelihood_optimizer: LikelihoodOptimizerPolicy
    simulation_distance_optimizer: SimulationOptimizerPolicy
    optimizer_starts: list[list[float]]
    common_initial_rates: list[list[float]]
    training_window_seconds: float
    held_out_window_seconds: float


class SeedLimitRecord(TypedDict):
    training_data_seed: int
    likelihood_search_seed: int
    production_search_seed: int
    production_selection_trial_seeds: list[int]
    production_final_seed: None
    held_out_data_seed: int
    training_observation_window_seconds: float
    held_out_observation_window_seconds: float
    generation_limits: dict[str, int | float]


class ProbeEvidence(TypedDict):
    schema_version: int
    probe: str
    policy: PolicyRecord
    hand_cases: list[HandRecord]
    extreme_cases: list[ExtremeRecord]
    trials: list[TrialRecord]
    gates: AggregateGates
    decision: DecisionRecord


def _rates(value: Sequence[float]) -> Rates:
    rates = tuple(value)
    if len(rates) != 4 or any(type(rate) is not float or not math.isfinite(rate) or rate <= 0.0 for rate in rates):
        raise ValueError("MMPP rates must contain four finite positive floats")
    q01, q10, lambda0, lambda1 = rates
    if lambda0 >= lambda1:
        raise ValueError("MMPP arrival rates must satisfy lambda0 < lambda1")
    return (q01, q10, lambda0, lambda1)


def _arrival_epoch(rates: Rates) -> NDArray[np.float64]:
    q01, q10, lambda0, lambda1 = rates
    log_weight0 = math.log(q10) + math.log(lambda0)
    log_weight1 = math.log(q01) + math.log(lambda1)
    maximum = max(log_weight0, log_weight1)
    weight0 = math.exp(log_weight0 - maximum)
    weight1 = math.exp(log_weight1 - maximum)
    total = weight0 + weight1
    return np.array((weight0 / total, weight1 / total), dtype=np.float64)


def _observations(iats: Iterable[float], terminal_silence: float) -> tuple[tuple[float, ...], float]:
    values = tuple(iats)
    if any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("MMPP IATs must be finite nonnegative floats")
    if type(terminal_silence) is not float or not math.isfinite(terminal_silence) or terminal_silence < 0.0:
        raise ValueError("MMPP terminal silence must be a finite nonnegative float")
    return (values, terminal_silence)


def mmpp_log_likelihood(iats: Iterable[float], terminal_silence: float, rates: Sequence[float]) -> float:
    """Return the scaled arrival-epoch likelihood with explicit terminal survival."""
    intervals, terminal = _observations(iats, terminal_silence)
    q01, q10, lambda0, lambda1 = _rates(rates)
    q = np.array(((-q01, q01), (q10, -q10)), dtype=np.float64)
    d1 = np.diag(np.array((lambda0, lambda1), dtype=np.float64))
    d0 = q - d1
    forward = _arrival_epoch((q01, q10, lambda0, lambda1))
    accumulated = 0.0
    for interval in intervals:
        forward = forward @ _expm(d0 * interval) @ d1
        scale = float(np.sum(forward))
        if not np.all(np.isfinite(forward)) or not math.isfinite(scale) or scale <= 0.0:
            raise ValueError("MMPP forward scale must be finite and positive")
        forward = forward / scale
        accumulated += math.log(scale)
    survival = float(forward @ _expm(d0 * terminal) @ np.ones(2, dtype=np.float64))
    if not math.isfinite(survival) or survival <= 0.0:
        raise ValueError("MMPP terminal survival must be finite and positive")
    result = accumulated + math.log(survival)
    if not math.isfinite(result):
        raise ValueError("MMPP log-likelihood must be finite")
    return result


def _log_interpolate(bounds: tuple[float, float], coordinate: float) -> float:
    lower, upper = bounds
    if coordinate == 0.0:
        return lower
    if coordinate == 1.0:
        return upper
    return math.exp(math.log(lower) + coordinate * (math.log(upper) - math.log(lower)))


def decode_rates(coordinates: Sequence[float], bounds: ProbeRateBounds) -> Rates:
    """Decode bounded log coordinates and a positive dynamic arrival-rate gap."""
    values = tuple(coordinates)
    if len(values) != 4 or any(
        type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values
    ):
        raise ValueError("optimizer coordinates must be four finite floats in [0, 1]")
    first, second, third, fourth = values
    q01 = _log_interpolate(bounds.q01, first)
    q10 = _log_interpolate(bounds.q10, second)
    lambda0 = _log_interpolate(bounds.lambda0, third)
    gap = _log_interpolate((bounds.lambda1[0] - lambda0, bounds.lambda1[1] - lambda0), fourth)
    decoded = (q01, q10, lambda0, lambda0 + gap)
    if not bounds.lambda1[0] <= decoded[3] <= bounds.lambda1[1]:
        raise AssertionError("validated gap transform escaped the named lambda1 bounds")
    return decoded


# Differential evolution round-trips custom starts through its unit-bound
# scale/unscale mapping before the first objective call. Retain that locked
# public-call result so the production comparator starts from bit-identical
# decoded rate vectors rather than merely mathematically equivalent floats.
_SCIPY_EFFECTIVE_STARTS = tuple(
    cast(Coordinates, tuple(0.5 + (coordinate - 0.5) for coordinate in start)) for start in OPTIMIZER_STARTS
)
COMMON_START_RATES = tuple(decode_rates(start, PROBE_BOUNDS) for start in _SCIPY_EFFECTIVE_STARTS)


def likelihood_evaluation_count(history: Sequence[LikelihoodEvaluation]) -> int:
    """Derive a validated objective count from a complete likelihood history."""
    indexes = tuple(item.evaluation_index for item in history)
    if not indexes:
        raise ValueError("likelihood evaluation history must be nonempty")
    if indexes != tuple(range(1, len(indexes) + 1)):
        raise ValueError("likelihood evaluation indexes must be contiguous from one")
    return len(indexes)


def simulation_evaluation_count(history: Sequence[SimulationGenerationHistory]) -> int:
    """Derive a validated objective count from production evaluation events."""
    if not history:
        raise ValueError("simulation evaluation history must be nonempty")
    indexes = tuple(
        candidate.evaluation_index
        for generation in history
        for candidate in generation.candidates
        if candidate.evaluation_index is not None
    )
    if not indexes:
        raise ValueError("simulation evaluation history must contain evaluation events")
    if indexes != tuple(range(1, len(indexes) + 1)):
        raise ValueError("simulation evaluation indexes must be contiguous from one")
    return len(indexes)


def fit_mmpp_likelihood(
    iats: Iterable[float], terminal_silence: float, bounds: ProbeRateBounds, *, seed: int
) -> LikelihoodFit:
    """Fit rates with deterministic PCG64 differential evolution and a hard budget."""
    intervals, terminal = _observations(iats, terminal_silence)
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

    result = _differential_evolution(
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


def _generate_trace(rates: Rates, *, seed: int, window: float) -> TrafficTrace:
    model = MmppModel(*rates, marks=_MARKS)
    result = MMPP_FAMILY.generate(model, seed, window, _GENERATION_LIMITS, clock=lambda: 0.0)
    return TrafficTrace.from_events(result.require_complete())


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


def _production_initial_population(events: Sequence[TraceEvent], starts: Sequence[Rates]) -> tuple[Candidate, ...]:
    return tuple(
        Candidate(
            identifier=CandidateId(birth_generation=0, birth_index=index),
            family="mmpp",
            genes=cast(Rates, MMPP_FAMILY.repair(rates, PRODUCTION_BOUNDS, events)),
            status="pending",
            fitness=0.0,
            trials=(),
            invalid=None,
            duplicate_diagnostics=(),
        )
        for index, rates in enumerate(starts)
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


def _simulation_distance_fit(
    reference: TrafficTrace,
    *,
    window: float,
    seed: int,
    trial_seeds: tuple[int, ...],
    starts: tuple[Rates, ...],
) -> SimulationFit:
    events = reference.to_events()
    bounds: dict[FamilyName, FamilyBounds] = {"mmpp": PRODUCTION_BOUNDS}
    priority: FamilyPriority = ("mmpp",)
    rng = make_rng(seed)
    context = validate_evaluation_context(
        EvaluationContext(
            reference=events,
            window=window,
            families={"mmpp": MMPP_FAMILY},
            bounds=bounds,
            trial_seeds=trial_seeds,
            trial_limits=_GENERATION_LIMITS,
            similarity=_SIMILARITY,
        )
    )
    population = _production_initial_population(events, starts)
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
                reference=events,
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


def _trace_observations(trace: TrafficTrace, window: float) -> tuple[tuple[float, ...], float]:
    iats = tuple(float(value) for value in trace.iats())
    terminal = window - float(trace.timestamps[-1])
    if terminal < 0.0 and math.isclose(terminal, 0.0, rel_tol=0.0, abs_tol=1e-12):
        terminal = 0.0
    return _observations(iats, terminal)


def _rates_list(rates: Rates) -> list[float]:
    return list(rates)


def _likelihood_history_records(history: Sequence[LikelihoodEvaluation]) -> list[LikelihoodHistoryRecord]:
    return [
        {
            "evaluation_index": item.evaluation_index,
            "coordinates": list(item.coordinates),
            "rates": None if item.rates is None else _rates_list(item.rates),
            "objective": item.objective,
            "log_likelihood": item.log_likelihood,
            "status": item.status,
            "failure": item.failure,
        }
        for item in history
    ]


def _simulation_history_records(
    history: Sequence[SimulationGenerationHistory],
) -> list[SimulationGenerationRecord]:
    return [
        {
            "generation": generation.generation,
            "candidates": [
                {
                    "generation": candidate.generation,
                    "candidate_id": list(candidate.candidate_id),
                    "genes": None if candidate.genes is None else _rates_list(candidate.genes),
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


def _seed_limit_record(plan: TrialPlan) -> SeedLimitRecord:
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
            "max_packets": _GENERATION_LIMITS.max_packets,
            "max_output_bytes": _GENERATION_LIMITS.max_output_bytes,
            "max_wall_seconds": _GENERATION_LIMITS.max_wall_seconds,
        },
    }


def _likelihood_optimizer_policy() -> LikelihoodOptimizerPolicy:
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


def _simulation_optimizer_policy() -> SimulationOptimizerPolicy:
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
    return {
        "outcome": "pass" if not failed else "reject",
        "failed_gates": failed,
        "production_changed": False,
    }


def _hand_records() -> list[HandRecord]:
    records: list[HandRecord] = []
    for case in HAND_CASES:
        observed = mmpp_log_likelihood(case["iats"], case["terminal_silence"], case["rates"])
        error = abs(observed - case["expected_log_likelihood"])
        records.append(
            {
                "name": case["name"],
                "rates": _rates_list(case["rates"]),
                "iats": list(case["iats"]),
                "terminal_silence": case["terminal_silence"],
                "expected_log_likelihood": case["expected_log_likelihood"],
                "observed_log_likelihood": observed,
                "absolute_error": error,
                "passed": error <= HAND_ABSOLUTE_TOLERANCE,
            }
        )
    return records


def _extreme_records() -> list[ExtremeRecord]:
    records: list[ExtremeRecord] = []
    for case in EXTREME_CASES:
        observed = mmpp_log_likelihood(case["iats"], case["terminal_silence"], case["rates"])
        records.append(
            {
                "name": case["name"],
                "rates": _rates_list(case["rates"]),
                "iats": list(case["iats"]),
                "terminal_silence": case["terminal_silence"],
                "observed_log_likelihood": observed,
                "passed": math.isfinite(observed),
            }
        )
    return records


def _trial(plan: TrialPlan) -> TrialRecord:
    seed = plan.training_data_seed
    training = _generate_trace(TRUE_RATES, seed=seed, window=TRAINING_WINDOW_SECONDS)
    training_iats, training_terminal = _trace_observations(training, TRAINING_WINDOW_SECONDS)
    likelihood = fit_mmpp_likelihood(
        training_iats,
        training_terminal,
        PROBE_BOUNDS,
        seed=plan.likelihood_search_seed,
    )
    simulation_window = float(training.timestamps[-1])
    simulation = _simulation_distance_fit(
        training,
        window=simulation_window,
        seed=plan.production_search_seed,
        trial_seeds=plan.production_selection_trial_seeds,
        starts=likelihood.starts,
    )
    held_out_seed = plan.held_out_data_seed
    held_out = _generate_trace(TRUE_RATES, seed=held_out_seed, window=HELD_OUT_WINDOW_SECONDS)
    held_out_iats, held_out_terminal = _trace_observations(held_out, HELD_OUT_WINDOW_SECONDS)
    likelihood_held_out = mmpp_log_likelihood(held_out_iats, held_out_terminal, likelihood.rates)
    simulation_held_out = mmpp_log_likelihood(held_out_iats, held_out_terminal, simulation.rates)
    log_errors = tuple(
        abs(math.log(observed / expected)) for observed, expected in zip(likelihood.rates, TRUE_RATES, strict=True)
    )
    recovery = all(error <= tolerance for error, tolerance in zip(log_errors, RECOVERY_TOLERANCES, strict=True))
    equal_budget = likelihood.evaluations == simulation.evaluations == EVALUATION_BUDGET
    held_out_gate = likelihood_held_out >= simulation_held_out - 1e-12
    return {
        "seed": seed,
        "true_rates": _rates_list(TRUE_RATES),
        "seed_limit_plan": _seed_limit_record(plan),
        "training_window_seconds": TRAINING_WINDOW_SECONDS,
        "simulation_reference_window_seconds": simulation_window,
        "training_event_count": len(training),
        "likelihood_fit": {
            "rates": _rates_list(likelihood.rates),
            "starts": [_rates_list(start) for start in likelihood.starts],
            "evaluations": likelihood.evaluations,
            "termination": likelihood.termination,
            "training_log_likelihood": likelihood.log_likelihood,
            "history": _likelihood_history_records(likelihood.history),
        },
        "simulation_distance_fit": {
            "rates": _rates_list(simulation.rates),
            "starts": [_rates_list(start) for start in simulation.starts],
            "evaluations": simulation.evaluations,
            "termination": simulation.termination,
            "training_fitness": simulation.fitness,
            "history": _simulation_history_records(simulation.history),
        },
        "held_out": {
            "seed": held_out_seed,
            "iats": list(held_out_iats),
            "terminal_silence": held_out_terminal,
            "likelihood_fit_log_likelihood": likelihood_held_out,
            "simulation_distance_fit_log_likelihood": simulation_held_out,
        },
        "log_rate_errors": list(log_errors),
        "gates": {
            "recovery": recovery,
            "equal_evaluation_budget": equal_budget,
            "held_out_likelihood": held_out_gate,
        },
    }


def build_probe_evidence() -> ProbeEvidence:
    """Run every predeclared gate and return complete machine-readable evidence."""
    hand = _hand_records()
    extreme = _extreme_records()
    trials = [_trial(plan) for plan in TRIAL_PLANS]
    gates: AggregateGates = {
        "hand_likelihood": all(record["passed"] for record in hand),
        "extreme_finite": all(record["passed"] for record in extreme),
        "synthetic_recovery": all(record["gates"]["recovery"] for record in trials),
        "equal_evaluation_budget": all(record["gates"]["equal_evaluation_budget"] for record in trials),
        "held_out_likelihood": all(record["gates"]["held_out_likelihood"] for record in trials),
    }
    policy: PolicyRecord = {
        "production_changed": False,
        "rng": "numpy.random.Generator/PCG64",
        "true_rates": _rates_list(TRUE_RATES),
        "rate_bounds": {
            "q01": list(PROBE_BOUNDS.q01),
            "q10": list(PROBE_BOUNDS.q10),
            "lambda0": list(PROBE_BOUNDS.lambda0),
            "lambda1": list(PROBE_BOUNDS.lambda1),
        },
        "recovery_seeds": list(RECOVERY_SEEDS),
        "recovery_log_rate_tolerances": _rates_list(RECOVERY_TOLERANCES),
        "evaluation_budget": EVALUATION_BUDGET,
        "likelihood_optimizer": _likelihood_optimizer_policy(),
        "simulation_distance_optimizer": _simulation_optimizer_policy(),
        "optimizer_starts": [list(start) for start in OPTIMIZER_STARTS],
        "common_initial_rates": [_rates_list(rates) for rates in COMMON_START_RATES],
        "training_window_seconds": TRAINING_WINDOW_SECONDS,
        "held_out_window_seconds": HELD_OUT_WINDOW_SECONDS,
    }
    return {
        "schema_version": 3,
        "probe": "scipy_two_state_mmpp_likelihood",
        "policy": policy,
        "hand_cases": hand,
        "extreme_cases": extreme,
        "trials": trials,
        "gates": gates,
        "decision": decide_probe(gates),
    }


def validate_probe_evidence(evidence: ProbeEvidence) -> ProbeEvidence:
    """Reject optimizer or per-trial policy drift before canonical rendering."""
    if evidence["schema_version"] != 3:
        raise ValueError("probe evidence schema version does not match the canonical optimizer policy")
    if evidence["policy"]["likelihood_optimizer"] != _likelihood_optimizer_policy():
        raise ValueError("likelihood optimizer policy does not match executed controls")
    if evidence["policy"]["simulation_distance_optimizer"] != _simulation_optimizer_policy():
        raise ValueError("simulation optimizer policy does not match executed controls")
    if len(evidence["trials"]) != len(TRIAL_PLANS):
        raise ValueError("trial seed/limit plan count does not match the executed trials")
    for trial, plan in zip(evidence["trials"], TRIAL_PLANS, strict=True):
        if trial["seed_limit_plan"] != _seed_limit_record(plan):
            raise ValueError("trial seed/limit plan does not match executed controls")
    return evidence


def render_probe_evidence(evidence: ProbeEvidence) -> bytes:
    """Render canonical UTF-8 JSON with sorted compact keys and one final newline."""
    validated = validate_probe_evidence(evidence)
    return (json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def write_probe_evidence(destination: Path, evidence: ProbeEvidence, *, check: bool) -> bool:
    """Write canonical evidence, or compare it byte-for-byte without mutation."""
    rendered = render_probe_evidence(evidence)
    if check:
        try:
            return destination.read_bytes() == rendered
        except FileNotFoundError:
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return True
