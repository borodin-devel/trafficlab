"""Exhaustive tests for pure capture lifecycle policy."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from itertools import combinations

import pytest

from trafficlab.capture.policy import (
    CaptureEvent,
    CaptureOutcome,
    EventObservation,
    FailureDetail,
    FailureKind,
    choose_event,
    record_capture_stopped,
    record_cleanup_failure,
    record_flush_failure,
    record_induced_target_status,
    record_interruption,
    record_natural_target_status,
    record_stage_timeout,
    record_total_timeout,
    record_validation_failure,
)

type Transition = Callable[[CaptureOutcome], CaptureOutcome]


_EVENT_FIELDS: tuple[tuple[CaptureEvent, str, object], ...] = (
    (CaptureEvent.USER_INTERRUPTION, "user_interruption", True),
    (CaptureEvent.NATURAL_TARGET_STOPPED, "natural_target_status", 0),
    (CaptureEvent.CAPTURE_STOPPED, "capture_stopped", True),
    (CaptureEvent.STAGE_TIMEOUT, "stage_timeout", True),
    (CaptureEvent.TOTAL_TIMEOUT, "total_timeout", True),
)


@pytest.mark.parametrize(
    ("higher", "higher_field", "higher_value", "lower", "lower_field", "lower_value"),
    [(*higher, *lower) for higher, lower in combinations(_EVENT_FIELDS, 2)],
    ids=[f"{higher[0].value}-before-{lower[0].value}" for higher, lower in combinations(_EVENT_FIELDS, 2)],
)
def test_choose_event_applies_every_priority_pair(
    higher: CaptureEvent,
    higher_field: str,
    higher_value: object,
    lower: CaptureEvent,
    lower_field: str,
    lower_value: object,
) -> None:
    del lower
    observation = EventObservation(**{higher_field: higher_value, lower_field: lower_value})  # type: ignore[arg-type]

    assert choose_event(observation) is higher


@pytest.mark.parametrize(
    ("event", "field", "value"),
    _EVENT_FIELDS,
    ids=[event.value for event, _field, _value in _EVENT_FIELDS],
)
def test_choose_event_returns_each_single_visible_event(event: CaptureEvent, field: str, value: object) -> None:
    observation = EventObservation(**{field: value})  # type: ignore[arg-type]

    assert choose_event(observation) is event


def test_choose_event_returns_none_when_nothing_is_visible() -> None:
    assert choose_event(EventObservation()) is None


def test_natural_target_stop_wins_target_capture_total_triple() -> None:
    observation = EventObservation(natural_target_status=-9, capture_stopped=True, total_timeout=True)

    assert choose_event(observation) is CaptureEvent.NATURAL_TARGET_STOPPED


def test_user_interruption_wins_when_all_events_are_visible() -> None:
    observation = EventObservation(
        user_interruption=True,
        natural_target_status=0,
        capture_stopped=True,
        stage_timeout=True,
        total_timeout=True,
    )

    assert choose_event(observation) is CaptureEvent.USER_INTERRUPTION


@pytest.mark.parametrize("status", [0, 1, -9, 2**100])
def test_event_observation_accepts_only_exact_integer_natural_target_statuses(status: int) -> None:
    observation = EventObservation(natural_target_status=status)

    assert observation.natural_target_status == status
    assert choose_event(observation) is CaptureEvent.NATURAL_TARGET_STOPPED


@pytest.mark.parametrize("status", [True, False, 0.0, "0", object()])
def test_event_observation_rejects_non_integer_natural_target_statuses(status: object) -> None:
    with pytest.raises(TypeError, match="natural_target_status must be an integer or None"):
        EventObservation(natural_target_status=status)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["user_interruption", "capture_stopped", "stage_timeout", "total_timeout"])
@pytest.mark.parametrize("value", [0, 1, None, "false"])
def test_event_observation_rejects_non_boolean_event_flags(field: str, value: object) -> None:
    with pytest.raises(TypeError, match=f"{field} must be a boolean"):
        EventObservation(**{field: value})  # type: ignore[arg-type]


def test_event_observation_is_immutable() -> None:
    observation = EventObservation()

    with pytest.raises(FrozenInstanceError):
        observation.total_timeout = True  # type: ignore[misc]


def test_kill_induced_status_cannot_be_presented_as_a_natural_event() -> None:
    outcome = record_stage_timeout(CaptureOutcome(), "workload stage timed out")
    status_observed_after_kill = 137

    assert choose_event(EventObservation()) is None
    with pytest.raises(TypeError, match="unexpected keyword argument 'target_status'"):
        EventObservation(target_status=status_observed_after_kill)  # type: ignore[call-arg]

    recorded = record_induced_target_status(outcome, status_observed_after_kill)
    assert recorded.primary_kind is FailureKind.STAGE_TIMEOUT
    assert recorded.secondary_details == (
        FailureDetail(
            FailureKind.INDUCED_TARGET_STATUS,
            "target exited after Trafficlab requested termination with status 137",
            137,
        ),
    )


def test_natural_target_zero_is_success_and_nonzero_is_primary() -> None:
    successful = record_natural_target_status(CaptureOutcome(), 0)
    failed = record_natural_target_status(successful, -17)

    assert successful == CaptureOutcome()
    assert failed.primary_kind is FailureKind.TARGET_NONZERO_EXIT
    assert failed.primary_detail == "target exited naturally with status -17"
    assert failed.primary_status == -17
    assert failed.secondary_details == ()


@pytest.mark.parametrize("status", [True, False, None, 1.0, "1"])
def test_target_status_transitions_reject_non_integer_values(status: object) -> None:
    with pytest.raises(TypeError, match="target status must be an integer"):
        record_natural_target_status(CaptureOutcome(), status)  # type: ignore[arg-type]
    primary = record_stage_timeout(CaptureOutcome(), "workload timed out")
    with pytest.raises(TypeError, match="target status must be an integer"):
        record_induced_target_status(primary, status)  # type: ignore[arg-type]


def _capture_failure(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_capture_stopped(outcome, "capture service exited unexpectedly")


def _stage_timeout(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_stage_timeout(outcome, "workload stage timed out")


def _interruption(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_interruption(outcome, "capture interrupted by user")


def _total_timeout(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_total_timeout(outcome, "total run deadline expired")


def _flush_failure(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_flush_failure(outcome, "capture flush failed")


def _validation_failure(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_validation_failure(outcome, "capture validation failed")


def _cleanup_failure(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_cleanup_failure(outcome, "Docker cleanup failed")


_PRIMARY_FAILURES: tuple[tuple[str, FailureKind, Transition], ...] = (
    ("capture", FailureKind.CAPTURE_STOPPED, _capture_failure),
    ("stage", FailureKind.STAGE_TIMEOUT, _stage_timeout),
    ("interruption", FailureKind.USER_INTERRUPTION, _interruption),
    ("total", FailureKind.TOTAL_TIMEOUT, _total_timeout),
    ("flush", FailureKind.FLUSH_FAILED, _flush_failure),
    ("validation", FailureKind.VALIDATION_FAILED, _validation_failure),
    ("cleanup", FailureKind.CLEANUP_FAILED, _cleanup_failure),
)


@pytest.mark.parametrize(
    ("name", "kind", "transition"),
    _PRIMARY_FAILURES,
    ids=[failure[0] for failure in _PRIMARY_FAILURES],
)
def test_each_direct_failure_is_primary_when_none_exists(
    name: str,
    kind: FailureKind,
    transition: Transition,
) -> None:
    del name
    outcome = transition(CaptureOutcome())

    assert outcome.primary_kind is kind
    assert outcome.primary_detail
    assert outcome.primary_status is None
    assert outcome.secondary_details == ()


@pytest.mark.parametrize(
    "cause",
    [_interruption, _capture_failure, _stage_timeout, _total_timeout],
    ids=["interruption", "capture", "stage", "total"],
)
def test_induced_target_status_is_secondary_after_each_target_killing_cause(cause: Transition) -> None:
    primary = cause(CaptureOutcome())
    outcome = record_induced_target_status(primary, -9)

    assert outcome.primary_kind is primary.primary_kind
    assert outcome.primary_detail == primary.primary_detail
    assert outcome.primary_status is None
    assert outcome.secondary_details == (
        FailureDetail(
            FailureKind.INDUCED_TARGET_STATUS,
            "target exited after Trafficlab requested termination with status -9",
            -9,
        ),
    )


@pytest.mark.parametrize(
    ("name", "outcome"),
    [
        ("empty", CaptureOutcome()),
        ("natural-target", record_natural_target_status(CaptureOutcome(), 7)),
        ("flush", _flush_failure(CaptureOutcome())),
        ("validation", _validation_failure(CaptureOutcome())),
        ("cleanup", _cleanup_failure(CaptureOutcome())),
    ],
    ids=["empty", "natural-target", "flush", "validation", "cleanup"],
)
def test_induced_target_status_rejects_outcomes_without_a_target_killing_cause(
    name: str,
    outcome: CaptureOutcome,
) -> None:
    del name
    with pytest.raises(ValueError, match="induced target status requires a target-killing primary failure"):
        record_induced_target_status(outcome, 137)


def test_natural_target_failure_remains_primary_through_flush_validation_and_cleanup() -> None:
    outcome = record_natural_target_status(CaptureOutcome(), 23)
    outcome = _flush_failure(outcome)
    outcome = _validation_failure(outcome)
    outcome = _cleanup_failure(outcome)

    assert outcome.primary_kind is FailureKind.TARGET_NONZERO_EXIT
    assert outcome.primary_status == 23
    assert tuple(detail.kind for detail in outcome.secondary_details) == (
        FailureKind.FLUSH_FAILED,
        FailureKind.VALIDATION_FAILED,
        FailureKind.CLEANUP_FAILED,
    )


def test_interruption_remains_primary_through_induced_exit_flush_and_cleanup() -> None:
    outcome = _interruption(CaptureOutcome())
    outcome = record_induced_target_status(outcome, 137)
    outcome = _flush_failure(outcome)
    outcome = _cleanup_failure(outcome)

    assert outcome.primary_kind is FailureKind.USER_INTERRUPTION
    assert tuple(detail.kind for detail in outcome.secondary_details) == (
        FailureKind.INDUCED_TARGET_STATUS,
        FailureKind.FLUSH_FAILED,
        FailureKind.CLEANUP_FAILED,
    )


def test_capture_failure_remains_primary_through_induced_exit_and_cleanup() -> None:
    outcome = _capture_failure(CaptureOutcome())
    outcome = record_induced_target_status(outcome, -9)
    outcome = _cleanup_failure(outcome)

    assert outcome.primary_kind is FailureKind.CAPTURE_STOPPED
    assert tuple(detail.kind for detail in outcome.secondary_details) == (
        FailureKind.INDUCED_TARGET_STATUS,
        FailureKind.CLEANUP_FAILED,
    )


def test_stage_timeout_remains_primary_through_induced_exit_total_timeout_and_cleanup() -> None:
    outcome = _stage_timeout(CaptureOutcome())
    outcome = record_induced_target_status(outcome, 137)
    outcome = _total_timeout(outcome)
    outcome = _cleanup_failure(outcome)

    assert outcome.primary_kind is FailureKind.STAGE_TIMEOUT
    assert tuple(detail.kind for detail in outcome.secondary_details) == (
        FailureKind.INDUCED_TARGET_STATUS,
        FailureKind.TOTAL_TIMEOUT,
        FailureKind.CLEANUP_FAILED,
    )


def test_total_timeout_remains_primary_through_induced_exit_and_cleanup() -> None:
    outcome = _total_timeout(CaptureOutcome())
    outcome = record_induced_target_status(outcome, 137)
    outcome = _cleanup_failure(outcome)

    assert outcome.primary_kind is FailureKind.TOTAL_TIMEOUT
    assert tuple(detail.kind for detail in outcome.secondary_details) == (
        FailureKind.INDUCED_TARGET_STATUS,
        FailureKind.CLEANUP_FAILED,
    )


def test_target_zero_allows_later_capture_stop_to_be_primary() -> None:
    outcome = record_natural_target_status(CaptureOutcome(), 0)
    outcome = record_capture_stopped(outcome, "capture stopped after target success")

    assert outcome.primary_kind is FailureKind.CAPTURE_STOPPED
    assert outcome.secondary_details == ()


@pytest.mark.parametrize(
    ("later_kind", "later"),
    [
        (FailureKind.FLUSH_FAILED, _flush_failure),
        (FailureKind.VALIDATION_FAILED, _validation_failure),
        (FailureKind.TOTAL_TIMEOUT, _total_timeout),
    ],
    ids=["flush", "validation", "total"],
)
def test_target_zero_allows_post_target_failure_to_be_primary(later_kind: FailureKind, later: Transition) -> None:
    outcome = later(record_natural_target_status(CaptureOutcome(), 0))

    assert outcome.primary_kind is later_kind
    assert outcome.secondary_details == ()


def test_stage_timeout_wins_simultaneous_total_timeout_and_remains_primary() -> None:
    observation = EventObservation(stage_timeout=True, total_timeout=True)
    outcome = _stage_timeout(CaptureOutcome())
    outcome = _total_timeout(outcome)

    assert choose_event(observation) is CaptureEvent.STAGE_TIMEOUT
    assert outcome.primary_kind is FailureKind.STAGE_TIMEOUT
    assert tuple(detail.kind for detail in outcome.secondary_details) == (FailureKind.TOTAL_TIMEOUT,)


def test_cleanup_failure_is_primary_only_for_an_otherwise_successful_outcome() -> None:
    after_success = _cleanup_failure(record_natural_target_status(CaptureOutcome(), 0))
    after_failure = _cleanup_failure(_stage_timeout(CaptureOutcome()))

    assert after_success.primary_kind is FailureKind.CLEANUP_FAILED
    assert after_failure.primary_kind is FailureKind.STAGE_TIMEOUT
    assert tuple(detail.kind for detail in after_failure.secondary_details) == (FailureKind.CLEANUP_FAILED,)


@pytest.mark.parametrize("detail", ["", " ", 1, None, True])
def test_failure_recorders_reject_invalid_details(detail: object) -> None:
    with pytest.raises((TypeError, ValueError), match="detail must be a nonempty string"):
        record_capture_stopped(CaptureOutcome(), detail)  # type: ignore[arg-type]


def test_outcomes_and_failure_details_are_immutable() -> None:
    outcome = _stage_timeout(CaptureOutcome())
    detail = FailureDetail(FailureKind.CLEANUP_FAILED, "cleanup failed")

    with pytest.raises(FrozenInstanceError):
        outcome.primary_kind = None  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        detail.status = 1  # type: ignore[misc]


def test_capture_outcome_rejects_inconsistent_primary_fields_and_mutable_secondaries() -> None:
    with pytest.raises(ValueError, match="primary detail and status require a primary kind"):
        CaptureOutcome(primary_detail="orphan detail")
    with pytest.raises(ValueError, match="a primary failure requires a nonempty detail"):
        CaptureOutcome(primary_kind=FailureKind.STAGE_TIMEOUT)
    with pytest.raises(TypeError, match="secondary_details must be a tuple"):
        CaptureOutcome(secondary_details=[])  # type: ignore[arg-type]


def test_capture_outcome_rejects_secondary_details_without_a_primary() -> None:
    induced = FailureDetail(FailureKind.INDUCED_TARGET_STATUS, "target stopped after kill", 137)

    with pytest.raises(ValueError, match="secondary details require a primary failure"):
        CaptureOutcome(secondary_details=(induced,))


def test_failure_detail_rejects_an_unknown_kind() -> None:
    with pytest.raises(TypeError, match="failure kind must be a FailureKind"):
        FailureDetail("stage_timeout", "stage timeout")  # type: ignore[arg-type]


def test_capture_outcome_rejects_an_unknown_primary_kind() -> None:
    with pytest.raises(TypeError, match="primary_kind must be a FailureKind or None"):
        CaptureOutcome(primary_kind="stage_timeout", primary_detail="stage timeout")  # type: ignore[arg-type]


def test_capture_outcome_rejects_non_detail_secondary_values() -> None:
    with pytest.raises(TypeError, match="secondary_details must contain only FailureDetail values"):
        CaptureOutcome(secondary_details=("cleanup failed",))  # type: ignore[arg-type]


def test_capture_outcome_requires_status_exactly_for_a_target_exit_primary() -> None:
    with pytest.raises(ValueError, match="target exit primary requires an integer status"):
        CaptureOutcome(primary_kind=FailureKind.TARGET_NONZERO_EXIT, primary_detail="target failed")
    with pytest.raises(ValueError, match="only a target exit primary may carry a status"):
        CaptureOutcome(primary_kind=FailureKind.STAGE_TIMEOUT, primary_detail="stage timeout", primary_status=9)
    with pytest.raises(ValueError, match="an observed target status cannot be primary"):
        CaptureOutcome(
            primary_kind=FailureKind.INDUCED_TARGET_STATUS,
            primary_detail="target was killed",
            primary_status=137,
        )
    with pytest.raises(ValueError, match="an observed target status cannot be primary"):
        CaptureOutcome(
            primary_kind=FailureKind.NATURAL_TARGET_STATUS,
            primary_detail="target was already stopped",
        )


def test_failure_detail_requires_status_exactly_for_target_status_kinds() -> None:
    with pytest.raises(ValueError, match="target status detail requires an integer status"):
        FailureDetail(FailureKind.INDUCED_TARGET_STATUS, "target was killed")
    with pytest.raises(ValueError, match="only a target status detail may carry a status"):
        FailureDetail(FailureKind.CLEANUP_FAILED, "cleanup failed", 1)
