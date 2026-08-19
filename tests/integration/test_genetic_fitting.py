from __future__ import annotations

import json
import math
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace as replace_dataclass
from pathlib import Path
from random import Random
from typing import Any, cast

import pytest
from pydantic import BaseModel

import trafficlab.docker_cli as docker_cli
import trafficlab.genetic.evaluation as genetic_evaluation
import trafficlab.genetic.operators as genetic_operators
import trafficlab.genetic.strategy as genetic_strategy
from trafficlab.artifacts import create_run_directory
from trafficlab.comparison import compare_experiment, load_comparison_result
from trafficlab.compatibility import ContentIdentity, identify_bytes
from trafficlab.config import ExperimentConfig, FamilyName, GenerationLimits, MethodWeights
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.fitting import FitDependencies, fit_experiment, read_fit_input
from trafficlab.generation import generate_experiment
from trafficlab.genetic.checkpoint import CheckpointState, load_checkpoint, render_history_csv
from trafficlab.genetic.evaluation import ValidatedEvaluationContext
from trafficlab.genetic.operators import ReproductionContext
from trafficlab.genetic.population import rank_candidates
from trafficlab.genetic.strategy import make_strategy_context, run_strategy
from trafficlab.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.models.common import MARKOV_MODEL_DIAGNOSTIC_KEYS, FittedModel, GenerationResult, Genes
from trafficlab.models.registry import load_best_model, render_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.preflight import PreflightReport, PreparedExperiment
from trafficlab.trace import normalize_reference, parse_capture_metadata

pytestmark = pytest.mark.integration


def replace[Record](record: Record, **changes: object) -> Record:
    """Build controlled in-process model states for integration fixtures."""
    if isinstance(record, BaseModel):
        values = {name: getattr(record, name) for name in type(record).model_fields}
        values.update(changes)
        return cast(Record, type(record).model_construct(**values))
    return cast(Record, replace_dataclass(cast(Any, record), **changes))


_ROOT = Path(__file__).resolve().parents[2]
_FIT_DIRECTORY = _ROOT / "examples" / "data" / "fit"
_EXPERIMENT_PATH = _FIT_DIRECTORY / "experiment.toml"
_EXPERIMENT_BYTES = _EXPERIMENT_PATH.read_bytes()
_CAPTURE_BYTES = (_FIT_DIRECTORY / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_DIRECTORY / "reference.pcapng").read_bytes()
_FAMILY_ORDER = ("markov_renewal", "mmpp", "poisson_empirical")
_OPERATOR_VALUES = {
    "markov_renewal": (1.0, 0.0, 0.06),
    "mmpp": (0.45, 0.0, 0.08),
    "poisson_empirical": (0.35, 0.0, 0.07),
}
_MIXED_METHOD_WEIGHTS = {
    "frame_size_ks": 0.1,
    "iat_ks": 0.2,
    "autocorrelation": 0.3,
    "multiscale_rate": 0.4,
}
_MIXED_COMPONENT_SCORES = {
    "markov_renewal": {
        "frame_size_ks": 0.4,
        "iat_ks": 0.4,
        "autocorrelation": 0.4,
        "multiscale_rate": 0.4,
    },
    "mmpp": {
        "frame_size_ks": 0.8,
        "iat_ks": 0.7,
        "autocorrelation": 0.9,
        "multiscale_rate": 0.8,
    },
    "poisson_empirical": {
        "frame_size_ks": 0.6,
        "iat_ks": 0.6,
        "autocorrelation": 0.6,
        "multiscale_rate": 0.6,
    },
}


def _copy_fixture_experiment(tmp_path: Path) -> tuple[Path, Path, ExperimentConfig]:
    run_directory = tmp_path / "run"
    base = load_experiment(_EXPERIMENT_PATH)
    config = base.model_copy(update={"run": base.run.model_copy(update={"directory": run_directory})})
    caller_path = tmp_path / "experiment.toml"
    caller_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    (run_directory / "capture.json").write_bytes(_CAPTURE_BYTES)
    (run_directory / "reference.pcapng").write_bytes(_REFERENCE_BYTES)
    return caller_path, run_directory, config


