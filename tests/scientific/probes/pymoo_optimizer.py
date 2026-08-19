"""Test-only pymoo optimizer, fairness, cache, and checkpoint-adoption probe."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, cast

import numpy as np
import pymoo  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, BeforeValidator, ConfigDict, StrictFloat, StrictInt
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
from trafficlab.genetic.types import Candidate, CandidateId
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

GENERATION_LIMITS = GenerationLimits(
    max_packets=1_000,
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
    "population",
    "generation",
    "evaluation_count",
    "termination",
    "configuration",
    "pymoo_version",
    "rng",
)
REPLAY_HISTORY_FIELDS = (
    "evaluation_index",
    "family",
    "genes",
    "objective",
    "fitness",
    "status",
    "failure",
    "trials",
    "cache_key",
    "cache_hit",
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


@dataclass(frozen=True, slots=True)
class _CachedOutcome:
    candidate: Candidate
    objective: float


@dataclass(frozen=True, slots=True)
class _FamilyExecution:
    record: JsonObject
    best_candidate: Candidate
    checkpoint: JsonObject


def _tuple_input(value: object) -> object:
    return tuple(cast(list[object], value)) if type(value) is list else value


class _StrictProbeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class PublicPopulationState(_StrictProbeRecord):
    """One public pymoo population member without opaque algorithm internals."""

    variables: dict[str, StrictInt | StrictFloat]
    objectives: Annotated[tuple[StrictFloat, ...], BeforeValidator(_tuple_input)]
    status: Annotated[tuple[str, ...], BeforeValidator(_tuple_input)]


class PublicTerminationState(_StrictProbeRecord):
    kind: Literal["MaximumGenerationTermination"]
    progress: StrictFloat
    has_terminated: bool


class PublicAlgorithmConfiguration(_StrictProbeRecord):
    algorithm: Literal["pymoo.core.mixed.MixedVariableGA"]
    family: FamilyName
    population_size: StrictInt
    generations: StrictInt
    eliminate_duplicates: bool
    variable_kinds: dict[str, Literal["integer", "real"]]


class PublicRngState(_StrictProbeRecord):
    engine: Literal["PCG64"]
    state: RngState


class PublicStateSnapshot(_StrictProbeRecord):
    """Strict transparent checkpoint fields observable through pymoo's public objects."""

    population: Annotated[tuple[PublicPopulationState, ...], BeforeValidator(_tuple_input)]
    generation: StrictInt
    evaluation_count: StrictInt
    termination: PublicTerminationState
    configuration: PublicAlgorithmConfiguration
    pymoo_version: Literal["0.6.2"]
    rng: PublicRngState


