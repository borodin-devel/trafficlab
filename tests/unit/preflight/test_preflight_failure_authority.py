from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import trafficlab.capture.docker_cli as docker_cli
import trafficlab.preflight.stage as preflight
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import FailureOutcome, TrafficlabError


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


def _install_prepared(
    monkeypatch: pytest.MonkeyPatch,
    prepared: preflight.PreparedExperiment,
    report: preflight.PreflightReport,
) -> None:
    def open_prepared(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        return prepared

    def check_report(
        _config: ExperimentConfig, _docker: object, *, deadline: float, clock: Callable[[], float]
    ) -> preflight.PreflightReport:
        del _config, _docker, deadline, clock
        return report

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(preflight, "check_docker", check_report)


def _report(
    prepared: preflight.PreparedExperiment,
    findings: tuple[preflight.PreflightFinding, ...] = (),
    *,
    identity: preflight.CaptureEnvironmentIdentity | None = None,
) -> preflight.PreflightReport:
    return preflight.PreflightReport(
        config=prepared.config,
        findings=findings,
        environment_identity=identity,
    )


def test_run_preflight_attaches_direct_configuration_failure_and_returns_config_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path / "run")
    _install_prepared(monkeypatch, prepared, _report(prepared))

    assert preflight.run_preflight(tmp_path / "experiment.toml", config_only=True) is prepared

    source_error = TrafficlabError("target.argv is invalid", corrective_action="correct target")

    def fail_open(_path: Path, *, writable: object) -> preflight.PreparedExperiment:
        del writable
        raise source_error

    monkeypatch.setattr(preflight, "open_or_prepare_experiment", fail_open)
    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(tmp_path / "experiment.toml", config_only=True)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "configuration_invalid"

    existing = FailureOutcome(
        kind="configuration_invalid",
        stage="preflight",
        detail="already classified",
        affected_evidence="run evidence",
        evidence_state="not_published",
        corrective_action="correct target",
        authority="primary",
    )
    source_error = TrafficlabError("already classified", corrective_action="correct target", failure_outcome=existing)
    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(tmp_path / "experiment.toml", config_only=True)
    assert caught.value.failure_outcome == existing


def test_run_preflight_default_docker_and_deadline_failure_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path / "run")
    success = preflight.PreflightFinding("docker_engine", True, "Docker Engine is available")
    _install_prepared(monkeypatch, prepared, _report(prepared, (success,)))
    records: list[dict[str, object]] = []
    constructed: list[object] = []

    class DefaultDocker:
        def __init__(self, *, clock: Callable[[], float]) -> None:
            constructed.append(clock)

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(docker_cli, "DockerCompose", DefaultDocker)
    monkeypatch.setattr(preflight, "append_run_log", append)
    result = preflight.run_preflight(tmp_path / "experiment.toml", config_only=False, clock=lambda: 100.0)
    assert result.run_directory == prepared.run_directory
    assert len(constructed) == 1
    assert records == [
        {
            "detail": "Docker Engine is available",
            "event": "preflight_check",
            "name": "docker_engine",
            "ok": True,
            "stage": "preflight",
        }
    ]

    def arithmetic_clock() -> float:
        raise ArithmeticError("injected clock failure")

    with pytest.raises(TrafficlabError, match="could not calculate the Docker preflight deadline"):
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=arithmetic_clock,
        )
    with pytest.raises(TrafficlabError, match="finite future Docker preflight deadline"):
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: float("nan"),
        )


def test_run_preflight_uses_preceding_findings_for_probe_cleanup_and_keeps_log_secondary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path / "run")
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(preflight, "append_run_log", append)
    standalone = preflight.PreflightFinding("probe_cleanup", False, "probe cleanup failed", "remove project")
    _install_prepared(monkeypatch, prepared, _report(prepared, (standalone,)))
    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )
    assert [item.authority for item in caught.value.failure_outcomes] == ["primary"]
    assert records[-1]["failure_outcome"] == caught.value.failure_outcome.as_dict()  # type: ignore[union-attr]

    records.clear()
    primary = preflight.PreflightFinding("docker_engine", False, "engine unavailable", "restore engine")
    _install_prepared(monkeypatch, prepared, _report(prepared, (primary, standalone)))
    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )
    outcomes = caught.value.failure_outcomes
    assert [item.authority for item in outcomes] == ["primary", "secondary"]
    assert len(outcomes) == 2
    assert records[-1]["failure_outcome"] == outcomes[1].as_dict()

    def fail_log(_directory: Path, _record: dict[str, object]) -> None:
        raise TrafficlabError("run log unavailable", corrective_action="repair run log")

    monkeypatch.setattr(preflight, "append_run_log", fail_log)
    _install_prepared(monkeypatch, prepared, _report(prepared, (primary,)))
    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )
    outcomes = caught.value.failure_outcomes
    assert [item.kind for item in outcomes] == ["docker_preflight_failed", "publication_failed"]
    assert len(outcomes) == 2
    assert outcomes[1].affected_evidence == "run.log"


def test_run_preflight_identity_log_failure_and_existing_failure_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepared(tmp_path / "run")
    identity = cast(
        preflight.CaptureEnvironmentIdentity,
        SimpleNamespace(
            capture_content_id="capture-id",
            capture_reference="capture-ref",
            capture_tool_version="tool-version",
            host_architecture="linux/amd64",
            target_content_id="target-id",
            target_reference="target-ref",
        ),
    )
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(preflight, "append_run_log", append)
    _install_prepared(monkeypatch, prepared, _report(prepared, identity=identity))
    result = preflight.run_preflight(
        tmp_path / "experiment.toml",
        config_only=False,
        docker=cast(preflight.DockerPreflight, object()),
        clock=lambda: 100.0,
    )
    assert result.run_directory == prepared.run_directory
    assert result.report.environment_identity == identity
    assert records[-1]["event"] == "capture_environment_identity"

    def fail_identity(_directory: Path, record: dict[str, object]) -> None:
        if record["event"] == "capture_environment_identity":
            raise TrafficlabError("identity log unavailable", corrective_action="repair run log")
        records.append(record)

    monkeypatch.setattr(preflight, "append_run_log", fail_identity)
    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )
    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.affected_evidence == "run.log"

    established = FailureOutcome(
        kind="docker_preflight_failed",
        stage="preflight",
        detail="preclassified failure",
        affected_evidence="capture evidence",
        evidence_state="not_published",
        corrective_action="repair Docker",
        authority="primary",
    )
    error = TrafficlabError("preclassified failure", corrective_action="repair Docker", failure_outcome=established)

    def raise_established(_report: preflight.PreflightReport) -> None:
        raise error

    monkeypatch.setattr(preflight.PreflightReport, "require_success", raise_established)
    _install_prepared(monkeypatch, prepared, _report(prepared))
    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            tmp_path / "experiment.toml",
            config_only=False,
            docker=cast(preflight.DockerPreflight, object()),
            clock=lambda: 100.0,
        )
    assert caught.value.failure_outcome == established
