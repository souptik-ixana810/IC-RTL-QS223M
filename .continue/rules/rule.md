# LiteUVM FHSS Testbench Rules

## 1. LiteUVM class source integration
- Do not compile `_seq.sv` or `_chkr.sv` as standalone filelist compilation
  units unless wrapped in a supported package flow.
- Include them from `_tb.sv` using `include`.
- Ensure the source directory is added to `script/rtl.flist` with `+incdir+`.

## 2. Interface and modport completeness
- Every signal read or written by the sequencer or checker must be explicitly
  listed in the corresponding modport with the correct direction.
- Any checker-written field must be writable from the checker modport.
- Do not place dynamic types such as `string` in modports using `input`,
  `output`, or `inout`. Keep metadata strings outside the modport.
- Whenever a new signal is added to the interface, audit all modports before
  the next compile. Do not assume existing modports cover new signals.

## 3. Assertion-only X checking policy
- Keep all X detection exclusively in `_bind.sv` using `$isunknown(...)`.
- Remove or avoid checker-side X propagation logic in this mode.
- Do not register framework-level X check signal lists in this mode.

## 4. Filelist policy
- List all testbench files directly in `test/rtl_test/<block>/script/rtl.flist`.
- Do not introduce a separate `tb.flist` unless explicitly requested.
- If `_tb.sv` includes `_seq.sv` and `_chkr.sv`, list only `_tb.sv` and
  provide the proper `+incdir+` path.

## 5. SystemVerilog structural edit safety
- After any structural edit or rollback, explicitly verify all block
  terminators: `timescale`, `endinterface`, `endmodule`, `endclass`,
  `endtask`.
- After removing any print or debug block, recount `begin`/`end` balance
  in the enclosing task or function before recompiling.

## 6. Simulator compatibility for expressions
- Prefer explicit `logic'(...)` casts for arithmetic in packed logic contexts.
- Avoid slicing directly from arithmetic expressions without a casted
  intermediate.

## 7. Framework/package filelist hygiene
- Do not redundantly list internal LiteUVM framework source files if the
  package entry point already provides them.
- Keep only approved package/filelist entry points in the module build flow.

## 8. Practical debug checklist
Before treating a failure as a tool limitation, verify:
- `+incdir+` present for all `include` files
- Modport directions match actual sequencer/checker reads and writes
- No dynamic types exposed through unsupported modport directions
- X-checking policy matches user intent
- `rtl.flist` contains the intended sources directly
- Recent edits did not introduce directive or terminator typos
- `begin`/`end` balance after any block removal

## 9. Post-write settling before readback
- After completing a batch of config writes in any sequencer task, always
  insert an explicit clock-cycle wait before starting readback verification.
- Never interleave writes and reads to the same address space without a
  settling wait between the two phases.
- The write-all-then-wait-then-verify pattern must be followed consistently
  across every config task, not only in originally debugged paths.

## 10. Per-test watchdog and thread scoping
- In `framework.sv`, always terminate all forked threads from a completed
  testcase before starting the next one.
- Use `disable fork` after the testcase completion sync point to ensure
  watchdog, sequencer, and checker threads from one test cannot affect
  subsequent tests.
- Treat this as a framework invariant. Verify it is still present any time
  `run_single_test()` is modified.

## 11. Sequencer task address and register ownership
- Each sequencer configuration task must have a clearly defined and
  non-overlapping set of registers or addresses it is responsible for.
- No task may write outside its declared scope, even to adjacent or
  seemingly related addresses.
- One-time setup registers must not be re-written by runtime or
  per-test reconfiguration tasks.
- When adding a new config task, define its register scope explicitly
  and verify it does not overlap with existing tasks.

## 12. Phase and slot control state ownership
- Every control flag that governs phase or slot behavior must be explicitly
  initialized at the start of the phase or slot that owns it, and explicitly
  cleared when that phase or slot exits.
- No control flag may carry implicit state across phase or slot boundaries.
- Recovery or resync logic must be tied to the scope of the slot or phase
  that triggers it, not to assumptions about prior execution order.
- When adding a new phase or slot type, define its required entry state and
  exit state before implementing its body.