class _SphereProblem(ElementwiseProblem):
    def __init__(self) -> None:
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            n_var=2,
            n_obj=1,
            xl=np.array((-2.0, -2.0)),
            xu=np.array((2.0, 2.0)),
        )

    def _evaluate(
        self, values: np.ndarray[Any, np.dtype[np.float64]], out: dict[str, object], *args: object, **kwargs: object
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
        items = cast(Mapping[object, object], value)
        return {str(key): _json_value(item) for key, item in items.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in cast(Sequence[object], value)]
    return value


def _population_records(population: PymooPopulation) -> list[JsonObject]:
    records: list[JsonObject] = []
    for individual in population:
        objectives = cast(np.ndarray[Any, np.dtype[np.float64]], individual.F)
        records.append(
            {
                "variables": _json_value(individual.X),
                "objectives": [float(item) for item in objectives],
                "status": sorted(str(item) for item in individual.evaluated),
            }
        )
    return records


def _known_history(algorithm: PymooAlgorithm) -> JsonObject:
    if algorithm.pop is None:
        raise AssertionError("completed pymoo generation has no population")
    return {
        "generation": cast(int, algorithm.n_gen),
        "evaluation_count": int(algorithm.evaluator.n_eval),
        "population": _population_records(algorithm.pop),
    }


def _run_known_case(name: str) -> JsonObject:
    history: list[JsonObject] = []
    if name == "bounded_continuous_sphere":
        sampling = np.array(((0.0, 0.0), (-2.0, -2.0), (2.0, 2.0), (1.0, -1.0)), dtype=np.float64)
        problem: ElementwiseProblem = _SphereProblem()
        algorithm: PymooAlgorithm = _GA(
            pop_size=INITIAL_EVALUATION_BUDGET,
            sampling=sampling,
            eliminate_duplicates=False,
        )
    elif name == "mixed_integer_real_quadratic":
        sampling = _POPULATION.new(
            X=np.array(
                (
                    {"count": 3, "scale": 1.25},
                    {"count": 0, "scale": 0.5},
                    {"count": 6, "scale": 2.0},
                    {"count": 2, "scale": 1.0},
                ),
                dtype=object,
            )
        )
        problem = _MixedKnownProblem()
        algorithm = _MIXED_VARIABLE_GA(
            pop_size=INITIAL_EVALUATION_BUDGET,
            sampling=sampling,
            eliminate_duplicates=False,
        )
    else:
        raise ValueError(f"unknown known-optimum case: {name}")
    algorithm.setup(problem, termination=("n_gen", TOTAL_GENERATIONS), seed=SEARCH_SEED, verbose=False)
    while algorithm.has_next():
        algorithm.next()
        history.append(_known_history(algorithm))
    result = algorithm.result()
    variables = _json_value(result.X)
    if name == "bounded_continuous_sphere":
        sequence = cast(list[object], variables)
        variables = {"x0": float(cast(Any, sequence[0])), "x1": float(cast(Any, sequence[1]))}
    objective = float(cast(np.ndarray[Any, np.dtype[np.float64]], result.F)[0])
    specification = next(case for case in KNOWN_CASES if case["name"] == name)
    return {
        "name": name,
        "known_optimum": _json_value(specification["known_optimum"]),
        "variables": variables,
        "objective": objective,
        "evaluations": int(algorithm.evaluator.n_eval),
        "history": history,
        "passed": variables == specification["known_optimum"]["variables"] and objective == 0.0,
    }


def _family_variables(family: ModelFamily, bounds: FamilyBounds) -> dict[str, PymooVariable]:
    variables: dict[str, PymooVariable] = {}
    for name in family.gene_names:
        bound = getattr(bounds, name)
        if type(bound) is IntegerBounds:
            variables[name] = Integer(bounds=(bound.lower, bound.upper))
        elif type(bound) is FloatBounds:
            variables[name] = Real(bounds=(bound.lower, bound.upper))
        else:
            raise TypeError(f"unsupported bounds for {family.name}.{name}")
    return variables


def _variable_kinds(variables: Mapping[str, PymooVariable]) -> dict[str, str]:
    return {name: "integer" if isinstance(variable, Integer) else "real" for name, variable in variables.items()}


def _genes(family: ModelFamily, values: Mapping[str, Any]) -> Genes:
    output: list[Gene] = []
    for name in family.gene_names:
        value = values[name]
        output.append(int(value) if type(getattr(_FAMILY_BOUNDS[family.name], name)) is IntegerBounds else float(value))
    return tuple(output)


def _cache_key(family: FamilyName, genes: Genes, context: ValidatedEvaluationContext) -> str:
    document = {
        "family": family,
        "genes": list(genes),
        "observation_window_seconds": context.window,
        "trial_seeds": list(context.trial_seeds),
        "generation_limits": context.trial_limits.model_dump(mode="json"),
        "similarity": context.similarity.model_dump(mode="json"),
    }
    if tuple(document) != CACHE_KEY_FIELDS:
        raise AssertionError("cache key fields drifted from the predeclared identity")
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _candidate_record(candidate: Candidate) -> tuple[JsonObject | None, list[JsonObject]]:
    failure = None if candidate.invalid is None else candidate.invalid.model_dump(mode="json")
    trials = [trial.model_dump(mode="json") for trial in candidate.trials]
    return (failure, trials)


class _TrafficProblem(ElementwiseProblem):
    def __init__(self, family: ModelFamily, context: ValidatedEvaluationContext) -> None:
        self.family = family
        self.context = context
        self.cache: dict[str, _CachedOutcome] = {}
        self.history: list[JsonObject] = []
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            vars=_family_variables(family, context.bounds[family.name])
        )

    def _evaluate(self, values: dict[str, Any], out: dict[str, object], *args: object, **kwargs: object) -> None:
        del args, kwargs
        genes = _genes(self.family, values)
        key = _cache_key(self.family.name, genes, self.context)
        cached = self.cache.get(key)
        cache_hit = cached is not None
        if cached is None:
            index = len(self.history)
            pending = Candidate(
                identifier=CandidateId(
                    birth_generation=index // INITIAL_EVALUATION_BUDGET,
                    birth_index=index % INITIAL_EVALUATION_BUDGET,
                ),
                family=self.family.name,
                genes=genes,
                status="pending",
                fitness=0.0,
                trials=(),
                invalid=None,
                duplicate_diagnostics=(),
            )
            candidate = evaluate_candidate(pending, self.context)
            cached = _CachedOutcome(candidate=candidate, objective=1.0 - candidate.fitness)
            self.cache[key] = cached
        failure, trials = _candidate_record(cached.candidate)
        self.history.append(
            {
                "evaluation_index": len(self.history) + 1,
                "family": self.family.name,
                "genes": list(cached.candidate.genes if cached.candidate.genes is not None else genes),
                "objective": cached.objective,
                "fitness": cached.candidate.fitness,
                "status": cached.candidate.status,
                "failure": failure,
                "trials": trials,
                "cache_key": key,
                "cache_hit": cache_hit,
            }
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


def _public_snapshot(algorithm: PymooAlgorithm, *, family: FamilyName, variable_kinds: dict[str, str]) -> JsonObject:
    population = algorithm.pop
    termination = algorithm.termination
    random_state = algorithm.random_state
    if population is None or termination is None or random_state is None:
        raise AssertionError("pymoo public state is incomplete after a generation")
    snapshot_record = PublicStateSnapshot.model_validate(
        {
            "population": _population_records(population),
            "generation": cast(int, algorithm.n_gen),
            "evaluation_count": int(algorithm.evaluator.n_eval),
            "termination": {
                "kind": type(termination).__name__,
                "progress": float(termination.perc),
                "has_terminated": bool(termination.has_terminated()),
            },
            "configuration": {
                "algorithm": "pymoo.core.mixed.MixedVariableGA",
                "family": family,
                "population_size": INITIAL_EVALUATION_BUDGET,
                "generations": TOTAL_GENERATIONS,
                "eliminate_duplicates": False,
                "variable_kinds": variable_kinds,
            },
            "pymoo_version": pymoo.__version__,
            "rng": {
                "engine": type(random_state.bit_generator).__name__,
                "state": cast(dict[str, object], _json_value(random_state.bit_generator.state)),
            },
        }
    )
    snapshot = snapshot_record.model_dump(mode="json")
    if tuple(snapshot) != CHECKPOINT_FIELDS:
        raise AssertionError("public checkpoint fields drifted from the predeclared record")
    return snapshot


def _run_family(family_name: FamilyName, context: ValidatedEvaluationContext) -> _FamilyExecution:
    family = context.families[family_name]
    variables = _family_variables(family, context.bounds[family_name])
    initial = _POPULATION.new(X=np.array(_INITIAL_VALUES[family_name], dtype=object))
    problem = _TrafficProblem(family, context)
    algorithm: PymooAlgorithm = _MIXED_VARIABLE_GA(
        pop_size=INITIAL_EVALUATION_BUDGET,
        sampling=initial,
        eliminate_duplicates=False,
    )
    algorithm.setup(problem, termination=("n_gen", TOTAL_GENERATIONS), seed=SEARCH_SEED, verbose=False)
    snapshot: JsonObject | None = None
    while algorithm.has_next():
        algorithm.next()
        if int(algorithm.evaluator.n_eval) == INITIAL_EVALUATION_BUDGET:
            snapshot = _public_snapshot(algorithm, family=family_name, variable_kinds=_variable_kinds(variables))
    if snapshot is None:
        raise AssertionError("pymoo run did not expose the initial evaluated generation")
    history = problem.history
    if len(history) != INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS:
        raise AssertionError("pymoo objective history does not match the configured evaluation budget")
    best_key = min(
        problem.cache,
        key=lambda key: (problem.cache[key].objective, key),
    )
    best = problem.cache[best_key].candidate
    record = {
        "family": family_name,
        "optimizer_id": f"pymoo.MixedVariableGA:{family_name}",
        "variable_kinds": _variable_kinds(variables),
        "initial_evaluations": INITIAL_EVALUATION_BUDGET,
        "total_attempts": len(history),
        "objective_evaluations": len(problem.cache),
        "cache_hits": sum(bool(item["cache_hit"]) for item in history),
        "history": history,
        "best_genes": list(cast(Genes, best.genes)),
        "best_fitness": best.fitness,
    }
    return _FamilyExecution(record=record, best_candidate=best, checkpoint=snapshot)


def _invalid_classification_case() -> JsonObject:
    context = _evaluation_context(GenerationLimits(max_packets=1, max_output_bytes=1_000_000, max_wall_seconds=5.0))
    candidate = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="poisson_empirical",
        genes=(1.0,),
        status="pending",
        fitness=0.0,
        trials=(),
        invalid=None,
        duplicate_diagnostics=(),
    )
    evaluated = evaluate_candidate(candidate, context)
    failure, trials = _candidate_record(evaluated)
    return {
        "family": evaluated.family,
        "genes": list(cast(Genes, evaluated.genes)),
        "objective": 1.0 - evaluated.fitness,
        "fitness": evaluated.fitness,
        "status": evaluated.status,
        "failure": failure,
        "trials": trials,
    }


