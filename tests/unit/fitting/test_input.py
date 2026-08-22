"""Cohesive fitting behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import trafficlab.fitting.stage as fitting
from tests.support.fitting import (
    build_config,
    build_dependencies,
    build_inputs,
    build_outcome,
    build_prepared,
)
from trafficlab.common.errors import FailureOutcome, TrafficlabError
from trafficlab.common.trace import TraceEvent
from trafficlab.fitting.genetic.strategy import FitOutcome, StrategyContext, run_strategy
from trafficlab.fitting.stage import FitDependencies, fit_experiment, read_fit_input
from trafficlab.preflight.stage import open_or_prepare_experiment


def test_fit_public_boundary_classifies_a_missing_reference_input(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A stage dependency's missing source must not collapse to a best-model corruption fallback."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)

    def read(path: Path) -> bytes:
        if path.name == "reference.pcapng":
            raise FileNotFoundError("injected missing reference")
        return inputs[path]

    dependencies = FitDependencies(
        lambda _path: build_prepared(config, experiment_path),
        read,
        lambda _context: build_outcome(config),
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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
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
        lambda _path: build_prepared(config, experiment_path),
        read,
        lambda _context: build_outcome(config),
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


def test_fit_classifies_reference_normalization_error(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owning fit boundary retains a normalization failure as preserved reference evidence."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)

    def fail_normalization(_events: object) -> tuple[tuple[TraceEvent, ...], float]:
        raise TrafficlabError("injected normalization failure", corrective_action="repair reference ordering")

    monkeypatch.setattr(fitting, "normalize_reference", fail_normalization)

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
        "reference.pcapng",
        "preserved",
    )


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
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)
    inputs[run_directory / changed_input] = b"malformed\n"
    strategy_called = False

    def forbidden(_context: StrategyContext) -> FitOutcome:
        nonlocal strategy_called
        strategy_called = True
        return build_outcome(config)

    with pytest.raises(TrafficlabError, match=expected):
        fit_experiment(experiment_path, dependencies=build_dependencies(config, experiment_path, inputs, forbidden))

    assert strategy_called is False
    assert not (run_directory / "best_model.json").exists()


def test_read_fit_input_translates_filesystem_failures(tmp_path: Path) -> None:
    """A raw OSError would bypass the fit CLI's actionable package-error boundary."""
    missing = tmp_path / "missing.bin"
    with pytest.raises(TrafficlabError, match="could not read fit input") as error:
        read_fit_input(missing)
    assert error.value.corrective_action == "verify the prepared fit inputs exist and are readable"


def test_production_fit_dependencies_select_the_real_offline_boundaries() -> None:
    """The default fit route must use local preparation, exact input reads, and the in-process strategy."""

    dependencies = FitDependencies.production()

    assert dependencies.open_or_prepare is open_or_prepare_experiment
    assert dependencies.read_bytes is read_fit_input
    assert dependencies.strategy is run_strategy
