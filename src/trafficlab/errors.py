"""Errors and canonical expected-failure evidence exposed by the trafficlab package."""

import json
from dataclasses import dataclass
from typing import Literal, Self, cast

type EvidenceState = Literal["not_published", "diagnostic_only", "preserved", "possibly_remaining"]
type FailureAuthority = Literal["primary", "secondary"]
type FailureStatus = int | str | None

_EVIDENCE_STATES = frozenset(("not_published", "diagnostic_only", "preserved", "possibly_remaining"))
_FAILURE_AUTHORITIES = frozenset(("primary", "secondary"))
_FAILURE_KINDS = frozenset(
    (
        "configuration_invalid",
        "docker_preflight_failed",
        "target_failed",
        "capture_failed",
        "stage_timeout",
        "interrupted",
        "capture_malformed",
        "artifact_missing",
        "artifact_changed",
        "artifact_foreign",
        "artifact_stale",
        "artifact_corrupt",
        "scientific_semantics_incompatible",
        "metric_infeasible",
        "generation_incomplete",
        "publication_collision",
        "publication_failed",
        "cleanup_failed",
    )
)
_FAILURE_STAGES = frozenset(("preflight", "capture", "fit", "generate", "compare", "publication"))


def _nonempty_string(value: object, *, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a nonempty string")
    if not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    """One immutable, serializable expected-failure record."""

    kind: str
    stage: str
    detail: str
    affected_evidence: str
    evidence_state: EvidenceState
    corrective_action: str
    authority: FailureAuthority
    status: FailureStatus = None

    def __post_init__(self) -> None:
        for name in ("kind", "stage", "detail", "affected_evidence", "corrective_action"):
            _nonempty_string(getattr(self, name), name=name)
        if self.kind not in _FAILURE_KINDS:
            raise ValueError(f"kind must be a canonical failure kind, got {self.kind!r}")
        if self.stage not in _FAILURE_STAGES:
            raise ValueError(f"stage must be a canonical failure stage, got {self.stage!r}")
        if self.evidence_state not in _EVIDENCE_STATES:
            raise ValueError("evidence_state must be a canonical evidence state")
        if self.authority not in _FAILURE_AUTHORITIES:
            raise ValueError("authority must be primary or secondary")
        if self.status is not None and (type(self.status) not in (int, str) or not str(self.status).strip()):
            raise TypeError("status must be an exact integer, nonempty string, or None")

    def as_dict(self) -> dict[str, str | int]:
        """Return the canonical JSON-safe representation, omitting an absent status."""
        document: dict[str, str | int] = {
            "affected_evidence": self.affected_evidence,
            "authority": self.authority,
            "corrective_action": self.corrective_action,
            "detail": self.detail,
            "evidence_state": self.evidence_state,
            "kind": self.kind,
            "stage": self.stage,
        }
        if self.status is not None:
            document["status"] = self.status
        return document

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Strictly parse one fixture or persisted canonical failure outcome."""
        if type(value) is not dict:
            raise TypeError("failure outcome must be a JSON object")
        document = cast(dict[str, object], value)
        expected = {
            "affected_evidence",
            "authority",
            "corrective_action",
            "detail",
            "evidence_state",
            "kind",
            "stage",
        }
        if set(document) not in (expected, expected | {"status"}):
            raise ValueError("failure outcome must contain exactly the canonical fields")
        return cls(
            kind=cast(str, document["kind"]),
            stage=cast(str, document["stage"]),
            detail=cast(str, document["detail"]),
            affected_evidence=cast(str, document["affected_evidence"]),
            evidence_state=cast(EvidenceState, document["evidence_state"]),
            corrective_action=cast(str, document["corrective_action"]),
            authority=cast(FailureAuthority, document["authority"]),
            status=cast(FailureStatus, document.get("status")),
        )

    @classmethod
    def from_json(cls, document: str | bytes) -> Self:
        """Strictly parse one raw canonical JSON object, rejecting duplicate keys."""
        if isinstance(document, bytes):
            try:
                document = document.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError("failure outcome JSON must be UTF-8") from error
        if type(document) is not str:
            raise TypeError("failure outcome JSON must be a string or bytes")
        try:
            parsed = json.loads(document, object_pairs_hook=_strict_json_object)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid failure outcome JSON: {error.msg}") from error
        return cls.from_dict(parsed)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"failure outcome JSON contains duplicate key {key!r}")
        document[key] = value
    return document


def failure_outcome_from_error(
    error: "TrafficlabError",
    *,
    kind: str,
    stage: str,
    affected_evidence: str,
    evidence_state: EvidenceState,
    authority: FailureAuthority = "primary",
    status: FailureStatus = None,
) -> FailureOutcome:
    """Preserve an existing structured error while rendering its canonical evidence record."""
    return FailureOutcome(
        kind=kind,
        stage=stage,
        detail=str(error),
        affected_evidence=affected_evidence,
        evidence_state=evidence_state,
        corrective_action=error.corrective_action,
        authority=authority,
        status=status,
    )


def _validate_failure_outcome_order(outcomes: tuple[FailureOutcome, ...]) -> None:
    """Require one primary outcome followed only by ordered secondary evidence."""
    if not outcomes:
        return
    if outcomes[0].authority != "primary":
        raise ValueError("the first failure outcome must have primary authority")
    if any(outcome.authority != "secondary" for outcome in outcomes[1:]):
        raise ValueError("later failure outcomes must have secondary authority")


class TrafficlabError(Exception):
    """An expected trafficlab failure with a suggested corrective action."""

    def __init__(
        self,
        message: str,
        *,
        corrective_action: str,
        exit_code: int = 2,
        failure_outcome: FailureOutcome | None = None,
        failure_outcomes: tuple[FailureOutcome, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.corrective_action = corrective_action
        self.exit_code = exit_code
        if failure_outcome is not None and type(failure_outcome) is not FailureOutcome:
            raise TypeError("failure_outcome must be a FailureOutcome or None")
        if failure_outcomes is not None and (
            type(failure_outcomes) is not tuple
            or any(type(outcome) is not FailureOutcome for outcome in failure_outcomes)
        ):
            raise TypeError("failure_outcomes must be a tuple of FailureOutcome values or None")
        outcomes = failure_outcomes
        if outcomes is None:
            outcomes = () if failure_outcome is None else (failure_outcome,)
        elif failure_outcome is not None and (not outcomes or outcomes[0] != failure_outcome):
            raise ValueError("failure_outcome must match the first failure_outcomes item")
        _validate_failure_outcome_order(outcomes)
        self.failure_outcomes = outcomes
        self.failure_outcome = outcomes[0] if outcomes else None


def attach_failure_outcome(
    error: TrafficlabError,
    *,
    kind: str,
    stage: str,
    affected_evidence: str,
    evidence_state: EvidenceState,
    authority: FailureAuthority = "primary",
    status: FailureStatus = None,
) -> TrafficlabError:
    """Attach one owning-boundary outcome without changing an established error's interface."""
    if error.failure_outcome is None:
        if authority != "primary":
            raise ValueError("an attached first failure outcome must have primary authority")
        outcome = failure_outcome_from_error(
            error,
            kind=kind,
            stage=stage,
            affected_evidence=affected_evidence,
            evidence_state=evidence_state,
            authority=authority,
            status=status,
        )
        error.failure_outcomes = (outcome,)
        error.failure_outcome = outcome
    return error


def append_failure_outcome(error: TrafficlabError, outcome: FailureOutcome) -> TrafficlabError:
    """Append secondary evidence without replacing the originating primary outcome."""
    if type(outcome) is not FailureOutcome:
        raise TypeError("outcome must be a FailureOutcome")
    if error.failure_outcome is None:
        raise ValueError("secondary failure evidence requires an existing primary outcome")
    if outcome.authority != "secondary":
        raise ValueError("appended failure evidence must have secondary authority")
    _validate_failure_outcome_order(error.failure_outcomes)
    error.failure_outcomes = (*error.failure_outcomes, outcome)
    return error


class DeadlineExceededError(TrafficlabError):
    """A structured signal that an absolute operation deadline has expired."""
