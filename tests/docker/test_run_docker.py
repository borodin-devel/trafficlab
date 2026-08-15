"""Real-Docker evidence for the complete run and every orchestration boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Literal, cast

import pytest

import trafficlab.capture as capture_module
import trafficlab.generation as generation_module
import trafficlab.genetic.strategy as strategy_module
import trafficlab.run as run_module
from tests.conftest import DockerTestEnvironment, EndpointDockerCompose
from tests.docker.support import (
    assert_tracked_projects_clean,
    capture_log,
    capture_project_name,
    write_run_docker_experiment,
)
from trafficlab.capture import CaptureResult, capture_prepared_experiment
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.cli import main
from trafficlab.comparison import (
    ComparisonResult,
    compare_experiment,
    load_comparison_result,
    render_comparison_result,
    sha256_bytes,
    similarity_settings_sha256,
)
from trafficlab.compose import ComposePaths
from trafficlab.config import ExperimentConfig, GenerationLimits
from trafficlab.config_io import load_experiment
from trafficlab.docker_cli import CommandResult
from trafficlab.errors import TrafficlabError
from trafficlab.fitting import FitStageResult, fit_experiment
from trafficlab.generation import GenerationStageResult, generate_experiment
from trafficlab.genetic.checkpoint import load_checkpoint, render_history_csv
from trafficlab.genetic.strategy import make_strategy_context
from trafficlab.genetic.types import TrialResult
from trafficlab.models.common import FittedModel, GenerationResult, ModelFamily
from trafficlab.models.registry import load_best_model, render_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment, run_preflight
from trafficlab.run import RunDependencies, RunResult, run_experiment
from trafficlab.trace import Direction, normalize_reference, parse_capture_metadata

pytestmark = [pytest.mark.docker, pytest.mark.integration]

_ORDINARY_RUN_FILES = {
    "best_model.json",
    "capture.json",
    "checkpoint.json",
    "experiment.toml",
    "ga_history.csv",
    "generated.pcapng",
    "reference.pcapng",
    "run.log",
    "similarity.json",
}
_STAGE_ORDER = ("preflight", "capture", "fit", "generate", "compare")
_FailureStage = Literal["capture", "fit", "generate", "compare"]


class _TargetRecordingDocker(EndpointDockerCompose):
    """Record real target starts while retaining the endpoint overlay adapter."""

    def __init__(self, original: EndpointDockerCompose) -> None:
        super().__init__(original.tracker)
        self.started_target_projects: list[str] = []

    def start_target(
        self,
        compose_path: Path,
        project_name: str,
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> CommandResult:
        self.started_target_projects.append(project_name)
        return super().start_target(compose_path, project_name, timeout=timeout, deadline=deadline)


def _real_dependencies(
    docker: EndpointDockerCompose,
    *,
    fit: Callable[[Path], FitStageResult] = fit_experiment,
    generate: Callable[[Path], GenerationStageResult] = generate_experiment,
    compare: Callable[[Path], ComparisonResult] = compare_experiment,
) -> RunDependencies:
    def preflight(path: Path) -> PreparedExperiment:
        return run_preflight(path, config_only=False, docker=docker)

    def capture(path: Path, prepared: PreparedExperiment) -> CaptureResult:
        return capture_prepared_experiment(path, prepared, docker=docker)

    return RunDependencies(preflight, capture, fit, generate, compare)


def _artifact_bytes(run_directory: Path, names: tuple[str, ...]) -> dict[str, bytes]:
    return {name: (run_directory / name).read_bytes() for name in names}


def _install_unready_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real container alive without creating the capture readiness files."""
    original_write = capture_module.write_production_compose

    def write(
        path: Path,
        config: ExperimentConfig,
        paths: ComposePaths,
        *,
        target_image: str,
        capture_image: str,
    ) -> None:
        original_write(
            path,
            config,
            paths,
            target_image=target_image,
            capture_image=capture_image,
        )
        document = cast(dict[str, object], json.loads(path.read_bytes()))
        services = cast(dict[str, object], document["services"])
        capture = cast(dict[str, object], services["capture"])
        capture["entrypoint"] = ["/bin/sh", "-c", "sleep 300"]
        path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    monkeypatch.setattr(capture_module, "write_production_compose", write)


def _assert_trial_window(trials: tuple[TrialResult, ...], window: float) -> None:
    for trial in trials:
        for method in trial.methods:
            assert method.diagnostics["observation_window_seconds"] == window


