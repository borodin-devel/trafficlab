"""Offline evidence for uninterrupted and checkpoint-resumed pipeline equivalence."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import trafficlab.docker_cli as docker_cli
import trafficlab.genetic.strategy as genetic_strategy
from trafficlab.artifacts import create_run_directory, load_or_recover_capture_pair
from trafficlab.capture import CaptureResult
from trafficlab.comparison import ComparisonResult, compare_experiment, sha256_bytes, similarity_settings_sha256
from trafficlab.compatibility import identify_bytes
from trafficlab.config_io import ConfigurationPair, load_configuration_pair, render_effective_config
from trafficlab.fitting import FitDependencies, FitStageResult, fit_experiment, read_fit_input
from trafficlab.generation import GenerationStageResult, generate_experiment
from trafficlab.genetic.checkpoint import CheckpointState, load_checkpoint
from trafficlab.genetic.strategy import make_strategy_context, run_strategy
from trafficlab.genetic.types import METHOD_ORDER
from trafficlab.models.registry import load_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.preflight import PreflightReport, PreparedExperiment
from trafficlab.run import RunDependencies, run_experiment
from trafficlab.trace import normalize_reference, parse_capture_metadata

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_FIT_DIRECTORY = _ROOT / "examples" / "data" / "fit"
_SOURCE_EXPERIMENT = _FIT_DIRECTORY / "experiment.toml"
_CAPTURE_BYTES = (_FIT_DIRECTORY / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_DIRECTORY / "reference.pcapng").read_bytes()
_NINE_FILE_INVENTORY = (
    "best_model.json",
    "capture.json",
    "checkpoint.json",
    "experiment.toml",
    "ga_history.csv",
    "generated.pcapng",
    "reference.pcapng",
    "run.log",
    "similarity.json",
)
_SCIENTIFIC_FILES = tuple(name for name in _NINE_FILE_INVENTORY if name != "run.log")


@dataclass(frozen=True, slots=True)
class _OfflineRun:
    experiment_path: Path
    pair: ConfigurationPair
    prepared: PreparedExperiment
    fit_dependencies: FitDependencies


def _prepare_offline_run(tmp_path: Path) -> _OfflineRun:
    """Create the checked capture pair from a portable caller configuration without Docker."""
    portable = load_configuration_pair(_SOURCE_EXPERIMENT).portable
    caller = tmp_path / "experiment.toml"
    portable = portable.model_copy(update={"run": portable.run.model_copy(update={"directory": Path("run")})})
    caller.write_bytes(render_effective_config(portable))
    pair = load_configuration_pair(caller)
    create_run_directory(pair.realized)
    (pair.realized.run.directory / "capture.json").write_bytes(_CAPTURE_BYTES)
    (pair.realized.run.directory / "reference.pcapng").write_bytes(_REFERENCE_BYTES)
    prepared = PreparedExperiment(
        source=caller,
        portable_config=pair.portable,
        config=pair.realized,
        report=PreflightReport(pair.realized, ()),
        run_directory=pair.realized.run.directory,
    )
    return _OfflineRun(
        caller,
        pair,
        prepared,
        FitDependencies(lambda _path: prepared, read_fit_input, run_strategy),
    )


def _offline_pipeline_dependencies(offline: _OfflineRun, calls: list[str]) -> RunDependencies:
    """Inject only the no-Docker capture boundary; later stage owners remain real."""

    def preflight(path: Path) -> PreparedExperiment:
        assert path == offline.experiment_path
        calls.append("preflight")
        return offline.prepared

    def capture(path: Path, prepared: PreparedExperiment) -> CaptureResult:
        assert path == offline.experiment_path
        assert prepared == offline.prepared
        calls.append("capture")
        pair = load_or_recover_capture_pair(prepared.run_directory, deadline=None)
        assert pair is not None
        return CaptureResult(
            prepared.run_directory,
            prepared.run_directory / "reference.pcapng",
            pair.packet_count,
            0,
            reused=True,
        )

    def fit(path: Path) -> FitStageResult:
        assert path == offline.experiment_path
        calls.append("fit")
        return fit_experiment(path, dependencies=offline.fit_dependencies)

    def generate(path: Path) -> GenerationStageResult:
        assert path == offline.experiment_path
        calls.append("generate")
        return generate_experiment(path)

    def compare(path: Path) -> ComparisonResult:
        assert path == offline.experiment_path
        calls.append("compare")
        return compare_experiment(path)

    return RunDependencies(preflight, capture, fit, generate, compare)


def _interrupt_after_atomic_generation_zero(offline: _OfflineRun, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave only a production-published whole-generation checkpoint for resume."""
    real_publish = genetic_strategy.publish_generation

    def publish_then_interrupt(destination: Path, state: CheckpointState) -> None:
        real_publish(destination, state)
        if state.generation == 0:
            raise KeyboardInterrupt

    with monkeypatch.context() as scoped:
        scoped.setattr(genetic_strategy, "publish_generation", publish_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            fit_experiment(offline.experiment_path, dependencies=offline.fit_dependencies)


def _strategy_context(offline: _OfflineRun):  # type: ignore[no-untyped-def]
    run_directory = offline.prepared.run_directory
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=run_directory / "capture.json")
    parsed = parse_pcapng_bytes(_REFERENCE_BYTES, metadata, source=run_directory / "reference.pcapng")
    reference, window = normalize_reference(parsed)
    return make_strategy_context(
        offline.pair.realized,
        reference,
        window,
        run_directory,
        experiment_identity=identify_bytes((run_directory / "experiment.toml").read_bytes()),
        reference_identity=identify_bytes(_REFERENCE_BYTES),
        capture_identity=identify_bytes(_CAPTURE_BYTES),
    )


