# IC-RTL-QS223M

This repository is a clean starting RTL structure for an IC flow.

## Clone and Submodules

The starter command adds the current `main` revision of IC-RTL-COMMON at
`package/rtl-commons` and adds LiteUVM at `package/rtl-liteuvm`. Both URLs are
stored as HTTPS so laptop users can authenticate with Git Credential Manager.
Servers may map the HTTPS URLs to SSH through their Git configuration.

After cloning the generated repository, initialize both submodules with:

```bash
git submodule update --init --recursive
```

Large SPICE handoff netlists under
`design/rtl/<module>/pd_handoff/spice_files/` are tracked with Git LFS by
`.gitattributes`. Git LFS hook installation is handled by the shared server
Git policy, not by this repository scaffold.

## Top-Level Layout

```text
IC-RTL-QS223M/
  .continue/rules/
  .github/
    actions/
    ci/
    workflows/
  design/
    lib/
    models/
    rtl/
  package/
    rtl-commons/
    rtl-liteuvm/
  test/
    Makefile
    fpga/
    model_test/
    rtl_test/
    top_level/
    util/
```

## Adding Modules

Create real modules and matching testbenches with the generator supplied by
IC-RTL-COMMON. The repo-root command is a thin adapter to that generator:

```bash
python3 create_sample.py <module_name> --top <rtl_top_name>
python3 create_sample.py <module_name> --top <rtl_top_name> --tb-top <testbench_top_name> --testbench
# Equivalent:
python3 package/rtl-commons/create_sample.py <module_name> --top <rtl_top_name>
python3 package/rtl-commons/create_sample.py <module_name> --top <rtl_top_name> --tb-top <testbench_top_name> --testbench
```

The generated module contains its lint/CDC wrappers and synthesis configuration.

To create another starter repo from a specific template checkout or git URL:

```bash
ic-rtl-create-starter
ic-rtl-create-starter IC-RTL-new --reference-repo /path/to/IC-RTL-structure
ic-rtl-create-starter IC-RTL-new --reference-repo git@github.com:Quasistatics/IC-RTL-structure.git --reference-branch main
ic-rtl-create-starter IC-RTL-new --reference-repo https://github.com/Quasistatics/IC-RTL-structure/tree/main
```

Run this from the repo root. It creates both `design/rtl/<module_name>` and
`test/rtl_test/<module_name>`.

Keep naming aligned:

- `design/rtl/<module_name>`
- `test/rtl_test/<module_name>`
- `test/rtl_test/<module_name>/script/Makefile`
- `test/rtl_test/<module_name>/script/rtl.flist`
- `test/rtl_test/<module_name>/script/sweep.json`
- `design/rtl/<module_name>/code/lint.tcl`
- `design/rtl/<module_name>/code/cdc.tcl`
- `design/rtl/<module_name>/code/cdc_waiver.tcl`
- `design/rtl/<module_name>/pd_handoff/constraint_files/constraints.tcl`
- `design/rtl/<module_name>/pd_handoff/synthesis/Makefile`
- `design/rtl/<module_name>/pd_handoff/synthesis/vol_sup.tcl`

The clean skeleton has no compile, lint, or CDC candidates. CI discovery starts running real checks when module `sweep.json` files and module lint/CDC scripts are added.
