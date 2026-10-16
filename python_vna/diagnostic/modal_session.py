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
    if "identification" in state:
        _validate_identification_state(state["identification"])
    if "candidate_search" in state:
        _validate_candidate_state(state["candidate_search"])
    if mode is not None and mode.get("preview_only"):
        local = mode.get("local_fit")
        if not isinstance(local, dict) or not isinstance(local.get("fingerprint"), str):
            raise ValueError("Invalid local modal preview")
        try:
            accepted = np.asarray(local["accepted"])
            coefficients, errors = np.asarray(local["coefficients"]), np.asarray(local["errors"])
            if (accepted.ndim != 1 or accepted.dtype != bool or not accepted.any()
                    or coefficients.shape != accepted.shape or errors.shape != accepted.shape
                    or not np.isfinite(coefficients[accepted]).all() or not np.isfinite(errors).all()
                    or local.get("mass_kg") is not None or local.get("normalization") != "display_only"):
                raise ValueError("Invalid local modal preview")
        except (KeyError, TypeError):
            raise ValueError("Invalid local modal preview") from None
    return state


def _validate_candidate_state(state):
    from python_vna.diagnostic.modal_candidates import CandidateSettings, validate_candidate_settings

    def fail():
        raise ValueError("Invalid modal candidate session")

    try:
        if not isinstance(state, dict) or not isinstance(state.get("settings"), dict):
            fail()
        validate_candidate_settings(CandidateSettings(**state["settings"]))
        result = state.get("result")
        if result is None:
            return
        if (not isinstance(result, dict) or not isinstance(result.get("fingerprint"), str)
                or not isinstance(result.get("stale"), bool)):
            fail()
        validate_candidate_settings(CandidateSettings(**result["settings"]))
        if not result["stale"] and result["settings"] != state["settings"]:
            fail()
        f = np.asarray(result["frequency_hz"], dtype=float)
        db = np.asarray(result["indicator_db"], dtype=float)
        if f.ndim != 1 or f.size < 8 or not np.isfinite(f).all() or np.any(f <= 0) or np.any(np.diff(f) <= 0):
            fail()
        if db.shape != f.shape or np.isinf(db).any():
            fail()
        labels = result["labels"]
        if not isinstance(labels, list) or not labels or any(not isinstance(label, str) for label in labels):
            fail()
        if not isinstance(result["warnings"], list) or any(not isinstance(w, str) for w in result["warnings"]):
            fail()
        if not isinstance(result["candidates"], list) or len(result["candidates"]) > 200:
            fail()
        for row in result["candidates"]:
            if not f[0] <= row["frequency_hz"] <= f[-1] or not np.isfinite(row["score"]):
                fail()
            if not isinstance(row["methods"], list) or not row["methods"] or set(row["methods"]) - {"CMIF", "突出度", "分频段极点"}:
                fail()
            families = row["families"]
            if not isinstance(families, list) or not families or set(families) - {"spectral", "pole"}:
                fail()
            if not isinstance(row["confirmed"], bool) or row["confirmed"] != (len(set(families)) >= 2):
                fail()
            if not isinstance(row["stable_orders"], int) or row["stable_orders"] < 0:
                fail()
            if row["damping_ratio"] is not None and not 0 < row["damping_ratio"] < .3:
                fail()
            if (not isinstance(row["channels"], list) or any(not isinstance(j, int) or not 0 <= j < len(labels) for j in row["channels"])
                    or not isinstance(row["bands"], list)):
                fail()
            for band in row["bands"]:
                if len(band) != 2 or not np.isfinite(band).all() or band[0] >= band[1]:
                    fail()
    except (TypeError, KeyError, OverflowError):
        fail()


