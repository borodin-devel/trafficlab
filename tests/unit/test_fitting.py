"""Prepared fitting-stage and exclusive best-model publication tests."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts as artifacts
import trafficlab.fitting as fitting
import trafficlab.genetic.checkpoint as checkpoint
import trafficlab.genetic.strategy as strategy_module
from trafficlab.artifacts import publish_best_model
from trafficlab.compatibility import ContentIdentity, identify_bytes
from trafficlab.config import ExperimentConfig, FloatBounds, GenerationLimits, PoissonConfig
from trafficlab.config_io import render_effective_config
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.fitting import FitDependencies, fit_experiment, read_fit_input
from trafficlab.genetic.checkpoint import load_checkpoint, publish_checkpoint
from trafficlab.genetic.strategy import FitOutcome, StrategyContext, make_strategy_context, run_strategy
from trafficlab.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.models.poisson import PoissonFamily
from trafficlab.models.registry import POISSON_FAMILY, load_best_model, make_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng
from trafficlab.preflight import PreflightReport, PreparedExperiment, open_or_prepare_experiment
from trafficlab.scientific_schema import ScientificArtifactSchemaError
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, TrafficTrace, render_capture_metadata

RAW_REFERENCE = (
    TraceEvent(10.0, Direction.OUTBOUND, 64),
    TraceEvent(11.0, Direction.INBOUND, 128),
    TraceEvent(12.0, Direction.OUTBOUND, 256),
)
NORMALIZED_REFERENCE = tuple(replace(event, timestamp=event.timestamp - 10.0) for event in RAW_REFERENCE)
METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")


def _config(valid_config_data: dict[str, object], run_directory: Path) -> ExperimentConfig:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["poisson_empirical"]
    models["markov_renewal"] = None
    models["mmpp"] = None
    genetic = cast(dict[str, object], data["genetic"])
    genetic.update(
        population_size=2,
        generation_count=0,
        tournament_size=2,
        elite_count=1,
        trial_seeds=[101],
        resume=True,
    )
    return ExperimentConfig.model_validate(data)


def _prepared(config: ExperimentConfig, experiment_path: Path) -> PreparedExperiment:
    return PreparedExperiment(experiment_path, config, PreflightReport(config, ()), config.run.directory)


def _trial(seed: int, score: float = 0.75) -> TrialResult:
    methods = tuple(MethodTrialResult(name, score, {"literal": score}) for name in METHOD_ORDER)
    return TrialResult(seed, score, cast(Any, methods))


def _outcome(config: ExperimentConfig, *, genes: tuple[float, ...] = (1.0,)) -> FitOutcome:
    winner = Candidate(
        CandidateId(0, 0),
        "poisson_empirical",
        genes,
        "valid",
        0.75,
        (_trial(config.genetic.trial_seeds[0]),),
        None,
        (),
    )
    return FitOutcome(winner, (_trial(config.run.final_seed),), 0, "hard_limit", ("poisson_empirical",))


def _inputs(config: ExperimentConfig, *, snapshot: bytes | None = None) -> dict[Path, bytes]:
    run_directory = config.run.directory
    return {
        run_directory / "experiment.toml": render_effective_config(config) if snapshot is None else snapshot,
        run_directory / "capture.json": render_capture_metadata(METADATA),
        run_directory / "reference.pcapng": encode_pcapng(RAW_REFERENCE, METADATA),
    }


def _dependencies(
    config: ExperimentConfig,
    experiment_path: Path,
    inputs: dict[Path, bytes],
    strategy: Callable[[StrategyContext], FitOutcome],
    *,
    reads: list[str] | None = None,
) -> FitDependencies:
    def read(path: Path) -> bytes:
        if reads is not None:
            reads.append(path.name)
        return inputs[path]

    return FitDependencies(lambda _path: _prepared(config, experiment_path), read, strategy)


def _valid_best_bytes(*, gene: float = 1.0, reference_hash: str = "a" * 64) -> bytes:
    model = make_best_model(
        POISSON_FAMILY,
        NORMALIZED_REFERENCE,
        (gene,),
        reference_identity=ContentIdentity(size=1, sha256=reference_hash),
        capture_identity=ContentIdentity(size=1, sha256="b" * 64),
        final_seed=101,
        final_limits=GenerationLimits(max_packets=1, max_output_bytes=1, max_wall_seconds=1.0),
        W=2.0,
        bounds=PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0)),
    )
    return render_best_model(model)


def _strategy_context_for_inputs(config: ExperimentConfig, inputs: dict[Path, bytes]) -> StrategyContext:
    run_directory = config.run.directory
    return make_strategy_context(
        config,
        NORMALIZED_REFERENCE,
        2.0,
        run_directory,
        experiment_identity=identify_bytes(inputs[run_directory / "experiment.toml"]),
        reference_identity=identify_bytes(inputs[run_directory / "reference.pcapng"]),
        capture_identity=identify_bytes(inputs[run_directory / "capture.json"]),
    )


def test_fit_public_boundary_classifies_a_missing_reference_input(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A stage dependency's missing source must not collapse to a best-model corruption fallback."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)

    def read(path: Path) -> bytes:
        if path.name == "reference.pcapng":
            raise FileNotFoundError("injected missing reference")
        return inputs[path]

    dependencies = FitDependencies(
        lambda _path: _prepared(config, experiment_path),
        read,
        lambda _context: _outcome(config),
    )

    with pytest.raises(TrafficlabError) as caught:
        fit_experiment(experiment_path, dependencies=dependencies)

    assert caught.value.failure_outcome == FailureOutcome(
        kind="artifact_missing",
        stage="fit",
        detail=f"could not read fit input {run_directory / 'reference.pcapng'}: injected missing reference",
        affected_evidence="reference.pcapng",
        evidence_state="not_published",
        corrective_action="verify the prepared fit inputs exist and are readable",
        authority="primary",
    )


@pytest.mark.parametrize(
    ("mode", "expected_kind", "expected_state"),
    [
        ("unreadable", "artifact_corrupt", "preserved"),
        ("unclassified", "artifact_corrupt", "preserved"),
        ("caused_missing", "artifact_missing", "not_published"),
        ("classified", "artifact_changed", "preserved"),
    ],
)
def test_fit_public_boundary_retains_source_specific_read_failures(
    valid_config_data: dict[str, object], tmp_path: Path, mode: str, expected_kind: str, expected_state: str
) -> None:
    """Raw and translated dependency failures receive source-specific outcomes at the public fit boundary."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    caused_missing = TrafficlabError("translated missing reference", corrective_action="restore reference")
    caused_missing.__cause__ = FileNotFoundError("injected missing reference")
    classified = TrafficlabError(
        "reference changed",
        corrective_action="recreate the capture pair in a new matching run",
        failure_outcome=FailureOutcome(
            kind="artifact_changed",
            stage="fit",
            detail="reference changed",
            affected_evidence="reference.pcapng",
            evidence_state="preserved",
            corrective_action="recreate the capture pair in a new matching run",
            authority="primary",
        ),
    )

    def read(path: Path) -> bytes:
        if path.name != "reference.pcapng":
            return inputs[path]
        if mode == "unreadable":
            raise PermissionError("injected unreadable reference")
        if mode == "caused_missing":
            raise caused_missing
        if mode == "classified":
            raise classified
        raise TrafficlabError("unclassified reference failure", corrective_action="repair reference")

    dependencies = FitDependencies(
        lambda _path: _prepared(config, experiment_path),
        read,
        lambda _context: _outcome(config),
    )

    with pytest.raises(TrafficlabError) as caught:
        fit_experiment(experiment_path, dependencies=dependencies)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        expected_kind,
        "fit",
        "reference.pcapng",
        expected_state,
    )


