from __future__ import annotations

from dataclasses import fields, is_dataclass
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

import numpy as np

from python_vna.analysis_data import AnalysisDataset, AnalysisSeries, ContinuousSegmentInfo


_TYPES = {item.__name__: item for item in (AnalysisDataset, AnalysisSeries, ContinuousSegmentInfo)}


def save_modal_session(path: str | Path, state: dict) -> Path:
    destination = Path(path)
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            array_count = 0

            def encode(value):
                nonlocal array_count
                if isinstance(value, np.ndarray):
                    name = f"arrays/{array_count}.npy"
                    array_count += 1
                    buffer = io.BytesIO()
                    np.save(buffer, value, allow_pickle=False)
                    archive.writestr(name, buffer.getvalue())
                    return {"type": "array", "value": name}
                if isinstance(value, np.generic):
                    return encode(value.item())
                if isinstance(value, Path):
                    return {"type": "path", "value": str(value)}
                if isinstance(value, complex):
                    return {"type": "complex", "value": [value.real, value.imag]}
                if is_dataclass(value) and type(value).__name__ in _TYPES:
                    return {"type": type(value).__name__, "value": {field.name: encode(getattr(value, field.name)) for field in fields(value)}}
                if isinstance(value, dict):
                    return {"type": "dict", "value": [[encode(key), encode(item)] for key, item in value.items()]}
                if isinstance(value, (list, tuple)):
                    return {"type": "tuple" if isinstance(value, tuple) else "list", "value": [encode(item) for item in value]}
                if value is None or isinstance(value, (str, bool, int, float)):
                    return value
                raise ValueError(f"Unsupported session value: {type(value).__name__}")

            archive.writestr("session.json", json.dumps({"format": "vianalysis-modal", "version": 1, "state": encode(state)}, ensure_ascii=False))
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def load_modal_session(path: str | Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 2 * 1024**3:
            raise ValueError("Modal session exceeds the 2 GiB limit")
        document = json.loads(archive.read("session.json"))
        if document.get("format") != "vianalysis-modal" or document.get("version") != 1:
            raise ValueError("Unsupported modal session format/version")

        def decode(value):
            if not isinstance(value, dict):
                return value
            kind, payload = value["type"], value["value"]
            if kind == "array":
                return np.load(io.BytesIO(archive.read(payload)), allow_pickle=False)
            if kind == "path":
                return Path(payload)
            if kind == "complex":
                return complex(*payload)
            if kind == "dict":
                return {decode(key): decode(item) for key, item in payload}
            if kind in {"tuple", "list"}:
                items = [decode(item) for item in payload]
                return tuple(items) if kind == "tuple" else items
            if kind in _TYPES:
                return _TYPES[kind](**{key: decode(item) for key, item in payload.items()})
            raise ValueError(f"Unsupported session type: {kind}")

        state = decode(document["state"])
    if not isinstance(state, dict) or not isinstance(state.get("datasets"), list):
        raise ValueError("Invalid modal session data")
    if not all(isinstance(dataset, AnalysisDataset) and not dataset.is_continuous for dataset in state["datasets"]):
        raise ValueError("Modal session must contain embedded datasets")
    for key, width in (("points", 12), ("lines", 4)):
        if not isinstance(state.get(key), list) or any(not isinstance(row, list) or len(row) != width for row in state[key]):
            raise ValueError(f"Invalid modal session {key}")
    for key in ("auto_peaks", "manual_peaks"):
        values = state.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, (int, float)) or not np.isfinite(value) for value in values):
            raise ValueError(f"Invalid modal session {key}")
    frequency = state.get("active_frequency")
    if frequency is not None and (not isinstance(frequency, (int, float)) or not np.isfinite(frequency)):
        raise ValueError("Invalid modal frequency")
    for key in ("phase_index", "preview_tab", "work_tab"):
        if not isinstance(state.get(key, 0), int):
            raise ValueError(f"Invalid modal session {key}")
    cameras = state.get("cameras", {})
    if not isinstance(cameras, dict):
        raise ValueError("Invalid modal cameras")
    for camera in cameras.values():
        if not isinstance(camera, dict) or any(not isinstance(camera.get(key), (int, float)) or not np.isfinite(camera[key]) for key in ("distance", "azimuth", "elevation", "fov")):
            raise ValueError("Invalid modal camera")
        center = np.asarray(camera.get("center"), dtype=float)
        if center.shape != (3,) or not np.all(np.isfinite(center)):
            raise ValueError("Invalid modal camera center")
    mode = state.get("last_mode")
    if mode is not None:
        if not isinstance(mode, dict):
            raise ValueError("Invalid modal result")
        coords = np.asarray(mode.get("coords"), dtype=float)
        displacement = np.asarray(mode.get("disp_complex"), dtype=complex)
        if coords.ndim != 2 or coords.shape[1] != 3 or displacement.shape != coords.shape:
            raise ValueError("Invalid modal result dimensions")
        if not np.all(np.isfinite(coords)) or not np.all(np.isfinite(displacement)) or len(mode.get("labels", [])) != len(coords):
            raise ValueError("Invalid modal result points")
        if not isinstance(mode.get("scale"), (int, float)) or not np.isfinite(mode["scale"]):
            raise ValueError("Invalid modal display scale")
    return state