def test_cli_complete_run_publishes_strict_nine_file_result_and_cleans_every_project(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any synthetic stage, seed/limit drift, extra artifact, or Docker leak must break the complete-run contract."""
    experiment = write_run_docker_experiment(
        tmp_path / "complete.toml",
        valid_config_data,
        docker_test_environment,
    )
    config = load_experiment(experiment)
    real_get_family = generation_module.get_family
    real_strategy_get_family = strategy_module.get_family
    trial_generation_calls: list[tuple[str, int, float, GenerationLimits]] = []
    final_generation_calls: list[tuple[int, float, GenerationLimits]] = []
    observe_trial_generation = False

    def observed_strategy_get_family(name: str) -> ModelFamily:
        family = real_strategy_get_family(name)
        original_generate = family.generate

        def observed_generate(
            _self: object,
            model: FittedModel,
            seed: int,
            window: float,
            limits: GenerationLimits,
            *,
            clock: Callable[[], float] = monotonic,
        ) -> GenerationResult:
            if observe_trial_generation:
                trial_generation_calls.append((family.name, seed, window, limits))
            return original_generate(model, seed, window, limits, clock=clock)

        monkeypatch.setattr(type(family), "generate", observed_generate)
        return family

    def observed_get_family(name: str) -> ModelFamily:
        family = real_get_family(name)

        def observed_generate(
            model: FittedModel,
            seed: int,
            window: float,
            limits: GenerationLimits,
            *,
            clock: Callable[[], float],
        ) -> GenerationResult:
            final_generation_calls.append((seed, window, limits))
            return family.generate(model, seed, window, limits, clock=clock)

        return cast(
            ModelFamily,
            SimpleNamespace(name=family.name, gene_names=family.gene_names, generate=observed_generate),
        )

    monkeypatch.setattr(strategy_module, "get_family", observed_strategy_get_family)
    monkeypatch.setattr(generation_module, "get_family", observed_get_family)
    docker = _TargetRecordingDocker(endpoint_docker)

    def observed_fit(path: Path) -> FitStageResult:
        nonlocal observe_trial_generation
        observe_trial_generation = True
        try:
            return fit_experiment(path)
        finally:
            observe_trial_generation = False

    dependencies = _real_dependencies(docker, fit=observed_fit)
    completed: list[RunResult] = []

    def run_from_cli(path: Path) -> RunResult:
        result = run_experiment(path, dependencies=dependencies)
        completed.append(result)
        return result

    status = main(["run", str(experiment)], run=run_from_cli)

    assert status == 0
    assert len(completed) == 1
    result = completed[0]
    run_directory = result.run_directory
    assert capsys.readouterr() == (
        f"run: family={result.fit.outcome.winner.family} fitness={result.fit.outcome.winner.fitness:.6f} "
        f"reference_packets={result.capture.packet_count} generated_packets={len(result.generation.events)} "
        f"aggregate_score={result.comparison.aggregate_score:.6f} output={run_directory}\n",
        "",
    )
    assert {path.name for path in run_directory.iterdir()} == _ORDINARY_RUN_FILES
    assert all(
        "diagnostic" not in path.name and ".tmp" not in path.name and "quarantine" not in path.name
        for path in run_directory.iterdir()
    )

    reopened = open_or_prepare_experiment(experiment)
    assert reopened.config == config
    capture_content = (run_directory / "capture.json").read_bytes()
    reference_content = (run_directory / "reference.pcapng").read_bytes()
    inspection = validate_capture_pair(
        run_directory / "capture.json",
        run_directory / "reference.pcapng",
        deadline=None,
    )
    assert inspection.packet_count == result.capture.packet_count > 0
    assert inspection.direction_counts[Direction.OUTBOUND] > 0
    assert inspection.direction_counts[Direction.INBOUND] > 0

    metadata = parse_capture_metadata(capture_content, source=run_directory / "capture.json")
    parsed_reference = parse_pcapng_bytes(
        reference_content,
        metadata,
        source=run_directory / "reference.pcapng",
    )
    reference, window = normalize_reference(parsed_reference)
    context = make_strategy_context(
        config,
        reference,
        window,
        run_directory,
        experiment_sha256=sha256_bytes((run_directory / "experiment.toml").read_bytes()),
        reference_sha256=sha256_bytes(reference_content),
        capture_sha256=sha256_bytes(capture_content),
    )
    checkpoint = load_checkpoint(run_directory / "checkpoint.json", context.compatibility)
    assert render_history_csv(checkpoint) == (run_directory / "ga_history.csv").read_bytes()
    expected_families = {"poisson_empirical", "markov_renewal", "mmpp"}
    assert {family.name for family in checkpoint.compatibility.families} == expected_families
    assert {candidate.family for candidate in checkpoint.population} == expected_families
    assert len(checkpoint.population) == config.genetic.population_size == 6
    assert checkpoint.generation == config.genetic.generation_count == 0
    assert all(
        tuple(trial.seed for trial in candidate.trials) == config.genetic.trial_seeds
        for candidate in checkpoint.population
        if candidate.status == "valid"
    )
    for candidate in checkpoint.population:
        _assert_trial_window(candidate.trials, window)
    assert trial_generation_calls
    assert all(
        call_window == window and limits == config.generation.trial
        for _family, _seed, call_window, limits in trial_generation_calls
    )
    assert {seed for _family, seed, _window, _limits in trial_generation_calls} == {
        *config.genetic.trial_seeds,
        config.run.final_seed,
    }
    assert {
        family for family, seed, _window, _limits in trial_generation_calls if seed in config.genetic.trial_seeds
    } == expected_families

    best_content = (run_directory / "best_model.json").read_bytes()
    best = load_best_model(best_content, source=run_directory / "best_model.json")
    assert render_best_model(best) == best_content
    assert best == result.fit.best_model
    assert tuple(trial.seed for trial in result.fit.outcome.final_trials) == (config.run.final_seed,)
    _assert_trial_window(result.fit.outcome.final_trials, window)
    assert [family for family, seed, _window, _limits in trial_generation_calls if seed == config.run.final_seed] == [
        result.fit.outcome.winner.family
    ]

    generated_content = (run_directory / "generated.pcapng").read_bytes()
    generated_events = parse_pcapng_bytes(
        generated_content,
        metadata,
        source=run_directory / "generated.pcapng",
    )
    assert generated_events == result.generation.events
    assert final_generation_calls == [(config.run.final_seed, window, config.generation.final)]

    comparison_content = (run_directory / "similarity.json").read_bytes()
    comparison = load_comparison_result(run_directory / "similarity.json")
    assert render_comparison_result(comparison) == comparison_content
    assert comparison == result.comparison
    assert comparison.input_sha256 == {
        "capture_json": sha256_bytes(capture_content),
        "generated_pcapng": sha256_bytes(generated_content),
        "reference_pcapng": sha256_bytes(reference_content),
        "similarity_settings": similarity_settings_sha256(config.similarity),
    }
    assert (
        window
        == checkpoint.compatibility.observation_window_seconds
        == best.observation_window_seconds
        == result.fit.observation_window_seconds
        == result.generation.observation_window_seconds
        == comparison.observation_window_seconds
    )
    assert all(method.diagnostics["observation_window_seconds"] == window for method in comparison.methods.values())

    records = capture_log(run_directory)
    run_completed = [record for record in records if record.get("event") == "run_completed"]
    assert run_completed == [
        {
            "aggregate_score": result.comparison.aggregate_score,
            "event": "run_completed",
            "family": result.fit.outcome.winner.family,
            "fitness": result.fit.outcome.winner.fitness,
            "generated_packet_count": len(result.generation.events),
            "reference_packet_count": result.capture.packet_count,
            "run_directory": str(run_directory),
            "stage": "run",
        }
    ]
    assert records[-1] == run_completed[0]
    assert len(docker.tracker.projects) == 2
    assert_tracked_projects_clean(docker.tracker)


def test_real_full_preflight_probe_failure_is_primary_and_cleans_its_unique_project(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed real network probe must neither leak its orphan nor become a later coordinator-stage error."""
    experiment = write_run_docker_experiment(
        tmp_path / "probe-failure.toml",
        valid_config_data,
        docker_test_environment,
        probe_url="http://endpoint:1/",
    )
    calls: list[str] = []

    def preflight(path: Path) -> PreparedExperiment:
        calls.append("preflight")
        return run_preflight(path, config_only=False, docker=endpoint_docker)

    def forbidden_capture(_path: Path, _prepared: PreparedExperiment) -> CaptureResult:
        calls.append("capture")
        raise AssertionError("capture ran after a failed full-preflight probe")

    def forbidden_coordinator_log(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("coordinator accessed run.log after preflight failure")

    dependencies = RunDependencies(
        preflight, forbidden_capture, fit_experiment, generate_experiment, compare_experiment
    )
    monkeypatch.setattr(run_module, "_append_run_failure", forbidden_coordinator_log)
    propagated: list[TrafficlabError] = []

    def run_from_cli(path: Path) -> RunResult:
        try:
            return run_experiment(path, dependencies=dependencies)
        except TrafficlabError as error:
            propagated.append(error)
            raise

    status = main(["run", str(experiment)], run=run_from_cli)

    assert len(propagated) == 1
    primary = propagated[0]
    assert "network_probe: network probe target exited with status" in str(primary)
    assert primary.corrective_action == "verify DNS and the configured probe endpoint are reachable from Docker"
    assert status == primary.exit_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"run: {primary}; {primary.corrective_action}\n"
    assert calls == ["preflight"]
    assert len(endpoint_docker.tracker.projects) == 1
    project_name = next(iter(endpoint_docker.tracker.projects))
    assert project_name.startswith("trafficlab-preflight-")
    assert_tracked_projects_clean(endpoint_docker.tracker)


@pytest.mark.parametrize("failed_stage", ["capture", "fit", "generate", "compare"])
def test_post_preflight_stage_failure_preserves_prior_artifacts_stops_and_cleans_every_project(
    failed_stage: _FailureStage,
    valid_config_data: dict[str, object],
    tmp_path: Path,
    docker_test_environment: DockerTestEnvironment,
    endpoint_docker: EndpointDockerCompose,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Replacing a primary error, rolling back evidence, running later work, or leaking Docker must fail."""
    experiment = write_run_docker_experiment(
        tmp_path / f"{failed_stage}-failure.toml",
        valid_config_data,
        docker_test_environment,
        readiness_timeout=0.5 if failed_stage == "capture" else 5.0,
    )
    run_directory = tmp_path / f"{failed_stage}-failure-run"
    docker = _TargetRecordingDocker(endpoint_docker)
    if failed_stage == "capture":
        _install_unready_capture(monkeypatch)

    calls: list[str] = []
    primary_errors: list[TrafficlabError] = []
    preserved_at_failure: dict[str, bytes] = {}
    injected = TrafficlabError(
        f"injected {failed_stage} Docker-boundary failure",
        corrective_action=f"repair {failed_stage} boundary",
        exit_code=37,
    )

    def preflight(path: Path) -> PreparedExperiment:
        calls.append("preflight")
        return run_preflight(path, config_only=False, docker=docker)

    def capture(path: Path, prepared: PreparedExperiment) -> CaptureResult:
        calls.append("capture")
        if failed_stage == "capture":
            preserved_at_failure.update(_artifact_bytes(run_directory, ("experiment.toml",)))
        try:
            return capture_prepared_experiment(path, prepared, docker=docker)
        except TrafficlabError as error:
            primary_errors.append(error)
            raise

    def fit(path: Path) -> FitStageResult:
        calls.append("fit")
        if failed_stage == "fit":
            preserved_at_failure.update(
                _artifact_bytes(run_directory, ("experiment.toml", "capture.json", "reference.pcapng"))
            )
            primary_errors.append(injected)
            raise injected
        return fit_experiment(path)

    def generate(path: Path) -> GenerationStageResult:
        calls.append("generate")
        if failed_stage == "generate":
            preserved_at_failure.update(
                _artifact_bytes(
                    run_directory,
                    (
                        "experiment.toml",
                        "capture.json",
                        "reference.pcapng",
                        "checkpoint.json",
                        "ga_history.csv",
                        "best_model.json",
                    ),
                )
            )
            primary_errors.append(injected)
            raise injected
        return generate_experiment(path)

    def compare(path: Path) -> ComparisonResult:
        calls.append("compare")
        if failed_stage == "compare":
            preserved_at_failure.update(
                _artifact_bytes(
                    run_directory,
                    (
                        "experiment.toml",
                        "capture.json",
                        "reference.pcapng",
                        "checkpoint.json",
                        "ga_history.csv",
                        "best_model.json",
                        "generated.pcapng",
                    ),
                )
            )
            primary_errors.append(injected)
            raise injected
        return compare_experiment(path)

    dependencies = RunDependencies(preflight, capture, fit, generate, compare)
    propagated: list[TrafficlabError] = []

    def run_from_cli(path: Path) -> RunResult:
        try:
            return run_experiment(path, dependencies=dependencies)
        except TrafficlabError as error:
            propagated.append(error)
            raise

    status = main(["run", str(experiment)], run=run_from_cli)

    assert len(propagated) == 1
    primary = propagated[0]
    assert len(primary_errors) == 1
    assert primary is primary_errors[0]
    assert status == primary.exit_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"run: {primary}; {primary.corrective_action}\n"
    if failed_stage == "capture":
        assert "capture readiness timed out" in str(primary)
        project_name = capture_project_name(run_directory)
        assert project_name not in docker.started_target_projects
    else:
        assert primary is injected
        assert primary.exit_code == 37
    expected_calls = list(_STAGE_ORDER[: _STAGE_ORDER.index(failed_stage) + 1])
    assert calls == expected_calls
    assert _artifact_bytes(run_directory, tuple(preserved_at_failure)) == preserved_at_failure

    downstream = {
        "capture": (
            "capture.json",
            "reference.pcapng",
            "checkpoint.json",
            "ga_history.csv",
            "best_model.json",
            "generated.pcapng",
            "similarity.json",
        ),
        "fit": ("checkpoint.json", "ga_history.csv", "best_model.json", "generated.pcapng", "similarity.json"),
        "generate": ("generated.pcapng", "similarity.json"),
        "compare": ("similarity.json",),
    }
    assert all(not (run_directory / name).exists() for name in downstream[failed_stage])
    expected_primary_by_stage: dict[_FailureStage, dict[str, object]] = {
        "capture": {
            "affected_evidence": "capture pair",
            "authority": "primary",
            "corrective_action": "correct timeout or workload",
            "detail": "capture readiness timed out",
            "evidence_state": "diagnostic_only",
            "kind": "stage_timeout",
            "stage": "capture",
        },
        "fit": {
            "affected_evidence": "fit inputs",
            "authority": "primary",
            "corrective_action": "repair fit boundary",
            "detail": "injected fit Docker-boundary failure",
            "evidence_state": "preserved",
            "kind": "artifact_corrupt",
            "stage": "fit",
        },
        "generate": {
            "affected_evidence": "generated.pcapng",
            "authority": "primary",
            "corrective_action": "repair generate boundary",
            "detail": "injected generate Docker-boundary failure",
            "evidence_state": "not_published",
            "kind": "generation_incomplete",
            "stage": "generate",
        },
        "compare": {
            "affected_evidence": "similarity.json",
            "authority": "primary",
            "corrective_action": "repair compare boundary",
            "detail": "injected compare Docker-boundary failure",
            "evidence_state": "not_published",
            "kind": "metric_infeasible",
            "stage": "compare",
        },
    }
    expected_primary = expected_primary_by_stage[failed_stage]
    expected_secondaries: list[dict[str, object]] = []
    if failed_stage == "capture":
        primary_detail, separator, inspection_detail = str(primary).partition("; secondary: ")
        assert primary_detail == "capture readiness timed out"
        assert separator == "; secondary: "
        assert inspection_detail.startswith(
            "could not inspect capture readiness state: Docker command timed out after "
        )
        expected_secondaries = [
            {
                "affected_evidence": "capture pair",
                "authority": "secondary",
                "corrective_action": "correct the capture producer",
                "detail": inspection_detail,
                "evidence_state": "diagnostic_only",
                "kind": "capture_malformed",
                "stage": "capture",
            }
        ]
    expected_outcomes = [expected_primary, *expected_secondaries]
    assert [outcome.as_dict() for outcome in primary.failure_outcomes] == expected_outcomes

    records = capture_log(run_directory)
    coordinator_failures = [record for record in records if record.get("event") == "run_failed"]
    expected_coordinator_failure: dict[str, object] = {
        "completed_stages": list(_STAGE_ORDER[: _STAGE_ORDER.index(failed_stage)]),
        "corrective_action": primary.corrective_action,
        "detail": str(primary),
        "event": "run_failed",
        "failed_stage": failed_stage,
        "failure_outcome": expected_primary,
        "stage": "run",
    }
    if expected_secondaries:
        expected_coordinator_failure["secondary_outcomes"] = expected_secondaries
    assert coordinator_failures == [expected_coordinator_failure]
    assert all(record.get("event") != "run_completed" for record in records)
    assert len(docker.tracker.projects) == 2
    assert_tracked_projects_clean(docker.tracker)