def _strategy_context(config: ExperimentConfig, run_directory: Path):  # type: ignore[no-untyped-def]
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=run_directory / "capture.json")
    parsed = parse_pcapng_bytes(_REFERENCE_BYTES, metadata, source=run_directory / "reference.pcapng")
    reference, window = normalize_reference(parsed)
    return make_strategy_context(
        config,
        reference,
        window,
        run_directory,
        experiment_identity=identify_bytes((run_directory / "experiment.toml").read_bytes()),
        reference_identity=identify_bytes(_REFERENCE_BYTES),
        capture_identity=identify_bytes(_CAPTURE_BYTES),
    )


def _portable_config() -> ExperimentConfig:
    return ExperimentConfig.model_validate(tomllib.loads(_EXPERIMENT_BYTES.decode("utf-8")))


def _portable_dependencies(run_directory: Path) -> FitDependencies:
    config = _portable_config()
    if not run_directory.exists():
        run_directory.mkdir(parents=True)
        (run_directory / "experiment.toml").write_bytes(_EXPERIMENT_BYTES)
        (run_directory / "capture.json").write_bytes(_CAPTURE_BYTES)
        (run_directory / "reference.pcapng").write_bytes(_REFERENCE_BYTES)
        (run_directory / "run.log").write_bytes(b"")
    prepared = PreparedExperiment(
        source=Path("fixture-experiment.toml"),
        config=config,
        report=PreflightReport(config, ()),
        run_directory=run_directory,
    )
    return FitDependencies(lambda _path: prepared, read_fit_input, run_strategy)


def _configure_fairness_matrix(
    config: ExperimentConfig,
    caller_path: Path,
    run_directory: Path,
    *,
    master_seed: int,
    enabled: tuple[FamilyName, ...],
) -> ExperimentConfig:
    """Materialize the documented P=6, W=10, two-seed in-process fairness configuration."""
    limits = GenerationLimits(max_packets=50_000, max_output_bytes=64 * 1024 * 1024, max_wall_seconds=30.0)
    configured = config.model_copy(
        update={
            "run": config.run.model_copy(update={"master_seed": master_seed, "final_seed": 97}),
            "generation": config.generation.model_copy(update={"trial": limits, "final": limits}),
            "genetic": config.genetic.model_copy(
                update={
                    "population_size": 6,
                    "generation_count": 1,
                    "tournament_size": 2,
                    "elite_count": 1,
                    "trial_seeds": (17, 29),
                    "duplicate_mutation_attempts": 1,
                    "early_stopping_generations": 0,
                    "early_stopping_tolerance": 0.0,
                    "resume": False,
                }
            ),
            "models": config.models.model_copy(update={"enabled": enabled}),
            "similarity": config.similarity.model_copy(
                update={"method_weights": MethodWeights(**_MIXED_METHOD_WEIGHTS)}
            ),
        }
    )
    rendered = render_effective_config(configured)
    caller_path.write_bytes(rendered)
    (run_directory / "experiment.toml").write_bytes(rendered)
    return configured


def _mixed_trial(family: FamilyName, seed: int) -> TrialResult:
    """Build deterministic component inputs while retaining the production strategy/fit owners."""
    components = _MIXED_COMPONENT_SCORES[family]
    methods = cast(
        tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult],
        tuple(
            MethodTrialResult(name=name, score=components[name], diagnostics={"matrix_family": family})
            for name in METHOD_ORDER
        ),
    )
    return TrialResult(
        seed=seed,
        aggregate_score=math.fsum(_MIXED_METHOD_WEIGHTS[name] * components[name] for name in METHOD_ORDER),
        methods=methods,
        model_diagnostics={name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS} if family == "markov_renewal" else {},
    )


