"""Запуск набора правил поверх общего отчёта проверки."""

from collections.abc import Callable, Iterable
from typing import Any

from trafficlab.contracts.validation import RuleResult, ValidationReport, ValidationStatus

Rule = Callable[[Any], RuleResult]


def validate(artifact_id: str, artifact: Any, rules: Iterable[Rule]) -> ValidationReport:
    results = list(rules)
    return ValidationReport(artifact_id, [rule(artifact) for rule in results])


def passing(rule_id: str, message: str = "") -> RuleResult:
    return RuleResult(rule_id, ValidationStatus.PASS, message)
