# CI Error Quick Reference

Use this page when a pull-request check fails. Fix the first underlying tool or
source error before addressing later messages, which are often consequences.

## Initial checks

| CI message | Cause | Fix |
|---|---|---|
| `missing-rtl-test` | `design/rtl/<module>` has no matching RTL test. | Add `test/rtl_test/<module>` or remove the incomplete module. |
| `missing-sweep-json` / `invalid-sweep-json` | `script/sweep.json` is absent, malformed, or not a JSON object. | Add or correct the JSON file. |
| `missing-required-modes` | The sweep has no valid `modes` object or no `SANITY`/`SANITY_*` mode. | Add at least one sanity mode. |
| `missing-lint-tcl` | The module lint entry script is missing. | Add `design/rtl/<module>/code/lint.tcl`. |
| `nested-model-directory` | Model components are inside subdirectories of `design/models/`. | Keep model component files directly under `design/models/`. |
| `loose-model-test-entry` | A non-directory entry is directly under `test/model_test/`. | Put each model test in its own directory. |
| `missing-model-test-rtl-flist` | A model test has no `script/rtl.flist`. | Add a filelist containing all sources required by that test. |
| `file-module-name` | A Verilog filename differs from its first outermost module name, outside excluded IP/netlist directories. | Rename the file or module so the names match. Files under `design/lib/IP/` and `pd_handoff/` are exempt from this rule only. |
| `duplicate-verilog-basename` | Two design/test sources have the same `.v` or `.sv` filename. | Give every source a globally unique basename and update filelists. |
| `design-filelist-location` | A design filelist is under `pd_handoff/filelist/`. | Move it directly under `design/rtl/<module>/pd_handoff/`. |
| `shared-rtl-common-file` | One RTL source expands through multiple design filelists outside `design/lib/`. | Move genuinely shared RTL to `design/lib/`, or remove unintended duplicate references. |
| `external-path` | An absolute path is outside the repository and approved roots. | Use `${PROJECT_ROOT}/...` for repository files. Keep external simulation data under `/rtl/`. |
| `tsmc-path-mismatch` / `tsmc-path-not-allowed` | A technology file uses a noncanonical or unapproved installed path. | Use the canonical path printed by CI. |
| `spice-file-location` | A `.sp` file is outside the required handoff directory. | Move it under `design/rtl/<module>/pd_handoff/spice_files/`. |
| `spice-lfs-configuration` / `spice-lfs-attribute` | SPICE files are not correctly tracked by Git LFS. | Restore or correct the corresponding `.gitattributes` rule. |
| `vcs-error-waiver-structure` | A `vcs_error_waivers.json` file is invalid. | Use valid JSON with `file`, `diagnostics`, `reason`, `added_by`, and `approved_by`. |
| `test-coverage-baseline-policy` | A tracked file in a module's RTL test changed, but its coverage baseline is missing or empty. | Run a coverage regression, run `make export_coverage_baseline` from the module's `script/` directory, and commit `coverage_baseline/ALL_TESTS_COV.vdb`. |
| `scan-error` | CI could not read a checked file. | Check its permissions, encoding, symlink target, and contents. |

Relative paths and missing top-level
simulation inputs are currently Initial-check warnings rather than failures.

## Coverage compatibility

| CI message | Cause | Fix |
|---|---|---|
| Incompatible/missing coverage point or instance | The submitted VDB lacks coverage objects matching the current default design. | Regenerate coverage for the current RTL and export the baseline. Extra compile configurations are allowed. |
| Coverage reference compilation failed | The module's default coverage-enabled compilation failed. | Fix the first error in `.ci-logs/coverage/<module_name>/reference_compile.log`. |
| Invalid VDB, unavailable tool, unsupported XML layout | The database cannot be validated safely. | Re-export incomplete VDBs. For tool/infrastructure issues, contact admin. |

