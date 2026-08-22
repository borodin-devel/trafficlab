"""Cohesive fitting behavior tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.fitting.genetic.checkpoint.history as checkpoint_history
import trafficlab.fitting.genetic.strategy as strategy_module
import trafficlab.fitting.stage as fitting
from tests.support.fitting import (
    METADATA,
    RAW_REFERENCE,
    build_config,
    build_dependencies,
    build_inputs,
    build_outcome,
    create_real_terminal_run,
    strategy_context_for_inputs,
    valid_best_bytes,
)
from tests.support.fitting import (
    replace_record as replace,
)
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.common.config import FloatBounds
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.checkpoint import load_checkpoint, publish_checkpoint
from trafficlab.fitting.genetic.strategy import FitOutcome, StrategyContext, run_strategy
from trafficlab.fitting.genetic.types import TrialResult
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.models.poisson import PoissonFamily


def test_existing_best_model_never_bypasses_strategy_or_checkpoint_failure(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Filename existence must not bypass checkpoint compatibility and fresh final validation."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    existing = valid_best_bytes()
    destination = run_directory / "best_model.json"
    destination.write_bytes(existing)
    calls = 0

    def fail_checkpoint(_context: StrategyContext) -> FitOutcome:
        nonlocal calls
        calls += 1
        raise TrafficlabError("checkpoint compatibility mismatch", corrective_action="restore checkpoint")

    with pytest.raises(TrafficlabError, match="checkpoint compatibility"):
        fit_experiment(
            experiment_path, dependencies=build_dependencies(config, experiment_path, inputs, fail_checkpoint)
        )

    assert calls == 1
    assert destination.read_bytes() == existing


def test_terminal_rerun_enters_strategy_refits_and_reuses_identical_best_model(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal best model is reusable only after strategy validation and a fresh artifact-construction fit."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    strategy_calls = 0
    publication_fits = 0
    original_fit = PoissonFamily.fit

    def strategy(_context: StrategyContext) -> FitOutcome:
        nonlocal strategy_calls
        strategy_calls += 1
        return build_outcome(config)

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
    dependencies = build_dependencies(config, experiment_path, inputs, strategy)

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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    initial = fit_experiment(
        experiment_path,
        dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: build_outcome(config)),
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
            dependencies=build_dependencies(config, experiment_path, inputs, forbidden_strategy, reads=reads),
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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    initial = fit_experiment(
        experiment_path,
        dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: build_outcome(config)),
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
            dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: build_outcome(config)),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "fit",
        "best_model.json",
        "preserved",
    )


def test_real_terminal_checkpoint_repairs_history_validates_refits_and_reuses_best_model(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production strategy re-entry must validate the real checkpoint before byte-identical artifact reuse."""
    experiment_path, config, inputs = create_real_terminal_run(valid_config_data, tmp_path)
    run_directory = config.run.directory
    best_before = (run_directory / "best_model.json").read_bytes()
    history_expected = (run_directory / "ga_history.csv").read_bytes()
    (run_directory / "ga_history.csv").write_bytes(b"stale derived history\n")
    events: list[str] = []
    original_load_checkpoint = checkpoint_history.load_checkpoint
    original_publish_history = checkpoint_history.publish_history_csv
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

    monkeypatch.setattr(checkpoint_history, "load_checkpoint", observed_load)
    monkeypatch.setattr(checkpoint_history, "publish_history_csv", observed_history)
    monkeypatch.setattr(strategy_module, "evaluate_final", observed_final)
    monkeypatch.setattr(fitting, "make_best_model", observed_make)
    monkeypatch.setattr(fitting, "publish_best_model", observed_publish)

    result = fit_experiment(
        experiment_path,
        dependencies=build_dependencies(config, experiment_path, inputs, run_strategy),
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
    experiment_path, config, inputs = create_real_terminal_run(valid_config_data, tmp_path)
    run_directory = config.run.directory
    best_before = (run_directory / "best_model.json").read_bytes()
    active_config = config
    active_inputs = dict(inputs)
    context = strategy_context_for_inputs(config, inputs)

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
            dependencies=build_dependencies(active_config, experiment_path, active_inputs, run_strategy),
        )

    assert (run_directory / "best_model.json").read_bytes() == best_before


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
    experiment_path, config, inputs = create_real_terminal_run(valid_config_data, tmp_path)
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
            dependencies=build_dependencies(config, experiment_path, inputs, run_strategy),
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
