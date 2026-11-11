# Experimental USB2404 transport

This feature belongs to PythonVNATest. NI remains the startup default. USB uses
the existing NetDAQ backend with `transport="usb"`; it does not use TCP ports.
No third-party Python package, vendor 32-bit DAQ DLL, firmware loader, driver
installer, or new shared analysis/storage change is included in this extension.

## Access

In **File / Device connection** select `netdaq`, then the
`USB2404V10 (0547:1003)` transport. IP/port controls are disabled for USB.
Device index starts at zero. An empty profile field allows read-only probing,
not Inst/Avg/Record. In addition to independently verified profiles, the explicit
DASP parameter compatibility path below permits nominal-voltage acquisition
without claiming verified hardware modes or calibration.
Connection settings are remembered without changing
the NI startup default. Physical query numbers are explicit; do not infer real
channel count from the range of indices that answer queries.

```powershell
.\.venv\Scripts\python.exe -m python_vna.app --probe-netdaq-json --netdaq-transport usb --netdaq-probe-channels 0,1,2,3
```

The driver must already provide the process-bitness `libusb0.dll` in the Windows
system directory. Only USB2404V10, interface 0/alternate 0 and the observed bulk
endpoint layout (08 OUT, 86 command IN, 82 data IN) are accepted. No automatic
SET_CONFIGURATION/reset is sent, so a read-only probe cannot reset acquisition.

## Verified profile additions

Use the [NetDAQ profile contract](NETDAQ.md), with these USB-specific constraints:

- `transport`: `usb` (TCP profiles without this key remain backward compatible).
- `firmware_hex`: exact checked seven-byte `!V` reply; both firmware and device
  descriptor must match before any acquisition configuration is written.
- `stream_format`: `raw32`, ADC width 24, container width 4, path byte 4.
- `divisor_offset`: zero; the first four InitAD bytes are BIG-endian. Other
  InitAD bytes are captured unchanged. Its little-endian channel mask must agree
  with the ascending, independently verified physical stream order (0..3 only).
- Each channel has exactly ONE fixed range/coupling option with captured
  `configuration_hex`. USB does not write `!C`, even for type 0/6/8. Current
  reported type/configuration must match. The configuration length comes from
  `!A`; `!B` must return exactly that many bytes after the channel echo.
  Range/coupling and IEPE are read-only.
- `volts_per_count`, `zero_count`, physical mode/supply, maximum sample rate and
  channel order MUST come from independent calibration/qualification, not from
  a convenient descriptor ratio or a successful protocol acknowledgement.

No real verified profile is shipped. Test fixtures clearly identify themselves
as synthetic and are never used for hardware tests. Hardware trigger and AO
remain disabled; host triggering is described below. Once a real profile is qualified, the same Inst/Avg/overlap,
Record, engineering-unit processing and VNA provenance paths are used.

## State, buffers and ownership

The native handle, commands, pending read and cleanup stay on one worker thread.
UI Stop only sets an event. There is at most one process-local USB lease, so a
connection probe cannot open/re-enumerate a handle while acquisition owns it.

Before sending Start, the transport marks the device as possibly running. ANY
unconfirmed Start (including a valid rejection) or Stop is latched. Only a
checked `!POK` clears the latch. Close attempts Stop for both running AND unknown
states; cleanup failure raises an explicit error rather than silently declaring
success. Repeated Start is refused until Stop is confirmed.

Samples are signed little-endian int32 containers, not compact three-byte data.
Incomplete bytes/channel scans are retained across USB transfers until a full
frame can be reconstructed. Polling `usb_reap_async_nocancel` does not reissue or
discard a pending read. Normal Stop drains the single short pending block
(approximately 100 ms at qualified rates), without submitting another one. This
avoids the old driver abort path for normal free-run acquisition. If that drain
cannot finish within one second, abort is attempted and its completion must be
confirmed before buffers are freed.
If the native driver cannot confirm cancellation, the buffer/context/lease are
retained and access is blocked until application/device recovery; forcibly
destroying an in-flight native buffer is not safe. Driver calls can themselves
hang on a broken kernel driver; this cannot be guaranteed away in-process.

