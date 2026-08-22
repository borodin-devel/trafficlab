"""Reference capture failures ownership."""

from __future__ import annotations

from pathlib import Path

from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.docker.types import CaptureLogOperations
from trafficlab.capture.policy import (
    CaptureFailureOrigin,
    CaptureOutcome,
    FailureKind,
    record_validation_failure,
)
from trafficlab.common.errors import (
    FailureAuthority,
    FailureOutcome,
    TrafficlabError,
)


def append_event(run_directory: Path, event: str, **detail: object) -> None:
    append_run_log(run_directory, {"event": event, "stage": "capture", **detail})


def outcome_error(outcome: CaptureOutcome) -> TrafficlabError:
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
        FailureOutcome.model_validate({**item.model_dump(mode="python"), "evidence_state": "not_published"})
        if item.affected_evidence == "capture pair"
        else item
        for item in outcomes
    )


def capture_failure_outcomes(
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


def capture_failure_logs(
    docker: CaptureLogOperations,
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
        append_event(
            run_directory,
            "capture_logs",
            detail=capture_logs,
            project_name=project_name,
        )
    except TrafficlabError as error:
        return record_validation_failure(outcome, f"could not record capture logs after {context}: {error}")
    return outcome
