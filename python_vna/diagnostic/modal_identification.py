"""SIMO experimental modal identification on calibrated complex FRFs.

The LSCF common denominator uses a band-centered complex bilinear mapping
(rather than assuming a continuous-time FRF is an exact sampled-data model).
LSFD coefficients use the second-order receptance convention: A_dd = 1/m
when the driving-point component is unity. They are not first-order residues.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Callable

import numpy as np
from scipy import linalg, optimize


_IDENTIFICATION_ALGORITHM = "complex-zoom-lscf-v2"


class ModalCancelled(Exception):
    pass


@dataclass(frozen=True)
class ModalSettings:
    minimum_hz: float = 0.0
    maximum_hz: float = 0.0
    maximum_order: int = 24
    frequency_tolerance: float = 0.01
    damping_tolerance: float = 0.10
    mac_tolerance: float = 0.95
    stable_orders: int = 3
    maximum_fit_error: float = 0.15
    maximum_complexity: float = 0.20


@dataclass(frozen=True)
class FRFMeasurement:
    label: str
    file_name: str
    force_key: str
    response_key: str
    point: str
    axis: int
    coordinates: tuple[float, float, float]
    frequency_hz: np.ndarray
    values: np.ndarray
    response_unit: str
    force_unit: str = "N"
    response_factor: float = 1.0
    force_factor: float = 1.0
    coherence: np.ndarray | None = None
    force_axis: int | None = None
    mass_warning: str = ""


@dataclass(frozen=True)
class ModalInput:
    frequency_hz: np.ndarray
    receptance: np.ndarray
    coherence: np.ndarray
    measurements: tuple[FRFMeasurement, ...]
    driving_index: int
    settings: ModalSettings
    fingerprint: str
    warnings: tuple[str, ...]


def _response_scale(unit: str) -> tuple[int, float]:
    text = unit.lower().replace(" ", "").replace("²", "^2").replace("**", "^")
    if text.endswith("/n"):
        text = text[:-2]
    scales = {
        "g": (2, 9.80665), "m/s^2": (2, 1.0), "m/s2": (2, 1.0),
        "(m/s^2)": (2, 1.0), "mm/s^2": (2, .001),
        "m/s": (1, 1.0), "mm/s": (1, .001), "um/s": (1, 1e-6),
        "m": (0, 1.0), "mm": (0, .001), "um": (0, 1e-6),
    }
    if text not in scales:
        raise ValueError(f"不支持或未明确的响应单位：{unit!r}；请选择加速度、速度或位移单位")
    return scales[text]


def prepare_modal_input(
    measurements: list[FRFMeasurement], driving_index: int, settings: ModalSettings,
) -> ModalInput:
    if not measurements or not 0 <= driving_index < len(measurements):
        raise ValueError("请选择有效的同点同向驱动点响应")
    reference_axes = {m.force_axis for m in measurements}
    if any(axis is not None and axis not in (0, 1, 2) for axis in reference_axes):
        raise ValueError("敲击方向必须为 X/Y/Z")
    if len(reference_axes) > 1:
        raise ValueError("当前单输入识别不能混合不同敲击方向；请按 X/Y/Z 力参考分组")
    reference_axis = measurements[driving_index].force_axis
    if reference_axis is not None and measurements[driving_index].axis != reference_axis:
        raise ValueError("驱动点响应必须与当前力参考同点同向")
    if settings.maximum_order < 6 or settings.maximum_order > 80 or settings.maximum_order % 2:
        raise ValueError("最大模型阶次必须为 6~80 之间的偶数")
    if (not 0 < settings.frequency_tolerance < 1 or not 0 < settings.damping_tolerance < 1
            or not 0 < settings.mac_tolerance <= 1 or settings.stable_orders < 3
            or not 0 < settings.maximum_fit_error < 1 or not 0 < settings.maximum_complexity < 1):
        raise ValueError("稳定性或质量校核参数无效")
    if not np.isfinite([settings.minimum_hz, settings.maximum_hz]).all() or settings.minimum_hz < 0 or settings.maximum_hz < 0:
        raise ValueError("分析频率必须为有限非负数，0 表示自动")
    if settings.maximum_hz and settings.minimum_hz >= settings.maximum_hz:
        raise ValueError("分析频率下限必须小于上限")
    converted, axes, coherences, frozen = [], [], [], []
    identities = set()
    digest = hashlib.sha256()
    digest.update(_IDENTIFICATION_ALGORITHM.encode())
    digest.update(json.dumps(asdict(settings), sort_keys=True).encode())
    digest.update(str(driving_index).encode())
    for measurement in measurements:
        m = measurement
        if m.axis not in (0, 1, 2):
            raise ValueError(f"{m.label}：响应方向无效")
        identity = (m.point.upper(), m.axis)
        if identity in identities:
            raise ValueError(f"测点 {m.point} 的 {'XYZ'[m.axis]} 方向重复；请检查文件敲击方向分组，同一力参考只保留一条物理响应")
        identities.add(identity)
        if m.axis not in (0, 1, 2) or len(m.coordinates) != 3 or not np.isfinite(m.coordinates).all():
            raise ValueError(f"{m.label}：测点坐标或方向无效")
        if not m.force_key or not m.response_key:
            raise ValueError(f"{m.label}：缺少明确的力输入或响应通道")
        f = np.asarray(m.frequency_hz, dtype=float).ravel()
        raw = np.asarray(m.values)
        if not np.iscomplexobj(raw):
            raise ValueError(f"{m.label}：需要包含相位的复数 FRF，不能使用幅值或自谱")
        h = np.asarray(raw, dtype=complex).ravel()
        if f.size != h.size:
            raise ValueError(f"{m.label}：FRF 与频率点数不一致")
        coh = np.asarray(m.coherence, dtype=float).ravel() if m.coherence is not None else np.full(f.size, np.nan)
        if coh.size != f.size:
            raise ValueError(f"{m.label}：相干性与频率点数不一致")
        valid_frequency = np.isfinite(f) & (f > 0)
        if settings.minimum_hz:
            valid_frequency &= f >= settings.minimum_hz
        if settings.maximum_hz:
            valid_frequency &= f <= settings.maximum_hz
        if np.any(valid_frequency & ~np.isfinite(h)):
            raise ValueError(f"{m.label}：FRF 有内部缺失/非法值，请先选择完整频段的数据")
        valid = valid_frequency & np.isfinite(h)
        f, h, coh = f[valid], h[valid], coh[valid]
        if f.size == 0:
            raise ValueError(f"{m.label}：所选 FRF 与分析范围没有共同有效频段")
        if np.any(np.isfinite(coh) & ((coh < 0) | (coh > 1))):
            raise ValueError(f"{m.label}：相干性必须位于 0~1")
        if f.size < 32 or np.any(np.diff(f) <= 0):
            raise ValueError(f"{m.label}：至少需要 32 个严格递增的有效正频率点")
        if np.linalg.norm(h) == 0 or np.linalg.norm(h.imag) <= np.linalg.norm(h) * 1e-12:
            raise ValueError(f"{m.label}：FRF 全零或缺少可用相位")
        raw_h = h.copy()
        power, scale = _response_scale(m.response_unit)
        force_scale = {"N": 1.0, "kN": 1000.0, "mN": .001}.get(m.force_unit)
        if force_scale is None:
            raise ValueError(f"{m.label}：力单位必须明确为 N、kN 或 mN")
        if not np.isfinite([m.response_factor, m.force_factor]).all() or m.force_factor <= 0 or m.response_factor == 0:
            raise ValueError(f"{m.label}：响应系数必须非零，力系数必须大于零")
        h = h * scale * m.response_factor / (force_scale * m.force_factor) / (2j * np.pi * f) ** power
        if not np.isfinite(h).all():
            raise ValueError(f"{m.label}：单位或工程系数换算后出现非法数值")
        for array in (f, h, raw_h, coh):
            array.setflags(write=False)
        copied = FRFMeasurement(**{**m.__dict__, "frequency_hz": f, "values": raw_h, "coherence": coh})
        frozen.append(copied)
        converted.append(h)
        axes.append(f)
        coherences.append(coh)
        digest.update(repr((m.label, m.file_name, m.force_key, m.response_key, m.point, m.axis, m.coordinates,
                            m.response_unit, m.force_unit, m.response_factor, m.force_factor)).encode())
        if m.mass_warning:
            digest.update(m.mass_warning.encode())
        if m.force_axis is not None:
            digest.update(f"force_axis={m.force_axis}".encode())
        for array in (f, h, coh):
            digest.update(array.tobytes())
    low = max([f[0] for f in axes] + [settings.minimum_hz])
    high = min([f[-1] for f in axes] + ([settings.maximum_hz] if settings.maximum_hz else []))
    if high <= low:
        raise ValueError("所选 FRF 与分析范围没有共同有效频段")
    # Use the coarsest native grid; interpolating finer would invent resolution.
    base = max(axes, key=lambda f: float(np.median(np.diff(f))))
    grid = base[(base >= low) & (base <= high)].copy()
    if grid.size < max(32, settings.maximum_order * 3):
        raise ValueError("共同频段有效点不足；请扩大频段或降低最大模型阶次")
    matrix = np.column_stack([np.interp(grid, f, h.real) + 1j * np.interp(grid, f, h.imag)
                              for f, h in zip(axes, converted)])
    quality = np.column_stack([np.interp(grid, f, c) for f, c in zip(axes, coherences)])
    warnings = []
    if any(not np.array_equal(f, base) for f in axes):
        warnings.append("各文件频率网格不同，已在共同频段按最粗原始网格插值；不补齐带外数据")
    if np.any(~np.isfinite(quality)):
        warnings.append("部分或全部相干性未提供")
    affected = sum(bool(m.mass_warning) for m in frozen)
    if affected:
        warnings.append(f"{affected} 条 FRF 存在锤击加窗引起的绝对幅值风险；驱动点若受影响，不输出未经校正的模态质量")
    for array in (grid, matrix, quality):
        array.setflags(write=False)
    return ModalInput(grid, matrix, quality, tuple(frozen), driving_index, settings, digest.hexdigest(), tuple(warnings))


def modal_mac(left: np.ndarray, right: np.ndarray) -> float:
    norm = float(np.vdot(left, left).real * np.vdot(right, right).real)
    return float(abs(np.vdot(left, right)) ** 2 / norm) if norm > 0 else 0.0


def _check_cancel(cancel: Callable[[], bool] | None) -> None:
    if cancel is not None and cancel():
        raise ModalCancelled("已取消模态识别")


def common_denominator_poles(f: np.ndarray, h: np.ndarray, order: int) -> np.ndarray:
    """Complex SIMO LSCF with frequency zoom and QR-eliminated numerators.

    Center the selected band and spread it around the unit circle. The former
    real-coefficient quarter-circle basis became nearly rank-deficient when
    zooming a high-frequency band. Complex coefficients allow the frequency
    shift; the inverse bilinear map returns continuous-time poles without
    logarithmic branch ambiguity or a sampled-data approximation.
    """
    omega_center = np.pi * float(f[0] + f[-1])
    # Band edges map to +/-2*atan(5), leaving a gap around the inverse-map singularity.
    omega_scale = 2 * np.pi * float(f[-1] - f[0]) * .1
    s_relative = 2j * np.pi * f - 1j * omega_center
    z = (omega_scale + s_relative) / (omega_scale - s_relative)
    basis = z[:, None] ** np.arange(order + 1)
    q, _ = linalg.qr(basis, mode="economic")
    blocks, rhs = [], []
    for channel in h.T:
        scale = max(float(np.linalg.norm(channel)), np.finfo(float).tiny)
        design = channel[:, None] * basis / scale
        projected = design - q @ (q.conj().T @ design)
        blocks.append(projected[:, :-1])
        rhs.append(-projected[:, -1])
    coefficients = linalg.lstsq(np.vstack(blocks), np.concatenate(rhs), cond=1e-11)[0]
    roots = np.polynomial.polynomial.polyroots(np.r_[coefficients, 1.0])
    roots = roots[abs(roots + 1) > 1e-10]
    poles = omega_scale * (roots - 1) / (roots + 1) + 1j * omega_center
    return poles[(poles.real < 0) & (poles.imag > 0)]


def lsfd(f: np.ndarray, receptance: np.ndarray, frequencies: np.ndarray, damping: np.ndarray):
    """Complex second-order coefficients with constant and 1/s^2 residuals.

    Weight by omega^2 to avoid overemphasizing low-frequency receptance after
    acceleration conversion. Channel scaling balances outputs in refinement.
    """
    w = 2 * np.pi * f
    wn = 2 * np.pi * frequencies
    modal_basis = 1 / (wn[None, :] ** 2 - w[:, None] ** 2 + 2j * w[:, None] * (wn * damping)[None, :])
    basis = np.column_stack((modal_basis, np.ones(f.size), -1 / w ** 2))
    weights = w ** 2
    design = basis * weights[:, None]
    scales = np.linalg.norm(design, axis=0)
    scales[scales == 0] = 1
    coefficients = linalg.lstsq(design / scales, receptance * weights[:, None], cond=1e-11)[0] / scales[:, None]
    return coefficients, basis @ coefficients


def fit_local_preview(data: ModalInput, target_hz: float, *, cancel=None, progress=None) -> dict:
    """Explicit SDOF preview, with rejected coefficients left missing, never FRF fallback."""
    f = data.frequency_hz
    if not np.isfinite(target_hz) or not f[0] < target_hz < f[-1]:
        raise ValueError("局部预览频率必须位于有效 FRF 频段内部")
    df = float(np.median(np.diff(f)))
    use = abs(f - target_hz) <= max(target_hz * .06, 10 * df)
    if np.count_nonzero(use) < 12:
        raise ValueError("局部拟合频段不足 12 个有效频率点；未使用原始 FRF 回退")
    local_f, measured = f[use], data.receptance[use]
    scale = np.maximum(np.linalg.norm(measured * (2 * np.pi * local_f[:, None]) ** 2, axis=0), 1e-300)
    width = max(target_hz * .02, 2 * df)

    def residual(params):
        _check_cancel(cancel)
        _, estimate = lsfd(local_f, measured, params[:1], params[1:])
        error = (estimate - measured) * (2 * np.pi * local_f[:, None]) ** 2 / scale
        return np.r_[error.real.ravel(), error.imag.ravel()]

    if progress:
        progress(10, "局部单模态拟合，仅供预览")
    optimum = optimize.least_squares(residual, [target_hz, .02],
        bounds=([max(local_f[0], target_hz - width), 1e-5], [min(local_f[-1], target_hz + width), .3]),
        max_nfev=80, x_scale="jac")
    _check_cancel(cancel)
    coefficients, estimate = lsfd(local_f, measured, optimum.x[:1], optimum.x[1:])
    errors = np.linalg.norm((estimate - measured) * (2 * np.pi * local_f[:, None]) ** 2, axis=0) / scale
    accepted = np.isfinite(coefficients[0]) & (errors <= data.settings.maximum_fit_error) & bool(optimum.success)
    values = coefficients[0].copy()
    values[~accepted] = complex(np.nan, np.nan)
    warnings = [f"{m.label}：局部拟合误差 {100 * error:.3g}%，未纳入预览"
                for m, error, good in zip(data.measurements, errors, accepted) if not good]
    if not optimum.success:
        warnings.append("局部拟合未收敛")
    if not accepted.any():
        raise ValueError("局部拟合没有通过误差校核的通道；未使用原始 FRF 回退，请改用多模态识别")
    if progress:
        progress(100, "局部预览拟合完成")
    return {"fingerprint": data.fingerprint, "frequency_hz": float(optimum.x[0]),
            "damping_ratio": float(optimum.x[1]), "coefficients": values, "accepted": accepted,
            "errors": errors, "warnings": warnings, "preview_only": True,
            "normalization": "display_only", "mass_kg": None}


def _pole_rows(data: ModalInput, cancel=None, progress=None) -> list[dict]:
    rows, previous = [], []
    settings = data.settings
    # Identification on accelerance prevents 1/omega^2 conditioning penalties.
    accelerance = data.receptance * -(2 * np.pi * data.frequency_hz[:, None]) ** 2
    omega = 2 * np.pi * data.frequency_hz
    response_norm = max(float(np.linalg.norm(accelerance)), np.finfo(float).tiny)
    orders = list(range(2, settings.maximum_order + 1, 2))
    for step, order in enumerate(orders):
        _check_cancel(cancel)
        poles = common_denominator_poles(data.frequency_hz, accelerance, order)
        current = []
        for pole in poles:
            frequency = float(abs(pole) / (2 * np.pi))
            damping = float(-pole.real / abs(pole))
            if not data.frequency_hz[0] < frequency < data.frequency_hz[-1] or not 1e-5 < damping < .5:
                continue
            current.append({"frequency_hz": frequency, "damping_ratio": damping, "order": order,
                            "stable_count": 1, "stable": False, "frequency_change": None,
                            "damping_change": None, "shape_mac": None,
                            "stability_reason": "前一阶次无可比较极点"})
        current.sort(key=lambda row: row["frequency_hz"])
        if current:
            coeff, _ = lsfd(data.frequency_hz, data.receptance, np.array([p["frequency_hz"] for p in current]),
                            np.array([p["damping_ratio"] for p in current]))
            used = set()
            for i, row in enumerate(current):
                shape = coeff[i].copy()
                row["vector"] = shape
                wn = 2 * np.pi * row["frequency_hz"]
                basis = -omega ** 2 / (wn ** 2 - omega ** 2 + 2j * row["damping_ratio"] * wn * omega)
                contribution = float(np.linalg.norm(basis[:, None] * shape[None, :]) / response_norm)
                row["relative_contribution"] = contribution
                if contribution <= 1e-8:
                    row["stability_reason"] = "留数近零，极点/零点抵消，不作为可观测模态"
                    continue
                matches = sorted(enumerate(previous), key=lambda pair: abs(pair[1]["frequency_hz"] - row["frequency_hz"]))
                for j, old in matches:
                    if old.get("relative_contribution", 1.) <= 1e-8:
                        continue
                    df = abs(row["frequency_hz"] / old["frequency_hz"] - 1)
                    dz = abs(row["damping_ratio"] / old["damping_ratio"] - 1)
                    mac = modal_mac(shape, old["vector"])
                    if row["frequency_change"] is None:
                        row.update(frequency_change=df, damping_change=dz, shape_mac=mac)
                        reasons = []
                        if df > settings.frequency_tolerance:
                            reasons.append(f"频率差 {100 * df:.3g}% > {100 * settings.frequency_tolerance:g}%")
                        if dz > settings.damping_tolerance:
                            reasons.append(f"阻尼差 {100 * dz:.3g}% > {100 * settings.damping_tolerance:g}%")
                        if mac < settings.mac_tolerance:
                            reasons.append(f"MAC {mac:.4g} < {settings.mac_tolerance:g}")
                        row["stability_reason"] = "；".join(reasons) or "相邻阶次极点已被匹配"
                    if j in used:
                        continue
                    if (df <= settings.frequency_tolerance and dz <= settings.damping_tolerance
                            and mac >= settings.mac_tolerance):
                        row["stable_count"] = old["stable_count"] + 1
                        row["stable"] = row["stable_count"] >= settings.stable_orders
                        row.update(frequency_change=df, damping_change=dz, shape_mac=mac,
                                   stability_reason="连续稳定" if row["stable"] else
                                   f"连续稳定 {row['stable_count']}/{settings.stable_orders} 阶，尚不足")
                        used.add(j)
                        break
            rows.extend(current)
        previous = current
        if progress:
            progress(int(65 * (step + 1) / len(orders)), f"模型阶次 {order}")
    return rows


def _identification_diagnostics(data: ModalInput, rows: list[dict]) -> dict:
    settings, f = data.settings, data.frequency_hz
    spacing = float(np.median(np.diff(f)))
    comparable = [p for p in rows if p["frequency_change"] is not None]
    frequency_ok = [p for p in comparable if p["frequency_change"] <= settings.frequency_tolerance]
    damping_ok = [p for p in frequency_ok if p["damping_change"] <= settings.damping_tolerance]
    shape_ok = [p for p in damping_ok if p["shape_mac"] >= settings.mac_tolerance]
    stable = [p for p in rows if p["stable"]]
    lines = ["识别算法：频带中心化复数 LSCF；频率/阻尼/MAC 和连续阶次判据未放宽",
             f"实际频段 {f[0]:.6g}~{f[-1]:.6g} Hz；{len(f)} 个频点；频率间隔中位数 {spacing:.6g} Hz；{len(data.measurements)} 条复数 FRF",
             f"模型阶次 2~{settings.maximum_order}（步长 2）；频率差 ≤{100 * settings.frequency_tolerance:g}%，阻尼相对差 ≤{100 * settings.damping_tolerance:g}%，MAC ≥{settings.mac_tolerance:g}；要求连续 {settings.stable_orders} 阶",
             f"带内有效极点 {len(rows)} 个；可比较 {len(comparable)}；依次通过频率/阻尼/MAC：{len(frequency_ok)}/{len(damping_ok)}/{len(shape_ok)}；连续稳定 {len(stable)} 个"]
    if not stable:
        if not rows:
            lines.append("所有阶次均未保留带内、负实部且阻尼在 0.001%~50% 的极点；请检查复数 FRF 相位/符号、实际覆盖及所选力参考，不只是调整频段。")
        elif not frequency_ok:
            lines.append("相邻模型阶次频率不稳定；建议在 CMIF 峰两侧选取包含完整共振带宽的较小频段，并检查输入参考与 FRF 质量。")
        elif not damping_ok:
            lines.append("频率接近，但阻尼在相邻阶次间不稳定；检查频率分辨率、泄漏、相干性及频段边界，不能仅凭幅值峰输出模态质量。")
        elif not shape_ok:
            lines.append("频率和阻尼接近，但振型 MAC 不足；检查混合敲击方向、测点映射、极性和噪声通道。")
        else:
            longest = max(p["stable_count"] for p in rows)
            lines.append(f"已有相邻阶次匹配，但最长仅连续 {longest}/{settings.stable_orders} 阶；可在数据分辨率允许时提高最大阶次，或缩小至完整共振频段。")
        lines.append("可在稳定图查看灰色极点并手动选择一个极点拟合；未连续稳定时只供检查，质量仍标记不可可靠估计。")
    narrow = [p for p in rows if 2 * p["damping_ratio"] * p["frequency_hz"] < 2 * spacing]
    if narrow:
        lines.append("部分候选的半功率带宽不足两个频率间隔，阻尼与质量可能难以分辨；应增加采样记录长度，而不是只插值加密。")
    return {"frequency_min_hz": float(f[0]), "frequency_max_hz": float(f[-1]),
            "spacing_hz": spacing, "point_count": len(f), "pole_count": len(rows),
            "stable_pole_count": len(stable), "lines": lines}


def fit_selected_poles(data: ModalInput, poles: list[dict], *, cancel=None, progress=None) -> dict:
    if not poles:
        raise ValueError("没有选中的稳定极点；请检查频段、最大阶次或手动选择极点")
    poles = sorted(poles, key=lambda p: p["frequency_hz"])
    f0 = np.array([p["frequency_hz"] for p in poles])
    d0 = np.array([p["damping_ratio"] for p in poles])
    f = data.frequency_hz
    widths = np.maximum(f0 * .02, np.median(np.diff(f)) * 2)
    lower = np.r_[np.maximum(f[0] * 1.000001, f0 - widths), np.full(len(poles), 1e-5)]
    upper = np.r_[np.minimum(f[-1] * .999999, f0 + widths), np.full(len(poles), .5)]
    scale = np.linalg.norm(data.receptance * (2 * np.pi * f[:, None]) ** 2, axis=0)
    scale = np.maximum(scale, np.finfo(float).tiny)

    def residual(params):
        _check_cancel(cancel)
        _, estimate = lsfd(f, data.receptance, params[:len(poles)], params[len(poles):])
        error = (estimate - data.receptance) * (2 * np.pi * f[:, None]) ** 2 / scale
        return np.r_[error.real.ravel(), error.imag.ravel()]

    _check_cancel(cancel)
    if progress:
        progress(70, "联合拟合模态留数与带外残余项")
    optimum = optimize.least_squares(residual, np.r_[f0, d0], bounds=(lower, upper),
                                     x_scale="jac", max_nfev=80, ftol=1e-10, xtol=1e-10, gtol=1e-10)
    _check_cancel(cancel)
    frequencies, damping = optimum.x[:len(poles)], optimum.x[len(poles):]
    coefficients, estimate = lsfd(f, data.receptance, frequencies, damping)
    error = np.linalg.norm((estimate - data.receptance) * (2 * np.pi * f[:, None]) ** 2, axis=0) / scale
    modes = []
    for i, (frequency, zeta) in enumerate(zip(frequencies, damping)):
        values = coefficients[i].copy()
        driver = values[data.driving_index]
        reasons = []
        if poles[i].get("stable") is False:
            reasons.append("所选极点尚未连续稳定")
        if not optimum.success:
            reasons.append("非线性精修未收敛")
        if data.measurements[data.driving_index].mass_warning:
            reasons.append(data.measurements[data.driving_index].mass_warning)
        if driver.real <= 0:
            reasons.append("驱动点系数非正，检查力/响应极性")
        if abs(driver) <= max(np.max(abs(values)) * 1e-3, np.finfo(float).tiny):
            reasons.append("驱动点接近该阶节点")
        complexity = float(np.linalg.norm(values.imag) / max(np.linalg.norm(values), np.finfo(float).tiny))
        if complexity > data.settings.maximum_complexity:
            reasons.append("复模态特征显著")
        if abs(driver.imag) / max(abs(driver), np.finfo(float).tiny) > data.settings.maximum_complexity:
            reasons.append("驱动点系数存在显著虚部")
        local = abs(f - frequency) <= max(frequency * zeta * 3, np.median(np.diff(f)) * 2)
        driver_measured = data.receptance[local, data.driving_index]
        driver_estimated = estimate[local, data.driving_index]
        local_error = float(np.linalg.norm(driver_estimated - driver_measured) / max(np.linalg.norm(driver_measured), np.finfo(float).tiny))
        if error[data.driving_index] > data.settings.maximum_fit_error or local_error > data.settings.maximum_fit_error:
            reasons.append("驱动点拟合误差过大")
        if frequency - 3 * frequency * zeta <= f[0] or frequency + 3 * frequency * zeta >= f[-1]:
            reasons.append("模态靠近频段边缘，带宽不完整")
        coh = data.coherence[local, data.driving_index]
        coh_mean = float(np.nanmean(coh)) if np.any(np.isfinite(coh)) else None
        if coh_mean is not None and coh_mean < .8:
            reasons.append("驱动点相干性低于 0.8")
        # Check each mode's resolvable contribution at the driving point.
        contribution = abs(driver) / max(2 * zeta * (2 * np.pi * frequency) ** 2, 1e-300)
        if contribution < .02 * max(float(np.max(abs(driver_measured))), np.finfo(float).tiny):
            reasons.append("驱动点该阶贡献过弱")
        mass = float(1 / driver.real) if not reasons else None
        shape = values / driver if abs(driver) > np.finfo(float).tiny else values / max(np.max(abs(values)), 1e-300)
        modes.append({"frequency_hz": float(frequency), "damping_ratio": float(zeta),
                      "coefficients": values, "shape": shape, "mass_kg": mass,
                      "stiffness_n_m": mass * (2 * np.pi * frequency) ** 2 if mass is not None else None,
                      "damping_n_s_m": 2 * mass * zeta * 2 * np.pi * frequency if mass is not None else None,
                      "fit_error": float(error[data.driving_index]), "local_error": local_error,
                      "complexity": complexity, "coherence": coh_mean,
                      "status": "；".join(reasons) if reasons else "有效",
                      "normalization": "driving_point_unity", "source_order": poles[i]["order"]})
    automac = np.array([[modal_mac(a["shape"], b["shape"]) for b in modes] for a in modes])
    for i, mode in enumerate(modes):
        if any(automac[i, j] > .98 and abs(mode["frequency_hz"] / modes[j]["frequency_hz"] - 1) < .01
               for j in range(len(modes)) if j != i):
            reason = "相邻结果高度相关，检查重复极点"
            status = reason if mode["status"] == "有效" else mode["status"] + "；" + reason
            mode.update(mass_kg=None, stiffness_n_m=None, damping_n_s_m=None, status=status)
    if progress:
        progress(100, "模态拟合与校核完成")
    return {"fingerprint": data.fingerprint, "algorithm": _IDENTIFICATION_ALGORITHM,
            "settings": asdict(data.settings), "frequency_hz": f.copy(),
            "measured": data.receptance.copy(), "reconstructed": estimate, "channel_errors": error,
            "residual_coefficients": coefficients[-2:].copy(), "modes": modes, "automac": automac,
            "driving_index": data.driving_index, "warnings": list(data.warnings),
            "measurements": [{k: v for k, v in vars(m).items() if k not in {"frequency_hz", "values", "coherence"}}
                             for m in data.measurements]}


def identify_modes(data: ModalInput, *, cancel=None, progress=None) -> dict:
    rows = _pole_rows(data, cancel, progress)
    selected = []
    for row in sorted((p for p in rows if p["stable"]), key=lambda p: (-p["stable_count"], -p["order"])):
        if not any(abs(row["frequency_hz"] / old["frequency_hz"] - 1) <= data.settings.frequency_tolerance
                   and modal_mac(row["vector"], old["vector"]) >= data.settings.mac_tolerance for old in selected):
            selected.append(row)
    if selected:
        result = fit_selected_poles(data, selected, cancel=cancel, progress=progress)
    else:
        result = {"fingerprint": data.fingerprint, "algorithm": _IDENTIFICATION_ALGORITHM,
                  "settings": asdict(data.settings), "frequency_hz": data.frequency_hz.copy(),
                  "measured": data.receptance.copy(), "reconstructed": None, "channel_errors": None,
                  "residual_coefficients": None, "modes": [], "automac": np.empty((0, 0)),
                  "driving_index": data.driving_index, "warnings": list(data.warnings) + ["未达到连续稳定极点判据；请查看诊断详情和稳定图，未据此输出模态质量"],
                  "measurements": [{k: v for k, v in vars(m).items() if k not in {"frequency_hz", "values", "coherence"}} for m in data.measurements]}
    result["poles"] = rows
    result["diagnostics"] = _identification_diagnostics(data, rows)
    return result