def test_read_fit_input_classifies_an_unreadable_source_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The production input reader distinguishes unreadability from absence before fit orchestration."""
    path = tmp_path / "reference.pcapng"

    def denied(_path: Path) -> bytes:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", denied)

    with pytest.raises(TrafficlabError) as caught:
        read_fit_input(path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "reference.pcapng",
        "preserved",
    )


def test_fit_failure_log_retains_ordered_secondary_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Failure logging cannot replace the source primary when cleanup evidence is also present."""
    primary = FailureOutcome(
        kind="artifact_corrupt",
        stage="fit",
        detail="checkpoint.json is corrupt",
        affected_evidence="checkpoint.json",
        evidence_state="preserved",
        corrective_action="recreate fit in a new run directory",
        authority="primary",
    )
    secondary = FailureOutcome(
        kind="cleanup_failed",
        stage="fit",
        detail="temporary cleanup failed",
        affected_evidence="inventory",
        evidence_state="possibly_remaining",
        corrective_action="remove the owned temporary file after preserving diagnostics",
        authority="secondary",
    )
    error = TrafficlabError(
        primary.detail,
        corrective_action=primary.corrective_action,
        failure_outcomes=(primary, secondary),
    )
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(fitting, "append_run_log", append)

    fitting._append_failure(tmp_path, error)  # pyright: ignore[reportPrivateUsage]

    assert records[0]["failure_outcome"] == primary.as_dict()
    assert records[0]["secondary_outcomes"] == [secondary.as_dict()]


def _create_real_terminal_run(
    valid_config_data: dict[str, object], tmp_path: Path
) -> tuple[Path, ExperimentConfig, dict[Path, bytes]]:
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    base_config = _config(valid_config_data, run_directory)
    poisson = base_config.models.poisson_empirical
    assert poisson is not None
    config = base_config.model_copy(
        update={
            "models": base_config.models.model_copy(
                update={
                    "poisson_empirical": poisson.model_copy(update={"c_lambda": FloatBounds(lower=20.0, upper=21.0)})
                }
            )
        }
    )
    inputs = _inputs(config)
    result = fit_experiment(
        experiment_path,
        dependencies=_dependencies(config, experiment_path, inputs, run_strategy),
    )
    assert result.outcome.terminal_reason == "hard_limit"
    assert (run_directory / "checkpoint.json").is_file()
    assert (run_directory / "ga_history.csv").is_file()
    assert result.best_model_path.is_file()
    return experiment_path, config, inputs


def test_fit_hashes_exact_bytes_and_passes_one_normalized_window(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lineage comes from evaluated bytes and the same inputs are rechecked before publication."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    original = dict(inputs)
    assert original[run_directory / "experiment.toml"] == render_effective_config(config)
    reads: list[str] = []
    contexts: list[StrategyContext] = []
    normalizations = 0
    original_normalize = fitting.normalize_reference

    def normalize(events: object) -> tuple[TrafficTrace, float]:
        nonlocal normalizations
        normalizations += 1
        return original_normalize(cast(Any, events))

    def strategy(context: StrategyContext) -> FitOutcome:
        contexts.append(context)
        return _outcome(config)

    monkeypatch.setattr(fitting, "normalize_reference", normalize)
    result = fit_experiment(
        experiment_path, dependencies=_dependencies(config, experiment_path, inputs, strategy, reads=reads)
    )

    assert reads == [
        "experiment.toml",
        "capture.json",
        "reference.pcapng",
        "experiment.toml",
        "capture.json",
        "reference.pcapng",
    ]
    assert normalizations == 1
    assert len(contexts) == 1
    context = contexts[0]
    assert context.evaluation.reference == NORMALIZED_REFERENCE
    assert context.evaluation.window == result.observation_window_seconds == 2.0
    assert (
        context.compatibility.experiment_sha256
        == hashlib.sha256(original[run_directory / "experiment.toml"]).hexdigest()
    )
    assert context.compatibility.capture_sha256 == hashlib.sha256(original[run_directory / "capture.json"]).hexdigest()
    assert (
        context.compatibility.reference_sha256
        == hashlib.sha256(original[run_directory / "reference.pcapng"]).hexdigest()
    )
    assert result.best_model_path == run_directory / "best_model.json"
    assert result.best_model.reference_sha256 == context.compatibility.reference_sha256
    assert result.best_model.capture_sha256 == context.compatibility.capture_sha256
    assert result.best_model.observation_window_seconds == 2.0
    poisson_bounds = config.models.poisson_empirical
    assert poisson_bounds is not None
    assert result.best_model.gene_bounds == {"c_lambda": poisson_bounds.c_lambda}
    assert load_best_model(result.best_model_path.read_bytes(), source=result.best_model_path) == result.best_model


def test_fit_rejects_reference_mutation_before_best_model_publication(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A strategy cannot publish lineage for a reference that no longer has the fitted bytes."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    reference_path = run_directory / "reference.pcapng"

    def mutate_reference(_context: StrategyContext) -> FitOutcome:
        inputs[reference_path] += b"changed after fitting"
        return _outcome(config)

    with pytest.raises(TrafficlabError, match="reference.pcapng changed during fit") as caught:
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, mutate_reference),
        )

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "artifact_changed"
    assert not (run_directory / "best_model.json").exists()


