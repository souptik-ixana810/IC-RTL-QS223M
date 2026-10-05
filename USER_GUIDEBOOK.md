# IC-RTL-QS222M User Guidebook

This guide is a practical, user-facing reference for working in this project. This guide is intentionally concise. For complete details, use:

- [IC-RTL-structure README](https://github.com/Quasistatics/IC-RTL-structure/blob/ci-dev/README.md)
- [SW-RTL Regression Runner README](https://github.com/Quasistatics/SW-RTL_Regression_Runner/blob/main/README.md)
- [CI Error Quick Reference](CI_ERROR_QUICK_REFERENCE.md)

## Table of Contents

1. [Repository Structure](#1-repository-structure)
2. [Compiling and Running Simulations](#2-compiling-and-running-simulations)
3. [RTL Checks, Coverage, Synthesis, and Waivers](#3-rtl-checks-coverage-synthesis-and-waivers)
4. [Github CI Rules and What Must Pass](#4-github-ci-rules-and-what-must-pass)

---

## 1. Repository Structure

Complete structure reference:

- [IC-RTL-structure README](https://github.com/Quasistatics/IC-RTL-structure/blob/ci-dev/README.md)

### 1.1 Clone correctly (with submodules)

```bash
git clone --recurse-submodules https://github.com/Quasistatics/IC-RTL-QS222M.git
cd IC-RTL-QS222M
```

If the repository was cloned without its submodules:

```bash
git submodule update --init --recursive
```

### 1.2 Update your current branch from `main`

From the repository root, run:

```bash
git pull origin main
git submodule update --init --recursive
```

The first command merges the latest `main` into your currently checked-out
branch. Commit or stash unfinished local changes before pulling. The second
command checks out the exact submodule versions selected by the updated parent
repository.

### 1.3 Top-level layout

```text
IC-RTL-QS222M/
  design/
  test/

/rtl/
  <project_or_module>/
```

- `design/`: RTL, models, constraints, waivers (designer-owned)
- `test/`: all verification collateral and simulation setups
- The external simulation-data path is fixed to `/rtl/`.

Keep all external simulation data under `/rtl/`, including input vectors,
corpora, memory files, and expected-output data. Do not use arbitrary external
paths for simulation datasets. Generated builds and logs remain under the
module's `script/sim/` directory.


### 1.4 Naming and organization rules

- Folder names must match across design and tests:
  - `design/rtl/<module_name>` <-> `test/rtl_test/<module_name>`
- Keep one makefile + one `sweep.json` + one testbench entry per module flow.
- Module simulation Makefiles should include `test/Makefile` last.
- Keep module lint, CDC, and module-specific waiver files under `design/rtl/<module_name>/code/`.

---

## 2. Compiling and Running Simulations

For `<module_name>`, run simulation commands from:

```text
test/rtl_test/<module_name>/script/
```

### 2.1 Direct Make flow

The usual flow is to compile once and then run the resulting build:

```bash
make compile
make sim
make gui
```

Common combinations are:

```bash
make compile sim       # compile and run
make compile gui       # compile and open the simulation GUI
```

The build directory is selected from the effective compile configuration.
Keep `TEST`, `MODE`, `TIMING`, `COVERAGE`, `BUILD_TAG`, and compile arguments
identical when a later `make sim` or `make gui` must use an existing build. For
example:

```bash
make compile CI_COMPILE_ARGS='+define+FOO +define+WIDTH=16'
make sim     CI_COMPILE_ARGS='+define+FOO +define+WIDTH=16'
make gui     CI_COMPILE_ARGS='+define+FOO +define+WIDTH=16'
```

Use `SIM_ARGS` for testbench plusargs:

```bash
make compile sim SIM_ARGS='+TEST_DATA_DIR=/rtl/<module_name>/test_data +DSSS_EN=1'
```

Compilation errors are displayed in the terminal and saved in the build's
`logs/compile*.log`. Simulation output is saved in `logs/sim*.log`.

### 2.2 RTL, SDF, and NLP builds

`MODE=rtl` is the default. For SDF, select one of the five named corners:

```bash
make compile sim MODE=sdf TIMING=RCBEST
```

The available SDF corners are `RCBEST`, `CBEST`, `TYPICAL`, `RCWORST`, and
`CWORST`. SDF defaults to `TYPICAL`. NLP continues to use `TIMING=min` or
`TIMING=max`:

```bash
make compile sim MODE=nlp TIMING=max
```

### 2.3 Maintenance commands

```bash
make clean        # remove the selected simulation build directory
make clean_build  # remove the selected compiled binary and VDB
make help         # show targets, variables, and the effective configuration
```

### 2.4 Regression scripts

Simulation intent is split intentionally:

- **Makefile `MODE`**: Contains compilation type (`rtl`, `sdf`, `nlp`) and flist locations
- **`sweep.json` `modes`**: regression sweep configs where you will be able to sweep through plus-args and mem file configs.

Complete RunSim and `sweep.json` reference:

- [SW-RTL Regression Runner README](https://github.com/Quasistatics/SW-RTL_Regression_Runner/blob/main/README.md)

### 2.5 Scripts

Use these 3 scripts for managing your simulation. You can always use --help option to get more info.

- `run_sim`: launch and execute sweeps
- `modify_sim`: interactively change an active run (future work only)
- `show_sim`: read-only status viewer for run state

### 2.6 Minimal usage

```bash
run_sim --ui
run_sim --mode SANITY
run_sim --mode SANITY MODULE_TESTS --concurrency 4
run_sim --config sweep.json --makefile-mode sdf
run_sim --config sweep.json --run-id <existing_run_id>
run_sim --help
```

### 2.7 What goes in `sweep.json`

- `default`: full baseline runtime interface (all common knobs and plusargs)
- `modes`: focused overrides/sweeps for regressions
- support fields typically set once (`commandDir`, `compileCmd`, `runCmd`, etc.)

Recommended:

- Always keep one trusted, fast `SANITY` mode per module.
- Keep all the plus-args in `default`, then override in modes.

### 2.8 `modify_sim` behavior (important)

- You can queue:
  - concurrency changes
  - additional not-yet-started modes
  - stop requests
- Changes apply only after **Send pending changes**.
- It does not rewrite already-started runs.

Example:

```bash
modify_sim --config sweep.json --run-id <active_run_id>
```

### 2.9 `show_sim` behavior

- Best for read-only checks or older runs.
- For active runs, `modify_sim` is usually enough because it already shows live status.

Example:

```bash
show_sim --config sweep.json --run-id <run_id>
```

---

## 3. RTL Checks, Coverage, Synthesis, and Waivers

These flows complement simulation. Keep module-specific inputs under
`design/rtl/<module_name>/`, and keep generated reports and run directories
out of version control.

### 3.1 Lint and CDC

Each maintained module should provide module-level configuration scripts:

```text
design/rtl/<module_name>/code/lint.tcl
design/rtl/<module_name>/code/waiver.tcl
design/rtl/<module_name>/code/cdc.tcl
design/rtl/<module_name>/code/cdc_waiver.tcl
```

The lint and CDC scripts define the module top, RTL filelist, constraints, and
the module-specific waiver file to use.

For RTL shared by several modules, keep the source in `design/lib/` and use
one Verilog library entry per file in each module's design `rtl.flist`:

```text
-v ${PROJECT_ROOT}/design/lib/clock_gate.v
-v ${PROJECT_ROOT}/design/lib/sync_resetn.v
```

Do not add commas after these entries. VCS and VC Static load library
definitions as needed; synthesis and FPGA treat them as hardware sources.

Run either check from the module's `code/` directory:

```bash
cd design/rtl/<module_name>/code
vc_static_shell -f lint.tcl
vc_static_shell -f cdc.tcl
```

Manual runs open the VC Static activity viewer when a display is available.
Use `vc_static_shell -gui -f lint.tcl` or
`vc_static_shell -gui -f cdc.tcl` to request GUI mode explicitly. CI sets
batch mode, so it records reports without opening a GUI.

The reports are written to:

```text
design/rtl/<module_name>/code/lint_run/report_hdl.top
design/rtl/<module_name>/code/cdc_run/report_cdc.top
```

Lint findings attributed to HDL files physically stored under
`design/lib/IP/` or `design/rtl/<module_name>/pd_handoff/` are ignored
automatically. These sources remain available for compilation, analysis, and
elaboration. The same directories are excluded from VCS compile
warning-to-error elevation, although native diagnostics may still be printed.
Keep external IP under `design/lib/IP/` and delivered or generated netlists
under the relevant module's `pd_handoff/` directory.

CDC suppresses a finding only when **both its source and destination** belong
to the excluded directories: `design/lib/IP/**` or
`design/rtl/<module_name>/pd_handoff/**`. Each endpoint may be in either
directory class. If either endpoint is outside them, the finding is not
automatically suppressed. If the tool cannot establish that both endpoints
are excluded, the finding remains visible. Files are still analyzed and elaborated.

### 3.2 Statement-based lint waivers and module-local CDC waivers

Lint waivers must identify the specific source file and statement being
waived. Add reviewed statement-based lint waivers to:

```text
design/rtl/<module_name>/code/waiver.tcl
```

Add reviewed CDC waivers only to:

```text
design/rtl/<module_name>/code/cdc_waiver.tcl
```

Use this form:

```tcl
waive_violation -app lint \
  -add {<lint_rule>} \
  -tag {<lint_rule>} \
  -filter {FileName =~ "*/design/rtl/<module_name>/code/<file_name>.v" AND Statement == "<exact statement from report>"} \
  -comment {Reason for the waiver}
```

Copy the `Statement` value exactly from the lint report, including spacing.
This waives only that finding, not every occurrence of the rule in the module.

In pull requests, this policy is checked for a module only when the PR changes
that module's own `.v`/`.sv` design source or its `waiver.tcl`. Lint may still
run for every module that consumes a changed source, but legacy waivers in
consumer or top modules do not block the PR. Changes below `design/lib/` also
do not activate statement-waiver validation for consuming modules; if the PR
changes a module waiver at the same time, that changed waiver is validated.

The module's `lint.tcl` or `cdc.tcl` must point to the corresponding waiver
file. Because these files are loaded only by that module wrapper, the waiver is
limited to that module's lint or CDC run. Keep the reason specific and obtain
review before committing a waiver.

The wrapper settings are:

```tcl
set lint_module_waiver_file "design/rtl/<module_name>/code/waiver.tcl"
set cdc_module_waiver_file "design/rtl/<module_name>/code/cdc_waiver.tcl"
```

<a id="cdc-statement-based-waivers"></a>

#### CDC instance specific waivers

Use instance-specific CDC waivers and do not waive an entire rule for a module
or file.

After running CDC, select the finding in the report/GUI and use its violation ID
in the same VC Static session. Replace `<cdc_rule>` with the finding's tag and
disable its line-number field before generating the waiver:

```tcl
configure_waiver_filter_field -tag <cdc_rule> -field LineNumber -disable
```

If the finding has nested line-number fields, disable each by its exact field
path too. For example, for a tag with `ConstraintInfoList`:

```tcl
configure_waiver_filter_field -tag <cdc_rule> \
    -field {ConstraintInfoList:ConstraintInfo:LineNumber} -disable
```

Use exact field paths so that other identifying fields remain enabled. Then
generate the waiver:

```tcl
generate_waiver_commands -app cdc -viol {<violation_id>}
```

Copy the generated `waive_violation` command into:

```bash
design/rtl/<module_name>/code/cdc_waiver.tcl
```

Rerun `vc_static_shell -f cdc.tcl` from the module's `code/` directory and
confirm that only the intended finding is waived. Run the configuration above
in each session before generating waivers, and check that the generated filter
contains no line-number conditions. Otherwise, edits that move the relevant
code can invalidate the waiver and require regeneration.

Keep the other instance-identifying fields intact. Changes to matched signals,
clocks, filenames or hierarchy still require reviewing the waiver.

### 3.3 Model file-specific compile diagnostic waivers

Some native VCS warnings and lint diagnostics are promoted to compile errors
for design sources. To retain a listed diagnostic at its native severity for
one model source file, add `vcs_error_waivers.json` below `design/models/`.

Example location:

```text
design/models/<model_name>/vcs_error_waivers.json
```

Example containing two file-specific waivers:

```json
{
  "waivers": [
    {
      "file": "example_module.v",
      "diagnostics": ["WMIA-L"],
      "reason": "Intentional truncation at the interface boundary",
      "added_by": "Engineer Name",
      "approved_by": "Reviewer Name"
    },
    {
      "file": "example_module.v",
      "diagnostics": ["PCTIO-L"],
      "reason": "Bidirectional compatibility port is required",
      "added_by": "Engineer Name",
      "approved_by": "Reviewer Name"
    }
  ]
}
```

The `file` path is relative to the directory containing the JSON file. All five
fields are required, the source must exist, and the diagnostic ID must be one
of the diagnostics supported by the compile flow. Only the listed diagnostic
for the listed file is retained at its native severity; other promoted
diagnostics still fail compilation. Every occurrence of that diagnostic in the
listed file is waived. The waiver applies to every test that compiles that
source. Compile waiver files are not allowed below RTL modules, tests, or other
repository folders. Do not set `VCS_ERROR_EXCLUDE_FILES` in a module Makefile.
External IP that must not receive compile elevation belongs under
`design/lib/IP/`; generated or delivered netlists belong under the relevant
module's `pd_handoff/` tree.

### 3.4 Code Coverage

Coverage compatibility validation runs only for tests selected in the compile
matrix that have `test/rtl_test/<module_name>/coverage_baseline/`. An empty
matrix skips validation. It runs once per selected baseline,
not per compile-switch group. An existing baseline directory must contain
`ALL_TESTS_COV.vdb`; tests without the directory are skipped by this validation.
The separate Initial-check baseline requirement for changed tests is unchanged.

Code coverage is an RTL simulation option. From a module or top-level
`script/` directory, compile and run with the same coverage setting:

```bash
make compile sim COVERAGE=code
make compile sim gui COVERAGE=code
```

The first command runs coverage without opening a GUI. The second opens the
coverage database after simulation. Use `COVERAGE=verify` when assertion
diagnostics are also required. Coverage is supported for `MODE=rtl`; its VDB
is stored with the selected simulation build. Keep `COVERAGE` identical for
separate compile, simulation, and GUI commands.

After a `run_sim` coverage sweep creates
`script/coverage/ALL_TESTS_COV.vdb`, export only the merged database:

Choose **yes** when the interactive runner asks whether to export the repository
baseline, pass `run_sim --coverage --coverage-export`, or run:

```bash
make export_coverage_baseline
```

This replaces
`test/rtl_test/<module_name>/coverage_baseline/ALL_TESTS_COV.vdb`.

If a pull request changes any tracked file below
`test/rtl_test/<module_name>/` (other than the baseline directory itself), that
module must have a non-empty baseline at the path above. This applies even when
the design RTL does not change. Initial checks fail when the baseline is
missing. CI also compiles the default configuration with coverage enabled
(without simulation) and checks that the submitted VDB contains compatible
default-design coverage points. Different compile-switch configurations may
contribute coverage; extra points are ignored. Missing/incompatible points or
unreadable databases fail. Percentages cover only the default repository design
scope; no minimum percentage is enforced. See [coverage compatibility checks](package/rtl-commons/COVERAGE_BASELINE.md#ci-compatibility-check)
for details and the local validation command.

### 3.5 Sanity and Synthesis

CI requires a missing synthesis folder only when the PR changes that module's
own `.v`/`.sv` files under `design/rtl/<module_name>/`. Unchanged modules and
parents that merely include a changed submodule are not required to add one.
To omit synthesis for a changed module that does not need it, add its exact name and a reason to
`design/synthesis_exemptions.json`. An existing synthesis folder must still
pass sanity and synthesis; an exemption cannot bypass it. Sanity failure skips
that module's synthesis and fails the Synthesis job. Coverage stays in Compile.
See [synthesis requirements and exemption example](package/rtl-commons/synthesis/CI_POLICY.md).

Keep the module-owned synthesis files together:

```text
design/rtl/<module_name>/pd_handoff/synthesis/
  Makefile
  vol_sup.tcl
  link_libraries.tcl
  lib.flist                 # required only when converting .lib files
  DB/                       # generated .db files
```

- `Makefile` defines the top module, RTL filelist, constraints, UPF, voltage
  supplies, link libraries, and default synthesis setting. `RTL_FLIST` may
  point to a synthesis-specific RTL filelist.
- `vol_sup.tcl` defines the module's voltage supplies.
- `link_libraries.tcl` is the sole technology-library configuration. Its
  `LINK_LIBS` list supplies both target and link libraries for sanity and
  synthesis, with duplicates removed and no preset targets added.
  Generated modules include the four standard databases and automatically add
  every `.db` under `DB/`.
  Edit this file to change the libraries. Each invocation reads the current
  list; legacy `PLUG/target_libs*.tcl` files are not generated. The list must
  not be empty. DC retains its in-memory and synthetic-library lookup entries.
- `lib.flist` lists Liberty inputs that must be converted to `.db`. Keep this
  file directly in the synthesis directory, with one `.lib` path per line.
  Blank lines and lines beginning with `#` are ignored. Paths may be absolute
  or relative to the synthesis directory.

Run the following commands from the synthesis directory:

```bash
make sanity
make synthesis
make db LIB_FILELIST=./lib.flist
make list-settings
make synthesis SETTINGS=hvt,lvt,hvt_svt
```

`make db` uses Library Compiler to write converted databases to `DB/`. The
source `.lib` files may remain in user-managed locations. `DB/` is generated
collateral and should not be committed.

`make sanity` runs the RTL sanity flow. `make synthesis` uses the module's
`DEFAULT_SETTINGS`, which may contain `hvt` and/or `hvt_svt`; newly generated
modules default to `hvt`. `SETTINGS=` overrides the default for a manual run,
and users may select any settings shown by `make list-settings`. Each selected
setting is generated in its own synthesis run directory.

The available settings are `svt`, `hvt`, `lvt`, `hvt_svt`, `hvt_lvt`,
`svt_lvt`, and `hvt_svt_lvt`.
These names retain separate run directories but no longer select libraries.
All use `LINK_LIBS` from `link_libraries.tcl`; edit that list to choose the
technology, voltage, or library flavour. Selecting multiple names with the
same list repeats the same synthesis configuration in separate directories.

---

## FPGA targets

FPGA targets live under `test/fpga/<target_name>/`, with separate hardware and
simulation-only filelists. RTL and testbench sources remain in their existing
folders; generated results stay in the target's ignored `output/` directory.
See [FPGA setup, configuration and commands](test/fpga/README.md).

## 4. Github CI Rules and What Must Pass

### 4.1 When Github CI runs

- Pull requests targeting branch `main`
- Merge-group events on `main`
- The general flow is that you only merge to main once a feature is complete (ie, you have already thoroughly tested it). Before this, you will be collaborating on feature branches.

### 4.2 Required Github CI stages

Your PR must pass the stages configured by its repository. The normal stages
are:

1. **Initial checks** (`.github/ci/initial_checks.py`)
2. **Compile**
3. **Lint and CDC**
4. **Synthesis** (separate job: sanity, then synthesis)

Code coverage is a user-selected simulation flow unless a repository explicitly
enables coverage in CI.

If any required stage fails, you wont be allowed to merge.

### 4.3 Initial-check expectations (key user rules)

Initial checks report warnings in Github CI summary and are intended to enforce structure consistency:

- Every maintained `design/rtl/<module>` should have `test/rtl_test/<module>`.
- Every module test should contain valid `script/sweep.json`.
- Each `sweep.json` should define:
  - at least one `SANITY*` mode
- Absolute/non-approved external paths in source/filelists are flagged.

### 4.4 Before opening a PR (quick checklist)

- `sweep.json` exists and parses cleanly.
- A `SANITY` mode is present and tested.
- Module naming parity between `design/rtl` and `test/rtl_test` is correct.
- No disallowed absolute paths in checked-in files.
- You can run a local sanity simulation before push.
- Lint and CDC scripts use the module RTL filelist and appropriate waivers.
- Modules whose own design `.v`/`.sv` files change have synthesis inputs or a justified entry in `design/synthesis_exemptions.json`.

---
