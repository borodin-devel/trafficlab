"""Bounded reference-capture lifecycle orchestration."""

from __future__ import annotations

import json
import math
import re
import stat
import tempfile
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
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
    record_mounted_input_incompatible,
    record_mounted_input_unavailable,
    record_natural_target_observation,
    record_snapshot_changed,
    record_stage_timeout,
    record_total_timeout,
    record_validation_failure,
)
from trafficlab.cleanup import CleanupCompose, cleanup_project
from trafficlab.compatibility import (
    ContentIdentity,
    identify_bytes,
    identify_directory,
    identify_file,
    require_compatible,
)
from trafficlab.compose import ComposePaths, write_production_compose
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.docker_cli import (
    CapturePlatform,
    CommandResult,
    DockerCompose,
    ProjectInventory,
    ServiceState,
    load_capture_image_lock,
)
from trafficlab.errors import (
    DeadlineExceededError,
    FailureAuthority,
    FailureOutcome,
    TrafficlabError,
    append_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.preflight import (
    CaptureEnvironmentIdentity,
    DockerPreflight,
    MountedInputIdentity,
    PreparedExperiment,
    run_preflight,
)
from trafficlab.trace import load_capture_metadata

_PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"
_CAPTURE_IMAGE_LOCK_PATH = Path(__file__).resolve().parents[2] / "docker" / "capture" / "image-lock.json"
_CAPTURE_ENVIRONMENT_FIELDS = (
    "host_architecture",
    "target_reference",
    "target_content_id",
    "capture_reference",
    "capture_content_id",
    "capture_tool_version",
    "mounted_inputs",
)
_CONTENT_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


class _SnapshotChangedError(TrafficlabError):
    """The realized snapshot changed after capture preparation."""


class _MountedInputUnavailableError(TrafficlabError):
    """A regular-file mounted input disappeared before validation."""


class _MountedInputIncompatibleError(TrafficlabError):
    """A regular-file mounted input no longer has its recorded identity."""


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


def _capture_environment_document(identity: CaptureEnvironmentIdentity) -> dict[str, object]:
    return {
        "host_architecture": identity.host_architecture,
        "target_reference": identity.target_reference,
        "target_content_id": identity.target_content_id,
        "capture_reference": identity.capture_reference,
        "capture_content_id": identity.capture_content_id,
        "capture_tool_version": identity.capture_tool_version,
        "mounted_inputs": [item.as_dict() for item in identity.mounted_inputs],
    }


def _mounted_input_name(target: str) -> str:
    return PurePosixPath(target).name or target


def _mounted_input_error(target: str, *, unavailable: bool) -> TrafficlabError:
    name = _mounted_input_name(target)
    if unavailable:
        error = _MountedInputUnavailableError(
            f"mounted input {name} is unavailable",
            corrective_action="restore the named mounted input bytes",
        )
    else:
        error = _MountedInputIncompatibleError(
            f"mounted input {name} is incompatible",
            corrective_action="restore the declared mounted-input content identity",
        )
    outcome = failure_outcome_from_error(
        error,
        kind="docker_preflight_failed",
        stage="preflight",
        affected_evidence="capture evidence",
        evidence_state="not_published",
    )
    error.failure_outcomes = (outcome,)
    error.failure_outcome = outcome
    return error


def _identify_mounted_inputs(config: ExperimentConfig) -> tuple[MountedInputIdentity, ...]:
    identities: list[MountedInputIdentity] = []
    for mount in config.target.mounts:
        if not mount.read_only:
            continue
        try:
            status = mount.source.stat(follow_symlinks=False)
        except OSError as error:
            raise _mounted_input_error(mount.target, unavailable=True) from error
        if stat.S_ISDIR(status.st_mode):
            identifier = identify_directory
        elif stat.S_ISREG(status.st_mode):
            identifier = identify_file
        else:
            raise _mounted_input_error(mount.target, unavailable=False)
        try:
            identity = identifier(mount.source)
        except TrafficlabError as error:
            try:
                current = mount.source.stat(follow_symlinks=False)
            except OSError:
                unavailable = True
            else:
                unavailable = stat.S_ISREG(current.st_mode) and isinstance(error.__cause__, OSError)
            raise _mounted_input_error(mount.target, unavailable=unavailable) from error
        identities.append(
            MountedInputIdentity(
                target=mount.target,
                read_only=mount.read_only,
                size=identity.size,
                sha256=identity.sha256,
            )
        )
    return tuple(identities)


def _require_matching_mounted_inputs(
    config: ExperimentConfig,
    expected: tuple[MountedInputIdentity, ...],
) -> tuple[MountedInputIdentity, ...]:
    current = _identify_mounted_inputs(config)
    if current == expected:
        return current
    mismatch_index = next(
        (index for index, pair in enumerate(zip(expected, current, strict=False)) if pair[0] != pair[1]),
        min(len(expected), len(current)),
    )
    if mismatch_index < len(expected):
        target = expected[mismatch_index].target
    else:
        target = current[mismatch_index].target
    raise _mounted_input_error(target, unavailable=False)


def _capture_lineage(
    run_directory: Path,
    environment_identity: CaptureEnvironmentIdentity,
    *,
    experiment_identity: ContentIdentity | None = None,
) -> dict[str, object]:
    if experiment_identity is None:
        experiment_identity = identify_file(run_directory / "experiment.toml")
    return {
        "experiment_identity": experiment_identity.as_dict(),
        "capture_identity": identify_file(run_directory / "capture.json").as_dict(),
        "reference_identity": identify_file(run_directory / "reference.pcapng").as_dict(),
        "capture_environment_identity": _capture_environment_document(environment_identity),
    }


def _require_unchanged_capture_snapshot(run_directory: Path, expected: ContentIdentity) -> None:
    try:
        require_compatible(
            {"experiment.toml": expected},
            {"experiment.toml": identify_file(run_directory / "experiment.toml")},
        )
    except TrafficlabError as error:
        changed = _SnapshotChangedError(
            "experiment.toml changed during capture",
            corrective_action="restore the prepared experiment snapshot and rerun capture",
        )
        outcome = failure_outcome_from_error(
            changed,
            kind="artifact_changed",
            stage="capture",
            affected_evidence="experiment.toml",
            evidence_state="preserved",
        )
        changed.failure_outcomes = (outcome,)
        changed.failure_outcome = outcome
        raise changed from error


def _require_unchanged_capture_inputs(
    run_directory: Path,
    experiment_identity: ContentIdentity,
    config: ExperimentConfig,
    mounted_inputs: tuple[MountedInputIdentity, ...],
) -> None:
    _require_unchanged_capture_snapshot(run_directory, experiment_identity)
    _require_matching_mounted_inputs(config, mounted_inputs)


def _capture_pair_stale_error() -> TrafficlabError:
    error = TrafficlabError(
        "capture pair has another identity",
        corrective_action="select its matching run or a new run directory",
    )
    outcome = FailureOutcome(
        kind="artifact_stale",
        stage="capture",
        detail=str(error),
        affected_evidence="capture pair",
        evidence_state="preserved",
        corrective_action=error.corrective_action,
        authority="primary",
    )
    error.failure_outcomes = (outcome,)
    error.failure_outcome = outcome
    return error


def _parse_capture_environment(value: object) -> CaptureEnvironmentIdentity:
    if type(value) is not dict:
        raise TypeError("capture environment identity must be an object")
    document = cast(dict[str, object], value)
    if set(document) != set(_CAPTURE_ENVIRONMENT_FIELDS):
        raise ValueError("capture environment identity fields are not canonical")
    mounted_value = document["mounted_inputs"]
    if type(mounted_value) is not list:
        raise TypeError("mounted_inputs must be an array")
    mounted_inputs = tuple(MountedInputIdentity.from_dict(item) for item in cast(list[object], mounted_value))
    string_fields = _CAPTURE_ENVIRONMENT_FIELDS[:-1]
    if any(type(document[name]) is not str or not cast(str, document[name]).strip() for name in string_fields):
        raise ValueError("capture environment identity strings must be nonempty")
    if document["host_architecture"] != "linux/amd64":
        raise ValueError("capture environment architecture is not canonical")
    for name in ("target_content_id", "capture_content_id"):
        if _CONTENT_ID_PATTERN.fullmatch(cast(str, document[name])) is None:
            raise ValueError(f"{name} is not a canonical content ID")
    return CaptureEnvironmentIdentity(
        host_architecture=cast(CapturePlatform, document["host_architecture"]),
        target_reference=cast(str, document["target_reference"]),
        target_content_id=cast(str, document["target_content_id"]),
        capture_reference=cast(str, document["capture_reference"]),
        capture_content_id=cast(str, document["capture_content_id"]),
        capture_tool_version=cast(str, document["capture_tool_version"]),
        mounted_inputs=mounted_inputs,
    )


def _parse_capture_lineage(
    record: dict[str, object],
) -> tuple[dict[str, object], CaptureEnvironmentIdentity]:
    environment = record.get("capture_environment_identity")
    parsed_environment = _parse_capture_environment(environment)
    parsed: dict[str, object] = {
        "experiment_identity": ContentIdentity.from_dict(
            record.get("experiment_identity"), name="experiment"
        ).as_dict(),
        "capture_identity": ContentIdentity.from_dict(record.get("capture_identity"), name="capture").as_dict(),
        "reference_identity": ContentIdentity.from_dict(record.get("reference_identity"), name="reference").as_dict(),
        "capture_environment_identity": _capture_environment_document(parsed_environment),
    }
    return parsed, parsed_environment


def _require_matching_capture_lineage(
    run_directory: Path,
    config: ExperimentConfig,
    environment_identity: CaptureEnvironmentIdentity | None,
) -> None:
    try:
        log_text = (run_directory / "run.log").read_bytes().decode("utf-8", errors="strict")
        records = [json.loads(line) for line in log_text.splitlines()]
        publications: list[dict[str, object]] = []
        for record in records:
            if type(record) is not dict:
                raise TypeError("run log record must be an object")
            document = cast(dict[str, object], record)
            if document.get("event") == "capture_published":
                publications.append(document)
        if len(publications) != 1:
            raise ValueError("capture publication lineage must occur exactly once")
        actual, recorded_environment = _parse_capture_lineage(publications[0])
        if recorded_environment.target_reference != config.target.image:
            raise ValueError("capture target reference differs from the realized configuration")
        if recorded_environment.capture_reference != config.capture.image:
            raise ValueError("capture image reference differs from the realized configuration")
        current_mounted_inputs = _require_matching_mounted_inputs(config, recorded_environment.mounted_inputs)
        expected_environment = recorded_environment
        if environment_identity is None:
            # Config-only reuse deliberately avoids Docker.  The checked lock is
            # therefore the independent authority that prevents a syntactically
            # valid run log from claiming a different capture image or tool.
            lock = load_capture_image_lock(_CAPTURE_IMAGE_LOCK_PATH)
            if (recorded_environment.capture_content_id, recorded_environment.capture_tool_version) != (
                lock.expected_capture_image_id,
                lock.capture_tool_version,
            ):
                raise ValueError("capture environment does not match the checked image lock")
        else:
            expected_environment = replace(environment_identity, mounted_inputs=current_mounted_inputs)
        expected = _capture_lineage(run_directory, expected_environment)
        require_compatible(expected, actual)
    except (_MountedInputUnavailableError, _MountedInputIncompatibleError):
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TrafficlabError, TypeError, ValueError) as error:
        raise _capture_pair_stale_error() from error


