"""Explicit complete-experiment coordinator contract tests."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from random import Random
from typing import Any, cast

import pytest
import tomli_w

import trafficlab.capture as capture_module
import trafficlab.run as run_module
from trafficlab.artifacts import FileIdentity
from trafficlab.capture import CaptureResult
from trafficlab.capture_validation import CaptureInspection
from trafficlab.comparison import ComparisonResult, render_comparison_result
from trafficlab.config import FamilyName
from trafficlab.config_io import render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.fitting import FitStageResult
from trafficlab.generation import GenerationStageResult
from trafficlab.genetic.checkpoint import (
    CheckpointState,
    encode_rng_state,
    render_checkpoint,
    render_history_csv,
    summarize_generation,
)
from trafficlab.genetic.strategy import FitOutcome, make_strategy_context
from trafficlab.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.models.registry import POISSON_FAMILY, make_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment
from trafficlab.run import RunDependencies, RunResult, run_experiment
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata


def _prepared_experiment(valid_config_data: dict[str, object], tmp_path: Path) -> tuple[Path, PreparedExperiment]:
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_text(tomli_w.dumps(valid_config_data), encoding="utf-8")
    return experiment_path, open_or_prepare_experiment(experiment_path)


def _trial(seed: int, score: float = 0.8) -> TrialResult:
    methods = tuple(MethodTrialResult(name, score, {"literal": score}) for name in METHOD_ORDER)
    return TrialResult(seed, score, cast(Any, methods))


def _fit_outcome(
    final_seed: int,
    *,
    genes: tuple[float, ...] = (1.0,),
    trial_seeds: tuple[int, ...] = (101, 102),
) -> FitOutcome:
    winner = Candidate(
        CandidateId(0, 0),
        "poisson_empirical",
        genes,
        "valid",
        0.8,
        tuple(_trial(seed) for seed in trial_seeds),
        None,
        (),
    )
    return FitOutcome(winner, (_trial(final_seed),), 0, "hard_limit")


def _comparison(window: float = 10.0, *, identities: dict[str, str] | None = None) -> ComparisonResult:
    fixture = Path(__file__).parents[2] / "examples" / "data" / "similarity.json"
    document = cast(dict[str, object], json.loads(fixture.read_bytes()))
    result = ComparisonResult.from_dict(document)
    if identities is not None:
        result = result.with_input_sha256(identities)
    if window != result.observation_window_seconds:
        object.__setattr__(result, "observation_window_seconds", window)
    return result


def _stage_results(
    experiment_path: Path,
    prepared: PreparedExperiment,
    *,
    window: float = 10.0,
) -> tuple[CaptureResult, FitStageResult, GenerationStageResult, ComparisonResult]:
    run_directory = prepared.run_directory
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    capture_content = render_capture_metadata(metadata)
    reference_events = (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(window, Direction.INBOUND, 96),
    )
    reference_content = encode_pcapng(reference_events, metadata)
    generation_events = (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(1.0, Direction.INBOUND, 96),
    )
    generated_content = encode_pcapng(generation_events, metadata)
    (run_directory / "capture.json").write_bytes(capture_content)
    (run_directory / "reference.pcapng").write_bytes(reference_content)
    (run_directory / "generated.pcapng").write_bytes(generated_content)

    capture = CaptureResult(run_directory, run_directory / "reference.pcapng", 2, 0)
    bounds = prepared.config.models.poisson_empirical
    assert bounds is not None
    best_model = make_best_model(
        POISSON_FAMILY,
        reference_events,
        (1.0,),
        reference_sha256=hashlib.sha256(reference_content).hexdigest(),
        capture_sha256=hashlib.sha256(capture_content).hexdigest(),
        W=window,
        bounds=bounds,
    )
    fit = FitStageResult(
        experiment_path,
        run_directory,
        run_directory / "best_model.json",
        best_model,
        replace(
            _fit_outcome(
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
        generation_events,
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
        "capture_json": hashlib.sha256(capture_content).hexdigest(),
        "generated_pcapng": hashlib.sha256(generated_content).hexdigest(),
        "reference_pcapng": hashlib.sha256(reference_content).hexdigest(),
        "similarity_settings": hashlib.sha256(settings_content).hexdigest(),
    }
    comparison = _comparison(window, identities=identities)

    snapshot_content = render_effective_config(prepared.config)
    context = make_strategy_context(
        prepared.config,
        reference_events,
        window,
        run_directory,
        experiment_sha256=hashlib.sha256(snapshot_content).hexdigest(),
        reference_sha256=identities["reference_pcapng"],
        capture_sha256=identities["capture_json"],
    )
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
                CandidateId(0, index),
                family,
                cast(Any, family_genes[family]),
                "valid",
                0.7,
                tuple(_trial(seed, 0.7) for seed in prepared.config.genetic.trial_seeds),
                None,
                (),
            )
        )
    population_tuple = tuple(population)
    history = tuple(
        row
        for generation_index in range(prepared.config.genetic.generation_count + 1)
        for row in summarize_generation(generation_index, population_tuple, family_names)
    )
    checkpoint = CheckpointState(
        context.compatibility,
        prepared.config.genetic.generation_count,
        population_tuple,
        history,
        encode_rng_state(Random(prepared.config.run.master_seed).getstate()),
        fit.outcome.winner.identifier,
        fit.outcome.winner.fitness,
        prepared.config.genetic.generation_count,
        "hard_limit",
    )
    (run_directory / "checkpoint.json").write_bytes(render_checkpoint(checkpoint))
    (run_directory / "ga_history.csv").write_bytes(render_history_csv(checkpoint))
    (run_directory / "best_model.json").write_bytes(render_best_model(fit.best_model))
    (run_directory / "similarity.json").write_bytes(render_comparison_result(comparison))
    return capture, fit, generation, comparison


def _success_dependencies(
    experiment_path: Path,
    prepared: PreparedExperiment,
    calls: list[str],
) -> tuple[RunDependencies, tuple[CaptureResult, FitStageResult, GenerationStageResult, ComparisonResult]]:
    capture, fit, generation, comparison = _stage_results(experiment_path, prepared)

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


def test_run_experiment_calls_five_stages_directly_in_order_and_returns_their_exact_results(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Reordering, copying, or substituting stage results would break the in-process contract."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, expected = _success_dependencies(experiment_path, prepared, calls)

    result = run_experiment(experiment_path, dependencies=dependencies)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert type(result) is RunResult
    assert result.experiment_path is experiment_path
    assert result.run_directory is prepared.run_directory
    assert (result.capture, result.fit, result.generation, result.comparison) == expected
    with pytest.raises(FrozenInstanceError):
        result.run_directory = tmp_path  # type: ignore[misc]


def test_run_experiment_appends_one_exact_completion_record(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A successful run must expose one deterministic whole-pipeline summary only after comparison validation."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    dependencies, _expected = _success_dependencies(experiment_path, prepared, [])

    run_experiment(experiment_path, dependencies=dependencies)

    completions = [record for record in _records(prepared) if record.get("event") == "run_completed"]
    assert completions == [
        {
            "aggregate_score": 0.741827964755164,
            "event": "run_completed",
            "family": "poisson_empirical",
            "fitness": 0.8,
            "generated_packet_count": 2,
            "reference_packet_count": 2,
            "run_directory": str(prepared.run_directory),
            "stage": "run",
        }
    ]


@pytest.mark.parametrize(
    ("corruption", "owner", "match"),
    [
        ("capture-count", "capture", "packet count"),
        ("capture-window", "capture", "reference window"),
        ("capture-pair", "capture", "capture validation failed"),
        ("experiment", "preflight", "experiment.toml"),
        ("missing-checkpoint", "run", "directory entries"),
        ("checkpoint", "fit", "checkpoint schema is incompatible"),
        ("checkpoint-state", "fit", "terminal state"),
        ("history", "fit", "ga_history.csv"),
        ("best-model", "fit", "best_model.json"),
        ("best-model-invalid", "fit", "invalid JSON"),
        ("best-model-noncanonical", "fit", "not canonical"),
        ("best-model-lineage", "fit", "lineage"),
        ("generated", "generate", "generated.pcapng"),
        ("generated-invalid", "generate", "invalid PCAPNG"),
        ("generated-lineage", "compare", "similarity.json lineage"),
        ("similarity", "compare", "similarity.json"),
        ("similarity-invalid", "compare", "similarity.json is invalid"),
        ("similarity-noncanonical", "compare", "not canonical"),
        ("extra-entry", "run", "directory entries"),
    ],
)
def test_run_experiment_strictly_reloads_every_owned_artifact_before_completion(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    corruption: str,
    owner: str,
    match: str,
) -> None:
    """Trusting returned objects would let post-stage artifact corruption receive run_completed."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (_capture, fit, _generation, comparison) = _success_dependencies(experiment_path, prepared, calls)
    run_directory = prepared.run_directory
    original_compare = dependencies.compare
    changed_path: Path
    changed_content: bytes | None
    post_compare_best_model: object | None = None

    if corruption in {"capture-count", "capture-window"}:
        metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
        changed_path = run_directory / "reference.pcapng"
        if corruption == "capture-count":
            events = (
                TraceEvent(0.0, Direction.OUTBOUND, 64),
                TraceEvent(5.0, Direction.OUTBOUND, 80),
                TraceEvent(10.0, Direction.INBOUND, 96),
            )
        else:
            events = (TraceEvent(0.0, Direction.OUTBOUND, 64), TraceEvent(12.0, Direction.INBOUND, 96))
        changed_content = encode_pcapng(events, metadata)
    elif corruption == "capture-pair":
        changed_path = run_directory / "capture.json"
        changed_content = b"{}\n"
    elif corruption == "experiment":
        changed_path = run_directory / "experiment.toml"
        changed_content = b'[run]\ndirectory = "different"\n'
    elif corruption == "missing-checkpoint":
        changed_path = run_directory / "checkpoint.json"
        changed_content = None
    elif corruption == "checkpoint":
        changed_path = run_directory / "checkpoint.json"
        changed_content = b"{}\n"
    elif corruption == "checkpoint-state":
        changed_path = run_directory / "checkpoint.json"
        checkpoint_document = cast(dict[str, Any], json.loads(changed_path.read_bytes()))
        population = cast(list[dict[str, Any]], checkpoint_document["population"])
        population[0]["genes"] = [1.25]
        changed_content = (
            json.dumps(checkpoint_document, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode()
    elif corruption == "history":
        changed_path = run_directory / "ga_history.csv"
        changed_content = b"not the checkpoint projection\n"
    elif corruption in {"best-model", "best-model-lineage"}:
        changed_path = run_directory / "best_model.json"
        changed_model = replace(fit.best_model, capture_sha256="0" * 64)
        if corruption == "best-model-lineage":
            post_compare_best_model = changed_model
        changed_content = render_best_model(changed_model)
    elif corruption == "best-model-invalid":
        changed_path = run_directory / "best_model.json"
        changed_content = b"not JSON\n"
    elif corruption == "best-model-noncanonical":
        changed_path = run_directory / "best_model.json"
        changed_content = changed_path.read_bytes().rstrip(b"\n") + b" \n"
    elif corruption in {"generated", "generated-lineage", "generated-invalid"}:
        metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
        changed_path = run_directory / "generated.pcapng"
        if corruption == "generated":
            changed_content = encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 512),), metadata)
        elif corruption == "generated-invalid":
            changed_content = b"not PCAPNG"
        else:
            changed_content = changed_path.read_bytes() + struct.pack("<III", 0x12345678, 12, 12)
    elif corruption in {"similarity", "similarity-invalid", "similarity-noncanonical"}:
        changed_path = run_directory / "similarity.json"
        if corruption == "similarity-invalid":
            changed_content = b"not JSON\n"
        elif corruption == "similarity-noncanonical":
            changed_content = changed_path.read_bytes().rstrip(b"\n") + b" \n"
        else:
            assert comparison.input_sha256 is not None
            identities = dict(comparison.input_sha256)
            identities["generated_pcapng"] = "0" * 64
            changed_content = render_comparison_result(comparison.with_input_sha256(identities))
    else:
        changed_path = run_directory / "unexpected.txt"
        changed_content = b"preserve unexpected entry\n"

    def corrupt_after_compare(path: Path) -> ComparisonResult:
        result = original_compare(path)
        if post_compare_best_model is not None:
            object.__setattr__(fit, "best_model", post_compare_best_model)
        if changed_content is None:
            changed_path.unlink()
        else:
            changed_path.write_bytes(changed_content)
        return result

    object.__setattr__(dependencies, "compare", corrupt_after_compare)

    with pytest.raises(TrafficlabError, match=match):
        run_experiment(experiment_path, dependencies=dependencies)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    if changed_content is None:
        assert not changed_path.exists()
    else:
        assert changed_path.read_bytes() == changed_content
    records = _records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == owner
    assert [record for record in records if record.get("event") == "run_completed"] == []


