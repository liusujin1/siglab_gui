from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from python_vna.daq.base import BackendFrame
from python_vna.models import (
    AcquisitionConfig,
    AveragingConfig,
    MeasurementSet,
    ModalProcessingConfig,
)

_SIGNAL_MODULE = None


def _signal():
    global _SIGNAL_MODULE
    if _SIGNAL_MODULE is None:
        from scipy import signal as scipy_signal

        _SIGNAL_MODULE = scipy_signal
    return _SIGNAL_MODULE


LEGACY_MODAL_NOTES = (
    "Legacy VNA modal flow: trigger mode chooses free-run/every/first and auto/manual arm; "
    "processing averages spectra in frequency domain by default; force windows apply to the "
    "reference channel, exponential windows apply to responses; FRF uses averaged G_yx/G_xx "
    "and coherence uses |G_yx|^2/(G_xx*G_yy). Inst mode only updates y(t), aspec, and fft."
)

LEGACY_INST_FUNCTIONS = ("time", "autospectrum", "fft")
LEGACY_AVG_FUNCTIONS = (
    "auto_correlation",
    "cross_correlation",
    "frf",
    "coherence",
    "cross_spectrum",
    "impulse_response",
)


def compute_fft(frame: BackendFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return a one-sided complex RMS spectrum, matching VNA's stored FFT units."""
    window = _processing_window(
        frame.data.shape[1],
        str(frame.metadata.get("processing_window", "boxcar")),
    )
    windowed = frame.data * window[np.newaxis, :]
    correction = 1.0 / max(float(np.mean(window)), 1e-12)
    spectrum = np.fft.rfft(windowed, axis=1) / frame.data.shape[1]
    spectrum *= correction
    if spectrum.shape[1] > 1:
        interior_stop = -1 if frame.data.shape[1] % 2 == 0 else None
        spectrum[:, 1:interior_stop] *= np.sqrt(2.0)
    frequencies = np.fft.rfftfreq(frame.data.shape[1], d=1.0 / frame.sample_rate)
    return frequencies, spectrum


def _processing_window(frame_size: int, window_name: str) -> np.ndarray:
    normalized = window_name.strip().lower()
    if normalized in {"hanning", "hann"}:
        # Legacy SigLab wincalc.m builds Hanning from convolution coefficients,
        # yielding a coherent-gain-normalized window: 1 - cos(2*pi*n/N).
        return 1.0 - np.cos(2.0 * np.pi * np.arange(frame_size, dtype=float) / frame_size)
    return np.ones(frame_size, dtype=float)


def compute_autospectrum(frame: BackendFrame) -> tuple[np.ndarray, np.ndarray]:
    frequencies, spectrum = compute_fft(frame)
    autospectrum = np.abs(spectrum) ** 2
    autospectrum *= _legacy_power_spectrum_scale(
        str(frame.metadata.get("processing_window", "boxcar"))
    )
    return frequencies, autospectrum


def compute_cross_spectrum(reference: np.ndarray, response: np.ndarray) -> np.ndarray:
    return reference.conjugate() * response


def _legacy_power_spectrum_scale(window_name: str) -> float:
    normalized = window_name.strip().lower()
    if normalized in {"hanning", "hann"}:
        # avgdef_h.m defines Hanning PWRCORc as 2/3. Applying the same power
        # normalization here keeps averaged autospectra energy-aligned with
        # legacy VNA files instead of preserving only coherent single-tone peaks.
        return 2.0 / 3.0
    return 1.0


def compute_frf(reference: np.ndarray, response: np.ndarray) -> np.ndarray:
    g_xx = compute_cross_spectrum(reference, reference)
    g_yx = compute_cross_spectrum(reference, response)
    return np.divide(g_yx, g_xx, out=np.zeros_like(g_yx), where=np.abs(g_xx) > 0.0)


def compute_coherence(reference: np.ndarray, response: np.ndarray) -> np.ndarray:
    g_xx = compute_cross_spectrum(reference, reference)
    g_yy = compute_cross_spectrum(response, response)
    g_yx = compute_cross_spectrum(reference, response)
    return compute_coherence_from_spectra(g_xx, g_yy, g_yx)


def compute_coherence_from_spectra(
    reference_power: np.ndarray,
    response_power: np.ndarray,
    cross_spectrum: np.ndarray,
) -> np.ndarray:
    """Magnitude-squared coherence from averaged spectra.

    This mirrors the legacy VNA data relationship: coherence is derived from
    averaged autospectra and cross spectra, not from one raw FFT frame.
    """
    numerator = np.abs(cross_spectrum) ** 2
    denominator = np.abs(reference_power) * np.abs(response_power)
    return np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator, dtype=float),
        where=denominator > 0.0,
    )


