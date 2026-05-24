from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python_vna.controller import VnaController
from python_vna.daq import NIDaqBackend, SimulatedDaqBackend
from python_vna.models import SavedSession
from python_vna.storage import default_session_config, load_legacy_vna, save_legacy_vna


def _build_backend(name: str):
    if name == "ni":
        return NIDaqBackend()
    if name == "simulated":
        return SimulatedDaqBackend()
    raise ValueError(f"Unsupported backend: {name}")


def _load_config(template_path: str | None):
    if template_path:
        return load_legacy_vna(template_path).config
    return default_session_config()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture one VNA averaged/instant measurement and save a legacy .vna file."
    )
    parser.add_argument("output", help="Destination .vna path.")
    parser.add_argument(
        "--template",
        help="Optional legacy .vna whose channel/acquisition setup should be reused.",
    )
    parser.add_argument("--backend", choices=["ni", "simulated"], default="ni")
    parser.add_argument("--device", default=None, help="Preferred NI device name, e.g. Dev1.")
    parser.add_argument("--inst", action="store_true", help="Capture instant mode instead of Avg.")
    parser.add_argument("--count", type=int, default=None, help="Override average frame count.")
    parser.add_argument("--sample-rate", type=float, default=None, help="Override sample rate.")
    parser.add_argument("--frame-size", type=int, default=None, help="Override frame size.")
    args = parser.parse_args()

    config = _load_config(args.template)
    if args.count is not None:
        config.acquisition.averaging.count = max(1, int(args.count))
    if args.sample_rate is not None:
        config.acquisition.sample_rate = float(args.sample_rate)
    if args.frame_size is not None:
        config.acquisition.frame_size = int(args.frame_size)
    if args.inst:
        config.acquisition.trigger.enabled = False
        config.acquisition.trigger.source = "immediate"

    backend = _build_backend(args.backend)
    controller = VnaController(backend, config)
    averaging_enabled = not args.inst
    target_count = config.acquisition.averaging.count if averaging_enabled else 1
    measurement = None
    try:
        controller.set_averaging_enabled(averaging_enabled)
        controller.configure(device_name=args.device)
        controller.start()
        for _index in range(target_count):
            measurement = controller.read_and_process()
            avg_count = measurement.metadata.get("average_count", 0)
            avg_target = measurement.metadata.get("average_target", 0)
            if averaging_enabled:
                print(f"avg {avg_count}/{avg_target}", flush=True)
        if measurement is None:
            raise RuntimeError("No measurement was captured.")
    finally:
        try:
            controller.abort()
        except Exception:
            pass
        controller.close()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    save_legacy_vna(SavedSession(config=config, measurement=measurement), output)
    print(f"saved {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
