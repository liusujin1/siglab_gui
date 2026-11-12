# Experimental NetDAQ integration

This backend belongs to PythonVNATest. NI remains the default. No vendor DLL,
new runtime dependency, firmware uploader, automatic subnet scan, or licensing
workaround is included.

## Current qualification status

- Offline: real loopback TCP transport, signed sample boundaries, channel order,
  conversion to volts, actual sample rate, cancellation, 100 start/stop cycles,
  free-run Avg/overlap, Record segmentation, and VNA round-trip are tested.
- Hardware: **not qualified**. Model, firmware, stream control, range tables,
  IEPE current, channel order, calibration and sample continuity still require
  the actual device. A synthetic test fixture is NOT a hardware profile.
- Trigger, pretrigger and AO are intentionally unavailable. No reconnection or
  silent continuation after a network error is attempted.
- A green indicator means a valid device descriptor was received, not that the
  hardware or its calibration has passed acceptance.

## First connection (read-only)

For USB2404V10 connected by USB, use the separate [USB transport contract](USBDAQ.md).
The network IP/port instructions below do not apply to that transport.

Open `File / Device connection` (Chinese UI: `文件 / 设备连接...`). Select
`netdaq`, enter the explicit device IP and use `只读探测`. Leave the profile
empty. Optional channel numbers must be the manufacturer's physical numbers;
leave them blank when their numbering is unknown. Copy the JSON report.

Only device descriptor, raw firmware version and explicitly requested channel
descriptors/current configuration bytes are queried. Opaque configuration bytes
do not establish what each index means electrically. There is no automatic
connection to 192.168.0.251.
Apply remembers the address/ports/profile path in the existing UI preferences;
it does NOT change the default NI backend for the next application launch.

The CLI equivalent (replace the address with the actual device):

```powershell
.\.venv\Scripts\python.exe -m python_vna.app --probe-netdaq-json --netdaq-host 192.0.2.10
```

Save the report together with the device label, firmware, vendor-software
channel settings and a capture of one known-good acquisition. Use only devices
you own or are authorized to operate. Do not enable IEPE by trial and error.

## Verified profile contract (schema 1)

Profiles are device-specific JSON, not the DASP `.chcfg` sensor files. The
backend refuses to configure a device without a verified profile, nonempty
evidence, calibration for every streamed channel and an exact descriptor match.
Firmware/device changes require requalification. Do not bypass a mismatch by
editing descriptor bytes alone.

Required fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `verified` | `true` only after independent bench verification |
| `model`, `evidence` | Actual model and reference to capture/calibration report |
| `descriptor_hex` | Exact 33-byte device reply from the qualified unit |
| `stream_format` | Qualified `packed24`, `raw16`, `raw24`, or `raw32`; signed little-endian |
| `max_sample_rate` | Hardware-qualified limit, NOT a guessed divisor-field limit |
| `init_ad_payload_hex` | Captured 13-byte InitAD payload with qualified channel mask/order |
| `divisor_offset` | Verified offset of the little-endian 4-byte divisor within that payload |
| `channels` | Entries in the exact hardware stream order; logical names are ai0, ai1, ... |

Each channel requires a unique `wire_number`, `channel_type` (the exact reported
`!A raw[4]`), independently established `input_mode`, and a nonempty `options`
list. Input mode is one of `iepe`, `voltage_single_ended_dc`,
`voltage_single_ended_ac`, `voltage_differential_dc`, `quarter_bridge`,
`half_bridge`, `full_bridge`. IEPE current, when known, belongs to the channel's
`iepe_current_ma` field, NOT to a coupling option; a verified IEPE profile
requires a known positive current. No command to switch channel type or current
is guessed. The UI shows IEPE and current as read-only for NetDAQ.
The USB fixed-supply exception is documented in `USBDAQ.md`: a qualified
`device_fixed` or `external` supply may have an unknown current, represented by
JSON null. TCP retains the positive-current requirement.

Each option requires `full_scale_v`, `coupling` (`ac`, `dc`, or `bias`),
`volts_per_count`, and `zero_count`. Only types **0, 6, 8** additionally use
`coupling_index` and `gain_index` for the writable bit fields. All other types
are read-only: exactly one captured option with `configuration_hex` is required,
and its current opaque bytes must match before acquisition. `!C` is never sent
to those channels. Do not put `iepe_enabled` or `iepe_current_ma` in `options`;
the loader rejects this old, misleading classification.