def _validate_identification_state(state: dict) -> None:
    """Validate optional additive v1 state before a page replaces its data."""
    def fail():
        raise ValueError("Invalid modal identification session")

    def finite_array(value, shape):
        array = np.asarray(value)
        if array.shape != shape or not np.issubdtype(array.dtype, np.number) or not np.all(np.isfinite(array)):
            fail()

    if not isinstance(state, dict) or state.get("schema_version") != 1:
        fail()
    if not isinstance(state.get("files"), dict) or not isinstance(state.get("controls"), dict):
        fail()
    if not isinstance(state.get("driver"), str) or state.get("direction") not in (0, 1, 2):
        fail()
    for key in ("force_confirm", "driver_confirm", "stale"):
        if not isinstance(state.get(key), bool):
            fail()
    for key, value in state["controls"].items():
        if not isinstance(key, str) or not isinstance(value, (int, float)) or not np.isfinite(value):
            fail()
    for key, options in state["files"].items():
        if not isinstance(key, str) or not isinstance(options, dict):
            fail()
        for name in ("force_key", "force_unit", "response_unit"):
            if not isinstance(options.get(name), str):
                fail()
        for name in ("force_factor", "response_factor"):
            if not isinstance(options.get(name), (int, float)) or not np.isfinite(options[name]) or options[name] <= 0:
                fail()
    result = state.get("result")
    if result is None:
        return
    if not isinstance(result, dict) or not isinstance(result.get("fingerprint"), str):
        fail()
    measurements, modes, poles = result.get("measurements"), result.get("modes"), result.get("poles")
    if not isinstance(measurements, list) or not measurements or not isinstance(modes, list) or not isinstance(poles, list):
        fail()
    channels = len(measurements)
    if not isinstance(result.get("driving_index"), int) or not 0 <= result["driving_index"] < channels:
        fail()
    frequency = np.asarray(result.get("frequency_hz"))
    if frequency.ndim != 1 or frequency.size < 2:
        fail()
    finite_array(frequency, frequency.shape)
    if np.any(frequency <= 0) or np.any(np.diff(frequency) <= 0):
        fail()
    finite_array(result.get("measured"), (frequency.size, channels))
    if result.get("reconstructed") is not None:
        finite_array(result["reconstructed"], (frequency.size, channels))
        finite_array(result.get("channel_errors"), (channels,))
        finite_array(result.get("residual_coefficients"), (2, channels))
    finite_array(result.get("automac"), (len(modes), len(modes)))
    if not isinstance(result.get("warnings"), list) or any(not isinstance(w, str) for w in result["warnings"]):
        fail()
    if not isinstance(result.get("settings"), dict) or "frequency_tolerance" not in result["settings"]:
        fail()
    for key in ("minimum_hz", "maximum_hz", "maximum_order", "frequency_tolerance", "damping_tolerance", "mac_tolerance",
                "stable_orders", "maximum_fit_error", "maximum_complexity"):
        value = result["settings"].get(key)
        if not isinstance(value, (int, float)) or not np.isfinite(value):
            fail()
    if not 0 < result["settings"]["frequency_tolerance"] < 1:
        fail()
    for measurement in measurements:
        if not isinstance(measurement, dict) or measurement.get("axis") not in (0, 1, 2):
            fail()
        finite_array(measurement.get("coordinates"), (3,))
        for key in ("label", "point", "file_name", "force_key", "response_key"):
            if not isinstance(measurement.get(key), str):
                fail()
    for pole in poles:
        if not isinstance(pole, dict) or not isinstance(pole.get("order"), int) or not isinstance(pole.get("stable_count"), int) or not isinstance(pole.get("stable"), bool):
            fail()
        finite_array([pole.get("frequency_hz"), pole.get("damping_ratio")], (2,))
        if pole["frequency_hz"] <= 0 or not 0 < pole["damping_ratio"] < .5:
            fail()
        finite_array(pole.get("vector"), (channels,))
    for mode in modes:
        if not isinstance(mode, dict) or not isinstance(mode.get("status"), str) or mode.get("normalization") != "driving_point_unity":
            fail()
        finite_array([mode.get(key) for key in ("frequency_hz", "damping_ratio", "fit_error", "local_error", "complexity")], (5,))
        finite_array(mode.get("shape"), (channels,))
        finite_array(mode.get("coefficients"), (channels,))
        if not isinstance(mode.get("source_order"), int):
            fail()
        for key in ("mass_kg", "stiffness_n_m", "damping_n_s_m", "coherence"):
            value = mode.get(key)
            if value is not None and (not isinstance(value, (int, float)) or not np.isfinite(value)):
                fail()
