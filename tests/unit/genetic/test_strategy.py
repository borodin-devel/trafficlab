"""Lifecycle tests for the resumable generational strategy."""

from __future__ import annotations

import copy
from dataclasses import replace as replace_dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import FailureOutcome, TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.fitting.genetic import strategy
from trafficlab.fitting.genetic.checkpoint import CheckpointState, load_checkpoint, render_checkpoint
from trafficlab.fitting.genetic.evaluation import ValidatedEvaluationContext
from trafficlab.fitting.genetic.strategy import (
    StrategyContext,
    advance_termination_state,
    initialize_or_resume,
    make_strategy_context,
    run_strategy,
    should_stop_early,
)
from trafficlab.fitting.genetic.types import (
    METHOD_ORDER,
    Candidate,
    CandidateFailure,
    CandidateId,
    MethodTrialResult,
    TrialResult,
)
from trafficlab.generation.models.common import MARKOV_MODEL_DIAGNOSTIC_KEYS, make_rng


def replace[Record](record: Record, **changes: object) -> Record:
    """Build deliberate model states at this test boundary."""
    if isinstance(record, BaseModel):
        values = {name: getattr(record, name) for name in type(record).model_fields}
        values.update(changes)
        return cast(Record, type(record).model_construct(**values))
    return cast(Record, replace_dataclass(cast(Any, record), **changes))


REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(1.0, Direction.INBOUND, 128),
        TraceEvent(2.0, Direction.OUTBOUND, 256),
    )
)
MATRIX_REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(5.0, Direction.INBOUND, 128),
        TraceEvent(10.0, Direction.OUTBOUND, 256),
    )
)


def _config(
    valid_config_data: dict[str, object],
    run_directory: Path,
    *,
    generation_count: int,
    resume: bool = True,
    early_stopping_generations: int = 0,
    early_stopping_tolerance: float = 0.0,
) -> ExperimentConfig:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    genetic = cast(dict[str, object], data["genetic"])
    genetic.update(
        population_size=2,
        generation_count=generation_count,
        tournament_size=2,
        elite_count=1,
        trial_seeds=[7],
        duplicate_mutation_attempts=1,
        early_stopping_generations=early_stopping_generations,
        early_stopping_tolerance=early_stopping_tolerance,
        resume=resume,
    )
    run = cast(dict[str, object], data["run"])
    run["master_seed"] = 73
    run["final_seed"] = 101
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["poisson_empirical"]
    models["markov_renewal"] = None
    models["mmpp"] = None
    return ExperimentConfig.model_validate(data)


def _context(
    valid_config_data: dict[str, object],
    run_directory: Path,
    *,
    generation_count: int,
    resume: bool = True,
    early_stopping_generations: int = 0,
    early_stopping_tolerance: float = 0.0,
) -> StrategyContext:
    run_directory.mkdir(parents=True, exist_ok=True)
    return make_strategy_context(
        _config(
            valid_config_data,
            run_directory,
            generation_count=generation_count,
            resume=resume,
            early_stopping_generations=early_stopping_generations,
            early_stopping_tolerance=early_stopping_tolerance,
        ),
        REFERENCE,
        2.0,
        run_directory,
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )


def test_strategy_context_retains_the_exact_columnar_reference(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strategy setup must not turn the shared NumPy reference back into event records."""
    trace = REFERENCE

    def reject_event_materialization(_trace: TrafficTrace) -> tuple[TraceEvent, ...]:
        raise AssertionError("strategy materialized TraceEvent objects")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    context = make_strategy_context(
        _config(valid_config_data, run_directory, generation_count=0),
        trace,
        2.0,
        run_directory,
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )

    assert context.evaluation.reference is trace


def _matrix_context(
    valid_config_data: dict[str, object],
    run_directory: Path,
    *,
    master_seed: int,
    generation_count: int,
    population_size: int = 6,
    enabled: tuple[str, ...] = ("markov_renewal", "mmpp", "poisson_empirical"),
) -> StrategyContext:
    """Build the documented in-process fairness configuration without using a stage fake."""
    data = copy.deepcopy(valid_config_data)
    run_directory.mkdir(parents=True, exist_ok=True)
    run = cast(dict[str, object], data["run"])
    run.update(directory=str(run_directory), master_seed=master_seed, final_seed=97)
    genetic = cast(dict[str, object], data["genetic"])
    genetic.update(
        population_size=population_size,
        generation_count=generation_count,
        tournament_size=2,
        elite_count=1,
        trial_seeds=[17, 29],
        duplicate_mutation_attempts=1,
        early_stopping_generations=0,
        early_stopping_tolerance=0.0,
        resume=False,
    )
    generation = cast(dict[str, object], data["generation"])
    generation["trial"] = {
        "max_packets": 50_000,
        "max_output_bytes": 64 * 1024 * 1024,
        "max_wall_seconds": 30.0,
    }
    generation["final"] = dict(cast(dict[str, object], generation["trial"]))
    models = cast(dict[str, object], data["models"])
    models["enabled"] = list(enabled)
    for family in ("markov_renewal", "mmpp", "poisson_empirical"):
        if family not in enabled:
            models[family] = None
    config = ExperimentConfig.model_validate(data)
    return make_strategy_context(
        config,
        MATRIX_REFERENCE,
        10.0,
        run_directory,
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )


def _trial(seed: int, score: float, *, family: str = "poisson_empirical") -> TrialResult:
    methods = tuple(MethodTrialResult(name=name, score=score, diagnostics={"literal": score}) for name in METHOD_ORDER)
    diagnostics = {name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS} if family == "markov_renewal" else {}
    return TrialResult(seed=seed, aggregate_score=score, methods=cast(Any, methods), model_diagnostics=diagnostics)


def _install_scoring(
    monkeypatch: pytest.MonkeyPatch,
    scores: dict[int, tuple[float, ...]],
    events: list[str] | None = None,
) -> None:
    seen_generations: set[int] = set()

    def evaluate(candidate: Candidate, context: ValidatedEvaluationContext) -> Candidate:
        if candidate.status != "pending":
            return candidate
        generation = candidate.identifier.birth_generation
        if events is not None and generation not in seen_generations:
            events.append(f"evaluate:{generation}")
            seen_generations.add(generation)
        generation_scores = scores[generation]
        score = generation_scores[candidate.identifier.birth_index % len(generation_scores)]
        return replace(
            candidate,
            status="valid",
            fitness=score,
            trials=(_trial(context.trial_seeds[0], score, family=candidate.family),),
        )

    def final(candidate: Candidate, context: ValidatedEvaluationContext, final_seed: int) -> tuple[TrialResult, ...]:
        if events is not None:
            events.append(f"final:{candidate.identifier.birth_generation}")
        return (_trial(final_seed, candidate.fitness, family=candidate.family),)

    monkeypatch.setattr(strategy, "evaluate_candidate", evaluate)
    monkeypatch.setattr(strategy, "evaluate_final", final)


def _install_family_scoring(
    monkeypatch: pytest.MonkeyPatch,
    scores: dict[int, dict[str, float]],
    *,
    invalid: bool = False,
) -> None:
    """Control evaluated trial inputs while retaining public strategy/checkpoint ownership."""

    def evaluate(candidate: Candidate, context: ValidatedEvaluationContext) -> Candidate:
        if candidate.status != "pending":
            return candidate
        if invalid:
            return replace(
                candidate,
                status="invalid",
                fitness=0.0,
                trials=(),
                invalid=CandidateFailure(
                    kind="fit",
                    seed=None,
                    detail="controlled symmetric fit failure",
                    stage="fit",
                    affected_evidence="candidate model",
                    evidence_state="diagnostic_only",
                    corrective_action="repair the candidate model",
                    authority="primary",
                ),
            )
        score = scores[candidate.identifier.birth_generation][candidate.family]
        return replace(
            candidate,
            status="valid",
            fitness=score,
            trials=tuple(_trial(seed, score, family=candidate.family) for seed in context.trial_seeds),
            invalid=None,
        )

    monkeypatch.setattr(strategy, "evaluate_candidate", evaluate)


def test_generation_zero_is_evaluated_and_checkpointed_before_first_reproduction(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving initialization after reproduction would skip the required generation-zero checkpoint."""
    events: list[str] = []
    _install_scoring(monkeypatch, {0: (0.4, 0.5), 1: (0.6,)}, events)
    original_initial = strategy.initial_population
    original_fill = strategy.fill_next_population
    original_publish = strategy.publish_generation
    original_validate = strategy.validate_evaluation_context
    validation_count = 0

    def validate(raw: object) -> ValidatedEvaluationContext:
        nonlocal validation_count
        validation_count += 1
        return original_validate(cast(Any, raw))

    def initialize(*args: object, **kwargs: object) -> tuple[Candidate, ...]:
        events.append("initialize")
        return original_initial(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    def reproduce(*args: object, **kwargs: object) -> tuple[Candidate, ...]:
        events.append("reproduce:1")
        return original_fill(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    def publish(run_directory: Path, state: CheckpointState) -> None:
        events.append(f"checkpoint:{state.generation}")
        original_publish(run_directory, state)

    monkeypatch.setattr(strategy, "initial_population", initialize)
    monkeypatch.setattr(strategy, "fill_next_population", reproduce)
    monkeypatch.setattr(strategy, "publish_generation", publish)
    monkeypatch.setattr(strategy, "validate_evaluation_context", validate)

    outcome = run_strategy(_context(valid_config_data, tmp_path / "run", generation_count=1))

    assert events == [
        "initialize",
        "evaluate:0",
        "checkpoint:0",
        "reproduce:1",
        "evaluate:1",
        "checkpoint:1",
        "final:1",
    ]
    assert outcome.generation == 1
    assert validation_count == 1


def test_generation_count_zero_checkpoints_hard_terminal_generation_zero(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treating G as a count of total generations would omit the mandatory generation zero."""
    events: list[str] = []
    _install_scoring(monkeypatch, {0: (0.4, 0.5)}, events)
    context = _context(valid_config_data, tmp_path / "run", generation_count=0)

    outcome = run_strategy(context)
    state = load_checkpoint(context.run_directory / "checkpoint.json", context.compatibility)

    assert (outcome.generation, state.generation, state.terminal_reason) == (0, 0, "hard_limit")
    assert not any(event.startswith("reproduce:") for event in events)


def test_early_stop_tolerance_and_hard_limit_precedence_are_exact() -> None:
    """Using >= for improvement or early-stop-first ordering changes the documented boundary."""
    assert should_stop_early(2, early_stopping_generations=2)
    assert not should_stop_early(20, early_stopping_generations=0)
    assert (
        advance_termination_state(
            2,
            generation_count=2,
            consecutive_stagnation=2,
            early_stopping_generations=2,
        )
        == "hard_limit"
    )
    assert (
        advance_termination_state(
            1,
            generation_count=2,
            consecutive_stagnation=2,
            early_stopping_generations=2,
        )
        == "early_stop"
    )


def test_small_improvement_updates_winner_but_increments_stagnation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coupling retention to tolerance would keep a scientifically inferior prior winner."""
    _install_scoring(monkeypatch, {0: (0.5, 0.4), 1: (0.505,)})
    context = _context(
        valid_config_data,
        tmp_path / "run",
        generation_count=1,
        early_stopping_generations=1,
        early_stopping_tolerance=0.01,
    )

    outcome = run_strategy(context)
    state = load_checkpoint(context.run_directory / "checkpoint.json", context.compatibility)

    assert state.best_fitness == 0.505
    assert state.best_identifier == outcome.winner.identifier
    assert state.consecutive_stagnation == 1
    assert state.terminal_reason == "hard_limit"


@pytest.mark.parametrize(
    ("third_score", "expected_generation", "expected_reason"),
    [(0.12, 2, "early_stop"), (0.1200001, 3, "hard_limit")],
)
def test_early_stop_counts_only_consecutive_improvements_not_greater_than_tolerance(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    third_score: float,
    expected_generation: int,
    expected_reason: str,
) -> None:
    """An exact-tolerance gain stagnates, while any larger gain resets the consecutive counter."""
    _install_scoring(
        monkeypatch,
        {0: (0.1, 0.05), 1: (0.11,), 2: (third_score,), 3: (0.05,)},
    )
    context = _context(
        valid_config_data,
        tmp_path / f"run-{third_score}",
        generation_count=3,
        early_stopping_generations=2,
        early_stopping_tolerance=0.01,
    )

    outcome = run_strategy(context)
    state = load_checkpoint(context.run_directory / "checkpoint.json", context.compatibility)

    assert (outcome.generation, outcome.terminal_reason) == (expected_generation, expected_reason)
    assert state.best_fitness == third_score
    assert state.consecutive_stagnation == (2 if third_score == 0.12 else 1)


def test_resume_absent_starts_fresh_and_false_rejects_present_checkpoint(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume policy must neither require an absent checkpoint nor overwrite an existing one."""
    fresh = _context(valid_config_data, tmp_path / "fresh", generation_count=0, resume=True)
    assert initialize_or_resume(fresh) is None

    _install_scoring(monkeypatch, {0: (0.4, 0.5)})
    occupied = _context(valid_config_data, tmp_path / "occupied", generation_count=0, resume=False)
    run_strategy(occupied)
    before = (occupied.run_directory / "checkpoint.json").read_bytes()

    with pytest.raises(TrafficlabError, match="resume"):
        initialize_or_resume(occupied)

    assert (occupied.run_directory / "checkpoint.json").read_bytes() == before


@pytest.mark.parametrize(
    ("checkpoint_state", "expected_kind"),
    [
        ("parse", "artifact_corrupt"),
        ("schema", "scientific_semantics_incompatible"),
        ("incompatible", "scientific_semantics_incompatible"),
    ],
)
def test_resume_checkpoint_failures_retain_the_canonical_preserved_outcome(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_state: str,
    expected_kind: str,
) -> None:
    """The resume owner must classify checkpoint bytes before fit-stage logging sees them."""
    context = _context(valid_config_data, tmp_path / checkpoint_state, generation_count=0)
    _install_scoring(monkeypatch, {0: (0.4, 0.5)})
    run_strategy(context)
    checkpoint_path = context.run_directory / "checkpoint.json"
    original = checkpoint_path.read_bytes()
    if checkpoint_state == "parse":
        checkpoint_path.write_bytes(b"{\n")
    elif checkpoint_state == "schema":
        checkpoint_path.write_bytes(b"{}\n")
    else:
        marker = b'"sha256":"' + (b"a" * 64) + b'"'
        checkpoint_path.write_bytes(original.replace(marker, b'"sha256":"' + (b"0" * 64) + b'"', 1))

    with pytest.raises(TrafficlabError) as captured:
        initialize_or_resume(context)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (expected_kind, "fit", "checkpoint.json", "preserved", "primary")
    if expected_kind == "artifact_corrupt":
        assert str(captured.value).startswith("invalid checkpoint:")
        assert captured.value.corrective_action == (
            "preserve the checkpoint and resume from a compatible complete generation"
        )
        assert outcome.detail == "checkpoint.json is corrupt"
        assert outcome.corrective_action == "recreate fit in a new run directory"
    else:
        assert outcome.detail == str(captured.value)
        assert outcome.corrective_action == captured.value.corrective_action


def test_resume_keeps_an_existing_checkpoint_outcome_without_reclassification(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume boundary must not replace an already classified checkpoint failure."""
    context = _context(valid_config_data, tmp_path / "classified", generation_count=0)
    checkpoint_path = context.run_directory / "checkpoint.json"
    checkpoint_path.write_bytes(b"present\n")
    outcome = FailureOutcome(
        kind="artifact_corrupt",
        stage="fit",
        detail="checkpoint.json is corrupt",
        affected_evidence="checkpoint.json",
        evidence_state="preserved",
        corrective_action="recreate fit in a new run directory",
        authority="primary",
    )
    error = TrafficlabError(outcome.detail, corrective_action=outcome.corrective_action, failure_outcome=outcome)

    def fail_load(_directory: Path, _compatibility: object) -> object:
        raise error

    monkeypatch.setattr(strategy, "load_generation", fail_load)

    with pytest.raises(TrafficlabError) as captured:
        initialize_or_resume(context)

    assert captured.value is error
    assert captured.value.failure_outcomes == (outcome,)


def test_context_resolves_lexical_families_and_exact_effective_settings_once(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    """Input family order or defaults must not leak into tie-adjacent checkpoint metadata."""
    config = ExperimentConfig.model_validate(valid_config_data)
    context = make_strategy_context(
        config,
        REFERENCE,
        2.0,
        tmp_path,
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )

    assert tuple(context.evaluation.families) == ("markov_renewal", "mmpp", "poisson_empirical")
    assert tuple(spec.name for spec in context.compatibility.families) == (
        "markov_renewal",
        "mmpp",
        "poisson_empirical",
    )
    assert context.compatibility.families[0].gene_order == ("q1", "q2", "alpha", "r", "c_t")
    assert context.compatibility.families[1].crossover_probability == 0.9
    assert context.compatibility.genetic.master_seed == config.run.master_seed
    assert context.compatibility.genetic.final_seed == config.run.final_seed
    assert context.compatibility.genetic.early_stopping_tolerance == 0.0
    assert context.evaluation.trial_limits is config.generation.trial
    assert context.compatibility.similarity == config.similarity
    assert context.compatibility.similarity is not config.similarity
    names = tuple(spec.name for spec in context.compatibility.families)
    assert names == ("markov_renewal", "mmpp", "poisson_empirical")
    assert context.compatibility.family_priority == ("markov_renewal", "poisson_empirical", "mmpp")


@pytest.mark.parametrize("master_seed", (4, 0, 6))
def test_context_persists_each_documented_priority_seed_without_advancing_search_rng(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    master_seed: int,
) -> None:
    """The discarded priority stream must not perturb the dedicated search stream."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["master_seed"] = master_seed
    cast(dict[str, object], data["genetic"]).update(generation_count=0, trial_seeds=[7])
    config = ExperimentConfig.model_validate(data)
    context = make_strategy_context(
        config,
        REFERENCE,
        2.0,
        tmp_path / str(master_seed),
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )
    context.run_directory.mkdir(parents=True)
    expected_priority = {
        4: ("markov_renewal", "mmpp", "poisson_empirical"),
        0: ("poisson_empirical", "markov_renewal", "mmpp"),
        6: ("markov_renewal", "mmpp", "poisson_empirical"),
    }[master_seed]
    assert context.compatibility.family_priority == expected_priority

    search_states: list[object] = []
    original_initial_population = strategy.initial_population

    def record_search_state(*args: object, **kwargs: object) -> tuple[Candidate, ...]:
        rng = cast(Any, kwargs["rng"])
        search_states.append(rng.bit_generator.state)
        return original_initial_population(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(strategy, "initial_population", record_search_state)
    _install_scoring(monkeypatch, {0: (0.5, 0.4, 0.3, 0.2, 0.1, 0.0)})
    run_strategy(context)

    assert search_states == [make_rng(master_seed).bit_generator.state]


class _StopAfterPublishedGeneration(RuntimeError):
    pass


def test_higher_priority_equal_catch_up_replaces_lower_priority_incumbent_and_round_trips_checkpoint(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scientific tie catch-up must replace, but not reset stagnation from, a lower-priority incumbent."""
    context = _matrix_context(
        valid_config_data,
        tmp_path / "run",
        master_seed=2,
        generation_count=1,
        population_size=3,
        enabled=("poisson_empirical", "mmpp"),
    )
    assert context.compatibility.family_priority == ("mmpp", "poisson_empirical")
    _install_family_scoring(
        monkeypatch,
        {
            0: {"mmpp": 0.5, "poisson_empirical": 0.6},
            1: {"mmpp": 0.6, "poisson_empirical": 0.5},
        },
    )
    published: list[CheckpointState] = []
    real_publish = strategy.publish_generation

    def publish_then_stop(destination: Path, state: CheckpointState) -> None:
        real_publish(destination, state)
        published.append(state)
        if state.generation == 1:
            raise _StopAfterPublishedGeneration

    monkeypatch.setattr(strategy, "publish_generation", publish_then_stop)
    with pytest.raises(_StopAfterPublishedGeneration):
        run_strategy(context)

    state = load_checkpoint(context.run_directory / "checkpoint.json", context.compatibility)
    generation_zero, generation_one = published
    incumbent = next(
        candidate for candidate in generation_zero.population if candidate.identifier == generation_zero.best_identifier
    )
    winner = next(candidate for candidate in state.population if candidate.identifier == state.best_identifier)

    assert incumbent.family == "poisson_empirical"
    assert incumbent.fitness == 0.6
    assert generation_zero.history[-1].best_identifier == incumbent.identifier
    assert winner.family == "mmpp"
    assert winner.fitness == state.best_fitness == 0.6
    assert state.history[-1].best_identifier == winner.identifier
    assert state.consecutive_stagnation == 1
    assert state == generation_one
    assert render_checkpoint(state) == (context.run_directory / "checkpoint.json").read_bytes()


@pytest.mark.parametrize(
    ("master_seed", "expected_priority"),
    (
        (4, ("markov_renewal", "mmpp", "poisson_empirical")),
        (0, ("poisson_empirical", "markov_renewal", "mmpp")),
        (6, ("markov_renewal", "mmpp", "poisson_empirical")),
    ),
)
@pytest.mark.parametrize("invalid", (False, True), ids=("all-equal", "symmetric-invalid"))
def test_public_strategy_fairness_matrix_directs_equal_and_invalid_results_by_priority(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    master_seed: int,
    expected_priority: tuple[str, ...],
    invalid: bool,
) -> None:
    """The documented seeds must direct public strategy ties, slots, and history at W=10."""
    context = _matrix_context(
        valid_config_data,
        tmp_path / f"{master_seed}-{invalid}",
        master_seed=master_seed,
        generation_count=0,
    )
    assert context.compatibility.family_priority == expected_priority
    _install_family_scoring(
        monkeypatch,
        {0: {family: 0.5 for family in expected_priority}},
        invalid=invalid,
    )
    initial: list[tuple[Candidate, ...]] = []
    real_initial_population = strategy.initial_population
    real_publish = strategy.publish_generation

    def trace_initial_population(*args: object, **kwargs: object) -> tuple[Candidate, ...]:
        population = real_initial_population(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        initial.append(population)
        return population

    def publish_then_stop(destination: Path, state: CheckpointState) -> None:
        real_publish(destination, state)
        raise _StopAfterPublishedGeneration

    monkeypatch.setattr(strategy, "initial_population", trace_initial_population)
    monkeypatch.setattr(strategy, "publish_generation", publish_then_stop)
    with pytest.raises(_StopAfterPublishedGeneration):
        run_strategy(context)

    state = load_checkpoint(context.run_directory / "checkpoint.json", context.compatibility)
    winner = next(candidate for candidate in state.population if candidate.identifier == state.best_identifier)
    expected_slots = tuple(family for family in expected_priority for _ in range(2))

    assert tuple(candidate.family for candidate in initial[0]) == expected_slots
    assert tuple(candidate.identifier for candidate in initial[0]) == tuple(
        CandidateId(birth_generation=0, birth_index=index) for index in range(6)
    )
    assert context.evaluation.trial_seeds == (17, 29)
    assert context.compatibility.observation_window_seconds == 10.0
    assert winner.family == expected_priority[0]
    assert state.history[-1].best_identifier == winner.identifier
    assert state.history[-1].valid_count == (0 if invalid else 6)
    assert all(candidate.status == ("invalid" if invalid else "valid") for candidate in state.population)
    assert all(
        tuple(trial.seed for trial in candidate.trials) == (17, 29)
        for candidate in state.population
        if candidate.status == "valid"
    )


class _InterruptedAfterGenerationOne(RuntimeError):
    pass


def test_resume_matches_uninterrupted_population_history_winner_and_rng_state(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restoring RNG or counter late would change the next child, winner, or serialized state."""
    _install_scoring(monkeypatch, {0: (0.5, 0.4), 1: (0.505,), 2: (0.51,)})
    full = _context(
        valid_config_data,
        tmp_path / "full",
        generation_count=3,
        early_stopping_generations=2,
        early_stopping_tolerance=0.01,
    )
    resumed_context = _context(
        valid_config_data,
        tmp_path / "resumed",
        generation_count=3,
        early_stopping_generations=2,
        early_stopping_tolerance=0.01,
    )
    uninterrupted = run_strategy(full)
    original_publish = strategy.publish_generation
    interrupted = False

    def publish_then_interrupt(run_directory: Path, state: CheckpointState) -> None:
        nonlocal interrupted
        original_publish(run_directory, state)
        if run_directory == resumed_context.run_directory and state.generation == 1 and not interrupted:
            interrupted = True
            raise _InterruptedAfterGenerationOne

    monkeypatch.setattr(strategy, "publish_generation", publish_then_interrupt)
    with pytest.raises(_InterruptedAfterGenerationOne):
        run_strategy(resumed_context)
    checkpoint_after_interrupt = load_checkpoint(
        resumed_context.run_directory / "checkpoint.json",
        resumed_context.compatibility,
    )
    assert (checkpoint_after_interrupt.generation, checkpoint_after_interrupt.consecutive_stagnation) == (1, 1)
    assert checkpoint_after_interrupt.family_priority == resumed_context.compatibility.family_priority
    (resumed_context.run_directory / "ga_history.csv").write_bytes(b"stale\n")

    resumed = run_strategy(resumed_context)

    assert resumed == uninterrupted
    assert resumed.family_priority == uninterrupted.family_priority == resumed_context.compatibility.family_priority
    assert resumed.generation == 2
    assert resumed.terminal_reason == "early_stop"
    assert (resumed_context.run_directory / "checkpoint.json").read_bytes() == (
        full.run_directory / "checkpoint.json"
    ).read_bytes()
    assert (resumed_context.run_directory / "ga_history.csv").read_bytes() == (
        full.run_directory / "ga_history.csv"
    ).read_bytes()
    terminal_checkpoint = (resumed_context.run_directory / "checkpoint.json").read_bytes()

    def forbidden(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        pytest.fail("an early-stop checkpoint re-entry performed a search lifecycle action")

    monkeypatch.setattr(strategy, "initial_population", forbidden)
    monkeypatch.setattr(strategy, "fill_next_population", forbidden)
    monkeypatch.setattr(strategy, "evaluate_candidate", forbidden)
    monkeypatch.setattr(strategy, "decode_rng_state", forbidden)
    terminal_reentry = run_strategy(resumed_context)
    assert terminal_reentry == resumed
    assert (resumed_context.run_directory / "checkpoint.json").read_bytes() == terminal_checkpoint


def test_terminal_reentry_repairs_history_and_only_freshly_validates_stored_winner(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal resume must not initialize, select, draw RNG, append history, or rewrite its checkpoint."""
    _install_scoring(monkeypatch, {0: (0.4, 0.5)})
    context = _context(valid_config_data, tmp_path / "run", generation_count=0)
    first = run_strategy(context)
    checkpoint_before = (context.run_directory / "checkpoint.json").read_bytes()
    history_expected = (context.run_directory / "ga_history.csv").read_bytes()
    (context.run_directory / "ga_history.csv").write_bytes(b"stale\n")
    events: list[str] = []
    original_load = strategy.load_generation
    original_validate = strategy.validate_evaluation_context
    validations = 0

    def validate(raw: object) -> ValidatedEvaluationContext:
        nonlocal validations
        validations += 1
        return original_validate(cast(Any, raw))

    def load(run_directory: Path, compatibility: object) -> CheckpointState:
        events.append("load_and_repair_history")
        return original_load(run_directory, cast(Any, compatibility))

    def forbidden(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        pytest.fail("terminal checkpoint re-entry performed a search lifecycle action")

    def final(candidate: Candidate, evaluation: ValidatedEvaluationContext, final_seed: int) -> tuple[TrialResult, ...]:
        del evaluation
        events.append("final_validation")
        return (_trial(final_seed, candidate.fitness),)

    monkeypatch.setattr(strategy, "validate_evaluation_context", validate)
    monkeypatch.setattr(strategy, "load_generation", load)
    monkeypatch.setattr(strategy, "initial_population", forbidden)
    monkeypatch.setattr(strategy, "fill_next_population", forbidden)
    monkeypatch.setattr(strategy, "evaluate_candidate", forbidden)
    monkeypatch.setattr(strategy, "decode_rng_state", forbidden)
    monkeypatch.setattr(strategy, "publish_generation", forbidden)
    monkeypatch.setattr(strategy, "evaluate_final", final)

    second = run_strategy(context)

    assert second == first
    assert validations == 1
    assert events == ["load_and_repair_history", "final_validation"]
    assert (context.run_directory / "checkpoint.json").read_bytes() == checkpoint_before
    assert (context.run_directory / "ga_history.csv").read_bytes() == history_expected


def test_terminal_final_failure_leaves_checkpoint_and_history_scientifically_unchanged(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final evidence failure must not mutate or reselect the already checkpointed search result."""
    _install_scoring(monkeypatch, {0: (0.4, 0.5)})
    context = _context(valid_config_data, tmp_path / "run", generation_count=0)
    first = run_strategy(context)
    before = {name: (context.run_directory / name).read_bytes() for name in ("checkpoint.json", "ga_history.csv")}

    def fail_final(*args: object, **kwargs: object) -> tuple[TrialResult, ...]:
        del args, kwargs
        raise TrafficlabError("final validation failed", corrective_action="fix the final candidate")

    monkeypatch.setattr(strategy, "evaluate_final", fail_final)
    with pytest.raises(TrafficlabError, match="final validation"):
        run_strategy(context)

    assert (
        first.winner.identifier
        == load_checkpoint(
            context.run_directory / "checkpoint.json",
            context.compatibility,
        ).best_identifier
    )
    assert {
        name: (context.run_directory / name).read_bytes() for name in ("checkpoint.json", "ga_history.csv")
    } == before