def _install_mixed_matrix_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject only evaluated trial measurements; strategy, reproduction, checkpointing, and fit stay real."""

    def evaluate(candidate: Candidate, context: ValidatedEvaluationContext) -> Candidate:
        if candidate.status != "pending":
            return candidate
        trials = tuple(_mixed_trial(candidate.family, seed) for seed in context.trial_seeds)
        return replace(
            candidate,
            status="valid",
            fitness=math.fsum(trial.aggregate_score for trial in trials) / len(trials),
            trials=trials,
            invalid=None,
        )

    monkeypatch.setattr(genetic_strategy, "evaluate_candidate", evaluate)


def _interrupt_after_generation_zero(run_directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dependencies = _portable_dependencies(run_directory)
    real_publish = genetic_strategy.publish_generation

    def publish_then_interrupt(destination: Path, state: CheckpointState) -> None:
        real_publish(destination, state)
        if state.generation == 0:
            raise KeyboardInterrupt

    with monkeypatch.context() as scoped:
        scoped.setattr(genetic_strategy, "publish_generation", publish_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            fit_experiment(Path("fixture-experiment.toml"), dependencies=dependencies)


def test_checked_fit_artifacts_load_through_every_strict_production_codec() -> None:
    """A byte fixture that bypasses any strict codec would not be reusable scientific evidence."""
    config = load_experiment(_EXPERIMENT_PATH)
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=_FIT_DIRECTORY / "capture.json")
    parsed = parse_pcapng_bytes(_REFERENCE_BYTES, metadata, source=_FIT_DIRECTORY / "reference.pcapng")
    reference, window = normalize_reference(parsed)
    context = make_strategy_context(
        config,
        reference,
        window,
        _FIT_DIRECTORY,
        experiment_identity=identify_bytes(_EXPERIMENT_BYTES),
        reference_identity=identify_bytes(_REFERENCE_BYTES),
        capture_identity=identify_bytes(_CAPTURE_BYTES),
    )
    checkpoint = load_checkpoint(_FIT_DIRECTORY / "checkpoint.json", context.compatibility)
    best = load_best_model((_FIT_DIRECTORY / "best_model.json").read_bytes(), source=_FIT_DIRECTORY / "best_model.json")

    assert tuple(family.name for family in checkpoint.compatibility.families) == _FAMILY_ORDER
    assert checkpoint.family_priority == checkpoint.compatibility.family_priority
    assert (checkpoint.compatibility.genetic.population_size, checkpoint.generation) == (6, 1)
    assert render_history_csv(checkpoint) == (_FIT_DIRECTORY / "ga_history.csv").read_bytes()
    assert render_best_model(best) == (_FIT_DIRECTORY / "best_model.json").read_bytes()
    assert best.observation_window_seconds == window == 10.0


def test_small_nondefault_three_family_population_keeps_each_family_and_common_evaluation_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping a family or giving it privileged seeds, limits, or W would invalidate heterogeneous competition."""
    caller_path, run_directory, config = _copy_fixture_experiment(tmp_path)
    fit_trace: list[tuple[FamilyName, float]] = []
    generation_trace: list[tuple[FamilyName, int, float, GenerationLimits]] = []
    real_fit = genetic_evaluation._fit_candidate  # pyright: ignore[reportPrivateUsage]
    real_generate = genetic_evaluation._generate_candidate  # pyright: ignore[reportPrivateUsage]

    def traced_fit(candidate: Candidate, context: ValidatedEvaluationContext) -> FittedModel:
        fit_trace.append((candidate.family, context.window))
        return real_fit(candidate, context)

    def traced_generate(
        candidate: Candidate,
        model: FittedModel,
        seed: int,
        context: ValidatedEvaluationContext,
    ) -> GenerationResult:
        generation_trace.append((candidate.family, seed, context.window, context.trial_limits))
        return real_generate(candidate, model, seed, context)

    monkeypatch.setattr(genetic_evaluation, "_fit_candidate", traced_fit)
    monkeypatch.setattr(genetic_evaluation, "_generate_candidate", traced_generate)

    result = fit_experiment(caller_path)
    context = _strategy_context(config, run_directory)
    checkpoint = load_checkpoint(run_directory / "checkpoint.json", context.compatibility)
    families = {candidate.family for candidate in checkpoint.population}
    stored_operators = {
        family.name: (
            family.crossover_probability,
            family.mutation_probability,
            family.mutation_scale,
        )
        for family in checkpoint.compatibility.families
    }

    assert families == set(_FAMILY_ORDER)
    assert result.best_model.family in families
    assert stored_operators == _OPERATOR_VALUES
    assert {family for family, window in fit_trace if window == 10.0} == set(_FAMILY_ORDER)
    assert {family for family, seed, _window, _limits in generation_trace if seed == 17} == set(_FAMILY_ORDER)
    assert all(window == 10.0 and limits == config.generation.trial for _, _, window, limits in generation_trace)
    assert [(family, seed) for family, seed, _, _ in generation_trace if seed != 17] == [(result.best_model.family, 97)]
    assert tuple(trial.seed for trial in result.outcome.final_trials) == (97,)
    assert all(
        tuple(trial.seed for trial in candidate.trials) == (17,)
        for candidate in checkpoint.population
        if candidate.status == "valid"
    )


