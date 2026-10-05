# FPGA targets

See [the FPGA guide](../../package/rtl-commons/fpga/README.md) for target creation,
source selection, build/simulation commands and generated output locations.

Keep RTL and testbench sources in their existing design/test directories.
Each target contains its Tcl configuration, design.flist, simulation.flist and
constraints.xdc. Generated files stay under that target's ignored output/.

### Choosing `--part`

`<vivado_part>` is the exact FPGA device identifier, including its package and
speed grade—not the RTL top, board name or Vivado version. Use the part matching
the FPGA on your board.

For example, `--part xc7a35tcpg236-1` selects an Artix-7 XC7A35T device in the
CPG236 package, speed grade `-1` (the Basys3 FPGA). This is an example, not a
default for other boards.

To find the part in an existing Vivado project, run in its Tcl console:

```tcl
get_property PART [current_project]
```

The generator saves this value as `fpga_part` in `<target_name>.tcl`; the build
uses it for project creation and RTL synthesis.