The ABI structures are BYTE-PACKED, including `usb_device` and `usb_bus`, not
only the 18-byte USB descriptor. On x64 `descriptor` starts at 536 and `config`
at 554, not 560. We do not dereference the config pointer to guess settings.
See the upstream [libusb-win32 header](https://github.com/mcuee/libusb-win32/blob/master/libusb/src/lusb0_usb.h)
and [async implementation](https://github.com/mcuee/libusb-win32/blob/master/libusb/src/windows.c).

## Hardware evidence, 2026-10-30

Read-only queries with the product transport returned:

```
USB product: USB2404V10; manufacturer: USBACQ; VID/PID: 0547/1003
firmware:   2156070200800d (2.0)
descriptor: 21442100011804040001f400ffffd8f000002710ff800000007fffff000081160d
types:      ch0..ch3 = 0
config:     ch0..ch3 = 06 / 01 / 01 / 01
```

Two short raw-count captures used divisor 64 (1000 Hz) and mask 0x0f. Each
returned 4096 samples/channel. Host timing after the first block measured
999.991 and 999.922 samples/s/channel. Both received checked Stop replies and
reopened successfully; channel configuration was unchanged before/after.
Negative int32 samples were observed (e.g. ch0 minimum -27788), supporting
sign-extension in the 32-bit container. No volts or EU amplitude is asserted.

After reconnecting the device, 100 hardware open/configure/start/stop/close
cycles completed in 22.39 seconds: 10 immediate Stops, 5 Stops requested during
reading, and 85 sampled cycles (21760 returned samples/channel in total). All
100 received checked `!POK`. No channel configuration commands were issued.
An earlier trial with a 4096-byte pending read showed that this installed
driver's abort did NOT produce a completion notification within two seconds;
the fail-closed guard reported an error instead of freeing that buffer. The
routine Stop path was then changed to short-block drain and revalidated above.
Forced-abort success on this driver is NOT claimed; an unconfirmed abort still
requires recovery rather than silent restart.

Five additional Stops used the backend's 1536-byte native buffer at 1000 Hz /
four streams. Two had a native read still pending when the worker observed Stop.
All completed with `!POK`; request-to-confirmation was 31.5..45.6 ms in this short
test. This is measured evidence for this configuration, not a universal latency
guarantee for other sampling rates or a failing driver.

These checks do NOT qualify physical channel mapping, electrical range,
IEPE/current, amplitude/phase calibration or absence of device-side sample loss.
There is no known hardware sequence counter: provenance explicitly records
`stream_continuity_verified=false`. At least 13 hours of sustained Record and
known-source comparison remain pending. Do not deploy for engineering amplitude
measurements until the real profile has been independently qualified.

## Offline protocol alignment, 2026-11-02

Reviewed external references (not imported or executed by the product):

```text
PROTOCOL_USB.md SHA-256:
e0652c70ca47cfaa16cd32e02b956352720c4b492b7d70aaf556187df61ec2dd
netdaq_usb.py SHA-256:
0dba48e0e6c73767788a803c2dca54f617d2d2a64bca11b5e48c964966359d85
```

The hardware is unavailable for this revision. New coverage uses injected USB
test doubles only; it does not constitute additional hardware acceptance.

- `desc[3]` is the reported channel count, not the stream container width.
  Samples remain signed little-endian int32 with a 24-bit ADC range. Queries,
  profiles and InitAD masks must stay within the reported physical count
  (at most four for this product).
- Probe JSON includes `usb_descriptor` with nominal voltage/ADC ranges and the
  derived nominal V/count. Its `calibration_verified=false` is deliberate:
  matching DASP's software conversion is not independent electrical calibration.
  The earlier NI/DASP voltage discrepancy remains unresolved. Acquisition still
  requires a verified profile; descriptor ratios never become a default profile.
- USB timing is separate from TCP timing. Selectors `0x86/87/88/89/8A` use
  64000/100000/48000/1000000/96000 Hz. Only `0x82` has the additional divisor
  factor of two. Zero selectors are normalized to `0x81`, as in device
  enumeration; other default branches use half the descriptor's maximum rate.
  `0x83` has no verified InitAD sequence and is rejected.
- Divisor calculation truncates after adding 0.4. Zero/overflow divisors are
  rejected before opening the device, and actual rates feed frames and storage.
- USB profile matching checks firmware as well as the descriptor.

The reference's unconditional `SET_CONFIGURATION` on open is not adopted:
read-only probing must not reset a device. The existing checked interface claim,
descriptor validation, Stop ownership and native-buffer quarantine remain.
Digital I/O and channel writes are not enabled by this revision.

Validation: 111 USB/TCP/settings tests passed. The required
`powershell -NoProfile -File scripts\test_vna_product.ps1 -Product python_vna_test`
completed with 530 tests passed, including the shared format and acquisition UI
regressions. No device was accessed during these tests.

## IEPE software path, offline

USB IEPE is a qualified channel interpretation, not a writable coupling index.
A profile can use `input_mode="iepe"` with the actual observed vendor type
(including type 0 if independently established). Type 0 by itself proves
nothing about IEPE support.

Optional channel fields:

- `iepe_supply`: `device_fixed`, `external`, or `unspecified`. Fixed means
  independently confirmed device excitation; external means an independently
  confirmed external conditioner. Neither value causes an excitation command.
- `iepe_current_ma`: known positive current, or null/zero for unknown current.
  Unknown current is accepted for USB only when the supply is explicitly fixed
  or external. It never falls back to NI's 2.1 mA default.
- Existing profiles with a known positive current remain compatible; omitted
  supply is recorded as unspecified, rather than inferred from the vendor type.

The channel editor shows enabled IEPE and current as read-only. Unknown current
is displayed as unconfirmed; `ChannelConfig.iepe_current_ma=0` is its storage
sentinel, not a claim of zero excitation. Provenance retains JSON null and the
supply source. Changing labels, units or sensitivity does not turn excitation on.
Coupling and voltage range still use the single qualified, captured option.
IEPE does not add per-frame mean subtraction or discard startup frames.

The backend returns volts, and the existing display/analysis chain applies
sensor sensitivity once. DASP's mV/EU sensitivity is reciprocal to EU/Volt:
for 51.567 mV/(m/s^2), enter `1000 / 51.567` EU/Volt, not 51.567.
No sensor coefficient is applied in the wire decoder.

Frame/VNA provenance records supply source, known/unknown current and units.
The optional NetDAQ JSON extension also preserves IEPE independently of AC
coupling, plus exact sensitivity, without changing MATLAB-era fields. Older
files without the extension retain their original fallback behavior.
Record obtains provenance before its first sample; binary manifests and text
headers retain it without changing sample columns or binary layout.

No real IEPE hardware profile, current switch, excitation command or sensor-fault
detection is shipped. Fixed excitation, external supply, current magnitude,
coupling and actual electrical response still need hardware qualification.

Validation on 2026-11-02: the PythonVNATest product command completed with
554 passed; VIanalysis completed with 455 passed and one skipped. Coverage
includes mixed voltage/IEPE channels, unknown current, unchanged TCP current
requirements, sensitivity applied once, VNA state/precision round-trip, and
Record manifest/text-header provenance with voltage-only sample storage.
All USB operations used injected test doubles; no hardware was accessed.

## Offline tools (2026-11-03)

The acquisition window's File menu now includes:

- **Import DASP channel parameters**: preview `Channel.ini` or `CH n.par`,
  then apply checked channels' labels, enabled flags, units and EU/Volt.
  Sensitivity in mV/EU becomes `1000 / (sensitivity * Gain * External_gain)` EU/Volt.
  Unmatched sensitivity/measurement units, virtual channels, negative polarity,
  and extra calibration curves are reported and skipped. Valid channels in the
  same file remain available. Offset compensation, hardware gain commands, coupling,
  auto-range and IEPE are not imported or inferred.
- **Offline replay data file / recording folder**: select VNA/MAT time data,
  a DASP `CH n.par`/`.sts` pair or measurement folder, or a continuous recording
  folder, `manifest.json`, closed `.bin`, `.dat` or `.zip` segment.
  Press Inst/Avg/Stop as for acquisition. Replay is explicitly non-hardware,
  never turns the connection lamp green, and does not enable AO.
- **Acquisition quality report**: after stopping, inspect/export a UTF-8-BOM
  JSON report with per-channel DC, RMS, AC RMS, min/max, peak-to-peak,
  invalid-sample count, near-range fraction and delivered-frame throughput.
  Statistics do not modify signal data or diagnose an IEPE sensor as disconnected.

The device connection dialog's **Profile preview / validation** works without
an address or hardware. It shows model, transport, stream format, sampling limit,
channel mapping, voltage conversion and known/unknown IEPE supply/current. Passing
this file check does not establish physical calibration or qualify a new device.

### Replay boundaries

Binary continuous data is read in bounded, read-only memory-mapped blocks.
Text/ZIP records are streamed using the existing DAT reader. VNA replay reuses
the legacy loader and has a 256 MiB input-file limit. A replay never rewrites its
source. Channel order and declared sample rate are retained; no resampling,
automatic baseline subtraction, zero filling or startup-frame rejection is used.
End of a finite replay is reported as completion; malformed or truncated data
is an error. Playback pacing is cancellable rather than an uninterruptible sleep.

The final short block is preserved for Inst/Record. Avg reports that a short
tail cannot participate in a fixed-length spectral average; it does not pad
samples or alter the source. Replay blocks are limited to 64 MiB.

DASP replay supports signed little-endian int32 containers of 24-bit ADC counts.
Its `.par` range endpoints are converted from the declared voltage units to
volts. Old zero compensation and sensor calibration are not applied to this
raw-voltage replay. The channel importer can separately apply validated sensor
units. The declared DASP sample rate is not independent evidence of its physical
output clock, and replay is not a solution to the deferred 1 V saturation issue.

Command-line entry:

```powershell
.\.venv\Scripts\python.exe -m python_vna.app --backend replay --replay-path "D:\record\manifest.json"
```

### Host trigger

A qualified NetDAQ profile permits **host software triggering**, not vendor
hardware-trigger commands. The source must be enabled and Abs Level must be
positive. A trigger occurs when a finite voltage leaves the `(-level, +level)`
window, including negative excursions. A sustained out-of-window level does not
produce repeated triggers until the input re-enters the window.

Every Frame/Manual Arm rearm per record; 1st Frame/1st-Manual Arm wait only for
the first event. Existing Manual Arm UI state still controls arming. Signed
Delay remains a percentage of frame length: negative values retain pretrigger
history, zero starts at the crossing, positive values start after it. The
display's time starts at zero. Trigger wait has a deadline and Stop polling;
history is bounded and channels remain synchronized. Too-early crossings without
enough prehistory cannot form a complete record. No valid startup frames are
unconditionally discarded.

Inst and Record use free-run; Avg uses the existing host averaging, overload
and double-hit pipeline. Separate triggered records are not combined by the
continuous-stream overlap buffer. NI's hardware trigger and overlap path are
unchanged. No additional `!C`, trigger command, gain or IEPE command is sent.

### Recording failures

Record rejects changed channel order or sample rate rather than silently
mislabeling data. A failed frame write attempts to truncate the partial frame
back to the last committed boundary. Flush/close failures still release the
file and attempt to mark the manifest incomplete. If the filesystem cannot even
write a manifest, the close error is surfaced; the previous manifest remains
the recovery point.

Offline stress coverage includes a virtual 13-hour/78-segment clock, bounded
memory, injected slow/full-disk writes, flush failure, stream interruption,
100 replay restarts, cross-block triggers and cancelled reads. Virtual time and
test doubles do not establish 13-hour physical USB stability, calibrated
amplitude, hardware gain ranges or actual IEPE electrical behavior.

## Revised DASP conversion evidence (2026-11-03)

Reviewed `D:\AI_test\claude\DASP\_ana\PROTOCOL_USB.md`, SHA-256:

```text
a3c68eb71d0f6f75089eb7dd7b130ba3523cedfa6c6f068d4ea897bfa00cde3a
```

The revised sections 3.1/3.2/3.4 provide consistent software-conversion evidence:
declared mV units in `.par` and the DASP language resource, plus signed ADC
samples in saved recordings. They establish the nominal conversion used by
DASP for the observed configuration, not an independent electrical calibration:

```text
volts = counts * 10 / 8388608
EU/Volt = 1000 / (Sensitivity_mV_per_EU * Gain * External_gain)
```

DASP raw replay now uses the number of ADC codes (`high - low + 1`), rather
than an inclusive-endpoint interpolation. Zero code is exactly 0 V, negative
full code is -10 V, and positive full code is `10 - 10/8388608` V. The earlier
endpoint interpolation added approximately half an LSB near zero; it could
not account for the reported order-of-magnitude discrepancy.

Gain and External gain are applied once in imported EU/Volt, with their
declared values visible in its tooltip. They are not applied in raw-voltage
replay or sent as hardware settings. The gain-divisor convention is based on
the documented DASP convention; non-unity physical gain still needs comparison.
`Inner gain` is not silently interpreted as `Gain` when non-unity.

Read-only verification against the installed DASP data found 42 `.par` files,
all with the same ADC interval and +/-10000 mV declaration. All declared Gain=1;
External gain was 1 or 10. Five CH1 recordings were also streamed:

| Measurement on 2026-10-30 | Mean reconstructed V | Positive full-code fraction |
| --- | ---: | ---: |
| 15-41-12 | 4.1717230919 | 0 |
| 15-47-25 | 4.1742877877 | 0 |
| 15-47-56 | 9.9999879088 | 0.9999989101 |
| 15-55-03 | 9.9999988079 | 1 |
| 16-07-04 | 9.9999988079 | 1 |

The later files already contain full positive ADC code; changing display
scaling cannot recover their signal. Saved parameters alone cannot distinguish
an actual overrange input, an open biased input, an undocumented hardware mode,
or another acquisition fault. Do not infer a specific electrical cause.

The DASP rate declaration remains unqualified: its saved 128000 Hz is not
automatically treated as a verified output clock. Replay provenance now
distinguishes `playback_sample_rate` from `actual_sample_rate` (null for this
unverified DASP path), retaining the explicit rate warning and conversion basis.
No timestamps are silently reinterpreted as 64000 Hz.

Two reference-document statements are not adopted as hardware conclusions:

- Section 9.3's generic claim that auto-range changes Voltage from/to is not
  evidence of such a change on this card; the inspected records show none.
  Conversely, unchanged `.par` declarations do not prove every opaque `!C`
  value has no effect on hardware gain.
- Section 7.2's claim that Stop replies mix with samples on the same IN
  endpoint conflicts with the separately observed 0x86 reply / 0x82 sample
  endpoints. The previously validated single-owner Stop/drain sequence remains.

IEPE switching/current, opaque channel bytes, physical channel mapping,
non-unity hardware gains, high-rate throughput and long-run continuity remain
hardware acceptance items. This update does not manufacture a verified device
profile, remove profile matching, issue `!C`, reset the USB configuration, or
subtract stored zero drift. It changes no shared analysis/storage modules.

## Reconnected hardware acceptance (2026-11-11)

The connected USB2404V10 reports the same descriptor and firmware 2.0 as above.
Its four channel types remain 0. Current opaque configurations are now
`07 / 01 / 01 / 01`; the earlier CH1 value was `06`. Neither byte is interpreted
as a gain, coupling or IEPE setting. All tests leave these bytes unchanged.
Windows also enumerates NI USB-6000 as Dev4; this establishes neither shared
signal wiring nor an independent voltage reference.

`python_vna.daq.usbdaq_validation` provides reproducible transport checks.
Default invocation sends only descriptor, firmware and channel queries:

```powershell
.\.venv\Scripts\python.exe -m python_vna.daq.usbdaq_validation --output .\tmp_usb_probe.json
```

An explicit nonzero duration enables the existing checked InitAD/Start/Stop
sequence. It does not load a synthetic profile or relax the application's
independent-profile requirement:

```powershell
.\.venv\Scripts\python.exe -m python_vna.daq.usbdaq_validation --seconds 6 --sample-rate 2560 --output .\tmp_usb_capture.json
.\.venv\Scripts\python.exe -m python_vna.daq.usbdaq_validation --seconds 0.064 --sample-rate 1000 --cycles 100 --output .\tmp_usb_cycles.json
```

The report file must be new and its directory writable before hardware opens.
Each completed cycle is flushed to JSON. Reports retain descriptor/configuration
snapshots, selected wire order, ADC limits, nominal voltage/DC/RMS/rail statistics,
Stop acknowledgement and transport release state. Invalid signed 24-bit data,
configuration drift, cancellation and failed cleanup cannot be reported as
ordinary capture success. Statistics use bounded blocks, without startup
discard or DC subtraction. Voltage conversion is explicitly uncalibrated.

Real four-channel results:

| Requested samples/s/channel | Divisor-derived samples/s/channel | Delivered samples/channel | Host delivery estimate |
| ---: | ---: | ---: | ---: |
| 1000 | 1000 | 6000 | 996.79 |
| 2560 | 2560 | 15360 | 2559.94 |
| 10000 | 10666.6667 | 64000 | 10580.55 |
| 25600 | 32000 | 192000 | 31914.09 |
| 64000 | 64000 | 384000 | 63998.98 |

Each row is a six-second data window. Delivery estimates exclude the first
returned frame but include transfer/frame boundary quantization; they are not
an independent clock calibration. A requested rate is not silently reported
as the actual divisor-derived rate. All five runs had checked Stop replies,
unchanged configuration and no full-code samples in the captured data.

An additional 100 open/query/InitAD/Start/read/Stop/close cycles at 1000 Hz
passed. Thirty cancellation tests at 1000 and 32000 Hz requested Stop 0,
20 or 200 ms after checked Start, five repetitions each. Only the owner thread
called native APIs; the timer thread set the Stop event. Every case received
checked Stop acknowledgement and closed the transport. The slowest measured
request-to-close time was 87.4 ms. No quarantined pending transfer was needed.
These are transport checks, not acceptance of the GUI acquisition workflow.

Local detailed reports are `tmp_usb_validation_20261111_100_cycles.json`,
`tmp_usb_validation_20261111_rate_*.json` and
`tmp_usb_validation_20261111_pending_stop.json` in the feature worktree.
They are intentionally ignored test artifacts.

Physical input identification, comparison against a shared NI-measured signal,
electrical range/gain, IEPE supply/current and channel-mode qualification remain
open. No real qualified profile is generated. Inst/Avg/trigger/rejection/Record
GUI acceptance and a physical 13-hour recording remain gated by those facts;
short successful reads do not certify amplitude, frequency accuracy, sample
continuity or long-duration stability.

## DASP parameter compatibility and CH1 sensor check (2026-11-11)

An explicit compatibility option now reuses original DASP declarations instead
of asking the operator to recreate an electrical calibration. In
**File / Device connection**, select NetDAQ / USB and choose
**From DASP parameters / USB compatibility**. Select `Channel.ini` or a saved
`CH n.par`, review the read-only probe and save a separate JSON profile.
This never rewrites the vendor source file.

The generated profile has `verified=false`, `qualification=dasp_parameters`
and a source snapshot with SHA-256. It requires the captured four-channel
USB2404V10 / firmware 2.0 / nominal +/-10 V layout. It retains all four opaque
configuration bytes and checks them again before Start, including host-disabled
channels. It uses the original signed 24-bit nominal conversion,
`10 / 8388608` V/count, and divisor-derived actual sampling rate. Out-of-range
signed 24-bit data is an error.

This path does not establish a hardware gain table, IEPE supply/current or
physical coupling. The profile's `dc` means that software preserves raw DC;
it is not a claim that the frontend is DC coupled. UI controls and provenance
explicitly show these facts as unknown/read-only. Raw voltage and unverified
calibration status survive VNA and Record persistence. No `!C`, zero-drift
subtraction or startup frame discard is introduced.

Valid sensor coefficients are imported once as host EU/Volt settings. The
source inspected on this date has an inconsistent CH1 force-sensor unit, so
that entry is skipped with an explanation; CH2--4 acceleration entries remain
available. The separate sensor import preview shows the transducer identifier
and lets the operator select a destination channel. Thus an accelerometer's
saved CH2 parameters can explicitly be assigned to current CH1, without
reinterpreting hardware bytes. Duplicate destination selections are refused.
Ordinary refreshes do not replace subsequent user sensitivity edits.

Real compatibility-backend checks completed at 2560 samples/s:

- Inst and three-count Avg were saved and read back as VNA. Four enabled
  channels produced `ai0->ai1`, `ai0->ai2`, `ai0->ai3` FRF keys during Avg.
- Record wrote and reread 7680 samples/channel across five binary segments.
  The completed manifest retained source/configuration provenance.
- Eighteen backend Stop/release trials at 2560 and 32000 samples/s, with
  requests 0, 20 or 200 ms after Start, all completed. The largest observed
  request-to-release latency was 94.46 ms.

The operator subsequently connected an IEPE accelerometer to CH1. An
eight-second transport capture and a thirty-second CH1-only Record returned
no full-code samples and stopped cleanly. The latter contained 76800 samples,
mean -0.02999997 V, AC RMS 0.06875144 V, minimum -0.86326122 V and maximum
0.65004706 V, using the nominal voltage conversion. Its binary data was read
back in full. These values describe that particular physical input period;
they do not identify IEPE current or independently certify voltage accuracy.

Three-count software-triggered Avg was also exercised with Delay -25%, 0%
and +25% of a 1024-sample frame. Reported event offsets were +256, 0 and -256
samples respectively; plotted time started at zero in each case. Smart-hit
selection was disabled for this conventional Avg test. Each result was saved
and loaded with identical raw time samples and only CH1 enabled.

The operator specified the connected sensor sensitivity as **5 V/g**. This
corresponds to **0.2 g/V**, or **1.96133 (m/s^2)/V** with standard gravity
9.80665 m/s^2. This replaces the old force-sensor setting for the current
sensor; it must not be inferred from that old DASP CH1 entry.

A further ten-second Record with that coefficient contained 25600 CH1 samples,
no invalid or full-code samples, nominal voltage limits -5.795362..7.084183 V
and AC RMS 0.576231 V. The Record reader and VNA reader both recovered the
1.96133 engineering factor. Loading the saved VNA through the main plotting
path was checked against raw volts times this factor, confirming one
engineering conversion. A local plot and `acceptance.json` are retained in
`tmp_dasp_live_20261111/iepe_5Vg/`. None of these checks measures IEPE current.

Detailed local artifacts are under `tmp_dasp_live_20261111/`, including the
unverified compatibility JSON, VNA files, Record manifests and trigger reports.
They are ignored measurement artifacts, not shipped verified device profiles.
Electrical gain/IEPE qualification, shared-signal reference comparison and
physical 13-hour continuous acquisition remain separate acceptance items.

Final product validation:
`powershell -NoProfile -File scripts\test_vna_product.ps1 -Product python_vna_test`
passed all 650 tests. This change adds no shared analysis/storage module change,
new dependency, version bump or packaged release.
