"""Canonical expected-failure evidence stays independent of Docker and the network."""

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import trafficlab.capture as capture
import trafficlab.comparison as comparison
import trafficlab.docker_cli as docker_cli
import trafficlab.fitting as fitting
import trafficlab.generation as generation
import trafficlab.preflight as preflight
import trafficlab.run as run
from tests.support.fixture_paths import DIAGNOSTIC_FIXTURE_ROOT
from trafficlab.capture_policy import CaptureOutcome, FailureDetail, FailureKind
from trafficlab.config import ExperimentConfig
from trafficlab.errors import FailureOutcome, TrafficlabError, attach_failure_outcome, failure_outcome_from_error

_FIXTURE = DIAGNOSTIC_FIXTURE_ROOT / "failure-outcomes.jsonl"


def _fixture_records() -> tuple[dict[str, object], ...]:
    return tuple(json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line)


def _fixture_outcome(detail: str, *, authority: str = "primary") -> FailureOutcome:
    for record in _fixture_records():
        if record["detail"] == detail and record["authority"] == authority:
            return FailureOutcome.from_dict(record)
    raise AssertionError(f"missing authoritative fixture row for {detail!r}/{authority}")


def _prepared_preflight(tmp_path: Path) -> preflight.PreparedExperiment:
    config = cast(ExperimentConfig, SimpleNamespace(capture=SimpleNamespace(total_timeout_seconds=5.0)))
    report = preflight.PreflightReport(config=config, findings=())
    return preflight.PreparedExperiment(
        source=tmp_path / "experiment.toml",
        config=config,
        report=report,
        run_directory=tmp_path / "run",
        portable_config=config,
    )


def test_credential_free_fixture_is_a_strict_authoritative_adverse_condition_table() -> None:
    """The checked table is strict offline evidence, while boundary tests prove its production mappings."""
    raw_records = tuple(line for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line)
    outcomes = tuple(FailureOutcome.from_json(line) for line in raw_records)

    assert len(outcomes) == 43
    assert {outcome.kind for outcome in outcomes} >= {
        "artifact_changed",
        "artifact_corrupt",
        "artifact_foreign",
        "artifact_missing",
        "artifact_stale",
        "capture_failed",
        "capture_malformed",
        "cleanup_failed",
        "configuration_invalid",
        "docker_preflight_failed",
        "generation_incomplete",
        "interrupted",
        "metric_infeasible",
        "publication_collision",
        "publication_failed",
        "scientific_semantics_incompatible",
        "stage_timeout",
        "target_failed",
    }
    assert all(
        "/home/" not in line and "token" not in line.lower() and "password" not in line.lower() for line in raw_records
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", "", "kind"),
        ("stage", "", "stage"),
        ("detail", "", "detail"),
        ("affected_evidence", "", "affected_evidence"),
        ("evidence_state", "lost", "evidence_state"),
        ("corrective_action", "", "corrective_action"),
        ("authority", "unknown", "authority"),
        ("status", True, "status"),
    ],
)
def test_failure_outcome_rejects_noncanonical_values(field: str, value: object, message: str) -> None:
    """A permissive outcome would let a later failure use an unreviewed vocabulary."""
    values: dict[str, object] = {
        "kind": "metric_infeasible",
        "stage": "compare",
        "detail": "insufficient samples",
        "affected_evidence": "similarity.json",
        "evidence_state": "not_published",
        "corrective_action": "correct samples or settings",
        "authority": "primary",
        "status": None,
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError), match=message):
        FailureOutcome(**values)  # type: ignore[arg-type]


