import copy
import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import trafficlab.capture.failures as capture_failures
import trafficlab.capture.lineage as capture_lineage
import trafficlab.capture.stage as capture
import trafficlab.comparison.publication as comparison
import trafficlab.comparison.stage as comparison_stage
import trafficlab.fitting.genetic.strategy as strategy_module
import trafficlab.fitting.stage as fitting
import trafficlab.generation.stage as generation
import trafficlab.preflight.stage as preflight
import trafficlab.preflight.types as preflight_types
import trafficlab.study_evidence.publication as study_evidence
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from tests.support.failure_matrix.cases import BoundaryCase
from tests.support.failure_matrix.doubles import CaptureDocker, PreflightClock, PreflightDocker
from tests.support.failure_matrix.oracle import (
    CAPTURE_DIAGNOSTIC_SCENARIOS,
    TreeValue,
    assert_adverse_inventory_unchanged,
    assert_failure_log_suffix,
    assert_log_unchanged,
    expected_capture_log_records,
    expected_fit_log_records,
    expected_generation_log_records,
    expected_preflight_log_records,
    log_snapshot,
    scientific_inventory,
    temporary_residue,
    tree_inventory,
)
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.common.config import ExperimentConfig, FloatBounds
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata
from trafficlab.fitting.genetic.strategy import FitOutcome, StrategyContext, run_strategy
from trafficlab.fitting.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.fitting.stage import FitDependencies
from trafficlab.generation.models.fitted_model import load_best_model, rebuild_best_model, render_best_model

REPOSITORY = Path(__file__).parents[3]

FIT_CHECKPOINT_FIXTURE = PIPELINE_FIXTURE_ROOT / "fit" / "checkpoint.json"

MODEL_FIXTURE = PIPELINE_FIXTURE_ROOT / "models" / "best_model.json"


def prepared_experiment(run_directory: Path) -> preflight_types.PreparedExperiment:
    config = cast(
        ExperimentConfig,
        SimpleNamespace(
            capture=SimpleNamespace(
                image="capture:test",
                total_timeout_seconds=5.0,
                readiness_timeout_seconds=1.0,
                workload_timeout_seconds=1.0,
                flush_timeout_seconds=1.0,
            ),
            target=SimpleNamespace(image="target:test", mounts=()),
            run=SimpleNamespace(directory=run_directory),
        ),
    )
    return preflight_types.PreparedExperiment(
        source=run_directory.parent / "experiment.toml",
        portable_config=config,
        config=config,
        report=preflight_types.PreflightReport(
            config=config,
            findings=(),
            environment_identity=preflight_types.CaptureEnvironmentIdentity(
                host_architecture="linux/amd64",
                target_reference="target:test",
                target_content_id="sha256:" + ("c" * 64),
                capture_reference="capture:test",
                capture_content_id="sha256:" + ("d" * 64),
                capture_tool_version="4.0.17",
            ),
        ),
        run_directory=run_directory,
    )


