"""Compare writable controller values before and after a hardware probe."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            equivalent(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            equivalent(a, b) for a, b in zip(left, right)
        )
    left_number = _number(left)
    right_number = _number(right)
    if left_number is not None and right_number is not None:
        return math.isclose(
            left_number, right_number, rel_tol=1e-5, abs_tol=1e-7
        )
    return str(left).strip().upper() == str(right).strip().upper()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _writable_projection(key: str, value: Any) -> Any:
    """Remove live/read-only fields returned alongside writable settings."""
    if key == "Controller/Position/pneumatic loop words":
        # BGSST returns Position, Pneumatic, DigitalInput and DigitalOutput,
        # while BSSST can write only the first two loop words.  The digital
        # status words legitimately change while the controller is running.
        return value[:2] if isinstance(value, (list, tuple)) else value
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument(
        "--write-report",
        type=Path,
        required=True,
        help="same-write report used to select writable parameter keys",
    )
    args = parser.parse_args()

    before = _load(args.before).get("values", {})
    after = _load(args.after).get("values", {})
    report = _load(args.write_report)
    writable_keys = {
        f"{result['page']}/{result['name']}"
        for result in report.get("results", [])
        if result.get("phase") == "WRITE"
        and result.get("status") in {"PASS", "SKIP_STATE"}
    }

    missing = sorted(
        key for key in writable_keys if key not in before or key not in after
    )
    changed = sorted(
        key
        for key in writable_keys
        if key in before
        and key in after
        and not equivalent(
            _writable_projection(key, before[key]),
            _writable_projection(key, after[key]),
        )
    )
    for key in missing:
        print(f"MISSING {key}")
    for key in changed:
        print(
            f"CHANGED {key}: "
            f"before={before[key]!r} after={after[key]!r}"
        )
    print(
        "SUMMARY "
        f"writable={len(writable_keys)} missing={len(missing)} changed={len(changed)}"
    )
    return 2 if missing or changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