def test_failure_outcome_from_error_preserves_the_existing_error_interface() -> None:
    """Canonical evidence augments, rather than rewrites, an established stage error."""
    error = TrafficlabError("reference input is missing", corrective_action="restore reference.pcapng", exit_code=17)

    outcome = failure_outcome_from_error(
        error,
        kind="artifact_missing",
        stage="fit",
        affected_evidence="reference.pcapng",
        evidence_state="not_published",
    )

    assert outcome == FailureOutcome(
        kind="artifact_missing",
        stage="fit",
        detail="reference input is missing",
        affected_evidence="reference.pcapng",
        evidence_state="not_published",
        corrective_action="restore reference.pcapng",
        authority="primary",
    )
    assert str(error) == "reference input is missing"
    assert error.corrective_action == "restore reference.pcapng"
    assert error.exit_code == 17


def test_expected_error_carries_one_immutable_canonical_outcome() -> None:
    """Direct-error boundaries need evidence even when no run log can exist yet."""
    outcome = FailureOutcome(
        kind="configuration_invalid",
        stage="preflight",
        detail="run.directory is invalid",
        affected_evidence="run evidence",
        evidence_state="not_published",
        corrective_action="correct run.directory",
        authority="primary",
    )
    error = TrafficlabError(
        "run.directory is invalid",
        corrective_action="correct run.directory",
        exit_code=17,
        failure_outcome=outcome,
    )

    assert error.failure_outcome is outcome
    assert str(error) == "run.directory is invalid"
    assert error.corrective_action == "correct run.directory"
    assert error.exit_code == 17


def test_expected_error_rejects_a_noncanonical_payload_and_attachment_preserves_an_existing_one() -> None:
    """The error carrier accepts only the immutable record and never overwrites the owning boundary."""
    with pytest.raises(TypeError, match="failure_outcome"):
        TrafficlabError(
            "invalid outcome",
            corrective_action="repair the test",
            failure_outcome=cast(FailureOutcome, object()),
        )

    error = TrafficlabError("missing model", corrective_action="rerun fit")
    attached = attach_failure_outcome(
        error,
        kind="artifact_missing",
        stage="generate",
        affected_evidence="best_model.json",
        evidence_state="not_published",
    )
    existing = FailureOutcome(
        kind="artifact_foreign",
        stage="compare",
        detail="foreign generated trace",
        affected_evidence="generated.pcapng",
        evidence_state="preserved",
        corrective_action="regenerate",
        authority="primary",
    )
    error.failure_outcome = existing

    assert attached is error
    assert (
        attach_failure_outcome(
            error,
            kind="metric_infeasible",
            stage="compare",
            affected_evidence="similarity.json",
            evidence_state="not_published",
        ).failure_outcome
        is existing
    )


