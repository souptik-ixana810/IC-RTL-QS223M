---
description: Chip Repository & LiteUVM Testbench Structure. All testbenches should use this set of rules.
---

# Chip Repository & LiteUVM Testbench Structure

- When planning and writing testbenches please follow these rules, the general repository structure rule and the testbench structure rule for creating liteuvm based testbenches. Note: All testbenches should follow the liteuvm structure.

## Repository Layout Rules

- Treat the structure rules below as the desired repository state, not as instructions to create folders or files on every prompt. Only create missing folders or files when they are relevant to the current prompt and are not already present.

- The repository keeps all design and verification sources. Shared or user-local test data must live outside the repository in the global path `/rtl/test_data/ic-rtl-structure/<username>/`, where `<username>` is chosen by the user running the flow.

- In the repository maintain exactly two first-level folders: `design/` and `test/`. Only designers add files under `design/`; verification engineers work under `test/`.

- Under `design/rtl/`, there should be one folder per modular RTL block named after the block (e.g., `fhss_top/`, `dvco/`). Each block folder should contain exactly three sub-folders: `code/` for all Verilog and SystemVerilog sources, `config/` for configuration description files, and `pd_handoff/` for everything physical design needs from the designer.

- Under each block's `code/` folder, keep three sub-folders strictly separated: `design_files/` for pure RTL, `lint_files/` for lint rule files, waivers, and reports, and `cdc_files/` for CDC rule files, waivers, and reports. Never mix lint or CDC artifacts with RTL source files.

- Under each block's `config/` folder, place plain-text files (one per register or config group) that describe the config name, bit-width, and default value. Name them `cfg_<descriptor>.txt`.

- Under each block's `pd_handoff/` folder, always provide `rtl.filelist` (listing only the RTL files for that block), a `netlist/` sub-folder (for netlists received from PD and used for SDF or NLP flows), and a `constraint/` sub-folder (for UPF and related constraint files).

- Under `design/models/`, there should be one folder per analog-based behavioral or netlist model. These folders follow the same naming convention as RTL block folders but live under `models/`, not `rtl/`.

- Under `design/lib/`, maintain exactly two sub-folders: `prev_chip/` for RTL reused verbatim from previous tape-outs, and `common/` for shared infrastructure blocks such as memory controllers and SPI controllers, each accompanied by their own RTL filelists.

- Under `test/rtl_test/`, there should be one folder per RTL block under test when that block needs dedicated RTL verification collateral. The folder name must exactly match the corresponding folder name under `design/rtl/`. Each block test folder should contain: `testbench/` (with sub-folders `testbench_files/` for sequencer, checker, and assertion sources; `test_mem/` for generated test memories regenerated fresh on every run; and optionally `python_tb/` for Python-based stimulus generation and validation), `regression/` for all regression run logs and reports, `code_coverage/` for VDB and coverage databases, `consolidated_report/` for summary reports across all regressions, and `script/` containing `rtl.filelist` and `makefile_child`.

- Under `test/model_test/`, there should be one folder per model under test when model-test collateral is needed, mirroring the naming under `design/models/`. Each folder contains a `testbench/` sub-folder for a lightweight testbench that validates connectivity and outputs, and a `script/` sub-folder with `rtl.filelist` and `makefile_child`.

- Under `test/top_level/`, maintain the following sub-folders: `common/` for device config and master MCU files shared across all top-level tests; `subtest/` for individual scheduled top-level tests; `script/` containing the top-level `rtl.filelist` and `makefile_child`; `regression/` for top-level regression setups and logs; `preset/` (with `mem_files/` and `json_files/` sub-folders) for Python-generated memory init and JSON config files derived from the RTL config; `firmware/` for firmware hex files used in top-level simulation; and `consolidated_report/` for summary reports across all top-level regressions.

- Place the single parent makefile at `test/makefile_parent`. This is the top-level entry point for all test invocations. All module-level `makefile_child` files are chained from here.

- Keep FPGA-related flows and collateral under `test/fpga/`, separate from simulation flows.

- Never store large generated or user-supplied test data in the repo. These will be kept under `/rtl/test_data/ic-rtl-structure/<username>/` which will be outside the repo. The filenames and internal folder structure for stimulus and golden files under that user directory are user defined, not repository defined.

---

## LiteUVM Testbench Structure Rules

- Follow the architecture used by `package/rtl-liteuvm/fhss_top_tb/fhss_liteuvm_tb.sv` and `package/rtl-liteuvm/dvco/dvco_calibration_tb.sv`.

- Before writing any code, analyse the RTL fully: identify all ports, widths, clock domains, reset strategy, protocol class, visible FSM states, parameters that need sweeping, and corner cases (X-at-reset, back-pressure, error injection, boundary values).

