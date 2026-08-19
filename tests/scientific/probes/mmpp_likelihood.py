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
from trafficlab.genetic.population import initial_population, rank_candidates
from trafficlab.genetic.types import Candidate, FamilyPriority
from trafficlab.models.common import FamilyBounds, MarkCount, MarkDistribution, make_rng
from trafficlab.models.mmpp import MmppModel
from trafficlab.models.registry import MMPP_FAMILY
from trafficlab.trace import Direction, TrafficTrace

type Rates = tuple[float, float, float, float]
type Coordinates = tuple[float, float, float, float]

HAND_ABSOLUTE_TOLERANCE = 1e-12
TRAINING_WINDOW_SECONDS = 180.0
HELD_OUT_WINDOW_SECONDS = 120.0
EVALUATION_BUDGET = 120
OPTIMIZER_GENERATIONS = 14
SIMULATION_GENERATIONS = 16
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
    crossover_probability=0.9,
    mutation_probability=0.25,
    mutation_scale=0.15,
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
class LikelihoodFit:
    rates: Rates
    log_likelihood: float
    evaluations: int
    termination: str


@dataclass(frozen=True, slots=True)
class SimulationFit:
    rates: Rates
    fitness: float
    evaluations: int
    termination: Literal["hard_limit"]
    starts: tuple[Rates, ...]


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


class FitRecord(TypedDict):
    rates: list[float]
    starts: list[list[float]]
    evaluations: int
    termination: str
    training_log_likelihood: float


class SimulationRecord(TypedDict):
    rates: list[float]
    starts: list[list[float]]
    evaluations: int
    termination: str
    training_fitness: float


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


class PolicyRecord(TypedDict):
    production_changed: bool
    rng: str
    true_rates: list[float]
    rate_bounds: dict[str, list[float]]
    recovery_seeds: list[int]
    recovery_log_rate_tolerances: list[float]
    evaluation_budget: int
    likelihood_optimizer: dict[str, int | str]
    simulation_distance_optimizer: dict[str, int | str]
    optimizer_starts: list[list[float]]
    training_window_seconds: float
    held_out_window_seconds: float


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


def fit_mmpp_likelihood(
    iats: Iterable[float], terminal_silence: float, bounds: ProbeRateBounds, *, seed: int
) -> LikelihoodFit:
    """Fit rates with deterministic PCG64 differential evolution and a hard budget."""
    intervals, terminal = _observations(iats, terminal_silence)
    if type(seed) is not int or seed < 0:
        raise ValueError("optimizer seed must be a nonnegative exact integer")

    def objective(raw: NDArray[np.float64]) -> float:
        coordinates = cast(Coordinates, tuple(float(item) for item in raw))
        return -mmpp_log_likelihood(intervals, terminal, decode_rates(coordinates, bounds))

    result = _differential_evolution(
        objective,
        bounds=((0.0, 1.0),) * 4,
        maxiter=OPTIMIZER_GENERATIONS,
        tol=0.0,
        atol=0.0,
        init=np.asarray(OPTIMIZER_STARTS, dtype=np.float64),
        polish=False,
        updating="immediate",
        workers=1,
        rng=np.random.Generator(np.random.PCG64(seed)),
    )
    coordinates = cast(Coordinates, tuple(float(item) for item in result.x))
    return LikelihoodFit(
        rates=decode_rates(coordinates, bounds),
        log_likelihood=-float(result.fun),
        evaluations=int(result.nfev),
        termination=str(result.message),
    )


def _generate_trace(rates: Rates, *, seed: int, window: float) -> TrafficTrace:
    model = MmppModel(*rates, marks=_MARKS)
    result = MMPP_FAMILY.generate(model, seed, window, _GENERATION_LIMITS, clock=lambda: 0.0)
    return TrafficTrace.from_events(result.require_complete())


def _evaluate_population(
    candidates: Sequence[Candidate], context: ValidatedEvaluationContext
) -> tuple[tuple[Candidate, ...], int]:
    evaluations = 0
    output: list[Candidate] = []
    for candidate in candidates:
        if candidate.status == "pending":
            candidate = evaluate_candidate(candidate, context)
            evaluations += 1
        output.append(candidate)
    return (tuple(output), evaluations)