No percentage threshold is enforced. See [coverage compatibility checks](package/rtl-commons/COVERAGE_BASELINE.md#ci-compatibility-check).

## Compile errors

Every native VCS error or fatal diagnostic fails CI. The following design/model
diagnostics are also promoted to errors:

| ID | Cause | Fix |
|---|---|---|
| `AOUP` | An undefined parameter is overridden. | Correct or declare the parameter, or remove the override. |
| `CAWM-L` | Expression and destination widths differ. | Correct the widths or use an explicit slice, extension, concatenation, or cast. |
| `IPDASP` / `IPDW` | An ANSI port identifier is invalid or declared more than once. | Keep one valid declaration for each port. |
| `IRIMW` | A memory word uses an illegal range. | Correct the array dimensions or selected range. |
| `IWNF` / `IWU` | An implicit wire is undriven or used. | Correct the likely typo and explicitly declare and drive the intended net. |
| `NS` | A null statement is present. | Remove the stray semicolon or add the intended statement. |
| `ONGS` | An output is never assigned. | Drive the output or remove the unused port. |
| `PCTI-L` / `PCTIO-L` | Connectivity coerces a port to input or inout. | Correct its direction and remove conflicting internal/external drivers. |
| `PCWM-L` | A port connection has the wrong width. | Match widths or connect an explicit slice/extension. |
| `SIOB` | A select index is outside the declared range. | Correct the index or signal declaration. |
| `TFIPC-L` | An instance has too few port connections. | Connect every required port, preferably by name. |
| `TMR` | A text macro is redefined. | Remove the duplicate definition or add suitable preprocessor guards. |
| `ULCO` | Comparison operands have unequal widths. | Resize the operands intentionally before comparing. |
| `WMIA-L` | Assignment source and destination widths differ. | Correct declarations or explicitly resize the expression. |

The elevation excludes `test/`, `design/lib/IP/`, module `pd_handoff/`
trees, `/rtl/prev_ic`, and approved technology libraries automatically.
Model sources remain elevated unless an exact diagnostic is waived for that
file by a `vcs_error_waivers.json` below `design/models/`. User-defined
`VCS_ERROR_EXCLUDE_FILES` entries are not allowed.

To reproduce the normal module compile:

```bash
cd test/rtl_test/<module_name>/script
make compile
```

Use the exact reproduction command printed in the GitHub job when the failing
row uses additional modes, build tags, or compile switches.

## Lint

| CI message | Cause | Fix |
|---|---|---|
| `Lint Violations` | `report_hdl.top` contains at least one unwaived repository finding. | Correct every reported rule violation, or add a narrowly justified module waiver. |
| `Lint Tool Failed` | VC Static failed during analysis, elaboration, library loading, scripting, or licensing. | Correct the first error in `.ci-logs/<module>__lint.log`. |
| `Missing Lint Report` | No parseable `lint_run/report_hdl.top` was generated. | Correct the preceding lint analysis/elaboration failure. |
| `Lint Runner Failed` | The CI wrapper encountered an internal path or configuration problem. | Inspect the annotation and runner log. |

Every unwaived repository lint finding fails the lint job, regardless of
whether VC Static labels it fatal, error, warning, or info.

Local reproduction:

```bash
cd design/rtl/<module_name>/code
vc_static_shell -f lint.tcl
```

## CDC

CDC reports tool failures, missing reports, and all unwaived repository CDC
findings. In the current workflow these are GitHub warnings and do not
independently fail CI because CDC is not invoked with `--fail-on-issues`.

Correct CDC findings by defining the clocks, resets, and clock relationships,
or by fixing the crossing with an approved synchronizer/CDC structure. Use a
module CDC waiver only for an intentional, reviewed crossing.

## Sanity and synthesis

Missing synthesis structure is an Initial-check error only for a module whose
own design `.v`/`.sv` files change in the PR, unless that module
has a justified entry in `design/synthesis_exemptions.json`. An existing synthesis
folder cannot be skipped: it must contain a working Makefile and pass both flows.
See [exemption format](package/rtl-commons/synthesis/CI_POLICY.md).

| Message | Cause | Fix |
|---|---|---|
| Missing required synthesis variable | `TOP`, `RTL_FLIST`, `CONSTRAINTS`, `UPF`, `VOLTAGE_SUPPLIES`, or `DEFAULT_SETTINGS` is absent. | Correct the module synthesis Makefile. |
| Missing synthesis input | A referenced synthesis file does not exist. | Correct the relative path or add the file. |
| Invalid `DEFAULT_SETTINGS` | The CI default contains a setting other than `hvt` or `hvt_svt`. | Keep defaults limited to `hvt` and/or `hvt_svt`. |
| `UID-95` | Design Compiler cannot resolve a referenced design object. | Correct the object name and ensure the design is read, elaborated, and linked before use. |
| `UPF-112a` | The UPF contains an invalid or unresolved power-intent condition. | Correct the supply/domain/port/net/voltage object identified by the full message. |
| Native `Error:`, `Severe Error:`, or `Fatal:` | Design Compiler logged an error. | Correct the first native error in the synthesis result log. |
| Nonzero synthesis exit | The script or tool terminated without a recognized native error line. | Inspect the result log for Tcl, library, license, or operating-system failures. |

CI runs sanity first. Default synthesis runs only if sanity succeeds.

Local reproduction:

```bash
cd design/rtl/<module_name>/pd_handoff/synthesis
make sanity
make synthesis
```

## Infrastructure failures

Infrastructure failures are not normally fixed by changing RTL. Contact the
administrator and include the workflow URL, job name, and relevant error text.

| Symptom | Likely cause | Action |
|---|---|---|
| Submodule authentication failure | Missing/expired token or missing repository access. | Correct repository access or the CI secret; keep stored submodule URLs as HTTPS. |
| EC2 runner remains queued | Provisioning, registration, capacity, or premature termination problem. | Inspect the provisioning and cleanup jobs. |
| EFS or `/rtl/prev_ic` probe fails | EFS did not mount or synchronized data is missing. | Check the EFS mount and main-server synchronization. |
| License checkout failure | No available license or license-server connectivity problem. | Check `lmstat -a` and retry when capacity is available. |
| Missing shared library | The runner image lacks an operating-system package. | Correct runner provisioning or the AMI. |
| `No space left on device` | Coverage, waveform, simulation, or tool output exhausted storage. | Identify the generating process and remove only confirmed generated data. |

The PR workflow compiles simulation configurations but does not run simulation
regressions.
