# Simulation and RTL Checks Quick Reference

Run the commands below from the repository root in a shell with the Synopsys tools and licences available (`vcs`, `verdi`/`dve`, and `vc_static_shell`).

For a module named `<module_name>`, use its simulation directory at
`test/rtl_test/<module_name>/script` and its RTL-check scripts under
`design/rtl/<module_name>/code`.

## Compile and run simulation

The usual RTL flow is compile once, then run. Keep `TEST`, `MODE`, `BUILD_TAG`, `COVERAGE`, and any `SIM_ARGS` identical for every command that uses the same build. `MODE=rtl` is the default.
You can run these commands in the "test/rtl_test/<module_name>/script" directory.

```bash
# Compile
make compile

# Run Sim
make sim

# Open Verdi GUI
make gui

# Compile and Run sim
make compile sim

# Compile and open verdi GUI
make compile gui

# Compile and run a named build (keeps its output separate)
make -C test/rtl_test/<module_name>/script compile sim \
  SIM_ARGS='+TEST_DATA_DIR=/path/to/test_data +DSSS_EN=1'
```

`SIM_ARGS` is the normal way to pass testbench plusargs. Output for the second example is under `test/rtl_test/<module_name>/script/sim/rtl_my_test_debug/`, including `logs/compile_debug.log` and `logs/sim_debug.log`.

Common maintenance commands:

```bash
make clean       # remove the current build directory
make clean_build # remove the current compiled binary and VDB
make help        # show supported make variables
```


## Code coverage

Code coverage is supported for RTL simulations only. Compile and run with `COVERAGE=code`, then open the coverage database:

```bash
#Run coverage without opening GUI
make compile sim COVERAGE=code
#Run coverage with opening GUI
make compile gui COVERAGE=code
```

The VDB is `sim/rtl_my_test_cov/simv_cov.vdb`.
`COVERAGE=verify` additionally enables assertion diagnostics.

### Code coverage with `run_sim`

Start the interactive sweep runner with:

```bash
run_sim --ui
```

If **Collect code coverage?** is set to `yes`, the UI asks:

- **Existing coverage results (`add`/`replace`)**: `add` retains and merges earlier VDBs; `replace` clears recognized earlier coverage results first.
- **Open merged coverage in Verdi? (`yes`/`no`)**: opens the final merged VDB after the sweep. This is separate from **Open GUI?**, which opens the GUI for every simulation.

Coverage defaults to `no`. The final merged database is written to `coverage/ALL_TESTS_COV.vdb` unless `coverageOutputDir` specifies another location in `sweep.json`.

## Lint and CDC

Run the module entry scripts from "design/rtl/<module_name>/code/":

```bash
vc_static_shell -gui -f lint.tcl
vc_static_shell -gui -f cdc.tcl
```

The `-gui` option starts the interactive VC Static session; lint and CDC then open the activity GUI after writing their reports. CI runs both checks non-interactively: its generated wrappers set batch mode, remove GUI commands, and append `exit` before running `vc_static_shell`.

Review the generated reports:

```text
design/rtl/<module_name>/code/lint_run/report_hdl.top
design/rtl/<module_name>/code/cdc_run/report_cdc.top
```

The scripts use the module RTL filelist and top automatically. For a new module, add matching `code/lint.tcl` and `code/cdc.tcl` entry scripts before running these checks.

## Synthesis

Run synthesis from `design/rtl/<module_name>/pd_handoff/synthesis`. The module `Makefile` defines the top module, RTL filelist, constraints, UPF file, and `vol_sup.tcl`; edit those values when setting up a new module.

```bash
# Run the synthesis sanity check
make sanity

# Run the default synthesis settings for this module
make synthesis

# List the available library settings
make list-settings

# Run selected settings in separate output folders
make synthesis SETTING=hvt,lvt,hvt_svt
```

The available named settings are `hvt`, `svt`, `lvt`, `hvt_svt`, `hvt_lvt`, `svt_lvt`, and `hvt_svt_lvt`. The module `Makefile` defines the default settings used by CI.