def _artifact_bytes(run_directory: Path, names: tuple[str, ...]) -> dict[str, bytes]:
    return {name: (run_directory / name).read_bytes() for name in names}


def _records(run_directory: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]


def _forbid_external_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline equivalence route may not construct Docker or launch a subprocess."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline pipeline constructed an external process boundary")

    monkeypatch.setattr(docker_cli, "DockerCompose", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def test_full_pipeline_resume_after_atomic_checkpoint_is_scientifically_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumed search must yield the same final evidence, not merely an equal score."""
    _forbid_external_processes(monkeypatch)
    uninterrupted = _prepare_offline_run(tmp_path)
    uninterrupted_calls: list[str] = []
    uninterrupted_result = run_experiment(
        uninterrupted.experiment_path,
        dependencies=_offline_pipeline_dependencies(uninterrupted, uninterrupted_calls),
    )
    uninterrupted_directory = uninterrupted.prepared.run_directory
    uninterrupted_bytes = _artifact_bytes(uninterrupted_directory, _NINE_FILE_INVENTORY)
    uninterrupted_records = _records(uninterrupted_directory)
    uninterrupted_context = _strategy_context(uninterrupted)
    uninterrupted_checkpoint = load_checkpoint(
        uninterrupted_directory / "checkpoint.json", uninterrupted_context.compatibility
    )
    uninterrupted_best = load_best_model(
        uninterrupted_bytes["best_model.json"], source=uninterrupted_directory / "best_model.json"
    )

    shutil.rmtree(uninterrupted_directory)
    resumed = _prepare_offline_run(tmp_path)
    _interrupt_after_atomic_generation_zero(resumed, monkeypatch)
    resumed_calls: list[str] = []
    resumed_result = run_experiment(
        resumed.experiment_path,
        dependencies=_offline_pipeline_dependencies(resumed, resumed_calls),
    )
    resumed_directory = resumed.prepared.run_directory
    resumed_bytes = _artifact_bytes(resumed_directory, _NINE_FILE_INVENTORY)
    resumed_records = _records(resumed_directory)
    resumed_context = _strategy_context(resumed)
    resumed_checkpoint = load_checkpoint(resumed_directory / "checkpoint.json", resumed_context.compatibility)
    resumed_best = load_best_model(resumed_bytes["best_model.json"], source=resumed_directory / "best_model.json")

    assert uninterrupted_calls == resumed_calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert sorted(uninterrupted_bytes) == sorted(resumed_bytes) == list(_NINE_FILE_INVENTORY)
    assert {name: uninterrupted_bytes[name] for name in _SCIENTIFIC_FILES} == {
        name: resumed_bytes[name] for name in _SCIENTIFIC_FILES
    }
    first_fit_record = next(
        index for index, record in enumerate(uninterrupted_records) if record["event"] == "fit_started"
    )
    assert resumed_records[:first_fit_record] == uninterrupted_records[:first_fit_record]
    assert resumed_records[first_fit_record]["event"] == "fit_started"
    assert resumed_records[first_fit_record + 1 :] == uninterrupted_records[first_fit_record:]
    assert uninterrupted_records[-1]["event"] == resumed_records[-1]["event"] == "run_completed"

    assert uninterrupted.pair == resumed.pair
    assert uninterrupted_result.fit.outcome == resumed_result.fit.outcome
    assert uninterrupted_result.fit.outcome.family_priority == resumed_result.fit.outcome.family_priority
    assert uninterrupted_checkpoint.family_priority == resumed_checkpoint.family_priority
    assert uninterrupted_checkpoint.rng_state == resumed_checkpoint.rng_state
    assert uninterrupted_checkpoint.population == resumed_checkpoint.population
    assert uninterrupted_checkpoint.history == resumed_checkpoint.history
    assert uninterrupted_checkpoint.best_identifier == resumed_checkpoint.best_identifier
    assert uninterrupted_checkpoint.best_fitness == resumed_checkpoint.best_fitness
    assert uninterrupted_result.fit.outcome.final_trials == resumed_result.fit.outcome.final_trials
    assert tuple(trial.seed for trial in uninterrupted_result.fit.outcome.final_trials) == (
        uninterrupted.pair.realized.run.final_seed,
    )
    assert tuple((candidate.identifier, candidate.genes) for candidate in uninterrupted_checkpoint.population) == tuple(
        (candidate.identifier, candidate.genes) for candidate in resumed_checkpoint.population
    )

    assert (
        uninterrupted_result.generation.seed
        == resumed_result.generation.seed
        == uninterrupted.pair.realized.run.final_seed
    )
    assert uninterrupted_result.comparison.aggregate_score == resumed_result.comparison.aggregate_score
    assert tuple(uninterrupted_result.comparison.methods) == tuple(resumed_result.comparison.methods) == METHOD_ORDER
    for method in METHOD_ORDER:
        assert uninterrupted_result.comparison.methods[method] == resumed_result.comparison.methods[method]
        assert "observation_window_seconds" in uninterrupted_result.comparison.methods[method].diagnostics

    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=uninterrupted_directory / "capture.json")
    assert parse_pcapng_bytes(
        uninterrupted_bytes["generated.pcapng"], metadata, source=uninterrupted_directory / "generated.pcapng"
    ) == parse_pcapng_bytes(resumed_bytes["generated.pcapng"], metadata, source=resumed_directory / "generated.pcapng")
    assert uninterrupted_best.reference_identity == resumed_best.reference_identity == identify_bytes(_REFERENCE_BYTES)
    assert uninterrupted_best.capture_identity == resumed_best.capture_identity == identify_bytes(_CAPTURE_BYTES)
    assert uninterrupted_checkpoint.compatibility.experiment_identity == identify_bytes(
        uninterrupted_bytes["experiment.toml"]
    )
    assert uninterrupted_checkpoint.compatibility.reference_identity == uninterrupted_best.reference_identity
    assert uninterrupted_checkpoint.compatibility.capture_identity == uninterrupted_best.capture_identity
    assert (
        uninterrupted_result.comparison.input_sha256
        == resumed_result.comparison.input_sha256
        == {
            "capture_json": sha256_bytes(_CAPTURE_BYTES),
            "generated_pcapng": sha256_bytes(uninterrupted_bytes["generated.pcapng"]),
            "reference_pcapng": sha256_bytes(_REFERENCE_BYTES),
            "similarity_settings": similarity_settings_sha256(uninterrupted.pair.realized.similarity),
        }
    )
