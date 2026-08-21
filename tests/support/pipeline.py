"""Explicit complete-experiment coordinator contract tests."""

from __future__ import annotations

import json
from dataclasses import replace as replace_dataclass
from pathlib import Path
from typing import Any, cast

import tomli_w
from pydantic import BaseModel

from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.capture.stage import CaptureResult
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import FamilyName
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, render_capture_metadata
from trafficlab.comparison.codec import render_comparison_result
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import (
    CheckpointState,
    encode_rng_state,
    render_checkpoint,
    render_history_csv,
    summarize_generation,
)
from trafficlab.fitting.genetic.strategy import FitOutcome, make_strategy_context
from trafficlab.fitting.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.fitting.stage import FitStageResult
from trafficlab.generation.models.common import MARKOV_MODEL_DIAGNOSTIC_KEYS, make_rng
from trafficlab.generation.models.registry import POISSON_FAMILY, make_best_model, render_best_model
from trafficlab.generation.stage import GenerationStageResult
from trafficlab.pipeline.types import RunDependencies
from trafficlab.preflight.stage import PreparedExperiment, open_or_prepare_experiment


def replace[Record](record: Record, **changes: object) -> Record:
    """Build deliberate model states at this test boundary."""
    if isinstance(record, BaseModel):
        values = {name: getattr(record, name) for name in type(record).model_fields}
        values.update(changes)
        return cast(Record, type(record).model_construct(**values))
    return cast(Record, replace_dataclass(cast(Any, record), **changes))


def prepared_experiment(valid_config_data: dict[str, object], tmp_path: Path) -> tuple[Path, PreparedExperiment]:
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    return experiment_path, open_or_prepare_experiment(experiment_path)


def trial(seed: int, score: float = 0.8, *, family: FamilyName = "poisson_empirical") -> TrialResult:
    methods = tuple(MethodTrialResult(name=name, score=score, diagnostics={"literal": score}) for name in METHOD_ORDER)
    diagnostics = {name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS} if family == "markov_renewal" else {}
    return TrialResult(seed=seed, aggregate_score=score, methods=cast(Any, methods), model_diagnostics=diagnostics)


def fit_outcome(
    final_seed: int,
    *,
    genes: tuple[float, ...] = (1.0,),
    trial_seeds: tuple[int, ...] = (101, 102),
) -> FitOutcome:
    winner = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="poisson_empirical",
        genes=genes,
        status="valid",
        fitness=0.8,
        trials=tuple(trial(seed) for seed in trial_seeds),
        invalid=None,
        duplicate_diagnostics=(),
    )
    return FitOutcome(winner, (trial(final_seed),), 0, "hard_limit", ("poisson_empirical",))


def _comparison(
    window: float = 10.0,
    *,
    identities: dict[str, ContentIdentity] | None = None,
) -> ComparisonResult:
    fixture = Path(__file__).parents[2] / "examples" / "data" / "similarity.json"
    document = cast(dict[str, object], json.loads(fixture.read_bytes()))
    result = ComparisonResult.from_dict(document)
    if identities is not None:
        result = result.with_input_identities(identities)
    if window != result.observation_window_seconds:
        object.__setattr__(result, "observation_window_seconds", window)
    return result


