"""Schema owner for Validation Study tooling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, TypedDict

from trafficlab.common.config import (
    C2stSettings,
    DispersionSettings,
    FloatBounds,
    GenerationLimits,
    MethodWeights,
    MmppConfig,
    PostfitSettings,
    SimilarityConfig,
    TransitionSettings,
)
from trafficlab.common.trace import Direction
from trafficlab.generation.models.common import MarkCount, MarkDistribution

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
    (0.15, 0.85, 0.35, 0.7),
    (0.85, 0.15, 0.7, 0.35),
    (0.35, 0.65, 0.5, 0.2),
    (0.65, 0.35, 0.2, 0.5),
    (0.25, 0.45, 0.8, 0.6),
    (0.75, 0.55, 0.6, 0.8),
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
        "expected_log_likelihood": -2.2108555447313236,
    },
    {
        "name": "zero_iat_without_terminal_silence",
        "rates": (0.5, 2.0, 3.0, 5.0),
        "iats": (0.0, 0.125),
        "terminal_silence": 0.0,
        "expected_log_likelihood": 2.0977643189333293,
    },
)

EXTREME_CASES: tuple[ExtremeCase, ...] = (
    {
        "name": "small_rates_long_intervals",
        "rates": (1e-08, 2e-08, 1e-06, 5e-06),
        "iats": (0.0, 100000.0, 200000.0),
        "terminal_silence": 300000.0,
    },
    {
        "name": "large_rates_short_intervals",
        "rates": (100000.0, 200000.0, 500000.0, 2000000.0),
        "iats": (1e-07, 2e-06, 0.0),
        "terminal_silence": 1e-06,
    },
    {
        "name": "widely_separated_rates",
        "rates": (0.001, 1000.0, 0.01, 10000.0),
        "iats": (0.0001, 0.1, 1.0),
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


PROBE_BOUNDS = ProbeRateBounds(q01=(0.1, 4.0), q10=(0.2, 6.0), lambda0=(0.5, 3.0), lambda1=(4.0, 12.0))

PRODUCTION_BOUNDS = MmppConfig(
    crossover_probability=PRODUCTION_CROSSOVER_PROBABILITY,
    mutation_probability=PRODUCTION_MUTATION_PROBABILITY,
    mutation_scale=PRODUCTION_MUTATION_SCALE,
    q01=FloatBounds(lower=PROBE_BOUNDS.q01[0], upper=PROBE_BOUNDS.q01[1]),
    q10=FloatBounds(lower=PROBE_BOUNDS.q10[0], upper=PROBE_BOUNDS.q10[1]),
    lambda0=FloatBounds(lower=PROBE_BOUNDS.lambda0[0], upper=PROBE_BOUNDS.lambda0[1]),
    lambda1=FloatBounds(lower=PROBE_BOUNDS.lambda1[0], upper=PROBE_BOUNDS.lambda1[1]),
)

GENERATION_LIMITS = GenerationLimits(max_packets=10000, max_output_bytes=10000000, max_wall_seconds=5.0)

SIMILARITY = SimilarityConfig(
    iat_diagnostic_quantile=0.75,
    acf_lags=(1,),
    acf_lag_weights=(1.0,),
    acf_iat_weight=1.0,
    acf_size_weight=0.0,
    multiscale_widths_seconds=(1.0, 10.0),
    multiscale_scale_weights=(0.5, 0.5),
    multiscale_packet_weight=1.0,
    multiscale_byte_weight=0.0,
    max_direction_bin_cells=1000,
    cvm_iat_weight=0.5,
    cvm_size_weight=0.5,
    ad_iat_weight=0.5,
    ad_size_weight=0.5,
    js_iat_bin_count=8,
    js_iat_weight=0.5,
    js_mark_weight=0.5,
    mmd_feature_count=32,
    mmd_seed=17,
    mmd_scale_floor=0.001,
    method_weights=MethodWeights(
        frame_size_ks=0.0,
        iat_ks=1.0,
        autocorrelation=0.0,
        multiscale_rate=0.0,
        cramer_von_mises=0.0,
        anderson_darling=0.0,
        jensen_shannon=0.0,
        approximate_mmd=0.0,
    ),
    postfit=PostfitSettings(
        dispersion=DispersionSettings(
            widths_seconds=(1.0, 10.0),
            scale_weights=(0.5, 0.5),
            fano_weight=0.5,
            allan_weight=0.5,
        ),
        transition=TransitionSettings(
            size_bin_count=2,
            iat_bin_count=2,
            pseudocount=0.5,
            occupancy_weight=0.34,
            transition_rows_weight=0.33,
            runs_weight=0.33,
        ),
        c2st=C2stSettings(
            feature_version="window-v1",
            window_width_seconds=1.0,
            fold_count=2,
            guard_window_count=1,
            maximum_window_count=64,
            l2_regularization=1.0,
            maximum_iterations=100,
            tolerance=1e-9,
        ),
    ),
)

MARKS = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1), MarkCount(Direction.INBOUND, 120, 1)))


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
