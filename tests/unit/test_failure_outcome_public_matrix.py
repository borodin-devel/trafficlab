"""Public-boundary coverage for the canonical expected-failure matrix.

The matrix uses the checked JSONL rows as its expected values.  Artifact-identity
and study-acceptance detections scheduled for Tasks 4 and 11 are injected as
typed errors at the nearest current public stage callback: this test verifies
their downstream serialization and publication state, not a detector that is
deliberately outside the current synthetic fixture harness.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

import pytest

import trafficlab.preflight as preflight
import trafficlab.run as run
from trafficlab.capture import CaptureResult
from trafficlab.comparison import ComparisonResult
from trafficlab.config import ExperimentConfig
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.fitting import FitStageResult
from trafficlab.generation import GenerationStageResult

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "diagnostics" / "failure-outcomes.jsonl"

type _InjectionStage = Literal["capture", "fit", "generate", "compare"]

_FUTURE_DETECTION_CASES = frozenset(
    {
        ("capture", "artifact_stale"),
        ("fit", "artifact_changed"),
        ("compare", "artifact_foreign"),
        ("publication", "publication_collision"),
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
    future_detector: bool

    @property
    def primary(self) -> FailureOutcome:
        return self.outcomes[0]

    @property
    def identifier(self) -> str:
        return f"{self.primary.stage}-{self.primary.kind}-{self.primary.detail}"


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
    elif primary.stage == "publication":
        injection_stage = "compare"
    else:
        injection_stage = cast(_InjectionStage, primary.stage)
    return _BoundaryCase(
        outcomes=outcomes,
        injection_stage=injection_stage,
        future_detector=(primary.stage, primary.kind) in _FUTURE_DETECTION_CASES,
    )


_PUBLIC_BOUNDARY_CASES = _build_boundary_cases()


def _prepared(run_directory: Path) -> preflight.PreparedExperiment:
    config = cast(
        ExperimentConfig,
        SimpleNamespace(
            capture=SimpleNamespace(total_timeout_seconds=5.0),
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


def _run_coordinator_case(case: _BoundaryCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_public_boundary_case_registry_covers_each_authoritative_fixture_row_once() -> None:
    """Every checked fixture row belongs to one public-boundary primary/secondary case."""
    fixture_rows = tuple(
        json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line
    )
    registry_rows = tuple(outcome.as_dict() for case in _PUBLIC_BOUNDARY_CASES for outcome in case.outcomes)

    assert len(fixture_rows) == 43
    assert registry_rows == fixture_rows
    assert any(case.future_detector for case in _PUBLIC_BOUNDARY_CASES)


@pytest.mark.parametrize("case", _PUBLIC_BOUNDARY_CASES, ids=lambda case: case.identifier)
def test_public_boundaries_serialize_the_authoritative_failure_matrix(
    case: _BoundaryCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A wrong owner, action, authority, state, or publication side effect breaks this matrix."""
    if case.injection_stage == "preflight":
        _run_preflight_case(case, monkeypatch, tmp_path)
    else:
        _run_coordinator_case(case, monkeypatch, tmp_path)
