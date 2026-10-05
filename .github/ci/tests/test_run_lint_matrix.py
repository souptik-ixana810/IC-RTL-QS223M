from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


CI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI_DIR))

import run_lint_matrix as lint_matrix  # noqa: E402


class LintDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, relative_path: str, contents: str = "") -> Path:
        path = self.repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def changed_file(self, relative_path: str) -> Path:
        return self.write("changed_files.txt", f"{relative_path}\n")

    def create_module(self, module: str, lint_flist: str | None = None) -> None:
        source = f"design/rtl/{module}/code/{module}.v"
        self.write(source, f"module {module}; endmodule\n")
        self.write(
            f"design/rtl/{module}/pd_handoff/rtl.flist",
            f"${{PROJECT_ROOT}}/{source}\n",
        )
        if lint_flist is not None:
            self.write(
                f"design/rtl/{module}/code/lint.tcl",
                f'set lint_rtl_flist "{lint_flist}"\n',
            )

    def discover(self, changed_path: str):
        return lint_matrix.discover_rows(
            self.repo,
            self.changed_file(changed_path),
            False,
            None,
        )

    def test_selected_valid_lint_module_is_kept(self) -> None:
        module = "valid_module"
        canonical = f"design/rtl/{module}/pd_handoff/rtl.flist"
        self.create_module(module, canonical)

        rows, failures = self.discover(f"design/rtl/{module}/code/{module}.v")

        self.assertEqual([module], [row.module for row in rows])
        self.assertEqual([], failures)

    def test_selected_module_missing_lint_tcl_fails_discovery(self) -> None:
        module = "missing_script"
        self.create_module(module)

        rows, failures = self.discover(f"design/rtl/{module}/code/{module}.v")

        self.assertEqual([], rows)
        self.assertEqual(1, len(failures))
        self.assertIn("missing", failures[0].message)
        self.assertIn("lint.tcl", failures[0].message)

    def test_selected_module_referencing_missing_flist_fails_discovery(self) -> None:
        module = "stale_script"
        self.create_module(module, "design/rtl/old_name/pd_handoff/rtl.flist")

        rows, failures = self.discover(f"design/rtl/{module}/code/{module}.v")

        self.assertEqual([], rows)
        self.assertEqual(1, len(failures))
        self.assertIn("references missing RTL filelist", failures[0].message)

    def test_invalid_unselected_module_does_not_block_unrelated_change(self) -> None:
        self.create_module("unselected_module")

        rows, failures = self.discover("README.md")

        self.assertEqual([], rows)
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
