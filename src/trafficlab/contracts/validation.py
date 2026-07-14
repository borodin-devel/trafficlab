"""Контракт общего отчёта проверки `validation.json`."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ValidationStatus(StrEnum):
    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL = "fail"

    # Backwards-compatible alias used by the initial tests.
    WARN = "pass_with_warnings"


class RuleStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class RuleSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class RuleResult:
    id: str
    status: RuleStatus | ValidationStatus
    severity: RuleSeverity = RuleSeverity.ERROR
    observed: Any = None
    expected: Any = None
    message: str = ""

    @property
    def rule_id(self) -> str:
        return self.id

    def __post_init__(self) -> None:
        if isinstance(self.status, ValidationStatus):
            object.__setattr__(self, "status", RuleStatus.FAIL if self.status is ValidationStatus.FAIL else RuleStatus.PASS)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["severity"] = self.severity.value
        return value


@dataclass
class ValidationReport:
    validated_artifact_id: str
    rules: list[RuleResult] = field(default_factory=list)
    profile: str = "generic-v1"
    summary: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0.0"

    @property
    def errors(self) -> int:
        return sum(1 for rule in self.rules if rule.status is RuleStatus.FAIL and rule.severity is RuleSeverity.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for rule in self.rules if rule.status is RuleStatus.FAIL and rule.severity is RuleSeverity.WARNING)

    @property
    def status(self) -> ValidationStatus:
        if self.errors:
            return ValidationStatus.FAIL
        if self.warnings:
            return ValidationStatus.PASS_WITH_WARNINGS
        return ValidationStatus.PASS

    # Backwards-compatible names from the initial skeleton.
    @property
    def artifact_id(self) -> str:
        return self.validated_artifact_id

    @property
    def results(self) -> list[RuleResult]:
        return self.rules

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "validated_artifact_id": self.validated_artifact_id,
            "status": self.status.value,
            "profile": self.profile,
            "rules": [rule.to_dict() for rule in self.rules],
            "errors": self.errors,
            "warnings": self.warnings,
            "summary": self.summary,
        }

    def validate(self) -> None:
        value = self.to_dict()
        if value["status"] == ValidationStatus.FAIL.value and value["errors"] == 0:
            raise ValueError("fail status requires errors > 0")
        if value["status"] == ValidationStatus.PASS.value and (value["errors"] or value["warnings"]):
            raise ValueError("pass status requires zero errors and warnings")
