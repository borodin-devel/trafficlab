"""Errors and canonical expected-failure evidence exposed by the trafficlab package."""

from dataclasses import dataclass
from typing import Literal, Self, cast

type EvidenceState = Literal["not_published", "diagnostic_only", "preserved", "possibly_remaining"]
type FailureAuthority = Literal["primary", "secondary"]
type FailureStatus = int | str | None

_EVIDENCE_STATES = frozenset(("not_published", "diagnostic_only", "preserved", "possibly_remaining"))
_FAILURE_AUTHORITIES = frozenset(("primary", "secondary"))


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


class TrafficlabError(Exception):
    """An expected trafficlab failure with a suggested corrective action."""

    def __init__(
        self,
        message: str,
        *,
        corrective_action: str,
        exit_code: int = 2,
        failure_outcome: FailureOutcome | None = None,
    ) -> None:
        super().__init__(message)
        self.corrective_action = corrective_action
        self.exit_code = exit_code
        if failure_outcome is not None and type(failure_outcome) is not FailureOutcome:
            raise TypeError("failure_outcome must be a FailureOutcome or None")
        self.failure_outcome = failure_outcome


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
        error.failure_outcome = failure_outcome_from_error(
            error,
            kind=kind,
            stage=stage,
            affected_evidence=affected_evidence,
            evidence_state=evidence_state,
            authority=authority,
            status=status,
        )
    return error


class DeadlineExceededError(TrafficlabError):
    """A structured signal that an absolute operation deadline has expired."""