- Dont test based on the logic provided by the RTL code though. The logic component should be separate and should be given by the user in the prompt or modified by the user in the plan.

- Keep the top module thin: clock generation, reset sequencing, interface instantiation, DUT and model wiring, `bind` statement, framework and test registration, and sequencer/checker instantiation with `set_vif()` calls only. No stimulus logic, no signal sampling, no scoreboard state.

- When a LiteUVM testbench is needed for a block, there should be exactly these repository-managed files for that block: `_if.sv`, `_seq.sv`, `_chkr.sv`, `_bind.sv`, `_tb.sv`, and `sweep.json`. All runtime input and golden/reference data files come from `/rtl/test_data/ic-rtl-structure/<username>/` using user-defined names and structure.

- Define a dedicated interface with `sequencer` and `chkr` modports. The `sequencer` modport drives DUT controls and reads response/status signals. The `chkr` modport is input-only with no outputs ever. Include an `x_check_signals[]` string array on the interface listing all critical DUT outputs.

- Put stimulus in classes extending `base_sequencer`; implement `set_vif()`, `init()`, and `execute()`. In `init()`, read the replay key, resolve the user-selected stimulus data path(s) from registered string plusargs, load the stimulus content, validate signal mappings, set checker enable flags, raise `arm_checkers [1]`, and block on `checker_ready [2]`. In `execute()`, fork an Initiator and a Responder: the Initiator drives per-TXN (wait trigger -> drive signals -> inject errors -> notify checker); the Responder monitors DUT requests, looks up the response table, applies delay, drives response, and notifies checker. When all transactions drain, assert `sequencer_done [3]`, emit stats, and wait for `checker_finalize [4]`.

- Implement `load_test_configs()` only when preloading DUT state is required — typically when initialising memory or registers over a side-channel such as SPI. Do not use it for normal stimulus.

- Put checking in classes extending `base_checker`; implement `set_vif()`, `sample_signal()`, the required monitors, and checker-owned post-processing such as result comparison or report generation. In `sample_signal()`, iterate over `x_check_signals[]` on every clock edge and report any `$isunknown` hit via `fw.report_x_propagation()`. Spawn monitors with `fork...join_none` only for the paths whose enable flag was set by the sequencer: `monitor_global()` for universal invariants, `monitor_protocol()` for per-TXN comparison against user-selected golden/reference data, `monitor_latency()` for timing bounds, and `monitor_coverage()` for functional bins. On `sequencer_done [3]`, flush the scoreboard, save logs, and close replay key handles. On finalize, classify errors using `error_type_e`, raise a bug entry with the replay key on any failure, and assert `checker_finalize [4]`.

- Place all SVA properties in `_bind.sv`, never inside RTL or the top module. Mirror DUT outputs as inputs to the assertion module, use `disable iff (!rst_n)` on every property, and treat assertion failures identically to checker errors: timestamped and tagged with the replay key.

- Register all runtime knobs with `fw.register_int_plusarg`, `fw.register_real_plusarg`, or `fw.register_str_plusarg` before any `fw.add_test()` call. Never hardcode parameter values or sweep matrices in SystemVerilog. Never write nested SV loops over parameter grids.

- If parameters need sweeping, run one point per simulation via plusargs; an external script based on the SimRunner doc below launches the sweep. Only a json file defining the sweep parameters needs to be provided.

- Register each scenario with `fw.add_test(...)` and select tests at runtime with `+ENABLE_TEST_<n>` instead of editing source between runs. Test names must match switch names in `sweep.json` exactly.

- Use framework snapshot registration so failures produce replayable diagnostics and consistent logs.

- Use sequencer/checker mailboxes only for structured handoff data the checker cannot observe passively on the interface, such as DVCO LUT readback values captured over SPI. Do not route normal signal observations through mailboxes; monitors observe the interface directly.

- Keep module-only analog or counter glue in the top module when it depends on `assign` or `always` blocks that have no policy content. Keep all policy and stimulus decisions inside the sequencer and checker classes.

- Every single run must be reproducible from the selected test name plus the passed plusargs alone. All randomness must be seeded via a registered plusarg. All file paths must be passed via `fw.register_str_plusarg`. No `$time`-dependent stimulus; use clock-edge counters instead. No source edits should be needed to switch between scenarios.

---

## Makefile And `sweep.json` Rules

There should be one makefile and one `sweep.json`:

Keep the responsibilities separate:

- the makefile selects the implementation view being built
- `sweep.json` defines the runtime defaults and regression intent

### Rules For Writing The Module Makefile