def _champion_record(execution: _FamilyExecution, context: ValidatedEvaluationContext) -> JsonObject:
    trials = evaluate_final(execution.best_candidate, context, CHAMPION_SEED)
    trial = trials[0]
    return {
        "family": execution.best_candidate.family,
        "genes": list(cast(Genes, execution.best_candidate.genes)),
        "selection_fitness": execution.best_candidate.fitness,
        "fresh_seed": CHAMPION_SEED,
        "fresh_fitness": trial.aggregate_score,
        "trials": [trial.model_dump(mode="json")],
    }


def _count_sloc(path: Path) -> int:
    return sum(bool(line.strip()) and not line.lstrip().startswith("#") for line in path.read_text().splitlines())


def _loc_inventory() -> JsonObject:
    repository = Path(__file__).resolve().parents[3]
    directory = repository / "src" / "trafficlab" / "genetic"
    current = tuple(path.name for path in sorted(directory.glob("*.py")))
    retained = ("__init__.py", "checkpoint.py", "evaluation.py", "types.py")
    removable = ("coordinates.py", "operators.py", "population.py", "strategy.py")
    counts = {name: _count_sloc(directory / name) for name in current}
    current_sloc = sum(counts.values())
    retained_sloc = sum(counts[name] for name in retained)
    removable_sloc = sum(counts[name] for name in removable)
    maximum = removable_sloc * 100.0 / current_sloc
    return {
        "method": "nonblank non-comment physical Python lines",
        "current_files": list(current),
        "required_retained_files": list(retained),
        "candidate_removable_files": list(removable),
        "file_sloc": counts,
        "current_sloc": current_sloc,
        "required_retained_sloc": retained_sloc,
        "candidate_removable_sloc": removable_sloc,
        "adapter_sloc_assumption": 0,
        "estimated_replacement_sloc": retained_sloc,
        "estimated_reduction_percent": maximum,
        "estimate_kind": "optimistic upper bound assuming a zero-line adapter",
        "maximum_reduction_percent_before_adapter": maximum,
        "required_reduction_percent": MINIMUM_LOC_REDUCTION_PERCENT,
        "gate_passed": maximum >= MINIMUM_LOC_REDUCTION_PERCENT,
        "reason": "required diagnostic/artifact files alone leave less than 40% removable before any adapter",
    }


