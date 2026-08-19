"""Test-only pymoo optimizer, fairness, cache, and checkpoint-adoption probe."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self, TypedDict, cast

import numpy as np
import pymoo  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictBool, StrictFloat, StrictInt, model_validator
from pymoo.algorithms.soo.nonconvex.ga import GA  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.mixed import MixedVariableGA  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.population import Population  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.problem import ElementwiseProblem  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.variable import Integer, Real  # pyright: ignore[reportMissingTypeStubs]

from trafficlab.config import (
    FamilyName,
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MarkovRenewalConfig,
    MethodWeights,
    MmppConfig,
    PoissonConfig,
    SimilarityConfig,
)
from trafficlab.genetic.checkpoint import RngState
from trafficlab.genetic.evaluation import (
    EvaluationContext,
    ValidatedEvaluationContext,
    evaluate_candidate,
    evaluate_final,
    validate_evaluation_context,
)
from trafficlab.genetic.types import Candidate, CandidateId, TrialResult, rebuild_genetic_record
from trafficlab.models.common import FamilyBounds, Gene, Genes, ModelFamily
from trafficlab.models.registry import REGISTRY
from trafficlab.trace import Direction, TraceEvent

type JsonObject = dict[str, Any]
type PymooAlgorithm = Any
type PymooPopulation = Any
type PymooVariable = Any

_GA = cast(Any, GA)
_MIXED_VARIABLE_GA = cast(Any, MixedVariableGA)
_POPULATION = cast(Any, Population)

FAMILY_NAMES: tuple[FamilyName, ...] = ("markov_renewal", "mmpp", "poisson_empirical")
INITIAL_EVALUATION_BUDGET = 4
TOTAL_GENERATIONS = 2
SEARCH_SEED = 6053
TRIAL_SEEDS = (17, 29)
CHAMPION_SEED = 43
WINDOW_SECONDS = 6.0
INVALID_OBJECTIVE = 1.0
MINIMUM_LOC_REDUCTION_PERCENT = 40.0

# Fix-round policy, declared before executing the revised known-case searches.
KNOWN_POPULATION_SIZE = 20
KNOWN_GENERATIONS = 40
KNOWN_TOLERANCES = {
    "bounded_continuous_sphere": 0.001,
    "mixed_integer_real_quadratic": 0.001,
}
CONTINUOUS_INITIAL_SAMPLES: tuple[tuple[float, float], ...] = (
    (-1.8, -1.8),
    (-1.8, -0.9),
    (-1.8, 0.9),
    (-1.8, 1.8),
    (-0.9, -1.8),
    (-0.9, -0.9),
    (-0.9, 0.9),
    (-0.9, 1.8),
    (0.9, -1.8),
    (0.9, -0.9),
    (0.9, 0.9),
    (0.9, 1.8),
    (1.8, -1.8),
    (1.8, -0.9),
    (1.8, 0.9),
    (1.8, 1.8),
    (-1.35, 0.45),
    (-0.45, 1.35),
    (0.45, -1.35),
    (1.35, -0.45),
)
MIXED_INITIAL_SAMPLES: tuple[dict[str, int | float], ...] = (
    {"count": 0, "scale": 0.6},
    {"count": 1, "scale": 0.8},
    {"count": 2, "scale": 1.0},
    {"count": 3, "scale": 1.5},
    {"count": 4, "scale": 1.7},
    {"count": 5, "scale": 1.9},
    {"count": 6, "scale": 0.7},
    {"count": 0, "scale": 0.9},
    {"count": 1, "scale": 1.1},
    {"count": 2, "scale": 1.4},
    {"count": 3, "scale": 1.0},
    {"count": 4, "scale": 0.8},
    {"count": 5, "scale": 1.6},
    {"count": 6, "scale": 1.8},
    {"count": 0, "scale": 2.0},
    {"count": 1, "scale": 0.5},
    {"count": 2, "scale": 1.9},
    {"count": 3, "scale": 1.7},
    {"count": 4, "scale": 1.1},
    {"count": 5, "scale": 0.6},
)

GENERATION_LIMITS = GenerationLimits(
    max_packets=1_000,
    max_output_bytes=1_000_000,
    max_wall_seconds=5.0,
)
INVALID_CASE_LIMITS = GenerationLimits(
    max_packets=1,
    max_output_bytes=1_000_000,
    max_wall_seconds=5.0,
)
SIMILARITY = SimilarityConfig(
    iat_diagnostic_quantile=0.75,
    acf_lags=(1,),
    acf_lag_weights=(1.0,),
    acf_iat_weight=0.5,
    acf_size_weight=0.5,
    multiscale_widths_seconds=(1.0, 2.0),
    multiscale_scale_weights=(0.5, 0.5),
    multiscale_packet_weight=0.5,
    multiscale_byte_weight=0.5,
    max_direction_bin_cells=100,
    method_weights=MethodWeights(
        frame_size_ks=0.25,
        iat_ks=0.25,
        autocorrelation=0.25,
        multiscale_rate=0.25,
    ),
)

CACHE_KEY_FIELDS = (
    "family",
    "genes",
    "observation_window_seconds",
    "trial_seeds",
    "generation_limits",
    "similarity",
)
CHECKPOINT_FIELDS = (
    "complete",
    "missing_fields",
    "population",
    "generation",
    "evaluation_count",
    "termination",
    "configuration",
    "pymoo_version",
    "rng",
)
REPLAY_CHECKED_FIELDS = (
    "snapshot.population",
    "snapshot.generation",
    "snapshot.evaluation_count",
    "snapshot.termination",
    "snapshot.configuration.search_seed",
    "snapshot.configuration.variables",
    "snapshot.configuration.initial_sampling",
    "snapshot.configuration.constructor",
    "snapshot.configuration.initialization",
    "snapshot.configuration.algorithm_repair",
    "snapshot.configuration.mating",
    "snapshot.configuration.mating.crossover.vtype",
    "snapshot.configuration.mating.crossover.repair",
    "snapshot.configuration.mating.mutation.vtype",
    "snapshot.configuration.mating.mutation.repair",
    "snapshot.pymoo_version",
    "snapshot.rng",
    "trial_history.evaluation_index",
    "trial_history.generation",
    "trial_history.candidate",
    "trial_history.objective",
    "trial_history.cache_key_payload",
    "trial_history.cache_key",
    "trial_history.cache_hit",
)
REPLAY_MISSING_FIELDS = (
    "documented public algorithm initialization/iteration restore API",
    "documented public operator configuration and mutable operator state",
    "documented public termination-progress restore API",
)
PROHIBITED_SERIALIZERS = ("dill", "pickle", "cloudpickle")
GATE_NAMES = (
    "known_optima",
    "deterministic_repeats",
    "family_fairness",
    "cache_and_diagnostics",
    "exact_public_state_replay",
    "production_loc_reduction",
)


class KnownCase(TypedDict):
    name: str
    variable_kinds: dict[str, str]
    bounds: dict[str, tuple[int, int] | tuple[float, float]]
    known_optimum: dict[str, object]


KNOWN_CASES: tuple[KnownCase, ...] = (
    {
        "name": "bounded_continuous_sphere",
        "variable_kinds": {"x0": "real", "x1": "real"},
        "bounds": {"x0": (-2.0, 2.0), "x1": (-2.0, 2.0)},
        "known_optimum": {"variables": {"x0": 0.0, "x1": 0.0}, "objective": 0.0},
    },
    {
        "name": "mixed_integer_real_quadratic",
        "variable_kinds": {"count": "integer", "scale": "real"},
        "bounds": {"count": (0, 6), "scale": (0.5, 2.0)},
        "known_optimum": {"variables": {"count": 3, "scale": 1.25}, "objective": 0.0},
    },
)

_FAMILY_BOUNDS: dict[FamilyName, FamilyBounds] = {
    "markov_renewal": MarkovRenewalConfig(
        q1=FloatBounds(lower=0.15, upper=0.45),
        q2=FloatBounds(lower=0.55, upper=0.9),
        alpha=FloatBounds(lower=0.05, upper=1.5),
        r=IntegerBounds(lower=1, upper=4),
        c_t=FloatBounds(lower=0.5, upper=1.5),
    ),
    "mmpp": MmppConfig(
        q01=FloatBounds(lower=0.1, upper=3.0),
        q10=FloatBounds(lower=0.1, upper=3.0),
        lambda0=FloatBounds(lower=0.2, upper=2.0),
        lambda1=FloatBounds(lower=2.5, upper=8.0),
    ),
    "poisson_empirical": PoissonConfig(c_lambda=FloatBounds(lower=0.5, upper=1.5)),
}
_INITIAL_VALUES: dict[FamilyName, tuple[dict[str, Gene], ...]] = {
    "markov_renewal": (
        {"q1": 0.2, "q2": 0.7, "alpha": 0.2, "r": 2, "c_t": 0.8},
        {"q1": 0.2, "q2": 0.7, "alpha": 0.2, "r": 2, "c_t": 0.8},
        {"q1": 0.3, "q2": 0.8, "alpha": 0.5, "r": 3, "c_t": 1.0},
        {"q1": 0.4, "q2": 0.85, "alpha": 1.0, "r": 4, "c_t": 1.2},
    ),
    "mmpp": (
        {"q01": 0.5, "q10": 1.5, "lambda0": 0.8, "lambda1": 4.0},
        {"q01": 0.5, "q10": 1.5, "lambda0": 0.8, "lambda1": 4.0},
        {"q01": 1.0, "q10": 2.0, "lambda0": 1.2, "lambda1": 6.0},
        {"q01": 2.0, "q10": 1.0, "lambda0": 0.5, "lambda1": 7.0},
    ),
    "poisson_empirical": (
        {"c_lambda": 0.75},
        {"c_lambda": 0.75},
        {"c_lambda": 1.0},
        {"c_lambda": 1.25},
    ),
}
_REFERENCE = tuple(
    TraceEvent(
        timestamp=index * 0.5,
        direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
        frame_length=60 + index * 24,
    )
    for index in range(13)
)


def _tuple_input(value: object) -> object:
    return tuple(cast(list[object], value)) if type(value) is list else value


class _StrictProbeRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


type Scalar = StrictInt | StrictFloat
type Scalars = dict[str, Scalar]


class VariableSpec(_StrictProbeRecord):
    name: str
    kind: Literal["integer", "real"]
    lower: Scalar
    upper: Scalar

    @model_validator(mode="after")
    def bound_types_match_kind(self) -> Self:
        values = (self.lower, self.upper)
        if self.kind == "integer" and any(type(value) is not int for value in values):
            raise ValueError("integer variable bounds must be exact integers")
        if self.kind == "real" and any(type(value) is not float for value in values):
            raise ValueError("real variable bounds must be exact floats")
        if self.lower >= self.upper:
            raise ValueError("variable lower bound must be less than upper")
        return self


class PublicPopulationState(_StrictProbeRecord):
    variables: Scalars
    objectives: Annotated[tuple[StrictFloat, ...], BeforeValidator(_tuple_input)]
    status: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]


class PublicTerminationState(_StrictProbeRecord):
    kind: Literal["MaximumGenerationTermination"]
    progress: StrictFloat
    has_terminated: StrictBool


class ConstructorSettings(_StrictProbeRecord):
    pop_size: StrictInt
    n_offsprings: StrictInt | None
    sampling: Literal["pymoo.core.population.Population"]
    mating: Literal["pymoo.core.mixed.MixedVariableMating"]
    eliminate_duplicates: StrictBool
    survival: Literal["pymoo.algorithms.soo.nonconvex.ga.FitnessSurvival"]
    output: Literal["pymoo.util.display.single.SingleObjectiveOutput"]
    callback: Literal["pymoo.core.callback.Callback"]
    display: Literal["pymoo.util.display.display.Display"]
    archive: None
    return_least_infeasible: Literal[False]
    save_history: Literal[False]
    verbose: Literal[False]
    evaluator: Literal["pymoo.core.evaluator.Evaluator"]
    advance_after_initial_infill: Literal[False]
    algorithm_duplicate_elimination: Literal["pymoo.core.duplicate.NoDuplicateElimination"]


class InitializationSettings(_StrictProbeRecord):
    sampling: Literal["pymoo.core.population.Population"]
    repair: Literal["pymoo.core.repair.NoRepair"]
    duplicate_elimination: Literal["pymoo.core.duplicate.NoDuplicateElimination"]


type OperatorScalar = StrictInt | StrictFloat | StrictBool | None


class RepairSettings(_StrictProbeRecord):
    operator_class: str
    name: str
    vtype: str | None
    repair: str | None


class OperatorSettings(_StrictProbeRecord):
    variable_type: str
    operator_class: str
    settings: dict[str, OperatorScalar]
    vtype: str | None
    repair: RepairSettings | None


class MatingDuplicateSettings(_StrictProbeRecord):
    operator_class: Literal["pymoo.core.mixed.MixedVariableDuplicateElimination"]
    epsilon: StrictFloat


class MatingSettings(_StrictProbeRecord):
    selection: Literal["pymoo.operators.selection.rnd.RandomSelection"]
    repair: Literal["pymoo.core.repair.NoRepair"]
    duplicate_elimination: MatingDuplicateSettings
    n_max_iterations: StrictInt
    crossover: Annotated[
        tuple[OperatorSettings, OperatorSettings, OperatorSettings, OperatorSettings],
        BeforeValidator(_tuple_input),
    ]
    mutation: Annotated[
        tuple[OperatorSettings, OperatorSettings, OperatorSettings, OperatorSettings],
        BeforeValidator(_tuple_input),
    ]


class TerminationSettings(_StrictProbeRecord):
    kind: Literal["n_gen"]
    value: StrictInt


class PublicAlgorithmConfiguration(_StrictProbeRecord):
    algorithm: Literal["pymoo.core.mixed.MixedVariableGA"]
    family: FamilyName
    search_seed: StrictInt
    variables: Annotated[tuple[VariableSpec, ...], BeforeValidator(_tuple_input)]
    initial_sampling: Annotated[tuple[Scalars, ...], BeforeValidator(_tuple_input)]
    constructor: ConstructorSettings
    initialization: InitializationSettings
    algorithm_repair: RepairSettings
    mating: MatingSettings
    termination: TerminationSettings


class PublicRngState(_StrictProbeRecord):
    engine: Literal["PCG64"]
    state: RngState


class PublicStateSnapshot(_StrictProbeRecord):
    complete: Literal[False]
    missing_fields: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    population: Annotated[tuple[PublicPopulationState, ...], BeforeValidator(_tuple_input)]
    generation: StrictInt
    evaluation_count: StrictInt
    termination: PublicTerminationState
    configuration: PublicAlgorithmConfiguration
    pymoo_version: Literal["0.6.2"]
    rng: PublicRngState


class CacheKeyRecord(_StrictProbeRecord):
    family: FamilyName
    genes: Annotated[tuple[Scalar, ...], BeforeValidator(_tuple_input)]
    observation_window_seconds: StrictFloat
    trial_seeds: Annotated[tuple[StrictInt, ...], BeforeValidator(_tuple_input)]
    generation_limits: GenerationLimits
    similarity: SimilarityConfig


class AttemptRecord(_StrictProbeRecord):
    evaluation_index: StrictInt
    generation: StrictInt
    candidate: Candidate
    objective: StrictFloat
    cache_key_payload: CacheKeyRecord
    cache_key: str
    cache_hit: StrictBool


class FamilyRunRecord(_StrictProbeRecord):
    optimizer_instance: StrictInt
    optimizer_class: Literal["pymoo.core.mixed.MixedVariableGA"]
    optimizer_config_alias: str
    family: FamilyName
    search_seed: StrictInt
    observation_window_seconds: StrictFloat
    trial_seeds: Annotated[tuple[StrictInt, ...], BeforeValidator(_tuple_input)]
    generation_limits: GenerationLimits
    similarity: SimilarityConfig
    variables: Annotated[tuple[VariableSpec, ...], BeforeValidator(_tuple_input)]
    initial_sampling: Annotated[tuple[Scalars, ...], BeforeValidator(_tuple_input)]
    initial_evaluations: StrictInt
    total_attempts: StrictInt
    objective_evaluations: StrictInt
    cache_hits: StrictInt
    history: Annotated[tuple[AttemptRecord, ...], BeforeValidator(_tuple_input)]
    best_candidate: Candidate

    @model_validator(mode="after")
    def history_cardinality_matches_summary(self) -> Self:
        if len(self.history) != self.total_attempts:
            raise ValueError("family history length must equal total attempts")
        return self


class FamilyEvidence(_StrictProbeRecord):
    family: FamilyName
    runs: Annotated[tuple[FamilyRunRecord, FamilyRunRecord], BeforeValidator(_tuple_input)]


class KnownPopulationRecord(_StrictProbeRecord):
    variables: Scalars
    objective: StrictFloat


class KnownGenerationRecord(_StrictProbeRecord):
    generation: StrictInt
    evaluation_count: StrictInt
    minimum_objective: StrictFloat
    population: Annotated[tuple[KnownPopulationRecord, ...], BeforeValidator(_tuple_input)]


class KnownRunRecord(_StrictProbeRecord):
    variables: Scalars
    objective: StrictFloat
    evaluations: StrictInt
    initial_minimum_objective: StrictFloat
    history: Annotated[tuple[KnownGenerationRecord, ...], BeforeValidator(_tuple_input)]


class KnownOptimumRecord(_StrictProbeRecord):
    variables: Scalars
    objective: StrictFloat


class KnownCaseEvidence(_StrictProbeRecord):
    name: Literal["bounded_continuous_sphere", "mixed_integer_real_quadratic"]
    objective_definition: Literal["sum_squares", "integer_real_quadratic"]
    seed: StrictInt
    variable_kinds: dict[str, Literal["integer", "real"]]
    bounds: dict[str, Annotated[tuple[Scalar, Scalar], BeforeValidator(_tuple_input)]]
    known_optimum: KnownOptimumRecord
    tolerance: StrictFloat
    population_size: StrictInt
    generations: StrictInt
    initial_sampling: Annotated[tuple[Scalars, ...], BeforeValidator(_tuple_input)]
    runs: Annotated[tuple[KnownRunRecord, KnownRunRecord], BeforeValidator(_tuple_input)]


class ChampionRecord(_StrictProbeRecord):
    family: FamilyName
    candidate: Candidate
    fresh_seed: StrictInt
    fresh_fitness: StrictFloat
    search_attempts_completed: StrictInt
    trials: Annotated[tuple[TrialResult, ...], BeforeValidator(_tuple_input)]


class FairnessEvidence(_StrictProbeRecord):
    measured_family_set: Annotated[tuple[FamilyName, ...], BeforeValidator(_tuple_input)]
    distinct_optimizer_instances: StrictInt
    common_search_seed: StrictInt
    common_trial_seeds: Annotated[tuple[StrictInt, ...], BeforeValidator(_tuple_input)]
    common_champion_seed: StrictInt
    common_window_seconds: StrictFloat
    common_generation_limits: GenerationLimits
    common_similarity: SimilarityConfig
    equal_initial_budget: StrictInt
    equal_total_budget: StrictInt
    cache_key_fields: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    champions_compared_after_attempts: StrictInt
    champion_comparison: Annotated[tuple[ChampionRecord, ...], BeforeValidator(_tuple_input)]
    champion_ranking: Annotated[tuple[FamilyName, ...], BeforeValidator(_tuple_input)]
    winner_family: FamilyName
    winner: ChampionRecord


class InvalidClassificationEvidence(_StrictProbeRecord):
    execution: Literal["pymoo.MixedVariableGA.next"]
    family: Literal["poisson_empirical"]
    limits: GenerationLimits
    pymoo_evaluations: StrictInt
    history: Annotated[tuple[AttemptRecord], BeforeValidator(_tuple_input)]


class ReplayComparison(_StrictProbeRecord):
    exact_history_identity: StrictBool
    checked_fields: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    uninterrupted_history: Annotated[tuple[AttemptRecord, ...], BeforeValidator(_tuple_input)]
    resumed_history: tuple[AttemptRecord, ...] | None
    reason: str


class PublicApiProof(_StrictProbeRecord):
    installed_algorithm_methods: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    installed_observable_state: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    documented_checkpoint_transport: Literal["dill algorithm serialization"]
    opaque_transport_allowed: Literal[False]
    transparent_restore_api: None
    checkpoint_documentation: str
    usage_documentation: str


class CheckpointEvidence(_StrictProbeRecord):
    encoding: Literal["canonical-json"]
    snapshot: PublicStateSnapshot
    comparison: ReplayComparison
    public_api_proof: PublicApiProof


class LocFileRecord(_StrictProbeRecord):
    path: str
    sloc: StrictInt


class ProductionLocEvidence(_StrictProbeRecord):
    status: Literal["indeterminate"]
    method: Literal["nonblank non-comment physical Python lines"]
    current_inventory: Annotated[tuple[LocFileRecord, ...], BeforeValidator(_tuple_input)]
    current_sloc: StrictInt
    estimated_reduction_percent: None
    required_reduction_percent: StrictFloat
    gate_passed: Literal[False]
    reason: str


class GateRecord(_StrictProbeRecord):
    known_optima: StrictBool
    deterministic_repeats: StrictBool
    family_fairness: StrictBool
    cache_and_diagnostics: StrictBool
    exact_public_state_replay: StrictBool
    production_loc_reduction: StrictBool


class DecisionRecord(_StrictProbeRecord):
    outcome: Literal["pass", "reject"]
    failed_gates: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    production_changed: Literal[False]
    production_strategy: Literal["basic_generational"]


class PolicyRecord(_StrictProbeRecord):
    production_changed: Literal[False]
    pymoo_version: Literal["0.6.2"]
    family_names: Annotated[tuple[FamilyName, ...], BeforeValidator(_tuple_input)]
    independent_optimizer_per_family: Literal[True]
    categorical_family_variable: Literal[False]
    initial_evaluation_budget: StrictInt
    total_generations: StrictInt
    search_seed: StrictInt
    trial_seeds: Annotated[tuple[StrictInt, ...], BeforeValidator(_tuple_input)]
    champion_seed: StrictInt
    window_seconds: StrictFloat
    generation_limits: GenerationLimits
    invalid_generation_limits: GenerationLimits
    similarity: SimilarityConfig
    cache_key_fields: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    checkpoint_fields: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    replay_checked_fields: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    checkpoint_encoding: Literal["canonical-json"]
    prohibited_serializers: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]
    minimum_loc_reduction_percent: StrictFloat
    known_population_size: StrictInt
    known_generations: StrictInt
    known_tolerances: dict[str, StrictFloat]


class ProbeEvidence(_StrictProbeRecord):
    schema_version: Literal[3]
    probe: Literal["pymoo_optimizer"]
    policy: PolicyRecord
    known_cases: Annotated[tuple[KnownCaseEvidence, KnownCaseEvidence], BeforeValidator(_tuple_input)]
    families: Annotated[
        tuple[FamilyEvidence, FamilyEvidence, FamilyEvidence],
        BeforeValidator(_tuple_input),
    ]
    fairness: FairnessEvidence
    invalid_classification: InvalidClassificationEvidence
    checkpoint: CheckpointEvidence
    production_loc: ProductionLocEvidence
    gates: GateRecord
    decision: DecisionRecord

    @model_validator(mode="after")
    def evidence_sets_are_exact(self) -> Self:
        if tuple(case.name for case in self.known_cases) != (
            "bounded_continuous_sphere",
            "mixed_integer_real_quadratic",
        ):
            raise ValueError("known case set and order must be exact")
        if tuple(item.family for item in self.families) != FAMILY_NAMES:
            raise ValueError("traffic family set and order must be exact")
        return self


@dataclass(frozen=True, slots=True)
class _CachedOutcome:
    candidate: Candidate
    objective: float


@dataclass(frozen=True, slots=True)
class _FamilyExecution:
    algorithm: object
    run: FamilyRunRecord
    checkpoint: PublicStateSnapshot


class _SphereProblem(ElementwiseProblem):
    def __init__(self) -> None:
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            n_var=2,
            n_obj=1,
            xl=np.array((-2.0, -2.0)),
            xu=np.array((2.0, 2.0)),
        )

    def _evaluate(
        self,
        values: np.ndarray[Any, np.dtype[np.float64]],
        out: dict[str, object],
        *args: object,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        out["F"] = float(values[0] ** 2 + values[1] ** 2)


class _MixedKnownProblem(ElementwiseProblem):
    def __init__(self) -> None:
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            vars={"count": Integer(bounds=(0, 6)), "scale": Real(bounds=(0.5, 2.0))}
        )

    def _evaluate(self, values: dict[str, object], out: dict[str, object], *args: object, **kwargs: object) -> None:
        del args, kwargs
        count = int(cast(int, values["count"]))
        scale = float(cast(float, values["scale"]))
        out["F"] = float((count - 3) ** 2 + (scale - 1.25) ** 2)


def _json_value(value: object) -> object:
    if isinstance(value, np.integer):
        return int(cast(Any, value))
    if isinstance(value, np.floating):
        return float(cast(Any, value))
    if isinstance(value, np.ndarray):
        return [_json_value(item) for item in value.tolist()]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in cast(Mapping[object, object], value).items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in cast(Sequence[object], value)]
    return value


def _known_population(algorithm: PymooAlgorithm) -> tuple[KnownPopulationRecord, ...]:
    population = algorithm.pop
    if population is None:
        raise AssertionError("completed pymoo generation has no population")
    output: list[KnownPopulationRecord] = []
    for individual in population:
        raw_variables = _json_value(individual.X)
        if isinstance(raw_variables, list):
            variables: Scalars = {
                "x0": float(cast(Any, raw_variables[0])),
                "x1": float(cast(Any, raw_variables[1])),
            }
        else:
            variables = cast(Scalars, raw_variables)
        output.append(KnownPopulationRecord(variables=variables, objective=float(individual.F[0])))
    return tuple(output)


def _run_known_case(name: str) -> KnownRunRecord:
    history: list[KnownGenerationRecord] = []
    if name == "bounded_continuous_sphere":
        problem: ElementwiseProblem = _SphereProblem()
        algorithm: PymooAlgorithm = _GA(
            pop_size=KNOWN_POPULATION_SIZE,
            sampling=np.asarray(CONTINUOUS_INITIAL_SAMPLES, dtype=np.float64),
            eliminate_duplicates=False,
        )
    elif name == "mixed_integer_real_quadratic":
        problem = _MixedKnownProblem()
        initial = _POPULATION.new(X=np.array(MIXED_INITIAL_SAMPLES, dtype=object))
        algorithm = _MIXED_VARIABLE_GA(
            pop_size=KNOWN_POPULATION_SIZE,
            sampling=initial,
            eliminate_duplicates=False,
        )
    else:
        raise ValueError(f"unknown known-optimum case: {name}")
    algorithm.setup(problem, termination=("n_gen", KNOWN_GENERATIONS), seed=SEARCH_SEED, verbose=False)
    while algorithm.has_next():
        algorithm.next()
        population = _known_population(algorithm)
        history.append(
            KnownGenerationRecord(
                generation=len(history),
                evaluation_count=int(algorithm.evaluator.n_eval),
                minimum_objective=min(item.objective for item in population),
                population=population,
            )
        )
    result = algorithm.result()
    raw_variables = _json_value(result.X)
    if isinstance(raw_variables, list):
        variables: Scalars = {
            "x0": float(cast(Any, raw_variables[0])),
            "x1": float(cast(Any, raw_variables[1])),
        }
    else:
        variables = cast(Scalars, raw_variables)
    return KnownRunRecord(
        variables=variables,
        objective=float(result.F[0]),
        evaluations=int(algorithm.evaluator.n_eval),
        initial_minimum_objective=history[0].minimum_objective,
        history=tuple(history),
    )


def _variable_specs(family: ModelFamily, bounds: FamilyBounds) -> tuple[VariableSpec, ...]:
    output: list[VariableSpec] = []
    for name in family.gene_names:
        bound = getattr(bounds, name)
        if type(bound) is IntegerBounds:
            output.append(VariableSpec(name=name, kind="integer", lower=bound.lower, upper=bound.upper))
        elif type(bound) is FloatBounds:
            output.append(VariableSpec(name=name, kind="real", lower=bound.lower, upper=bound.upper))
        else:
            raise TypeError(f"unsupported bounds for {family.name}.{name}")
    return tuple(output)


def _family_variables(specs: Sequence[VariableSpec]) -> dict[str, PymooVariable]:
    return {
        spec.name: Integer(bounds=(spec.lower, spec.upper))
        if spec.kind == "integer"
        else Real(bounds=(spec.lower, spec.upper))
        for spec in specs
    }


def _genes(family: ModelFamily, values: Mapping[str, Any]) -> Genes:
    output: list[Gene] = []
    for name in family.gene_names:
        value = values[name]
        output.append(int(value) if type(getattr(_FAMILY_BOUNDS[family.name], name)) is IntegerBounds else float(value))
    return tuple(output)


def _cache_payload(family: FamilyName, genes: Genes, context: ValidatedEvaluationContext) -> CacheKeyRecord:
    return CacheKeyRecord(
        family=family,
        genes=genes,
        observation_window_seconds=context.window,
        trial_seeds=context.trial_seeds,
        generation_limits=context.trial_limits,
        similarity=context.similarity,
    )


def _render_cache_key(payload: CacheKeyRecord) -> str:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)


class _TrafficProblem(ElementwiseProblem):
    def __init__(
        self,
        family: ModelFamily,
        context: ValidatedEvaluationContext,
        *,
        population_size: int,
    ) -> None:
        self.family = family
        self.context = context
        self.population_size = population_size
        self.cache: dict[str, _CachedOutcome] = {}
        self.history: list[AttemptRecord] = []
        specs = _variable_specs(family, context.bounds[family.name])
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            vars=_family_variables(specs)
        )

    def _evaluate(self, values: dict[str, Any], out: dict[str, object], *args: object, **kwargs: object) -> None:
        del args, kwargs
        evaluation_index = len(self.history) + 1
        generation = (evaluation_index - 1) // self.population_size
        birth_index = (evaluation_index - 1) % self.population_size
        identifier = CandidateId(birth_generation=generation, birth_index=birth_index)
        genes = _genes(self.family, values)
        payload = _cache_payload(self.family.name, genes, self.context)
        key = _render_cache_key(payload)
        cached = self.cache.get(key)
        cache_hit = cached is not None
        if cached is None:
            pending = Candidate(
                identifier=identifier,
                family=self.family.name,
                genes=genes,
                status="pending",
                fitness=0.0,
                trials=(),
                invalid=None,
                duplicate_diagnostics=(),
            )
            evaluated = evaluate_candidate(pending, self.context)
            cached = _CachedOutcome(candidate=evaluated, objective=1.0 - evaluated.fitness)
            self.cache[key] = cached
        attempt_candidate = rebuild_genetic_record(cached.candidate, identifier=identifier)
        self.history.append(
            AttemptRecord(
                evaluation_index=evaluation_index,
                generation=generation,
                candidate=attempt_candidate,
                objective=cached.objective,
                cache_key_payload=payload,
                cache_key=key,
                cache_hit=cache_hit,
            )
        )
        out["F"] = cached.objective


def _evaluation_context(limits: GenerationLimits = GENERATION_LIMITS) -> ValidatedEvaluationContext:
    families: dict[FamilyName, ModelFamily] = {name: REGISTRY[name] for name in FAMILY_NAMES}
    return validate_evaluation_context(
        EvaluationContext(
            reference=_REFERENCE,
            window=WINDOW_SECONDS,
            families=families,
            bounds=_FAMILY_BOUNDS,
            trial_seeds=TRIAL_SEEDS,
            trial_limits=limits,
            similarity=SIMILARITY,
        )
    )


def _public_population(population: PymooPopulation) -> tuple[PublicPopulationState, ...]:
    return tuple(
        PublicPopulationState(
            variables=cast(Scalars, _json_value(individual.X)),
            objectives=tuple(float(item) for item in individual.F),
            status=tuple(sorted(str(item) for item in individual.evaluated)),
        )
        for individual in population
    )


def _qualified_name(value: object) -> str:
    owner = value if isinstance(value, type) else type(value)
    return f"{owner.__module__}.{owner.__name__}"


def _operator_scalar(value: object) -> int | float | bool | None:
    dynamic_value = cast(Any, value)
    resolved: Any = dynamic_value.get() if hasattr(value, "get") else value
    if resolved is None or type(resolved) in {int, float, bool}:
        return cast(int | float | bool | None, resolved)
    if isinstance(resolved, np.integer):
        return int(cast(Any, resolved))
    if isinstance(resolved, np.floating):
        return float(cast(Any, resolved))
    raise TypeError("public pymoo operator setting must be a deterministic scalar")


def _public_type_name(value: object) -> str | None:
    return None if value is None else _qualified_name(value)


def _repair_settings(value: object) -> RepairSettings | None:
    if value is None:
        return None
    dynamic = cast(Any, value)
    return RepairSettings(
        operator_class=_qualified_name(value),
        name=str(dynamic.name),
        vtype=_public_type_name(dynamic.vtype),
        repair=_public_type_name(dynamic.repair),
    )


def _operator_settings(variable_type: type[object], operator: object, *, mutation: bool) -> OperatorSettings:
    dynamic = cast(Any, operator)
    names = ["prob"]
    operator_name = type(operator).__name__
    if mutation:
        names.append("prob_var")
        if operator_name == "PM":
            names.extend(("eta", "at_least_once"))
    else:
        names.extend(("n_parents", "n_offsprings"))
        if operator_name == "SBX":
            names.extend(("prob_var", "eta", "prob_bin", "prob_exch"))
    settings = {name: _operator_scalar(getattr(operator, name)) for name in names}
    return OperatorSettings(
        variable_type=_qualified_name(variable_type),
        operator_class=_qualified_name(operator),
        settings=settings,
        vtype=_public_type_name(dynamic.vtype),
        repair=_repair_settings(dynamic.repair),
    )


def _mating_settings(algorithm: PymooAlgorithm) -> MatingSettings:
    mating = algorithm.mating
    crossover = cast(Mapping[type[object], object], mating.crossover)
    mutation = cast(Mapping[type[object], object], mating.mutation)
    return MatingSettings(
        selection=cast(Literal["pymoo.operators.selection.rnd.RandomSelection"], _qualified_name(mating.selection)),
        repair=cast(Literal["pymoo.core.repair.NoRepair"], _qualified_name(mating.repair)),
        duplicate_elimination=MatingDuplicateSettings(
            operator_class=cast(
                Literal["pymoo.core.mixed.MixedVariableDuplicateElimination"],
                _qualified_name(mating.eliminate_duplicates),
            ),
            epsilon=float(mating.eliminate_duplicates.epsilon),
        ),
        n_max_iterations=int(mating.n_max_iterations),
        crossover=cast(
            tuple[OperatorSettings, OperatorSettings, OperatorSettings, OperatorSettings],
            tuple(_operator_settings(kind, operator, mutation=False) for kind, operator in crossover.items()),
        ),
        mutation=cast(
            tuple[OperatorSettings, OperatorSettings, OperatorSettings, OperatorSettings],
            tuple(_operator_settings(kind, operator, mutation=True) for kind, operator in mutation.items()),
        ),
    )


def _public_snapshot(
    algorithm: PymooAlgorithm,
    *,
    family: ModelFamily,
    specs: tuple[VariableSpec, ...],
    initial_sampling: tuple[Scalars, ...],
) -> PublicStateSnapshot:
    population = algorithm.pop
    termination = algorithm.termination
    random_state = algorithm.random_state
    if population is None or termination is None or random_state is None:
        raise AssertionError("pymoo public state is incomplete after a generation")
    return PublicStateSnapshot(
        complete=False,
        missing_fields=REPLAY_MISSING_FIELDS,
        population=_public_population(population),
        generation=cast(int, algorithm.n_gen),
        evaluation_count=int(algorithm.evaluator.n_eval),
        termination=PublicTerminationState(
            kind="MaximumGenerationTermination",
            progress=float(termination.perc),
            has_terminated=bool(termination.has_terminated()),
        ),
        configuration=PublicAlgorithmConfiguration(
            algorithm="pymoo.core.mixed.MixedVariableGA",
            family=family.name,
            search_seed=SEARCH_SEED,
            variables=specs,
            initial_sampling=initial_sampling,
            constructor=ConstructorSettings(
                pop_size=INITIAL_EVALUATION_BUDGET,
                n_offsprings=None,
                sampling="pymoo.core.population.Population",
                mating="pymoo.core.mixed.MixedVariableMating",
                eliminate_duplicates=False,
                survival="pymoo.algorithms.soo.nonconvex.ga.FitnessSurvival",
                output="pymoo.util.display.single.SingleObjectiveOutput",
                callback="pymoo.core.callback.Callback",
                display="pymoo.util.display.display.Display",
                archive=None,
                return_least_infeasible=False,
                save_history=False,
                verbose=False,
                evaluator="pymoo.core.evaluator.Evaluator",
                advance_after_initial_infill=False,
                algorithm_duplicate_elimination="pymoo.core.duplicate.NoDuplicateElimination",
            ),
            initialization=InitializationSettings(
                sampling=cast(
                    Literal["pymoo.core.population.Population"],
                    _qualified_name(algorithm.initialization.sampling),
                ),
                repair=cast(
                    Literal["pymoo.core.repair.NoRepair"],
                    _qualified_name(algorithm.initialization.repair),
                ),
                duplicate_elimination=cast(
                    Literal["pymoo.core.duplicate.NoDuplicateElimination"],
                    _qualified_name(algorithm.initialization.eliminate_duplicates),
                ),
            ),
            algorithm_repair=cast(RepairSettings, _repair_settings(algorithm.repair)),
            mating=_mating_settings(algorithm),
            termination=TerminationSettings(kind="n_gen", value=TOTAL_GENERATIONS),
        ),
        pymoo_version=cast(Literal["0.6.2"], pymoo.__version__),
        rng=PublicRngState(
            engine=cast(Literal["PCG64"], type(random_state.bit_generator).__name__),
            state=RngState.model_validate(_json_value(random_state.bit_generator.state)),
        ),
    )


def _run_family(family_name: FamilyName, context: ValidatedEvaluationContext) -> _FamilyExecution:
    family = context.families[family_name]
    specs = _variable_specs(family, context.bounds[family_name])
    initial_sampling: tuple[Scalars, ...] = tuple(dict(item) for item in _INITIAL_VALUES[family_name])
    initial = _POPULATION.new(X=np.array(initial_sampling, dtype=object))
    problem = _TrafficProblem(family, context, population_size=INITIAL_EVALUATION_BUDGET)
    algorithm: PymooAlgorithm = _MIXED_VARIABLE_GA(
        pop_size=INITIAL_EVALUATION_BUDGET,
        sampling=initial,
        eliminate_duplicates=False,
    )
    algorithm.setup(problem, termination=("n_gen", TOTAL_GENERATIONS), seed=SEARCH_SEED, verbose=False)
    snapshot: PublicStateSnapshot | None = None
    while algorithm.has_next():
        algorithm.next()
        if int(algorithm.evaluator.n_eval) == INITIAL_EVALUATION_BUDGET:
            snapshot = _public_snapshot(
                algorithm,
                family=family,
                specs=specs,
                initial_sampling=initial_sampling,
            )
    if snapshot is None:
        raise AssertionError("pymoo run did not expose the initial evaluated generation")
    history = tuple(problem.history)
    best = min(problem.cache.values(), key=lambda item: (item.objective, item.candidate.identifier)).candidate
    run = FamilyRunRecord(
        optimizer_instance=0,
        optimizer_class="pymoo.core.mixed.MixedVariableGA",
        optimizer_config_alias=f"pymoo.MixedVariableGA:{family_name}",
        family=family_name,
        search_seed=SEARCH_SEED,
        observation_window_seconds=WINDOW_SECONDS,
        trial_seeds=TRIAL_SEEDS,
        generation_limits=GENERATION_LIMITS,
        similarity=SIMILARITY,
        variables=specs,
        initial_sampling=initial_sampling,
        initial_evaluations=INITIAL_EVALUATION_BUDGET,
        total_attempts=len(history),
        objective_evaluations=sum(not item.cache_hit for item in history),
        cache_hits=sum(item.cache_hit for item in history),
        history=history,
        best_candidate=best,
    )
    return _FamilyExecution(algorithm=algorithm, run=run, checkpoint=snapshot)


def _run_invalid_adapter() -> InvalidClassificationEvidence:
    context = _evaluation_context(INVALID_CASE_LIMITS)
    family = context.families["poisson_empirical"]
    problem = _TrafficProblem(family, context, population_size=1)
    initial = _POPULATION.new(X=np.array(({"c_lambda": 1.0},), dtype=object))
    algorithm: PymooAlgorithm = _MIXED_VARIABLE_GA(pop_size=1, sampling=initial, eliminate_duplicates=False)
    algorithm.setup(problem, termination=("n_gen", 1), seed=SEARCH_SEED, verbose=False)
    algorithm.next()
    return InvalidClassificationEvidence(
        execution="pymoo.MixedVariableGA.next",
        family="poisson_empirical",
        limits=INVALID_CASE_LIMITS,
        pymoo_evaluations=int(algorithm.evaluator.n_eval),
        history=cast(tuple[AttemptRecord], tuple(problem.history)),
    )


def _champion(execution: _FamilyExecution, context: ValidatedEvaluationContext) -> ChampionRecord:
    trials = evaluate_final(execution.run.best_candidate, context, CHAMPION_SEED)
    return ChampionRecord(
        family=execution.run.family,
        candidate=execution.run.best_candidate,
        fresh_seed=CHAMPION_SEED,
        fresh_fitness=trials[0].aggregate_score,
        search_attempts_completed=execution.run.total_attempts,
        trials=trials,
    )


def _count_sloc(path: Path) -> int:
    return sum(bool(line.strip()) and not line.lstrip().startswith("#") for line in path.read_text().splitlines())


def _loc_inventory() -> ProductionLocEvidence:
    directory = Path(__file__).resolve().parents[3] / "src" / "trafficlab" / "genetic"
    inventory = tuple(LocFileRecord(path=path.name, sloc=_count_sloc(path)) for path in sorted(directory.glob("*.py")))
    return ProductionLocEvidence(
        status="indeterminate",
        method="nonblank non-comment physical Python lines",
        current_inventory=inventory,
        current_sloc=sum(item.sloc for item in inventory),
        estimated_reduction_percent=None,
        required_reduction_percent=MINIMUM_LOC_REDUCTION_PERCENT,
        gate_passed=False,
        reason=(
            "exact line-level retained/removable attribution requires designing the rejected adapter; "
            "no defensible production reduction estimate exists after the replay rejection"
        ),
    )


def _expected_mating_settings() -> MatingSettings:
    algorithm = _MIXED_VARIABLE_GA(
        pop_size=INITIAL_EVALUATION_BUDGET,
        eliminate_duplicates=False,
    )
    return _mating_settings(algorithm)


def _expected_algorithm_repair() -> RepairSettings:
    algorithm = _MIXED_VARIABLE_GA(
        pop_size=INITIAL_EVALUATION_BUDGET,
        eliminate_duplicates=False,
    )
    return cast(RepairSettings, _repair_settings(algorithm.repair))


def _known_objective(case_name: str, variables: Mapping[str, int | float]) -> float:
    if case_name == "bounded_continuous_sphere":
        return float(variables["x0"]) ** 2 + float(variables["x1"]) ** 2
    return (int(variables["count"]) - 3) ** 2 + (float(variables["scale"]) - 1.25) ** 2


def attempt_history_is_complete(run: FamilyRunRecord) -> bool:
    """Recompute sequential identity, cache, and generation accounting for one run."""
    history = run.history
    if len(history) != run.total_attempts:
        return False
    if tuple(item.evaluation_index for item in history) != tuple(range(1, run.total_attempts + 1)):
        return False
    if any(item.generation != (item.evaluation_index - 1) // run.initial_evaluations for item in history):
        return False
    identifiers = tuple(
        (item.candidate.identifier.birth_generation, item.candidate.identifier.birth_index) for item in history
    )
    expected_ids = tuple(
        ((index - 1) // run.initial_evaluations, (index - 1) % run.initial_evaluations)
        for index in range(1, run.total_attempts + 1)
    )
    if identifiers != expected_ids:
        return False
    actual_hits = sum(item.cache_hit for item in history)
    if actual_hits != run.cache_hits:
        return False
    if run.objective_evaluations != run.total_attempts - actual_hits:
        return False
    misses: dict[str, tuple[JsonObject, float]] = {}
    for item in history:
        if item.cache_key != _render_cache_key(item.cache_key_payload):
            return False
        payload = item.cache_key_payload
        candidate = item.candidate
        if not (
            payload.family == run.family == candidate.family
            and candidate.genes is not None
            and payload.genes == candidate.genes
            and payload.observation_window_seconds == run.observation_window_seconds
            and payload.trial_seeds == run.trial_seeds
            and payload.generation_limits == run.generation_limits
            and payload.similarity == run.similarity
        ):
            return False
        if candidate.status == "valid":
            if not (
                candidate.invalid is None
                and tuple(trial.seed for trial in candidate.trials) == run.trial_seeds
                and item.objective == 1.0 - candidate.fitness
            ):
                return False
        elif candidate.status == "invalid":
            if not (
                candidate.invalid is not None
                and candidate.fitness == 0.0
                and not candidate.trials
                and item.objective == INVALID_OBJECTIVE
            ):
                return False
        else:
            return False
        scientific_result = (
            item.candidate.model_dump(mode="json", exclude={"identifier"}),
            item.objective,
        )
        if item.cache_hit:
            if misses.get(item.cache_key) != scientific_result:
                return False
        else:
            if item.cache_key in misses:
                return False
            misses[item.cache_key] = scientific_result
    return True


def _expected_known_initial(name: str) -> tuple[Scalars, ...]:
    if name == "bounded_continuous_sphere":
        return tuple({"x0": left, "x1": right} for left, right in CONTINUOUS_INITIAL_SAMPLES)
    return tuple(dict(item) for item in MIXED_INITIAL_SAMPLES)


def _known_metadata_is_exact(case: KnownCaseEvidence) -> bool:
    specification = next(item for item in KNOWN_CASES if item["name"] == case.name)
    expected_definition = "sum_squares" if case.name == "bounded_continuous_sphere" else "integer_real_quadratic"
    return (
        case.objective_definition == expected_definition
        and case.seed == SEARCH_SEED
        and case.variable_kinds == specification["variable_kinds"]
        and case.bounds == specification["bounds"]
        and case.known_optimum.model_dump(mode="json") == specification["known_optimum"]
        and case.known_optimum.objective == _known_objective(case.name, case.known_optimum.variables)
        and case.tolerance == KNOWN_TOLERANCES[case.name]
        and case.population_size == KNOWN_POPULATION_SIZE
        and case.generations == KNOWN_GENERATIONS
        and case.initial_sampling == _expected_known_initial(case.name)
    )


def _known_variables_are_valid(case: KnownCaseEvidence, variables: Scalars) -> bool:
    if set(variables) != set(case.bounds):
        return False
    for name, value in variables.items():
        lower, upper = case.bounds[name]
        kind = case.variable_kinds[name]
        if kind == "integer" and type(value) is not int:
            return False
        if kind == "real" and type(value) is not float:
            return False
        if not lower <= value <= upper:
            return False
    return True


def _known_gate(evidence: ProbeEvidence) -> bool:
    for case in evidence.known_cases:
        if not _known_metadata_is_exact(case):
            return False
        declared_initial_objectives = tuple(_known_objective(case.name, item) for item in case.initial_sampling)
        declared_initial_minimum = min(declared_initial_objectives)
        for run in case.runs:
            objective = _known_objective(case.name, run.variables)
            observed_initial = run.history[0].population
            generations_valid = True
            for index, generation in enumerate(run.history):
                recomputed = tuple(_known_objective(case.name, item.variables) for item in generation.population)
                generations_valid = generations_valid and (
                    generation.generation == index
                    and generation.evaluation_count == (index + 1) * case.population_size
                    and len(generation.population) == case.population_size
                    and all(_known_variables_are_valid(case, item.variables) for item in generation.population)
                    and tuple(item.objective for item in generation.population) == recomputed
                    and generation.minimum_objective == min(recomputed)
                )
            if not (
                declared_initial_minimum > case.tolerance
                and run.initial_minimum_objective == declared_initial_minimum
                and tuple(item.variables for item in observed_initial) == case.initial_sampling
                and tuple(item.objective for item in observed_initial) == declared_initial_objectives
                and run.objective <= case.tolerance
                and run.objective == objective
                and _known_variables_are_valid(case, run.variables)
                and run.evaluations == case.population_size * case.generations
                and len(run.history) == case.generations
                and run.history[0].minimum_objective == run.initial_minimum_objective
                and generations_valid
                and run.objective == run.history[-1].minimum_objective
                and any(
                    item.variables == run.variables and item.objective == run.objective
                    for item in run.history[-1].population
                )
            ):
                return False
    return True


def _repeat_gate(evidence: ProbeEvidence) -> bool:
    known_equal = all(case.runs[0] == case.runs[1] for case in evidence.known_cases)
    family_equal = all(
        family.runs[0].model_dump(mode="json", exclude={"optimizer_instance"})
        == family.runs[1].model_dump(mode="json", exclude={"optimizer_instance"})
        for family in evidence.families
    )
    return known_equal and family_equal


def _fairness_gate(evidence: ProbeEvidence) -> bool:
    first_runs = tuple(family.runs[0] for family in evidence.families)
    all_runs = tuple(run for family in evidence.families for run in family.runs)
    fairness = evidence.fairness
    controls_match = all(
        run.search_seed == evidence.policy.search_seed
        and run.observation_window_seconds == evidence.policy.window_seconds
        and run.trial_seeds == evidence.policy.trial_seeds
        and run.generation_limits == evidence.policy.generation_limits
        and run.similarity == evidence.policy.similarity
        and run.initial_evaluations == evidence.policy.initial_evaluation_budget
        and run.total_attempts == evidence.policy.initial_evaluation_budget * evidence.policy.total_generations
        and run.optimizer_config_alias == f"pymoo.MixedVariableGA:{run.family}"
        and attempt_history_is_complete(run)
        for run in all_runs
    )
    family_configs_match = all(
        family.runs[0].variables == family.runs[1].variables
        and family.runs[0].initial_sampling == family.runs[1].initial_sampling
        and all(
            run.variables == _variable_specs(REGISTRY[family.family], _FAMILY_BOUNDS[family.family])
            and run.initial_sampling == tuple(dict(item) for item in _INITIAL_VALUES[family.family])
            for run in family.runs
        )
        for family in evidence.families
    )
    instances = len({run.optimizer_instance for run in all_runs})
    canonical_instances = all(
        tuple(run.optimizer_instance for run in family.runs) == (family_index + 1, family_index + 4)
        for family_index, family in enumerate(evidence.families)
    )
    champions_match = tuple(item.family for item in fairness.champion_comparison) == FAMILY_NAMES
    for family, champion in zip(evidence.families, fairness.champion_comparison, strict=True):
        first_run = family.runs[0]
        expected_attempt = min(
            first_run.history,
            key=lambda item: (item.objective, item.candidate.identifier),
        )
        champions_match = champions_match and (
            all(
                run.best_candidate
                == min(run.history, key=lambda item: (item.objective, item.candidate.identifier)).candidate
                for run in family.runs
            )
            and champion.candidate == expected_attempt.candidate
            and champion.candidate.status == "valid"
            and champion.fresh_seed == evidence.policy.champion_seed
            and champion.search_attempts_completed == first_run.total_attempts
            and len(champion.trials) == 1
            and champion.trials[0].seed == evidence.policy.champion_seed
            and champion.fresh_fitness == champion.trials[0].aggregate_score
        )
    ranking = tuple(
        item.family
        for item in sorted(
            fairness.champion_comparison,
            key=lambda item: (-item.fresh_fitness, FAMILY_NAMES.index(item.family)),
        )
    )
    winner = next(item for item in fairness.champion_comparison if item.family == ranking[0])
    return (
        tuple(item.family for item in evidence.families) == FAMILY_NAMES
        and fairness.measured_family_set == FAMILY_NAMES
        and instances == len(all_runs)
        and canonical_instances
        and fairness.distinct_optimizer_instances == instances
        and fairness.common_search_seed == first_runs[0].search_seed
        and fairness.common_trial_seeds == first_runs[0].trial_seeds
        and fairness.common_champion_seed == fairness.champion_comparison[0].fresh_seed
        and fairness.common_window_seconds == first_runs[0].observation_window_seconds
        and fairness.common_generation_limits == first_runs[0].generation_limits
        and fairness.common_similarity == first_runs[0].similarity
        and controls_match
        and family_configs_match
        and fairness.equal_initial_budget == INITIAL_EVALUATION_BUDGET
        and fairness.equal_total_budget == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS
        and fairness.cache_key_fields == CACHE_KEY_FIELDS
        and fairness.champions_compared_after_attempts == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS
        and champions_match
        and fairness.champion_ranking == ranking
        and fairness.winner_family == ranking[0]
        and fairness.winner == winner
    )


def _cache_diagnostics_gate(evidence: ProbeEvidence) -> bool:
    runs = tuple(run for family in evidence.families for run in family.runs)
    histories_complete = all(attempt_history_is_complete(run) for run in runs)
    candidates_complete = all(
        attempt.candidate.family == run.family
        and attempt.cache_key_payload.family == run.family
        and (attempt.candidate.status == "invalid" or len(attempt.candidate.trials) == len(evidence.policy.trial_seeds))
        for run in runs
        for attempt in run.history
    )
    invalid = evidence.invalid_classification
    invalid_attempt = invalid.history[0]
    invalid_adapter = (
        invalid.execution == "pymoo.MixedVariableGA.next"
        and invalid.limits == evidence.policy.invalid_generation_limits
        and invalid.pymoo_evaluations == 1
        and len(invalid.history) == 1
        and invalid_attempt.evaluation_index == 1
        and invalid_attempt.generation == 0
        and invalid_attempt.candidate.identifier == CandidateId(birth_generation=0, birth_index=0)
        and invalid_attempt.cache_key_payload.family == invalid.family == invalid_attempt.candidate.family
        and invalid_attempt.candidate.genes is not None
        and invalid_attempt.cache_key_payload.genes == invalid_attempt.candidate.genes
        and invalid_attempt.cache_key_payload.observation_window_seconds == evidence.policy.window_seconds
        and invalid_attempt.cache_key_payload.trial_seeds == evidence.policy.trial_seeds
        and invalid_attempt.cache_key_payload.generation_limits == invalid.limits
        and invalid_attempt.cache_key_payload.similarity == evidence.policy.similarity
        and invalid_attempt.cache_key == _render_cache_key(invalid_attempt.cache_key_payload)
        and not invalid_attempt.cache_hit
        and invalid_attempt.candidate.status == "invalid"
        and invalid_attempt.candidate.invalid is not None
        and invalid_attempt.candidate.fitness == 0.0
        and not invalid_attempt.candidate.trials
        and invalid_attempt.candidate.invalid.kind == "incomplete_generation"
        and invalid_attempt.candidate.invalid.authority == "primary"
        and invalid_attempt.candidate.invalid.seed == evidence.policy.trial_seeds[0]
        and invalid_attempt.objective == INVALID_OBJECTIVE
    )
    return histories_complete and candidates_complete and invalid_adapter


def _checkpoint_fields_are_linked(evidence: ProbeEvidence) -> bool:
    policy = evidence.policy
    snapshot = evidence.checkpoint.snapshot
    first_run = evidence.families[0].runs[0]
    configuration = snapshot.configuration
    initial_attempts = first_run.history[: policy.initial_evaluation_budget]
    expected_population = tuple(
        PublicPopulationState(
            variables={
                spec.name: value
                for spec, value in zip(first_run.variables, attempt.cache_key_payload.genes, strict=True)
            },
            objectives=(attempt.objective,),
            status=("F", "G", "H"),
        )
        for attempt in initial_attempts
    )
    initial_sections_linked = (
        len(initial_attempts) == policy.initial_evaluation_budget
        and tuple(attempt.generation for attempt in initial_attempts) == (0,) * policy.initial_evaluation_budget
        and len(snapshot.population) == len(configuration.initial_sampling) == policy.initial_evaluation_budget
        and configuration.initial_sampling == tuple(item.variables for item in expected_population)
        and snapshot.population == expected_population
    )
    return (
        initial_sections_linked
        and snapshot.generation == 2
        and snapshot.evaluation_count == policy.initial_evaluation_budget
        and snapshot.termination.kind == "MaximumGenerationTermination"
        and snapshot.termination.progress == 1.0 / policy.total_generations
        and not snapshot.termination.has_terminated
        and configuration.constructor.pop_size == policy.initial_evaluation_budget
        and len(configuration.initial_sampling) == configuration.constructor.pop_size
        and len(configuration.variables) == len(first_run.variables)
    )


def semantic_root_is_consistent(evidence: ProbeEvidence) -> bool:
    """Cross-check every strict section against canonical policy and measured peers."""
    first_run = evidence.families[0].runs[0]
    snapshot = evidence.checkpoint.snapshot
    configuration = snapshot.configuration
    comparison = evidence.checkpoint.comparison
    return (
        evidence.policy == _policy()
        and all(_known_metadata_is_exact(case) for case in evidence.known_cases)
        and comparison.checked_fields == REPLAY_CHECKED_FIELDS
        and not snapshot.complete
        and snapshot.missing_fields == REPLAY_MISSING_FIELDS
        and not comparison.exact_history_identity
        and comparison.resumed_history is None
        and comparison.uninterrupted_history == first_run.history
        and configuration.family == first_run.family
        and configuration.search_seed == first_run.search_seed
        and configuration.variables == first_run.variables
        and configuration.initial_sampling == first_run.initial_sampling
        and configuration.mating == _expected_mating_settings()
        and configuration.algorithm_repair == _expected_algorithm_repair()
        and _checkpoint_fields_are_linked(evidence)
        and evidence.invalid_classification.limits == evidence.policy.invalid_generation_limits
        and evidence.production_loc == _loc_inventory()
    )


def derive_gates(evidence: ProbeEvidence) -> GateRecord:
    """Derive every adoption gate exclusively from strict measured evidence."""
    comparison = evidence.checkpoint.comparison
    replay = (
        evidence.checkpoint.snapshot.complete
        and not evidence.checkpoint.snapshot.missing_fields
        and comparison.exact_history_identity
        and comparison.resumed_history is not None
        and comparison.resumed_history == comparison.uninterrupted_history
    )
    loc = evidence.production_loc
    loc_pass = (
        loc.status != "indeterminate"
        and loc.estimated_reduction_percent is not None
        and loc.estimated_reduction_percent >= loc.required_reduction_percent
    )
    return GateRecord(
        known_optima=_known_gate(evidence),
        deterministic_repeats=_repeat_gate(evidence),
        family_fairness=_fairness_gate(evidence),
        cache_and_diagnostics=_cache_diagnostics_gate(evidence),
        exact_public_state_replay=replay,
        production_loc_reduction=loc_pass,
    )


def decide_probe(gates: Mapping[str, object]) -> JsonObject:
    """Apply the immutable all-gates adoption rule without changing production."""
    unknown = sorted(set(gates) - set(GATE_NAMES))
    failed = (
        [f"unknown:{name}" for name in unknown]
        if unknown
        else [name for name in GATE_NAMES if type(gates.get(name)) is not bool or not gates[name]]
    )
    return {
        "outcome": "pass" if not failed else "reject",
        "failed_gates": failed,
        "production_changed": False,
        "production_strategy": "basic_generational",
    }


def _policy() -> PolicyRecord:
    return PolicyRecord(
        production_changed=False,
        pymoo_version=cast(Literal["0.6.2"], pymoo.__version__),
        family_names=FAMILY_NAMES,
        independent_optimizer_per_family=True,
        categorical_family_variable=False,
        initial_evaluation_budget=INITIAL_EVALUATION_BUDGET,
        total_generations=TOTAL_GENERATIONS,
        search_seed=SEARCH_SEED,
        trial_seeds=TRIAL_SEEDS,
        champion_seed=CHAMPION_SEED,
        window_seconds=WINDOW_SECONDS,
        generation_limits=GENERATION_LIMITS,
        invalid_generation_limits=INVALID_CASE_LIMITS,
        similarity=SIMILARITY,
        cache_key_fields=CACHE_KEY_FIELDS,
        checkpoint_fields=CHECKPOINT_FIELDS,
        replay_checked_fields=REPLAY_CHECKED_FIELDS,
        checkpoint_encoding="canonical-json",
        prohibited_serializers=PROHIBITED_SERIALIZERS,
        minimum_loc_reduction_percent=MINIMUM_LOC_REDUCTION_PERCENT,
        known_population_size=KNOWN_POPULATION_SIZE,
        known_generations=KNOWN_GENERATIONS,
        known_tolerances=KNOWN_TOLERANCES,
    )


def _known_evidence() -> tuple[KnownCaseEvidence, KnownCaseEvidence]:
    output: list[KnownCaseEvidence] = []
    samples = (CONTINUOUS_INITIAL_SAMPLES, MIXED_INITIAL_SAMPLES)
    for specification, initial in zip(KNOWN_CASES, samples, strict=True):
        name = specification["name"]
        run_one = _run_known_case(name)
        run_two = _run_known_case(name)
        normalized_initial = tuple(
            {"x0": item[0], "x1": item[1]} if isinstance(item, tuple) else dict(item) for item in initial
        )
        output.append(
            KnownCaseEvidence(
                name=cast(Any, name),
                objective_definition=(
                    "sum_squares" if name == "bounded_continuous_sphere" else "integer_real_quadratic"
                ),
                seed=SEARCH_SEED,
                variable_kinds=cast(Any, specification["variable_kinds"]),
                bounds=cast(Any, specification["bounds"]),
                known_optimum=KnownOptimumRecord.model_validate(specification["known_optimum"]),
                tolerance=KNOWN_TOLERANCES[name],
                population_size=KNOWN_POPULATION_SIZE,
                generations=KNOWN_GENERATIONS,
                initial_sampling=normalized_initial,
                runs=(run_one, run_two),
            )
        )
    return cast(tuple[KnownCaseEvidence, KnownCaseEvidence], tuple(output))


def _checkpoint(execution: _FamilyExecution) -> CheckpointEvidence:
    return CheckpointEvidence(
        encoding="canonical-json",
        snapshot=execution.checkpoint,
        comparison=ReplayComparison(
            exact_history_identity=False,
            checked_fields=REPLAY_CHECKED_FIELDS,
            uninterrupted_history=execution.run.history,
            resumed_history=None,
            reason=(
                "pymoo 0.6.2 documents ask/tell observation but no transparent restore API; "
                "its checkpoint guide restores only a serialized whole Algorithm object"
            ),
        ),
        public_api_proof=PublicApiProof(
            installed_algorithm_methods=("setup", "has_next", "next", "ask", "tell", "result"),
            installed_observable_state=("pop", "n_gen", "evaluator.n_eval", "termination", "random_state"),
            documented_checkpoint_transport="dill algorithm serialization",
            opaque_transport_allowed=False,
            transparent_restore_api=None,
            checkpoint_documentation="https://pymoo.org/misc/checkpoint.html",
            usage_documentation="https://pymoo.org/algorithms/usage.html",
        ),
    )


def build_probe_evidence() -> JsonObject:
    """Execute every bounded optimizer gate and return strict canonical evidence."""
    known = _known_evidence()
    context = _evaluation_context()
    first = [_run_family(name, context) for name in FAMILY_NAMES]
    second = [_run_family(name, context) for name in FAMILY_NAMES]
    all_executions = (*first, *second)
    identity_ordinals = {id(item.algorithm): index + 1 for index, item in enumerate(all_executions)}
    families: list[FamilyEvidence] = []
    for first_run, second_run in zip(first, second, strict=True):
        run_one = first_run.run.model_copy(
            update={"optimizer_instance": identity_ordinals[id(first_run.algorithm)]},
        )
        run_two = second_run.run.model_copy(
            update={"optimizer_instance": identity_ordinals[id(second_run.algorithm)]},
        )
        families.append(FamilyEvidence(family=run_one.family, runs=(run_one, run_two)))
    family_tuple = cast(tuple[FamilyEvidence, FamilyEvidence, FamilyEvidence], tuple(families))
    champions = tuple(_champion(item, context) for item in first)
    winner = max(
        champions,
        key=lambda item: (item.fresh_fitness, -FAMILY_NAMES.index(item.family)),
    )
    ranking = cast(
        tuple[FamilyName, ...],
        tuple(
            item.family
            for item in sorted(
                champions,
                key=lambda item: (-item.fresh_fitness, FAMILY_NAMES.index(item.family)),
            )
        ),
    )
    fairness = FairnessEvidence(
        measured_family_set=tuple(item.family for item in family_tuple),
        distinct_optimizer_instances=len({run.optimizer_instance for item in family_tuple for run in item.runs}),
        common_search_seed=family_tuple[0].runs[0].search_seed,
        common_trial_seeds=family_tuple[0].runs[0].trial_seeds,
        common_champion_seed=champions[0].fresh_seed,
        common_window_seconds=family_tuple[0].runs[0].observation_window_seconds,
        common_generation_limits=family_tuple[0].runs[0].generation_limits,
        common_similarity=family_tuple[0].runs[0].similarity,
        equal_initial_budget=min(item.runs[0].initial_evaluations for item in family_tuple),
        equal_total_budget=min(item.runs[0].total_attempts for item in family_tuple),
        cache_key_fields=CACHE_KEY_FIELDS,
        champions_compared_after_attempts=min(item.search_attempts_completed for item in champions),
        champion_comparison=champions,
        champion_ranking=ranking,
        winner_family=winner.family,
        winner=winner,
    )
    placeholder = GateRecord(
        known_optima=False,
        deterministic_repeats=False,
        family_fairness=False,
        cache_and_diagnostics=False,
        exact_public_state_replay=False,
        production_loc_reduction=False,
    )
    provisional = ProbeEvidence(
        schema_version=3,
        probe="pymoo_optimizer",
        policy=_policy(),
        known_cases=known,
        families=family_tuple,
        fairness=fairness,
        invalid_classification=_run_invalid_adapter(),
        checkpoint=_checkpoint(first[0]),
        production_loc=_loc_inventory(),
        gates=placeholder,
        decision=DecisionRecord(
            outcome="reject",
            failed_gates=GATE_NAMES,
            production_changed=False,
            production_strategy="basic_generational",
        ),
    )
    gates = derive_gates(provisional)
    decision = DecisionRecord.model_validate(decide_probe(gates.model_dump(mode="python")))
    evidence = provisional.model_copy(update={"gates": gates, "decision": decision})
    return evidence.model_dump(mode="json")


def validate_probe_evidence(evidence: JsonObject) -> JsonObject:
    """Strictly parse every section and recompute both gates and decision."""
    try:
        parsed = ProbeEvidence.model_validate(evidence)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid strict pymoo probe evidence: {error}") from error
    if not semantic_root_is_consistent(parsed):
        raise ValueError("pymoo probe cross-section semantics do not match canonical measured evidence")
    derived = derive_gates(parsed)
    if parsed.gates != derived:
        raise ValueError("pymoo probe stored gates do not match derived gates")
    expected_decision = DecisionRecord.model_validate(decide_probe(derived.model_dump(mode="python")))
    if parsed.decision != expected_decision:
        raise ValueError("pymoo probe decision does not match derived gates")
    if parsed.model_dump(mode="json") != evidence:
        raise ValueError("pymoo probe evidence is not in strict canonical value form")
    return evidence


def render_probe_evidence(evidence: JsonObject) -> bytes:
    validated = validate_probe_evidence(evidence)
    return (json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def write_probe_evidence(destination: Path, evidence: JsonObject, *, check: bool) -> bool:
    rendered = render_probe_evidence(evidence)
    if check:
        try:
            return destination.read_bytes() == rendered
        except FileNotFoundError:
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return True