@dataclass(slots=True)
class _MutationTrace:
    family: FamilyName
    operators: tuple[float, float, float]
    force_if_none: bool
    mandatory_integers: bool
    before: Genes
    after: Genes


@dataclass(slots=True)
class _ReproductionTrace:
    parent_families: tuple[FamilyName, FamilyName]
    probabilities: list[float]
    mutations: list[_MutationTrace]
    child_family: FamilyName | None = None


def test_real_strategy_trace_proves_same_family_only_crossover_and_cross_family_forced_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-family chromosomes must never enter uniform crossover or borrow another family's operators."""
    caller_path, _, config = _copy_fixture_experiment(tmp_path)
    traces: list[_ReproductionTrace] = []
    active: _ReproductionTrace | None = None
    selection_call = 0
    real_reproduce = genetic_operators.reproduce_child
    real_bernoulli = genetic_operators.bernoulli
    real_mutate = genetic_operators._mutate_genes  # pyright: ignore[reportPrivateUsage]

    def scripted_select(
        candidates: Sequence[Candidate], *, tournament_size: int, rng: Random, family_priority: tuple[FamilyName, ...]
    ) -> Candidate:
        del tournament_size, rng
        nonlocal selection_call
        pair, side = divmod(selection_call, 2)
        selection_call += 1
        if pair == 0:
            choices = sorted(
                (candidate for candidate in candidates if candidate.family == "markov_renewal"),
                key=lambda candidate: candidate.identifier,
            )
            return choices[side]
        if pair == 1:
            ranked = rank_candidates(candidates, family_priority=family_priority)
            source = ranked[0]
            other = next(candidate for candidate in reversed(ranked) if candidate.family != source.family)
            return (source, other)[side]
        choices = sorted(
            (candidate for candidate in candidates if candidate.family == "poisson_empirical"),
            key=lambda candidate: candidate.identifier,
        )
        return choices[side]

    def traced_bernoulli(rng: Random, probability: float) -> bool:
        assert active is not None
        active.probabilities.append(probability)
        return real_bernoulli(rng, probability)

    def traced_mutate(
        genes: Genes,
        *,
        family: FamilyName,
        context: ReproductionContext,
        rng: Random,
        force_if_none: bool,
        mandatory_integers: bool,
    ) -> Genes:
        assert active is not None
        result = real_mutate(
            genes,
            family=family,
            context=context,
            rng=rng,
            force_if_none=force_if_none,
            mandatory_integers=mandatory_integers,
        )
        active.mutations.append(
            _MutationTrace(
                family,
                context.operators_for(family).operator_values,
                force_if_none,
                mandatory_integers,
                genes,
                result,
            )
        )
        return result

    def traced_reproduce(
        parent_a: Candidate,
        parent_b: Candidate,
        *,
        context: ReproductionContext,
        identifier: CandidateId,
        rng: Random,
    ) -> Candidate:
        nonlocal active
        trace = _ReproductionTrace((parent_a.family, parent_b.family), [], [])
        traces.append(trace)
        active = trace
        try:
            child = real_reproduce(parent_a, parent_b, context=context, identifier=identifier, rng=rng)
        finally:
            active = None
        trace.child_family = child.family
        return child

    monkeypatch.setattr(genetic_operators, "tournament_select", scripted_select)
    monkeypatch.setattr(genetic_operators, "bernoulli", traced_bernoulli)
    monkeypatch.setattr(genetic_operators, "_mutate_genes", traced_mutate)
    monkeypatch.setattr(genetic_operators, "reproduce_child", traced_reproduce)

    fit_experiment(caller_path)

    assert len(traces) == 3
    markov_same = next(trace for trace in traces if trace.parent_families == ("markov_renewal",) * 2)
    cross = next(trace for trace in traces if trace.parent_families[0] != trace.parent_families[1])
    assert markov_same.probabilities[:6] == [1.0, *([0.5] * 5)]
    assert all(
        0.5 not in trace.probabilities for trace in traces if trace.parent_families[0] != trace.parent_families[1]
    )
    assert cross.mutations[0].force_if_none is True
    assert cross.mutations[0].mandatory_integers is True
    assert cross.mutations[0].before != cross.mutations[0].after
    assert all(
        mutation.operators == _OPERATOR_VALUES[mutation.family] for trace in traces for mutation in trace.mutations
    )
    assert all(trace.child_family == mutation.family for trace in traces for mutation in trace.mutations)
    assert all(trace.child_family in config.models.enabled for trace in traces)


