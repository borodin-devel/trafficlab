import unittest

from trafficlab.contracts.validation import RuleResult, RuleSeverity, RuleStatus, ValidationReport, ValidationStatus


class ValidationReportTests(unittest.TestCase):
    def test_report_status_counts_errors_and_warnings(self):
        report = ValidationReport(
            "sha256:" + "a" * 64,
            [
                RuleResult("WARN-001", RuleStatus.FAIL, RuleSeverity.WARNING, observed=False, expected=True),
                RuleResult("SKIP-001", RuleStatus.SKIP, RuleSeverity.ERROR),
            ],
        )

        self.assertEqual(report.status, ValidationStatus.PASS_WITH_WARNINGS)
        self.assertEqual(report.errors, 0)
        self.assertEqual(report.warnings, 1)
        self.assertEqual(report.to_dict()["status"], "pass_with_warnings")
