"""Explicit complete-experiment coordinator contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from tests.support.pipeline import (
    fit_outcome,
    prepared_experiment,
    read_run_records,
    replace,
    success_dependencies,
    trial,
)
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.capture.lineage import CaptureResult
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.stage import FitStageResult
from trafficlab.generation.stage import GenerationStageResult
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies
from trafficlab.preflight.stage import PreparedExperiment


@pytest.mark.parametrize("corruption", ["type", "source", "directory", "relative-directory"])
def test_run_experiment_rejects_invalid_preflight_results_without_coordinator_logging(
    valid_config_data: dict[str, object], tmp_path: Path, corruption: str
) -> None:
    """The coordinator cannot trust or write a run log until the prepared result is exact."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
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

    assert [record for record in read_run_records(prepared) if record.get("event") == "run_failed"] == []


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
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (capture, fit, generation, _comparison_result) = success_dependencies(
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
            fit = replace(fit, outcome=fit_outcome(prepared.config.run.final_seed + 1))

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
        ("generate", "events-type", "TrafficTrace"),
        ("generate", "event-member", "TrafficTrace"),
        ("generate", "reuse", "reuse"),
        ("generate", "output-identity", "generated output trace"),
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
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (_capture, fit, generation, comparison) = success_dependencies(experiment_path, prepared, calls)

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
            object.__setattr__(fit.outcome.winner, "trials", (trial(999),))
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
            object.__setattr__(
                fit.best_model,
                "capture_identity",
                ContentIdentity(size=fit.best_model.capture_identity.size, sha256="0" * 64),
            )
        else:
            object.__setattr__(
                fit.best_model,
                "reference_identity",
                ContentIdentity(size=fit.best_model.reference_identity.size, sha256="0" * 64),
            )

        def corrupted_fit(path: Path) -> FitStageResult:
            del path
            calls.append("fit")
            return fit

        object.__setattr__(dependencies, "fit", corrupted_fit)
    elif stage == "generate":
        if corruption == "window-type":
            generation = replace(generation, observation_window_seconds=cast(Any, 10))
        elif corruption == "events-type":
            generation = replace(generation, trace=cast(Any, list(generation.trace)))
        elif corruption == "event-member":
            generation = replace(generation, trace=cast(Any, (object(),)))
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
                aggregate_score=comparison.aggregate_score,
                observation_window_seconds=comparison.observation_window_seconds,
                methods=comparison.methods,
                input_identities=None,
            )
        else:
            assert comparison.input_identities is not None
            identities = comparison.input_identities.as_content_identities()
            identity_name = {
                "lineage-capture": "capture_json",
                "lineage-reference": "reference_pcapng",
                "lineage-generated": "generated_pcapng",
                "lineage-settings": "similarity_settings",
            }[corruption]
            identities[identity_name] = ContentIdentity(size=identities[identity_name].size, sha256="0" * 64)
            comparison = comparison.with_input_identities(identities)

        def corrupted_comparison(path: Path) -> ComparisonResult:
            del path
            calls.append("compare")
            return comparison

        object.__setattr__(dependencies, "compare", corrupted_comparison)

    with pytest.raises(TrafficlabError, match=match):
        run_experiment(experiment_path, dependencies=dependencies)

    stop = ["capture", "fit", "generate", "compare"].index(stage) + 2
    assert calls == ["preflight", "capture", "fit", "generate", "compare"][:stop]
    failures = [record for record in read_run_records(prepared) if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == stage
    assert f"{stage} returned invalid result" in cast(str, failures[0]["detail"])
    assert [record for record in read_run_records(prepared) if record.get("event") == "run_completed"] == []
