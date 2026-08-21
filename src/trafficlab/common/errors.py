"""Errors and canonical expected-failure evidence exposed by the trafficlab package."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator

type EvidenceState = Literal["not_published", "diagnostic_only", "preserved", "possibly_remaining"]
type FailureAuthority = Literal["primary", "secondary"]
type FailureStatus = int | str | None

type FailureKind = Literal[
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
]
type FailureStage = Literal["preflight", "capture", "fit", "generate", "compare", "publication"]
type NonEmptyStrictString = Annotated[StrictStr, Field(min_length=1)]


class FailureOutcomeRecord(BaseModel):
    """One immutable, serializable expected-failure record."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    kind: FailureKind
    stage: FailureStage
    detail: NonEmptyStrictString
    affected_evidence: NonEmptyStrictString
    evidence_state: EvidenceState
    corrective_action: NonEmptyStrictString
    authority: FailureAuthority
    status: StrictInt | StrictStr | None = None

    @field_validator("detail", "affected_evidence", "corrective_action")
    @classmethod
    def string_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must be a nonempty string")
        return value

    @field_validator("status")
    @classmethod
    def status_string_is_not_blank(cls, value: int | str | None) -> int | str | None:
        if isinstance(value, str) and not value.strip():
            raise ValueError("status must be an exact integer, nonempty string, or None")
        return value

    def as_dict(self) -> dict[str, str | int]:
        """Return the canonical JSON-safe representation, omitting an absent status."""
        validated = type(self).model_validate(self.model_dump(mode="python"))
        document: dict[str, str | int] = {
            "affected_evidence": validated.affected_evidence,
            "authority": validated.authority,
            "corrective_action": validated.corrective_action,
            "detail": validated.detail,
            "evidence_state": validated.evidence_state,
            "kind": validated.kind,
            "stage": validated.stage,
        }
        if validated.status is not None:
            document["status"] = validated.status
        return document

    @classmethod
    def from_dict(cls, value: object) -> Self:
        """Strictly parse one fixture or persisted canonical failure outcome."""
        try:
            return cls.model_validate(value)
        except ValidationError as error:
            first = error.errors()[0]
            field = ".".join(str(part) for part in first["loc"])
            raise ValueError(f"invalid failure outcome canonical field {field}: {first['msg']}") from error

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


# The short name remains the construction and exception API while the explicit
# record name identifies the public schema root.
FailureOutcome = FailureOutcomeRecord


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
    return FailureOutcome.model_validate(
        {
            "kind": kind,
            "stage": stage,
            "detail": str(error),
            "affected_evidence": affected_evidence,
            "evidence_state": evidence_state,
            "corrective_action": error.corrective_action,
            "authority": authority,
            "status": status,
        }
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
