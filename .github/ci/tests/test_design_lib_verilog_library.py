"""Shared Verilog libraries remain valid design sources and CI dependencies."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


CI_DIR = Path(__file__).resolve().parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, CI_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DISCOVER = load("library_discovery", "discover_compile_matrix.py")
INITIAL = load("library_initial_checks", "initial_checks.py")


class DesignLibVerilogLibraryTests(unittest.TestCase):
    def test_shared_v_library_passes_initial_checks_and_triggers_ci(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            library = repo / "design/lib/clock_gate.v"
            library.parent.mkdir(parents=True)
            library.write_text("module clock_gate; endmodule\n")
            for module in ("first", "second"):
                filelist = repo / f"design/rtl/{module}/pd_handoff/rtl.flist"
                filelist.parent.mkdir(parents=True)
                filelist.write_text("-v ${PROJECT_ROOT}/design/lib/clock_gate.v\n")
            top = repo / "test/rtl_test/first/script/rtl.flist"
            top.parent.mkdir(parents=True)
            top.write_text("-f ${PROJECT_ROOT}/design/rtl/first/pd_handoff/rtl.flist\n")

            self.assertEqual(INITIAL.check_common_design_filelist_reuse(repo), [])
            self.assertEqual(INITIAL.scan_external_paths(repo, repo / "allowlist.txt"), [])
            self.assertIn("design/lib/clock_gate.v", DISCOVER.expand_flist(top, repo))


if __name__ == "__main__":
    unittest.main()
