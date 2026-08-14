"""Pure lifecycle event arbitration and capture failure precedence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CaptureEvent(StrEnum):
    """Visible lifecycle events in their fixed arbitration order."""

    USER_INTERRUPTION = "user_interruption"
    NATURAL_TARGET_STOPPED = "natural_target_stopped"
    CAPTURE_STOPPED = "capture_stopped"
    STAGE_TIMEOUT = "stage_timeout"
    TOTAL_TIMEOUT = "total_timeout"


@dataclass(frozen=True, slots=True)
class EventObservation:
    """All events visible from one clock observation."""

    user_interruption: bool = False
    natural_target_status: int | None = None
    capture_stopped: bool = False
    stage_timeout: bool = False
    total_timeout: bool = False

    def __post_init__(self) -> None:
        for name in ("user_interruption", "capture_stopped", "stage_timeout", "total_timeout"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean")
        if self.natural_target_status is not None and type(self.natural_target_status) is not int:
            raise TypeError("natural_target_status must be an integer or None")


def choose_event(observation: EventObservation) -> CaptureEvent | None:
    """Choose one visible event using the architecture's fixed priority."""
    if observation.user_interruption:
        return CaptureEvent.USER_INTERRUPTION
    if observation.natural_target_status is not None:
        return CaptureEvent.NATURAL_TARGET_STOPPED
    if observation.capture_stopped:
        return CaptureEvent.CAPTURE_STOPPED
    if observation.stage_timeout:
        return CaptureEvent.STAGE_TIMEOUT
    if observation.total_timeout:
        return CaptureEvent.TOTAL_TIMEOUT
    return None


class FailureKind(StrEnum):
    """Kinds of failure or diagnostic status recorded during capture."""

    USER_INTERRUPTION = "user_interruption"
    TARGET_NONZERO_EXIT = "target_nonzero_exit"
    NATURAL_TARGET_STATUS = "natural_target_status"
    INDUCED_TARGET_STATUS = "induced_target_status"
    CAPTURE_STOPPED = "capture_stopped"
    STAGE_TIMEOUT = "stage_timeout"
    TOTAL_TIMEOUT = "total_timeout"
    FLUSH_FAILED = "flush_failed"
    VALIDATION_FAILED = "validation_failed"
    CLEANUP_FAILED = "cleanup_failed"


def _require_detail(detail: object) -> str:
    if type(detail) is not str:
        raise TypeError("detail must be a nonempty string")
    if not detail.strip():
        raise ValueError("detail must be a nonempty string")
    return detail


def _require_status(status: object) -> int:
    if type(status) is not int:
        raise TypeError("target status must be an integer")
    return status


@dataclass(frozen=True, slots=True)
class FailureDetail:
    """One immutable later failure or induced target status."""

    kind: FailureKind
    detail: str
    status: int | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not FailureKind:
            raise TypeError("failure kind must be a FailureKind")
        _require_detail(self.detail)
        status_kinds = (
            FailureKind.TARGET_NONZERO_EXIT,
            FailureKind.NATURAL_TARGET_STATUS,
            FailureKind.INDUCED_TARGET_STATUS,
        )
        if self.kind in status_kinds:
            if self.status is None:
                raise ValueError("target status detail requires an integer status")
            _require_status(self.status)
        elif self.status is not None:
            raise ValueError("only a target status detail may carry a status")


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """First primary failure plus later details in discovery order."""

    primary_kind: FailureKind | None = None
    primary_detail: str | None = None
    primary_status: int | None = None
    secondary_details: tuple[FailureDetail, ...] = ()

    def __post_init__(self) -> None:
        if type(self.secondary_details) is not tuple:
            raise TypeError("secondary_details must be a tuple")
        if not all(type(detail) is FailureDetail for detail in self.secondary_details):
            raise TypeError("secondary_details must contain only FailureDetail values")
        if self.primary_kind is None:
            if self.primary_detail is not None or self.primary_status is not None:
                raise ValueError("primary detail and status require a primary kind")
            if self.secondary_details:
                raise ValueError("secondary details require a primary failure")
        else:
            if type(self.primary_kind) is not FailureKind:
                raise TypeError("primary_kind must be a FailureKind or None")
            if self.primary_detail is None:
                raise ValueError("a primary failure requires a nonempty detail")
            _require_detail(self.primary_detail)
            if self.primary_kind in (FailureKind.NATURAL_TARGET_STATUS, FailureKind.INDUCED_TARGET_STATUS):
                raise ValueError("an observed target status cannot be primary")
            if self.primary_kind is FailureKind.TARGET_NONZERO_EXIT:
                if self.primary_status is None:
                    raise ValueError("target exit primary requires an integer status")
                _require_status(self.primary_status)
            elif self.primary_status is not None:
                raise ValueError("only a target exit primary may carry a status")