def test_fit_rejects_snapshot_bytes_that_do_not_encode_the_prepared_config(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Hashing one snapshot while evaluating another prepared config would make resume lineage false."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    changed = config.model_copy(update={"run": config.run.model_copy(update={"master_seed": 999_001})})
    inputs = _inputs(config, snapshot=render_effective_config(changed))
    strategy_called = False

    def forbidden(_context: StrategyContext) -> FitOutcome:
        nonlocal strategy_called
        strategy_called = True
        return _outcome(config)

    with pytest.raises(TrafficlabError, match="authoritative experiment snapshot") as error:
        fit_experiment(experiment_path, dependencies=_dependencies(config, experiment_path, inputs, forbidden))

    assert error.value.corrective_action == "restore experiment.toml to the exact prepared effective configuration"
    assert strategy_called is False
    assert not (run_directory / "best_model.json").exists()


def test_final_validation_fit_precedes_one_publication_refit_and_uses_same_genes(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caching final fitted state or repairing different genes would break the locked two-fit boundary."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    outcome = _outcome(config, genes=(1.25,))
    events: list[tuple[str, tuple[float, ...]]] = []
    phase = "final_validation"
    original_fit = PoissonFamily.fit

    def observed_fit(
        self: PoissonFamily,
        reference: object,
        genes: object,
        *,
        W: float,
        bounds: object,
    ) -> object:
        events.append((phase, cast(tuple[float, ...], tuple(cast(Any, genes)))))
        return original_fit(self, cast(Any, reference), cast(Any, genes), W=W, bounds=cast(Any, bounds))

    monkeypatch.setattr(PoissonFamily, "fit", observed_fit)

    def strategy(context: StrategyContext) -> FitOutcome:
        family = cast(PoissonFamily, context.evaluation.families["poisson_empirical"])
        family.fit(
            context.evaluation.reference,
            cast(tuple[float, ...], outcome.winner.genes),
            W=context.evaluation.window,
            bounds=context.evaluation.bounds["poisson_empirical"],
        )
        return outcome

    def switch_to_publication(_directory: Path, record: dict[str, object]) -> None:
        nonlocal phase
        if record["event"] == "final_validation_succeeded":
            phase = "make_best_model"
        artifacts.append_run_log(run_directory, record)

    monkeypatch.setattr(fitting, "append_run_log", switch_to_publication)
    result = fit_experiment(experiment_path, dependencies=_dependencies(config, experiment_path, inputs, strategy))

    assert events == [("final_validation", (1.25,)), ("make_best_model", (1.25,))]
    assert result.best_model.genes == result.outcome.winner.genes == (1.25,)
    assert cast(Any, result.best_model.fitted).rate == 1.25


def test_final_validation_failure_publishes_no_best_model(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """A checkpointed winner without fresh final evidence is not a publishable model."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)

    def fail_final(_context: StrategyContext) -> FitOutcome:
        raise TrafficlabError("final validation failed", corrective_action="repair final validation")

    with pytest.raises(TrafficlabError, match="final validation"):
        fit_experiment(experiment_path, dependencies=_dependencies(config, experiment_path, inputs, fail_final))

    assert not (run_directory / "best_model.json").exists()
    records = [json.loads(line) for line in (run_directory / "run.log").read_text().splitlines()]
    assert [record["event"] for record in records] == ["fit_started", "stage_failed"]


def test_existing_best_model_never_bypasses_strategy_or_checkpoint_failure(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Filename existence must not bypass checkpoint compatibility and fresh final validation."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    existing = _valid_best_bytes()
    destination = run_directory / "best_model.json"
    destination.write_bytes(existing)
    calls = 0

    def fail_checkpoint(_context: StrategyContext) -> FitOutcome:
        nonlocal calls
        calls += 1
        raise TrafficlabError("checkpoint compatibility mismatch", corrective_action="restore checkpoint")

    with pytest.raises(TrafficlabError, match="checkpoint compatibility"):
        fit_experiment(experiment_path, dependencies=_dependencies(config, experiment_path, inputs, fail_checkpoint))

    assert calls == 1
    assert destination.read_bytes() == existing


def test_terminal_rerun_enters_strategy_refits_and_reuses_identical_best_model(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal best model is reusable only after strategy validation and a fresh artifact-construction fit."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    strategy_calls = 0
    publication_fits = 0
    original_fit = PoissonFamily.fit

    def strategy(_context: StrategyContext) -> FitOutcome:
        nonlocal strategy_calls
        strategy_calls += 1
        return _outcome(config)

    def observed_fit(
        self: PoissonFamily,
        reference: object,
        genes: object,
        *,
        W: float,
        bounds: object,
    ) -> object:
        nonlocal publication_fits
        publication_fits += 1
        return original_fit(self, cast(Any, reference), cast(Any, genes), W=W, bounds=cast(Any, bounds))

    monkeypatch.setattr(PoissonFamily, "fit", observed_fit)
    dependencies = _dependencies(config, experiment_path, inputs, strategy)

    first = fit_experiment(experiment_path, dependencies=dependencies)
    first_bytes = first.best_model_path.read_bytes()
    second = fit_experiment(experiment_path, dependencies=dependencies)

    assert strategy_calls == 2
    assert publication_fits == 2
    assert first.reused_best_model is False
    assert second.reused_best_model is True
    assert second.best_model_path.read_bytes() == first_bytes
    records = [json.loads(line) for line in (run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["event"] == "best_model_reused"


def test_fit_rejects_an_occupied_schema_one_best_model_before_strategy_or_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incompatible retained model is scientific evidence, not a later publication collision."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    initial = fit_experiment(
        experiment_path,
        dependencies=_dependencies(config, experiment_path, inputs, lambda _context: _outcome(config)),
    )
    document = cast(dict[str, object], json.loads(initial.best_model_path.read_bytes()))
    document["scientific_artifact_schema"] = 1
    schema_one = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    initial.best_model_path.write_bytes(schema_one)
    reads: list[str] = []

    def forbidden_context(*_args: object, **_kwargs: object) -> StrategyContext:
        pytest.fail("schema validation reached strategy-context construction")

    def forbidden_strategy(_context: StrategyContext) -> FitOutcome:
        pytest.fail("schema validation reached strategy/RNG/search")

    def forbidden_publication(_path: Path, _content: bytes) -> object:
        pytest.fail("schema validation reached best-model publication")

    monkeypatch.setattr(fitting, "make_strategy_context", forbidden_context)
    monkeypatch.setattr(fitting, "publish_best_model", forbidden_publication)

    with pytest.raises(TrafficlabError) as captured:
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, forbidden_strategy, reads=reads),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "scientific_semantics_incompatible",
        "stage": "fit",
        "detail": "best model schema is incompatible",
        "corrective_action": "refit under the current schema",
        "affected_evidence": "best_model.json",
        "evidence_state": "preserved",
        "authority": "primary",
    }
    assert reads == []
    assert initial.best_model_path.read_bytes() == schema_one
    records = [json.loads(line) for line in (run_directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_outcome"] == outcome.as_dict()


def test_fit_classifies_a_nonsemantic_existing_model_before_strategy_work(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Early occupancy validation keeps non-schema malformed evidence at the fit boundary."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    initial = fit_experiment(
        experiment_path,
        dependencies=_dependencies(config, experiment_path, inputs, lambda _context: _outcome(config)),
    )
    document = cast(dict[str, object], json.loads(initial.best_model_path.read_bytes()))
    document["version"] = 2
    initial.best_model_path.write_bytes((json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode())

    def forbidden_context(*_args: object, **_kwargs: object) -> StrategyContext:
        pytest.fail("malformed occupancy validation reached strategy-context construction")

    monkeypatch.setattr(fitting, "make_strategy_context", forbidden_context)

    with pytest.raises(TrafficlabError) as captured:
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, lambda _context: _outcome(config)),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "fit",
        "best_model.json",
        "preserved",
    )


def test_fit_classifies_reference_normalization_error(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owning fit boundary retains a normalization failure as preserved reference evidence."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)

    def fail_normalization(_events: object) -> tuple[tuple[TraceEvent, ...], float]:
        raise TrafficlabError("injected normalization failure", corrective_action="repair reference ordering")

    monkeypatch.setattr(fitting, "normalize_reference", fail_normalization)

    with pytest.raises(TrafficlabError) as captured:
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, lambda _context: _outcome(config)),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "fit",
        "reference.pcapng",
        "preserved",
    )


@pytest.mark.parametrize("semantic", [True, False], ids=["schema", "publication"])
def test_fit_retains_the_owning_best_model_publisher_classification(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, semantic: bool
) -> None:
    """A publisher's typed schema error must not be overwritten by the collision fallback."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)

    def fail_publication(_path: Path, _content: bytes) -> object:
        if semantic:
            raise ScientificArtifactSchemaError(
                "best model schema is incompatible",
                corrective_action="refit under the current schema",
            )
        raise TrafficlabError("injected publication conflict", corrective_action="preserve the conflicting model")

    monkeypatch.setattr(fitting, "publish_best_model", fail_publication)

    with pytest.raises(TrafficlabError) as captured:
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, lambda _context: _outcome(config)),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == ("scientific_semantics_incompatible" if semantic else "publication_collision")


def test_real_terminal_checkpoint_repairs_history_validates_refits_and_reuses_best_model(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production strategy re-entry must validate the real checkpoint before byte-identical artifact reuse."""
    experiment_path, config, inputs = _create_real_terminal_run(valid_config_data, tmp_path)
    run_directory = config.run.directory
    best_before = (run_directory / "best_model.json").read_bytes()
    history_expected = (run_directory / "ga_history.csv").read_bytes()
    (run_directory / "ga_history.csv").write_bytes(b"stale derived history\n")
    events: list[str] = []
    original_load_checkpoint = checkpoint.load_checkpoint
    original_publish_history = checkpoint.publish_history_csv
    original_evaluate_final = strategy_module.evaluate_final
    original_make_best_model = fitting.make_best_model
    original_publish_best_model = fitting.publish_best_model

    def observed_load(path: Path, compatibility: object) -> object:
        events.append("load_checkpoint")
        return original_load_checkpoint(path, cast(Any, compatibility))

    def observed_history(path: Path, state: object) -> None:
        events.append("repair_history")
        original_publish_history(path, cast(Any, state))

    def observed_final(candidate: object, evaluation: object, final_seed: int) -> tuple[TrialResult, ...]:
        events.append("final_validation")
        return original_evaluate_final(cast(Any, candidate), cast(Any, evaluation), final_seed)

    def observed_make(*args: object, **kwargs: object) -> object:
        events.append("make_best_model")
        return original_make_best_model(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    def observed_publish(path: Path, content: bytes) -> object:
        publication = original_publish_best_model(path, content)
        assert publication.created_by_call is False
        events.append("reuse")
        return publication

    monkeypatch.setattr(checkpoint, "load_checkpoint", observed_load)
    monkeypatch.setattr(checkpoint, "publish_history_csv", observed_history)
    monkeypatch.setattr(strategy_module, "evaluate_final", observed_final)
    monkeypatch.setattr(fitting, "make_best_model", observed_make)
    monkeypatch.setattr(fitting, "publish_best_model", observed_publish)

    result = fit_experiment(
        experiment_path,
        dependencies=_dependencies(config, experiment_path, inputs, run_strategy),
    )

    assert result.reused_best_model is True
    assert events == ["load_checkpoint", "repair_history", "final_validation", "make_best_model", "reuse"]
    assert (run_directory / "ga_history.csv").read_bytes() == history_expected
    assert (run_directory / "best_model.json").read_bytes() == best_before


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ("experiment_settings", "experiment snapshot SHA-256"),
        ("reference_hash", "reference SHA-256"),
        ("capture_hash", "capture SHA-256"),
        ("window", "observation window"),
        ("bounds", "coordinate metadata for family poisson_empirical"),
        ("operators", "operator values for family poisson_empirical"),
        ("checkpoint", "checkpoint schema is incompatible"),
        ("final_seed", "genetic setting final_seed"),
    ],
)
def test_real_existing_best_model_never_bypasses_ordered_checkpoint_compatibility(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    expected_error: str,
) -> None:
    """Production strategy must reject the owning incompatibility before prospective artifact construction."""
    experiment_path, config, inputs = _create_real_terminal_run(valid_config_data, tmp_path)
    run_directory = config.run.directory
    best_before = (run_directory / "best_model.json").read_bytes()
    active_config = config
    active_inputs = dict(inputs)
    context = _strategy_context_for_inputs(config, inputs)

    if change == "experiment_settings":
        active_config = config.model_copy(update={"run": config.run.model_copy(update={"master_seed": 900_001})})
        active_inputs[run_directory / "experiment.toml"] = render_effective_config(active_config)
    elif change == "reference_hash":
        changed_reference = tuple(replace(event, frame_length=event.frame_length + 1) for event in RAW_REFERENCE)
        active_inputs[run_directory / "reference.pcapng"] = encode_pcapng(changed_reference, METADATA)
    elif change == "capture_hash":
        active_inputs[run_directory / "capture.json"] = b'{"interface":"eth0","target_mac":"02:42:ac:11:00:02"}\n'
    elif change == "checkpoint":
        (run_directory / "checkpoint.json").write_bytes(b"{}\n")
    else:
        state = load_checkpoint(run_directory / "checkpoint.json", context.compatibility)
        compatibility = state.compatibility
        if change == "window":
            compatibility = replace(compatibility, observation_window_seconds=3.0)
        elif change == "bounds":
            family = compatibility.families[0]
            coordinate = replace(
                family.coordinates[0],
                bounds=FloatBounds(lower=0.2, upper=family.coordinates[0].bounds.upper),
            )
            compatibility = replace(
                compatibility,
                families=(replace(family, coordinates=(coordinate,)),),
            )
        elif change == "operators":
            family = compatibility.families[0]
            compatibility = replace(
                compatibility,
                families=(replace(family, crossover_probability=0.8),),
            )
        else:
            assert change == "final_seed"
            compatibility = replace(
                compatibility,
                genetic=replace(compatibility.genetic, final_seed=compatibility.genetic.final_seed + 1),
            )
        publish_checkpoint(run_directory / "checkpoint.json", replace(state, compatibility=compatibility))

    def forbidden_make_best_model(*_args: object, **_kwargs: object) -> object:
        pytest.fail("checkpoint incompatibility reached prospective best-model construction")

    monkeypatch.setattr(fitting, "make_best_model", forbidden_make_best_model)

    with pytest.raises(TrafficlabError, match=expected_error):
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(active_config, experiment_path, active_inputs, run_strategy),
        )

    assert (run_directory / "best_model.json").read_bytes() == best_before


@pytest.mark.parametrize(
    ("changed_input", "expected"),
    [
        ("capture.json", "capture metadata"),
        ("reference.pcapng", "reference"),
    ],
)
def test_parser_errors_abort_fit_instead_of_becoming_invalid_candidates(
    valid_config_data: dict[str, object], tmp_path: Path, changed_input: str, expected: str
) -> None:
    """Required-input corruption is stage-fatal infrastructure evidence, never candidate fitness zero."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    inputs[run_directory / changed_input] = b"malformed\n"
    strategy_called = False

    def forbidden(_context: StrategyContext) -> FitOutcome:
        nonlocal strategy_called
        strategy_called = True
        return _outcome(config)

    with pytest.raises(TrafficlabError, match=expected):
        fit_experiment(experiment_path, dependencies=_dependencies(config, experiment_path, inputs, forbidden))

    assert strategy_called is False
    assert not (run_directory / "best_model.json").exists()


def test_noncanonical_strategy_winner_is_not_repaired_into_a_different_published_model(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Final validation and artifact construction must describe the exact same canonical winner genes."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    outcome = _outcome(config, genes=(99.0,))

    with pytest.raises(AssertionError, match="same canonical winner genes"):
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, lambda _context: outcome),
        )

    assert not (run_directory / "best_model.json").exists()


@pytest.mark.parametrize(
    ("checkpoint_state", "expected_kind"),
    [
        ("parse", "artifact_corrupt"),
        ("schema", "scientific_semantics_incompatible"),
        ("incompatible", "scientific_semantics_incompatible"),
    ],
)
def test_fit_preserves_and_logs_canonical_resume_checkpoint_outcomes(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    checkpoint_state: str,
    expected_kind: str,
) -> None:
    """A real fit must retain the source-owned checkpoint classification in its failure log."""
    experiment_path, config, inputs = _create_real_terminal_run(valid_config_data, tmp_path)
    checkpoint_path = config.run.directory / "checkpoint.json"
    original = checkpoint_path.read_bytes()
    if checkpoint_state == "parse":
        checkpoint_path.write_bytes(b"{\n")
    elif checkpoint_state == "schema":
        checkpoint_path.write_bytes(b"{}\n")
    else:
        document = cast(dict[str, object], json.loads(original))
        marker = cast(str, cast(dict[str, object], document["experiment_identity"])["sha256"]).encode()
        checkpoint_path.write_bytes(original.replace(marker, b"0" * 64, 1))

    with pytest.raises(TrafficlabError) as captured:
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, run_strategy),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (expected_kind, "fit", "checkpoint.json", "preserved", "primary")
    records = [json.loads(line) for line in (config.run.directory / "run.log").read_text().splitlines()]
    assert records[-1]["failure_outcome"] == outcome.as_dict()


def test_fit_logs_only_completed_events_in_stage_order(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """Logging a prospective checkpoint, validation, or artifact would make a failed run look complete."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)

    result = fit_experiment(
        experiment_path,
        dependencies=_dependencies(config, experiment_path, inputs, lambda _context: _outcome(config)),
    )

    records = [json.loads(line) for line in (run_directory / "run.log").read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "fit_started",
        "checkpoint_ready",
        "final_validation_succeeded",
        "best_model_published",
    ]
    assert records[1] == {
        "event": "checkpoint_ready",
        "generation": 0,
        "path": str(run_directory / "checkpoint.json"),
        "stage": "fit",
        "terminal_reason": "hard_limit",
    }
    assert records[-1]["path"] == str(result.best_model_path)
    assert records[-1]["reference_sha256"] == result.best_model.reference_sha256


def test_fit_success_logging_failure_reports_the_preserved_artifact(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final log durability error must not erase or falsely report loss of the published winner."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    real_append = artifacts.append_run_log

    def fail_final_log(directory: Path, record: dict[str, object]) -> None:
        if record["event"] == "best_model_published":
            raise TrafficlabError("injected logging failure", corrective_action="repair logging")
        real_append(directory, record)

    monkeypatch.setattr(fitting, "append_run_log", fail_final_log)

    with pytest.raises(TrafficlabError, match="best model was published.*injected logging failure"):
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, lambda _context: _outcome(config)),
        )

    assert (
        load_best_model(
            (run_directory / "best_model.json").read_bytes(), source=run_directory / "best_model.json"
        ).family
        == "poisson_empirical"
    )


def test_failure_logging_retains_the_primary_error_and_corrective_action(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secondary run-log failure must not conceal the strategy's primary checkpoint error."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)

    def fail_strategy(_context: StrategyContext) -> FitOutcome:
        raise TrafficlabError("checkpoint unreadable", corrective_action="restore checkpoint", exit_code=7)

    def fail_failure_log(_directory: Path, record: dict[str, object]) -> None:
        if record["event"] == "stage_failed":
            raise TrafficlabError("injected logging failure", corrective_action="repair logging")

    monkeypatch.setattr(fitting, "append_run_log", fail_failure_log)

    with pytest.raises(TrafficlabError, match="checkpoint unreadable.*additionally.*injected logging failure") as error:
        fit_experiment(experiment_path, dependencies=_dependencies(config, experiment_path, inputs, fail_strategy))

    assert error.value.corrective_action == "restore checkpoint"
    assert error.value.exit_code == 7


def test_read_fit_input_translates_filesystem_failures(tmp_path: Path) -> None:
    """A raw OSError would bypass the fit CLI's actionable package-error boundary."""
    missing = tmp_path / "missing.bin"
    with pytest.raises(TrafficlabError, match="could not read fit input") as error:
        read_fit_input(missing)
    assert error.value.corrective_action == "verify the prepared fit inputs exist and are readable"


def test_production_fit_dependencies_select_the_real_offline_boundaries() -> None:
    """The default fit route must use local preparation, exact input reads, and the in-process strategy."""
    from trafficlab.genetic.strategy import run_strategy

    dependencies = FitDependencies.production()

    assert dependencies.open_or_prepare is open_or_prepare_experiment
    assert dependencies.read_bytes is read_fit_input
    assert dependencies.strategy is run_strategy


def test_strategy_contract_violation_with_missing_winner_genes_never_publishes(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Artifact construction requires the validated strategy winner's canonical chromosome."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    malformed_winner = Candidate(
        CandidateId(0, 0),
        "poisson_empirical",
        None,
        "valid",
        0.75,
        (_trial(config.genetic.trial_seeds[0]),),
        None,
        (),
    )
    outcome = FitOutcome(
        malformed_winner,
        (_trial(config.run.final_seed),),
        0,
        "hard_limit",
        ("poisson_empirical",),
    )

    with pytest.raises(AssertionError, match="canonical genes"):
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, lambda _context: outcome),
        )

    assert not (run_directory / "best_model.json").exists()


