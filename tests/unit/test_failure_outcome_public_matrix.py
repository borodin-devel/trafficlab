"""Public-boundary coverage for the canonical expected-failure matrix.

The matrix uses the checked JSONL rows as its expected values.  Artifact-identity
and study-acceptance detections scheduled for Tasks 4 and 11 are injected as
typed errors at the nearest current public stage callback: this test verifies
their downstream serialization and publication state, not a detector that is
deliberately outside the current synthetic fixture harness.
"""

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

import trafficlab.capture as capture
import trafficlab.comparison as comparison
import trafficlab.fitting as fitting
import trafficlab.generation as generation
import trafficlab.genetic.strategy as strategy_module
import trafficlab.preflight as preflight
import trafficlab.run as run
import trafficlab.study_evidence as study_evidence
from trafficlab.capture import CaptureResult
from trafficlab.capture_policy import CaptureFailureOrigin, CaptureOutcome, FailureDetail, FailureKind
from trafficlab.comparison import ComparisonResult
from trafficlab.config import ExperimentConfig, FloatBounds
from trafficlab.config_io import render_effective_config
from trafficlab.docker_cli import ServiceState
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.fitting import FitDependencies, FitStageResult
from trafficlab.generation import GenerationStageResult
from trafficlab.genetic.checkpoint import CheckpointCorruptionError
from trafficlab.genetic.strategy import FitOutcome, StrategyContext, run_strategy
from trafficlab.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.pcapng import encode_pcapng
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "diagnostics" / "failure-outcomes.jsonl"

type _InjectionStage = Literal["capture", "fit", "generate", "compare", "publication"]

# These rows belong to intentionally deferred Task 4/11 detectors.  They are
# the only matrix cases allowed to inject an already-rendered outcome; every
# other row must begin as a primitive source failure at its boundary harness.
_DEFERRED_TYPED_DETECTOR_CASES = frozenset(
    {
        ("capture", "artifact_stale"),
        ("fit", "artifact_changed"),
        ("fit", "scientific_semantics_incompatible"),
        ("generate", "scientific_semantics_incompatible"),
        ("compare", "artifact_foreign"),
    }
)
_PREFLIGHT_FINDING_NAMES = {
    "Docker Engine is unavailable": "docker_engine",
    "Docker Compose version is incompatible": "docker_compose",
    "target image example.invalid/app is unavailable": "target_image",
    "capture image identity is incompatible": "capture_image",
    "dumpcap is unavailable": "capture_tool",
    "dumpcap version is incompatible": "capture_tool",
    "mount source fixture-data is unavailable": "mounts",
    "mount target /work/data is incompatible": "mounts",
    "mounted input request.txt is unavailable": "mounts",
    "mounted input request.txt is incompatible": "mounts",
    "capture prerequisite is unavailable": "network_probe",
    "capture prerequisite is incompatible": "network_probe",
}
_STAGE_OUTPUT_NAMES: dict[str, tuple[str, ...]] = {
    "preflight": ("capture.json", "reference.pcapng"),
    "capture": ("capture.json", "reference.pcapng"),
    "fit": ("best_model.json",),
    "generate": ("generated.pcapng",),
    "compare": ("similarity.json",),
    "publication": ("accepted-evidence-bundle.json",),
}
_PRESERVED_EVIDENCE_NAMES: dict[str, tuple[str, ...]] = {
    "best_model.json": ("best_model.json",),
    "capture pair": ("capture.json", "reference.pcapng"),
    "checkpoint.json": ("checkpoint.json",),
    "generated.pcapng": ("generated.pcapng",),
    "reference.pcapng": ("reference.pcapng",),
}