def test_direct_preflight_configuration_failure_carries_outcome_without_a_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A local error precedes run creation, so its canonical evidence must travel with the error."""
    error = TrafficlabError(
        "target.argv: must contain at least one argument", corrective_action="correct the named field or path"
    )

    def fail_open(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", fail_open)

    with pytest.raises(TrafficlabError, match="target.argv") as caught:
        preflight.run_preflight(tmp_path / "experiment.toml", config_only=True)

    assert caught.value.failure_outcome == _fixture_outcome("target.argv: must contain at least one argument")
    assert not (tmp_path / "run.log").exists()


def test_direct_preflight_preserves_an_existing_boundary_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lower boundary may already have a more specific direct-error classification."""
    existing = FailureOutcome(
        kind="artifact_stale",
        stage="capture",
        detail="existing capture pair conflicts",
        affected_evidence="capture pair",
        evidence_state="preserved",
        corrective_action="choose a new run directory",
        authority="primary",
    )
    error = TrafficlabError(
        "existing capture pair conflicts",
        corrective_action="choose a new run directory",
        failure_outcome=existing,
    )

    def fail_open(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", fail_open)

    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(tmp_path / "experiment.toml", config_only=True)

    assert caught.value.failure_outcome is existing


def test_generate_public_boundary_classifies_a_missing_best_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The public generation API reports its absent source artifact before any generated output exists."""
    prepared = _prepared_preflight(tmp_path)
    prepared.run_directory.mkdir()

    def open_prepared(_path: Path) -> preflight.PreparedExperiment:
        return prepared

    monkeypatch.setattr(generation, "open_or_prepare_experiment", open_prepared)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(tmp_path / "experiment.toml")

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_missing",
        "generate",
        "best_model.json",
        "not_published",
    )


def test_generate_public_boundary_classifies_an_unreadable_best_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An existing unreadable model is preserved corruption evidence, not absence or incomplete generation."""
    prepared = _prepared_preflight(tmp_path)
    prepared.run_directory.mkdir()
    model_path = prepared.run_directory / "best_model.json"
    real_read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == model_path:
            raise PermissionError("injected denied model")
        return real_read_bytes(path)

    def open_prepared(_path: Path) -> preflight.PreparedExperiment:
        return prepared

    monkeypatch.setattr(generation, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(Path, "read_bytes", unreadable)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(tmp_path / "experiment.toml")

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "generate",
        "best_model.json",
        "preserved",
    )


def test_preflight_adapter_maps_probe_cleanup_to_remaining_inventory() -> None:
    """Probe cleanup has a distinct retained-inventory outcome from Docker availability."""
    outcome = preflight._preflight_failure_outcome(  # pyright: ignore[reportPrivateUsage]
        preflight.PreflightFinding("probe_cleanup", False, "probe project remained", "remove probe project")
    )

    assert outcome == FailureOutcome(
        kind="cleanup_failed",
        stage="preflight",
        detail="probe project remained",
        affected_evidence="inventory",
        evidence_state="possibly_remaining",
        corrective_action="remove probe project",
        authority="primary",
    )


def test_run_preflight_constructs_docker_and_logs_successful_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The no-injected-Docker path remains a normal full-preflight adapter boundary."""
    prepared = _prepared_preflight(tmp_path)
    records: list[dict[str, object]] = []

    class Compose:
        def __init__(self, *, clock: object) -> None:
            self.clock = clock

    def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        return prepared

    def check(
        _config: ExperimentConfig, _docker: object, *, deadline: float, clock: object
    ) -> preflight.PreflightReport:
        del _config, _docker, clock
        assert deadline == 105.0
        return preflight.PreflightReport(
            config=prepared.config,
            findings=(preflight.PreflightFinding("docker_daemon", True, "Docker is ready"),),
        )

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(docker_cli, "DockerCompose", Compose)
    monkeypatch.setattr(preflight, "check_docker", check)
    monkeypatch.setattr(preflight, "append_run_log", append)

    result = preflight.run_preflight(tmp_path / "experiment.toml", config_only=False, clock=lambda: 100.0)

    assert result.run_directory == prepared.run_directory
    assert records == [
        {
            "detail": "Docker is ready",
            "event": "preflight_check",
            "name": "docker_daemon",
            "ok": True,
            "stage": "preflight",
        }
    ]


@pytest.mark.parametrize("clock", [lambda: (_ for _ in ()).throw(ArithmeticError("injected")), lambda: float("nan")])
def test_run_preflight_deadline_errors_carry_docker_outcomes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: Callable[[], float]
) -> None:
    """Deadline construction failures are full-preflight failures even before a Docker command runs."""
    prepared = _prepared_preflight(tmp_path)

    def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        return prepared

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)

    with pytest.raises(TrafficlabError, match="Docker preflight deadline") as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=clock,
        )

    assert caught.value.failure_outcome == FailureOutcome(
        kind="docker_preflight_failed",
        stage="preflight",
        detail=str(caught.value),
        affected_evidence="capture evidence",
        evidence_state="not_published",
        corrective_action=caught.value.corrective_action,
        authority="primary",
    )


def test_run_preflight_records_append_and_identity_failures_as_direct_outcomes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Log failures retain the first failure while the environment identity never masks it."""
    prepared = _prepared_preflight(tmp_path)
    calls = 0

    def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        return prepared

    def check(
        _config: ExperimentConfig, _docker: object, *, deadline: float, clock: object
    ) -> preflight.PreflightReport:
        del _config, _docker, deadline, clock
        identity = cast(
            preflight.CaptureEnvironmentIdentity,
            SimpleNamespace(
                capture_content_id="capture-id",
                capture_reference="capture:fixed",
                capture_tool_version="1.0",
                host_architecture="linux/amd64",
                target_content_id="target-id",
                target_reference="target:fixed",
            ),
        )
        return preflight.PreflightReport(
            config=prepared.config,
            findings=(preflight.PreflightFinding("docker_daemon", True, "Docker is ready"),),
            environment_identity=identity,
        )

    def fail_second_append(_directory: Path, _record: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return
        raise TrafficlabError("identity log failed", corrective_action="repair run.log")

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(preflight, "check_docker", check)
    monkeypatch.setattr(preflight, "append_run_log", fail_second_append)

    with pytest.raises(TrafficlabError, match="run_log: identity log failed") as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "publication_failed"


def test_run_preflight_preserves_docker_failure_when_its_log_append_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run-log failure is secondary to the first Docker preflight failure."""
    prepared = _prepared_preflight(tmp_path)

    def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        return prepared

    def check(
        _config: ExperimentConfig, _docker: object, *, deadline: float, clock: object
    ) -> preflight.PreflightReport:
        del _config, _docker, deadline, clock
        return preflight.PreflightReport(
            config=prepared.config,
            findings=(preflight.PreflightFinding("docker_daemon", False, "Docker is unavailable", "restore Docker"),),
        )

    def fail_append(_directory: Path, _record: dict[str, object]) -> None:
        raise TrafficlabError("run log unavailable", corrective_action="repair run.log")

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(preflight, "check_docker", check)
    monkeypatch.setattr(preflight, "append_run_log", fail_append)

    with pytest.raises(TrafficlabError, match="docker_daemon: Docker is unavailable") as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "docker_preflight_failed"


def test_run_preflight_keeps_probe_cleanup_as_secondary_after_probe_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed probe owns the primary result; later inventory cleanup is ordered secondary evidence."""
    prepared = _prepared_preflight(tmp_path)

    def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        return prepared

    def check(
        _config: ExperimentConfig, _docker: object, *, deadline: float, clock: object
    ) -> preflight.PreflightReport:
        del _config, _docker, deadline, clock
        return preflight.PreflightReport(
            config=prepared.config,
            findings=(
                preflight.PreflightFinding(
                    "network_probe", False, "capture prerequisite is unavailable", "repair probe"
                ),
                preflight.PreflightFinding("probe_cleanup", False, "probe project remained", "remove probe project"),
            ),
        )

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(preflight, "check_docker", check)

    def append(_directory: Path, _record: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(preflight, "append_run_log", append)

    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )

    assert [
        (item.kind, item.authority, item.affected_evidence, item.evidence_state)
        for item in caught.value.failure_outcomes
    ] == [
        ("docker_preflight_failed", "primary", "capture evidence", "not_published"),
        ("cleanup_failed", "secondary", "inventory", "possibly_remaining"),
    ]


def test_run_preflight_keeps_an_existing_report_outcome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Aggregation must not overwrite a failure already classified at its owner."""
    prepared = _prepared_preflight(tmp_path)
    existing = FailureOutcome(
        kind="docker_preflight_failed",
        stage="preflight",
        detail="Docker unavailable",
        affected_evidence="capture evidence",
        evidence_state="not_published",
        corrective_action="restore Docker",
        authority="primary",
    )
    error = TrafficlabError("Docker unavailable", corrective_action="restore Docker", failure_outcome=existing)

    def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        return prepared

    def check(
        _config: ExperimentConfig, _docker: object, *, deadline: float, clock: object
    ) -> preflight.PreflightReport:
        del _config, _docker, deadline, clock
        return preflight.PreflightReport(config=prepared.config, findings=())

    def raise_existing(_report: preflight.PreflightReport) -> None:
        raise error

    def append(_directory: Path, _record: dict[str, object]) -> None:
        return None

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(preflight, "check_docker", check)
    monkeypatch.setattr(preflight, "append_run_log", append)
    monkeypatch.setattr(preflight.PreflightReport, "require_success", raise_existing)

    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )

    assert caught.value is error


@pytest.mark.parametrize(
    ("stage_module", "outcome_kind", "stage", "affected_evidence", "evidence_state", "failure_kind"),
    [
        (fitting, "artifact_changed", "fit", "reference.pcapng", "preserved", None),
        (fitting, "artifact_corrupt", "fit", "checkpoint.json", "preserved", None),
        (fitting, "scientific_semantics_incompatible", "fit", "checkpoint.json", "preserved", None),
        (fitting, "publication_collision", "fit", "best_model.json", "preserved", None),
        (generation, "artifact_missing", "generate", "best_model.json", "not_published", None),
        (generation, "scientific_semantics_incompatible", "generate", "best_model.json", "preserved", None),
        (generation, "generation_incomplete", "generate", "generated.pcapng", "not_published", None),
        (comparison, "artifact_foreign", "compare", "generated.pcapng", "preserved", "evaluation_or_input"),
        (comparison, "metric_infeasible", "compare", "similarity.json", "not_published", "evaluation_or_input"),
        (comparison, "publication_failed", "compare", "similarity.json", "not_published", "publication"),
    ],
)
def test_stage_adapters_preserve_explicit_boundary_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage_module: object,
    outcome_kind: str,
    stage: str,
    affected_evidence: str,
    evidence_state: str,
    failure_kind: str | None,
) -> None:
    """Adapters must retain owning-boundary semantics rather than infer a coarse stage default."""
    outcome = FailureOutcome(
        kind=outcome_kind,
        stage=stage,
        detail=f"{stage} injected failure",
        affected_evidence=affected_evidence,
        evidence_state=evidence_state,  # type: ignore[arg-type]
        corrective_action=f"repair {stage}",
        authority="primary",
    )
    error = TrafficlabError(
        f"{stage} injected failure",
        corrective_action=f"repair {stage}",
        failure_outcome=outcome,
    )
    records: list[dict[str, object]] = []

    def append(_run_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(stage_module, "append_run_log", append)
    if stage_module is comparison:
        comparison._append_failure(  # pyright: ignore[reportPrivateUsage]
            tmp_path, error, failure_kind=cast(str, failure_kind)
        )
    else:
        stage_module._append_failure(tmp_path, error)  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]

    assert records[0]["failure_outcome"] == outcome.as_dict()


def test_coordinator_preserves_the_originating_stage_outcome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The coordinator must not rewrite a precise stage outcome while recording run_failed."""
    outcome = FailureOutcome(
        kind="publication_collision",
        stage="fit",
        detail="best model already exists",
        affected_evidence="best_model.json",
        evidence_state="preserved",
        corrective_action="choose a new run directory",
        authority="primary",
    )
    error = TrafficlabError(
        "best model already exists",
        corrective_action="choose a new run directory",
        failure_outcome=outcome,
    )
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(run, "append_run_log", append)

    run._append_run_failure(  # pyright: ignore[reportPrivateUsage]
        tmp_path,
        error,
        failed_stage="fit",
        completed_stages=("preflight", "capture"),
    )

    assert records[0]["failure_outcome"] == outcome.as_dict()


def test_coordinator_records_ordered_secondary_outcomes_without_reclassification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A coordinator log retains later cleanup evidence rather than deriving another coarse primary."""
    primary = FailureOutcome(
        kind="publication_failed",
        stage="compare",
        detail="similarity durability check failed",
        affected_evidence="similarity.json",
        evidence_state="not_published",
        corrective_action="correct storage and rerun compare",
        authority="primary",
    )
    secondary = FailureOutcome(
        kind="cleanup_failed",
        stage="compare",
        detail="owned temporary file cleanup failed",
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

    monkeypatch.setattr(run, "append_run_log", append)

    run._append_run_failure(  # pyright: ignore[reportPrivateUsage]
        tmp_path,
        error,
        failed_stage="compare",
        completed_stages=("preflight", "capture", "fit", "generate"),
    )

    assert records[0]["failure_outcome"] == primary.as_dict()
    assert records[0]["secondary_outcomes"] == [secondary.as_dict()]


@pytest.mark.parametrize(
    ("kind", "capture_status", "expected_kind", "expected_state", "expected_status"),
    [
        (FailureKind.STAGE_TIMEOUT, None, "stage_timeout", "diagnostic_only", None),
        (FailureKind.FLUSH_FAILED, None, "stage_timeout", "not_published", None),
        (FailureKind.TOTAL_TIMEOUT, None, "stage_timeout", "not_published", None),
        (FailureKind.USER_INTERRUPTION, None, "interrupted", "diagnostic_only", 130),
        (FailureKind.CLEANUP_FAILED, None, "cleanup_failed", "possibly_remaining", None),
        (FailureKind.CAPTURE_STOPPED, 42, "capture_failed", "not_published", 42),
    ],
)
def test_capture_adapter_retains_timeout_state_and_service_status(
    kind: FailureKind,
    capture_status: int | None,
    expected_kind: str,
    expected_state: str,
    expected_status: int | None,
) -> None:
    """Canonical capture facts supplement, but never replace, the existing lifecycle arbitration."""
    outcome = CaptureOutcome(kind, f"{kind.value} detail")

    primary, secondary = capture._capture_failure_outcomes(  # pyright: ignore[reportPrivateUsage]
        outcome, capture_status=capture_status
    )

    assert secondary == ()
    assert primary.kind == expected_kind
    assert primary.evidence_state == expected_state
    assert primary.status == expected_status
    assert primary.authority == "primary"


def test_capture_adapter_carries_capture_service_status_for_secondary_failure() -> None:
    """A later capture exit still needs its actual service status, not the CLI error exit code."""
    outcome = CaptureOutcome(
        FailureKind.TARGET_NONZERO_EXIT,
        "target exited naturally with status 23",
        23,
        (FailureDetail(FailureKind.CAPTURE_STOPPED, "capture exited with status 42"),),
    )

    _primary, secondary = capture._capture_failure_outcomes(  # pyright: ignore[reportPrivateUsage]
        outcome, capture_status=42
    )

    assert secondary[0].kind == "capture_failed"
    assert secondary[0].status == 42
    assert secondary[0].authority == "secondary"


def test_capture_adapter_uses_the_full_arbitration_sequence_for_combined_evidence() -> None:
    """A target/capture/total combination has different state and actions than each enum alone."""
    outcome = CaptureOutcome(
        FailureKind.TARGET_NONZERO_EXIT,
        "target exited naturally with status 23",
        23,
        (
            FailureDetail(FailureKind.CAPTURE_STOPPED, "capture stopped with status 42"),
            FailureDetail(FailureKind.TOTAL_TIMEOUT, "total-run deadline expired"),
        ),
    )

    primary, secondary = capture._capture_failure_outcomes(  # pyright: ignore[reportPrivateUsage]
        outcome, capture_status=42
    )

    assert primary == FailureOutcome(
        kind="target_failed",
        stage="capture",
        detail="target exited naturally with status 23",
        affected_evidence="capture pair",
        evidence_state="not_published",
        corrective_action="inspect target first, then capture and budget",
        authority="primary",
        status=23,
    )
    assert [item.as_dict() for item in secondary] == [
        {
            "kind": "capture_failed",
            "stage": "capture",
            "detail": "capture stopped with status 42",
            "affected_evidence": "capture pair",
            "evidence_state": "not_published",
            "corrective_action": "inspect capture status and log",
            "authority": "secondary",
            "status": 42,
        },
        {
            "kind": "stage_timeout",
            "stage": "capture",
            "detail": "total-run deadline expired",
            "affected_evidence": "capture pair",
            "evidence_state": "not_published",
            "corrective_action": "increase total budget",
            "authority": "secondary",
        },
    ]


def test_capture_adapter_requires_an_existing_primary_failure() -> None:
    """The adapter cannot invent a primary when capture arbitration succeeded."""
    with pytest.raises(ValueError, match="primary failure"):
        capture._capture_failure_outcomes(CaptureOutcome())  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("stage_module", "kind", "stage", "affected_evidence", "evidence_state"),
    [
        (fitting, "artifact_corrupt", "fit", "fit inputs", "preserved"),
        (generation, "artifact_corrupt", "generate", "generation inputs", "preserved"),
        (comparison, "publication_failed", "compare", "similarity.json", "not_published"),
        (run, "metric_infeasible", "compare", "similarity.json", "not_published"),
    ],
)
def test_existing_stage_failure_log_adapters_render_canonical_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage_module: object,
    kind: str,
    stage: str,
    affected_evidence: str,
    evidence_state: str,
) -> None:
    """Stage-specific logs must retain one machine-readable outcome without changing their error text."""
    records: list[dict[str, object]] = []
    error = TrafficlabError(f"{stage} injected failure", corrective_action=f"repair {stage}", exit_code=19)

    def append(_run_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(stage_module, "append_run_log", append)
    if stage_module is comparison:
        comparison._append_failure(tmp_path, error, failure_kind="publication")  # pyright: ignore[reportPrivateUsage]
    elif stage_module is run:
        run._append_run_failure(  # pyright: ignore[reportPrivateUsage]
            tmp_path,
            error,
            failed_stage="compare",
            completed_stages=("preflight", "capture", "fit", "generate"),
        )
    else:
        stage_module._append_failure(tmp_path, error)  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]

    assert (
        records[0]["failure_outcome"]
        == FailureOutcome(
            kind=kind,
            stage=stage,
            detail=f"{stage} injected failure",
            affected_evidence=affected_evidence,
            evidence_state=evidence_state,  # type: ignore[arg-type]
            corrective_action=f"repair {stage}",
            authority="primary",
        ).as_dict()
    )


def test_preflight_and_capture_adapters_preserve_primary_secondary_authority() -> None:
    """Canonical records must mirror the existing direct preflight and capture arbitration boundaries."""
    preflight_outcome = preflight._preflight_failure_outcome(  # pyright: ignore[reportPrivateUsage]
        preflight.PreflightFinding("docker_engine", False, "Docker Engine is unavailable", "restore Docker Engine")
    )
    capture_outcome = CaptureOutcome(
        FailureKind.TARGET_NONZERO_EXIT,
        "target exited naturally with status 23",
        23,
        (FailureDetail(FailureKind.CLEANUP_FAILED, "capture cleanup timed out"),),
    )
    primary, secondary = capture._capture_failure_outcomes(capture_outcome)  # pyright: ignore[reportPrivateUsage]

    assert preflight_outcome == FailureOutcome(
        kind="docker_preflight_failed",
        stage="preflight",
        detail="Docker Engine is unavailable",
        affected_evidence="capture evidence",
        evidence_state="not_published",
        corrective_action="restore Docker Engine",
        authority="primary",
    )
    assert primary.kind == "target_failed"
    assert primary.authority == "primary"
    assert primary.status == 23
    assert secondary == (
        FailureOutcome(
            kind="cleanup_failed",
            stage="capture",
            detail="capture cleanup timed out",
            affected_evidence="inventory",
            evidence_state="possibly_remaining",
            corrective_action="remove the named project",
            authority="secondary",
        ),
    )
