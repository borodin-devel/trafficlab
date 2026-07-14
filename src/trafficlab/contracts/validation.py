"""Общий результат проверки артефакта."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ValidationStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class RuleResult:
    rule_id: str
    status: ValidationStatus
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    artifact_id: str
    results: list[RuleResult] = field(default_factory=list)

    @property
    def status(self) -> ValidationStatus:
        if any(r.status is ValidationStatus.FAIL for r in self.results):
            return ValidationStatus.FAIL
        if any(r.status is ValidationStatus.WARN for r in self.results):
            return ValidationStatus.WARN
        return ValidationStatus.PASS
