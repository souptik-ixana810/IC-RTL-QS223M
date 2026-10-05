#!/usr/bin/env python3
"""Regression tests for lint severity gating."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CI_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CI_DIR))

from run_lint_matrix import LintReport, lint_result_returncode  # noqa: E402


def make_report(*, fatals: int = 0, errors: int = 0, warnings: int = 0, infos: int = 0) -> LintReport:
    return LintReport(
        path=Path("report_hdl.top"),
        fatals=fatals,
        errors=errors,
        warnings=warnings,
        infos=infos,
        waived=0,
        reported_fatals=fatals,
        reported_errors=errors,
        reported_warnings=warnings,
        reported_infos=infos,
        ignored_external_violations=0,
        summary_text="",
    )


class LintSeverityGateTests(unittest.TestCase):
    def test_warnings_and_infos_do_not_fail(self) -> None:
        report = make_report(warnings=671, infos=83)
        self.assertEqual(report.blocking_violations, 0)
        self.assertEqual(lint_result_returncode(0, report), 0)

    def test_errors_and_fatals_fail(self) -> None:
        self.assertEqual(lint_result_returncode(0, make_report(errors=1)), 1)
        self.assertEqual(lint_result_returncode(0, make_report(fatals=1)), 1)

    def test_tool_or_report_failure_still_fails(self) -> None:
        self.assertEqual(lint_result_returncode(1, make_report()), 1)
        self.assertEqual(lint_result_returncode(0, None), 1)


if __name__ == "__main__":
    unittest.main()