def _simulation_distance_fit(reference: TrafficTrace, *, window: float, seed: int) -> SimulationFit:
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
            trial_seeds=(seed + 100_000,),
            trial_limits=_GENERATION_LIMITS,
            similarity=_SIMILARITY,
        )
    )
    population = initial_population(
        priority, population_size=len(OPTIMIZER_STARTS), bounds=bounds, reference=events, rng=rng
    )
    starts = tuple(cast(Rates, candidate.genes) for candidate in population)
    population, evaluations = _evaluate_population(population, context)
    for generation in range(1, SIMULATION_GENERATIONS + 1):
        population = fill_next_population(
            population,
            generation=generation,
            population_size=len(OPTIMIZER_STARTS),
            elite_count=1,
            tournament_size=2,
            context=ReproductionContext(
                reference=events,
                family_bounds=bounds,
                family_priority=priority,
                duplicate_mutation_attempts=4,
            ),
            rng=rng,
        )
        population, new_evaluations = _evaluate_population(population, context)
        evaluations += new_evaluations
    winner = rank_candidates(population, family_priority=priority)[0]
    if winner.status != "valid" or winner.genes is None:
        raise AssertionError("bounded production MMPP search produced no valid winner")
    return SimulationFit(
        rates=cast(Rates, winner.genes),
        fitness=winner.fitness,
        evaluations=evaluations,
        termination="hard_limit",
        starts=starts,
    )


def _trace_observations(trace: TrafficTrace, window: float) -> tuple[tuple[float, ...], float]:
    iats = tuple(float(value) for value in trace.iats())
    terminal = window - float(trace.timestamps[-1])
    if terminal < 0.0 and math.isclose(terminal, 0.0, rel_tol=0.0, abs_tol=1e-12):
        terminal = 0.0
    return _observations(iats, terminal)


def _rates_list(rates: Rates) -> list[float]:
    return list(rates)


def decide_probe(gates: Mapping[str, bool]) -> DecisionRecord:
    """Return a fail-closed decision with every failed gate named deterministically."""
    failed = sorted(name for name, passed in gates.items() if not passed)
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


def _trial(seed: int) -> TrialRecord:
    training = _generate_trace(TRUE_RATES, seed=seed, window=TRAINING_WINDOW_SECONDS)
    training_iats, training_terminal = _trace_observations(training, TRAINING_WINDOW_SECONDS)
    likelihood = fit_mmpp_likelihood(training_iats, training_terminal, PROBE_BOUNDS, seed=seed + 10_000)
    simulation_window = float(training.timestamps[-1])
    simulation = _simulation_distance_fit(training, window=simulation_window, seed=seed + 20_000)
    held_out_seed = seed + 30_000
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
        "training_window_seconds": TRAINING_WINDOW_SECONDS,
        "simulation_reference_window_seconds": simulation_window,
        "training_event_count": len(training),
        "likelihood_fit": {
            "rates": _rates_list(likelihood.rates),
            "starts": [_rates_list(decode_rates(start, PROBE_BOUNDS)) for start in OPTIMIZER_STARTS],
            "evaluations": likelihood.evaluations,
            "termination": likelihood.termination,
            "training_log_likelihood": likelihood.log_likelihood,
        },
        "simulation_distance_fit": {
            "rates": _rates_list(simulation.rates),
            "starts": [_rates_list(start) for start in simulation.starts],
            "evaluations": simulation.evaluations,
            "termination": simulation.termination,
            "training_fitness": simulation.fitness,
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
    trials = [_trial(seed) for seed in RECOVERY_SEEDS]
    gates: AggregateGates = {
        "hand_likelihood": all(record["passed"] for record in hand),
        "extreme_finite": all(record["passed"] for record in extreme),
        "synthetic_recovery": all(record["gates"]["recovery"] for record in trials),
        "equal_budget_held_out": all(
            record["gates"]["equal_evaluation_budget"] and record["gates"]["held_out_likelihood"] for record in trials
        ),
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
        "likelihood_optimizer": {
            "method": "scipy.optimize.differential_evolution",
            "population_size": len(OPTIMIZER_STARTS),
            "generations": OPTIMIZER_GENERATIONS,
        },
        "simulation_distance_optimizer": {
            "method": "trafficlab production genetic operators and similarity",
            "population_size": len(OPTIMIZER_STARTS),
            "generations": SIMULATION_GENERATIONS,
        },
        "optimizer_starts": [list(start) for start in OPTIMIZER_STARTS],
        "training_window_seconds": TRAINING_WINDOW_SECONDS,
        "held_out_window_seconds": HELD_OUT_WINDOW_SECONDS,
    }
    return {
        "schema_version": 1,
        "probe": "scipy_two_state_mmpp_likelihood",
        "policy": policy,
        "hand_cases": hand,
        "extreme_cases": extreme,
        "trials": trials,
        "gates": gates,
        "decision": decide_probe(gates),
    }


def render_probe_evidence(evidence: ProbeEvidence) -> bytes:
    """Render canonical UTF-8 JSON with sorted compact keys and one final newline."""
    return (json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


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