def run_preflight_case(
    case: BoundaryCase,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    run_directory = tmp_path / "run"
    experiment_path = tmp_path / "experiment.toml"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    target = cast(dict[str, object], data["target"])
    target["mounts"] = []
    if case.scenario == "target_image_unavailable":
        target["image"] = "example.invalid/app"
    mount_source: Path | None = None
    if case.scenario in {"mount_source_unavailable", "mount_target_incompatible"}:
        mount_source = tmp_path / "fixture-data"
        mount_source.write_bytes(b"fixture")
        target["mounts"] = [{"source": str(mount_source), "target": "/work/data", "read_only": True}]
    config = ExperimentConfig.model_validate(data)
    content = render_effective_config(config)
    if case.scenario == "config_invalid":
        content = re.sub(rb"argv = \[[\s\S]*?\]", b"argv = []", content, count=1)
    experiment_path.write_bytes(content)
    docker = PreflightDocker(case.scenario, mount_source)
    clock = PreflightClock(case.scenario)
    if case.scenario != "config_invalid":
        preflight.run_preflight(
            experiment_path,
            config_only=True,
            docker=cast(preflight_types.DockerPreflight, docker),
            clock=clock,
        )
    source_before = experiment_path.read_bytes()
    inventory_before = scientific_inventory(run_directory)
    log_before = log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            experiment_path,
            config_only=case.scenario == "config_invalid",
            docker=cast(preflight_types.DockerPreflight, docker),
            clock=clock,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    if case.scenario == "config_invalid":
        assert_log_unchanged(run_directory, log_before)
    else:
        assert_failure_log_suffix(
            run_directory,
            log_before,
            expected_records=expected_preflight_log_records(case, config, docker),
        )
    assert experiment_path.read_bytes() == source_before
    assert_adverse_inventory_unchanged(run_directory, inventory_before)


def render_snapshot(_config: object) -> bytes:
    return b"canonical snapshot"


def capture_prepared(
    valid_config_data: dict[str, object], tmp_path: Path
) -> tuple[Path, preflight_types.PreparedExperiment]:
    run_directory = tmp_path / "run"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = []
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    environment = preflight_types.CaptureEnvironmentIdentity(
        host_architecture="linux/amd64",
        target_reference=prepared.config.target.image,
        target_content_id="sha256:" + ("c" * 64),
        capture_reference=prepared.config.capture.image,
        capture_content_id="sha256:" + ("d" * 64),
        capture_tool_version="4.0.17",
    )
    return run_directory, replace(prepared, report=replace(prepared.report, environment_identity=environment))


def run_capture_boundary_case(
    case: BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    run_directory, prepared = capture_prepared(valid_config_data, tmp_path)
    docker = CaptureDocker(case.scenario)
    inventory_before = scientific_inventory(run_directory)
    log_before = log_snapshot(run_directory)

    def fixed_deadline(_clock: Callable[[], float], _seconds: float, *, stage: str) -> float:
        if case.scenario == "target_23_capture_42_total_timeout" and stage == "workload":
            return 20.0
        return 10.0 if stage in {"project creation", "total-run"} else 5.0

    monkeypatch.setattr(capture, "future_deadline", fixed_deadline)
    with pytest.raises(TrafficlabError) as caught:
        capture.capture_prepared_experiment(
            prepared.source,
            prepared,
            docker=cast(capture.CaptureDocker, docker),
            clock=docker.clock,
            interruption=lambda: case.scenario == "user_interrupt" and docker.target_started,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert_failure_log_suffix(
        run_directory,
        log_before,
        expected_records=expected_capture_log_records(case, docker),
    )
    expected_new: dict[str, TreeValue] = {}
    if case.scenario in CAPTURE_DIAGNOSTIC_SCENARIOS:
        assert docker.created_metadata is not None
        assert docker.created_reference is not None
        expected_new = {
            "diagnostic-capture.json": ("file", docker.created_metadata),
            "diagnostic-reference.pcapng": ("file", docker.created_reference),
        }
    assert_adverse_inventory_unchanged(run_directory, inventory_before, expected_new=expected_new)


def run_capture_stale_boundary_case(
    case: BoundaryCase,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Drive valid-pair lineage rejection through the public no-Docker capture boundary."""
    run_directory = tmp_path / "run"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = []
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    environment = preflight_types.CaptureEnvironmentIdentity(
        host_architecture="linux/amd64",
        target_reference=prepared.config.target.image,
        target_content_id="sha256:" + ("c" * 64),
        capture_reference=prepared.config.capture.image,
        capture_content_id="sha256:" + ("d" * 64),
        capture_tool_version="4.0.17",
    )
    prepared = replace(prepared, report=replace(prepared.report, environment_identity=environment))
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    metadata_content = render_capture_metadata(metadata)
    original_reference = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    metadata_path.write_bytes(metadata_content)
    reference_path.write_bytes(original_reference)
    capture_failures.append_event(
        run_directory,
        "capture_published",
        **capture_lineage.capture_lineage(run_directory, environment),
        packet_count=1,
        path=str(reference_path),
        project_name="matrix",
        reused=False,
    )
    log_before = log_snapshot(run_directory)
    changed_reference = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 65),), metadata)
    reference_path.write_bytes(changed_reference)
    inventory_before = scientific_inventory(run_directory)

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"stale public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture.capture_experiment(
            experiment_path,
            docker=cast(capture.CaptureDocker, NoDocker()),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert metadata_path.read_bytes() == metadata_content
    assert reference_path.read_bytes() == changed_reference
    assert_log_unchanged(run_directory, log_before)
    assert_adverse_inventory_unchanged(run_directory, inventory_before)


def run_capture_mounted_input_boundary_case(
    case: BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Drive mounted-input failure through public pair reuse without Docker."""
    run_directory = tmp_path / "run"
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    mounted_inputs = capture_lineage.identify_mounted_inputs(prepared.config)
    environment = preflight_types.CaptureEnvironmentIdentity(
        host_architecture="linux/amd64",
        target_reference=prepared.config.target.image,
        target_content_id="sha256:" + ("c" * 64),
        capture_reference=prepared.config.capture.image,
        capture_content_id="sha256:" + ("d" * 64),
        capture_tool_version="4.0.17",
        mounted_inputs=mounted_inputs,
    )
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    metadata_content = render_capture_metadata(metadata)
    reference_content = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    metadata_path.write_bytes(metadata_content)
    reference_path.write_bytes(reference_content)
    capture_failures.append_event(
        run_directory,
        "capture_published",
        **capture_lineage.capture_lineage(run_directory, environment),
        packet_count=1,
        path=str(reference_path),
        project_name="matrix",
        reused=False,
    )
    log_before = log_snapshot(run_directory)
    inventory_before = scientific_inventory(run_directory)
    mutation = "remove" if case.scenario == "mounted_input_unavailable" else "change"
    real_run_preflight = capture.run_preflight
    mutated = False

    def mutate_after_local_preflight(
        path: Path,
        *,
        config_only: bool,
        docker: preflight_types.DockerPreflight | None,
        clock: Callable[[], float],
    ) -> preflight_types.PreparedExperiment:
        nonlocal mutated
        result = real_run_preflight(
            path,
            config_only=config_only,
            docker=docker,
            clock=clock,
        )
        if config_only and not mutated:
            if mutation == "remove":
                mounted.unlink()
            else:
                mounted.write_bytes(b"request-v2")
            mutated = True
        return result

    monkeypatch.setattr(capture, "run_preflight", mutate_after_local_preflight)

    docker_calls: list[str] = []

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            docker_calls.append(name)
            raise AssertionError(f"mounted-input public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture.capture_experiment(
            experiment_path,
            docker=cast(capture.CaptureDocker, NoDocker()),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert caught.value.failure_outcome == case.primary
    assert metadata_path.read_bytes() == metadata_content
    assert reference_path.read_bytes() == reference_content
    assert_log_unchanged(run_directory, log_before)
    assert docker_calls == []
    assert {path.name for path in run_directory.iterdir()} == {
        "capture.json",
        "experiment.toml",
        "reference.pcapng",
        "run.log",
    }
    assert_adverse_inventory_unchanged(run_directory, inventory_before)


FIT_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")

FIT_REFERENCE = (
    TraceEvent(10.0, Direction.OUTBOUND, 64),
    TraceEvent(11.0, Direction.INBOUND, 128),
    TraceEvent(12.0, Direction.OUTBOUND, 256),
)


def fit_config(valid_config_data: dict[str, object], run_directory: Path) -> ExperimentConfig:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["poisson_empirical"]
    models["markov_renewal"] = None
    models["mmpp"] = None
    models["nhpp"] = None
    models["acd"] = None
    models["markov_packet_train"] = None
    models["packet_hmm"] = None
    genetic = cast(dict[str, object], data["genetic"])
    genetic.update(
        population_size=2,
        generation_count=0,
        tournament_size=2,
        elite_count=1,
        trial_seeds=[101],
        resume=True,
    )
    base = ExperimentConfig.model_validate(data)
    poisson = base.models.poisson_empirical
    assert poisson is not None
    return base.model_copy(
        update={
            "models": base.models.model_copy(
                update={
                    "poisson_empirical": poisson.model_copy(update={"c_lambda": FloatBounds(lower=20.0, upper=21.0)})
                }
            )
        }
    )


def fit_trial(seed: int) -> TrialResult:
    methods = tuple(MethodTrialResult(name=name, score=0.75, diagnostics={"literal": 0.75}) for name in METHOD_ORDER)
    return TrialResult(
        seed=seed,
        aggregate_score=0.75,
        methods=cast(
            tuple[
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
                MethodTrialResult,
            ],
            methods,
        ),
    )


def fit_success_outcome(config: ExperimentConfig) -> FitOutcome:
    winner = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="poisson_empirical",
        genes=(20.5,),
        status="valid",
        fitness=0.75,
        trials=(fit_trial(config.genetic.trial_seeds[0]),),
        invalid=None,
        duplicate_diagnostics=(),
    )
    from trafficlab.fitting.genetic.population import derive_family_priority

    return FitOutcome(
        winner,
        (fit_trial(config.run.final_seed),),
        0,
        "hard_limit",
        derive_family_priority(config.run.master_seed, config.models.enabled),
    )


def fit_inputs(config: ExperimentConfig) -> dict[Path, bytes]:
    run_directory = config.run.directory
    return {
        run_directory / "experiment.toml": render_effective_config(config),
        run_directory / "capture.json": render_capture_metadata(FIT_METADATA),
        run_directory / "reference.pcapng": encode_pcapng(FIT_REFERENCE, FIT_METADATA),
    }


def fit_dependencies(
    config: ExperimentConfig,
    experiment_path: Path,
    inputs: dict[Path, bytes],
    strategy: Callable[[StrategyContext], FitOutcome],
) -> FitDependencies:
    prepared = preflight_types.PreparedExperiment(
        experiment_path,
        config,
        preflight_types.PreflightReport(config, ()),
        config.run.directory,
    )
    return FitDependencies(lambda _path: prepared, lambda path: inputs[path], strategy)


def run_fit_boundary_case(
    case: BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Exercise real fit ownership from checkpoint and publisher source conditions."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    config = fit_config(valid_config_data, run_directory)
    inputs = fit_inputs(config)

    if case.scenario == "checkpoint_corrupt":
        checkpoint_path = run_directory / "checkpoint.json"
        checkpoint_path.write_bytes(b"{\n")

        def forbid_search_draws(*_args: object, **_kwargs: object) -> object:
            pytest.fail("malformed checkpoint bytes reached genetic search draws")

        monkeypatch.setattr(strategy_module, "initial_population", forbid_search_draws)
        dependencies = fit_dependencies(config, experiment_path, inputs, run_strategy)
    elif case.scenario == "checkpoint_schema":
        checkpoint_path = run_directory / "checkpoint.json"
        document = cast(dict[str, object], json.loads(FIT_CHECKPOINT_FIXTURE.read_bytes()))
        document["scientific_artifact_schema"] = 1
        incompatible = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        checkpoint_path.write_bytes(incompatible)

        def forbid_search_draws(*_args: object, **_kwargs: object) -> object:
            pytest.fail("incompatible checkpoint schema reached genetic search draws")

        monkeypatch.setattr(strategy_module, "initial_population", forbid_search_draws)
        dependencies = fit_dependencies(config, experiment_path, inputs, run_strategy)
    elif case.scenario == "best_model_collision":
        best_model_path = run_directory / "best_model.json"
        existing = load_best_model(MODEL_FIXTURE.read_bytes(), source=MODEL_FIXTURE)
        existing_best_model = render_best_model(rebuild_best_model(existing, final_seed=existing.final_seed + 1))
        best_model_path.write_bytes(existing_best_model)

        dependencies = fit_dependencies(
            config,
            experiment_path,
            inputs,
            lambda _context: fit_success_outcome(config),
        )
    elif case.scenario == "reference_changed":
        reference_path = run_directory / "reference.pcapng"
        original_reference = inputs[reference_path]
        changed_reference = original_reference + b"changed after fitting\n"
        reference_path.write_bytes(original_reference)
        reads = 0

        def read_bytes(path: Path) -> bytes:
            nonlocal reads
            if path == reference_path:
                reads += 1
                return original_reference if reads == 1 else changed_reference
            return inputs[path]

        prepared = preflight_types.PreparedExperiment(
            experiment_path,
            config,
            preflight_types.PreflightReport(config, ()),
            run_directory,
        )
        dependencies = FitDependencies(
            lambda _path: prepared,
            read_bytes,
            lambda _context: fit_success_outcome(config),
        )
    else:
        raise AssertionError(f"unsupported primitive fit scenario {case.scenario!r}")
    inventory_before = scientific_inventory(run_directory)
    log_before = log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        fitting.fit_experiment(experiment_path, dependencies=dependencies)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert_failure_log_suffix(
        run_directory,
        log_before,
        expected_records=expected_fit_log_records(case, experiment_path, config),
    )
    assert_adverse_inventory_unchanged(run_directory, inventory_before)


def run_generation_boundary_case(
    case: BoundaryCase,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Exercise missing, incompatible, and limited generation through real files and models."""
    run_directory = tmp_path / "run"
    experiment_path = tmp_path / "experiment.toml"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = []
    config = ExperimentConfig.model_validate(data)
    best_content = MODEL_FIXTURE.read_bytes()
    if case.scenario == "packet_limit":
        best = load_best_model(best_content, source=MODEL_FIXTURE)
        limits = best.final_limits.model_copy(update={"max_packets": 1})
        best_content = render_best_model(rebuild_best_model(best, final_limits=limits))
        trial_limits = config.generation.trial.model_copy(update={"max_packets": 1})
        config = config.model_copy(
            update={"generation": config.generation.model_copy(update={"trial": trial_limits, "final": limits})}
        )
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    capture_content = (REPOSITORY / "examples" / "data" / "capture.json").read_bytes()
    (run_directory / "capture.json").write_bytes(capture_content)
    if case.scenario == "best_model_schema":
        document = cast(dict[str, object], json.loads(best_content))
        document["scientific_artifact_schema"] = 1
        best_content = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if case.scenario != "best_model_missing":
        model_path = run_directory / "best_model.json"
        model_path.write_bytes(best_content)
    inventory_before = scientific_inventory(run_directory)
    log_before = log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(prepared.source, clock=lambda: 0.0)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert_failure_log_suffix(
        run_directory,
        log_before,
        expected_records=expected_generation_log_records(case),
    )
    assert_adverse_inventory_unchanged(run_directory, inventory_before)


def run_comparison_boundary_case(
    case: BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Exercise public comparison mapping from evaluation and publication sources."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    if case.scenario == "metric_infeasible":
        similarity = cast(dict[str, object], data["similarity"])
        similarity["acf_lags"] = [100]
        similarity["acf_lag_weights"] = [1.0]
    config = ExperimentConfig.model_validate(data)
    snapshot = render_effective_config(config)
    experiment_path.write_bytes(snapshot)
    (run_directory / "experiment.toml").write_bytes(snapshot)
    example_data = REPOSITORY / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
    ):
        destination.write_bytes(source.read_bytes())
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(comparison_stage, "append_run_log", append)
    if case.scenario == "foreign_generated":
        generated_path = run_directory / "generated.pcapng"
        foreign_generated = (run_directory / "reference.pcapng").read_bytes()
        generated_path.write_bytes(foreign_generated)
    elif case.scenario == "metric_infeasible":
        pass
    elif case.scenario == "similarity_durability":

        def fail_fsync(_file_descriptor: int) -> None:
            raise OSError("injected similarity fsync failure")

        monkeypatch.setattr(comparison.os, "fsync", fail_fsync)
    else:
        raise AssertionError(f"unsupported primitive comparison scenario {case.scenario!r}")
    inventory_before = scientific_inventory(run_directory)
    log_before = log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    detail = (
        f"could not publish similarity artifact {run_directory / 'similarity.json'}: injected similarity fsync failure"
        if case.scenario == "similarity_durability"
        else case.primary.detail
    )
    expected_record: dict[str, object] = {
        "detail": detail,
        "event": "comparison_failed",
        "failure_kind": "publication" if case.scenario == "similarity_durability" else "evaluation_or_input",
        "failure_outcome": case.primary.as_dict(),
        "stage": "compare",
    }
    if case.outcomes[1:]:
        expected_record["secondary_outcomes"] = [outcome.as_dict() for outcome in case.outcomes[1:]]
    assert records == [expected_record]
    assert_log_unchanged(run_directory, log_before)
    assert_adverse_inventory_unchanged(run_directory, inventory_before)


def run_study_publication_case(case: BoundaryCase, tmp_path: Path) -> None:
    """Exercise exclusive accepted-bundle publication from an occupied destination."""
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_bytes(b'{"files":[]}\n')
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    destination.mkdir(parents=True)
    retained = destination / "retained.txt"
    retained.write_bytes(b"accepted evidence\n")
    candidate_before = tree_inventory(candidate)
    evidence_before = tree_inventory(evidence_root)
    log_before = log_snapshot(candidate)

    with pytest.raises(TrafficlabError) as caught:
        study_evidence.publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert_log_unchanged(candidate, log_before)
    assert tree_inventory(candidate) == candidate_before
    assert tree_inventory(evidence_root) == evidence_before
    assert tuple(evidence_root.glob(".study-1.*.tmp")) == ()
    assert temporary_residue(evidence_root) == ()