def _record_failure(
    outcome: CaptureOutcome,
    kind: FailureKind,
    detail: str,
    *,
    status: int | None = None,
) -> CaptureOutcome:
    validated_detail = _require_detail(detail)
    if status is not None:
        _require_status(status)
    if outcome.primary_kind is None:
        return CaptureOutcome(
            primary_kind=kind,
            primary_detail=validated_detail,
            primary_status=status,
            secondary_details=outcome.secondary_details,
        )
    secondary = FailureDetail(kind, validated_detail, status)
    return CaptureOutcome(
        primary_kind=outcome.primary_kind,
        primary_detail=outcome.primary_detail,
        primary_status=outcome.primary_status,
        secondary_details=(*outcome.secondary_details, secondary),
    )


def record_natural_target_status(outcome: CaptureOutcome, status: int) -> CaptureOutcome:
    """Record a naturally observed target status, failing only when nonzero."""
    validated_status = _require_status(status)
    if validated_status == 0:
        return outcome
    return _record_failure(
        outcome,
        FailureKind.TARGET_NONZERO_EXIT,
        f"target exited naturally with status {validated_status}",
        status=validated_status,
    )


def record_natural_target_observation(outcome: CaptureOutcome, status: int) -> CaptureOutcome:
    """Record a visible natural status, retaining zero when a higher-priority event is primary."""
    validated_status = _require_status(status)
    if outcome.primary_kind is None:
        return record_natural_target_status(outcome, validated_status)
    detail = FailureDetail(
        FailureKind.NATURAL_TARGET_STATUS,
        f"target was also observed naturally exited with status {validated_status}",
        validated_status,
    )
    return CaptureOutcome(
        primary_kind=outcome.primary_kind,
        primary_detail=outcome.primary_detail,
        primary_status=outcome.primary_status,
        secondary_details=(*outcome.secondary_details, detail),
    )


def record_induced_target_status(outcome: CaptureOutcome, status: int) -> CaptureOutcome:
    """Append the status observed after Trafficlab requested target termination."""
    validated_status = _require_status(status)
    target_killing_causes = (
        FailureKind.USER_INTERRUPTION,
        FailureKind.CAPTURE_STOPPED,
        FailureKind.STAGE_TIMEOUT,
        FailureKind.TOTAL_TIMEOUT,
    )
    if outcome.primary_kind not in target_killing_causes:
        raise ValueError("induced target status requires a target-killing primary failure")
    detail = FailureDetail(
        FailureKind.INDUCED_TARGET_STATUS,
        f"target exited after Trafficlab requested termination with status {validated_status}",
        validated_status,
    )
    return CaptureOutcome(
        primary_kind=outcome.primary_kind,
        primary_detail=outcome.primary_detail,
        primary_status=outcome.primary_status,
        secondary_details=(*outcome.secondary_details, detail),
    )


def record_capture_stopped(outcome: CaptureOutcome, detail: str) -> CaptureOutcome:
    """Record an unexpected capture-service exit."""
    return _record_failure(outcome, FailureKind.CAPTURE_STOPPED, detail)


def record_stage_timeout(outcome: CaptureOutcome, detail: str) -> CaptureOutcome:
    """Record a stage-specific deadline expiry."""
    return _record_failure(outcome, FailureKind.STAGE_TIMEOUT, detail)


def record_interruption(outcome: CaptureOutcome, detail: str) -> CaptureOutcome:
    """Record a user interruption."""
    return _record_failure(outcome, FailureKind.USER_INTERRUPTION, detail)


def record_total_timeout(outcome: CaptureOutcome, detail: str) -> CaptureOutcome:
    """Record total-run deadline expiry."""
    return _record_failure(outcome, FailureKind.TOTAL_TIMEOUT, detail)


def record_flush_failure(outcome: CaptureOutcome, detail: str) -> CaptureOutcome:
    """Record a failure while flushing the capture service."""
    return _record_failure(outcome, FailureKind.FLUSH_FAILED, detail)


def record_validation_failure(outcome: CaptureOutcome, detail: str) -> CaptureOutcome:
    """Record capture parsing or validation failure."""
    return _record_failure(outcome, FailureKind.VALIDATION_FAILED, detail)


def record_cleanup_failure(outcome: CaptureOutcome, detail: str) -> CaptureOutcome:
    """Record cleanup failure, primary only when the run otherwise succeeded."""
    return _record_failure(outcome, FailureKind.CLEANUP_FAILED, detail)
