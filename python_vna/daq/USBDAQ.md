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
  reported type/configuration must match. Range/coupling and IEPE are read-only.
- `volts_per_count`, `zero_count`, physical mode/current, maximum sample rate and
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
