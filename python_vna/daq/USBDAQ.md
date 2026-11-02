# Experimental USB2404 transport

This feature belongs to PythonVNATest. NI remains the startup default. USB uses
the existing NetDAQ backend with `transport="usb"`; it does not use TCP ports.
No third-party Python package, vendor 32-bit DAQ DLL, firmware loader, driver
installer, or new shared analysis/storage change is included in this extension.

## Access

In **File / Device connection** select `netdaq`, then the
`USB2404V10 (0547:1003)` transport. IP/port controls are disabled for USB.
Device index starts at zero. An empty verified-profile field allows read-only
probing, not Inst/Avg/Record. Connection settings are remembered without changing
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
as synthetic and are never used for hardware tests. Trigger/pretrigger and AO
remain disabled. Once a real profile is qualified, the same Inst/Avg/overlap,
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
