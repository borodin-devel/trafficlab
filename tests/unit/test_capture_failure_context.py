from __future__ import annotations

from collections.abc import Callable

import pytest

from trafficlab.capture import _capture_failure_outcomes  # pyright: ignore[reportPrivateUsage]
from trafficlab.capture_policy import (
    CaptureFailureOrigin,
    CaptureOutcome,
    record_capture_stopped,
    record_cleanup_failure,
    record_natural_target_status,
    record_stage_timeout,
    record_total_timeout,
)


@pytest.mark.parametrize(
    ("origin", "expected_action", "expected_state"),
    [
        (CaptureFailureOrigin.WORKLOAD, "correct timeout or workload", "diagnostic_only"),
        (CaptureFailureOrigin.FLUSH, "correct capture flush or budget", "not_published"),
    ],
)
def test_stage_timeout_uses_structured_origin_not_detail_text(
    origin: CaptureFailureOrigin, expected_action: str, expected_state: str
) -> None:
    outcome = record_stage_timeout(
        CaptureOutcome(),
        "flush deadline expired after natural target success",
        origin=origin,
    )

    primary, secondary = _capture_failure_outcomes(outcome)

    assert secondary == ()
    assert primary.kind == "stage_timeout"
    assert primary.corrective_action == expected_action
    assert primary.evidence_state == expected_state


type CaptureFailure = Callable[[CaptureOutcome], CaptureOutcome]


def _after_capture_stopped(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_capture_stopped(outcome, "capture stopped")


def _after_flush_timeout(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_stage_timeout(outcome, "timeout", origin=CaptureFailureOrigin.FLUSH)


def _after_total_timeout(outcome: CaptureOutcome) -> CaptureOutcome:
    return record_total_timeout(outcome, "timeout")


_LATER_CAPTURE_FAILURES: tuple[tuple[CaptureFailure, str, str], ...] = (
    (_after_capture_stopped, "not_published", "inspect target status and log"),
    (_after_flush_timeout, "not_published", "inspect target status and log"),
    (_after_total_timeout, "not_published", "inspect target status and log"),
)


@pytest.mark.parametrize(
    ("failure", "expected_state", "expected_action"),
    _LATER_CAPTURE_FAILURES,
)
def test_target_primary_is_not_published_with_one_later_nonreusable_capture_failure(
    failure: CaptureFailure, expected_state: str, expected_action: str
) -> None:
    outcome = record_natural_target_status(CaptureOutcome(), 23)
    completed = failure(outcome)

    primary, secondary = _capture_failure_outcomes(completed)

    assert primary.kind == "target_failed"
    assert primary.evidence_state == expected_state
    assert primary.corrective_action == expected_action
    assert primary.status == 23
    assert primary.authority == "primary"
    assert [item.evidence_state for item in secondary] == [expected_state]
    assert [item.authority for item in secondary] == ["secondary"]


def test_target_primary_becomes_not_published_only_for_capture_stop_and_total_timeout() -> None:
    outcome = record_natural_target_status(CaptureOutcome(), 23)
    outcome = record_capture_stopped(outcome, "capture stopped")
    outcome = record_total_timeout(outcome, "total timeout")

    primary, secondary = _capture_failure_outcomes(outcome)

    assert primary.evidence_state == "not_published"
    assert primary.corrective_action == "inspect target first, then capture and budget"
    assert [item.kind for item in secondary] == ["capture_failed", "stage_timeout"]


def test_target_primary_keeps_cleanup_as_diagnostic_secondary() -> None:
    outcome = record_natural_target_status(CaptureOutcome(), 23)
    outcome = record_cleanup_failure(outcome, "cleanup timed out")

    primary, secondary = _capture_failure_outcomes(outcome)

    assert primary.evidence_state == "diagnostic_only"
    assert primary.corrective_action == "inspect target then remove project"
    assert secondary[0].kind == "cleanup_failed"


def test_nonreusable_capture_pair_does_not_reclassify_cleanup_inventory() -> None:
    outcome = record_natural_target_status(CaptureOutcome(), 23)
    outcome = record_capture_stopped(outcome, "capture stopped")
    outcome = record_cleanup_failure(outcome, "cleanup timed out")

    primary, secondary = _capture_failure_outcomes(outcome)

    assert primary.evidence_state == "not_published"
    assert primary.corrective_action == "inspect target then remove project"
    assert primary.status == 23
    assert primary.authority == "primary"
    assert [(item.kind, item.evidence_state, item.authority) for item in secondary] == [
        ("capture_failed", "not_published", "secondary"),
        ("cleanup_failed", "possibly_remaining", "secondary"),
    ]


def test_capture_failure_outcomes_require_a_primary_failure() -> None:
    with pytest.raises(ValueError, match="existing primary"):
        _capture_failure_outcomes(CaptureOutcome())