def _policy() -> JsonObject:
    return {
        "production_changed": False,
        "pymoo_version": pymoo.__version__,
        "family_names": list(FAMILY_NAMES),
        "independent_optimizer_per_family": True,
        "categorical_family_variable": False,
        "initial_evaluation_budget": INITIAL_EVALUATION_BUDGET,
        "total_generations": TOTAL_GENERATIONS,
        "search_seed": SEARCH_SEED,
        "trial_seeds": list(TRIAL_SEEDS),
        "champion_seed": CHAMPION_SEED,
        "window_seconds": WINDOW_SECONDS,
        "generation_limits": GENERATION_LIMITS.model_dump(mode="json"),
        "similarity": SIMILARITY.model_dump(mode="json"),
        "cache_key_fields": list(CACHE_KEY_FIELDS),
        "checkpoint_fields": list(CHECKPOINT_FIELDS),
        "replay_history_fields": list(REPLAY_HISTORY_FIELDS),
        "checkpoint_encoding": "canonical-json",
        "prohibited_serializers": list(PROHIBITED_SERIALIZERS),
        "minimum_loc_reduction_percent": MINIMUM_LOC_REDUCTION_PERCENT,
    }


def decide_probe(gates: Mapping[str, object]) -> JsonObject:
    """Apply the immutable all-gates adoption rule without changing production."""
    unknown = sorted(set(gates) - set(GATE_NAMES))
    if unknown:
        failed = [f"unknown:{name}" for name in unknown]
    else:
        failed = [name for name in GATE_NAMES if type(gates.get(name)) is not bool or not gates[name]]
    return {
        "outcome": "pass" if not failed else "reject",
        "failed_gates": failed,
        "production_changed": False,
        "production_strategy": "basic_generational",
    }


