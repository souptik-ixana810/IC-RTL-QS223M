from pathlib import Path
import unittest

from run_cdc_matrix import count_repo_violations


class CdcIgnoredReportTests(unittest.TestCase):
    def test_remaining_errors_in_section_with_ignored_findings(self):
        report = """
  CDC_UNSYNC_NOSCHEME  (2 errors/1 waived/3 ignored)
  FileName : /repo/design/rtl/top/code/top.v
  FileName : /repo/design/lib/IP/boundary.v
  SETUP_CLOCK_GROUP_MISSING (1 warning/0 waived)
  FileName : /repo/design/rtl/top/code/top.v
"""
        self.assertEqual(count_repo_violations(report, Path("/repo")), ((0, 2, 1, 0), 0))

    def test_all_ignored_and_legacy_headers(self):
        report = """
  CDC_UNSYNC_NOSCHEME (0 errors/0 waived/3 ignored)
  SETUP_PORT_UNCONSTRAINED (1 error/0 waived)
  FileName : /repo/design/rtl/top/code/top.v
"""
        self.assertEqual(count_repo_violations(report, Path("/repo")), ((0, 1, 0, 0), 0))


if __name__ == "__main__":
    unittest.main()
