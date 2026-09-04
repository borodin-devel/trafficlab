"""Preflight preparation, reuse validation, and stage coordination."""

from __future__ import annotations

import json
import math
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

from trafficlab.artifacts.io import append_run_log
from trafficlab.artifacts.run_directory import create_run_directory
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import (
    ConfigurationPair,
    load_configuration_pair,
    load_experiment,
    render_effective_config,
)
from trafficlab.common.errors import (
    FailureAuthority,
    TrafficlabError,
    attach_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.preflight.docker import check_docker, finding_from_error, preflight_failure_outcome
from trafficlab.preflight.local import check_free_space, check_local, check_mounts, default_writable
from trafficlab.preflight.types import DockerPreflight, PreflightFinding, PreflightReport, PreparedExperiment, Writable

type ConfigurationGuard = Callable[[ConfigurationPair], None]


def _prepare_configuration_pair(path: Path, pair: ConfigurationPair, *, writable: Writable) -> PreparedExperiment:
    config = pair.realized
    report = check_local(config, writable=writable)
    report.require_success()
    run_directory = create_run_directory(config)
    return PreparedExperiment(
        source=path,
        portable_config=pair.portable,
        config=config,
        report=report,
        run_directory=run_directory,
    )


def prepare_experiment(path: Path, *, writable: Writable = default_writable) -> PreparedExperiment:
    """Load, locally validate, and publish a new experiment run directory."""
    return _prepare_configuration_pair(path, load_configuration_pair(path), writable=writable)


def _initial_run_records(run_directory: Path) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "event": "effective_config_published",
            "path": str(run_directory / "experiment.toml"),
            "stage": "preflight",
        },
        {"event": "run_prepared", "path": str(run_directory), "stage": "preflight"},
    )


