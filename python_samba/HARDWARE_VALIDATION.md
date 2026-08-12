# Hardware validation

Core endpoint/action validation was completed against firmware V3.3.122
(library 103); the current Logging and interactive-record validation was run
against firmware V3.3.127 (library 103).  Both used COM1 at 57600 baud.
Hostnames, user accounts, private controller configuration files, and raw
device snapshots are kept out of the repository.

## Final results

- Current local Samba suite: **175 tests passed**, with Qt-heavy files run
  sequentially.  The shared-server
  hardware run also passed remote `compileall`, 107 non-page Samba tests, and
  all 131 SIDMAT tests available at that validation point.
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

## Logging and Records / Plot

- The local GUI connected to the hardware through the remote Communication
  Server; no SSH-launched test process was used.
- All 40 monitor definitions and live values were read through the Logging
  page.  Channel 40 and the event settings were changed, read back, and
  restored.  A short Standard file log produced timestamp, elapsed time, and
  all 40 signal columns.
- A real 4096-sample, 3-channel controller trace was downloaded into the
  standalone Records / Plot window.  Mean removal, Hann FFT, paired CSV export,
  and its metadata sidecar all passed.  The final comparison reported
  `restorable_changed=[]` and `writes_restored=true`.
- Complete `DGLDA` responses can exceed the server/serial 64 KiB safety cap.
  Trace downloads therefore use ordered 128-sample `DGLDV` batches: one WAN
  request per batch while retaining bounded controller responses.
- Firmware documentation states that starting DSSET or changing DSETP
  invalidates saved traces.  The hardware probe now makes a complete backup
  before any write and skips DSETP/DSSET whenever saved traces exist.

Development incident: an earlier DSETP interface validation invalidated one
saved 4096-sample trace before the preservation guard was added.  Its original
waveform was not recoverable.  A replacement Standard trace was captured,
restored to `SavedTraceNum=1`, downloaded in full, and backed up locally; the
replacement is not claimed to reproduce the lost waveform.

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

## Discoverable shared Communication Server

- LAN UDP discovery and Tailscale peer discovery returned the same stable
  service UUID through their respective source addresses, without a copied
  token or fixed client endpoint.
- SAMBA and SIDMAT attached together through the server. SIDMAT captured 1024
  fast-data samples while SAMBA completed seven status refresh cycles.
- A temporary cross-client output-limit write was visible to both clients and
  restored. Direct COM1 access was denied while clients were attached and was
  available immediately after the final detach.
- Read-only probes before and after the shared-server test each passed 315
  endpoints; the 297-key writable comparison reported zero missing and zero
  changed values.
- The portable `PythonSambaCommServer.exe` was launched and connected on the
  hardware host without invoking its Python environment.