def stage_results(
    experiment_path: Path,
    prepared: PreparedExperiment,
    *,
    window: float = 10.0,
) -> tuple[CaptureResult, FitStageResult, GenerationStageResult, ComparisonResult]:
    run_directory = prepared.run_directory
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    capture_content = render_capture_metadata(metadata)
    reference_trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 64),
            TraceEvent(window, Direction.INBOUND, 96),
        )
    )
    reference_content = encode_pcapng(reference_trace.to_events(), metadata)
    generation_trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 64),
            TraceEvent(1.0, Direction.INBOUND, 96),
        )
    )
    generated_content = encode_pcapng(generation_trace.to_events(), metadata)
    (run_directory / "capture.json").write_bytes(capture_content)
    (run_directory / "reference.pcapng").write_bytes(reference_content)
    (run_directory / "generated.pcapng").write_bytes(generated_content)

    capture = CaptureResult(run_directory, run_directory / "reference.pcapng", 2, 0)
    bounds = prepared.config.models.poisson_empirical
    assert bounds is not None
    best_model = make_best_model(
        POISSON_FAMILY,
        reference_trace,
        (1.0,),
        reference_identity=identify_bytes(reference_content),
        capture_identity=identify_bytes(capture_content),
        final_seed=prepared.config.run.final_seed,
        final_limits=prepared.config.generation.final,
        W=window,
        bounds=bounds,
    )
    fit = FitStageResult(
        experiment_path,
        run_directory,
        run_directory / "best_model.json",
        best_model,
        replace(
            fit_outcome(
                prepared.config.run.final_seed,
                genes=cast(tuple[float, ...], best_model.genes),
                trial_seeds=prepared.config.genetic.trial_seeds,
            ),
            generation=prepared.config.genetic.generation_count,
        ),
        window,
        False,
    )
    generation = GenerationStageResult(
        run_directory,
        run_directory / "generated.pcapng",
        generation_trace,
        prepared.config.run.final_seed,
        window,
        False,
    )
    settings_content = json.dumps(
        prepared.config.similarity.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    identities = {
        "capture_json": identify_bytes(capture_content),
        "generated_pcapng": identify_bytes(generated_content),
        "reference_pcapng": identify_bytes(reference_content),
        "similarity_settings": identify_bytes(settings_content),
    }
    comparison = _comparison(window, identities=identities)

    snapshot_content = render_effective_config(prepared.config)
    context = make_strategy_context(
        prepared.config,
        reference_trace,
        window,
        run_directory,
        experiment_identity=identify_bytes(snapshot_content),
        reference_identity=identify_bytes(reference_content),
        capture_identity=identify_bytes(capture_content),
    )
    fit = replace(fit, outcome=replace(fit.outcome, family_priority=context.compatibility.family_priority))
    family_names = cast(tuple[FamilyName, ...], tuple(family.name for family in context.compatibility.families))
    family_genes = {
        "markov_renewal": (0.2, 0.7, 0.5, 2, 1.0),
        "mmpp": (1.0, 2.0, 3.0, 4.0),
        "poisson_empirical": (1.0,),
    }
    population = [fit.outcome.winner]
    for index in range(1, prepared.config.genetic.population_size):
        family = family_names[(index - 1) % len(family_names)]
        population.append(
            Candidate(
                identifier=CandidateId(birth_generation=0, birth_index=index),
                family=family,
                genes=cast(Any, family_genes[family]),
                status="valid",
                fitness=0.7,
                trials=tuple(trial(seed, 0.7, family=family) for seed in prepared.config.genetic.trial_seeds),
                invalid=None,
                duplicate_diagnostics=(),
            )
        )
    population_tuple = tuple(population)
    history = tuple(
        row
        for generation_index in range(prepared.config.genetic.generation_count + 1)
        for row in summarize_generation(
            generation_index,
            population_tuple,
            family_names,
            family_priority=context.compatibility.family_priority,
        )
    )
    checkpoint = CheckpointState(
        compatibility=context.compatibility,
        generation=prepared.config.genetic.generation_count,
        population=population_tuple,
        history=history,
        rng_state=encode_rng_state(make_rng(prepared.config.run.master_seed)),
        best_identifier=fit.outcome.winner.identifier,
        best_fitness=fit.outcome.winner.fitness,
        consecutive_stagnation=prepared.config.genetic.generation_count,
        terminal_reason="hard_limit",
        family_priority=context.compatibility.family_priority,
    )
    (run_directory / "checkpoint.json").write_bytes(render_checkpoint(checkpoint))
    (run_directory / "ga_history.csv").write_bytes(render_history_csv(checkpoint))
    (run_directory / "best_model.json").write_bytes(render_best_model(fit.best_model))
    (run_directory / "similarity.json").write_bytes(render_comparison_result(comparison))
    return capture, fit, generation, comparison


def success_dependencies(
    experiment_path: Path,
    prepared: PreparedExperiment,
    calls: list[str],
) -> tuple[RunDependencies, tuple[CaptureResult, FitStageResult, GenerationStageResult, ComparisonResult]]:
    capture, fit, generation, comparison = stage_results(experiment_path, prepared)

    def preflight(path: Path) -> PreparedExperiment:
        assert path is experiment_path
        calls.append("preflight")
        return prepared

    def capture_stage(path: Path, value: PreparedExperiment) -> CaptureResult:
        assert path is experiment_path
        assert value is prepared
        calls.append("capture")
        return capture

    def fit_stage(path: Path) -> FitStageResult:
        assert path is experiment_path
        calls.append("fit")
        return fit

    def generate_stage(path: Path) -> GenerationStageResult:
        assert path is experiment_path
        calls.append("generate")
        return generation

    def compare_stage(path: Path) -> ComparisonResult:
        assert path is experiment_path
        calls.append("compare")
        return comparison

    dependencies = RunDependencies(preflight, capture_stage, fit_stage, generate_stage, compare_stage)
    return dependencies, (capture, fit, generation, comparison)


def _records(prepared: PreparedExperiment) -> list[dict[str, object]]:
    return [json.loads(line) for line in (prepared.run_directory / "run.log").read_text().splitlines()]


make_comparison_result = _comparison
read_run_records = _records
