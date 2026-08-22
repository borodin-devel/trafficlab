"""Adapter owner for Validation Study tooling."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import numpy as np
import pymoo  # pyright: ignore[reportMissingTypeStubs]
from pymoo.algorithms.soo.nonconvex.ga import GA  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.mixed import MixedVariableGA  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.population import Population  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.problem import ElementwiseProblem  # pyright: ignore[reportMissingTypeStubs]
from pymoo.core.variable import Integer, Real  # pyright: ignore[reportMissingTypeStubs]

from tests.scientific.fitting.probes.pymoo_optimizer.schema import (
    CHAMPION_SEED,
    CONTINUOUS_INITIAL_SAMPLES,
    FAMILY_BOUNDS,
    FAMILY_NAMES,
    GENERATION_LIMITS,
    INITIAL_EVALUATION_BUDGET,
    INITIAL_VALUES,
    INVALID_CASE_LIMITS,
    KNOWN_GENERATIONS,
    KNOWN_POPULATION_SIZE,
    MIXED_INITIAL_SAMPLES,
    REFERENCE,
    REPLAY_MISSING_FIELDS,
    SEARCH_SEED,
    SIMILARITY,
    TOTAL_GENERATIONS,
    TRIAL_SEEDS,
    WINDOW_SECONDS,
    AttemptRecord,
    CacheKeyRecord,
    ChampionRecord,
    ConstructorSettings,
    FamilyRunRecord,
    InitializationSettings,
    InvalidClassificationEvidence,
    KnownGenerationRecord,
    KnownPopulationRecord,
    KnownRunRecord,
    MatingDuplicateSettings,
    MatingSettings,
    OperatorSettings,
    PublicAlgorithmConfiguration,
    PublicPopulationState,
    PublicRngState,
    PublicStateSnapshot,
    PublicTerminationState,
    RepairSettings,
    Scalars,
    TerminationSettings,
    VariableSpec,
)
from trafficlab.common.config import (
    FamilyName,
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
)
from trafficlab.fitting.genetic.checkpoint import RngState
from trafficlab.fitting.genetic.evaluation import (
    EvaluationContext,
    ValidatedEvaluationContext,
    evaluate_candidate,
    evaluate_final,
    validate_evaluation_context,
)
from trafficlab.fitting.genetic.types import Candidate, CandidateId, rebuild_genetic_record
from trafficlab.generation.models.common import FamilyBounds, Gene, Genes, ModelFamily
from trafficlab.generation.models.registry import REGISTRY

type PymooAlgorithm = Any

type PymooPopulation = Any

type PymooVariable = Any

PYMOO_GA_CLASS = cast(Any, GA)

MIXED_VARIABLE_GA = cast(Any, MixedVariableGA)

POPULATION = cast(Any, Population)


@dataclass(frozen=True, slots=True)
class _CachedOutcome:
    candidate: Candidate
    objective: float


@dataclass(frozen=True, slots=True)
class FamilyExecution:
    algorithm: object
    run: FamilyRunRecord
    checkpoint: PublicStateSnapshot


class _SphereProblem(ElementwiseProblem):
    def __init__(self) -> None:
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            n_var=2, n_obj=1, xl=np.array((-2.0, -2.0)), xu=np.array((2.0, 2.0))
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
            variables: Scalars = {"x0": float(cast(Any, raw_variables[0])), "x1": float(cast(Any, raw_variables[1]))}
        else:
            variables = cast(Scalars, raw_variables)
        output.append(KnownPopulationRecord(variables=variables, objective=float(individual.F[0])))
    return tuple(output)


def run_known_case(name: str) -> KnownRunRecord:
    history: list[KnownGenerationRecord] = []
    if name == "bounded_continuous_sphere":
        problem: ElementwiseProblem = _SphereProblem()
        algorithm: PymooAlgorithm = PYMOO_GA_CLASS(
            pop_size=KNOWN_POPULATION_SIZE,
            sampling=np.asarray(CONTINUOUS_INITIAL_SAMPLES, dtype=np.float64),
            eliminate_duplicates=False,
        )
    elif name == "mixed_integer_real_quadratic":
        problem = _MixedKnownProblem()
        initial = POPULATION.new(X=np.array(MIXED_INITIAL_SAMPLES, dtype=object))
        algorithm = MIXED_VARIABLE_GA(pop_size=KNOWN_POPULATION_SIZE, sampling=initial, eliminate_duplicates=False)
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
        variables: Scalars = {"x0": float(cast(Any, raw_variables[0])), "x1": float(cast(Any, raw_variables[1]))}
    else:
        variables = cast(Scalars, raw_variables)
    return KnownRunRecord(
        variables=variables,
        objective=float(result.F[0]),
        evaluations=int(algorithm.evaluator.n_eval),
        initial_minimum_objective=history[0].minimum_objective,
        history=tuple(history),
    )


def variable_specs(family: ModelFamily, bounds: FamilyBounds) -> tuple[VariableSpec, ...]:
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
        output.append(int(value) if type(getattr(FAMILY_BOUNDS[family.name], name)) is IntegerBounds else float(value))
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


def render_cache_key(payload: CacheKeyRecord) -> str:
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)


class _TrafficProblem(ElementwiseProblem):
    def __init__(self, family: ModelFamily, context: ValidatedEvaluationContext, *, population_size: int) -> None:
        self.family = family
        self.context = context
        self.population_size = population_size
        self.cache: dict[str, _CachedOutcome] = {}
        self.history: list[AttemptRecord] = []
        specs = variable_specs(family, context.bounds[family.name])
        super().__init__(vars=_family_variables(specs))  # pyright: ignore[reportUnknownMemberType]

    def _evaluate(self, values: dict[str, Any], out: dict[str, object], *args: object, **kwargs: object) -> None:
        del args, kwargs
        evaluation_index = len(self.history) + 1
        generation = (evaluation_index - 1) // self.population_size
        birth_index = (evaluation_index - 1) % self.population_size
        identifier = CandidateId(birth_generation=generation, birth_index=birth_index)
        genes = _genes(self.family, values)
        payload = _cache_payload(self.family.name, genes, self.context)
        key = render_cache_key(payload)
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


def evaluation_context(limits: GenerationLimits = GENERATION_LIMITS) -> ValidatedEvaluationContext:
    families: dict[FamilyName, ModelFamily] = {name: REGISTRY[name] for name in FAMILY_NAMES}
    return validate_evaluation_context(
        EvaluationContext(
            reference=REFERENCE,
            window=WINDOW_SECONDS,
            families=families,
            bounds=FAMILY_BOUNDS,
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


def repair_settings(value: object) -> RepairSettings | None:
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
        repair=repair_settings(dynamic.repair),
    )


def mating_settings(algorithm: PymooAlgorithm) -> MatingSettings:
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
            tuple((_operator_settings(kind, operator, mutation=False) for kind, operator in crossover.items())),
        ),
        mutation=cast(
            tuple[OperatorSettings, OperatorSettings, OperatorSettings, OperatorSettings],
            tuple((_operator_settings(kind, operator, mutation=True) for kind, operator in mutation.items())),
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
                    Literal["pymoo.core.population.Population"], _qualified_name(algorithm.initialization.sampling)
                ),
                repair=cast(Literal["pymoo.core.repair.NoRepair"], _qualified_name(algorithm.initialization.repair)),
                duplicate_elimination=cast(
                    Literal["pymoo.core.duplicate.NoDuplicateElimination"],
                    _qualified_name(algorithm.initialization.eliminate_duplicates),
                ),
            ),
            algorithm_repair=cast(RepairSettings, repair_settings(algorithm.repair)),
            mating=mating_settings(algorithm),
            termination=TerminationSettings(kind="n_gen", value=TOTAL_GENERATIONS),
        ),
        pymoo_version=cast(Literal["0.6.2"], pymoo.__version__),
        rng=PublicRngState(
            engine=cast(Literal["PCG64"], type(random_state.bit_generator).__name__),
            state=RngState.model_validate(_json_value(random_state.bit_generator.state)),
        ),
    )


def run_family(family_name: FamilyName, context: ValidatedEvaluationContext) -> FamilyExecution:
    family = context.families[family_name]
    specs = variable_specs(family, context.bounds[family_name])
    initial_sampling: tuple[Scalars, ...] = tuple(dict(item) for item in INITIAL_VALUES[family_name])
    initial = POPULATION.new(X=np.array(initial_sampling, dtype=object))
    problem = _TrafficProblem(family, context, population_size=INITIAL_EVALUATION_BUDGET)
    algorithm: PymooAlgorithm = MIXED_VARIABLE_GA(
        pop_size=INITIAL_EVALUATION_BUDGET, sampling=initial, eliminate_duplicates=False
    )
    algorithm.setup(problem, termination=("n_gen", TOTAL_GENERATIONS), seed=SEARCH_SEED, verbose=False)
    snapshot: PublicStateSnapshot | None = None
    while algorithm.has_next():
        algorithm.next()
        if int(algorithm.evaluator.n_eval) == INITIAL_EVALUATION_BUDGET:
            snapshot = _public_snapshot(algorithm, family=family, specs=specs, initial_sampling=initial_sampling)
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
    return FamilyExecution(algorithm=algorithm, run=run, checkpoint=snapshot)


def run_invalid_adapter() -> InvalidClassificationEvidence:
    context = evaluation_context(INVALID_CASE_LIMITS)
    family = context.families["poisson_empirical"]
    problem = _TrafficProblem(family, context, population_size=1)
    initial = POPULATION.new(X=np.array(({"c_lambda": 1.0},), dtype=object))
    algorithm: PymooAlgorithm = MIXED_VARIABLE_GA(pop_size=1, sampling=initial, eliminate_duplicates=False)
    algorithm.setup(problem, termination=("n_gen", 1), seed=SEARCH_SEED, verbose=False)
    algorithm.next()
    return InvalidClassificationEvidence(
        execution="pymoo.MixedVariableGA.next",
        family="poisson_empirical",
        limits=INVALID_CASE_LIMITS,
        pymoo_evaluations=int(algorithm.evaluator.n_eval),
        history=cast(tuple[AttemptRecord], tuple(problem.history)),
    )


def champion(execution: FamilyExecution, context: ValidatedEvaluationContext) -> ChampionRecord:
    trials = evaluate_final(execution.run.best_candidate, context, CHAMPION_SEED)
    return ChampionRecord(
        family=execution.run.family,
        candidate=execution.run.best_candidate,
        fresh_seed=CHAMPION_SEED,
        fresh_fitness=trials[0].aggregate_score,
        search_attempts_completed=execution.run.total_attempts,
        trials=trials,
    )
