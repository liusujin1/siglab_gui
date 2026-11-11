"""USB2404 transport acceptance; reported voltages are explicitly uncalibrated.

Default CLI use is read-only. Capture uses only the existing InitAD/Start/Stop
sequence, never channel writes, USB resets, or a fabricated qualified profile.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import sys
import time

import numpy as np

from .base import BackendFrame
from .netdaq_protocol import NetDaqProtocolError, NetDaqStopped
from .quality import AcquisitionQuality
from .usbdaq import UsbConnection, UsbStream, descriptor_info, sampling_divisor
from ..models import ChannelConfig


class UsbValidationFailure(NetDaqProtocolError):
    def __init__(self, error, report):
        super().__init__(str(error))
        self.report = report


def _snapshot(control, count):
    result = {}
    for number in range(count):
        kind, length = control.channel_descriptor(number)
        result[str(number)] = dict(
            type=kind, config_bytes=length,
            configuration_hex=control.channel_configuration(number).hex())
    return result


def validate_usb_device(*, seconds=0.0, sample_rate=2560.0, channel_mask=None,
                        index=0, timeout=2.0, stop_event=None,
                        connection_factory=UsbConnection):
    """Return an auditable, bounded-memory check without qualifying calibration."""
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or seconds < 0
            or isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float))
            or not math.isfinite(sample_rate) or sample_rate <= 0):
        raise ValueError("Capture seconds must be nonnegative and rate positive and finite")
    if channel_mask is not None and (
            isinstance(channel_mask, bool) or not isinstance(channel_mask, int)
            or not 1 <= channel_mask <= 15):
        raise ValueError("Channel mask must be within 1..15")
    report = dict(
        format="python_vna_usb_transport_validation", schema_version=1,
        started_utc=datetime.now(timezone.utc).isoformat(), status="running",
        capture_seconds_requested=seconds, sample_rate_requested=sample_rate,
        stop_acknowledged=None, configuration_unchanged=None,
        calibration_verified=False, stream_continuity_verified=False,
        note="Nominal volts from descriptor only; no gain/IEPE settings, DC removal "
             "or startup frame discard. Delivery rate is not an independent clock calibration.")
    control = connection_factory(index=index, timeout=timeout, stop_event=stop_event)
    try:
        with control:
            raw = control.descriptor()
            info = descriptor_info(raw)
            count = info["channel_count"]
            mask = (1 << count) - 1 if channel_mask is None else channel_mask
            if mask >> count:
                raise ValueError("Channel mask exceeds reported physical channel count")
            before = _snapshot(control, count)
            report.update(
                usb=dict(control.info), descriptor_hex=raw.hex(), descriptor=info,
                firmware_hex=control.command(0x56, size=7).hex(),
                configuration_before=before, channel_mask=mask)
            if seconds:
                divisor, rate = sampling_divisor(raw, sample_rate)
                numbers = [i for i in range(count) if mask & (1 << i)]
                names = [f"ai{i}" for i in numbers]
                low = info["nominal_voltage_min_mv"] * .001
                high = info["nominal_voltage_max_mv"] * .001
                channels = [ChannelConfig(
                    name=name, physical_name=name, min_value=low, max_value=high)
                    for name in names]
                quality = AcquisitionQuality(channels, backend_name="USB transport diagnostic")
                target = max(1, math.ceil(seconds * rate))
                total, first_end, first_samples, last_end = 0, None, 0, None
                count_min = np.full(len(names), info["adc_max"], dtype=np.int64)
                count_max = np.full(len(names), info["adc_min"], dtype=np.int64)
                payload = struct.pack(">I", divisor) + b"\x01" + struct.pack("<I", mask) + bytes(4)
                report.update(
                    configured_sample_rate=rate, divisor=divisor,
                    init_ad_payload_hex=payload.hex(), channel_order=names)
                control.initialize_ad(payload)
                control.prepare_stream(rate, len(names))
                stream = UsbStream(control)
                try:
                    # Start can fail after actually starting the device.
                    report["stop_acknowledged"] = False
                    control.start()
                    started = time.monotonic()
                    while total < target:
                        size = min(1024, target - total)
                        deadline = time.monotonic() + timeout + size / rate
                        if total == 0:
                            deadline += 5.0
                        block = stream.receive(size * len(names) * 4, deadline=deadline)
                        last_end = time.monotonic()
                        counts = np.frombuffer(block, dtype="<i4").reshape(size, len(names)).T
                        if np.any(counts < info["adc_min"]) or np.any(counts > info["adc_max"]):
                            raise NetDaqProtocolError("USBDAQ stream contains invalid signed 24-bit samples")
                        count_min = np.minimum(count_min, counts.min(axis=1))
                        count_max = np.maximum(count_max, counts.max(axis=1))
                        volts = (counts.astype(float) - info["adc_min"]) * info["nominal_volts_per_count"] + low
                        quality.add(BackendFrame(
                            rate, names, volts, np.empty(0), quality.frames))
                        total += size
                        if first_end is None:
                            first_end, first_samples = last_end, size
                        report["samples_per_channel"] = total
                finally:
                    try:
                        if control.may_be_running or control.stop_uncertain:
                            stop_started = time.monotonic()
                            control.stop()
                            report["stop_seconds"] = time.monotonic() - stop_started
                            report["stop_acknowledged"] = True
                    finally:
                        stream.close()
                        report["quality"] = quality.report()
                        report["counts_min"] = count_min.tolist() if total else []
                        report["counts_max"] = count_max.tolist() if total else []
                report.update(
                    first_block_seconds=first_end - started,
                    capture_wall_seconds=last_end - started,
                    delivery_samples_per_second=(
                        (total - first_samples) / (last_end - first_end)
                        if total > first_samples and last_end > first_end else None))
            after = _snapshot(control, count)
            report.update(configuration_after=after, configuration_unchanged=before == after)
            if before != after:
                raise NetDaqProtocolError("USBDAQ configuration changed during validation")
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            status="cancelled" if isinstance(exc, (KeyboardInterrupt, NetDaqStopped)) else "failed",
            error=f"{type(exc).__name__}: {exc}")
        raise UsbValidationFailure(exc, report) from exc
    finally:
        report["transport_closed"] = not control.connected
        report["ended_utc"] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=0, help="0 = read-only probe")
    parser.add_argument("--sample-rate", type=float, default=2560)
    parser.add_argument("--channel-mask", type=lambda text: int(text, 0))
    parser.add_argument("--usb-index", type=int, default=0)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.cycles <= 10000:
        parser.error("cycles must be within 1..10000")
    # Reserve the report before starting hardware, and preserve it after every cycle.
    with args.output.open("x", encoding="utf-8") as output:
        summary = dict(format="python_vna_usb_validation_run", status="running", cycles=[])

        def save():
            output.seek(0)
            json.dump(summary, output, ensure_ascii=True, indent=2, allow_nan=False)
            output.truncate()
            output.flush()

        save()
        for number in range(args.cycles):
            try:
                report = validate_usb_device(
                    seconds=args.seconds, sample_rate=args.sample_rate,
                    channel_mask=args.channel_mask, index=args.usb_index)
            except (Exception, KeyboardInterrupt) as exc:
                failure = exc.report if isinstance(exc, UsbValidationFailure) else dict(
                    status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                    error=f"{type(exc).__name__}: {exc}")
                summary["cycles"].append(failure)
                summary["status"] = failure["status"]
                save()
                print(str(exc), file=sys.stderr)
                return 130 if summary["status"] == "cancelled" else 1
            summary["cycles"].append(report)
            save()
            print(f"cycle {number + 1}/{args.cycles}: {report['status']}", file=sys.stderr)
        summary["status"] = "passed"
        save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
