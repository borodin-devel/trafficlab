"""Schema owner for Validation Study tooling."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self, TypedDict, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictBool, StrictFloat, StrictInt, model_validator

from trafficlab.common.config import (
    C2stSettings,
    DispersionSettings,
    FamilyName,
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MarkovRenewalConfig,
    MethodWeights,
    MmppConfig,
    PoissonConfig,
    PostfitSettings,
    SimilarityConfig,
    TransitionSettings,
)
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.fitting.genetic.checkpoint import RngState
from trafficlab.fitting.genetic.types import Candidate, TrialResult
from trafficlab.generation.models.common import FamilyBounds, Gene

type JsonObject = dict[str, Any]

FAMILY_NAMES: tuple[FamilyName, ...] = ("markov_renewal", "mmpp", "poisson_empirical")

INITIAL_EVALUATION_BUDGET = 4

TOTAL_GENERATIONS = 2

SEARCH_SEED = 6053

TRIAL_SEEDS = (17, 29)

CHAMPION_SEED = 43

WINDOW_SECONDS = 6.0

INVALID_OBJECTIVE = 1.0

MINIMUM_LOC_REDUCTION_PERCENT = 40.0

KNOWN_POPULATION_SIZE = 20

KNOWN_GENERATIONS = 40

KNOWN_TOLERANCES = {"bounded_continuous_sphere": 0.001, "mixed_integer_real_quadratic": 0.001}

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

GENERATION_LIMITS = GenerationLimits(max_packets=1000, max_output_bytes=1000000, max_wall_seconds=5.0)

INVALID_CASE_LIMITS = GenerationLimits(max_packets=1, max_output_bytes=1000000, max_wall_seconds=5.0)

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
        frame_size_ks=0.125,
        iat_ks=0.125,
        autocorrelation=0.125,
        multiscale_rate=0.125,
        cramer_von_mises=0.125,
        anderson_darling=0.125,
        jensen_shannon=0.125,
        approximate_mmd=0.125,
    ),
    postfit=PostfitSettings(
        dispersion=DispersionSettings(
            widths_seconds=(1.0, 2.0),
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

CACHE_KEY_FIELDS = ("family", "genes", "observation_window_seconds", "trial_seeds", "generation_limits", "similarity")

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

FAMILY_BOUNDS: dict[FamilyName, FamilyBounds] = {
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

INITIAL_VALUES: dict[FamilyName, tuple[dict[str, Gene], ...]] = {
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
    "poisson_empirical": ({"c_lambda": 0.75}, {"c_lambda": 0.75}, {"c_lambda": 1.0}, {"c_lambda": 1.25}),
}

REFERENCE = TrafficTrace.from_events(
    tuple(
        TraceEvent(
            timestamp=index * 0.5,
            direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
            frame_length=60 + index * 24,
        )
        for index in range(13)
    )
)


def _tuple_input(value: object) -> object:
    return tuple(cast(list[object], value)) if type(value) is list else value


class _StrictProbeRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False, revalidate_instances="always"
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
        tuple[OperatorSettings, OperatorSettings, OperatorSettings, OperatorSettings], BeforeValidator(_tuple_input)
    ]
    mutation: Annotated[
        tuple[OperatorSettings, OperatorSettings, OperatorSettings, OperatorSettings], BeforeValidator(_tuple_input)
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
    families: Annotated[tuple[FamilyEvidence, FamilyEvidence, FamilyEvidence], BeforeValidator(_tuple_input)]
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