The application selects an exact matching range/coupling/IEPE option. An unknown
combination fails BEFORE writing any settings. Configure is read-only; Start
first checks ALL selected reported channel types, performs only supported
channel writes and substitutes ONLY the divisor in the
captured InitAD payload. The hardware mask remains fixed; disabled application
channels are filtered on the host. This avoids inventing a channel-mask layout.

The initial implementation uses legacy `!I / !S / !P` control on the control
connection and the verified raw/packed stream on the data connection. It does
not mix speculative BeginRead/EndRead commands into that stream. This exact
sequence must be established as appropriate for the model before `verified`
can be set. Other protocol families require a separate qualified implementation.

## Data and safety invariants

- Backend arrays always contain volts. Sensor sensitivity is applied once in
  the existing processing/recording layer, never in the wire decoder.
- Actual rate is derived from the selected clock/divisor and copied to the
  session, frame, time axis and Record header. Requested rate is kept in frame
  provenance. The device's real rate must still be measured during acceptance.
- A frame may span multiple TCP reads/packets. Invalid magic, truncated samples,
  disconnects and timeouts fail the acquisition rather than returning zeros.
- Socket reads/writes have total deadlines and short polling timeouts. UI Stop
  only sets a cancellation event. Stop/close and the hardware stop acknowledgement
  happen on the owning worker, not the GUI thread.
- A lost Start acknowledgement still triggers an attempted hardware Stop. If
  Stop cannot be acknowledged, sockets are closed and an explicit error is
  raised; physical device state must be checked. This latches the backend's
  safety gate and disables further starts until connection settings are
  deliberately reapplied after the device state has been checked. A Record
  whose stop failed is finalized as incomplete, not silently marked complete.
- Device counters/timestamps are not known. `stream_continuity_verified=false`
  is explicit: TCP reliability is NOT proof of absence of device-side sample loss.

## VNA compatibility

Legacy MAT variables remain unchanged. NetDAQ measurements additionally carry
an ASCII `PythonVNAAcquisition_json` field with actual sample rate, bandwidth and
acquisition provenance. PythonVNATest and VIanalysis restore this metadata.
Legacy MATLAB readers ignore the extension and retain their nearest legacy
configuration-rate index; exact non-SigLab rate restoration is NOT promised for
old MATLAB acquisition controls. The stored time/frequency vectors remain real.
NI/old files without the extension continue through the original path.

## Hardware acceptance (pending)

1. Capture descriptor, actual physical channel count/numbering, firmware and
   vendor-software settings. Determine the applicable legacy/raw/packed path.
2. Qualify each used range/coupling/IEPE setting, InitAD field and stream order.
3. Compare DC and known sine input against vendor software and calibrated source;
   verify voltage/EU scale, phase alignment, actual rate and clipping behavior.
4. Run single/multichannel Inst, free-run Avg, overlap and save/open/Record checks.
5. Run at least 100 hardware start/stop cycles, including immediate Stop,
   disconnect, blocked read, lost acknowledgement and application close.
6. Run at least 13 hours at the highest required rate. Verify segment sample
   counts, time continuity, bounded memory, stop response and device-side losses.
7. Trigger/AO require separate qualification and tests before enabling their UI.

## Provenance

Reviewed 2026-10-29 against reverse-engineered NetDAQ.DLL exports and the v2.6
reference under `D:/AI_test/claude/DASP/_ana`. This is not an official vendor SDK.

| Input | SHA256 |
| --- | --- |
| `netdaq_protocol.py` | `7392E5E45B290A54D9EAE13BA659D5C7EEB44A2DCC2656138939999B02CE1596` |
| `PROTOCOL.md` | `1D62D61453EDF1B6915A194F7E52AE98A4DF1532ECF8B88888F2438075B283D6` |
| `extracted/NetDAQ.DLL` | `6537D1D6F36FA2D311465BD671C9E6536EB7B54D15C8004E4A4CEFFC23C1767A` |
| `extracted/Plugin__CL.BPL` | `6D78DC9F58DB19FAB92C113DFD8C74D99795B4CE3152E980941C7009F968C20D` |

The product module is deliberately smaller than that reference: no unverified
register/file commands and no assumption that its synthetic self-tests qualify
the actual hardware.

Supplemental mode-table evidence: `Plugin__CL.BPL` at VA `0x418938..0x418944`
contains pointers to Voltage single-ended DC/AC, Voltage differential DC and
IEPE labels, alongside filter/gain and bridge tables. This separates physical
mode from coupling/gain. String order alone is NOT used to assign numeric
vendor type IDs; that mapping must be confirmed by its call sites/device report.
