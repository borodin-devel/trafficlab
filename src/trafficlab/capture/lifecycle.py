"""Reference capture lifecycle ownership."""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from trafficlab.capture.cleanup import CleanupCompose
from trafficlab.capture.docker.types import CommandResult, ServiceState
from trafficlab.capture.policy import (
    CaptureFailureOrigin,
    CaptureOutcome,
    EventObservation,
    FailureKind,
    choose_event,
    record_capture_stopped,
    record_flush_failure,
    record_induced_target_status,
    record_interruption,
    record_natural_target_observation,
    record_stage_timeout,
    record_total_timeout,
    record_validation_failure,
)
from trafficlab.common.errors import (
    TrafficlabError,
)
from trafficlab.common.trace import load_capture_metadata
from trafficlab.preflight.stage import (
    DockerPreflight,
)

_PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"


class CaptureDocker(DockerPreflight, CleanupCompose, Protocol):
    """Docker operations used after full preflight."""

    def service_logs(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> str: ...

    def kill_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult: ...

    def kill_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult: ...


@contextmanager
def temporary_capture_directory(
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
        # Cleanup is secondary to the capture result.  Report it through the
        # caller's arbitration callback rather than replacing an active primary
        # failure while unwinding the context manager.
        try:
            temporary_directory.__exit__(None, None, None)
        except OSError as error:
            cleanup_failure(f"could not remove temporary capture directory: {error}")


def future_deadline(clock: Callable[[], float], seconds: float, *, stage: str) -> float:
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


def remember(states: dict[str, ServiceState], service: str, state: ServiceState | None) -> None:
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


def wait_readiness(
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
        remember(states, "capture", capture_state)
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


def observe_workload(
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
        remember(states, "target", target)
        remember(states, "capture", capture)
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


def flush_capture(
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
        remember(states, "capture", capture)
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


def interrupt_lifecycle(
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
            remember(states, "target", target)
            if target is not None and target.state == "exited":
                outcome = record_induced_target_status(outcome, target.exit_code)
    capture_state = states.get("capture")
    if capture_state is not None and capture_state.state == "running":
        flush_deadline = future_deadline(clock, flush_timeout_seconds, stage="flush")
        outcome = flush_capture(
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