def _contains_bytes(value: object) -> bool:
    if isinstance(value, bytes):
        return True
    if isinstance(value, Mapping):
        return any(_contains_bytes(item) for item in cast(Mapping[object, object], value).values())
    if isinstance(value, Sequence) and not isinstance(value, str):
        return any(_contains_bytes(item) for item in cast(Sequence[object], value))
    return False


def build_probe_evidence() -> JsonObject:
    """Execute every bounded optimizer gate and return canonical JSON-compatible evidence."""
    known_records: list[JsonObject] = []
    known_repeats = True
    for case in KNOWN_CASES:
        first = _run_known_case(case["name"])
        second = _run_known_case(case["name"])
        repeat_equal = first == second
        known_repeats &= repeat_equal
        first["repeat_history_equal"] = repeat_equal
        known_records.append(first)

    context = _evaluation_context()
    first_runs = [_run_family(name, context) for name in FAMILY_NAMES]
    second_runs = [_run_family(name, context) for name in FAMILY_NAMES]
    family_records: list[JsonObject] = []
    family_repeats = True
    for first, second in zip(first_runs, second_runs, strict=True):
        repeat_equal = first.record == second.record and first.checkpoint == second.checkpoint
        family_repeats &= repeat_equal
        first.record["repeat_history_equal"] = repeat_equal
        family_records.append(first.record)

    champions = [_champion_record(run, context) for run in first_runs]
    champion = max(
        champions,
        key=lambda item: (cast(float, item["fresh_fitness"]), -FAMILY_NAMES.index(cast(FamilyName, item["family"]))),
    )
    loc = _loc_inventory()
    cache_diagnostics_gate = all(
        cast(int, record["cache_hits"]) >= 1
        and cast(int, record["objective_evaluations"]) + cast(int, record["cache_hits"])
        == cast(int, record["total_attempts"])
        for record in family_records
    )
    fairness_gate = (
        len(first_runs) == len(FAMILY_NAMES)
        and all(record["initial_evaluations"] == INITIAL_EVALUATION_BUDGET for record in family_records)
        and all("family" not in cast(dict[str, str], record["variable_kinds"]) for record in family_records)
    )
    checkpoint = {
        "encoding": "canonical-json",
        "snapshot": first_runs[0].checkpoint,
        "comparison": {
            "exact_history_identity": False,
            "checked_fields": list(REPLAY_HISTORY_FIELDS),
            "uninterrupted_history": family_records[0]["history"],
            "resumed_history": None,
            "missing_public_restore_fields": [
                "algorithm initialization/iteration state",
                "operator state",
                "termination restoration",
            ],
            "reason": (
                "pymoo 0.6.2 documents transparent ask/tell observation but no transparent state restore API; "
                "its checkpoint guide restores only a serialized whole Algorithm object"
            ),
        },
        "public_api_proof": {
            "installed_algorithm_methods": ["setup", "has_next", "next", "ask", "tell", "result"],
            "installed_observable_state": ["pop", "n_gen", "evaluator.n_eval", "termination", "random_state"],
            "documented_checkpoint_transport": "dill algorithm serialization",
            "opaque_transport_allowed": False,
            "transparent_restore_api": None,
            "checkpoint_documentation": "https://pymoo.org/misc/checkpoint.html",
            "usage_documentation": "https://pymoo.org/algorithms/usage.html",
        },
    }
    gates: dict[str, bool] = {
        "known_optima": all(bool(record["passed"]) for record in known_records),
        "deterministic_repeats": known_repeats and family_repeats,
        "family_fairness": fairness_gate,
        "cache_and_diagnostics": cache_diagnostics_gate,
        "exact_public_state_replay": False,
        "production_loc_reduction": bool(loc["gate_passed"]),
    }
    evidence = {
        "schema_version": 3,
        "probe": "pymoo_optimizer",
        "policy": _policy(),
        "known_cases": known_records,
        "family_runs": family_records,
        "fairness": {
            "independent_optimizer_count": len(first_runs),
            "categorical_family_variable": False,
            "initial_evaluations_by_family": dict.fromkeys(FAMILY_NAMES, INITIAL_EVALUATION_BUDGET),
            "common_search_seed": SEARCH_SEED,
            "common_trial_seeds": list(TRIAL_SEEDS),
            "common_champion_seed": CHAMPION_SEED,
            "common_window_seconds": WINDOW_SECONDS,
            "common_generation_limits": GENERATION_LIMITS.model_dump(mode="json"),
            "common_similarity": SIMILARITY.model_dump(mode="json"),
            "cache_key_fields": list(CACHE_KEY_FIELDS),
            "champion_comparison": champions,
            "winner_family": champion["family"],
        },
        "invalid_classification_case": _invalid_classification_case(),
        "checkpoint": checkpoint,
        "production_loc": loc,
        "gates": gates,
        "decision": decide_probe(gates),
    }
    if _contains_bytes(evidence):
        raise AssertionError("transparent checkpoint evidence must not contain opaque bytes")
    return evidence


