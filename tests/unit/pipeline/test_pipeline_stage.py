"""Explicit complete-experiment coordinator contract tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.capture.stage as capture_module
import trafficlab.pipeline.stage as run_stage
import trafficlab.pipeline.types as run_types
from tests.support.pipeline import (
    make_comparison_result,
    prepared_experiment,
    read_run_records,
    stage_results,
    success_dependencies,
)
from trafficlab.capture.stage import CaptureResult
from trafficlab.common.errors import TrafficlabError
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.stage import FitStageResult
from trafficlab.generation.stage import GenerationStageResult
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.preflight.stage import PreparedExperiment


def test_run_experiment_calls_five_stages_directly_in_order_and_returns_their_exact_results(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Reordering, copying, or substituting stage results would break the in-process contract."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, expected = success_dependencies(experiment_path, prepared, calls)

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
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _expected = success_dependencies(experiment_path, prepared, [])

    run_experiment(experiment_path, dependencies=dependencies)

    completions = [record for record in read_run_records(prepared) if record.get("event") == "run_completed"]
    assert completions == [
        {
            "aggregate_score": 0.5662202380952381,
            "event": "run_completed",
            "family": "poisson_empirical",
            "fitness": 0.8,
            "generated_packet_count": 2,
            "reference_packet_count": 2,
            "run_directory": str(prepared.run_directory),
            "stage": "run",
        }
    ]


def test_run_completion_log_failure_reports_preserved_run_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing final log record must not recast a completed comparison as absent."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _expected = success_dependencies(experiment_path, prepared, [])
    original_append = run_stage.append_run_log

    def fail_only_completion(run_directory: Path, record: object) -> None:
        document = cast(dict[str, object], record)
        if document.get("event") == "run_completed":
            raise TrafficlabError("synthetic completion log failure", corrective_action="repair run.log")
        original_append(run_directory, document)

    monkeypatch.setattr(run_stage, "append_run_log", fail_only_completion)
    similarity_before = (prepared.run_directory / "similarity.json").read_bytes()

    with pytest.raises(TrafficlabError, match="final run completion logging failed") as caught:
        run_experiment(experiment_path, dependencies=dependencies)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "affected_evidence": "run.log",
        "authority": "primary",
        "corrective_action": "repair run.log",
        "detail": "final run completion logging failed: synthetic completion log failure",
        "evidence_state": "preserved",
        "kind": "publication_failed",
        "stage": "publication",
    }
    assert (prepared.run_directory / "similarity.json").read_bytes() == similarity_before
    records = read_run_records(prepared)
    assert [record for record in records if record.get("event") == "run_completed"] == []
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert failures == [
        {
            "completed_stages": ["preflight", "capture", "fit", "generate", "compare"],
            "corrective_action": "repair run.log",
            "detail": "final run completion logging failed: synthetic completion log failure",
            "event": "run_failed",
            "failed_stage": "run",
            "failure_outcome": outcome.as_dict(),
            "stage": "run",
        }
    ]


@pytest.mark.parametrize(
    ("failed_stage", "exit_code"),
    [("preflight", 11), ("capture", 12), ("fit", 13), ("generate", 14), ("compare", 15)],
)
def test_run_experiment_stops_at_each_primary_stage_failure_and_preserves_earlier_artifacts(
    valid_config_data: dict[str, object], tmp_path: Path, failed_stage: str, exit_code: int
) -> None:
    """A later call or rollback after a stage failure would destroy useful completed research evidence."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    sentinel = prepared.run_directory / "earlier-stage.bin"
    sentinel.write_bytes(b"preserve exactly")
    calls: list[str] = []
    dependencies, _results = success_dependencies(experiment_path, prepared, calls)

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
    coordinator_failures = [record for record in read_run_records(prepared) if record.get("event") == "run_failed"]
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
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = success_dependencies(experiment_path, prepared, calls)

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
    failures = [record for record in read_run_records(prepared) if record.get("event") == "run_failed"]
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
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (capture, fit, generation, comparison) = success_dependencies(experiment_path, prepared, calls)
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
            generation.trace,
            generation.seed + 1 if mutation == "seed" else generation.seed,
            11.0 if mutation == "window" else generation.observation_window_seconds,
            generation.reused,
        )
    else:
        comparison = make_comparison_result(11.0)

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
    assert len([record for record in read_run_records(prepared) if record.get("event") == "run_failed"]) == 1


def test_run_experiment_retains_primary_error_when_run_failure_logging_also_fails(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic write failure must remain secondary to the stage error and preserve its exit code."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = success_dependencies(experiment_path, prepared, [])
    primary = TrafficlabError("primary capture failure", corrective_action="fix capture", exit_code=42)

    def fail_capture(path: Path, value: PreparedExperiment) -> CaptureResult:
        del path, value
        raise primary

    def fail_log(run_directory: Path, record: object) -> None:
        del run_directory, record
        raise TrafficlabError("secondary log failure", corrective_action="fix log", exit_code=99)

    monkeypatch.setattr(run_stage, "append_run_log", fail_log)
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
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)

    def fail_preflight(path: Path) -> PreparedExperiment:
        del path
        raise TrafficlabError("direct preflight failure", corrective_action="fix preflight", exit_code=11)

    def reject_log(run_directory: Path, record: object) -> None:
        del run_directory, record
        raise AssertionError("coordinator accessed run.log after preflight failure")

    monkeypatch.setattr(run_stage, "append_run_log", reject_log)
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
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    capture, fit, generation, comparison = stage_results(experiment_path, prepared)
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

    monkeypatch.setattr(run_types, "run_preflight", preflight)
    monkeypatch.setattr(run_types, "capture_prepared_experiment", prepared_capture)
    monkeypatch.setattr(capture_module, "capture_experiment", capture_wrapper)
    monkeypatch.setattr(run_types, "fit_experiment", fit_stage)
    monkeypatch.setattr(run_types, "generate_experiment", generate_stage)
    monkeypatch.setattr(run_types, "compare_experiment", compare_stage)

    run_experiment(experiment_path)

    assert calls == [
        ("preflight", experiment_path, False),
        ("capture_prepared", experiment_path, prepared),
    ]