def compute_correlations(frame: BackendFrame) -> dict[str, np.ndarray]:
    correlations: dict[str, np.ndarray] = {}
    data = frame.data
    sig = _signal()
    for i, channel_name in enumerate(frame.channel_names):
        correlations[f"{channel_name}:auto"] = sig.correlate(
            data[i], data[i], mode="full"
        )
    if data.shape[0] >= 2:
        reference = data[0]
        for i in range(1, data.shape[0]):
            correlations[f"{frame.channel_names[i]}:cross"] = sig.correlate(
                data[i], reference, mode="full"
            )
    return correlations


def compute_impulse_response(frf: np.ndarray) -> np.ndarray:
    return np.fft.irfft(frf, axis=-1)


def force_window(frame_size: int, fraction: float) -> np.ndarray:
    fraction = float(np.clip(fraction, 0.01, 1.0))
    cutoff = max(1, int(frame_size * fraction))
    window = np.zeros(frame_size, dtype=float)
    window[:cutoff] = 1.0
    return window


def exponential_window(frame_size: int, decay_fraction: float) -> np.ndarray:
    decay_fraction = float(np.clip(decay_fraction, 0.001, 1.0))
    stop_value = max(decay_fraction, 1e-6)
    return np.geomspace(1.0, stop_value, num=frame_size)


@dataclass(slots=True)
class DoubleHitResult:
    rejected: bool
    peak_count: int
    threshold_level: float
    first_peak_index: int | None
    second_peak_index: int | None
    first_peak_value: float
    second_peak_value: float
    min_spacing_samples: int
    min_spacing_seconds: float


def _double_hit_spacing_samples(
    frame_size: int,
    delay_fraction: float,
    sample_rate: float | None,
) -> int:
    legacy_spacing = int(frame_size * np.clip(delay_fraction, 0.0, 1.0))
    if sample_rate is None or sample_rate <= 0.0:
        return max(1, legacy_spacing)

    # Legacy SigLab receives the delay as "% of frame", but applying that
    # literally to long Python frames misses common hammer double hits.  Treat
    # it as a short post-impact ignore window while never exceeding the legacy
    # full-frame interpretation.
    practical_seconds = float(np.clip(delay_fraction, 0.0, 1.0)) * 0.1
    practical_spacing = int(round(sample_rate * practical_seconds))
    return max(1, min(max(1, legacy_spacing), max(1, practical_spacing)))


