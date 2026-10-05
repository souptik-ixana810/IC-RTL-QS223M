#!/usr/bin/env python3
"""Run the module generator supplied by IC-RTL-COMMON."""

import runpy
import sys
from pathlib import Path


script = Path(__file__).resolve().parent / "package" / "rtl-commons" / "create_sample.py"
if not script.is_file():
    print(
        "ERROR: IC-RTL-COMMON is not initialized; run "
        "git submodule update --init --recursive.",
        file=sys.stderr,
    )
    raise SystemExit(1)

runpy.run_path(str(script), run_name="__main__")
