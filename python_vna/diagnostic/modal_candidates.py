"""Candidate-only SIMO indicators. Never supplies residues or modal masses."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

import numpy as np
from scipy import signal

from python_vna.diagnostic.modal_identification import ModalCancelled, common_denominator_poles


METHOD_NAMES = {"cmif": "CMIF", "prominence": "突出度", "segmented": "分频段极点", "consensus": "谱峰/极点交叉"}


@dataclass(frozen=True)
class CandidateSettings:
    method: str = "cmif"
    minimum_hz: float = 0.0
    maximum_hz: float = 0.0
    prominence_db: float = 1.0
    minimum_spacing_hz: float = 0.0
    relative_spacing: float = .002
    maximum_candidates: int = 24
    band_width_hz: float = 0.0
    band_overlap: float = .25
    maximum_order: int = 16
    frequency_tolerance: float = .01
    damping_tolerance: float = .2
    stable_orders: int = 3


def _cancelled(cancel):
    if cancel is not None and cancel():
        raise ModalCancelled("已取消候选搜索")


def validate_candidate_settings(settings: CandidateSettings):
    if settings.method not in METHOD_NAMES:
        raise ValueError("未知候选搜索算法")
    numeric = list(asdict(settings).values())[1:]
    if not np.isfinite(numeric).all() or min(settings.minimum_hz, settings.maximum_hz, settings.prominence_db,
                                            settings.minimum_spacing_hz, settings.relative_spacing, settings.band_width_hz) < 0:
        raise ValueError("候选搜索参数必须是有限非负数")
    if settings.maximum_hz and settings.maximum_hz <= settings.minimum_hz:
        raise ValueError("候选搜索频段下限必须小于上限")
    if (not 0 <= settings.band_overlap < .8 or settings.maximum_order not in range(6, 41, 2)
            or not 1 <= settings.maximum_candidates <= 200 or not 3 <= settings.stable_orders <= 10
            or not 0 < settings.frequency_tolerance < 1 or not 0 < settings.damping_tolerance < 1):
        raise ValueError("候选数量、频段重叠或极点阶次/稳定性设置无效")
    if any(not isinstance(value, (int, np.integer)) or isinstance(value, bool)
           for value in (settings.maximum_order, settings.maximum_candidates, settings.stable_orders)):
        raise ValueError("候选数量和模型阶次必须是整数")


def prepare_candidate_input(curves, settings: CandidateSettings):
    validate_candidate_settings(settings)
    clean, warnings = [], []
    for curve in curves:
        f = np.asarray(curve["frequency_hz"], dtype=float).ravel()
        h = np.asarray(curve["values"]).ravel()
        if f.size != h.size or not np.iscomplexobj(h):
            warnings.append(f"{curve['label']}：不是有效复数 FRF，已跳过")
            continue
        valid_f = np.isfinite(f) & (f > 0) & (f >= settings.minimum_hz)
        if settings.maximum_hz:
            valid_f &= f <= settings.maximum_hz
        f, h = f[valid_f], h[valid_f].astype(complex)
        if f.size < 8 or np.any(np.diff(f) <= 0):
            warnings.append(f"{curve['label']}：频段内不足 8 点或频率未严格递增，已跳过")
            continue
        clean.append((str(curve["label"]), f, h))
    if not clean:
        raise ValueError("没有可搜索的复数 FRF；自谱/PSD 不能替代复数 FRF")
    base = max(clean, key=lambda c: float(np.median(np.diff(c[1]))))[1]
    low, high = min(c[1][0] for c in clean), max(c[1][-1] for c in clean)
    # Use measured bins only; no extrapolation and no interpolation across missing samples.
    grid = base[(base >= low) & (base <= high)].copy()
    values = np.full((grid.size, len(clean)), complex(np.nan, np.nan))
    for j, (_label, f, h) in enumerate(clean):
        valid = np.isfinite(h)
        runs = np.split(np.flatnonzero(valid), np.flatnonzero(np.diff(np.flatnonzero(valid)) > 1) + 1)
        for run in runs:
            if run.size < 2:
                continue
            use = (grid >= f[run[0]]) & (grid <= f[run[-1]])
            values[use, j] = np.interp(grid[use], f[run], h[run].real) + 1j * np.interp(grid[use], f[run], h[run].imag)
    keep = np.sum(np.isfinite(values), axis=0) >= 8
    values = values[:, keep]
    labels = [c[0] for c, usable in zip(clean, keep) if usable]
    if not labels or grid.size < 8:
        raise ValueError("有效 FRF 公共参考网格不足，无法搜索候选")
    digest = hashlib.sha256(json.dumps(asdict(settings), sort_keys=True).encode())
    digest.update(repr(labels).encode())
    for array in (grid, values):
        digest.update(array.tobytes())
        array.setflags(write=False)
    return {"frequency_hz": grid, "values": values, "labels": labels,
            "settings": settings, "fingerprint": digest.hexdigest(), "warnings": warnings}


def _indicator(values):
    normalized = np.full(values.shape, np.nan, dtype=float)
    for j in range(values.shape[1]):
        valid = np.isfinite(values[:, j])
        scale = np.median(abs(values[valid, j])) if valid.any() else 0
        if scale > 0:
            normalized[valid, j] = abs(values[valid, j]) / scale
    valid = np.any(np.isfinite(normalized), axis=1)
    indicator = np.full(values.shape[0], np.nan)
    indicator[valid] = np.sqrt(np.nansum(normalized[valid] ** 2, axis=1))
    maximum = np.nanmax(indicator) if valid.any() else 0
    db = np.full(indicator.shape, np.nan)
    if maximum > 0:
        db[valid] = 20 * np.log10(np.maximum(indicator[valid] / maximum, 1e-300))
    return db, normalized


def _spectral_peaks(f, y, settings, method, channels):
    rows = []
    finite = np.flatnonzero(np.isfinite(y))
    for run in np.split(finite, np.flatnonzero(np.diff(finite) > 1) + 1):
        if run.size < 3:
            continue
        # Only the display indicator is smoothed; complex fitting data is untouched.
        smooth = signal.convolve(y[run], np.ones(3) / 3, mode="same")
        smooth[[0, -1]] = y[run[[0, -1]]]
        indices, properties = signal.find_peaks(smooth, prominence=settings.prominence_db)
        for index, prominence in zip(indices, properties["prominences"]):
            rows.append({"frequency_hz": float(f[run[index]]), "methods": [method],
                         "families": ["spectral"], "score": float(prominence), "channels": list(channels),
                         "stable_orders": 0, "damping_ratio": None, "bands": []})
    return rows


def _segmented_poles(f, values, settings, cancel, progress):
    df = float(np.median(np.diff(f)))
    span = f[-1] - f[0]
    width = settings.band_width_hz or max(span / 4, df * (3 * settings.maximum_order + 4))
    if span / (width * (1 - settings.band_overlap)) > 128:
        raise ValueError("分频段数量超过 128，请增大分频段宽度")
    starts = np.arange(f[0], f[-1], width * (1 - settings.band_overlap))
    rows, diagnostics = [], []
    for band_index, lo in enumerate(starts):
        _cancelled(cancel)
        hi = min(float(f[-1]), float(lo + width))
        use = (f >= lo) & (f <= hi)
        band_f, band_h = f[use], values[use]
        usable = np.sum(np.isfinite(band_h), axis=0) >= max(24, settings.maximum_order * 3)
        band_h = band_h[:, usable]
        if not band_h.size:
            continue
        complete = np.all(np.isfinite(band_h), axis=1)
        # Do not join disconnected valid bands into a single fit.
        runs = np.split(np.flatnonzero(complete), np.flatnonzero(np.diff(np.flatnonzero(complete)) > 1) + 1)
        run = max(runs, key=len)
        if run.size < max(24, settings.maximum_order * 3):
            continue
        band_f, band_h = band_f[run], band_h[run]
        previous = []
        for order in range(2, settings.maximum_order + 1, 2):
            _cancelled(cancel)
            poles = common_denominator_poles(band_f, band_h, order)
            current, matched = [], set()
            for pole in sorted(poles, key=abs):
                frequency = float(abs(pole) / (2 * np.pi))
                damping = float(-pole.real / abs(pole))
                if not band_f[0] < frequency < band_f[-1] or not 1e-5 < damping < .3:
                    continue
                count = 1
                for j, old in sorted(enumerate(previous), key=lambda pair: abs(pair[1][0] - frequency)):
                    if (j not in matched and abs(frequency / old[0] - 1) <= settings.frequency_tolerance
                            and abs(damping / old[1] - 1) <= settings.damping_tolerance):
                        count = old[2] + 1
                        matched.add(j)
                        break
                current.append((frequency, damping, count))
                diagnostics.append({"frequency_hz": frequency, "damping_ratio": damping,
                                    "order": order, "stable_orders": count, "band": [float(lo), hi]})
                if count >= settings.stable_orders:
                    rows.append({"frequency_hz": frequency, "methods": ["分频段极点"], "families": ["pole"],
                                 "score": float(count), "channels": np.flatnonzero(usable).tolist(),
                                 "stable_orders": count, "damping_ratio": damping, "bands": [[float(lo), hi]]})
            previous = current
        if progress:
            progress(10 + int(85 * (band_index + 1) / max(len(starts), 1)), f"候选频段 {lo:.4g}–{hi:.4g} Hz")
    return rows, diagnostics


def merge_candidates(rows, settings):
    remaining = sorted(rows, key=lambda row: (-len(row["families"]), -row["score"]))
    groups = []
    while remaining:
        seed = remaining.pop(0)
        tolerance = max(settings.minimum_spacing_hz, settings.relative_spacing * seed["frequency_hz"])
        members = [seed] + [r for r in remaining if abs(r["frequency_hz"] - seed["frequency_hz"]) <= tolerance]
        remaining = [r for r in remaining if r not in members]
        pole = max((r for r in members if "pole" in r["families"]), key=lambda r: r["stable_orders"], default=None)
        representative = pole or seed
        group = {**representative,
                 "methods": sorted({m for r in members for m in r["methods"]}),
                 "families": sorted({m for r in members for m in r["families"]}),
                 "channels": sorted({c for r in members for c in r["channels"]}),
                 "bands": [list(band) for band in sorted({tuple(band) for r in members for band in r["bands"]})]}
        group["confirmed"] = len(group["families"]) >= 2
        groups.append(group)
    if settings.method == "consensus":
        groups = [r for r in groups if r["confirmed"]]
    groups.sort(key=lambda r: (not r["confirmed"], -r["stable_orders"], -r["score"]))
    return sorted(groups[:settings.maximum_candidates], key=lambda r: r["frequency_hz"])


def search_modal_candidates(data, *, cancel=None, progress=None):
    _cancelled(cancel)
    f, h, settings = data["frequency_hz"], data["values"], data["settings"]
    db, normalized = _indicator(h)
    rows, poles = [], []
    if settings.method in {"cmif", "consensus"}:
        rows.extend(_spectral_peaks(f, db, settings, "CMIF", range(h.shape[1])))
    if settings.method in {"prominence", "consensus"}:
        for j in range(h.shape[1]):
            _cancelled(cancel)
            y = np.full(f.shape, np.nan)
            valid = np.isfinite(normalized[:, j]) & (normalized[:, j] > 0)
            y[valid] = 20 * np.log10(normalized[valid, j])
            rows.extend(_spectral_peaks(f, y, settings, "突出度", [j]))
    if settings.method in {"segmented", "consensus"}:
        local, poles = _segmented_poles(f, h, settings, cancel, progress)
        rows.extend(local)
    candidates = merge_candidates(rows, settings)
    warnings = list(data["warnings"])
    if settings.method == "consensus" and not candidates:
        warnings.append("没有谱峰与稳定极点共同支持的候选；未退回单算法结果")
    if np.any(~np.isfinite(h)):
        warnings.append("存在缺失频点/不完整覆盖：未补零，未外推，未跨缺口拟合")
    if progress:
        progress(100, "候选搜索完成")
    _cancelled(cancel)
    return {"fingerprint": data["fingerprint"], "settings": asdict(settings), "frequency_hz": f.copy(),
            "indicator_db": db, "candidates": candidates, "poles": poles, "labels": list(data["labels"]),
            "warnings": warnings, "stale": False}