@dataclass(frozen=True, slots=True)
class _BoundaryCase:
    """One primary outcome and all of its ordered fixture-defined secondaries."""

    outcomes: tuple[FailureOutcome, ...]
    injection_stage: _InjectionStage | Literal["preflight"]
    deferred_typed_detector: bool

    @property
    def primary(self) -> FailureOutcome:
        return self.outcomes[0]

    @property
    def identifier(self) -> str:
        route = "typed-task4-11" if self.deferred_typed_detector else "primitive-boundary"
        return f"{route}-{self.primary.stage}-{self.primary.kind}-{self.primary.detail}"


def _fixture_outcomes() -> tuple[FailureOutcome, ...]:
    return tuple(
        FailureOutcome.from_json(line)
        for line in _FIXTURE.read_text(encoding="utf-8").splitlines()
        if line
    )


def _build_boundary_cases() -> tuple[_BoundaryCase, ...]:
    cases: list[_BoundaryCase] = []
    active: list[FailureOutcome] = []
    for outcome in _fixture_outcomes():
        if outcome.authority == "primary":
            if active:
                cases.append(_boundary_case(tuple(active)))
            active = [outcome]
        else:
            if not active:
                raise AssertionError("a fixture secondary outcome must follow a primary outcome")
            active.append(outcome)
    if active:
        cases.append(_boundary_case(tuple(active)))
    return tuple(cases)


def _boundary_case(outcomes: tuple[FailureOutcome, ...]) -> _BoundaryCase:
    primary = outcomes[0]
    if primary.stage == "preflight":
        injection_stage: _InjectionStage | Literal["preflight"] = "preflight"
    else:
        injection_stage = cast(_InjectionStage, primary.stage)
    return _BoundaryCase(
        outcomes=outcomes,
        injection_stage=injection_stage,
        deferred_typed_detector=(primary.stage, primary.kind) in _DEFERRED_TYPED_DETECTOR_CASES,
    )


_PUBLIC_BOUNDARY_CASES = _build_boundary_cases()


def _prepared(run_directory: Path) -> preflight.PreparedExperiment:
    config = cast(
        ExperimentConfig,
        SimpleNamespace(
            capture=SimpleNamespace(
                total_timeout_seconds=5.0,
                readiness_timeout_seconds=1.0,
                workload_timeout_seconds=1.0,
                flush_timeout_seconds=1.0,
            ),
            run=SimpleNamespace(directory=run_directory),
        ),
    )
    return preflight.PreparedExperiment(
        source=run_directory.parent / "experiment.toml",
        portable_config=config,
        config=config,
        report=preflight.PreflightReport(config=config, findings=()),
        run_directory=run_directory,
    )


def _preserved_paths(outcome: FailureOutcome, run_directory: Path) -> tuple[Path, ...]:
    names = _PRESERVED_EVIDENCE_NAMES.get(outcome.affected_evidence, ())
    if outcome.evidence_state == "preserved" and not names:
        raise AssertionError(f"missing preserved-artifact mapping for {outcome.affected_evidence!r}")
    return tuple(run_directory / name for name in names)


def _prepare_publication_state(case: _BoundaryCase, run_directory: Path) -> dict[Path, bytes]:
    primary = case.primary
    expected: dict[Path, bytes] = {}
    if primary.evidence_state == "preserved":
        for path in _preserved_paths(primary, run_directory):
            content = f"preserved fixture evidence: {path.name}\n".encode()
            path.write_bytes(content)
            expected[path] = content
    elif primary.evidence_state == "possibly_remaining":
        marker = run_directory / "inventory.marker"
        marker.write_text("owned inventory may remain\n", encoding="utf-8")
        expected[marker] = marker.read_bytes()
    return expected


def _assert_publication_state(
    case: _BoundaryCase, run_directory: Path, expected_preserved: dict[Path, bytes]
) -> None:
    primary = case.primary
    for path, expected in expected_preserved.items():
        assert path.read_bytes() == expected
    preserved = frozenset(expected_preserved)
    for output_name in _STAGE_OUTPUT_NAMES[primary.stage]:
        output_path = run_directory / output_name
        if output_path not in preserved:
            assert not output_path.exists()


