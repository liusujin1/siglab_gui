from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np
from scipy.io import loadmat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from python_vna.daq.base import BackendFrame
from python_vna.signal_pipeline import compute_autospectrum, compute_fft


@dataclass
class ChannelStats:
    index: int
    label: str
    td_rms: float
    td_ac_rms: float
    td_peak: float
    stored_aspec_sum: float
    stored_aspec_sum_no_dc: float
    stored_aspec_peak: float
    stored_aspec_peak_hz: float
    stored_aspec_to_td_rms2: float
    stored_aspec_no_dc_to_td_ac_rms2: float
    power_corrected_aspec_to_td_rms2: float
    amplitude_recomputed_aspec_sum: float
    stored_to_amplitude_recomputed_sum_ratio: float
    recomputed_aspec_sum: float
    stored_to_recomputed_sum_ratio: float
    recomputed_aspec_peak_hz: float
    fft_power_sum: float
    stored_to_saved_fft_power_sum_ratio: float
    saved_fft_power_peak_hz: float
    stored_top_peaks: tuple[tuple[float, float], ...]
    saved_fft_top_peaks: tuple[tuple[float, float], ...]


@dataclass
class FileStats:
    path: Path
    sample_rate: float
    rbw_hz: float
    freq_points: int
    freq_max_hz: float
    average_count: int
    window_index: int
    persisted_wincor: float
    runtime_wincor: float
    top_display: tuple[int, int, int]
    bottom_display: tuple[int, int, int]
    channels: list[ChannelStats]
    frf: dict[str, np.ndarray]
    coherence: dict[str, np.ndarray]


WINDOW_POWER_CORRECTION = {
    1: 1.0,
    2: 0.6666666667,
    3: 0.2617872274,
    4: 0.2920416990,
    5: 0.3378558539,
    6: 0.5642952975,
    7: 0.4947506823,
    8: 0.7311777141,
    9: 0.5791201619,
    10: 0.5904311459,
    11: 0.6208287665,
    12: 0.5852957066,
    13: 0.5583575867,
    14: 0.4989141365,
}


def _scalar(value, default: float = np.nan) -> float:
    try:
        squeezed = np.squeeze(value)
        if getattr(squeezed, "size", 0) == 0:
            return default
        if np.isscalar(squeezed):
            return float(squeezed)
        return float(np.ravel(squeezed)[0])
    except Exception:
        return default


def _int_scalar(value, default: int = 0) -> int:
    try:
        return int(round(_scalar(value, float(default))))
    except Exception:
        return default


def _array(value, dtype=None) -> np.ndarray:
    arr = np.asarray(np.squeeze(value))
    if dtype is not None:
        arr = arr.astype(dtype)
    return arr