- Write one module-specific makefile per testbench.
- Resolve the module directory with a local variable such as `TB_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))`.
- Set a default implementation mode before any mode-specific logic, for example `MODE ?= rtl`.
- Use makefile `MODE` only for implementation views such as `rtl`, `sdf`, or `nlp`.
- In each `MODE` branch, add the filelists needed for that implementation view.
- Add mode-specific compile options in the relevant `MODE` branch only.
- If two implementation views share the same timing-oriented source list, they may reuse the same filelist.
- Include the shared parent makefile last so child-specific settings are captured.
- Do not put regression sweeps, runtime test intent, or parameter matrices in the makefile. Those belong in `sweep.json`.

Example structure:

```make
TB_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

MODE ?= rtl

ifeq ($(MODE),rtl)
    FLISTS += -f $(TB_DIR)/flist/rtl.flist
else ifeq ($(MODE),sdf)
    FLISTS += -f $(TB_DIR)/flist/timing.flist
else ifeq ($(MODE),nlp)
    FLISTS += -f $(TB_DIR)/flist/timing.flist
    COMPILE_ARGS += -upf $(TB_DIR)/fhss_top_nlp.upf
    COMPILE_ARGS += -power_top fhss_top
endif

include $(TB_DIR)/../../Makefile
```

### Rules For Writing `sweep.json`

- Write one `sweep.json` per module.
- Use `default` to define the complete baseline testbench interface.
- Put all stable testbench parameters and common plusargs in `default`.
- Include every user-facing runtime knob in `default`, even if many modes override only a subset.
- Put the common `TEST` selection in `default` when most runs use the same test.
- Use `modes` to define regression intent on top of `default`.
- Every module must include a `SANITY` mode that is fast, stable, and trusted for first-pass checking.
- Keep JSON mode names focused on regression intent, such as `SMOKE`, `SANITY`, `SWEEP`, or `INTEGRATION_CHECK`.
- Do not use JSON `modes` to represent implementation views. Implementation views belong in makefile `MODE`.
- Prefer everything other than `default` and `modes` to stay fixed once the flow is established for the repository or project area.
- Treat the non-`default` and non-`modes` fields as flow infrastructure, not as the usual per-module customization surface.

### Allowed Content In A Mode

A mode entry may contain:

- constant values
- list-based sweeps
- range-based sweeps
- exclusions
- grouped sweeps
- mode-local `compileSwitches`
- mode-local `compileSwitchGroups`

### Mode Authoring Rules

- Use a plain value for a fixed override.
- Use a list for a simple sweep over multiple values.
- Use the `".."` form for inclusive ranges as `[start, stop]` or `[start, stop, step]`.
- Use `Exclude_<name>` to remove values from a list or range sweep.
- Use a nested object for grouped sweeps when multiple parameters must move together instead of forming a cross-product.
- Grouped sweep lists must have the same final length after exclusions.
- Grouped sweeps use simple index-by-index zip behavior. Do not rely on one grouped entry expanding into multiple values against another grouped entry.
- Use mode-local `compileSwitches` when every run in that mode needs extra compile defines or compile arguments.
- Use `compileSwitchGroups` when one regression mode must branch into multiple compile fingerprints.

### `compileSwitches` Rules

- `compileSwitches` affects compilation, not runtime plusargs.
- `"FOO"` becomes `+define+FOO`.
- `"FOO=1"` becomes `+define+FOO=1`.
- Raw compile tokens such as `"+incdir+./rtl/include"` or `"-f ./extra.flist"` pass through unchanged.
- Runs with the same compile-switch set should be able to reuse the same compiled build.

### Fixed Top-Level `sweep.json` Fields

Prefer the following top-level fields to remain fixed across modules once the flow is set up:

- `commandDir`
- `compileCmd`
- `runCmd`
- `guiRunCmd`
- `cleanupCmd`
- `preRunCleanupCmd`
- `buildTagParam`
- top-level `compileSwitches`
- `replayKeyPlusarg`
- `completedRunsPath`
- `singleRunCmd`

For normal authoring, the main per-module work should be in:

- `default`
- `modes`

### Example `sweep.json`

```json
{
  "commandDir": "./scripts",
  "compileCmd": "make compile",
  "runCmd": "make sim",
  "guiRunCmd": "make gui",
  "cleanupCmd": "make clean_build",
  "replayKeyPlusarg": "REPLAY_KEY",
  "completedRunsPath": "./artifacts",
  "default": {
    "TEST": "smoke",
    "PARAM_A": 0,
    "PARAM_B": 16,
    "ENABLE_ASSERTS": 1
  },
  "modes": {
    "SMOKE": {
      "SEED": [1, 2, 3]
    },
    "SWEEP": {
      "PARAM_A": { "..": [0, 10, 2] },
      "Exclude_PARAM_A": [4]
    },
    "SANITY": {
      "PARAM_B": [0, 10],
      "PARAM_A": [0, 10]
    }
  }
}
```