def test_guard_truncated_real_candidate_is_invalid_with_direct_seed_reason(tmp_path: Path) -> None:
    """A packet guard must become candidate evidence, not an infrastructure abort or a shortened W."""
    config = _portable_config()
    trial = config.generation.trial.model_copy(update={"max_packets": 1})
    genetic = config.genetic.model_copy(update={"generation_count": 0})
    limited = config.model_copy(
        update={"generation": config.generation.model_copy(update={"trial": trial}), "genetic": genetic}
    )
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=Path("capture.json"))
    parsed = parse_pcapng_bytes(_REFERENCE_BYTES, metadata, source=Path("reference.pcapng"))
    reference, window = normalize_reference(parsed)
    context = make_strategy_context(
        limited,
        reference,
        window,
        tmp_path,
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )

    with pytest.raises(TrafficlabError, match="final validation"):
        run_strategy(context)
    checkpoint = load_checkpoint(tmp_path / "checkpoint.json", context.compatibility)
    truncated = [
        candidate
        for candidate in checkpoint.population
        if candidate.invalid is not None and candidate.invalid.kind == "incomplete_generation"
    ]

    assert truncated
    assert all(
        (candidate.status, candidate.fitness, candidate.trials) == ("invalid", 0.0, ()) for candidate in truncated
    )
    assert all(candidate.invalid is not None and candidate.invalid.seed == 17 for candidate in truncated)
    assert all(candidate.invalid is not None and candidate.invalid.detail == "max_packets" for candidate in truncated)
    assert context.evaluation.window == window == 10.0


