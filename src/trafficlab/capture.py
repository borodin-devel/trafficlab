"""Bounded reference-capture lifecycle orchestration."""

from __future__ import annotations

import json
import math
import tempfile
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, cast

from trafficlab.artifacts import (
    CapturePublication,
    append_run_log,
    load_or_recover_capture_pair,
    publish_capture_pair,
    remove_stable_capture_diagnostics,
    rollback_capture_publication,
)
from trafficlab.capture_policy import (
    CaptureFailureOrigin,
    CaptureOutcome,
    EventObservation,
    FailureKind,
    choose_event,
    record_capture_stopped,
    record_cleanup_failure,
    record_flush_failure,
    record_induced_target_status,
    record_interruption,
    record_natural_target_observation,
    record_stage_timeout,
    record_total_timeout,
    record_validation_failure,
)
from trafficlab.cleanup import CleanupCompose, cleanup_project
from trafficlab.compose import ComposePaths, write_production_compose
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.docker_cli import CommandResult, DockerCompose, ProjectInventory, ServiceState
from trafficlab.errors import (
    DeadlineExceededError,
    FailureAuthority,
    FailureOutcome,
    TrafficlabError,
    append_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.preflight import DockerPreflight, PreparedExperiment, run_preflight
from trafficlab.trace import load_capture_metadata

_PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"


class CaptureDocker(DockerPreflight, CleanupCompose, Protocol):
    """Docker operations used after full preflight."""

    def service_logs(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> str: ...

    def kill_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult: ...

    def kill_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Published result of one successful reference capture."""

    run_directory: Path
    reference_path: Path
    packet_count: int
    target_status: int
    reused: bool = False

    def __post_init__(self) -> None:
        run_directory = cast(object, self.run_directory)
        reference_path = cast(object, self.reference_path)
        if not isinstance(run_directory, Path) or not run_directory.is_absolute():
            raise TypeError("run_directory must be an absolute Path")
        if not isinstance(reference_path, Path) or not reference_path.is_absolute():
            raise TypeError("reference_path must be an absolute Path")
        if type(self.packet_count) is not int:
            raise TypeError("packet_count must be a positive integer")
        if self.packet_count <= 0:
            raise ValueError("packet_count must be a positive integer")
        if type(self.target_status) is not int:
            raise TypeError("target_status must be an integer")
        if type(self.reused) is not bool:
            raise TypeError("reused must be a boolean")


def _append_event(run_directory: Path, event: str, **detail: object) -> None:
    append_run_log(run_directory, {"event": event, "stage": "capture", **detail})


@contextmanager
def _temporary_capture_directory(
    run_directory: Path,
    *,
    cleanup_failure: Callable[[str], None],
) -> Generator[str]:
    try:
        temporary_directory = tempfile.TemporaryDirectory(prefix=".trafficlab-capture-", dir=run_directory)
    except OSError as error:
        raise TrafficlabError(
            f"could not create temporary capture directory: {error}",
            corrective_action="verify the run directory is writable and retry capture",
        ) from error
    try:
        yield temporary_directory.__enter__()
    finally:
        try:
            temporary_directory.__exit__(None, None, None)
        except OSError as error:
            cleanup_failure(f"could not remove temporary capture directory: {error}")


def _future_deadline(clock: Callable[[], float], seconds: float, *, stage: str) -> float:
    try:
        started = clock()
        deadline = started + seconds
    except ArithmeticError as error:
        raise TrafficlabError(
            f"could not calculate the {stage} deadline",
            corrective_action="use a finite monotonic clock and retry capture",
        ) from error
    if not math.isfinite(started) or not math.isfinite(deadline) or deadline <= started:
        raise TrafficlabError(
            f"could not calculate a finite future {stage} deadline",
            corrective_action="use a finite monotonic clock and positive capture timeouts",
        )
    return deadline


def _inventory(
    states: dict[str, ServiceState],
    *,
    project_name: str,
    project_may_exist: bool,
) -> ProjectInventory:
    return ProjectInventory(
        containers=tuple(sorted(states.values(), key=lambda item: (item.service, item.name, item.identifier))),
        networks=(f"{project_name}_default",) if project_may_exist else (),
    )


def _remember(states: dict[str, ServiceState], service: str, state: ServiceState | None) -> None:
    if state is not None:
        states[service] = state


def _capture_ready(metadata_path: Path, pcapng_path: Path) -> bool:
    try:
        load_capture_metadata(metadata_path)
        with pcapng_path.open("rb") as stream:
            return stream.read(len(_PCAPNG_MAGIC)) == _PCAPNG_MAGIC
    except (OSError, TrafficlabError):
        return False


def _record_observation(
    outcome: CaptureOutcome,
    observation: EventObservation,
    *,
    interruption_detail: str,
    capture_detail: str,
    stage_detail: str,
    total_detail: str,
) -> CaptureOutcome:
    if observation.user_interruption:
        outcome = record_interruption(outcome, interruption_detail)
    deferred_natural_success = observation.natural_target_status == 0 and outcome.primary_kind is None
    if observation.natural_target_status is not None and not deferred_natural_success:
        outcome = record_natural_target_observation(outcome, observation.natural_target_status)
    if observation.capture_stopped:
        outcome = record_capture_stopped(outcome, capture_detail)
        if deferred_natural_success:
            outcome = record_natural_target_observation(outcome, 0)
            deferred_natural_success = False
    if observation.stage_timeout:
        outcome = record_stage_timeout(outcome, stage_detail)
        if deferred_natural_success:
            outcome = record_natural_target_observation(outcome, 0)
            deferred_natural_success = False
    if observation.total_timeout:
        outcome = record_total_timeout(outcome, total_detail)
        if deferred_natural_success:
            outcome = record_natural_target_observation(outcome, 0)
    return outcome


def _wait_readiness(
    docker: CaptureDocker,
    compose_path: Path,
    project_name: str,
    metadata_path: Path,
    pcapng_path: Path,
    states: dict[str, ServiceState],
    *,
    deadline: float,
    total_deadline: float = math.inf,
    clock: Callable[[], float],
    interruption: Callable[[], bool],
) -> CaptureOutcome:
    outcome = CaptureOutcome()
    while True:
        active_deadline = min(deadline, total_deadline)
        boundary_error: TrafficlabError | OSError | None = None
        try:
            capture_state = docker.service_state(compose_path, project_name, "capture", deadline=active_deadline)
        except (TrafficlabError, OSError) as error:
            capture_state = None
            boundary_error = error
        _remember(states, "capture", capture_state)
        ready = (
            boundary_error is None
            and capture_state is not None
            and capture_state.state == "running"
            and _capture_ready(metadata_path, pcapng_path)
        )
        now = clock()
        observation = EventObservation(
            user_interruption=interruption(),
            capture_stopped=boundary_error is None and (capture_state is None or capture_state.state != "running"),
            stage_timeout=now >= deadline,
            total_timeout=now >= total_deadline,
        )
        event = choose_event(observation)
        if boundary_error is not None and event is None:
            return record_validation_failure(outcome, f"could not inspect capture readiness state: {boundary_error}")
        if event is not None:
            outcome = _record_observation(
                outcome,
                observation,
                interruption_detail="capture interrupted during readiness",
                capture_detail="capture stopped before readiness",
                stage_detail="capture readiness timed out",
                total_detail="capture total-run deadline expired during readiness",
            )
            if boundary_error is not None:
                outcome = record_validation_failure(
                    outcome,
                    f"could not inspect capture readiness state: {boundary_error}",
                )
            return outcome
        if ready:
            return outcome


def _observe_workload(
    docker: CaptureDocker,
    compose_path: Path,
    project_name: str,
    states: dict[str, ServiceState],
    *,
    stage_deadline: float,
    total_deadline: float,
    clock: Callable[[], float],
    interruption: Callable[[], bool],
) -> tuple[CaptureOutcome, ServiceState | None]:
    outcome = CaptureOutcome()
    while True:
        active_deadline = min(stage_deadline, total_deadline)
        target: ServiceState | None = None
        capture: ServiceState | None = None
        capture_observed = False
        boundary_error: TrafficlabError | OSError | None = None
        try:
            target = docker.service_state(compose_path, project_name, "target", deadline=active_deadline)
            capture = docker.service_state(compose_path, project_name, "capture", deadline=active_deadline)
            capture_observed = True
        except (TrafficlabError, OSError) as error:
            boundary_error = error
        _remember(states, "target", target)
        _remember(states, "capture", capture)
        now = clock()
        observation = EventObservation(
            user_interruption=interruption(),
            natural_target_status=(target.exit_code if target is not None and target.state == "exited" else None),
            capture_stopped=capture_observed and (capture is None or capture.state != "running"),
            stage_timeout=now >= stage_deadline,
            total_timeout=now >= total_deadline,
        )
        event = choose_event(observation)
        last_known_capture = capture if capture_observed else states.get("capture")
        if boundary_error is not None and event is None:
            return (
                record_validation_failure(outcome, f"could not inspect target workload state: {boundary_error}"),
                last_known_capture,
            )
        if event is None:
            continue
        outcome = _record_observation(
            outcome,
            observation,
            interruption_detail="capture interrupted during target workload",
            capture_detail="capture stopped during target workload",
            stage_detail="target workload timed out",
            total_detail="capture total-run deadline expired",
        )
        if boundary_error is not None:
            outcome = record_validation_failure(outcome, f"could not inspect target workload state: {boundary_error}")
        return outcome, last_known_capture


def _record_flush_expiry(
    outcome: CaptureOutcome,
    *,
    now: float,
    stage_deadline: float,
    total_deadline: float,
) -> CaptureOutcome | None:
    stage_expired = now >= stage_deadline
    total_expired = now >= total_deadline
    if not stage_expired and not total_expired:
        return None
    if stage_expired:
        outcome = record_stage_timeout(
            outcome,
            "capture flush timed out",
            origin=CaptureFailureOrigin.FLUSH,
        )
    if total_expired:
        outcome = record_total_timeout(
            outcome,
            "capture total-run deadline expired during flush, so capture could not be killed",
            origin=CaptureFailureOrigin.FLUSH,
        )
    return outcome


def _kill_after_flush_timeout(
    docker: CaptureDocker,
    compose_path: Path,
    project_name: str,
    outcome: CaptureOutcome,
    *,
    now: float,
    total_deadline: float,
) -> CaptureOutcome:
    if now >= total_deadline:
        return outcome
    try:
        docker.kill_capture(compose_path, project_name, deadline=total_deadline)
    except (TrafficlabError, OSError) as error:
        return record_flush_failure(outcome, f"could not kill capture after flush timeout: {error}")
    return outcome


def _flush_capture(
    docker: CaptureDocker,
    compose_path: Path,
    project_name: str,
    states: dict[str, ServiceState],
    outcome: CaptureOutcome,
    *,
    stage_deadline: float,
    total_deadline: float,
    clock: Callable[[], float],
) -> CaptureOutcome:
    now = clock()
    expired = _record_flush_expiry(
        outcome,
        now=now,
        stage_deadline=stage_deadline,
        total_deadline=total_deadline,
    )
    if expired is not None:
        return _kill_after_flush_timeout(
            docker,
            compose_path,
            project_name,
            expired,
            now=now,
            total_deadline=total_deadline,
        )
    active_deadline = min(stage_deadline, total_deadline)
    try:
        docker.signal_capture(compose_path, project_name, deadline=active_deadline)
    except (TrafficlabError, OSError) as error:
        now = clock()
        expired = _record_flush_expiry(
            outcome,
            now=now,
            stage_deadline=stage_deadline,
            total_deadline=total_deadline,
        )
        if expired is not None:
            return _kill_after_flush_timeout(
                docker,
                compose_path,
                project_name,
                expired,
                now=now,
                total_deadline=total_deadline,
            )
        return record_flush_failure(outcome, f"could not signal capture during flush: {error}")
    while True:
        try:
            capture = docker.service_state(compose_path, project_name, "capture", deadline=active_deadline)
        except (TrafficlabError, OSError) as error:
            now = clock()
            expired = _record_flush_expiry(
                outcome,
                now=now,
                stage_deadline=stage_deadline,
                total_deadline=total_deadline,
            )
            if expired is not None:
                return _kill_after_flush_timeout(
                    docker,
                    compose_path,
                    project_name,
                    expired,
                    now=now,
                    total_deadline=total_deadline,
                )
            return record_flush_failure(outcome, f"could not inspect capture state during flush: {error}")
        _remember(states, "capture", capture)
        if capture is None:
            return record_flush_failure(outcome, "capture disappeared during flush")
        if capture.state == "exited":
            if capture.exit_code != 0:
                return record_flush_failure(outcome, f"capture exited with status {capture.exit_code} during flush")
            return outcome
        if capture.state != "running":
            return record_flush_failure(outcome, f"capture entered non-running state {capture.state!r} during flush")
        now = clock()
        expired = _record_flush_expiry(
            outcome,
            now=now,
            stage_deadline=stage_deadline,
            total_deadline=total_deadline,
        )
        if expired is not None:
            return _kill_after_flush_timeout(
                docker,
                compose_path,
                project_name,
                expired,
                now=now,
                total_deadline=total_deadline,
            )


def _outcome_error(outcome: CaptureOutcome) -> TrafficlabError:
    detail = outcome.primary_detail or "capture failed"
    if outcome.secondary_details:
        detail = f"{detail}; secondary: {'; '.join(item.detail for item in outcome.secondary_details)}"
    exit_code = 2
    if outcome.primary_kind is FailureKind.TARGET_NONZERO_EXIT:
        assert outcome.primary_status is not None
        exit_code = outcome.primary_status
    elif outcome.primary_kind is FailureKind.USER_INTERRUPTION:
        exit_code = 130
    return TrafficlabError(
        detail,
        corrective_action="inspect run.log, correct the capture failure, and retry",
        exit_code=exit_code,
    )


def _capture_failure_outcome(
    kind: FailureKind,
    detail: str,
    *,
    status: int | None,
    origin: CaptureFailureOrigin,
    authority: FailureAuthority,
    all_kinds: tuple[FailureKind, ...],
    natural_target_succeeded: bool,
) -> FailureOutcome:
    """Translate the complete existing capture arbitration result to its canonical evidence."""
    has_cleanup = FailureKind.CLEANUP_FAILED in all_kinds
    has_capture_and_total = FailureKind.CAPTURE_STOPPED in all_kinds and FailureKind.TOTAL_TIMEOUT in all_kinds
    is_flush_timeout = kind is FailureKind.FLUSH_FAILED or origin is CaptureFailureOrigin.FLUSH
    if kind in (FailureKind.TARGET_NONZERO_EXIT, FailureKind.NATURAL_TARGET_STATUS, FailureKind.INDUCED_TARGET_STATUS):
        outcome_kind, evidence, state = "target_failed", "capture pair", "diagnostic_only"
        if authority == "primary" and has_capture_and_total:
            state = "not_published"
            corrective_action = "inspect target first, then capture and budget"
        elif authority == "primary" and has_cleanup:
            corrective_action = "inspect target then remove project"
        else:
            corrective_action = "inspect target status and log"
    elif kind is FailureKind.USER_INTERRUPTION:
        outcome_kind, evidence, state = "interrupted", "capture pair", "diagnostic_only"
        status = 130
        corrective_action = "retry when ready"
    elif kind is FailureKind.CLEANUP_FAILED:
        outcome_kind, evidence, state = "cleanup_failed", "inventory", "possibly_remaining"
        status = None
        corrective_action = "remove the named project"
    elif kind is FailureKind.CAPTURE_STOPPED:
        outcome_kind, evidence, state = "capture_failed", "capture pair", "not_published"
        corrective_action = (
            "inspect capture status without SIGINT or flush wait"
            if authority == "primary" and natural_target_succeeded
            else "inspect capture status and log"
        )
    elif kind is FailureKind.STAGE_TIMEOUT:
        outcome_kind, evidence, state = "stage_timeout", "capture pair", "diagnostic_only"
        if is_flush_timeout:
            state = "not_published"
            corrective_action = (
                "correct flush then total budget"
                if FailureKind.TOTAL_TIMEOUT in all_kinds
                else "correct capture flush or budget"
            )
        elif FailureKind.INDUCED_TARGET_STATUS in all_kinds:
            corrective_action = "correct workload or timeout"
        else:
            corrective_action = "correct timeout or workload"
    elif kind in (FailureKind.TOTAL_TIMEOUT, FailureKind.FLUSH_FAILED):
        outcome_kind, evidence, state = "stage_timeout", "capture pair", "not_published"
        if kind is FailureKind.TOTAL_TIMEOUT:
            corrective_action = (
                "increase total budget"
                if (
                    FailureKind.STAGE_TIMEOUT in all_kinds
                    or FailureKind.FLUSH_FAILED in all_kinds
                    or FailureKind.CAPTURE_STOPPED in all_kinds
                )
                else "increase total budget or reduce validation input"
            )
        else:
            corrective_action = "correct capture flush or budget"
    else:
        outcome_kind, evidence, state = "capture_malformed", "capture pair", "diagnostic_only"
        corrective_action = "correct the capture producer"
    return FailureOutcome(
        kind=outcome_kind,
        stage="capture",
        detail=detail,
        affected_evidence=evidence,
        evidence_state=state,
        corrective_action=corrective_action,
        authority=authority,
        status=status,
    )


def _normalize_capture_pair_evidence_states(
    outcomes: tuple[FailureOutcome, ...],
) -> tuple[FailureOutcome, ...]:
    """Keep all records for one non-reusable capture pair in the same evidence state."""
    pair_is_not_reusable = any(
        item.affected_evidence == "capture pair" and item.evidence_state == "not_published" for item in outcomes
    )
    if not pair_is_not_reusable:
        return outcomes
    return tuple(
        replace(item, evidence_state="not_published") if item.affected_evidence == "capture pair" else item
        for item in outcomes
    )


def _capture_failure_outcomes(
    outcome: CaptureOutcome, *, capture_status: int | None = None
) -> tuple[FailureOutcome, tuple[FailureOutcome, ...]]:
    """Render one primary and every retained secondary capture failure in existing discovery order."""
    if outcome.primary_kind is None or outcome.primary_detail is None:
        raise ValueError("capture failure outcomes require an existing primary failure")
    all_details = (
        (outcome.primary_kind, outcome.primary_detail, outcome.primary_status, outcome.primary_origin),
    ) + tuple((item.kind, item.detail, item.status, item.origin) for item in outcome.secondary_details)
    rendered_details = tuple(
        item for item in all_details if not (item[0] is FailureKind.NATURAL_TARGET_STATUS and item[2] == 0)
    )
    all_kinds = tuple(item[0] for item in rendered_details)
    natural_target_succeeded = any(
        item.kind is FailureKind.NATURAL_TARGET_STATUS and item.status == 0 for item in outcome.secondary_details
    )
    primary_kind, primary_detail, primary_status, primary_origin = rendered_details[0]
    primary = _capture_failure_outcome(
        primary_kind,
        primary_detail,
        status=capture_status if primary_kind is FailureKind.CAPTURE_STOPPED else primary_status,
        origin=primary_origin,
        authority="primary",
        all_kinds=all_kinds,
        natural_target_succeeded=natural_target_succeeded,
    )
    secondary = tuple(
        _capture_failure_outcome(
            kind,
            detail,
            status=capture_status if kind is FailureKind.CAPTURE_STOPPED else status,
            origin=origin,
            authority="secondary",
            all_kinds=all_kinds,
            natural_target_succeeded=natural_target_succeeded,
        )
        for kind, detail, status, origin in rendered_details[1:]
    )
    normalized = _normalize_capture_pair_evidence_states((primary, *secondary))
    return normalized[0], normalized[1:]


def _capture_failure_logs(
    docker: CaptureDocker,
    compose_path: Path,
    project_name: str,
    run_directory: Path,
    outcome: CaptureOutcome,
    *,
    deadline: float,
    context: str,
) -> CaptureOutcome:
    """Collect and persist capture logs without replacing an earlier lifecycle failure."""
    try:
        capture_logs = docker.service_logs(compose_path, project_name, "capture", deadline=deadline)
    except (TrafficlabError, OSError) as error:
        return record_validation_failure(outcome, f"could not read capture logs after {context}: {error}")
    try:
        _append_event(
            run_directory,
            "capture_logs",
            detail=capture_logs,
            project_name=project_name,
        )
    except TrafficlabError as error:
        return record_validation_failure(outcome, f"could not record capture logs after {context}: {error}")
    return outcome


def _interrupt_lifecycle(
    docker: CaptureDocker,
    compose_path: Path,
    project_name: str,
    states: dict[str, ServiceState],
    outcome: CaptureOutcome,
    *,
    target_may_exist: bool,
    total_deadline: float,
    flush_timeout_seconds: float,
    clock: Callable[[], float],
) -> CaptureOutcome:
    if outcome.primary_kind is not FailureKind.USER_INTERRUPTION:
        outcome = record_interruption(outcome, "capture interrupted by user")
    if target_may_exist:
        try:
            docker.kill_target(compose_path, project_name, deadline=total_deadline)
        except (TrafficlabError, OSError) as error:
            outcome = record_validation_failure(outcome, f"could not kill target after interruption: {error}")
        try:
            target = docker.service_state(compose_path, project_name, "target", deadline=total_deadline)
        except (TrafficlabError, OSError) as error:
            outcome = record_validation_failure(outcome, f"could not inspect target after interruption: {error}")
        else:
            _remember(states, "target", target)
            if target is not None and target.state == "exited":
                outcome = record_induced_target_status(outcome, target.exit_code)
    capture_state = states.get("capture")
    if capture_state is not None and capture_state.state == "running":
        flush_deadline = _future_deadline(clock, flush_timeout_seconds, stage="flush")
        outcome = _flush_capture(
            docker,
            compose_path,
            project_name,
            states,
            outcome,
            stage_deadline=flush_deadline,
            total_deadline=total_deadline,
            clock=clock,
        )
    return outcome


def _validate_prepared_capture(path: Path, prepared: PreparedExperiment) -> Path:
    if type(prepared) is not PreparedExperiment:
        raise TypeError("prepared must be a PreparedExperiment")
    if prepared.source != path:
        raise TrafficlabError(
            f"prepared experiment source {prepared.source} does not match capture source {path}",
            corrective_action="pass the exact PreparedExperiment returned for this experiment path",
        )
    caller_config = load_experiment(path)
    if caller_config != prepared.config:
        raise TrafficlabError(
            "prepared effective configuration does not match the capture experiment",
            corrective_action="rerun full preflight for the exact experiment configuration",
        )
    run_directory = prepared.run_directory
    if run_directory != prepared.config.run.directory:
        raise TrafficlabError(
            "prepared run directory does not match the effective configuration",
            corrective_action="rerun full preflight for the exact experiment configuration",
        )

    snapshot_path = run_directory / "experiment.toml"
    log_path = run_directory / "run.log"
    try:
        if snapshot_path.read_bytes() != render_effective_config(prepared.config):
            raise ValueError("experiment.toml bytes do not match the prepared effective configuration")
        log_text = log_path.read_bytes().decode("utf-8", errors="strict")
        if not log_text.endswith("\n"):
            raise ValueError("run.log is not newline terminated")
        records = [json.loads(line) for line in log_text.splitlines()]
        expected_initial = (
            {
                "event": "effective_config_published",
                "path": str(snapshot_path),
                "stage": "preflight",
            },
            {"event": "run_prepared", "path": str(run_directory), "stage": "preflight"},
        )
        if len(records) < 2 or tuple(records[:2]) != expected_initial:
            raise ValueError("run.log does not contain the required initial records")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TrafficlabError, ValueError) as error:
        raise TrafficlabError(
            f"prepared capture inputs are not reusable: {error}",
            corrective_action="restore the exact prepared experiment snapshot and initial run log",
        ) from error
    return run_directory


def capture_prepared_experiment(
    path: Path,
    prepared: PreparedExperiment,
    *,
    docker: CaptureDocker | None = None,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], bool] = lambda: False,
) -> CaptureResult:
    """Capture an already-preflighted experiment or reuse its stable valid pair."""
    run_directory = _validate_prepared_capture(path, prepared)
    existing = load_or_recover_capture_pair(run_directory, deadline=None, clock=clock)
    if existing is not None:
        remove_stable_capture_diagnostics(run_directory)
        result = CaptureResult(
            run_directory=run_directory,
            reference_path=run_directory / "reference.pcapng",
            packet_count=existing.packet_count,
            target_status=0,
            reused=True,
        )
        _append_event(
            run_directory,
            "capture_reused",
            packet_count=result.packet_count,
            path=str(result.reference_path),
            reused=True,
        )
        return result

    environment_identity = prepared.report.environment_identity
    if environment_identity is None:
        raise TrafficlabError(
            "fresh capture requires resolved Docker image identities from full preflight",
            corrective_action="run full preflight without --config-only and retry capture",
        )

    if docker is None:
        docker = cast(CaptureDocker, DockerCompose(clock=clock))
    config = prepared.config
    project_name = f"trafficlab-capture-{uuid.uuid4().hex}"
    states: dict[str, ServiceState] = {}
    outcome = CaptureOutcome()
    total_deadline: float | None = None
    result: CaptureResult | None = None
    publication: CapturePublication | None = None
    target_may_exist = False
    project_may_exist = False

    def record_temporary_cleanup_failure(detail: str) -> None:
        nonlocal outcome
        outcome = record_validation_failure(outcome, detail)

    with _temporary_capture_directory(
        run_directory,
        cleanup_failure=record_temporary_cleanup_failure,
    ) as temporary:
        capture_directory = Path(temporary).resolve()
        compose_path = (capture_directory / "compose.json").resolve()
        metadata_path = capture_directory / "capture.json"
        pcapng_path = capture_directory / "reference.pcapng.tmp"
        write_production_compose(
            compose_path,
            config,
            ComposePaths(project_name=project_name, output_directory=capture_directory),
            target_image=environment_identity.target_content_id,
            capture_image=environment_identity.capture_content_id,
        )
        creation_deadline = _future_deadline(clock, config.capture.total_timeout_seconds, stage="project creation")
        operation = "record capture project creation"

        try:
            _append_event(run_directory, "capture_project_created", project_name=project_name)
            operation = "create capture service"
            project_may_exist = True
            docker.create_capture(compose_path, project_name, deadline=creation_deadline)
            total_deadline = _future_deadline(clock, config.capture.total_timeout_seconds, stage="total-run")
            operation = "start capture service"
            docker.start_capture(compose_path, project_name, deadline=total_deadline)
            readiness_deadline = _future_deadline(clock, config.capture.readiness_timeout_seconds, stage="readiness")
            operation = "wait for capture readiness"
            outcome = _wait_readiness(
                docker,
                compose_path,
                project_name,
                metadata_path,
                pcapng_path,
                states,
                deadline=readiness_deadline,
                total_deadline=total_deadline,
                clock=clock,
                interruption=interruption,
            )
            if outcome.primary_kind is not None:
                if outcome.primary_kind is FailureKind.USER_INTERRUPTION:
                    outcome = _interrupt_lifecycle(
                        docker,
                        compose_path,
                        project_name,
                        states,
                        outcome,
                        target_may_exist=False,
                        total_deadline=total_deadline,
                        flush_timeout_seconds=config.capture.flush_timeout_seconds,
                        clock=clock,
                    )
                    closed_capture = states.get("capture")
                    if (
                        closed_capture is not None
                        and closed_capture.state == "exited"
                        and closed_capture.exit_code == 0
                    ):
                        try:
                            publication = publish_capture_pair(
                                metadata_path,
                                pcapng_path,
                                run_directory,
                                target_success=False,
                                deadline=total_deadline,
                                clock=clock,
                            )
                        except DeadlineExceededError as error:
                            outcome = record_total_timeout(
                                outcome,
                                str(error),
                                origin=CaptureFailureOrigin.VALIDATION,
                            )
                        except TrafficlabError as error:
                            outcome = record_validation_failure(outcome, str(error))
                        else:
                            for warning in publication.warnings:
                                outcome = record_validation_failure(
                                    outcome,
                                    f"capture publication cleanup warning: {warning}",
                                )
                else:
                    outcome = _capture_failure_logs(
                        docker,
                        compose_path,
                        project_name,
                        run_directory,
                        outcome,
                        deadline=total_deadline,
                        context="readiness failure",
                    )
            if outcome.primary_kind is None:
                operation = "record capture readiness"
                _append_event(run_directory, "capture_ready", project_name=project_name)
                operation = "start target service"
                target_may_exist = True
                docker.start_target(compose_path, project_name, deadline=total_deadline)
                operation = "calculate target workload deadline"
                workload_deadline = _future_deadline(clock, config.capture.workload_timeout_seconds, stage="workload")
                operation = "observe target workload"
                outcome, capture_state = _observe_workload(
                    docker,
                    compose_path,
                    project_name,
                    states,
                    stage_deadline=workload_deadline,
                    total_deadline=total_deadline,
                    clock=clock,
                    interruption=interruption,
                )
                target = states.get("target")
                natural_target = target is not None and target.state == "exited"
                if not natural_target:
                    try:
                        docker.kill_target(compose_path, project_name, deadline=total_deadline)
                    except (TrafficlabError, OSError) as error:
                        outcome = record_validation_failure(
                            outcome,
                            f"could not kill target after capture stopped or workload ended: {error}",
                        )
                    try:
                        killed = docker.service_state(compose_path, project_name, "target", deadline=total_deadline)
                    except (TrafficlabError, OSError) as error:
                        outcome = record_validation_failure(
                            outcome,
                            f"could not inspect target after requested kill: {error}",
                        )
                    else:
                        _remember(states, "target", killed)
                        if killed is not None and killed.state == "exited":
                            outcome = record_induced_target_status(outcome, killed.exit_code)
                if outcome.primary_kind is FailureKind.CAPTURE_STOPPED:
                    outcome = _capture_failure_logs(
                        docker,
                        compose_path,
                        project_name,
                        run_directory,
                        outcome,
                        deadline=total_deadline,
                        context="capture stopped",
                    )
                if capture_state is not None and capture_state.state == "running":
                    operation = "flush capture output"
                    flush_deadline = _future_deadline(clock, config.capture.flush_timeout_seconds, stage="flush")
                    outcome = _flush_capture(
                        docker,
                        compose_path,
                        project_name,
                        states,
                        outcome,
                        stage_deadline=flush_deadline,
                        total_deadline=total_deadline,
                        clock=clock,
                    )
                target_status = target.exit_code if natural_target and target is not None else None
                closed_capture = states.get("capture")
                capture_closed_cleanly = (
                    closed_capture is not None and closed_capture.state == "exited" and closed_capture.exit_code == 0
                )
                if capture_closed_cleanly:
                    operation = "validate and publish capture output"
                    try:
                        publication = publish_capture_pair(
                            metadata_path,
                            pcapng_path,
                            run_directory,
                            target_success=target_status == 0,
                            deadline=total_deadline,
                            clock=clock,
                        )
                    except DeadlineExceededError as error:
                        outcome = record_total_timeout(
                            outcome,
                            str(error),
                            origin=CaptureFailureOrigin.VALIDATION,
                        )
                    except TrafficlabError as error:
                        outcome = record_validation_failure(outcome, str(error))
                    else:
                        for warning in publication.warnings:
                            outcome = record_validation_failure(
                                outcome,
                                f"capture publication cleanup warning: {warning}",
                            )
                        if target_status == 0 and outcome.primary_kind is None:
                            result = CaptureResult(
                                run_directory=run_directory,
                                reference_path=run_directory / "reference.pcapng",
                                packet_count=publication.inspection.packet_count,
                                target_status=target_status,
                            )
        except KeyboardInterrupt:
            active_deadline = total_deadline if total_deadline is not None else creation_deadline
            outcome = _interrupt_lifecycle(
                docker,
                compose_path,
                project_name,
                states,
                outcome,
                target_may_exist=target_may_exist,
                total_deadline=active_deadline,
                flush_timeout_seconds=config.capture.flush_timeout_seconds,
                clock=clock,
            )
            closed_capture = states.get("capture")
            if closed_capture is not None and closed_capture.state == "exited" and closed_capture.exit_code == 0:
                try:
                    publication = publish_capture_pair(
                        metadata_path,
                        pcapng_path,
                        run_directory,
                        target_success=False,
                        deadline=active_deadline,
                        clock=clock,
                    )
                except DeadlineExceededError as error:
                    outcome = record_total_timeout(
                        outcome,
                        str(error),
                        origin=CaptureFailureOrigin.VALIDATION,
                    )
                except TrafficlabError as error:
                    outcome = record_validation_failure(outcome, str(error))
                else:
                    for warning in publication.warnings:
                        outcome = record_validation_failure(
                            outcome,
                            f"capture publication cleanup warning: {warning}",
                        )
        except (TrafficlabError, OSError) as error:
            outcome = record_validation_failure(outcome, f"could not {operation}: {error}")
        finally:
            cleanup_deadline = total_deadline if total_deadline is not None else creation_deadline
            cleanup = cleanup_project(
                docker,
                compose_path,
                project_name,
                _inventory(states, project_name=project_name, project_may_exist=project_may_exist),
                deadline=cleanup_deadline,
                clock=clock,
            )
            if not cleanup.success:
                outcome = record_cleanup_failure(outcome, cleanup.detail)
                for secondary_detail in cleanup.secondary_details:
                    outcome = record_cleanup_failure(outcome, f"additional cleanup failure: {secondary_detail}")

    if publication is not None and publication.created_by_call and outcome.primary_kind is not None:
        try:
            rollback_capture_publication(run_directory, publication)
        except TrafficlabError as error:
            outcome = record_validation_failure(
                outcome,
                f"could not roll back owned capture publication after capture failure: {error}",
            )
        result = None

    if outcome.primary_kind is not None:
        capture_state = states.get("capture")
        capture_status = (
            capture_state.exit_code if capture_state is not None and capture_state.state == "exited" else None
        )
        primary_outcome, secondary_outcomes = _capture_failure_outcomes(outcome, capture_status=capture_status)
        error = _outcome_error(outcome)
        error.failure_outcomes = (primary_outcome, *secondary_outcomes)
        error.failure_outcome = primary_outcome
        try:
            _append_event(
                run_directory,
                "capture_failed",
                detail=outcome.primary_detail,
                failure_kind=outcome.primary_kind.value,
                failure_outcome=primary_outcome.as_dict(),
                primary_status=outcome.primary_status,
                secondary_details=[item.detail for item in outcome.secondary_details],
                secondary_failures=[
                    {"detail": item.detail, "kind": item.kind.value, "status": item.status}
                    for item in outcome.secondary_details
                ],
                secondary_outcomes=[item.as_dict() for item in secondary_outcomes],
            )
        except TrafficlabError as logging_error:
            append_failure_outcome(
                error,
                failure_outcome_from_error(
                    logging_error,
                    kind="publication_failed",
                    stage="capture",
                    affected_evidence="run.log",
                    evidence_state="not_published",
                    authority="secondary",
                ),
            )
            error.args = (f"{error}; additionally could not append capture failure to run.log: {logging_error}",)
        raise error
    if result is None:
        raise TrafficlabError(
            "capture completed without a reusable reference",
            corrective_action="inspect run.log and retry capture",
        )
    remove_stable_capture_diagnostics(run_directory)
    _append_event(
        run_directory,
        "capture_published",
        packet_count=result.packet_count,
        path=str(result.reference_path),
        project_name=project_name,
        reused=False,
    )
    return result


def capture_experiment(
    path: Path,
    *,
    docker: CaptureDocker | None = None,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], bool] = lambda: False,
) -> CaptureResult:
    """Run full preflight once, then execute the prepared capture core."""
    prepared = run_preflight(path, config_only=False, docker=docker, clock=clock)
    return capture_prepared_experiment(
        path,
        prepared,
        docker=docker,
        clock=clock,
        interruption=interruption,
    )
