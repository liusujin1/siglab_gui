# Hardware validation

Validation was completed against a SAMBA controller running firmware
V3.3.122 (library 103) over COM1 at 57600 baud.  Hostnames, user accounts,
private controller configuration files, and raw device snapshots are kept out
of the repository.

## Final results

- Local and remote automated suites: **95 passed**; `compileall` passed.
- Supported read-only endpoint inventory: **315 passed** before and after
  writable/action tests.
- Same-value write/readback coverage: **297 writable UI parameter keys**;
  final comparison reported **0 missing and 0 changed**.
- All 11 authorized action ports passed, including digital trace, pneumatic
  move/adopt, proximity adopt, FF/PFF FIR reset-and-restore, and event start/
  stop.  Every restorable value was snapshotted and restored.
- UI binding verification passed for loop state, controller configuration,
  sensor names, Used ADC mapping (`7` displayed as `40`), motor offsets,
  position SI conversion, pneumatic status/timers, FF/PFF controls, velocity
  stages, and non-scientific numeric display.

## Save/Load Setup

The final hardware probe returned **9 PASS** and one explicit user-excluded
operation:

- Controller to Save File: complete controller capture and vendor-compatible
  XML serialization passed.
- Open File to Controller: a single low-risk dither-frequency marker was
  applied, read back, and restored through the original setup file.
- Save to NVRAM and Restore from NVRAM passed with readback restoration.
- Build Check Sum and Read Check Sum passed; all saved/actual checksum pairs
  matched and the final status word was zero.
- A final complete capture and 297-key comparison found no residual changes.
- **Clear NVRAM (`NACLR`) was never called**, as explicitly requested.

Firmware capability discovery excluded 20 endpoints that this controller does
not advertise (including Cascaded Position, Pneumatic Ramp, Safety/ZMS, and
Analysis).  These are capability skips rather than test failures.
