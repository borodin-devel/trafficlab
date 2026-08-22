"""Cohesive fitting behavior tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts.io as artifact_io
import trafficlab.fitting.stage as fitting
from tests.support.fitting import (
    NORMALIZED_REFERENCE,
    build_config,
    build_dependencies,
    build_inputs,
    build_outcome,
    build_trial,
)
from tests.support.fitting import (
    replace_record as replace,
)
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import FailureOutcome, TrafficlabError
from trafficlab.common.trace import TrafficTrace
from trafficlab.fitting.genetic.strategy import FitOutcome, StrategyContext
from trafficlab.fitting.genetic.types import Candidate, CandidateId
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.models.fitted_model import (
    load_best_model,
)
from trafficlab.generation.models.poisson import PoissonFamily


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


def test_fit_hashes_exact_bytes_and_passes_one_normalized_window(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lineage comes from evaluated bytes and the same inputs are rechecked before publication."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
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
        return build_outcome(config)

    monkeypatch.setattr(fitting, "normalize_reference", normalize)
    result = fit_experiment(
        experiment_path, dependencies=build_dependencies(config, experiment_path, inputs, strategy, reads=reads)
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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    reference_path = run_directory / "reference.pcapng"

    def mutate_reference(_context: StrategyContext) -> FitOutcome:
        inputs[reference_path] += b"changed after fitting"
        return build_outcome(config)

    with pytest.raises(TrafficlabError, match="reference.pcapng changed during fit") as caught:
        fit_experiment(
            experiment_path,
            dependencies=build_dependencies(config, experiment_path, inputs, mutate_reference),
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
    config = build_config(valid_config_data, run_directory)
    changed = config.model_copy(update={"run": config.run.model_copy(update={"master_seed": 999_001})})
    inputs = build_inputs(config, snapshot=render_effective_config(changed))
    strategy_called = False

    def forbidden(_context: StrategyContext) -> FitOutcome:
        nonlocal strategy_called
        strategy_called = True
        return build_outcome(config)

    with pytest.raises(TrafficlabError, match="authoritative experiment snapshot") as error:
        fit_experiment(experiment_path, dependencies=build_dependencies(config, experiment_path, inputs, forbidden))

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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    outcome = build_outcome(config, genes=(1.25,))
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
        artifact_io.append_run_log(run_directory, record)

    monkeypatch.setattr(fitting, "append_run_log", switch_to_publication)
    result = fit_experiment(experiment_path, dependencies=build_dependencies(config, experiment_path, inputs, strategy))

    assert events == [("final_validation", (1.25,)), ("make_best_model", (1.25,))]
    assert result.best_model.genes == result.outcome.winner.genes == (1.25,)
    assert cast(Any, result.best_model.fitted).rate == 1.25


def test_final_validation_failure_publishes_no_best_model(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """A checkpointed winner without fresh final evidence is not a publishable model."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)

    def fail_final(_context: StrategyContext) -> FitOutcome:
        raise TrafficlabError("final validation failed", corrective_action="repair final validation")

    with pytest.raises(TrafficlabError, match="final validation"):
        fit_experiment(experiment_path, dependencies=build_dependencies(config, experiment_path, inputs, fail_final))

    assert not (run_directory / "best_model.json").exists()
    records = [json.loads(line) for line in (run_directory / "run.log").read_text().splitlines()]
    assert [record["event"] for record in records] == ["fit_started", "stage_failed"]


def test_noncanonical_strategy_winner_is_not_repaired_into_a_different_published_model(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Final validation and artifact construction must describe the exact same canonical winner genes."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    outcome = build_outcome(config, genes=(99.0,))

    with pytest.raises(AssertionError, match="same canonical winner genes"):
        fit_experiment(
            experiment_path,
            dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: outcome),
        )

    assert not (run_directory / "best_model.json").exists()


def test_fit_logs_only_completed_events_in_stage_order(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """Logging a prospective checkpoint, validation, or artifact would make a failed run look complete."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)

    result = fit_experiment(
        experiment_path,
        dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: build_outcome(config)),
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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    real_append = artifact_io.append_run_log

    def fail_final_log(directory: Path, record: dict[str, object]) -> None:
        if record["event"] == "best_model_published":
            raise TrafficlabError("injected logging failure", corrective_action="repair logging")
        real_append(directory, record)

    monkeypatch.setattr(fitting, "append_run_log", fail_final_log)

    with pytest.raises(TrafficlabError, match="best model was published.*injected logging failure"):
        fit_experiment(
            experiment_path,
            dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: build_outcome(config)),
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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)

    def fail_strategy(_context: StrategyContext) -> FitOutcome:
        raise TrafficlabError("checkpoint unreadable", corrective_action="restore checkpoint", exit_code=7)

    def fail_failure_log(_directory: Path, record: dict[str, object]) -> None:
        if record["event"] == "stage_failed":
            raise TrafficlabError("injected logging failure", corrective_action="repair logging")

    monkeypatch.setattr(fitting, "append_run_log", fail_failure_log)

    with pytest.raises(TrafficlabError, match="checkpoint unreadable.*additionally.*injected logging failure") as error:
        fit_experiment(experiment_path, dependencies=build_dependencies(config, experiment_path, inputs, fail_strategy))

    assert error.value.corrective_action == "restore checkpoint"
    assert error.value.exit_code == 7


def test_strategy_contract_violation_with_missing_winner_genes_never_publishes(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Artifact construction requires the validated strategy winner's canonical chromosome."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    malformed_winner = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="poisson_empirical",
        genes=None,
        status="valid",
        fitness=0.75,
        trials=(build_trial(config.genetic.trial_seeds[0]),),
        invalid=None,
        duplicate_diagnostics=(),
    )
    outcome = FitOutcome(
        malformed_winner,
        (build_trial(config.run.final_seed),),
        0,
        "hard_limit",
        ("poisson_empirical",),
    )

    with pytest.raises(AssertionError, match="canonical genes"):
        fit_experiment(
            experiment_path,
            dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: outcome),
        )

    assert not (run_directory / "best_model.json").exists()


def test_fit_rejects_a_strategy_priority_that_disagrees_with_its_checkpoint_context(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Final publication must not accept a strategy result from a different priority lineage."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    outcome = replace(build_outcome(config), family_priority=("mmpp",))

    with pytest.raises(AssertionError, match="family priority"):
        fit_experiment(
            experiment_path,
            dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: outcome),
        )

    assert not (run_directory / "best_model.json").exists()
