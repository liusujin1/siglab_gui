"""python_sidmat CLI — headless measurement (connect -> trace -> export CSV).

Useful for verifying against real hardware without the GUI:

    python -m python_sidmat.cli --mock --length 8192 --avg 3 -o out.csv
    python -m python_sidmat.cli --port COM3 --baud 57600 --length 8192
"""

from __future__ import annotations

import argparse
import csv
import sys

import numpy as np

from python_sidmat.analysis.pwelch import pwelch
from python_sidmat.analysis.windows import WindowType
from python_sidmat.backend.controller import Controller
from python_sidmat.measurement.trace import TraceParameters


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python-sidmat", description=__doc__)
    parser.add_argument("--mock", action="store_true", help="use mock (legacy alias)")
    parser.add_argument(
        "--backend", choices=["server", "serial", "mock"], default="server"
    )
    parser.add_argument("--port", default="COM1", help="physical serial port")
    parser.add_argument("--baud", type=int, default=57600, help="baud rate")
    parser.add_argument("--server", default="127.0.0.1:47619")
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--comm-server-exe", default=None)
    parser.add_argument("--no-auto-start", action="store_true")
    parser.add_argument("--ch0", default="0 0 0", help="ch0 IO 'Type Main Sub'")
    parser.add_argument("--ch1", default="0 1 0", help="ch1 IO 'Type Main Sub'")
    parser.add_argument("--length", type=int, default=8192, help="samples per trace")
    parser.add_argument("--undersample", type=int, default=1)
    parser.add_argument("--avg", type=int, default=3, help="averages")
    parser.add_argument("--nfft", type=int, default=0, help="FFT length (0=auto)")
    parser.add_argument("--window", type=str, default="HANNING", help="window name")
    parser.add_argument("-o", "--output", default="", help="CSV output path")
    return parser


def _parse_io(text: str) -> tuple[int, int, int]:
    parts = text.split()
    if len(parts) != 3:
        raise ValueError(f"IO must be 'Type Main Sub', got {text!r}")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def _auto_nfft(n_samples: int) -> int:
    if n_samples < 2:
        raise ValueError("at least two samples are required for FRF analysis")
    n = 1
    while n * 2 <= n_samples:
        n *= 2
    return max(2, min(n, 4096))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        window = WindowType[args.window.upper()]
    except KeyError:
        valid = ", ".join(item.name for item in WindowType if item is not WindowType.LASTWINDOW)
        raise SystemExit(f"invalid --window {args.window!r}; choose one of: {valid}")

    backend = "mock" if args.mock else args.backend
    if backend == "mock":
        controller = Controller.connect_mock(readonly=False)
    elif backend == "server":
        controller = Controller.connect_server(
            args.port,
            baudrate=args.baud,
            server=args.server,
            token_file=args.token_file,
            comm_server_exe=args.comm_server_exe,
            auto_start=not args.no_auto_start,
        )
    else:
        controller = Controller.connect(args.port, baudrate=args.baud)

    with controller:
        fs = controller.get_sample_frequency()
        print(f"Controller connected. Sample frequency: {fs:.0f} Hz")

        trace = TraceParameters(
            trace_ch0=_make_io(args.ch0),
            trace_ch1=_make_io(args.ch1),
            undersamples=args.undersample,
            no_samples=args.length,
            average_number=args.avg,
        )
        controller.set_trace(trace)
        print(
            f"Trace: {trace.trace_ch0.name} vs {trace.trace_ch1.name}, "
            f"n={args.length}, us={args.undersample}, avg={args.avg}"
        )

        from python_sidmat.measurement.engine import MeasurementEngine

        engine = MeasurementEngine(controller, trace, sample_frequency=fs,
                                   on_average_complete=None)
        raw = engine.run()
        ch0 = raw.channel(0)
        ch1 = raw.channel(1)
        print(f"Acquired {len(ch0)} samples across {raw.avg_num} average(s).")

        if len(ch0) == 0 or len(ch1) == 0:
            raise RuntimeError("controller returned no complete trace data")
        nfft = args.nfft if args.nfft else _auto_nfft(min(len(ch0), len(ch1)))
        effective_fs = raw.effective_sample_rate or fs
        result = pwelch(ch0, ch1, window, 50, nfft, len(ch0), effective_fs)
        print(f"pwelch: nfft={nfft}, {len(result.freq)} frequency bins")

        if args.output:
            _write_csv(args.output, raw, result)
            print(f"Wrote {args.output}")
        else:
            # brief console summary of the FRF at the peak
            idx = int(result.amplitude.argmax())
            mag_db = 20.0 * np.log10(max(float(result.amplitude[idx]), 1e-30))
            print(
                f"Peak |H1| at {result.freq[idx]:.2f} Hz: "
                f"{mag_db:.3f} dB (coherence "
                f"{result.coherence[idx]:.3f})"
            )

    return 0


def _make_io(text: str):
    from python_sidmat.backend.iosignal import IOType

    return IOType(*_parse_io(text))


def _write_csv(path: str, raw, result) -> None:
    ch0 = raw.channel(0)
    ch1 = raw.channel(1) if raw.channel_count > 1 else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["freq_Hz", "H1_re", "H1_im", "H1_mag", "H1_phase_deg",
             "coherence", "spec1", "spec2"]
        )
        for i, freq in enumerate(result.freq):
            writer.writerow(
                [f"{freq:.6g}", f"{result.re[i]:.6g}", f"{result.im[i]:.6g}",
                 f"{result.amplitude[i]:.6g}", f"{result.phase_deg[i]:.6g}",
                 f"{result.coherence[i]:.6g}", f"{result.spec1[i]:.6g}",
                 f"{result.spec2[i]:.6g}"]
            )
        writer.writerow([])
        writer.writerow(["sample", "ch0", "ch1"])
        for i in range(max(len(ch0), len(ch1))):
            c0 = ch0[i] if i < len(ch0) else 0.0
            c1 = ch1[i] if i < len(ch1) else 0.0
            writer.writerow([i, f"{c0:.17g}", f"{c1:.17g}"])


if __name__ == "__main__":
    raise SystemExit(main())