def test_interrupt_resume_is_byte_identical_and_operator_tamper_precedes_reproduction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing RNG bytes or checking operators after reproduction would make resume scientifically different."""
    full_directory = tmp_path / "full"
    full = fit_experiment(Path("fixture-experiment.toml"), dependencies=_portable_dependencies(full_directory))
    resume_directory = tmp_path / "resume"
    _interrupt_after_generation_zero(resume_directory, monkeypatch)
    resumed = fit_experiment(Path("fixture-experiment.toml"), dependencies=_portable_dependencies(resume_directory))

    assert resumed.outcome == full.outcome
    assert resumed.outcome.family_priority == full.outcome.family_priority
    for name in ("checkpoint.json", "ga_history.csv", "best_model.json"):
        assert (resume_directory / name).read_bytes() == (full_directory / name).read_bytes()

    tamper_directory = tmp_path / "tamper"
    _interrupt_after_generation_zero(tamper_directory, monkeypatch)
    checkpoint_path = tamper_directory / "checkpoint.json"
    document = cast(dict[str, object], json.loads(checkpoint_path.read_bytes()))
    families = cast(list[dict[str, object]], document["families"])
    mmpp = next(family for family in families if family["name"] == "mmpp")
    operators = cast(dict[str, object], mmpp["operators"])
    operators["mutation_scale"] = 0.081
    checkpoint_path.write_bytes(
        (json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    )
    reproduction_called = False

    def forbidden_reproduction(*_args: object, **_kwargs: object) -> tuple[Candidate, ...]:
        nonlocal reproduction_called
        reproduction_called = True
        raise AssertionError("checkpoint compatibility must fail before reproduction")

    monkeypatch.setattr(genetic_strategy, "fill_next_population", forbidden_reproduction)
    with pytest.raises(TrafficlabError, match="operator values for family mmpp"):
        fit_experiment(Path("fixture-experiment.toml"), dependencies=_portable_dependencies(tamper_directory))
    assert reproduction_called is False


def test_configuration_and_registry_permutations_keep_priority_population_children_and_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Input order must not change any priority-governed search evidence."""
    import trafficlab.models.registry as model_registry

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_path, first_directory, first_config = _copy_fixture_experiment(first_root)
    second_path, second_directory, second_config = _copy_fixture_experiment(second_root)
    permuted_models = second_config.models.model_copy(update={"enabled": tuple(reversed(second_config.models.enabled))})
    permuted_config = second_config.model_copy(update={"models": permuted_models})
    rendered = render_effective_config(permuted_config)
    second_path.write_bytes(rendered)
    (second_directory / "experiment.toml").write_bytes(rendered)

    first = fit_experiment(first_path)
    original_registry = model_registry.REGISTRY
    monkeypatch.setattr(
        model_registry,
        "REGISTRY",
        {name: original_registry[name] for name in reversed(tuple(original_registry))},
    )
    second = fit_experiment(second_path)

    first_state = load_checkpoint(
        first_directory / "checkpoint.json", _strategy_context(first_config, first_directory).compatibility
    )
    second_state = load_checkpoint(
        second_directory / "checkpoint.json",
        _strategy_context(permuted_config, second_directory).compatibility,
    )
    assert first_state.family_priority == second_state.family_priority
    assert first.outcome.family_priority == second.outcome.family_priority == first_state.family_priority
    assert first_state.population == second_state.population
    assert first_state.history == second_state.history
    assert first.outcome.winner == second.outcome.winner