def validate_probe_evidence(evidence: JsonObject) -> JsonObject:
    """Reject policy drift or a decision inconsistent with the executed gate record."""
    if evidence.get("schema_version") != 3 or evidence.get("probe") != "pymoo_optimizer":
        raise ValueError("pymoo probe evidence identity is invalid")
    if evidence.get("policy") != _policy():
        raise ValueError("pymoo probe policy does not match the executed controls")
    gates = evidence.get("gates")
    if not isinstance(gates, Mapping) or tuple(cast(Mapping[str, object], gates)) != GATE_NAMES:
        raise ValueError("pymoo probe gates do not match the predeclared gate set")
    if evidence.get("decision") != decide_probe(cast(Mapping[str, object], gates)):
        raise ValueError("pymoo probe decision does not match its gates")
    family_runs = evidence.get("family_runs")
    family_items = cast(list[object], family_runs) if isinstance(family_runs, list) else []
    observed_families = [
        cast(Mapping[str, object], item).get("family") for item in family_items if isinstance(item, dict)
    ]
    if not isinstance(family_runs, list) or observed_families != list(FAMILY_NAMES):
        raise ValueError("pymoo family run order does not match the predeclared families")
    if _contains_bytes(evidence):
        raise ValueError("pymoo checkpoint evidence contains opaque bytes")
    return evidence


def render_probe_evidence(evidence: JsonObject) -> bytes:
    """Render sorted compact UTF-8 JSON with one final newline."""
    validated = validate_probe_evidence(evidence)
    return (json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def write_probe_evidence(destination: Path, evidence: JsonObject, *, check: bool) -> bool:
    """Write canonical evidence or check an existing fixture without mutation."""
    rendered = render_probe_evidence(evidence)
    if check:
        try:
            return destination.read_bytes() == rendered
        except FileNotFoundError:
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return True