def test_fit_rejects_a_strategy_priority_that_disagrees_with_its_checkpoint_context(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Final publication must not accept a strategy result from a different priority lineage."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory)
    inputs = _inputs(config)
    outcome = replace(_outcome(config), family_priority=("mmpp",))

    with pytest.raises(AssertionError, match="family priority"):
        fit_experiment(
            experiment_path,
            dependencies=_dependencies(config, experiment_path, inputs, lambda _context: outcome),
        )

    assert not (run_directory / "best_model.json").exists()


def test_best_model_is_exclusive_except_validated_identical_reuse(tmp_path: Path) -> None:
    """A rerun may prove exact identity but must never overwrite a distinct completed model."""
    destination = tmp_path / "best_model.json"
    first_content = _valid_best_bytes(gene=1.0)
    other_content = _valid_best_bytes(gene=1.5)

    first = publish_best_model(destination, first_content)
    reused = publish_best_model(destination, first_content)

    assert (first.path, first.content, first.created_by_call) == (destination, first_content, True)
    assert (reused.path, reused.content, reused.created_by_call) == (destination, first_content, False)
    with pytest.raises(TrafficlabError, match=r"best_model\.json already exists"):
        publish_best_model(destination, other_content)
    assert destination.read_bytes() == first_content


def test_best_model_rejects_malformed_prospective_or_existing_bytes_without_replacement(tmp_path: Path) -> None:
    """Byte equality alone cannot bless malformed state, and rejected caller state must be preserved."""
    destination = tmp_path / "best_model.json"
    with pytest.raises(TrafficlabError, match="best model|best-model"):
        publish_best_model(destination, b"{}\n")
    assert not destination.exists()

    document = json.loads(_valid_best_bytes())
    noncanonical = (json.dumps(document, indent=2) + "\n").encode()
    with pytest.raises(TrafficlabError, match="not canonical"):
        publish_best_model(destination, noncanonical)
    assert not destination.exists()

    malformed = b"caller-owned malformed model\n"
    destination.write_bytes(malformed)
    with pytest.raises(TrafficlabError, match="best model|JSON"):
        publish_best_model(destination, _valid_best_bytes())
    assert destination.read_bytes() == malformed


def test_best_model_reports_an_unreadable_existing_destination_without_replacing_it(tmp_path: Path) -> None:
    """A non-readable destination must be preserved instead of being treated as absence."""
    destination = tmp_path / "best_model.json"
    destination.mkdir()

    with pytest.raises(TrafficlabError, match="could not read best model"):
        publish_best_model(destination, _valid_best_bytes())

    assert destination.is_dir()


def test_best_model_rejects_and_preserves_a_dangling_destination_symlink(tmp_path: Path) -> None:
    """A dangling symlink is an existing malformed artifact entry, not permission to publish through its name."""
    destination = tmp_path / "best_model.json"
    destination.symlink_to(tmp_path / "missing-target.json")

    with pytest.raises(TrafficlabError, match="existing best model entry.*unreadable"):
        publish_best_model(destination, _valid_best_bytes())

    assert destination.is_symlink()
    assert os.readlink(destination) == str(tmp_path / "missing-target.json")
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_absence_probe_oserror_is_translated_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed lstat cannot be treated as proof that an exclusive destination name is absent."""
    destination = tmp_path / "best_model.json"
    real_read_bytes = Path.read_bytes
    real_lstat = Path.lstat

    def missing_read(path: Path) -> bytes:
        if path == destination:
            raise FileNotFoundError("injected missing read")
        return real_read_bytes(path)

    def fail_lstat(path: Path) -> os.stat_result:
        if path == destination:
            raise OSError("injected lstat failure")
        return real_lstat(path)

    monkeypatch.setattr(Path, "read_bytes", missing_read)
    monkeypatch.setattr(Path, "lstat", fail_lstat)

    with pytest.raises(TrafficlabError, match="could not inspect best model entry.*lstat failure"):
        publish_best_model(destination, _valid_best_bytes())

    assert not destination.exists()


@pytest.mark.parametrize("collision", [False, True], ids=["existing", "link-race-winner"])
def test_best_model_reuse_rejects_an_entry_replaced_immediately_after_its_validation_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision: bool,
) -> None:
    """Validated best-model bytes cannot authorize reuse of a subsequently replaced canonical entry."""
    destination = tmp_path / "best_model.json"
    expected = _valid_best_bytes(gene=1.0)
    replacement = _valid_best_bytes(gene=1.5)
    if not collision:
        destination.write_bytes(expected)

    real_read_bytes = Path.read_bytes
    real_link = os.link
    replaced = False

    def replace_after_read(path: Path) -> bytes:
        nonlocal replaced
        content = real_read_bytes(path)
        if path == destination and not replaced:
            replacement_path = tmp_path / "replacement-best-model.json"
            replacement_path.write_bytes(replacement)
            os.replace(replacement_path, destination)
            replaced = True
        return content

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(expected)
        real_link(source, target)

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    if collision:
        monkeypatch.setattr(artifacts.os, "link", collide)

    with pytest.raises(TrafficlabError, match="changed during.*validation"):
        publish_best_model(destination, expected)

    assert replaced is True
    assert real_read_bytes(destination) == replacement
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


@pytest.mark.parametrize("racing_bytes_match", [True, False])
def test_best_model_publication_race_preserves_and_validates_the_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, racing_bytes_match: bool
) -> None:
    """Losing an exclusive-link race must validate the winner and clean only this call's temporary file."""
    destination = tmp_path / "best_model.json"
    prospective = _valid_best_bytes(gene=1.0)
    winner = prospective if racing_bytes_match else _valid_best_bytes(gene=1.5)
    real_link = os.link

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(winner)
        real_link(source, target)

    monkeypatch.setattr(artifacts.os, "link", collide)

    if racing_bytes_match:
        publication = publish_best_model(destination, prospective)
        assert publication.created_by_call is False
    else:
        with pytest.raises(TrafficlabError, match=r"best_model\.json already exists"):
            publish_best_model(destination, prospective)

    assert destination.read_bytes() == winner
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_race_winner_directory_entry_is_made_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A losing publisher must durably acknowledge the racing winner before reporting identical reuse."""
    destination = tmp_path / "best_model.json"
    content = _valid_best_bytes()
    real_link = os.link
    real_open = os.open
    events: list[str] = []

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(content)
        real_link(source, target)

    def observed_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & getattr(os, "O_DIRECTORY", 0):
            events.append("directory_open")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifacts.os, "link", collide)
    monkeypatch.setattr(artifacts.os, "open", observed_open)

    publication = publish_best_model(destination, content)

    assert publication.created_by_call is False
    assert events == ["directory_open"]


def test_best_model_reports_a_disappearing_collision_winner_without_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A race winner that disappears before validation is an actionable publication error, never an assertion."""
    destination = tmp_path / "best_model.json"
    real_link = os.link

    def disappear(source: str | Path, target: str | Path) -> None:
        target_path = Path(target)
        target_path.write_bytes(_valid_best_bytes())
        try:
            real_link(source, target)
        except FileExistsError:
            target_path.unlink()
            raise

    monkeypatch.setattr(artifacts.os, "link", disappear)

    with pytest.raises(TrafficlabError, match="publication race winner disappeared"):
        publish_best_model(destination, _valid_best_bytes())

    assert not destination.exists()
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_publication_fsyncs_the_containing_directory_after_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flushed temporary file alone does not make the new directory entry crash-durable."""
    destination = tmp_path / "best_model.json"
    events: list[str] = []
    real_link = os.link
    real_open = os.open

    def observed_link(source: str | Path, target: str | Path) -> None:
        events.append("link")
        real_link(source, target)

    def observed_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & getattr(os, "O_DIRECTORY", 0):
            events.append("directory_open")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifacts.os, "link", observed_link)
    monkeypatch.setattr(artifacts.os, "open", observed_open)

    publish_best_model(destination, _valid_best_bytes())

    assert events == ["link", "directory_open"]


def test_best_model_directory_durability_failure_preserves_the_published_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-link directory failure is reportable but must never roll back the exclusive winner."""
    destination = tmp_path / "best_model.json"
    content = _valid_best_bytes()
    real_open = os.open

    def fail_directory_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if flags & getattr(os, "O_DIRECTORY", 0):
            raise OSError("injected directory durability failure")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(artifacts.os, "open", fail_directory_open)

    with pytest.raises(TrafficlabError, match="directory durability failure.*destination may be present") as caught:
        publish_best_model(destination, content)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "publication_failed"
    assert outcome.stage == "fit"
    assert outcome.affected_evidence == "best_model.json"
    assert outcome.evidence_state == "preserved"
    assert destination.read_bytes() == content
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_post_link_temp_cleanup_failure_preserves_the_published_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owned-temp cleanup failure after publication must not delete or overwrite the valid winner."""
    destination = tmp_path / "best_model.json"
    content = _valid_best_bytes()
    real_unlink = os.unlink
    attempts = 0

    def fail_temp_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *args: object, **kwargs: object
    ) -> None:
        nonlocal attempts
        if Path(os.fsdecode(path)).name.startswith(".best_model.json."):
            attempts += 1
            raise OSError("injected post-link cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "unlink", fail_temp_unlink)

    with pytest.raises(TrafficlabError, match="was published.*post-link cleanup failure"):
        publish_best_model(destination, content)

    assert attempts == 1
    assert destination.read_bytes() == content


def test_best_model_rejects_a_changed_persisted_temporary_copy_before_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation must inspect exact persisted temporary bytes, not only the prospective in-memory model."""
    destination = tmp_path / "best_model.json"
    content = _valid_best_bytes()
    real_read_bytes = Path.read_bytes

    def changed_temp(path: Path) -> bytes:
        if path.name.startswith(".best_model.json."):
            return b"changed after fsync\n"
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", changed_temp)

    with pytest.raises(TrafficlabError, match="persisted temporary best model differs"):
        publish_best_model(destination, content)

    assert not destination.exists()
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_link_and_cleanup_failure_reports_both_without_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication failure cleanup stays limited to its owned temp and retains both failure details."""
    destination = tmp_path / "best_model.json"
    real_unlink = os.unlink
    cleanup_attempts = 0

    def fail_link(_source: str | Path, _target: str | Path) -> None:
        raise OSError("injected link failure")

    def fail_temp_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *args: object, **kwargs: object
    ) -> None:
        nonlocal cleanup_attempts
        if Path(os.fsdecode(path)).name.startswith(".best_model.json."):
            cleanup_attempts += 1
            raise OSError("injected failure cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "link", fail_link)
    monkeypatch.setattr(artifacts.os, "unlink", fail_temp_unlink)

    with pytest.raises(TrafficlabError, match="injected link failure.*cleanup incomplete.*cleanup failure"):
        publish_best_model(destination, _valid_best_bytes())

    assert cleanup_attempts == 1
    assert not destination.exists()


def test_best_model_unexpected_link_error_propagates_after_owned_temp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected programming failures must not be disguised as expected publication errors."""
    destination = tmp_path / "best_model.json"

    def fail_link(_source: str | Path, _target: str | Path) -> None:
        raise RuntimeError("injected unexpected link defect")

    monkeypatch.setattr(artifacts.os, "link", fail_link)

    with pytest.raises(RuntimeError, match="unexpected link defect"):
        publish_best_model(destination, _valid_best_bytes())

    assert not destination.exists()
    assert list(tmp_path.glob(".best_model.json.*.tmp")) == []


def test_best_model_temp_creation_failure_has_no_cleanup_side_effect(tmp_path: Path) -> None:
    """A failure before temp ownership must report the write boundary without attempting cleanup."""
    missing_parent = tmp_path / "missing"
    destination = missing_parent / "best_model.json"

    with pytest.raises(TrafficlabError, match="could not publish best model"):
        publish_best_model(destination, _valid_best_bytes())

    assert not missing_parent.exists()
