"""Regression tests for filename/module-name exemptions only."""
from pathlib import Path
import importlib.util
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "initial_checks.py"
SPEC = importlib.util.spec_from_file_location("initial_checks_filename_tests", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CHECK
SPEC.loader.exec_module(CHECK)


class FileModuleNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, path, module="actual_module"):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"module {module}; endmodule\n", encoding="utf-8")
        return file

    def test_ip_root_and_nested_verilog_and_systemverilog_are_exempt(self):
        for path in ("design/lib/IP/vendor_delivery.v", "design/lib/IP/vendor/nested/model.sv"):
            self.source(path)
        self.assertEqual(CHECK.check_file_module_names(self.root), [])

    def test_pd_handoff_exemption_is_preserved(self):
        for path in ("design/rtl/block/pd_handoff/delivered.v",
                     "design/rtl/block/pd_handoff/netlists/corner/mapped.sv"):
            self.source(path)
        self.assertEqual(CHECK.check_file_module_names(self.root), [])

    def test_normal_rtl_library_models_and_testbenches_still_fail(self):
        for path in ("design/rtl/block/code/wrong.v", "design/lib/wrong_lib.v",
                     "design/models/wrong_model.v", "test/rtl_test/block/testbench/wrong_tb.sv"):
            self.source(path)
        findings = CHECK.check_file_module_names(self.root)
        self.assertEqual(len(findings), 4)
        self.assertTrue(all(f.check == "file-module-name" and f.is_error for f in findings))

    def test_similarly_named_or_misplaced_ip_directories_are_not_exempt(self):
        for path in ("design/lib/IP_extra/wrong.v", "design/rtl/block/code/IP/wrong.v",
                     "test/rtl_test/block/IP/wrong.sv"):
            self.source(path)
        self.assertEqual(len(CHECK.check_file_module_names(self.root)), 3)

    def test_matching_normal_filename_still_passes(self):
        self.source("design/rtl/block/code/actual_module.v")
        self.assertEqual(CHECK.check_file_module_names(self.root), [])

    def test_duplicate_basename_rule_still_covers_ip_and_handoff(self):
        for path in ("design/lib/IP/shared.v", "design/rtl/block/pd_handoff/shared.v",
                     "design/rtl/other/code/shared.v"):
            self.source(path, "shared")
        findings = CHECK.check_duplicate_verilog_basenames(self.root)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check, "duplicate-verilog-basename")
        self.assertTrue(findings[0].is_error)

    def test_first_outermost_module_rule_is_unchanged_elsewhere(self):
        file = self.source("design/rtl/block/code/first.v", "first")
        file.write_text("// module ignored; endmodule\nmodule first; endmodule\nmodule second; endmodule\n")
        self.assertEqual(CHECK.check_file_module_names(self.root), [])


if __name__ == "__main__":
    unittest.main()