@pytest.mark.parametrize(
    ("artifact_name", "detail", "corrective_action"),
    [
        (
            "checkpoint.json",
            "checkpoint schema is incompatible",
            "refit under the current schema in a new run directory",
        ),
        (
            "best_model.json",
            "best model schema is incompatible",
            "refit under the current schema",
        ),
    ],
)
def test_final_reload_preserves_schema_incompatibility_outcome(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    artifact_name: str,
    detail: str,
    corrective_action: str,
) -> None:
    """Final reloads retain the source schema outcome instead of reclassifying it as corruption."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = _success_dependencies(experiment_path, prepared, calls)
    artifact_path = prepared.run_directory / artifact_name
    original_compare = dependencies.compare
    schema_one: bytes | None = None

    def replace_schema_after_compare(path: Path) -> ComparisonResult:
        nonlocal schema_one
        result = original_compare(path)
        document = cast(dict[str, object], json.loads(artifact_path.read_bytes()))
        document["scientific_artifact_schema"] = 1
        schema_one = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        artifact_path.write_bytes(schema_one)
        return result

    object.__setattr__(dependencies, "compare", replace_schema_after_compare)

    with pytest.raises(TrafficlabError) as captured:
        run_experiment(experiment_path, dependencies=dependencies)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "scientific_semantics_incompatible",
        "stage": "fit",
        "detail": detail,
        "corrective_action": corrective_action,
        "affected_evidence": artifact_name,
        "evidence_state": "preserved",
        "authority": "primary",
    }
    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert captured.value.failure_outcomes == (outcome,)
    assert schema_one is not None
    assert artifact_path.read_bytes() == schema_one
    records = _records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "fit"
    assert failures[0]["failure_outcome"] == outcome.as_dict()
    assert failures[0]["corrective_action"] == corrective_action
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_final_reload_preserves_the_generic_checkpoint_error_boundary(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A non-schema checkpoint decode error still follows the existing fit corruption fallback."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = _success_dependencies(experiment_path, prepared, [])
    checkpoint_path = prepared.run_directory / "checkpoint.json"
    original_compare = dependencies.compare

    def replace_checkpoint_after_compare(path: Path) -> ComparisonResult:
        result = original_compare(path)
        checkpoint_path.write_bytes(b'{"scientific_artifact_schema":2}\n')
        return result

    object.__setattr__(dependencies, "compare", replace_checkpoint_after_compare)

    with pytest.raises(TrafficlabError) as captured:
        run_experiment(experiment_path, dependencies=dependencies)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "fit",
        "fit inputs",
        "preserved",
    )


@pytest.mark.parametrize(
    ("name", "owner"),
    [
        ("experiment.toml", "preflight"),
        ("run.log", "preflight"),
        ("capture.json", "capture"),
        ("reference.pcapng", "capture"),
        ("checkpoint.json", "fit"),
        ("ga_history.csv", "fit"),
        ("best_model.json", "fit"),
        ("generated.pcapng", "generate"),
        ("similarity.json", "compare"),
    ],
)
def test_run_experiment_rejects_every_final_artifact_replaced_immediately_after_its_read(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    owner: str,
) -> None:
    """Detached validated bytes cannot authorize success after their canonical entry is replaced."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = _success_dependencies(experiment_path, prepared, calls)
    destination = prepared.run_directory / name
    replacement = (
        b'{"event":"concurrent_replacement","stage":"preflight"}\n'
        if name == "run.log"
        else b"concurrent replacement\n"
    )
    real_compare = dependencies.compare
    real_read_bytes = Path.read_bytes
    final_validation_started = False
    replaced = False
    destination_reads = 0

    def activate_final_validation(path: Path) -> ComparisonResult:
        nonlocal final_validation_started
        result = real_compare(path)
        final_validation_started = True
        return result

    def replace_after_read(path: Path) -> bytes:
        nonlocal destination_reads, replaced
        content = real_read_bytes(path)
        if final_validation_started and path == destination:
            destination_reads += 1
        trigger_read = 2 if name == "capture.json" else 1
        if destination_reads == trigger_read and not replaced:
            replacement_path = prepared.run_directory / f"replacement-{name}"
            replacement_path.write_bytes(replacement)
            os.replace(replacement_path, destination)
            replaced = True
        return content

    object.__setattr__(dependencies, "compare", activate_final_validation)
    monkeypatch.setattr(Path, "read_bytes", replace_after_read)

    with pytest.raises(TrafficlabError, match=f"{name}.*changed during final validation"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert replaced is True
    persisted = real_read_bytes(destination)
    if name == "run.log":
        assert persisted.startswith(replacement)
    else:
        assert persisted == replacement
    records = [json.loads(line) for line in real_read_bytes(prepared.run_directory / "run.log").splitlines()]
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == owner
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_rechecks_the_exact_tree_after_the_last_artifact_read(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late new entry must not appear after the sole tree check and still receive completion."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = _success_dependencies(experiment_path, prepared, [])
    unexpected = prepared.run_directory / "late-entry"
    real_read = run_module._read_final_artifact  # pyright: ignore[reportPrivateUsage]

    def create_after_last_read(path: Path, *, owner: Any, identities: Any) -> bytes:
        content = real_read(path, owner=owner, identities=identities)
        if path.name == "similarity.json":
            unexpected.write_bytes(b"preserve late entry\n")
        return content

    monkeypatch.setattr(run_module, "_read_final_artifact", create_after_last_read)

    with pytest.raises(TrafficlabError, match="directory entries"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert unexpected.read_bytes() == b"preserve late entry\n"
    records = _records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "run"
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_rejects_an_artifact_replaced_during_validation_of_its_read_bytes(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity must remain stable through parsing and lineage checks, not only through the read call."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = _success_dependencies(experiment_path, prepared, [])
    destination = prepared.run_directory / "similarity.json"
    replacement = b"replacement during validation\n"
    real_parse = run_module.parse_comparison_result

    def replace_during_parse(content: bytes) -> ComparisonResult:
        result = real_parse(content)
        replacement_path = prepared.run_directory / "replacement-similarity.json"
        replacement_path.write_bytes(replacement)
        os.replace(replacement_path, destination)
        return result

    monkeypatch.setattr(run_module, "parse_comparison_result", replace_during_parse)

    with pytest.raises(TrafficlabError, match="similarity.json.*changed during final validation"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert destination.read_bytes() == replacement
    records = _records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "compare"
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_translates_a_final_identity_recheck_failure(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-follow stat failure after validation must retain the exact artifact owner."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = _success_dependencies(experiment_path, prepared, [])
    destination = prepared.run_directory / "similarity.json"
    real_identity = run_module._file_identity  # pyright: ignore[reportPrivateUsage]
    destination_calls = 0

    def fail_recheck(path: Path, *, kind: str, corrective_action: str) -> FileIdentity | None:
        nonlocal destination_calls
        if path == destination:
            destination_calls += 1
            if destination_calls == 3:
                raise TrafficlabError("simulated final identity failure", corrective_action="stabilize entry")
        return real_identity(path, kind=kind, corrective_action=corrective_action)

    monkeypatch.setattr(run_module, "_file_identity", fail_recheck)

    with pytest.raises(TrafficlabError, match="could not inspect similarity.json.*final identity failure"):
        run_experiment(experiment_path, dependencies=dependencies)

    records = _records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "compare"
    assert [record for record in records if record.get("event") == "run_completed"] == []


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"{}", "end with a newline"),
        (b"[]\n", "JSON object"),
        (b'{ "event":"run_prepared"}\n', "canonical sorted compact JSON"),
    ],
)
def test_final_run_log_validation_rejects_each_noncanonical_shape(content: bytes, match: str) -> None:
    """The final reload must reject truncation, non-object records, and noncanonical JSON bytes."""
    with pytest.raises(TrafficlabError, match=match):
        run_module._validate_final_run_log(content)  # pyright: ignore[reportPrivateUsage]


def test_final_artifact_read_failure_is_translated_with_its_owner(tmp_path: Path) -> None:
    """A missing final artifact must identify both its owning stage and exact filename."""
    missing = tmp_path / "checkpoint.json"

    with pytest.raises(TrafficlabError, match="final run artifact validation failed for fit.*checkpoint.json"):
        run_module._read_final_artifact(  # pyright: ignore[reportPrivateUsage]
            missing,
            owner="fit",
            identities={},
        )


def test_run_experiment_translates_final_directory_inspection_failure(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final directory read failure must prevent completion and retain one contextual run failure."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = _success_dependencies(experiment_path, prepared, calls)
    real_iterdir = Path.iterdir

    def fail_run_directory(path: Path) -> Iterator[Path]:
        if path == prepared.run_directory:
            raise PermissionError("simulated directory inspection failure")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_run_directory)

    with pytest.raises(TrafficlabError, match="could not inspect the run directory"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    failures = [record for record in _records(prepared) if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "run"
    assert [record for record in _records(prepared) if record.get("event") == "run_completed"] == []


def test_run_experiment_rejects_a_capture_pair_replaced_after_strict_pair_validation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict pair validation cannot authorize lineage loading from subsequently replaced reference bytes."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = _success_dependencies(experiment_path, prepared, calls)
    reference_path = prepared.run_directory / "reference.pcapng"
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    replacement = encode_pcapng(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 64),
            TraceEvent(5.0, Direction.OUTBOUND, 80),
            TraceEvent(10.0, Direction.INBOUND, 96),
        ),
        metadata,
    )
    real_validate = run_module.validate_capture_pair

    def replace_after_validation(
        metadata_path: Path,
        pcapng_path: Path,
        *,
        deadline: float | None,
    ) -> CaptureInspection:
        inspection = real_validate(metadata_path, pcapng_path, deadline=deadline)
        reference_path.write_bytes(replacement)
        return inspection

    monkeypatch.setattr(run_module, "validate_capture_pair", replace_after_validation)

    with pytest.raises(TrafficlabError, match="changed between strict validation and lineage loading"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert reference_path.read_bytes() == replacement
    records = _records(prepared)
    assert len([record for record in records if record.get("event") == "run_failed"]) == 1
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_translates_invalid_reference_bytes_installed_after_pair_validation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-inspection invalid reference must remain a capture-owned final-validation failure."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = _success_dependencies(experiment_path, prepared, [])
    reference_path = prepared.run_directory / "reference.pcapng"
    replacement = b"not PCAPNG"
    real_validate = run_module.validate_capture_pair

    def replace_after_validation(
        metadata_path: Path,
        pcapng_path: Path,
        *,
        deadline: float | None,
    ) -> CaptureInspection:
        inspection = real_validate(metadata_path, pcapng_path, deadline=deadline)
        reference_path.write_bytes(replacement)
        return inspection

    monkeypatch.setattr(run_module, "validate_capture_pair", replace_after_validation)

    with pytest.raises(TrafficlabError, match="final run artifact validation failed for capture.*invalid PCAPNG"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert reference_path.read_bytes() == replacement
    records = _records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "capture"
    assert [record for record in records if record.get("event") == "run_completed"] == []


@pytest.mark.parametrize(
    ("failed_stage", "exit_code"),
    [("preflight", 11), ("capture", 12), ("fit", 13), ("generate", 14), ("compare", 15)],
)
def test_run_experiment_stops_at_each_primary_stage_failure_and_preserves_earlier_artifacts(
    valid_config_data: dict[str, object], tmp_path: Path, failed_stage: str, exit_code: int
) -> None:
    """A later call or rollback after a stage failure would destroy useful completed research evidence."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    sentinel = prepared.run_directory / "earlier-stage.bin"
    sentinel.write_bytes(b"preserve exactly")
    calls: list[str] = []
    dependencies, _results = _success_dependencies(experiment_path, prepared, calls)

    def fail(*args: object) -> Any:
        del args
        calls.append(failed_stage)
        raise TrafficlabError(
            f"injected {failed_stage} failure",
            corrective_action=f"correct {failed_stage}",
            exit_code=exit_code,
        )

    dependencies = RunDependencies(
        cast(Any, fail) if failed_stage == "preflight" else dependencies.preflight,
        cast(Any, fail) if failed_stage == "capture" else dependencies.capture,
        cast(Any, fail) if failed_stage == "fit" else dependencies.fit,
        cast(Any, fail) if failed_stage == "generate" else dependencies.generate,
        cast(Any, fail) if failed_stage == "compare" else dependencies.compare,
    )

    with pytest.raises(TrafficlabError, match=f"injected {failed_stage} failure") as caught:
        run_experiment(experiment_path, dependencies=dependencies)

    assert caught.value.exit_code == exit_code
    assert calls == ["preflight", "capture", "fit", "generate", "compare"][: exit_code - 10]
    assert sentinel.read_bytes() == b"preserve exactly"
    coordinator_failures = [record for record in _records(prepared) if record.get("event") == "run_failed"]
    if failed_stage == "preflight":
        assert coordinator_failures == []
    else:
        assert len(coordinator_failures) == 1
        assert coordinator_failures[0]["failed_stage"] == failed_stage
        assert coordinator_failures[0]["detail"] == f"injected {failed_stage} failure"
        assert coordinator_failures[0]["corrective_action"] == f"correct {failed_stage}"


@pytest.mark.parametrize("stage", ["capture", "fit", "generate", "compare"])
def test_run_experiment_validates_each_result_before_calling_the_next_stage(
    valid_config_data: dict[str, object], tmp_path: Path, stage: str
) -> None:
    """Deferring validation could let a later stage consume an invalid result and mutate its artifacts."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = _success_dependencies(experiment_path, prepared, calls)

    def invalid(*args: object) -> object:
        del args
        calls.append(stage)
        return object()

    dependencies = RunDependencies(
        dependencies.preflight,
        cast(Any, invalid) if stage == "capture" else dependencies.capture,
        cast(Any, invalid) if stage == "fit" else dependencies.fit,
        cast(Any, invalid) if stage == "generate" else dependencies.generate,
        cast(Any, invalid) if stage == "compare" else dependencies.compare,
    )

    with pytest.raises(TrafficlabError, match=f"{stage} returned invalid result"):
        run_experiment(experiment_path, dependencies=dependencies)

    expected_index = ["capture", "fit", "generate", "compare"].index(stage) + 2
    assert calls == ["preflight", "capture", "fit", "generate", "compare"][:expected_index]
    failures = [record for record in _records(prepared) if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == stage


@pytest.mark.parametrize(
    ("stage", "mutation", "match"),
    [
        ("capture", "path", "reference_path"),
        ("capture", "status", "target_status"),
        ("fit", "path", "best_model_path"),
        ("fit", "window", "observation window"),
        ("generate", "path", "generated_path"),
        ("generate", "seed", "final seed"),
        ("generate", "window", "observation window"),
        ("compare", "window", "observation window"),
    ],
)
def test_run_experiment_rejects_strict_path_status_window_and_seed_mismatches(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    stage: str,
    mutation: str,
    match: str,
) -> None:
    """Accepting a mismatched path, status, W, or seed would join artifacts from different experiments."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (capture, fit, generation, comparison) = _success_dependencies(experiment_path, prepared, calls)
    if stage == "capture":
        capture = CaptureResult(
            capture.run_directory,
            tmp_path / "other.pcapng" if mutation == "path" else capture.reference_path,
            capture.packet_count,
            1 if mutation == "status" else capture.target_status,
        )
    elif stage == "fit":
        fit = FitStageResult(
            fit.experiment_path,
            fit.run_directory,
            tmp_path / "other.json" if mutation == "path" else fit.best_model_path,
            fit.best_model,
            fit.outcome,
            11.0 if mutation == "window" else fit.observation_window_seconds,
            fit.reused_best_model,
        )
    elif stage == "generate":
        generation = GenerationStageResult(
            generation.run_directory,
            tmp_path / "other.pcapng" if mutation == "path" else generation.generated_path,
            generation.events,
            generation.seed + 1 if mutation == "seed" else generation.seed,
            11.0 if mutation == "window" else generation.observation_window_seconds,
            generation.reused,
        )
    else:
        comparison = _comparison(11.0)

    if stage == "capture":

        def replace_capture(path: Path, value: PreparedExperiment) -> CaptureResult:
            del path, value
            calls.append("capture")
            return capture

        object.__setattr__(dependencies, "capture", replace_capture)
    elif stage == "fit":

        def replace_fit(path: Path) -> FitStageResult:
            del path
            calls.append("fit")
            return fit

        object.__setattr__(dependencies, "fit", replace_fit)
    elif stage == "generate":

        def replace_generation(path: Path) -> GenerationStageResult:
            del path
            calls.append("generate")
            return generation

        object.__setattr__(dependencies, "generate", replace_generation)
    else:

        def replace_comparison(path: Path) -> ComparisonResult:
            del path
            calls.append("compare")
            return comparison

        object.__setattr__(dependencies, "compare", replace_comparison)

    with pytest.raises(TrafficlabError, match=match):
        run_experiment(experiment_path, dependencies=dependencies)

    stop = ["capture", "fit", "generate", "compare"].index(stage) + 2
    assert calls == ["preflight", "capture", "fit", "generate", "compare"][:stop]
    assert len([record for record in _records(prepared) if record.get("event") == "run_failed"]) == 1


def test_run_experiment_retains_primary_error_when_run_failure_logging_also_fails(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic write failure must remain secondary to the stage error and preserve its exit code."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = _success_dependencies(experiment_path, prepared, [])
    primary = TrafficlabError("primary capture failure", corrective_action="fix capture", exit_code=42)

    def fail_capture(path: Path, value: PreparedExperiment) -> CaptureResult:
        del path, value
        raise primary

    def fail_log(run_directory: Path, record: object) -> None:
        del run_directory, record
        raise TrafficlabError("secondary log failure", corrective_action="fix log", exit_code=99)

    monkeypatch.setattr(run_module, "append_run_log", fail_log)
    dependencies = RunDependencies(
        dependencies.preflight,
        fail_capture,
        dependencies.fit,
        dependencies.generate,
        dependencies.compare,
    )

    with pytest.raises(TrafficlabError, match="primary capture failure.*secondary log failure") as caught:
        run_experiment(experiment_path, dependencies=dependencies)

    assert caught.value is primary
    assert caught.value.exit_code == 42
    assert caught.value.corrective_action == "fix capture"


def test_preflight_failure_never_calls_the_coordinator_log_boundary(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The coordinator must not assume run.log exists when full preflight raises."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)

    def fail_preflight(path: Path) -> PreparedExperiment:
        del path
        raise TrafficlabError("direct preflight failure", corrective_action="fix preflight", exit_code=11)

    def reject_log(run_directory: Path, record: object) -> None:
        del run_directory, record
        raise AssertionError("coordinator accessed run.log after preflight failure")

    monkeypatch.setattr(run_module, "append_run_log", reject_log)
    dependencies = RunDependencies(
        fail_preflight,
        lambda path, value: cast(CaptureResult, object()),
        lambda path: cast(FitStageResult, object()),
        lambda path: cast(GenerationStageResult, object()),
        lambda path: cast(ComparisonResult, object()),
    )

    with pytest.raises(TrafficlabError, match="direct preflight failure") as caught:
        run_experiment(experiment_path, dependencies=dependencies)

    assert caught.value.exit_code == 11
    assert prepared.run_directory.is_dir()


def test_production_dependencies_run_full_preflight_once_and_call_only_the_prepared_capture_core(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wiring capture_experiment would repeat full Docker preflight inside one coordinated run."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    capture, fit, generation, comparison = _stage_results(experiment_path, prepared)
    calls: list[tuple[object, ...]] = []

    def preflight(path: Path, *, config_only: bool) -> PreparedExperiment:
        calls.append(("preflight", path, config_only))
        return prepared

    def prepared_capture(path: Path, value: PreparedExperiment) -> CaptureResult:
        calls.append(("capture_prepared", path, value))
        return capture

    def capture_wrapper(*args: object, **kwargs: object) -> CaptureResult:
        del args, kwargs
        raise AssertionError("coordinator called the full-preflight capture wrapper")

    def fit_stage(path: Path) -> FitStageResult:
        del path
        return fit

    def generate_stage(path: Path) -> GenerationStageResult:
        del path
        return generation

    def compare_stage(path: Path) -> ComparisonResult:
        del path
        return comparison

    monkeypatch.setattr(run_module, "run_preflight", preflight)
    monkeypatch.setattr(run_module, "capture_prepared_experiment", prepared_capture)
    monkeypatch.setattr(capture_module, "capture_experiment", capture_wrapper)
    monkeypatch.setattr(run_module, "fit_experiment", fit_stage)
    monkeypatch.setattr(run_module, "generate_experiment", generate_stage)
    monkeypatch.setattr(run_module, "compare_experiment", compare_stage)

    run_experiment(experiment_path)

    assert calls == [
        ("preflight", experiment_path, False),
        ("capture_prepared", experiment_path, prepared),
    ]


@pytest.mark.parametrize("corruption", ["type", "source", "directory", "relative-directory"])
def test_run_experiment_rejects_invalid_preflight_results_without_coordinator_logging(
    valid_config_data: dict[str, object], tmp_path: Path, corruption: str
) -> None:
    """The coordinator cannot trust or write a run log until the prepared result is exact."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    candidate: object
    if corruption == "type":
        candidate = object()
    elif corruption == "source":
        candidate = replace(prepared, source=tmp_path / "other.toml")
    elif corruption == "directory":
        candidate = replace(prepared, run_directory=tmp_path / "other-run")
    else:
        relative_run = prepared.config.run.model_copy(update={"directory": Path("relative-run")})
        candidate = replace(
            prepared,
            config=prepared.config.model_copy(update={"run": relative_run}),
            run_directory=Path("relative-run"),
        )
    dependencies = RunDependencies(
        lambda path: cast(PreparedExperiment, candidate),
        lambda path, value: cast(CaptureResult, object()),
        lambda path: cast(FitStageResult, object()),
        lambda path: cast(GenerationStageResult, object()),
        lambda path: cast(ComparisonResult, object()),
    )

    with pytest.raises(TrafficlabError, match="preflight returned invalid result"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert [record for record in _records(prepared) if record.get("event") == "run_failed"] == []


@pytest.mark.parametrize(
    ("stage", "corruption", "match"),
    [
        ("capture", "directory", "run_directory"),
        ("fit", "experiment", "experiment or run path"),
        ("fit", "directory", "experiment or run path"),
        ("fit", "model-type", "BestModel"),
        ("fit", "window-type", "finite positive"),
        ("fit", "window-nan", "finite positive"),
        ("fit", "window-zero", "finite positive"),
        ("fit", "family", "winning family"),
        ("fit", "seed", "final seed"),
        ("generate", "directory", "run_directory"),
    ],
)
def test_run_experiment_rejects_remaining_strict_stage_invariants_before_the_next_call(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    stage: str,
    corruption: str,
    match: str,
) -> None:
    """Every remaining strict path, type, W, family, and seed branch is enforced immediately."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (capture, fit, generation, _comparison_result) = _success_dependencies(
        experiment_path, prepared, calls
    )
    if stage == "capture":
        capture = replace(capture, run_directory=tmp_path / "other-run")

        def remaining_capture(path: Path, value: PreparedExperiment) -> CaptureResult:
            del path, value
            calls.append("capture")
            return capture

        object.__setattr__(dependencies, "capture", remaining_capture)
    elif stage == "fit":
        if corruption == "experiment":
            fit = replace(fit, experiment_path=tmp_path / "other.toml")
        elif corruption == "directory":
            fit = replace(fit, run_directory=tmp_path / "other-run")
        elif corruption == "model-type":
            fit = replace(fit, best_model=cast(Any, object()))
        elif corruption.startswith("window-"):
            window: object = {"window-type": 10, "window-nan": float("nan"), "window-zero": 0.0}[corruption]
            fit = replace(fit, observation_window_seconds=cast(Any, window))
        elif corruption == "family":
            object.__setattr__(fit.best_model, "family", "mmpp")
        else:
            fit = replace(fit, outcome=_fit_outcome(prepared.config.run.final_seed + 1))

        def remaining_fit(path: Path) -> FitStageResult:
            del path
            calls.append("fit")
            return fit

        object.__setattr__(dependencies, "fit", remaining_fit)
    else:
        generation = replace(generation, run_directory=tmp_path / "other-run")

        def remaining_generation(path: Path) -> GenerationStageResult:
            del path
            calls.append("generate")
            return generation

        object.__setattr__(dependencies, "generate", remaining_generation)

    with pytest.raises(TrafficlabError, match=match):
        run_experiment(experiment_path, dependencies=dependencies)

    stop = ["capture", "fit", "generate"].index(stage) + 2
    assert calls == ["preflight", "capture", "fit", "generate"][:stop]


@pytest.mark.parametrize(
    ("stage", "corruption", "match"),
    [
        ("fit", "outcome-type", "FitOutcome"),
        ("fit", "winner-type", "winner"),
        ("fit", "winner-status", "valid candidate"),
        ("fit", "winner-invalid", "valid candidate"),
        ("fit", "fitness-type", "winner fitness"),
        ("fit", "fitness-nan", "winner fitness"),
        ("fit", "fitness-range", "winner fitness"),
        ("fit", "winner-trials-type", "winner trials"),
        ("fit", "winner-trial-member", "winner trials"),
        ("fit", "winner-trial-seeds", "trial seeds"),
        ("fit", "final-trials-type", "final trials"),
        ("fit", "final-trial-member", "final trials"),
        ("fit", "genes", "winning genes"),
        ("fit", "reuse", "reuse"),
        ("fit", "capture-read", "capture.json"),
        ("fit", "reference-read", "reference.pcapng"),
        ("fit", "capture-lineage", "capture lineage"),
        ("fit", "reference-lineage", "reference lineage"),
        ("generate", "window-type", "observation window"),
        ("generate", "events-type", "event tuple"),
        ("generate", "event-member", "TraceEvent"),
        ("generate", "reuse", "reuse"),
        ("generate", "output-identity", "generated output events"),
        ("generate", "output-invalid", "generated output identity"),
        ("generate", "output-read", "generated.pcapng"),
        ("compare", "window-type", "observation window"),
        ("compare", "lineage-none", "input lineage"),
        ("compare", "lineage-capture", "input lineage"),
        ("compare", "lineage-reference", "input lineage"),
        ("compare", "lineage-generated", "input lineage"),
        ("compare", "lineage-settings", "input lineage"),
    ],
)
def test_run_experiment_rejects_nested_and_lineage_corruption_before_the_next_call(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    stage: str,
    corruption: str,
    match: str,
) -> None:
    """Corrupt nested evidence or lineage must fail contextually before downstream work."""
    experiment_path, prepared = _prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (_capture, fit, generation, comparison) = _success_dependencies(experiment_path, prepared, calls)

    if stage == "fit":
        if corruption == "outcome-type":
            fit = replace(fit, outcome=cast(Any, object()))
        elif corruption == "winner-type":
            object.__setattr__(fit.outcome, "winner", object())
        elif corruption == "winner-status":
            object.__setattr__(fit.outcome.winner, "status", "pending")
        elif corruption == "winner-invalid":
            object.__setattr__(fit.outcome.winner, "invalid", object())
        elif corruption.startswith("fitness-"):
            fitness: object = {"fitness-type": 1, "fitness-nan": float("nan"), "fitness-range": 2.0}[corruption]
            object.__setattr__(fit.outcome.winner, "fitness", fitness)
        elif corruption == "winner-trials-type":
            object.__setattr__(fit.outcome.winner, "trials", [])
        elif corruption == "winner-trial-member":
            object.__setattr__(fit.outcome.winner, "trials", (object(),))
        elif corruption == "winner-trial-seeds":
            object.__setattr__(fit.outcome.winner, "trials", (_trial(999),))
        elif corruption == "final-trials-type":
            object.__setattr__(fit.outcome, "final_trials", [])
        elif corruption == "final-trial-member":
            object.__setattr__(fit.outcome, "final_trials", (object(),))
        elif corruption == "genes":
            object.__setattr__(fit.outcome.winner, "genes", (2.0,))
        elif corruption == "reuse":
            fit = replace(fit, reused_best_model=cast(Any, 1))
        elif corruption == "capture-read":
            (prepared.run_directory / "capture.json").unlink()
        elif corruption == "reference-read":
            (prepared.run_directory / "reference.pcapng").unlink()
        elif corruption == "capture-lineage":
            object.__setattr__(fit.best_model, "capture_sha256", "0" * 64)
        else:
            object.__setattr__(fit.best_model, "reference_sha256", "0" * 64)

        def corrupted_fit(path: Path) -> FitStageResult:
            del path
            calls.append("fit")
            return fit

        object.__setattr__(dependencies, "fit", corrupted_fit)
    elif stage == "generate":
        if corruption == "window-type":
            generation = replace(generation, observation_window_seconds=cast(Any, 10))
        elif corruption == "events-type":
            generation = replace(generation, events=cast(Any, list(generation.events)))
        elif corruption == "event-member":
            generation = replace(generation, events=cast(Any, (object(),)))
        elif corruption == "reuse":
            generation = replace(generation, reused=cast(Any, 1))
        elif corruption == "output-identity":
            metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
            generation.generated_path.write_bytes(encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 512),), metadata))
        elif corruption == "output-invalid":
            generation.generated_path.write_bytes(b"invalid PCAPNG")
        else:
            generation.generated_path.unlink()

        def corrupted_generation(path: Path) -> GenerationStageResult:
            del path
            calls.append("generate")
            return generation

        object.__setattr__(dependencies, "generate", corrupted_generation)
    else:
        if corruption == "window-type":
            object.__setattr__(comparison, "observation_window_seconds", 10)
        elif corruption == "lineage-none":
            comparison = ComparisonResult(
                comparison.aggregate_score,
                comparison.observation_window_seconds,
                comparison.methods,
                None,
            )
        else:
            assert comparison.input_sha256 is not None
            identities = dict(comparison.input_sha256)
            identity_name = {
                "lineage-capture": "capture_json",
                "lineage-reference": "reference_pcapng",
                "lineage-generated": "generated_pcapng",
                "lineage-settings": "similarity_settings",
            }[corruption]
            identities[identity_name] = "0" * 64
            comparison = comparison.with_input_sha256(identities)

        def corrupted_comparison(path: Path) -> ComparisonResult:
            del path
            calls.append("compare")
            return comparison

        object.__setattr__(dependencies, "compare", corrupted_comparison)

    with pytest.raises(TrafficlabError, match=match):
        run_experiment(experiment_path, dependencies=dependencies)

    stop = ["capture", "fit", "generate", "compare"].index(stage) + 2
    assert calls == ["preflight", "capture", "fit", "generate", "compare"][:stop]
    failures = [record for record in _records(prepared) if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == stage
    assert f"{stage} returned invalid result" in cast(str, failures[0]["detail"])
    assert [record for record in _records(prepared) if record.get("event") == "run_completed"] == []
