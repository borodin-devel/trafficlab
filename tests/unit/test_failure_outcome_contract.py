"""Strict contract coverage for canonical failure evidence."""

from __future__ import annotations

import json
from typing import cast

import pytest

from trafficlab.errors import FailureOutcome, TrafficlabError, append_failure_outcome, attach_failure_outcome


def _outcome(*, kind: str = "metric_infeasible", stage: str = "compare") -> FailureOutcome:
    return FailureOutcome(
        kind=kind,
        stage=stage,
        affected_evidence="similarity.json",
        evidence_state="not_published",
        detail="metric cannot be computed",
        corrective_action="correct the metric input and retry comparison",
        status=None,
        authority="primary",
    )


@pytest.mark.parametrize(("field", "value"), [("kind", "unknown_kind"), ("stage", "unknown_stage")])
def test_failure_outcome_rejects_unknown_canonical_vocabulary(field: str, value: str) -> None:
    values = _outcome().as_dict()
    values[field] = value

    with pytest.raises(ValueError, match="canonical"):
        FailureOutcome.from_dict(values)


def test_failure_outcome_from_json_rejects_duplicate_keys() -> None:
    document = (
        '{"kind":"metric_infeasible","kind":"artifact_corrupt","stage":"compare",'
        '"affected_evidence":"similarity.json","evidence_state":"not_published",'
        '"detail":"metric cannot be computed",'
        '"corrective_action":"correct the metric input and retry comparison",'
        '"status":null,"authority":"primary"}'
    )

    with pytest.raises(ValueError, match="duplicate key"):
        FailureOutcome.from_json(document)


@pytest.mark.parametrize(
    ("document", "error"),
    [
        (b"\xff", ValueError),
        (1, TypeError),
        ("{", ValueError),
        ("[]", TypeError),
    ],
)
def test_failure_outcome_from_json_rejects_invalid_raw_documents(document: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        FailureOutcome.from_json(document)  # type: ignore[arg-type]


def test_failure_outcome_strict_serializers_cover_optional_status_and_object_validation() -> None:
    outcome = FailureOutcome(
        kind="target_failed",
        stage="capture",
        affected_evidence="capture pair",
        evidence_state="diagnostic_only",
        detail="target exited naturally with status 23",
        corrective_action="inspect target status and log",
        status=23,
        authority="primary",
    )

    assert FailureOutcome.from_json(json.dumps(outcome.as_dict()).encode("utf-8")) == outcome
    with pytest.raises(TypeError, match="JSON object"):
        FailureOutcome.from_dict([])
    with pytest.raises(ValueError, match="exactly"):
        FailureOutcome.from_dict({"kind": "metric_infeasible"})
    with pytest.raises(TypeError, match="kind"):
        FailureOutcome.from_dict(
            {
                **_outcome().as_dict(),
                "kind": 1,
            }
        )


def test_trafficlab_error_retains_primary_and_secondary_outcomes_in_order() -> None:
    primary = _outcome()
    secondary = FailureOutcome(
        kind="cleanup_failed",
        stage="compare",
        affected_evidence="inventory",
        evidence_state="possibly_remaining",
        detail="owned cleanup did not complete",
        corrective_action="remove the named project resources after preserving diagnostics",
        status=None,
        authority="secondary",
    )
    error = TrafficlabError(
        "comparison failed",
        corrective_action="correct the comparison input and retry",
        failure_outcome=primary,
    )

    append_failure_outcome(error, secondary)

    assert error.failure_outcome == primary
    assert error.failure_outcomes == (primary, secondary)


def test_trafficlab_error_rejects_invalid_ordered_payloads_and_append_requires_a_primary() -> None:
    primary = _outcome()
    secondary = FailureOutcome(
        kind="cleanup_failed",
        stage="compare",
        affected_evidence="inventory",
        evidence_state="possibly_remaining",
        detail="owned cleanup did not complete",
        corrective_action="remove the named project",
        authority="secondary",
    )

    with pytest.raises(TypeError, match="failure_outcomes"):
        TrafficlabError(
            "invalid outcomes", corrective_action="repair", failure_outcomes=(cast(FailureOutcome, object()),)
        )
    with pytest.raises(TypeError, match="failure_outcome"):
        TrafficlabError("invalid primary", corrective_action="repair", failure_outcome=cast(FailureOutcome, object()))
    with pytest.raises(ValueError, match="match"):
        TrafficlabError(
            "mismatched outcomes", corrective_action="repair", failure_outcome=primary, failure_outcomes=(secondary,)
        )
    with pytest.raises(ValueError, match="first"):
        TrafficlabError("secondary first", corrective_action="repair", failure_outcomes=(secondary,))
    with pytest.raises(ValueError, match="secondary"):
        TrafficlabError("later primary", corrective_action="repair", failure_outcomes=(primary, primary))
    error = TrafficlabError("missing primary", corrective_action="repair")
    with pytest.raises(ValueError, match="primary"):
        append_failure_outcome(error, secondary)
    with pytest.raises(ValueError, match="primary"):
        append_failure_outcome(error, primary)
    error = TrafficlabError("primary established", corrective_action="repair", failure_outcome=primary)
    with pytest.raises(ValueError, match="secondary"):
        append_failure_outcome(error, primary)
    with pytest.raises(TypeError, match="outcome"):
        append_failure_outcome(error, cast(FailureOutcome, object()))


def test_attach_failure_outcome_cannot_establish_secondary_authority() -> None:
    error = TrafficlabError("missing outcome", corrective_action="repair")

    with pytest.raises(ValueError, match="primary"):
        attach_failure_outcome(
            error,
            kind="metric_infeasible",
            stage="compare",
            affected_evidence="similarity.json",
            evidence_state="not_published",
            authority="secondary",
        )

    primary = _outcome()
    established = TrafficlabError("established", corrective_action="repair", failure_outcome=primary)
    assert (
        attach_failure_outcome(
            established,
            kind="publication_failed",
            stage="compare",
            affected_evidence="similarity.json",
            evidence_state="not_published",
        )
        is established
    )
    assert established.failure_outcomes == (primary,)