def _record_capture_input_failure(outcome: CaptureOutcome, error: TrafficlabError) -> CaptureOutcome:
    if isinstance(error, _SnapshotChangedError):
        return record_snapshot_changed(outcome, str(error))
    if isinstance(error, _MountedInputUnavailableError):
        return record_mounted_input_unavailable(outcome, str(error))
    if isinstance(error, _MountedInputIncompatibleError):
        return record_mounted_input_incompatible(outcome, str(error))
    return record_validation_failure(outcome, str(error))


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
        # Cleanup is secondary to the capture result.  Report it through the
        # caller's arbitration callback rather than replacing an active primary
        # failure while unwinding the context manager.
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
    stage = "capture"
    if kind in (FailureKind.TARGET_NONZERO_EXIT, FailureKind.NATURAL_TARGET_STATUS, FailureKind.INDUCED_TARGET_STATUS):
        assert status is not None
        detail = (
            f"target exited after Trafficlab requested termination with status {status}"
            if kind is FailureKind.INDUCED_TARGET_STATUS
            else f"target exited naturally with status {status}"
        )
        outcome_kind, evidence, state = "target_failed", "capture pair", "diagnostic_only"
        if authority == "primary" and has_capture_and_total:
            state = "not_published"
            corrective_action = "inspect target first, then capture and budget"
        elif authority == "primary" and has_cleanup:
            corrective_action = "inspect target then remove project"
        else:
            corrective_action = "inspect target status and log"
    elif kind is FailureKind.USER_INTERRUPTION:
        detail = "capture interrupted by user"
        outcome_kind, evidence, state = "interrupted", "capture pair", "diagnostic_only"
        status = 130
        corrective_action = "retry when ready"
    elif kind is FailureKind.CLEANUP_FAILED:
        detail = "capture cleanup timed out"
        outcome_kind, evidence, state = "cleanup_failed", "inventory", "possibly_remaining"
        status = None
        corrective_action = "remove the named project"
    elif kind is FailureKind.SNAPSHOT_CHANGED:
        outcome_kind, evidence, state = "artifact_changed", "experiment.toml", "preserved"
        status = None
        corrective_action = "restore the prepared experiment snapshot and rerun capture"
    elif kind is FailureKind.MOUNTED_INPUT_UNAVAILABLE:
        outcome_kind, evidence, state = "docker_preflight_failed", "capture evidence", "not_published"
        stage = "preflight"
        status = None
        corrective_action = "restore the named mounted input bytes"
    elif kind is FailureKind.MOUNTED_INPUT_INCOMPATIBLE:
        outcome_kind, evidence, state = "docker_preflight_failed", "capture evidence", "not_published"
        stage = "preflight"
        status = None
        corrective_action = "restore the declared mounted-input content identity"
    elif kind is FailureKind.CAPTURE_STOPPED:
        if status is not None:
            detail = (
                f"capture stopped with status {status} after natural target success"
                if authority == "primary" and natural_target_succeeded
                else f"capture stopped with status {status} while target remained active"
                if authority == "primary"
                else f"capture stopped with status {status}"
            )
        outcome_kind, evidence, state = "capture_failed", "capture pair", "not_published"
        corrective_action = (
            "inspect capture status without SIGINT or flush wait"
            if authority == "primary" and natural_target_succeeded
            else "inspect capture status and log"
        )
    elif kind is FailureKind.STAGE_TIMEOUT:
        detail = (
            "flush deadline expired after natural target success"
            if is_flush_timeout and natural_target_succeeded and FailureKind.TOTAL_TIMEOUT not in all_kinds
            else "flush deadline expired"
            if is_flush_timeout
            else "workload deadline expired"
        )
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
        if kind is FailureKind.TOTAL_TIMEOUT:
            detail = (
                "total-run deadline expired during validation"
                if origin is CaptureFailureOrigin.VALIDATION
                else "total-run deadline expired"
            )
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
        detail = "capture PCAPNG is malformed"
        outcome_kind, evidence, state = "capture_malformed", "capture pair", "diagnostic_only"
        corrective_action = "correct the capture producer"
    return FailureOutcome(
        kind=outcome_kind,
        stage=stage,
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
    outcome: CaptureOutcome,
    *,
    capture_status: int | None = None,
    natural_target_succeeded: bool = False,
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
    natural_target_succeeded = natural_target_succeeded or any(
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


def _try_reuse_prepared_capture(
    path: Path,
    prepared: PreparedExperiment,
    *,
    clock: Callable[[], float],
) -> tuple[CaptureResult | None, Path, ContentIdentity]:
    run_directory = _validate_prepared_capture(path, prepared)
    experiment_identity = identify_bytes(render_effective_config(prepared.config))
    existing = load_or_recover_capture_pair(run_directory, deadline=None, clock=clock)
    if existing is None:
        return None, run_directory, experiment_identity
    _require_matching_capture_lineage(
        run_directory,
        prepared.config,
        prepared.report.environment_identity,
    )
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
    return result, run_directory, experiment_identity


def capture_prepared_experiment(
    path: Path,
    prepared: PreparedExperiment,
    *,
    docker: CaptureDocker | None = None,
    clock: Callable[[], float] = time.monotonic,
    interruption: Callable[[], bool] = lambda: False,
) -> CaptureResult:
    """Capture an already-preflighted experiment or reuse its stable valid pair."""
    reused, run_directory, experiment_identity = _try_reuse_prepared_capture(path, prepared, clock=clock)
    if reused is not None:
        return reused

    environment_identity = prepared.report.environment_identity
    if environment_identity is None:
        raise TrafficlabError(
            "fresh capture requires resolved Docker image identities from full preflight",
            corrective_action="run full preflight without --config-only and retry capture",
        )

    if docker is None:
        docker = cast(CaptureDocker, DockerCompose(clock=clock))
    config = prepared.config
    environment_identity = replace(
        environment_identity,
        mounted_inputs=_identify_mounted_inputs(config),
    )
    project_name = f"trafficlab-capture-{uuid.uuid4().hex}"
    states: dict[str, ServiceState] = {}
    outcome = CaptureOutcome()
    total_deadline: float | None = None
    result: CaptureResult | None = None
    publication: CapturePublication | None = None
    target_may_exist = False
    project_may_exist = False
    natural_target_succeeded = False

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
                            _require_unchanged_capture_inputs(
                                run_directory,
                                experiment_identity,
                                config,
                                environment_identity.mounted_inputs,
                            )
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
                            outcome = _record_capture_input_failure(outcome, error)
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
                natural_target_succeeded = target_status == 0
                closed_capture = states.get("capture")
                capture_closed_cleanly = (
                    closed_capture is not None and closed_capture.state == "exited" and closed_capture.exit_code == 0
                )
                if capture_closed_cleanly:
                    operation = "validate and publish capture output"
                    try:
                        _require_unchanged_capture_inputs(
                            run_directory,
                            experiment_identity,
                            config,
                            environment_identity.mounted_inputs,
                        )
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
                        outcome = _record_capture_input_failure(outcome, error)
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
                    _require_unchanged_capture_inputs(
                        run_directory,
                        experiment_identity,
                        config,
                        environment_identity.mounted_inputs,
                    )
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
                    outcome = _record_capture_input_failure(outcome, error)
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
        primary_outcome, secondary_outcomes = _capture_failure_outcomes(
            outcome,
            capture_status=capture_status,
            natural_target_succeeded=natural_target_succeeded,
        )
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
        **_capture_lineage(run_directory, environment_identity, experiment_identity=experiment_identity),
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
    """Reuse after local preparation, otherwise run full preflight and capture."""
    locally_prepared = run_preflight(path, config_only=True, docker=docker, clock=clock)
    reused, _, _ = _try_reuse_prepared_capture(path, locally_prepared, clock=clock)
    if reused is not None:
        return reused
    prepared = run_preflight(path, config_only=False, docker=docker, clock=clock)
    return capture_prepared_experiment(
        path,
        prepared,
        docker=docker,
        clock=clock,
        interruption=interruption,
    )