def analyze_double_hit(
    reference_signal: np.ndarray,
    threshold: float,
    delay_fraction: float = 0.1,
    sample_rate: float | None = None,
) -> DoubleHitResult:
    """Detect a second independent hammer impact on the reference channel."""
    values = np.asarray(reference_signal, dtype=float).ravel()
    if values.size == 0:
        return DoubleHitResult(False, 0, 0.0, None, None, 0.0, 0.0, 1, 0.0)

    centered = values - float(np.median(values))
    max_abs_index = int(np.argmax(np.abs(centered)))
    polarity = 1.0 if centered[max_abs_index] >= 0.0 else -1.0
    force_like = polarity * centered
    force_like = np.maximum(force_like, 0.0)
    peak = float(np.max(force_like, initial=0.0))
    min_spacing = _double_hit_spacing_samples(values.size, delay_fraction, sample_rate)
    min_spacing_seconds = (
        float(min_spacing) / float(sample_rate)
        if sample_rate is not None and sample_rate > 0.0
        else 0.0
    )
    if peak <= 0.0:
        return DoubleHitResult(
            False,
            0,
            0.0,
            None,
            None,
            0.0,
            0.0,
            min_spacing,
            min_spacing_seconds,
        )

    threshold_fraction = float(threshold)
    if threshold_fraction > 1.0:
        threshold_fraction /= 100.0
    threshold_fraction = float(np.clip(threshold_fraction, 0.0, 1.0))
    threshold_level = peak * threshold_fraction
    peak_distance = max(1, min_spacing // 4)
    peaks, properties = _signal().find_peaks(
        force_like,
        height=threshold_level,
        distance=peak_distance,
        prominence=max(peak * 0.03, np.finfo(float).eps),
    )
    peak_count = int(len(peaks))
    if peak_count < 2:
        return DoubleHitResult(
            False,
            peak_count,
            threshold_level,
            int(peaks[0]) if peak_count else None,
            None,
            float(properties["peak_heights"][0]) if peak_count else 0.0,
            0.0,
            min_spacing,
            min_spacing_seconds,
        )

    heights = np.asarray(properties["peak_heights"], dtype=float)
    first_peak = int(peaks[0])
    first_value = float(heights[0])
    early_spacing = max(1, min_spacing // 2)
    for peak_index, peak_value in zip(peaks[1:], heights[1:]):
        peak_index = int(peak_index)
        peak_value = float(peak_value)
        spacing = peak_index - first_peak
        if spacing >= min_spacing:
            return DoubleHitResult(
                True,
                peak_count,
                threshold_level,
                first_peak,
                peak_index,
                first_value,
                peak_value,
                min_spacing,
                min_spacing_seconds,
            )
        if sample_rate is not None and sample_rate > 0.0 and spacing >= early_spacing:
            valley = float(np.min(force_like[first_peak : peak_index + 1], initial=peak))
            valley_ratio = valley / max(min(first_value, peak_value), np.finfo(float).eps)
            if valley_ratio <= 0.2:
                return DoubleHitResult(
                    True,
                    peak_count,
                    threshold_level,
                    first_peak,
                    peak_index,
                    first_value,
                    peak_value,
                    min_spacing,
                    min_spacing_seconds,
                )

    return DoubleHitResult(
        False,
        peak_count,
        threshold_level,
        first_peak,
        None,
        first_value,
        0.0,
        min_spacing,
        min_spacing_seconds,
    )


def detect_double_hit(
    reference_signal: np.ndarray,
    threshold: float,
    delay_fraction: float = 0.1,
    sample_rate: float | None = None,
) -> bool:
    """Approximate legacy double-hit reject for impact/modal tests."""
    return analyze_double_hit(
        reference_signal,
        threshold,
        delay_fraction,
        sample_rate=sample_rate,
    ).rejected


def detect_overload(
    data: np.ndarray,
    channel_names: list[str],
    metadata: dict,
    threshold: float = 0.999,
) -> bool:
    """Return True when DAQ metadata or full-scale clipping indicates overload."""
    overload_channels = metadata.get("overload_channels")
    if isinstance(overload_channels, (list, tuple, set)):
        return bool(overload_channels)
    if isinstance(overload_channels, np.ndarray):
        return bool(overload_channels.size and np.any(overload_channels))
    if bool(metadata.get("overload", False)):
        return True

    full_scales = metadata.get("channel_full_scales", {})
    if not full_scales:
        return False
    if isinstance(full_scales, dict):
        scale_by_name = full_scales
    else:
        scale_by_name = {
            name: full_scales[index]
            for index, name in enumerate(channel_names)
            if index < len(full_scales)
        }

    for index, name in enumerate(channel_names):
        try:
            full_scale = abs(float(scale_by_name.get(name, 0.0)))
        except (TypeError, ValueError, AttributeError):
            full_scale = 0.0
        if full_scale <= 0.0 or index >= data.shape[0]:
            continue
        channel_peak = float(np.max(np.abs(data[index]), initial=0.0))
        if channel_peak >= threshold * full_scale:
            return True
    return False


def apply_modal_processing(
    frame: BackendFrame,
    channel_index: dict[str, int],
    reference_name: str,
    modal: ModalProcessingConfig,
) -> tuple[BackendFrame, dict[str, bool]]:
    flags = {
        "double_hit_rejected": False,
        "overload_rejected": False,
        "rejected": False,
    }
    data = frame.data.copy()

    if modal.reject_double_hit and reference_name in channel_index:
        ref_signal = data[channel_index[reference_name]]
        double_hit = analyze_double_hit(
            ref_signal,
            modal.double_hit_threshold,
            modal.double_hit_delay_fraction,
            sample_rate=frame.sample_rate,
        )
        flags.update(
            {
                "double_hit_reference_channel": reference_name,
                "double_hit_peak_count": double_hit.peak_count,
                "double_hit_threshold_level": double_hit.threshold_level,
                "double_hit_first_peak_index": double_hit.first_peak_index,
                "double_hit_second_peak_index": double_hit.second_peak_index,
                "double_hit_first_peak_value": double_hit.first_peak_value,
                "double_hit_second_peak_value": double_hit.second_peak_value,
                "double_hit_min_spacing_samples": double_hit.min_spacing_samples,
                "double_hit_min_spacing_seconds": double_hit.min_spacing_seconds,
            }
        )
        if double_hit.rejected:
            flags["double_hit_rejected"] = True
            flags["rejected"] = True

    if modal.reject_overload:
        if detect_overload(data, list(frame.channel_names), frame.metadata):
            flags["overload_rejected"] = True
            flags["rejected"] = True

    if modal.enabled and reference_name in channel_index:
        if modal.force_window_enabled:
            fwin = force_window(data.shape[1], modal.force_window_fraction)
            data[channel_index[reference_name]] *= fwin
        if modal.exponential_window_enabled:
            ewin = exponential_window(data.shape[1], modal.exponential_decay_fraction)
            for idx in range(data.shape[0]):
                if idx == channel_index[reference_name]:
                    continue
                data[idx] *= ewin

    processed_frame = BackendFrame(
        sample_rate=frame.sample_rate,
        channel_names=frame.channel_names,
        data=data,
        timestamps=frame.timestamps,
        frame_index=frame.frame_index,
        metadata={**frame.metadata, **flags},
    )
    return processed_frame, flags


@dataclass(slots=True)
class RunningAverager:
    config: AveragingConfig
    linear_count: int = 0
    linear_accumulator: np.ndarray | None = None
    exponential_state: np.ndarray | None = None
    peak_state: np.ndarray | None = None

    def update(self, value: np.ndarray) -> np.ndarray:
        if self.config.mode == "off":
            return value
        if self.config.mode == "peak":
            if self.peak_state is None:
                self.peak_state = np.abs(value)
            else:
                self.peak_state = np.maximum(self.peak_state, np.abs(value))
            return self.peak_state
        if self.config.mode == "exponential":
            if self.exponential_state is None:
                self.exponential_state = value.copy()
            alpha = float(np.clip(self.config.exponential_alpha, 0.0, 1.0))
            self.exponential_state = alpha * value + (1.0 - alpha) * self.exponential_state
            return self.exponential_state

        if self.linear_accumulator is None:
            self.linear_accumulator = np.zeros_like(value)
            self.linear_count = 0
        target_count = max(1, self.config.count)
        if self.linear_count < target_count:
            self.linear_accumulator += value
            self.linear_count += 1
        divisor = min(max(self.linear_count, 1), target_count)
        return self.linear_accumulator / divisor


@dataclass(slots=True)
class RollingAverager:
    max_count: int
    _values: deque[np.ndarray] = field(default_factory=deque, init=False)

    def update(self, value: np.ndarray) -> np.ndarray:
        self._values.append(np.asarray(value).copy())
        while len(self._values) > max(1, self.max_count):
            self._values.popleft()
        return np.mean(np.asarray(self._values), axis=0)

    @property
    def count(self) -> int:
        return len(self._values)


@dataclass(slots=True)
class FrameProcessor:
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)
    averaging_enabled: bool = True
    _processed_frames: int = field(default=0, init=False)
    _accepted_frames: int = field(default=0, init=False)
    _fft_averager: RunningAverager = field(init=False)
    _autospectrum_averager: RunningAverager = field(init=False)
    _cross_spectrum_averager: RunningAverager = field(init=False)
    _coherence_autospectrum_averager: RollingAverager = field(init=False)
    _coherence_cross_spectrum_averager: RollingAverager = field(init=False)

    def __post_init__(self) -> None:
        averaging = self.acquisition.averaging if self.averaging_enabled else AveragingConfig(mode="off")
        self._fft_averager = RunningAverager(averaging)
        self._autospectrum_averager = RunningAverager(averaging)
        self._cross_spectrum_averager = RunningAverager(averaging)
        coherence_count = max(2, int(self.acquisition.averaging.count))
        self._coherence_autospectrum_averager = RollingAverager(coherence_count)
        self._coherence_cross_spectrum_averager = RollingAverager(coherence_count)

    def process(self, frame: BackendFrame) -> MeasurementSet:
        self._processed_frames += 1
        channel_index = {
            name: idx for idx, name in enumerate(frame.channel_names)
        }
        reference_name = self.acquisition.reference_channel
        requested_responses = self.acquisition.response_channels or []
        if reference_name not in channel_index and frame.channel_names:
            reference_name = frame.channel_names[0]
        response_names = [
            name
            for name in requested_responses
            if name in channel_index and name != reference_name
        ]
        if not response_names:
            response_names = [
                name for name in frame.channel_names if name != reference_name
            ]

        frame, modal_flags = apply_modal_processing(
            frame, channel_index, reference_name, self.acquisition.modal
        )
        frame.metadata["processing_window"] = self.acquisition.processing_window
        if modal_flags.get("rejected", False):
            return self._rejected_measurement(frame, reference_name, response_names, modal_flags)

        self._accepted_frames += 1
        frequencies, fft_data = compute_fft(frame)
        power_scale = _legacy_power_spectrum_scale(self.acquisition.processing_window)
        autospectra = (np.abs(fft_data) ** 2) * power_scale
        averaged_fft = self._fft_averager.update(fft_data)
        averaged_autospectra = self._autospectrum_averager.update(autospectra)

        frf: dict[str, np.ndarray] = {}
        coherence: dict[str, np.ndarray] = {}
        cross_spectra: dict[str, np.ndarray] = {}
        impulse_responses: dict[str, np.ndarray] = {}
        correlations = compute_correlations(frame) if self.averaging_enabled else {}

        if (
            self.averaging_enabled
            and len(frame.channel_names) >= 2
            and reference_name in channel_index
        ):
            ref_index = channel_index[reference_name]
            ref_spectrum = fft_data[ref_index]
            cross_values = []
            cross_keys: list[tuple[str, int]] = []
            for resp_name in response_names:
                resp_index = channel_index[resp_name]
                cross_keys.append((f"{reference_name}->{resp_name}", resp_index))
                cross_values.append(
                    compute_cross_spectrum(ref_spectrum, fft_data[resp_index]) * power_scale
                )

            if cross_values:
                cross_values_array = np.asarray(cross_values)
                averaged_cross = self._cross_spectrum_averager.update(cross_values_array)
                coherence_autospectra = averaged_autospectra
                coherence_cross = averaged_cross
                coherence_average_count = self._accepted_frames
                reference_power = averaged_autospectra[ref_index]
                for pair_index, (key, resp_index) in enumerate(cross_keys):
                    response_power = averaged_autospectra[resp_index]
                    cross_spectra[key] = averaged_cross[pair_index]
                    frf[key] = np.divide(
                        cross_spectra[key],
                        reference_power,
                        out=np.zeros_like(cross_spectra[key]),
                        where=np.abs(reference_power) > 0.0,
                    )
                    coherence[key] = compute_coherence_from_spectra(
                        coherence_autospectra[ref_index],
                        coherence_autospectra[resp_index],
                        coherence_cross[pair_index],
                    )
                    impulse_responses[key] = compute_impulse_response(frf[key])

        average_target = (
            self.acquisition.averaging.count
            if self.averaging_enabled
            else 0
        )
        reported_average_count = self._accepted_frames if self.averaging_enabled else 0
        if (
            self.averaging_enabled
            and self.acquisition.averaging.mode in {"linear", "peak"}
            and average_target > 0
        ):
            reported_average_count = min(reported_average_count, average_target)

        return MeasurementSet(
            sample_rate=frame.sample_rate,
            time_data={
                "t": frame.timestamps,
                "channels": {
                    name: frame.data[idx] for idx, name in enumerate(frame.channel_names)
                },
            },
            spectra={
                "f": frequencies,
                "fft": {
                    name: averaged_fft[idx] for idx, name in enumerate(frame.channel_names)
                },
                "autospectrum": {
                    name: averaged_autospectra[idx] for idx, name in enumerate(frame.channel_names)
                },
            },
            frf=frf,
            coherence=coherence,
            cross_spectra=cross_spectra,
            correlations=correlations,
            impulse_responses=impulse_responses,
            metadata={
                "frame_index": frame.frame_index,
                "rbw_hz": float(frequencies[1] - frequencies[0]) if frequencies.size > 1 else 0.0,
                "processing_window": self.acquisition.processing_window,
                "overlap_percent": int(self.acquisition.overlap_percent),
                "overlap_hop_size": frame.metadata.get("overlap_hop_size"),
                "overlap_keep_size": frame.metadata.get("overlap_keep_size"),
                "legacy_power_spectrum_scale": power_scale,
                "reference_channel": reference_name,
                "response_channels": response_names,
                "averaging_enabled": self.averaging_enabled,
                "average_count": reported_average_count,
                "average_target": average_target,
                "coherence_average_count": min(
                    locals().get("coherence_average_count", 0),
                    average_target,
                )
                if average_target
                else locals().get("coherence_average_count", 0),
                "legacy_inst_functions": list(LEGACY_INST_FUNCTIONS),
                "legacy_avg_functions": list(LEGACY_AVG_FUNCTIONS),
                "cross_functions_require_avg": True,
                "cross_functions_available": self.averaging_enabled,
                "modal_processing_note": LEGACY_MODAL_NOTES
                if (
                    self.acquisition.modal.enabled
                    or self.acquisition.modal.reject_double_hit
                    or self.acquisition.modal.reject_overload
                )
                else "",
                **modal_flags,
            },
        )

    def _rejected_measurement(
        self,
        frame: BackendFrame,
        reference_name: str,
        response_names: list[str],
        modal_flags: dict[str, bool],
    ) -> MeasurementSet:
        average_target = (
            self.acquisition.averaging.count
            if self.averaging_enabled
            else 0
        )
        reported_average_count = self._accepted_frames if self.averaging_enabled else 0
        if (
            self.averaging_enabled
            and self.acquisition.averaging.mode in {"linear", "peak"}
            and average_target > 0
        ):
            reported_average_count = min(reported_average_count, average_target)
        return MeasurementSet(
            sample_rate=frame.sample_rate,
            time_data={
                "t": frame.timestamps,
                "channels": {
                    name: frame.data[idx] for idx, name in enumerate(frame.channel_names)
                },
            },
            spectra={"f": np.array([], dtype=float), "fft": {}, "autospectrum": {}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={
                "frame_index": frame.frame_index,
                "rbw_hz": 0.0,
                "processing_window": self.acquisition.processing_window,
                "overlap_percent": int(self.acquisition.overlap_percent),
                "overlap_hop_size": frame.metadata.get("overlap_hop_size"),
                "overlap_keep_size": frame.metadata.get("overlap_keep_size"),
                "legacy_power_spectrum_scale": _legacy_power_spectrum_scale(
                    self.acquisition.processing_window
                ),
                "reference_channel": reference_name,
                "response_channels": response_names,
                "averaging_enabled": self.averaging_enabled,
                "average_count": reported_average_count,
                "average_target": average_target,
                "coherence_average_count": reported_average_count,
                "legacy_inst_functions": list(LEGACY_INST_FUNCTIONS),
                "legacy_avg_functions": list(LEGACY_AVG_FUNCTIONS),
                "cross_functions_require_avg": True,
                "cross_functions_available": self.averaging_enabled,
                "modal_processing_note": LEGACY_MODAL_NOTES
                if (
                    self.acquisition.modal.enabled
                    or self.acquisition.modal.reject_double_hit
                    or self.acquisition.modal.reject_overload
                )
                else "",
                **modal_flags,
            },
        )
