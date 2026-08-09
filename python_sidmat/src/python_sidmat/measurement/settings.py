"""Portable measurement-setting snapshots for the Sidmat Open/Save panel."""

from __future__ import annotations

import json
from os import PathLike

__all__ = ["save_measurement_settings", "load_measurement_settings"]

SETTINGS_VERSION = 1


def save_measurement_settings(payload: dict, path: str | PathLike[str]) -> None:
    """Write a validated-enough JSON settings snapshot."""
    data = dict(payload)
    data["schema"] = "python_sidmat.measurement"
    data["version"] = SETTINGS_VERSION
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def load_measurement_settings(path: str | PathLike[str]) -> dict:
    """Read a measurement settings snapshot and reject unrelated JSON."""
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("measurement settings must be a JSON object")
    if data.get("schema") not in (None, "python_sidmat.measurement"):
        raise ValueError(f"unsupported settings schema: {data.get('schema')!r}")
    raw_version = data.get("version", SETTINGS_VERSION)
    if isinstance(raw_version, bool):
        raise ValueError("settings version must be an integer")
    version = int(raw_version)
    if isinstance(raw_version, float) and not raw_version.is_integer():
        raise ValueError("settings version must be an integer")
    if version < 1:
        raise ValueError(f"unsupported settings version {version}")
    if version > SETTINGS_VERSION:
        raise ValueError(f"settings version {version} is newer than supported {SETTINGS_VERSION}")
    return data