def _validate_existing_run(config: ExperimentConfig) -> None:
    run_directory = config.run.directory
    snapshot_path = run_directory / "experiment.toml"
    log_path = run_directory / "run.log"
    try:
        expected_snapshot = render_effective_config(config)
        actual_snapshot = snapshot_path.read_bytes()
        if actual_snapshot != expected_snapshot:
            raise ValueError("experiment.toml bytes do not match the current effective configuration")
        if load_experiment(snapshot_path) != config:
            raise ValueError("experiment.toml does not parse as the current effective configuration")

        log_bytes = log_path.read_bytes()
        log_text = log_bytes.decode("utf-8", errors="strict")
        if not log_text.endswith("\n"):
            raise ValueError("run.log is not newline terminated")
        records: list[object] = [json.loads(line) for line in log_text.splitlines()]
        if len(records) < 2 or tuple(records[:2]) != _initial_run_records(run_directory):
            raise ValueError("run.log does not contain the required initial records")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("run.log contains a record that is not an object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TrafficlabError, ValueError) as error:
        raise TrafficlabError(
            f"existing run is not reusable: {error}",
            corrective_action="use the original matching experiment or choose a new run.directory",
        ) from error


def open_or_prepare_experiment(
    path: Path,
    *,
    writable: Writable = default_writable,
    configuration_guard: ConfigurationGuard | None = None,
) -> PreparedExperiment:
    """Prepare an absent run or reopen an exact, authoritative prepared run without mutation."""
    pair = load_configuration_pair(path)
    if configuration_guard is not None:
        configuration_guard(pair)
    config = pair.realized
    if not config.run.directory.exists():
        return _prepare_configuration_pair(path, pair, writable=writable)
    if not config.run.directory.is_dir():
        raise TrafficlabError(
            f"existing run is not reusable: run path is not a directory: {config.run.directory}",
            corrective_action="choose a new run.directory",
        )

    _validate_existing_run(config)
    if writable(config.run.directory):
        run_directory_finding = PreflightFinding(
            "run_directory",
            True,
            "existing prepared run matches the effective configuration and is writable",
        )
    else:
        run_directory_finding = PreflightFinding(
            "run_directory",
            False,
            f"existing run directory is not writable: {config.run.directory}",
            "make the existing run directory writable or choose a new run.directory",
        )
    report = PreflightReport(
        config=config,
        findings=(
            check_mounts(config),
            run_directory_finding,
            check_free_space(config, shutil.disk_usage),
        ),
    )
    report.require_success()
    return PreparedExperiment(
        source=path,
        portable_config=pair.portable,
        config=config,
        report=report,
        run_directory=config.run.directory,
    )


def run_preflight(
    path: Path,
    *,
    config_only: bool,
    docker: DockerPreflight | None = None,
    clock: Callable[[], float] = time.monotonic,
    writable: Writable = default_writable,
    configuration_guard: ConfigurationGuard | None = None,
) -> PreparedExperiment:
    """Run local preparation and, unless disabled, the injected Docker preflight."""
    try:
        if configuration_guard is None:
            prepared = open_or_prepare_experiment(path, writable=writable)
        else:
            prepared = open_or_prepare_experiment(
                path,
                writable=writable,
                configuration_guard=configuration_guard,
            )
    except TrafficlabError as error:
        if error.failure_outcome is None:
            outcome = failure_outcome_from_error(
                error,
                kind="configuration_invalid",
                stage="preflight",
                affected_evidence="run evidence",
                evidence_state="not_published",
            )
            error.failure_outcomes = (outcome,)
            error.failure_outcome = outcome
        raise
    if config_only:
        return prepared

    if docker is None:
        from trafficlab.capture.docker.compose import DockerCompose

        docker = cast(DockerPreflight, DockerCompose(clock=clock))
    try:
        started = clock()
        deadline = started + prepared.config.capture.total_timeout_seconds
    except ArithmeticError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                "could not calculate the Docker preflight deadline",
                corrective_action="use a finite monotonic clock and retry",
            ),
            kind="docker_preflight_failed",
            stage="preflight",
            affected_evidence="capture evidence",
            evidence_state="not_published",
        ) from error
    if not math.isfinite(started) or not math.isfinite(deadline) or deadline <= started:
        raise attach_failure_outcome(
            TrafficlabError(
                "could not calculate a finite future Docker preflight deadline",
                corrective_action="use a finite monotonic clock and positive total timeout",
            ),
            kind="docker_preflight_failed",
            stage="preflight",
            affected_evidence="capture evidence",
            evidence_state="not_published",
        )

    docker_report = check_docker(prepared.config, docker, deadline=deadline, clock=clock)
    prepared_findings = list(prepared.report.findings)
    docker_findings = list(docker_report.findings)
    findings = [*prepared_findings, *docker_findings]
    for index, finding in enumerate(docker_findings):
        try:
            record: dict[str, object] = {
                "detail": finding.detail,
                "event": "preflight_check",
                "name": finding.name,
                "ok": finding.ok,
                "stage": "preflight",
            }
            if not finding.ok:
                earlier_failure = any(not previous.ok for previous in (*prepared_findings, *docker_findings[:index]))
                authority: FailureAuthority = (
                    "secondary" if finding.name == "probe_cleanup" and earlier_failure else "primary"
                )
                record["failure_outcome"] = preflight_failure_outcome(finding, authority=authority).as_dict()
            append_run_log(prepared.run_directory, record)
        except TrafficlabError as error:
            findings.append(finding_from_error("run_log", error))
            break

    environment_identity = docker_report.environment_identity
    if environment_identity is not None and not any(
        finding.name == "run_log" and not finding.ok for finding in findings
    ):
        try:
            append_run_log(
                prepared.run_directory,
                {
                    "capture_content_id": environment_identity.capture_content_id,
                    "capture_reference": environment_identity.capture_reference,
                    "capture_tool_version": environment_identity.capture_tool_version,
                    "event": "capture_environment_identity",
                    "host_architecture": environment_identity.host_architecture,
                    "stage": "preflight",
                    "target_content_id": environment_identity.target_content_id,
                    "target_reference": environment_identity.target_reference,
                },
            )
        except TrafficlabError as error:
            findings.append(finding_from_error("run_log", error))

    report = PreflightReport(
        config=prepared.config,
        findings=tuple(findings),
        environment_identity=environment_identity,
    )
    try:
        report.require_success()
    except TrafficlabError as error:
        if error.failure_outcome is None:
            failed_findings = tuple(finding for finding in findings if not finding.ok)
            primary = preflight_failure_outcome(failed_findings[0])
            secondary = tuple(
                preflight_failure_outcome(finding, authority="secondary")
                for finding in failed_findings[1:]
                if finding.name in {"probe_cleanup", "run_log"}
            )
            error.failure_outcomes = (primary, *secondary)
            error.failure_outcome = primary
        raise
    return PreparedExperiment(
        source=prepared.source,
        portable_config=prepared.portable_config,
        config=prepared.config,
        report=report,
        run_directory=prepared.run_directory,
    )