@pytest.mark.parametrize(
    ("master_seed", "expected_priority"),
    (
        (4, ("markov_renewal", "mmpp", "poisson_empirical")),
        (0, ("mmpp", "poisson_empirical", "markov_renewal")),
        (6, ("poisson_empirical", "markov_renewal", "mmpp")),
    ),
)
def test_in_process_fairness_matrix_preserves_slots_children_and_mixed_mmpp_winner_across_orders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    master_seed: int,
    expected_priority: tuple[FamilyName, ...],
) -> None:
    """The public fit owner must retain the complete documented fairness evidence across input orders."""
    import trafficlab.models.registry as model_registry

    _install_mixed_matrix_scoring(monkeypatch)
    initial_populations: list[tuple[Candidate, ...]] = []
    published: dict[Path, list[CheckpointState]] = {}
    real_initial_population = genetic_strategy.initial_population
    real_publish = genetic_strategy.publish_generation

    def trace_initial_population(*args: object, **kwargs: object) -> tuple[Candidate, ...]:
        population = real_initial_population(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        initial_populations.append(population)
        return population

    def trace_publish(destination: Path, state: CheckpointState) -> None:
        published.setdefault(destination, []).append(state)
        real_publish(destination, state)

    monkeypatch.setattr(genetic_strategy, "initial_population", trace_initial_population)
    monkeypatch.setattr(genetic_strategy, "publish_generation", trace_publish)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_path, first_directory, first_config = _copy_fixture_experiment(first_root)
    second_path, second_directory, second_config = _copy_fixture_experiment(second_root)
    first_config = _configure_fairness_matrix(
        first_config,
        first_path,
        first_directory,
        master_seed=master_seed,
        enabled=tuple(_FAMILY_ORDER),
    )
    second_config = _configure_fairness_matrix(
        second_config,
        second_path,
        second_directory,
        master_seed=master_seed,
        enabled=tuple(reversed(_FAMILY_ORDER)),
    )

    first = fit_experiment(first_path)
    original_registry = model_registry.REGISTRY
    monkeypatch.setattr(
        model_registry,
        "REGISTRY",
        {name: original_registry[name] for name in reversed(tuple(original_registry))},
    )
    second = fit_experiment(second_path)
    first_context = _strategy_context(first_config, first_directory)
    second_context = _strategy_context(second_config, second_directory)
    first_state = load_checkpoint(first_directory / "checkpoint.json", first_context.compatibility)
    second_state = load_checkpoint(second_directory / "checkpoint.json", second_context.compatibility)
    expected_slots = tuple(family for family in expected_priority for _ in range(2))
    expected_children = tuple(CandidateId(birth_generation=1, birth_index=index) for index in range(3))
    expected_mmpp_score = math.fsum(
        _MIXED_METHOD_WEIGHTS[name] * _MIXED_COMPONENT_SCORES["mmpp"][name] for name in METHOD_ORDER
    )

    assert (
        first_context.compatibility.observation_window_seconds
        == second_context.compatibility.observation_window_seconds
        == 10.0
    )
    assert first_context.compatibility.trial_seeds == second_context.compatibility.trial_seeds == (17, 29)
    assert first_state.family_priority == second_state.family_priority == expected_priority
    assert len(initial_populations) == 2
    assert all(
        tuple(candidate.family for candidate in population) == expected_slots for population in initial_populations
    )
    assert all(
        tuple(candidate.identifier for candidate in population)
        == tuple(CandidateId(birth_generation=0, birth_index=index) for index in range(6))
        for population in initial_populations
    )
    assert all(
        {family: sum(candidate.family == family for candidate in population) for family in expected_priority}
        == {family: 2 for family in expected_priority}
        for population in initial_populations
    )
    assert tuple(state.generation for state in published[first_directory]) == (0, 1)
    assert tuple(state.generation for state in published[second_directory]) == (0, 1)
    assert first_state.population == second_state.population
    assert first_state.history == second_state.history
    assert first_state.best_identifier == second_state.best_identifier
    assert first.outcome.winner == second.outcome.winner
    assert first.outcome.family_priority == second.outcome.family_priority == expected_priority
    assert (
        tuple(
            candidate.identifier for candidate in first_state.population if candidate.identifier.birth_generation == 1
        )
        == expected_children
    )
    assert (
        tuple(
            candidate.identifier for candidate in second_state.population if candidate.identifier.birth_generation == 1
        )
        == expected_children
    )
    assert all(candidate.status == "valid" for candidate in first_state.population)
    assert all(tuple(trial.seed for trial in candidate.trials) == (17, 29) for candidate in first_state.population)
    winner = next(
        candidate for candidate in first_state.population if candidate.identifier == first_state.best_identifier
    )
    assert winner.family == first.outcome.winner.family == "mmpp"
    assert winner.fitness == first_state.best_fitness == expected_mmpp_score
    assert first_state.history[-1].best_identifier == winner.identifier


def test_offline_fit_generate_compare_preserves_one_window_without_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The analytical offline chain must use real stages without drifting W or touching Docker."""
    caller_path, run_directory, config = _copy_fixture_experiment(tmp_path)

    class ForbiddenDocker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("offline fit/generate/compare constructed the Docker boundary")

    def reject_subprocess(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline fit/generate/compare invoked a subprocess")

    with monkeypatch.context() as offline:
        offline.setattr(docker_cli, "DockerCompose", ForbiddenDocker)
        offline.setattr(subprocess, "run", reject_subprocess)
        offline.setattr(subprocess, "Popen", reject_subprocess)
        fit = fit_experiment(caller_path)
        generated = generate_experiment(fit.experiment_path)
        comparison = compare_experiment(fit.experiment_path)
    loaded_comparison = load_comparison_result(run_directory / "similarity.json")
    loaded_best = load_best_model(
        (run_directory / "best_model.json").read_bytes(), source=run_directory / "best_model.json"
    )
    context = _strategy_context(config, run_directory)
    load_checkpoint(run_directory / "checkpoint.json", context.compatibility)

    assert fit.observation_window_seconds == generated.observation_window_seconds == 10.0
    assert comparison.observation_window_seconds == loaded_comparison.observation_window_seconds == 10.0
    assert loaded_best.observation_window_seconds == 10.0
    assert generated.seed == config.run.final_seed == 97
    assert all(method.diagnostics["observation_window_seconds"] == 10.0 for method in comparison.methods.values())