def _safe_ratio(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0.0:
        return np.nan
    return float(numerator / denominator)


def _top_peaks(
    freqs: np.ndarray,
    values: np.ndarray,
    count: int = 3,
    min_hz: float = 1.0,
) -> tuple[tuple[float, float], ...]:
    if freqs.size == 0 or values.size == 0:
        return ()
    point_count = min(freqs.size, values.size)
    x = np.asarray(freqs[:point_count], dtype=float)
    y = np.asarray(values[:point_count], dtype=float).copy()
    finite = np.isfinite(x) & np.isfinite(y)
    finite &= x >= min_hz
    if not np.any(finite):
        return ()
    y[~finite] = -np.inf
    take = min(count, int(np.count_nonzero(finite)))
    indices = np.argpartition(y, -take)[-take:]
    indices = indices[np.argsort(y[indices])[::-1]]
    return tuple((float(x[index]), float(y[index])) for index in indices)


def _legacy_label(entry, fallback: str) -> str:
    try:
        text = str(np.squeeze(getattr(entry, "label")))
    except Exception:
        return fallback
    parts = [part.strip() for part in text.split("~") if part.strip()]
    return parts[0] if parts else fallback


def _panel_display(mat, panel_index: int) -> tuple[int, int, int]:
    try:
        panel = mat["xplot_s1"][0, panel_index]
        return (
            _int_scalar(getattr(panel, "ypu1sel"), 0),
            _int_scalar(getattr(panel, "ypu2sel"), 0),
            _int_scalar(getattr(panel, "yapcor"), 1),
        )
    except Exception:
        return (0, 0, 1)


def _legacy_cross_results(slm) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    frf: dict[str, np.ndarray] = {}
    coherence: dict[str, np.ndarray] = {}
    try:
        xcmeas = getattr(slm, "xcmeas")
    except Exception:
        return frf, coherence
    for ref_index in range(xcmeas.shape[0]):
        for channel_index in range(xcmeas.shape[1]):
            if ref_index == channel_index:
                continue
            try:
                cell = xcmeas[ref_index, channel_index]
                xfer = np.asarray(np.squeeze(getattr(cell, "xfer")))
                coh = np.asarray(np.squeeze(getattr(cell, "coh")))
            except Exception:
                continue
            key = f"ch{ref_index + 1}->ch{channel_index + 1}"
            if xfer.size:
                frf[key] = xfer.astype(complex, copy=False)
            if coh.size:
                coherence[key] = coh.astype(float, copy=False)
    return frf, coherence


def read_stats(path: str | Path, channels: int = 4) -> FileStats:
    file_path = Path(path)
    mat = loadmat(file_path, squeeze_me=False, struct_as_record=False)
    slm = mat["SLm"][0, 0]
    sample_rate = _scalar(mat.get("SampleRate", [[np.nan]]))
    rbw_hz = _scalar(getattr(slm, "rbw", [[np.nan]]))
    average_count = _int_scalar(getattr(slm, "navg", [[0]]), 0)
    window_index = _int_scalar(getattr(slm, "winsel", [[1]]), 1)
    persisted_wincor = _scalar(getattr(slm, "wincor", [[1.0]]), 1.0)
    runtime_wincor = WINDOW_POWER_CORRECTION.get(window_index, 1.0)
    freqs = _array(getattr(slm, "fdxvec", []), dtype=float)
    channel_stats: list[ChannelStats] = []
    channel_count = min(channels, getattr(slm, "scmeas").shape[1])
    for index in range(channel_count):
        entry = slm.scmeas[0, index]
        label = _legacy_label(entry, f"Ch {index + 1}")
        td = _array(getattr(entry, "tdmeas", []), dtype=float)
        aspec = _array(getattr(entry, "aspec", []), dtype=float)
        saved_fft = _array(getattr(entry, "fft", []), dtype=complex)
        frame = BackendFrame(
            sample_rate=sample_rate,
            channel_names=[label],
            data=td.reshape(1, -1),
            timestamps=np.arange(td.size, dtype=float) / max(sample_rate, 1e-20),
            frame_index=0,
            metadata={"processing_window": "hanning" if window_index == 2 else "boxcar"},
        )
        if td.size:
            _fft_freqs, amplitude_recomputed_fft = compute_fft(frame)
            amplitude_recomputed = np.abs(amplitude_recomputed_fft[0]) ** 2
            recomputed = compute_autospectrum(frame)[1][0]
        else:
            amplitude_recomputed = np.array([])
            recomputed = np.array([])
        fft_power = np.abs(saved_fft) ** 2 if saved_fft.size else np.array([])
        td_rms2 = float(np.mean(td * td)) if td.size else np.nan
        td_ac = td - np.mean(td) if td.size else np.array([])
        td_ac_rms2 = float(np.mean(td_ac * td_ac)) if td_ac.size else np.nan
        stored_sum = float(np.nansum(aspec)) if aspec.size else np.nan
        stored_sum_no_dc = float(np.nansum(aspec[1:])) if aspec.size > 1 else np.nan
        amplitude_recomputed_sum = (
            float(np.nansum(amplitude_recomputed)) if amplitude_recomputed.size else np.nan
        )
        recomputed_sum = float(np.nansum(recomputed)) if recomputed.size else np.nan
        fft_power_sum = float(np.nansum(fft_power)) if fft_power.size else np.nan
        stored_peak_index = int(np.nanargmax(aspec)) if aspec.size else 0
        recomputed_peak_index = int(np.nanargmax(recomputed)) if recomputed.size else 0
        saved_fft_peak_index = int(np.nanargmax(fft_power)) if fft_power.size else 0
        channel_stats.append(
            ChannelStats(
                index=index + 1,
                label=label,
                td_rms=float(np.sqrt(td_rms2)) if td.size else np.nan,
                td_ac_rms=float(np.sqrt(td_ac_rms2)) if td.size else np.nan,
                td_peak=float(np.max(np.abs(td))) if td.size else np.nan,
                stored_aspec_sum=stored_sum,
                stored_aspec_sum_no_dc=stored_sum_no_dc,
                stored_aspec_peak=float(np.nanmax(aspec)) if aspec.size else np.nan,
                stored_aspec_peak_hz=float(freqs[stored_peak_index])
                if freqs.size > stored_peak_index
                else np.nan,
                stored_aspec_to_td_rms2=_safe_ratio(stored_sum, td_rms2),
                stored_aspec_no_dc_to_td_ac_rms2=_safe_ratio(stored_sum_no_dc, td_ac_rms2),
                power_corrected_aspec_to_td_rms2=_safe_ratio(
                    stored_sum * runtime_wincor,
                    td_rms2,
                ),
                amplitude_recomputed_aspec_sum=amplitude_recomputed_sum,
                stored_to_amplitude_recomputed_sum_ratio=_safe_ratio(
                    stored_sum,
                    amplitude_recomputed_sum,
                ),
                recomputed_aspec_sum=recomputed_sum,
                stored_to_recomputed_sum_ratio=_safe_ratio(stored_sum, recomputed_sum),
                recomputed_aspec_peak_hz=float(freqs[recomputed_peak_index])
                if freqs.size > recomputed_peak_index
                else np.nan,
                fft_power_sum=fft_power_sum,
                stored_to_saved_fft_power_sum_ratio=_safe_ratio(stored_sum, fft_power_sum),
                saved_fft_power_peak_hz=float(freqs[saved_fft_peak_index])
                if freqs.size > saved_fft_peak_index
                else np.nan,
                stored_top_peaks=_top_peaks(freqs, aspec),
                saved_fft_top_peaks=_top_peaks(freqs, fft_power),
            )
        )
    frf, coherence = _legacy_cross_results(slm)
    return FileStats(
        path=file_path,
        sample_rate=sample_rate,
        rbw_hz=rbw_hz,
        freq_points=int(freqs.size),
        freq_max_hz=float(freqs[-1]) if freqs.size else np.nan,
        average_count=average_count,
        window_index=window_index,
        persisted_wincor=persisted_wincor,
        runtime_wincor=runtime_wincor,
        top_display=_panel_display(mat, 0),
        bottom_display=_panel_display(mat, 1),
        channels=channel_stats,
        frf=frf,
        coherence=coherence,
    )


def _ratio(new: float, old: float) -> str:
    if not np.isfinite(new) or not np.isfinite(old) or old == 0.0:
        return "n/a"
    return f"{new / old:.4g}x"


def _value_pair(new: float, old: float, precision: int = 4) -> str:
    if not np.isfinite(new) or not np.isfinite(old):
        return "n/a"
    return f"{old:.{precision}g}->{new:.{precision}g}"


def _format_peaks(peaks: tuple[tuple[float, float], ...]) -> str:
    if not peaks:
        return "n/a"
    return ", ".join(f"{freq:g}Hz:{value:.3g}" for freq, value in peaks)


def _finite_pair_mask(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    count = min(reference.size, candidate.size)
    if count <= 0:
        return np.array([], dtype=bool)
    ref = reference[:count]
    cand = candidate[:count]
    if np.iscomplexobj(ref) or np.iscomplexobj(cand):
        return (
            np.isfinite(ref.real)
            & np.isfinite(ref.imag)
            & np.isfinite(cand.real)
            & np.isfinite(cand.imag)
        )
    return np.isfinite(ref) & np.isfinite(cand)


def print_stats(reference: FileStats, candidate: FileStats) -> None:
    print(f"Reference: {reference.path}")
    print(f"Candidate: {candidate.path}")
    print()
    print("Config")
    for label, ref_value, cand_value in (
        ("sample_rate", reference.sample_rate, candidate.sample_rate),
        ("rbw_hz", reference.rbw_hz, candidate.rbw_hz),
        ("freq points", reference.freq_points, candidate.freq_points),
        ("freq max Hz", reference.freq_max_hz, candidate.freq_max_hz),
        ("average_count", reference.average_count, candidate.average_count),
        ("window_index", reference.window_index, candidate.window_index),
        ("persisted_wincor", reference.persisted_wincor, candidate.persisted_wincor),
        ("runtime_wincor", reference.runtime_wincor, candidate.runtime_wincor),
        ("top ypu1/ypu2/yapcor", reference.top_display, candidate.top_display),
        ("bottom ypu1/ypu2/yapcor", reference.bottom_display, candidate.bottom_display),
    ):
        print(f"  {label}: {ref_value} -> {cand_value}")
    print()
    print(
        "Ch  Label        td_rms ratio  td_peak ratio  aspec_sum ratio  "
        "aspec_peak ratio  saved/current  saved/rawFFT  stored peak Hz  last-frame peak Hz"
    )
    for ref_ch, cand_ch in zip(reference.channels, candidate.channels):
        stored_peak_note = (
            f"{ref_ch.stored_aspec_peak_hz:g}->{cand_ch.stored_aspec_peak_hz:g}"
        )
        last_peak_note = (
            f"{ref_ch.recomputed_aspec_peak_hz:g}->{cand_ch.recomputed_aspec_peak_hz:g}"
        )
        print(
            f"{ref_ch.index:<3} {cand_ch.label[:10]:<10} "
            f"{_ratio(cand_ch.td_rms, ref_ch.td_rms):>12} "
            f"{_ratio(cand_ch.td_peak, ref_ch.td_peak):>14} "
            f"{_ratio(cand_ch.stored_aspec_sum, ref_ch.stored_aspec_sum):>16} "
            f"{_ratio(cand_ch.stored_aspec_peak, ref_ch.stored_aspec_peak):>17} "
            f"{cand_ch.stored_to_recomputed_sum_ratio:>12.4g} "
            f"{cand_ch.stored_to_amplitude_recomputed_sum_ratio:>12.4g} "
            f"{stored_peak_note:>15} {last_peak_note:>19}"
        )
    print()
    print("Stored Autospectrum Normalization")
    print(
        "Ch  aspec/RMS^2 ref->cand  P*aspec/RMS^2 ref->cand  "
        "aspec/rawFFT(last) ref->cand  aspec/savedFFT ref->cand"
    )
    for ref_ch, cand_ch in zip(reference.channels, candidate.channels):
        print(
            f"{ref_ch.index:<3} "
            f"{_value_pair(cand_ch.stored_aspec_to_td_rms2, ref_ch.stored_aspec_to_td_rms2):>22} "
            f"{_value_pair(cand_ch.power_corrected_aspec_to_td_rms2, ref_ch.power_corrected_aspec_to_td_rms2):>28} "
            f"{_value_pair(cand_ch.stored_to_amplitude_recomputed_sum_ratio, ref_ch.stored_to_amplitude_recomputed_sum_ratio):>31} "
            f"{_value_pair(cand_ch.stored_to_saved_fft_power_sum_ratio, ref_ch.stored_to_saved_fft_power_sum_ratio):>29}"
        )
    print()
    print("Peak Diagnostics")
    for ref_ch, cand_ch in zip(reference.channels, candidate.channels):
        print(f"  Ch {ref_ch.index} reference saved aspec peaks: {_format_peaks(ref_ch.stored_top_peaks)}")
        print(f"  Ch {ref_ch.index} candidate saved aspec peaks: {_format_peaks(cand_ch.stored_top_peaks)}")
        print(f"  Ch {ref_ch.index} reference saved fft^2 peaks: {_format_peaks(ref_ch.saved_fft_top_peaks)}")
        print(f"  Ch {ref_ch.index} candidate saved fft^2 peaks: {_format_peaks(cand_ch.saved_fft_top_peaks)}")
    print()
    print("Notes")
    print("  stored peak Hz comes from the saved averaged aspec.")
    print("  last-frame peak Hz is recomputed from saved tdmeas, usually the final frame.")
    print("  saved/current compares saved averaged aspec energy to current-code aspec from saved tdmeas.")
    print("  saved/rawFFT compares saved averaged aspec energy to amplitude-corrected FFT^2 from saved tdmeas.")
    print("  aspec/savedFFT compares saved aspec with the file's saved fft field; legacy files may save fft from a different instant.")
    print("  P*aspec/RMS^2 applies the runtime window power correction used by the legacy P/RMS readout.")
    print("  cspec and impulse are ignored because legacy files can leave stale values when those displays were not saved.")
    print()
    print("Cross Functions")
    common_keys = sorted(set(reference.frf) & set(candidate.frf))
    if not common_keys:
        print("  No common xfer/coh traces found.")
        return
    print(
        "Trace      high-coh bins  xfer ratio dB median/mean/p10/p90       "
        "coh mean ref->cand  max |coh delta|"
    )
    for key in common_keys:
        ref_xfer = np.ravel(np.asarray(reference.frf[key]))
        cand_xfer = np.ravel(np.asarray(candidate.frf[key]))
        count = min(ref_xfer.size, cand_xfer.size)
        if count <= 0:
            continue
        ref_xfer = ref_xfer[:count]
        cand_xfer = cand_xfer[:count]
        mask = _finite_pair_mask(ref_xfer, cand_xfer)
        mask &= (np.abs(ref_xfer) > 0.0) & (np.abs(cand_xfer) > 0.0)
        ref_coh = np.ravel(np.asarray(reference.coherence.get(key, np.array([]))))
        cand_coh = np.ravel(np.asarray(candidate.coherence.get(key, np.array([]))))
        coh_count = min(ref_coh.size, cand_coh.size, count)
        if coh_count:
            coh_mask = (
                np.isfinite(ref_coh[:coh_count])
                & np.isfinite(cand_coh[:coh_count])
            )
            high_coh_mask = np.zeros(count, dtype=bool)
            high_coh_mask[:coh_count] = (
                coh_mask
                & (ref_coh[:coh_count] >= 0.8)
                & (cand_coh[:coh_count] >= 0.8)
            )
            xfer_mask = mask & high_coh_mask
            ref_coh_valid = ref_coh[:coh_count][coh_mask]
            cand_coh_valid = cand_coh[:coh_count][coh_mask]
        else:
            xfer_mask = mask
            ref_coh_valid = np.array([])
            cand_coh_valid = np.array([])
        if not np.any(xfer_mask):
            xfer_mask = mask
        if np.any(xfer_mask):
            ratio_db = 20.0 * np.log10(np.abs(cand_xfer[xfer_mask]) / np.abs(ref_xfer[xfer_mask]))
            ratio_summary = (
                f"{np.median(ratio_db):+.3f}/"
                f"{np.mean(ratio_db):+.3f}/"
                f"{np.percentile(ratio_db, 10):+.3f}/"
                f"{np.percentile(ratio_db, 90):+.3f}"
            )
        else:
            ratio_summary = "n/a"
        if ref_coh_valid.size and cand_coh_valid.size:
            coh_summary = f"{np.mean(ref_coh_valid):.3f}->{np.mean(cand_coh_valid):.3f}"
            coh_delta = f"{np.max(np.abs(cand_coh_valid - ref_coh_valid)):.3f}"
        else:
            coh_summary = "n/a"
            coh_delta = "n/a"
        print(
            f"{key:<10} {int(np.count_nonzero(xfer_mask)):>13}  "
            f"{ratio_summary:<38} {coh_summary:>17} {coh_delta:>16}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare legacy/Python VNA files by config, time data, and saved spectra."
    )
    parser.add_argument("reference", help="Reference .vna file, usually MATLAB output")
    parser.add_argument("candidate", help="Candidate .vna file, usually Python output")
    parser.add_argument("--channels", type=int, default=4, help="Number of channels to compare")
    args = parser.parse_args()
    reference = read_stats(args.reference, channels=args.channels)
    candidate = read_stats(args.candidate, channels=args.channels)
    print_stats(reference, candidate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