def _assert_serialized_outcomes(record: dict[str, object], case: _BoundaryCase) -> None:
    assert record["failure_outcome"] == case.primary.as_dict()
    assert record.get("secondary_outcomes", []) == [outcome.as_dict() for outcome in case.outcomes[1:]]


def _run_preflight_case(case: _BoundaryCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    primary = case.primary
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = _prepared(run_directory)
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(preflight, "append_run_log", append)
    if primary.kind == "configuration_invalid":
        source_error = TrafficlabError(primary.detail, corrective_action=primary.corrective_action)

        def fail_open(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
            del writable
            raise source_error

        monkeypatch.setattr(preflight, "open_or_prepare_experiment", fail_open)
        with pytest.raises(TrafficlabError) as caught:
            preflight.run_preflight(tmp_path / "experiment.toml", config_only=True)
    else:
        finding_name = _PREFLIGHT_FINDING_NAMES[primary.detail]

        def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
            del writable
            return prepared

        def docker_report(
            _config: ExperimentConfig, _docker: object, *, deadline: float, clock: object
        ) -> preflight.PreflightReport:
            del _config, _docker, deadline, clock
            return preflight.PreflightReport(
                config=prepared.config,
                findings=(
                    preflight.PreflightFinding(
                        finding_name,
                        False,
                        primary.detail,
                        primary.corrective_action,
                    ),
                ),
            )

        monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)
        monkeypatch.setattr(preflight, "check_docker", docker_report)
        with pytest.raises(TrafficlabError) as caught:
            preflight.run_preflight(
                tmp_path / "experiment.toml",
                config_only=False,
                docker=cast(preflight.DockerPreflight, object()),
                clock=lambda: 100.0,
            )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    if records:
        _assert_serialized_outcomes(records[-1], case)
    _assert_publication_state(case, run_directory, {})


def _run_deferred_typed_coordinator_case(
    case: _BoundaryCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise the public coordinator while only replacing completed-stage contracts.

    The injected error is raised by the dependency matching its owner stage.
    This keeps the test focused on real ``run_experiment`` failure logging and
    artifact publication semantics rather than recreating unrelated fit and
    capture fixtures for every adverse-condition row.
    """
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = _prepared(run_directory)
    expected_preserved = _prepare_publication_state(case, run_directory)
    # Task 4/11 detector coverage is deliberately deferred.  Preserve the
    # fixture payload only here so the coordinator contract remains covered
    # without falsely claiming that a detector has already been implemented.
    error = TrafficlabError(
        case.primary.detail,
        corrective_action=case.primary.corrective_action,
        failure_outcomes=case.outcomes,
    )
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    def validate_noop(*_args: object) -> None:
        return None

    def validate_fit(*_args: object) -> tuple[float, bytes, dict[str, str]]:
        return (1.0, b"", {})

    def validate_generation(*_args: object) -> str:
        return "generated-input-sha256"

    def preflight_success(_path: Path) -> preflight.PreparedExperiment:
        return prepared

    def capture_success(_path: Path, _prepared: preflight.PreparedExperiment) -> CaptureResult:
        return cast(CaptureResult, object())

    def fit_success(_path: Path) -> FitStageResult:
        return cast(FitStageResult, object())

    def generation_success(_path: Path) -> GenerationStageResult:
        return cast(GenerationStageResult, object())

    def comparison_success(_path: Path) -> ComparisonResult:
        return cast(ComparisonResult, object())

    def capture_failure(_path: Path, _prepared: preflight.PreparedExperiment) -> CaptureResult:
        raise error

    def fit_failure(_path: Path) -> FitStageResult:
        raise error

    def generation_failure(_path: Path) -> GenerationStageResult:
        raise error

    def comparison_failure(_path: Path) -> ComparisonResult:
        raise error

    capture_stage: Callable[[Path, preflight.PreparedExperiment], CaptureResult] = (
        capture_failure if case.injection_stage == "capture" else capture_success
    )
    fit_stage: Callable[[Path], FitStageResult] = fit_failure if case.injection_stage == "fit" else fit_success
    generation_stage: Callable[[Path], GenerationStageResult] = (
        generation_failure if case.injection_stage == "generate" else generation_success
    )
    comparison_stage: Callable[[Path], ComparisonResult] = (
        comparison_failure if case.injection_stage == "compare" else comparison_success
    )

    monkeypatch.setattr(run, "append_run_log", append)
    monkeypatch.setattr(run, "_validate_preflight_result", validate_noop)
    monkeypatch.setattr(run, "_validate_capture_result", validate_noop)
    monkeypatch.setattr(run, "_validate_fit_result", validate_fit)
    monkeypatch.setattr(run, "_validate_generation_result", validate_generation)
    monkeypatch.setattr(run, "_validate_comparison_result", validate_noop)

    dependencies = run.RunDependencies(
        preflight=preflight_success,
        capture=capture_stage,
        fit=fit_stage,
        generate=generation_stage,
        compare=comparison_stage,
    )
    with pytest.raises(TrafficlabError) as caught:
        run.run_experiment(tmp_path / "experiment.toml", dependencies=dependencies)

    assert caught.value is error
    _assert_serialized_outcomes(records[-1], case)
    assert records[-1]["failed_stage"] == case.injection_stage
    _assert_publication_state(case, run_directory, expected_preserved)


class _CaptureBoundaryDocker:
    """The smallest lifecycle fake needed after the real public entry point starts."""

    def create_capture(self, *_args: object, **_kwargs: object) -> None:
        return None

    def start_capture(self, *_args: object, **_kwargs: object) -> None:
        return None

    def start_target(self, *_args: object, **_kwargs: object) -> None:
        return None

    def kill_target(self, *_args: object, **_kwargs: object) -> None:
        return None

    def signal_capture(self, *_args: object, **_kwargs: object) -> None:
        return None

    def kill_capture(self, *_args: object, **_kwargs: object) -> None:
        return None

    def service_state(self, *_args: object, **_kwargs: object) -> ServiceState | None:
        return None

    def service_logs(self, *_args: object, **_kwargs: object) -> str:
        return "capture diagnostics"


def _no_op(*_args: object, **_kwargs: object) -> None:
    return None


def _capture_source_kind(outcome: FailureOutcome, *, primary: bool) -> tuple[FailureKind, CaptureFailureOrigin]:
    """Describe a lower-boundary lifecycle observation, never an output payload."""
    if outcome.kind == "target_failed":
        return (
            FailureKind.TARGET_NONZERO_EXIT if primary else FailureKind.INDUCED_TARGET_STATUS,
            CaptureFailureOrigin.WORKLOAD,
        )
    if outcome.kind == "capture_failed":
        return FailureKind.CAPTURE_STOPPED, CaptureFailureOrigin.WORKLOAD
    if outcome.kind == "interrupted":
        return FailureKind.USER_INTERRUPTION, CaptureFailureOrigin.WORKLOAD
    if outcome.kind == "capture_malformed":
        return FailureKind.VALIDATION_FAILED, CaptureFailureOrigin.VALIDATION
    if outcome.kind == "cleanup_failed":
        return FailureKind.CLEANUP_FAILED, CaptureFailureOrigin.WORKLOAD
    if outcome.kind != "stage_timeout":
        raise AssertionError(f"unsupported primitive capture outcome {outcome.kind!r}")
    if outcome.detail in {
        "flush deadline expired after natural target success",
        "flush deadline expired",
    }:
        return FailureKind.STAGE_TIMEOUT, CaptureFailureOrigin.FLUSH
    if outcome.detail == "total-run deadline expired during validation":
        return FailureKind.TOTAL_TIMEOUT, CaptureFailureOrigin.VALIDATION
    if outcome.detail == "total-run deadline expired":
        return FailureKind.TOTAL_TIMEOUT, CaptureFailureOrigin.FLUSH
    return FailureKind.STAGE_TIMEOUT, CaptureFailureOrigin.WORKLOAD


def _capture_lifecycle_outcome(case: _BoundaryCase) -> CaptureOutcome:
    """Build primitive observed states that the public capture boundary renders."""
    primary = case.primary
    if primary.kind == "cleanup_failed":
        return CaptureOutcome()
    primary_kind, primary_origin = _capture_source_kind(primary, primary=True)
    secondary: list[FailureDetail] = []
    for item in case.outcomes[1:]:
        if item.kind == "cleanup_failed":
            continue
        kind, origin = _capture_source_kind(item, primary=False)
        secondary.append(
            FailureDetail(
                kind,
                item.detail,
                cast(int | None, item.status)
                if kind in (FailureKind.INDUCED_TARGET_STATUS, FailureKind.NATURAL_TARGET_STATUS)
                else None,
                origin=origin,
            )
        )
    if primary.detail == "capture stopped with status 42 after natural target success":
        secondary.append(
            FailureDetail(
                FailureKind.NATURAL_TARGET_STATUS,
                "target was also observed naturally exited with status 0",
                0,
            )
        )
    return CaptureOutcome(
        primary_kind,
        primary.detail,
        cast(int | None, primary.status) if primary_kind is FailureKind.TARGET_NONZERO_EXIT else None,
        primary_origin=primary_origin,
        secondary_details=tuple(secondary),
    )


def _capture_states(case: _BoundaryCase) -> tuple[ServiceState | None, ServiceState]:
    primary = case.primary
    has_capture_failure = any(item.kind == "capture_failed" for item in case.outcomes)
    flush = primary.detail in {
        "flush deadline expired after natural target success",
        "flush deadline expired",
    }
    capture_state = ServiceState(
        "capture",
        "capture",
        "capture",
        "running" if flush else "exited",
        0 if flush else (42 if has_capture_failure else 7),
    )
    if primary.kind == "target_failed":
        return (
            ServiceState("target", "target", "target", "exited", cast(int, primary.status)),
            capture_state,
        )
    if primary.kind == "capture_failed" and primary.detail == "capture stopped with status 42 while target remained active":
        return None, capture_state
    return ServiceState("target", "target", "target", "exited", 0), capture_state


def _run_capture_boundary_case(case: _BoundaryCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Route capture rows through ``capture_prepared_experiment`` and its final mapper."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = _prepared(run_directory)
    lifecycle = _capture_lifecycle_outcome(case)
    target_state, capture_state = _capture_states(case)
    has_cleanup = any(item.kind == "cleanup_failed" for item in case.outcomes)

    def ready(*_args: object, **_kwargs: object) -> CaptureOutcome:
        return CaptureOutcome()

    def observe(
        _docker: object,
        _compose_path: Path,
        _project_name: str,
        states: dict[str, ServiceState],
        **_kwargs: object,
    ) -> tuple[CaptureOutcome, ServiceState]:
        if target_state is not None:
            states["target"] = target_state
        states["capture"] = capture_state
        return lifecycle, capture_state

    def flush(
        _docker: object,
        _compose_path: Path,
        _project_name: str,
        states: dict[str, ServiceState],
        outcome: CaptureOutcome,
        **_kwargs: object,
    ) -> CaptureOutcome:
        states["capture"] = ServiceState("capture", "capture", "capture", "exited", 7)
        return outcome

    def cleanup(*_args: object, **_kwargs: object) -> SimpleNamespace:
        if has_cleanup:
            cleanup_outcome = next(item for item in case.outcomes if item.kind == "cleanup_failed")
            return SimpleNamespace(success=False, detail=cleanup_outcome.detail, secondary_details=())
        return SimpleNamespace(success=True, detail="", secondary_details=())

    def validated_prepared(_path: Path, _prepared_value: preflight.PreparedExperiment) -> Path:
        return run_directory

    monkeypatch.setattr(capture, "_validate_prepared_capture", validated_prepared)
    monkeypatch.setattr(capture, "load_or_recover_capture_pair", _no_op)
    monkeypatch.setattr(capture, "write_production_compose", _no_op)
    monkeypatch.setattr(capture, "_wait_readiness", ready)
    monkeypatch.setattr(capture, "_observe_workload", observe)
    monkeypatch.setattr(capture, "_flush_capture", flush)
    monkeypatch.setattr(capture, "cleanup_project", cleanup)

    with pytest.raises(TrafficlabError) as caught:
        capture.capture_prepared_experiment(
            prepared.source,
            prepared,
            docker=cast(capture.CaptureDocker, _CaptureBoundaryDocker()),
            clock=lambda: 0.0,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    records = [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]
    _assert_serialized_outcomes(records[-1], case)
    _assert_publication_state(case, run_directory, {})


_FIT_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_FIT_REFERENCE = (
    TraceEvent(10.0, Direction.OUTBOUND, 64),
    TraceEvent(11.0, Direction.INBOUND, 128),
    TraceEvent(12.0, Direction.OUTBOUND, 256),
)


def _fit_config(valid_config_data: dict[str, object], run_directory: Path) -> ExperimentConfig:
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
    base = ExperimentConfig.model_validate(data)
    poisson = base.models.poisson_empirical
    assert poisson is not None
    return base.model_copy(
        update={
            "models": base.models.model_copy(
                update={"poisson_empirical": poisson.model_copy(update={"c_lambda": FloatBounds(lower=20.0, upper=21.0)})}
            )
        }
    )


def _fit_trial(seed: int) -> TrialResult:
    methods = tuple(MethodTrialResult(name, 0.75, {"literal": 0.75}) for name in METHOD_ORDER)
    return TrialResult(
        seed,
        0.75,
        cast(tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult], methods),
    )


def _fit_success_outcome(config: ExperimentConfig) -> FitOutcome:
    winner = Candidate(
        CandidateId(0, 0),
        "poisson_empirical",
        (20.5,),
        "valid",
        0.75,
        (_fit_trial(config.genetic.trial_seeds[0]),),
        None,
        (),
    )
    return FitOutcome(winner, (_fit_trial(config.run.final_seed),), 0, "hard_limit")


def _fit_inputs(config: ExperimentConfig) -> dict[Path, bytes]:
    run_directory = config.run.directory
    return {
        run_directory / "experiment.toml": render_effective_config(config),
        run_directory / "capture.json": render_capture_metadata(_FIT_METADATA),
        run_directory / "reference.pcapng": encode_pcapng(_FIT_REFERENCE, _FIT_METADATA),
    }


def _fit_dependencies(
    config: ExperimentConfig,
    experiment_path: Path,
    inputs: dict[Path, bytes],
    strategy: Callable[[StrategyContext], FitOutcome],
) -> FitDependencies:
    prepared = preflight.PreparedExperiment(
        experiment_path,
        config,
        preflight.PreflightReport(config, ()),
        config.run.directory,
    )
    return FitDependencies(lambda _path: prepared, lambda path: inputs[path], strategy)


def _run_fit_boundary_case(
    case: _BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Exercise real fit ownership from checkpoint and publisher source conditions."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    config = _fit_config(valid_config_data, run_directory)
    inputs = _fit_inputs(config)

    if case.primary.kind == "artifact_corrupt":
        checkpoint_path = run_directory / "checkpoint.json"
        checkpoint_path.write_bytes(b"corrupt checkpoint\n")
        expected_preserved = {checkpoint_path: b"corrupt checkpoint\n"}

        def corrupt_checkpoint(*_args: object, **_kwargs: object) -> object:
            raise CheckpointCorruptionError(case.primary.detail, corrective_action=case.primary.corrective_action)

        monkeypatch.setattr(strategy_module, "load_generation", corrupt_checkpoint)
        dependencies = _fit_dependencies(config, experiment_path, inputs, run_strategy)
    elif case.primary.kind == "publication_collision":
        expected_preserved = _prepare_publication_state(case, run_directory)

        def collide(_path: Path, _content: bytes) -> object:
            raise TrafficlabError(case.primary.detail, corrective_action=case.primary.corrective_action)

        monkeypatch.setattr(fitting, "publish_best_model", collide)
        dependencies = _fit_dependencies(
            config,
            experiment_path,
            inputs,
            lambda _context: _fit_success_outcome(config),
        )
    else:
        raise AssertionError(f"unsupported primitive fit outcome {case.primary.kind!r}")

    with pytest.raises(TrafficlabError) as caught:
        fitting.fit_experiment(experiment_path, dependencies=dependencies)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    records = [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]
    _assert_serialized_outcomes(records[-1], case)
    _assert_publication_state(case, run_directory, expected_preserved)


def _run_generation_boundary_case(case: _BoundaryCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exercise public generation mapping from bare read and generator failures."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = _prepared(run_directory)
    prepared_config = cast(Any, prepared.config)
    prepared_config.run.final_seed = 1
    prepared_config.models = SimpleNamespace(enabled=("poisson_empirical",), poisson_empirical=SimpleNamespace())
    prepared_config.generation = SimpleNamespace(final=SimpleNamespace())
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    def open_prepared(_path: Path) -> preflight.PreparedExperiment:
        return prepared

    monkeypatch.setattr(generation, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(generation, "append_run_log", append)
    if case.primary.kind == "artifact_missing":

        def missing(_path: Path, **_kwargs: object) -> bytes:
            raise TrafficlabError(case.primary.detail, corrective_action=case.primary.corrective_action)

        monkeypatch.setattr(generation, "_read_required_bytes", missing)
    elif case.primary.kind == "generation_incomplete":
        captured = b"capture metadata"
        best = SimpleNamespace(
            family="poisson_empirical",
            gene_bounds={},
            capture_sha256=hashlib.sha256(captured).hexdigest(),
            observation_window_seconds=1.0,
            fitted=object(),
        )

        def read(_path: Path, **_kwargs: object) -> bytes:
            return captured

        def load_best(*_args: object, **_kwargs: object) -> object:
            return best

        def family_for(_name: str) -> object:
            return _Family()

        def parse_metadata(*_args: object, **_kwargs: object) -> object:
            return object()

        class _Family:
            gene_names: tuple[str, ...] = ()

            @staticmethod
            def generate(*_args: object, **_kwargs: object) -> object:
                raise TrafficlabError(case.primary.detail, corrective_action=case.primary.corrective_action)

        monkeypatch.setattr(generation, "_read_required_bytes", read)
        monkeypatch.setattr(generation, "load_best_model", load_best)
        monkeypatch.setattr(generation, "get_family", family_for)
        monkeypatch.setattr(generation, "parse_capture_metadata", parse_metadata)
    else:
        raise AssertionError(f"unsupported primitive generation outcome {case.primary.kind!r}")

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(prepared.source)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    _assert_serialized_outcomes(records[-1], case)
    _assert_publication_state(case, run_directory, {})


def _run_comparison_boundary_case(case: _BoundaryCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Exercise public comparison mapping from evaluation and publication sources."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    config = SimpleNamespace(run=SimpleNamespace(directory=run_directory), similarity=SimpleNamespace())
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    def load_config(_path: Path) -> object:
        return config

    def read_input(*_args: object, **_kwargs: object) -> bytes:
        return b"input"

    def parse_metadata(*_args: object, **_kwargs: object) -> object:
        return object()

    def parse_events(*_args: object, **_kwargs: object) -> tuple[()]:
        return ()

    def normalized(_events: object) -> tuple[tuple[()], float]:
        return (), 1.0

    def aligned(_events: object, _window: object) -> tuple[()]:
        return ()

    def settings_hash(_settings: object) -> str:
        return "a" * 64

    monkeypatch.setattr(comparison, "load_experiment", load_config)
    monkeypatch.setattr(comparison, "_read_comparison_input", read_input)
    monkeypatch.setattr(comparison, "parse_capture_metadata", parse_metadata)
    monkeypatch.setattr(comparison, "parse_pcapng_bytes", parse_events)
    monkeypatch.setattr(comparison, "normalize_reference", normalized)
    monkeypatch.setattr(comparison, "align_generated", aligned)
    monkeypatch.setattr(comparison, "similarity_settings_sha256", settings_hash)
    monkeypatch.setattr(comparison, "append_run_log", append)
    if case.primary.kind == "metric_infeasible":

        def infeasible(*_args: object, **_kwargs: object) -> ComparisonResult:
            raise TrafficlabError(case.primary.detail, corrective_action=case.primary.corrective_action)

        monkeypatch.setattr(comparison, "compare_traces", infeasible)
    elif case.primary.kind == "publication_failed":

        class _Result:
            def with_input_sha256(self, _identities: object) -> "_Result":
                return self

        def result(*_args: object, **_kwargs: object) -> _Result:
            return _Result()

        def durability(_path: Path, _result: object) -> bool:
            raise comparison._PublicationError(  # pyright: ignore[reportPrivateUsage]
                case.primary.detail,
                corrective_action=case.primary.corrective_action,
            )

        monkeypatch.setattr(comparison, "compare_traces", result)
        monkeypatch.setattr(comparison, "_publish_comparison_result", durability)
    else:
        raise AssertionError(f"unsupported primitive comparison outcome {case.primary.kind!r}")

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    _assert_serialized_outcomes(records[-1], case)
    _assert_publication_state(case, run_directory, {})


def _run_study_publication_case(case: _BoundaryCase, tmp_path: Path) -> None:
    """Exercise exclusive accepted-bundle publication from an occupied destination."""
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_bytes(b'{"files":[]}\n')
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    destination.mkdir(parents=True)
    retained = destination / "retained.txt"
    retained.write_bytes(b"accepted evidence\n")

    with pytest.raises(TrafficlabError) as caught:
        study_evidence.publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert retained.read_bytes() == b"accepted evidence\n"


def test_public_boundary_case_registry_covers_each_authoritative_fixture_row_once() -> None:
    """Every checked fixture row belongs to one public-boundary primary/secondary case."""
    fixture_rows = tuple(
        json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line
    )
    registry_rows = tuple(outcome.as_dict() for case in _PUBLIC_BOUNDARY_CASES for outcome in case.outcomes)

    assert len(fixture_rows) == 43
    assert registry_rows == fixture_rows
    assert any(case.deferred_typed_detector for case in _PUBLIC_BOUNDARY_CASES)


@pytest.mark.parametrize("case", _PUBLIC_BOUNDARY_CASES, ids=lambda case: case.identifier)
def test_public_boundaries_serialize_the_authoritative_failure_matrix(
    case: _BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """A wrong owner, action, authority, state, or publication side effect breaks this matrix."""
    if case.injection_stage == "preflight":
        _run_preflight_case(case, monkeypatch, tmp_path)
    elif case.deferred_typed_detector:
        _run_deferred_typed_coordinator_case(case, monkeypatch, tmp_path)
    elif case.primary.stage == "capture":
        _run_capture_boundary_case(case, monkeypatch, tmp_path)
    elif case.primary.stage == "fit":
        _run_fit_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.primary.stage == "generate":
        _run_generation_boundary_case(case, monkeypatch, tmp_path)
    elif case.primary.stage == "compare":
        _run_comparison_boundary_case(case, monkeypatch, tmp_path)
    elif case.primary.stage == "publication":
        _run_study_publication_case(case, tmp_path)
    else:
        raise AssertionError(f"unsupported public boundary stage {case.primary.stage!r}")
