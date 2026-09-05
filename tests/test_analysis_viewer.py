from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from python_vna.analysis_derivation import (
    DERIVE_BASE_TO_TOP,
    DERIVE_TOP_TO_BASE,
    diagonal_psd_matrix,
    derive_psd_from_transfer,
    derive_time_from_transfer,
    fully_correlated_psd_matrix,
    invert_mimo_independent_input_psd,
    invert_mimo_input_psd,
    predict_mimo_response_psd,
    psd_matrix_diagonal,
    solve_mimo_independent_psd,
    synthesize_time_from_psd,
)
from python_vna.analysis_curve_editing import (
    apply_db_magnitude_profile,
    apply_power_db_profile,
    evaluate_db_control_curve,
    sample_curve_as_db_points,
    stitch_frequency_curves,
    transfer_from_db_points,
)
from python_vna.analysis_algorithms import (
    FilterConfig,
    apply_filter_to_signal,
    compute_cumulative_spectrum,
    compute_dynamic_stiffness,
    compute_mimo_transfer_function_welch,
    compute_transfer_function_welch,
    compute_welch_psd,
    compute_periodogram_psd,
    third_octave_bands,
    compute_third_octave_velocity_rms,
    convert_acceleration_psd,
    convert_acceleration_time_series,
    quantity_cumulative_label,
    quantity_psd_label,
    quantity_time_label,
)
from python_vna.analysis_data import (
    AnalysisDataset,
    AnalysisSeries,
    dataset_from_measurement,
    load_analysis_path,
    load_continuous_channels,
)
from python_vna.continuous_recording import ContinuousDatWriter
from python_vna.daq.base import BackendFrame
from python_vna.models import MeasurementSet
from python_vna.storage import default_session_config
from python_vna.ui.analysis_viewer import (
    AnalysisViewer,
    _vc_reference_acceleration_psd,
    _vc_reference_acceleration_psd_for_transfer_grid,
    _vc_reference_band_edges,
    _vc_reference_frequency_velocity,
)
from python_vna.ui.plot_interactions import DataTipText, VnaAxisItem


def _matlab_sd_round(value: float, digits: int, multiple: int) -> float:
    decade = np.ceil(np.log10(abs(value)))
    if abs(value) - 10.0**decade == 0.0:
        decade += 1.0
    scale = 10.0 ** (digits - decade)
    buffer = scale / multiple
    return float(np.floor(buffer * value + 0.5) / buffer)


def _dark_analysis_theme() -> dict[str, object]:
    return {
        "window_bg": "#111827",
        "panel_bg": "#172033",
        "panel_bg_alt": "#22304a",
        "cell_bg": "#263653",
        "plot_bg": "#05070d",
        "text": "#f5f7fb",
        "muted_text": "#a9b7c9",
        "label_text": "#f7d774",
        "axis": "#f5f7fb",
        "accent": "#2563eb",
        "accent_alt": "#0f9f8f",
        "border": "#31415f",
        "control_border": "#4b5d7a",
        "table_bg": "#0f172a",
        "menu_bg": "#101827",
        "grid_alpha": 0.25,
    }


class AnalysisAlgorithmTests(unittest.TestCase):
    def test_periodogram_and_cumulative_spectrum_are_stable(self):
        sample_rate = 1000.0
        time_s = np.arange(1000, dtype=float) / sample_rate
        signal = np.sin(2.0 * np.pi * 50.0 * time_s)

        freqs, psd = compute_periodogram_psd(signal, sample_rate)
        welch_freqs, welch_psd = compute_welch_psd(signal, sample_rate, 256)
        peak_frequency = freqs[int(np.argmax(psd))]
        cumulative_f, cumulative = compute_cumulative_spectrum(freqs, psd)

        self.assertAlmostEqual(peak_frequency, 50.0, delta=1.0)
        self.assertAlmostEqual(welch_freqs[int(np.argmax(welch_psd))], 50.0, delta=4.0)
        self.assertEqual(cumulative_f.shape, cumulative.shape)
        self.assertGreater(float(cumulative[-1]), 0.0)

    def test_psd_to_time_synthesis_infers_sample_rate_when_missing(self):
        frequency = np.array([10.0, 20.0, 30.0, 40.0], dtype=float)
        psd = np.ones(frequency.shape, dtype=float)

        time_s, values, sample_rate = synthesize_time_from_psd(frequency, psd, 0.0, seed=1)

        self.assertGreater(sample_rate, 0.0)
        self.assertGreaterEqual(time_s.size, 32)
        self.assertEqual(time_s.size, values.size)
        self.assertGreater(float(np.std(values)), 0.0)

    def test_vc_psd_time_synthesis_preserves_third_octave_levels(self):
        frequency, acceleration_psd = _vc_reference_acceleration_psd("VC C")
        band_edges = _vc_reference_band_edges("VC C")
        self.assertIsNotNone(band_edges)

        time_s, values, sample_rate = synthesize_time_from_psd(
            frequency,
            acceleration_psd,
            4096.0,
            seed=2,
            duration_s=32.0,
            band_edges=band_edges,
        )

        self.assertGreater(time_s.size, 1000)
        welch_freqs, welch_psd = compute_periodogram_psd(values, sample_rate)
        rbw_hz = float(np.median(np.diff(welch_freqs)))
        centers, velocity = compute_third_octave_velocity_rms(welch_freqs, welch_psd, rbw_hz)
        target_centers, target_velocity = _vc_reference_frequency_velocity("VC C")
        expected = np.interp(centers, target_centers, target_velocity)
        in_band = (centers >= 2.0) & (centers <= 800.0)
        ratio = velocity[in_band] / expected[in_band]
        self.assertLess(float(np.nanmax(ratio)), 1.35)
        self.assertGreater(float(np.nanmin(ratio)), 0.65)

    def test_welch_transfer_from_time_data_recovers_gain(self):
        sample_rate = 1000.0
        time_s = np.arange(4096, dtype=float) / sample_rate
        reference = np.sin(2.0 * np.pi * 40.0 * time_s)
        response = 2.5 * reference

        freqs, xfer = compute_transfer_function_welch(reference, response, sample_rate, 512)

        peak_index = int(np.argmin(np.abs(freqs - 40.0)))
        self.assertAlmostEqual(abs(xfer[peak_index]), 2.5, delta=0.1)

    def test_derived_psd_transfer_converts_both_directions(self):
        freqs = np.array([10.0, 20.0, 30.0], dtype=float)
        psd = np.array([1.0, 2.0, 3.0], dtype=float)
        transfer = np.full(freqs.shape, 2.0 + 0.0j, dtype=complex)

        top_f, top_psd = derive_psd_from_transfer(
            freqs,
            psd,
            freqs,
            transfer,
            direction=DERIVE_BASE_TO_TOP,
        )
        base_f, base_psd = derive_psd_from_transfer(
            freqs,
            top_psd,
            freqs,
            transfer,
            direction=DERIVE_TOP_TO_BASE,
        )

        np.testing.assert_allclose(top_f, freqs)
        np.testing.assert_allclose(top_psd, psd * 4.0)
        np.testing.assert_allclose(base_f, freqs)
        np.testing.assert_allclose(base_psd, psd)

    def test_derived_psd_transfer_can_apply_coherence_correction(self):
        freqs = np.array([10.0, 20.0, 30.0], dtype=float)
        psd = np.array([1.0, 2.0, 3.0], dtype=float)
        transfer = np.full(freqs.shape, 2.0 + 0.0j, dtype=complex)
        coherence = np.full(freqs.shape, 0.25, dtype=float)

        top_f, top_psd = derive_psd_from_transfer(
            freqs,
            psd,
            freqs,
            transfer,
            direction=DERIVE_BASE_TO_TOP,
            coherence_frequency=freqs,
            coherence_values=coherence,
            coherence_correction=True,
        )
        base_f, base_psd = derive_psd_from_transfer(
            freqs,
            top_psd,
            freqs,
            transfer,
            direction=DERIVE_TOP_TO_BASE,
            coherence_frequency=freqs,
            coherence_values=coherence,
            coherence_correction=True,
        )

        np.testing.assert_allclose(top_f, freqs)
        np.testing.assert_allclose(top_psd, psd * 16.0)
        np.testing.assert_allclose(base_f, freqs)
        np.testing.assert_allclose(base_psd, psd)

    def test_mimo_forward_matches_scalar_diagonal_transfer(self):
        freqs = np.array([10.0, 20.0], dtype=float)
        transfer = np.zeros((2, 3, 3), dtype=complex)
        gains = np.array([2.0, 3.0, 4.0], dtype=float)
        transfer[:, np.arange(3), np.arange(3)] = gains
        input_psd = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=float)

        out_f, output_matrix = predict_mimo_response_psd(
            freqs,
            transfer,
            diagonal_psd_matrix(input_psd),
        )

        np.testing.assert_allclose(out_f, freqs)
        np.testing.assert_allclose(psd_matrix_diagonal(output_matrix), input_psd * gains**2)
        np.testing.assert_allclose(output_matrix[:, 0, 1], 0.0)

    def test_mimo_forward_adds_coupled_independent_axis_contributions(self):
        freqs = np.array([15.0], dtype=float)
        transfer = np.array([[[1.0, 0.5, 0.0], [0.25, 2.0, 0.0]]], dtype=complex)
        input_psd = np.array([[4.0, 9.0, 0.0]], dtype=float)

        _out_f, output_matrix = predict_mimo_response_psd(
            freqs,
            transfer,
            diagonal_psd_matrix(input_psd),
        )

        output_psd = psd_matrix_diagonal(output_matrix)
        np.testing.assert_allclose(output_psd[0, 0], 1.0**2 * 4.0 + 0.5**2 * 9.0)
        np.testing.assert_allclose(output_psd[0, 1], 0.25**2 * 4.0 + 2.0**2 * 9.0)

    def test_mimo_independent_inverse_then_forward_recovers_target(self):
        freqs = np.array([10.0, 20.0], dtype=float)
        transfer = np.zeros((2, 3, 3), dtype=complex)
        transfer[:] = np.array(
            [
                [1.2, 0.2, 0.1],
                [0.1, 1.4, 0.15],
                [0.05, 0.25, 1.1],
            ],
            dtype=complex,
        )
        target_psd = np.array([[2.0, 3.0, 4.0], [5.0, 6.0, 7.0]], dtype=float)

        out_f, input_psd = invert_mimo_independent_input_psd(
            freqs,
            transfer,
            target_psd,
        )
        check_f, output_matrix = predict_mimo_response_psd(
            out_f,
            transfer,
            diagonal_psd_matrix(input_psd),
        )

        np.testing.assert_allclose(out_f, freqs)
        np.testing.assert_allclose(check_f, freqs)
        np.testing.assert_allclose(psd_matrix_diagonal(output_matrix), target_psd, rtol=1e-10, atol=1e-10)

    def test_mimo_independent_inverse_default_preserves_small_unitful_transfer(self):
        freqs = np.array([3.0, 10.0], dtype=float)
        transfer = np.zeros((2, 3, 3), dtype=complex)
        gains = np.array([5e-14, 2e-13, 8e-13], dtype=float)
        transfer[:, np.arange(3), np.arange(3)] = gains
        target_psd = np.array([[8e-8, 8e-8, 8e-8], [2e-7, 2e-7, 2e-7]], dtype=float)

        out_f, input_psd, predicted_psd = solve_mimo_independent_psd(freqs, transfer, target_psd)

        np.testing.assert_allclose(out_f, freqs)
        self.assertTrue(np.all(np.isfinite(input_psd)))
        np.testing.assert_allclose(predicted_psd, target_psd, rtol=1e-10, atol=1e-20)

    def test_mimo_full_cross_psd_inverse_handles_correlated_axes(self):
        freqs = np.array([25.0], dtype=float)
        transfer = np.array(
            [
                [
                    [1.0 + 0.0j, 0.15 + 0.05j, 0.0 + 0.0j],
                    [0.0 + 0.0j, 1.1 + 0.0j, 0.2 - 0.05j],
                    [0.1 + 0.0j, 0.0 + 0.0j, 0.9 + 0.0j],
                ]
            ],
            dtype=complex,
        )
        source_psd = fully_correlated_psd_matrix(np.array([[3.0, 2.0, 1.0]], dtype=float))
        _f, target_matrix = predict_mimo_response_psd(freqs, transfer, source_psd)

        inv_f, recovered_input = invert_mimo_input_psd(freqs, transfer, target_matrix, regularization_floor=0.0)
        check_f, recovered_output = predict_mimo_response_psd(inv_f, transfer, recovered_input)

        np.testing.assert_allclose(check_f, freqs)
        np.testing.assert_allclose(recovered_output, target_matrix, rtol=1e-10, atol=1e-10)

    def test_mimo_inverse_regularization_prevents_near_singular_blowup(self):
        freqs = np.array([10.0], dtype=float)
        transfer = np.array([[[1e-9, 0.0], [0.0, 1.0]]], dtype=complex)
        target = diagonal_psd_matrix(np.array([[1.0, 1.0]], dtype=float))

        out_f, input_matrix = invert_mimo_input_psd(
            freqs,
            transfer,
            target,
            regularization_floor=1e-3,
        )

        np.testing.assert_allclose(out_f, freqs)
        self.assertTrue(np.all(np.isfinite(input_matrix)))
        self.assertLess(float(psd_matrix_diagonal(input_matrix)[0, 0]), 1e6)

    def test_mimo_welch_transfer_recovers_correlated_time_inputs(self):
        rng = np.random.default_rng(123)
        sample_rate = 1024.0
        sample_count = 8192
        input_x = rng.standard_normal(sample_count)
        input_y = 0.75 * input_x + 0.4 * rng.standard_normal(sample_count)
        input_z = -0.3 * input_x + 0.2 * input_y + 0.5 * rng.standard_normal(sample_count)
        inputs = np.vstack([input_x, input_y, input_z])
        expected = np.array(
            [
                [1.0, 0.35, 0.10],
                [0.20, 1.30, -0.25],
                [-0.15, 0.45, 0.90],
            ],
            dtype=float,
        )
        outputs = expected @ inputs

        frequency, transfer = compute_mimo_transfer_function_welch(
            inputs,
            outputs,
            sample_rate,
            2048,
            regularization_floor=0.0,
        )

        self.assertGreater(frequency.size, 10)
        np.testing.assert_allclose(np.median(np.real(transfer), axis=0), expected, rtol=1e-10, atol=1e-10)
        _pair_f, pair_h = compute_transfer_function_welch(inputs[0], outputs[0], sample_rate, 2048)
        self.assertGreater(abs(float(np.median(np.real(pair_h))) - expected[0, 0]), 0.1)

    def test_manual_transfer_points_fit_in_log_frequency(self):
        control_f = np.array([10.0, 100.0, 1000.0], dtype=float)
        control_db = np.array([0.0, 20.0, 40.0], dtype=float)
        target_f = np.array([10.0, 100.0, 1000.0], dtype=float)

        fitted_f, fitted_h = transfer_from_db_points(control_f, control_db, target_f)
        top_f, top_psd = derive_psd_from_transfer(
            target_f,
            np.ones_like(target_f),
            fitted_f,
            fitted_h,
            direction=DERIVE_BASE_TO_TOP,
        )

        np.testing.assert_allclose(fitted_f, target_f)
        np.testing.assert_allclose(20.0 * np.log10(fitted_h), control_db, atol=1e-12)
        np.testing.assert_allclose(top_f, target_f)
        np.testing.assert_allclose(top_psd, [1.0, 100.0, 10000.0], rtol=1e-12)

    def test_adaptive_db_sampling_preserves_narrow_transfer_features(self):
        freqs = np.logspace(1.0, 3.0, 600)
        log_f = np.log10(freqs)
        source_db = (
            -8.0
            + 4.0 * np.sin(6.0 * log_f)
            + 28.0 * np.exp(-0.5 * ((log_f - np.log10(320.0)) / 0.025) ** 2)
            - 22.0 * np.exp(-0.5 * ((log_f - np.log10(520.0)) / 0.02) ** 2)
        )
        values = 10.0 ** (source_db / 20.0)
        sparse_targets = np.logspace(1.0, 3.0, 8)

        sparse_f, sparse_db = sample_curve_as_db_points(
            freqs,
            values,
            power_values=False,
            target_frequency_hz=sparse_targets,
        )
        adaptive_f, adaptive_db = sample_curve_as_db_points(
            freqs,
            values,
            power_values=False,
            target_frequency_hz=sparse_targets,
            max_count=90,
            error_threshold_db=2.0,
        )
        fitted_f, sparse_fit_db = evaluate_db_control_curve(sparse_f, sparse_db, freqs)
        _fitted_f, adaptive_fit_db = evaluate_db_control_curve(adaptive_f, adaptive_db, freqs)
        source_at_fit = np.interp(np.log10(fitted_f), log_f, source_db)

        self.assertGreater(adaptive_f.size, sparse_f.size)
        self.assertLess(np.max(np.abs(adaptive_fit_db - source_at_fit)), 2.0)
        self.assertGreater(np.max(np.abs(sparse_fit_db - source_at_fit)), 20.0)
        self.assertTrue(np.any(np.abs(np.log10(adaptive_f) - np.log10(320.0)) < 0.02))
        self.assertTrue(np.any(np.abs(np.log10(adaptive_f) - np.log10(520.0)) < 0.02))

    def test_transfer_magnitude_edit_preserves_complex_phase(self):
        freqs = np.array([10.0, 20.0, 30.0], dtype=float)
        phase = np.exp(1.0j * np.array([0.1, 0.2, 0.3], dtype=float))
        source = np.array([1.0, 2.0, 3.0], dtype=float) * phase

        edited_f, edited = apply_db_magnitude_profile(
            freqs,
            source,
            np.array([10.0, 20.0, 30.0], dtype=float),
            np.array([6.0, 6.0, 6.0], dtype=float),
        )

        np.testing.assert_allclose(edited_f, freqs)
        np.testing.assert_allclose(np.angle(edited), np.angle(source), atol=1e-12)
        np.testing.assert_allclose(np.abs(edited), 10.0 ** (6.0 / 20.0), rtol=1e-12)

    def test_psd_edit_and_stitch_helpers(self):
        freqs = np.array([10.0, 20.0, 30.0, 40.0], dtype=float)
        psd = np.ones_like(freqs)

        edited_f, edited_psd = apply_power_db_profile(
            freqs,
            psd,
            np.array([10.0, 20.0, 40.0], dtype=float),
            np.array([0.0, 10.0, 0.0], dtype=float),
        )
        stitched_f, stitched = stitch_frequency_curves(
            edited_f,
            edited_psd,
            np.array([25.0, 35.0, 45.0], dtype=float),
            np.array([100.0, 200.0, 300.0], dtype=float),
            30.0,
        )

        self.assertGreater(edited_psd[1], edited_psd[0])
        np.testing.assert_allclose(stitched_f, [10.0, 20.0, 30.0, 35.0, 45.0])
        np.testing.assert_allclose(stitched[-2:], [200.0, 300.0])

    def test_derived_time_requires_complex_transfer_and_recovers_gain(self):
        sample_rate = 1000.0
        time_s = np.arange(1000, dtype=float) / sample_rate
        signal = np.sin(2.0 * np.pi * 20.0 * time_s)
        transfer_f = np.array([1.0, 20.0, 500.0], dtype=float)
        complex_h = np.full(3, 2.0 + 0.0j, dtype=complex)

        derived_t, derived = derive_time_from_transfer(
            time_s,
            signal,
            sample_rate,
            transfer_f,
            complex_h,
            direction=DERIVE_BASE_TO_TOP,
        )
        no_phase_t, no_phase = derive_time_from_transfer(
            time_s,
            signal,
            sample_rate,
            transfer_f,
            np.full(3, 2.0, dtype=float),
            direction=DERIVE_BASE_TO_TOP,
        )

        np.testing.assert_allclose(derived_t, time_s)
        np.testing.assert_allclose(derived, 2.0 * signal, atol=1e-10)
        self.assertEqual(no_phase_t.size, 0)
        self.assertEqual(no_phase.size, 0)

    def test_acceleration_conversions_match_expected_units(self):
        freqs = np.array([1.0, 10.0, 100.0], dtype=float)
        psd = np.ones(3, dtype=float)
        velocity_f, velocity_psd = convert_acceleration_psd(freqs, psd, "Velocity")

        np.testing.assert_allclose(velocity_f, freqs)
        np.testing.assert_allclose(velocity_psd, psd / ((2.0 * np.pi * freqs) ** 2) * 1e12)

        sample_rate = 1000.0
        signal = np.sin(2.0 * np.pi * 20.0 * np.arange(1000) / sample_rate)
        velocity = convert_acceleration_time_series(signal, sample_rate, "Velocity")
        self.assertEqual(velocity.shape, signal.shape)
        self.assertTrue(np.all(np.isfinite(velocity)))

    def test_force_quantity_conversions_keep_raw_force_units(self):
        freqs = np.array([1.0, 10.0, 100.0], dtype=float)
        force_psd = np.array([4.0, 9.0, 16.0], dtype=float)
        force_f, converted_force_psd = convert_acceleration_psd(freqs, force_psd, "Force")

        np.testing.assert_allclose(force_f, freqs)
        np.testing.assert_allclose(converted_force_psd, force_psd)

        sample_rate = 1000.0
        force = np.sin(2.0 * np.pi * 20.0 * np.arange(1000) / sample_rate)
        converted_force = convert_acceleration_time_series(force, sample_rate, "Force")
        np.testing.assert_allclose(converted_force, force)
        self.assertEqual(quantity_time_label("Force"), "Force (N)")
        self.assertEqual(quantity_psd_label("Force"), "N^2/Hz")
        self.assertEqual(quantity_cumulative_label("Force"), "3 sigma force (N)")

    def test_foundation_helpers_compute_expected_curves(self):
        freqs = np.linspace(2.0, 200.0, 200)
        psd = np.full_like(freqs, 1e-6)
        centers, velocity = compute_third_octave_velocity_rms(freqs, psd, 1.0)

        self.assertGreater(centers.size, 0)
        self.assertEqual(centers.shape, velocity.shape)
        self.assertTrue(np.all(velocity > 0.0))

        frf = np.full_like(freqs, 2.0 + 0.0j, dtype=complex)
        stiff_f, stiffness = compute_dynamic_stiffness(freqs, frf, 1.0, 800.0)
        self.assertEqual(stiff_f.shape, stiffness.shape)
        self.assertTrue(np.all(stiffness > 0.0))

    def test_highpass_filter_trim_matches_matlab_padding_limit(self):
        sample_rate = 2560.0
        time_s = np.arange(4096, dtype=float) / sample_rate
        signal = 0.2 + np.sin(2.0 * np.pi * 20.0 * time_s)

        filtered, trim = apply_filter_to_signal(
            signal,
            sample_rate,
            FilterConfig(highpass_enabled=True, highpass_hz=5.0, order=4),
        )

        self.assertEqual(filtered.shape, signal.shape)
        self.assertTrue(np.all(np.isfinite(filtered)))
        self.assertLess(trim, 64)

    def test_third_octave_band_edges_follow_matlab_unrounded_centers(self):
        centers, lower_edges, upper_edges = third_octave_bands(1.0, 100.0)
        index = int(np.argmin(np.abs(centers - 20.0)))
        raw_center = 1000.0 / (10.0 ** (3.0 / (10.0 * 3.0))) ** 17

        self.assertAlmostEqual(centers[index], 20.0)
        self.assertAlmostEqual(lower_edges[index], _matlab_sd_round(raw_center / (2.0 ** (1.0 / 6.0)), 3, 5))
        self.assertAlmostEqual(upper_edges[index], _matlab_sd_round(raw_center * (2.0 ** (1.0 / 6.0)), 3, 5))


class AnalysisDataTests(unittest.TestCase):
    def test_load_numeric_text_with_time_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1,2\n0.1,3,4\n0.2,5,6\n", encoding="utf-8")

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.sample_rate, 10.0)
        self.assertEqual([series.display_name for series in dataset.series], ["Ch 1", "Ch 2"])
        time_s, values = dataset.load_time_series("Ch 2")
        np.testing.assert_allclose(time_s, [0.0, 0.1, 0.2])
        np.testing.assert_allclose(values, [2.0, 4.0, 6.0])

    def test_load_headered_csv_with_time_column_and_trailing_empty_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sim_saved.csv"
            path.write_text(
                "time,pay_below_x,pay_below_y,pay_below_z,\n"
                "0.000000,0.000096,0.000122,0.000000\n"
                "0.000400,0.000089,-0.036738,-0.000001\n"
                "0.000800,0.000093,-0.069735,-0.000006\n",
                encoding="utf-8",
            )

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.metadata["source"], "headered_time_table")
        self.assertAlmostEqual(dataset.sample_rate, 2500.0)
        self.assertEqual(
            [series.display_name for series in dataset.series],
            ["pay_below_x", "pay_below_y", "pay_below_z"],
        )
        time_s, values = dataset.load_time_series("pay_below_y")
        np.testing.assert_allclose(time_s, [0.0, 0.0004, 0.0008])
        np.testing.assert_allclose(values, [0.000122, -0.036738, -0.069735])

    def test_load_plot_export_psd_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "exported_psd.csv"
            path.write_text(
                "# python_vna_plot_export=1\n"
                "# plot_kind=psd\n"
                "active_trace_x,active_trace_y\n"
                "1,0.25\n"
                "2,0.5\n"
                "4,1\n",
                encoding="utf-8-sig",
            )

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.metadata["source"], "plot_export")
        self.assertEqual(dataset.metadata["autospectrum_kind"], "psd")
        np.testing.assert_allclose(dataset.frequency_hz, [1.0, 2.0, 4.0])
        self.assertIn("active trace", dataset.autospectrum)
        np.testing.assert_allclose(dataset.autospectrum["active trace"], [0.25, 0.5, 1.0])

    def test_load_plot_export_transfer_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "exported_transfer.csv"
            path.write_text(
                "# python_vna_plot_export=1\n"
                "# plot_kind=transfer\n"
                "top_to_base_x,top_to_base_y\n"
                "1,0\n"
                "2,6.020599913279624\n"
                "4,-6.020599913279624\n",
                encoding="utf-8-sig",
            )

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.metadata["source"], "plot_export")
        self.assertEqual(dataset.metadata["plot_kind"], "transfer")
        self.assertEqual([series.channel_key for series in dataset.series], ["Input", "top to base"])
        self.assertIn("Input->top to base", dataset.frf)
        np.testing.assert_allclose(np.abs(dataset.frf["Input->top to base"]), [1.0, 2.0, 0.5])

    def test_forced_csv_import_as_psd_uses_first_column_as_frequency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "generic_frequency.csv"
            path.write_text(
                "Frequency,base,top\n"
                "1,0.25,0.5\n"
                "2,0.5,1.0\n"
                "4,1.0,2.0\n",
                encoding="utf-8",
            )

            dataset = load_analysis_path(path, fs_hint=100.0, import_kind="psd")

        self.assertEqual(dataset.metadata["import_kind"], "psd")
        self.assertEqual(dataset.metadata["autospectrum_kind"], "psd")
        np.testing.assert_allclose(dataset.frequency_hz, [1.0, 2.0, 4.0])
        self.assertIn("base", dataset.autospectrum)
        self.assertIn("top", dataset.autospectrum)
        np.testing.assert_allclose(dataset.autospectrum["top"], [0.5, 1.0, 2.0])

    def test_forced_csv_import_as_transfer_uses_db_magnitude_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "generic_transfer.csv"
            path.write_text(
                "Frequency,top\n"
                "1,0\n"
                "2,6.020599913279624\n"
                "4,-6.020599913279624\n",
                encoding="utf-8",
            )

            dataset = load_analysis_path(path, fs_hint=100.0, import_kind="transfer")

        self.assertEqual(dataset.metadata["import_kind"], "transfer")
        self.assertEqual(dataset.metadata["plot_kind"], "transfer")
        self.assertIn("Input->top", dataset.frf)
        np.testing.assert_allclose(np.abs(dataset.frf["Input->top"]), [1.0, 2.0, 0.5])

    def test_forced_mat_import_as_psd_accepts_frequency_variable(self):
        from scipy.io import savemat

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "frequency_psd.mat"
            savemat(
                path,
                {
                    "frequency": np.array([1.0, 2.0, 4.0], dtype=float),
                    "base_psd": np.array([0.25, 0.5, 1.0], dtype=float),
                },
            )

            dataset = load_analysis_path(path, fs_hint=100.0, import_kind="psd")

        self.assertEqual(dataset.metadata["import_kind"], "psd")
        self.assertIn("base_psd", dataset.autospectrum)
        np.testing.assert_allclose(dataset.frequency_hz, [1.0, 2.0, 4.0])
        np.testing.assert_allclose(dataset.autospectrum["base_psd"], [0.25, 0.5, 1.0])

    def test_load_floor_response_eu_ascii_as_acceleration_psd(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "Gxxsv00005 - TF-0003.txt"
            path.write_text(
                "Data written for 3 signals in a group signal\n"
                "Ref Chan:Resp Chan\t 0-Z:0-Z\t 0-Z:0-Z\t 0-Z:0-Z\n"
                "Channel Names\tI2\tI3\tI4\n"
                "Channel Comments\t\t\t\n"
                "\n"
                "   \t G2, 2\t G3, 3\t G4, 4\n"
                "Frequency\t Mag\tMag\tMag\n"
                "   \tEU\tEU\tEU\n"
                "\n"
                "Units:\n"
                "Hz\tg\tg\tg\n"
                "0.0000000000000e+000\t0.10\t0.20\t0.30\n"
                "3.1250000000000e-001\t0.20\t0.30\t0.40\n"
                "6.2500000000000e-001\t0.40\t0.50\t0.60\n",
                encoding="utf-8",
            )

            dataset = load_analysis_path(path, fs_hint=100.0)

        expected_first = (0.10 * 9.8) ** 2 / 0.9376
        expected_second = (0.20 * 9.8) ** 2 / 0.9376
        self.assertEqual(dataset.metadata["source"], "floor_response_eu_ascii")
        self.assertEqual(dataset.metadata["autospectrum_kind"], "psd")
        self.assertAlmostEqual(dataset.rbw_hz, 0.3125)
        np.testing.assert_allclose(dataset.frequency_hz, [0.0, 0.3125, 0.625])
        self.assertEqual(
            [series.channel_key for series in dataset.series],
            ["I2 (G2, 2)", "I3 (G3, 3)", "I4 (G4, 4)"],
        )
        np.testing.assert_allclose(dataset.autospectrum["I2 (G2, 2)"][:2], [expected_first, expected_second])

    def test_load_floor_response_transfer_ascii_with_companion_coherence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            transfer_path = root / "Hxysv00014 - HS-2#_009_Z.txt"
            coherence_path = root / "Cxysv00014 - HS-2#_009_Z.txt"
            transfer_path.write_text(
                "Data written for 3 signals in a group signal\n"
                "Ref Chan:Resp Chan\t0-Z:0-Z\t0-Z:0-Z\t0-Z:0-Z\n"
                "Channel Names\tI1 : I2\tI1 : I3\tI1 : I4\n"
                "Channel Comments\t\t\t\n\n"
                "\tH1, 2\t\tH1, 3\t\tH1, 4\n"
                "Frequency\tMag\tPhase\tMag\tPhase\tMag\tPhase\n"
                "\tRatio\t\tRatio\t\tRatio\n\n"
                "Units:\n"
                "Hz\tg / N\t\tg / N\t\tg / N\n"
                "0\t0.10\t0\t0.20\t0\t0.30\t0\n"
                "40\t0.20\t90\t0.40\t-90\t0.60\t180\n"
                "80\t0.40\t45\t0.80\t-45\t1.20\t90\n",
                encoding="utf-8",
            )
            coherence_path.write_text(
                "Data written for 3 signals in a group signal\n"
                "Ref Chan:Resp Chan\t0-Z:0-Z\t0-Z:0-Z\t0-Z:0-Z\n"
                "Channel Names\tI2\tI3\tI4\n"
                "Channel Comments\t\t\t\n\n"
                "\tC1, 2\tC1, 3\tC1, 4\n"
                "Frequency\tMag\tMag\tMag\n"
                "\tRatio\tRatio\tRatio\n\n"
                "Units:\n"
                "Hz\t\t\t\n"
                "0\t0.90\t0.80\t0.70\n"
                "40\t0.91\t0.81\t0.71\n"
                "80\t0.92\t0.82\t0.72\n",
                encoding="utf-8",
            )

            dataset = load_analysis_path(transfer_path, fs_hint=100.0)
            coherence_dataset = load_analysis_path(coherence_path, fs_hint=100.0)

        self.assertEqual(dataset.metadata["source"], "floor_response_transfer_ascii")
        self.assertEqual([series.channel_key for series in dataset.series], ["ai0", "ai1", "ai2", "ai3"])
        self.assertEqual([series.display_name for series in dataset.series], ["I1", "I2", "I3", "I4"])
        self.assertEqual(set(dataset.frf), {"ai0->ai1", "ai0->ai2", "ai0->ai3"})
        self.assertEqual(set(dataset.coherence), {"ai0->ai1", "ai0->ai2", "ai0->ai3"})
        self.assertEqual(coherence_dataset.path.name, coherence_path.name)
        self.assertEqual(set(coherence_dataset.frf), set(dataset.frf))
        self.assertEqual(set(coherence_dataset.coherence), set(dataset.coherence))
        np.testing.assert_allclose(np.abs(dataset.frf["ai0->ai3"]), [0.30, 0.60, 1.20])
        np.testing.assert_allclose(dataset.coherence["ai0->ai3"], [0.70, 0.71, 0.72])
        stiffness_f, stiffness = compute_dynamic_stiffness(
            dataset.frequency_hz,
            dataset.frf["ai0->ai3"],
            dataset.series[3].scale,
            dataset.series[0].scale,
        )
        np.testing.assert_allclose(stiffness_f, [40.0, 80.0])
        np.testing.assert_allclose(
            stiffness,
            (2.0 * np.pi * stiffness_f) ** 2 / (np.array([0.60, 1.20]) * 10.0),
        )

    def test_load_simulink_mat_with_global_time_and_numeric_channels(self):
        from scipy.io import savemat

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sim_export.mat"
            time_s = np.array([0.0, 0.1, 0.2, 0.3], dtype=float)
            savemat(
                path,
                {
                    "tout": time_s,
                    "motor_speed": np.array([1.0, 2.0, 4.0, 8.0], dtype=float),
                    "controller": np.column_stack((time_s, time_s + 10.0, time_s + 20.0)),
                },
            )

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.metadata["source"], "simulink_mat")
        self.assertAlmostEqual(dataset.sample_rate, 10.0)
        self.assertIn("motor_speed", dataset.channels)
        self.assertIn("controller_1", dataset.channels)
        self.assertIn("controller_2", dataset.channels)
        np.testing.assert_allclose(dataset.time_s, time_s)
        np.testing.assert_allclose(dataset.channels["motor_speed"], [1.0, 2.0, 4.0, 8.0])
        np.testing.assert_allclose(dataset.channels["controller_1"], time_s + 10.0)
        np.testing.assert_allclose(dataset.channels["controller_2"], time_s + 20.0)

    def test_load_simulink_mat_structure_with_time_signals_values(self):
        from scipy.io import savemat

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "structure_with_time.mat"
            time_s = np.array([0.0, 0.05, 0.10, 0.15], dtype=float)
            values = np.column_stack((np.sin(time_s), np.cos(time_s)))
            savemat(
                path,
                {
                    "simout": {
                        "time": time_s,
                        "signals": {
                            "values": values,
                            "label": "plant",
                        },
                    },
                },
            )

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.metadata["source"], "simulink_mat")
        self.assertAlmostEqual(dataset.sample_rate, 20.0)
        self.assertIn("plant_1", dataset.channels)
        self.assertIn("plant_2", dataset.channels)
        np.testing.assert_allclose(dataset.channels["plant_1"], values[:, 0])
        np.testing.assert_allclose(dataset.channels["plant_2"], values[:, 1])

    def test_load_simulink_mat_v73_hdf5_numeric_channels(self):
        import h5py

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sim_export_v73.mat"
            time_s = np.array([0.0, 0.1, 0.2, 0.3], dtype=float)
            with h5py.File(path, "w") as handle:
                handle.create_dataset("tout", data=time_s)
                handle.create_dataset("motor_speed", data=np.array([1.0, 2.0, 4.0, 8.0], dtype=float))
                handle.create_dataset("controller", data=np.column_stack((time_s, time_s + 10.0)))

            with mock.patch(
                "scipy.io.loadmat",
                side_effect=NotImplementedError("Please use HDF reader for matlab v7.3 files, e.g. h5py"),
            ):
                dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.metadata["source"], "simulink_mat")
        self.assertAlmostEqual(dataset.sample_rate, 10.0)
        self.assertIn("motor_speed", dataset.channels)
        self.assertIn("controller", dataset.channels)
        np.testing.assert_allclose(dataset.time_s, time_s)
        np.testing.assert_allclose(dataset.channels["motor_speed"], [1.0, 2.0, 4.0, 8.0])
        np.testing.assert_allclose(dataset.channels["controller"], time_s + 10.0)

    def test_load_continuous_manifest_lazily_reads_frames(self):
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
            )
            writer.start()
            writer.write_frame(
                BackendFrame(
                    sample_rate=2560.0,
                    channel_names=["ai0", "ai1"],
                    data=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=float),
                    timestamps=np.array([0.0, 1.0 / 2560.0, 2.0 / 2560.0]),
                    frame_index=1,
                    metadata={},
                )
            )
            writer.close()

            dataset = load_analysis_path(output_dir / "manifest.json")
            time_s, values = dataset.load_time_series("ai1")
            bulk_time_s, bulk_channels = load_continuous_channels(dataset, ["ai0", "ai1"])

        self.assertTrue(dataset.is_continuous)
        self.assertEqual(dataset.sample_rate, 2560.0)
        self.assertEqual(dataset.metadata["frame_size"], 4096)
        np.testing.assert_allclose(values, [4.0, 5.0, 6.0])
        np.testing.assert_allclose(time_s, np.array([0.0, 1.0, 2.0]) / 2560.0)
        np.testing.assert_allclose(bulk_time_s, time_s)
        np.testing.assert_allclose(bulk_channels["ai0"], [1.0, 2.0, 3.0])
        np.testing.assert_allclose(bulk_channels["ai1"], [4.0, 5.0, 6.0])

    def test_load_continuous_zip_segments_uses_bulk_table_path(self):
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                compress_closed_segments=True,
            )
            writer.start()
            writer.write_frame(
                BackendFrame(
                    sample_rate=2560.0,
                    channel_names=["ai0", "ai1"],
                    data=np.array([[1.0, 2.0, 3.0], [11.0, 12.0, 13.0]], dtype=float),
                    timestamps=np.array([0.0, 1.0 / 2560.0, 2.0 / 2560.0]),
                    frame_index=1,
                    metadata={},
                )
            )
            writer.close()

            dataset = load_analysis_path(output_dir / "manifest.json")
            self.assertEqual(dataset.continuous_segments[0].path.suffix.lower(), ".zip")
            time_s, channels = load_continuous_channels(dataset, ["ai0", "ai1"])

        np.testing.assert_allclose(time_s, np.array([0.0, 1.0, 2.0]) / 2560.0)
        np.testing.assert_allclose(channels["ai0"], [1.0, 2.0, 3.0])
        np.testing.assert_allclose(channels["ai1"], [11.0, 12.0, 13.0])

    def test_load_continuous_full_range_reuses_channel_cache(self):
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
                compress_closed_segments=True,
            )
            writer.start()
            writer.write_frame(
                BackendFrame(
                    sample_rate=2560.0,
                    channel_names=["ai0", "ai1"],
                    data=np.array([[1.0, 2.0, 3.0], [11.0, 12.0, 13.0]], dtype=float),
                    timestamps=np.array([0.0, 1.0 / 2560.0, 2.0 / 2560.0]),
                    frame_index=1,
                    metadata={},
                )
            )
            writer.close()

            dataset = load_analysis_path(output_dir / "manifest.json")
            first_time_s, first_channels = load_continuous_channels(dataset, ["ai0"])
            for segment in dataset.continuous_segments:
                segment.path.unlink()
            cached_time_s, cached_channels = load_continuous_channels(dataset, ["ai0"])

        np.testing.assert_allclose(first_time_s, cached_time_s)
        np.testing.assert_allclose(first_channels["ai0"], cached_channels["ai0"])

    def test_load_continuous_multiple_segments_keeps_manifest_order(self):
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0"],
                software_version="test",
                segment_seconds=0.01,
                compress_closed_segments=True,
            )
            writer.start()
            writer.write_frame(
                BackendFrame(
                    sample_rate=100.0,
                    channel_names=["ai0"],
                    data=np.array([[1.0, 2.0]], dtype=float),
                    timestamps=np.array([0.0, 0.01]),
                    frame_index=1,
                    metadata={},
                )
            )
            writer._monotonic_fn = lambda: 1.0
            writer.write_frame(
                BackendFrame(
                    sample_rate=100.0,
                    channel_names=["ai0"],
                    data=np.array([[3.0, 4.0]], dtype=float),
                    timestamps=np.array([0.02, 0.03]),
                    frame_index=2,
                    metadata={},
                )
            )
            writer.close()

            dataset = load_analysis_path(output_dir / "manifest.json")
            time_s, channels = load_continuous_channels(dataset, ["ai0"])

        np.testing.assert_allclose(time_s, np.arange(4, dtype=float) / 2560.0)
        np.testing.assert_allclose(channels["ai0"], [1.0, 2.0, 3.0, 4.0])

    def test_load_numeric_text_attaches_readme_condition_for_grouped_numbers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            path = folder / "007.csv"
            path.write_text("0,1\n0.1,2\n", encoding="utf-8")
            (folder / "readme.txt").write_text("004:Z\n005:X\n006,007:Y\n", encoding="utf-8")

            dataset = load_analysis_path(path, fs_hint=100.0)

        self.assertEqual(dataset.condition_number, "007")
        self.assertEqual(dataset.condition_text, "Y")
        self.assertIn("006,007:Y", dataset.readme_text)
        self.assertEqual(dataset.readme_path.name, "readme.txt")

    def test_load_continuous_manifest_attaches_directory_or_parent_readme(self):
        session = default_session_config()
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            output_dir = parent / "rec_012"
            (parent / "readme.txt").write_text("012:父目录工况\n", encoding="utf-8")
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0"],
                software_version="test",
            )
            writer.start()
            writer.write_frame(
                BackendFrame(
                    sample_rate=2560.0,
                    channel_names=["ai0"],
                    data=np.array([[1.0, 2.0, 3.0]], dtype=float),
                    timestamps=np.array([0.0, 1.0 / 2560.0, 2.0 / 2560.0]),
                    frame_index=1,
                    metadata={},
                )
            )
            writer.close()

            dataset = load_analysis_path(output_dir / "manifest.json")

        self.assertEqual(dataset.condition_number, "012")
        self.assertEqual(dataset.condition_text, "父目录工况")
        self.assertEqual(dataset.readme_path.parent, parent)

    def test_load_legacy_vna_exposes_frequency_results(self):
        dataset = load_analysis_path(r"D:\SynologyDrive\codex\vna\dsa\vna\sample.vna")

        self.assertGreaterEqual(len(dataset.series), 4)
        self.assertIsNotNone(dataset.frequency_hz)
        self.assertGreater(np.asarray(dataset.frequency_hz).size, 0)
        self.assertTrue(dataset.autospectrum)
        self.assertTrue(dataset.frf)
        self.assertTrue(dataset.coherence)


class AnalysisViewerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_floor_response_transfer_dataset_plots_stiffness_and_coherence(self):
        frequency = np.array([0.0, 40.0, 80.0], dtype=float)
        dataset = AnalysisDataset(
            id=1,
            path=Path("Hxysv00014.txt"),
            name="Hxysv00014.txt",
            sample_rate=0.0,
            series=[
                AnalysisSeries(1, 0, "ai0", "I1", "N", 1.0),
                AnalysisSeries(1, 1, "ai1", "I2", "g", 10.0),
                AnalysisSeries(1, 2, "ai2", "I3", "g", 10.0),
                AnalysisSeries(1, 3, "ai3", "I4", "g", 10.0),
            ],
            frequency_hz=frequency,
            frf={"ai0->ai3": np.array([0.3, 0.6, 1.2], dtype=complex)},
            coherence={"ai0->ai3": np.array([0.7, 0.71, 0.72], dtype=float)},
        )
        viewer = AnalysisViewer()
        try:
            viewer.foundation_resp_edit.setText("4")
            viewer._plot_foundation_stiffness(viewer.foundation_plots[1], dataset)
            viewer._plot_foundation_coherence(viewer.foundation_plots[2], dataset)
            stiffness_curves = viewer._plot_curves[viewer.foundation_plots[1]]
            coherence_curves = viewer._plot_curves[viewer.foundation_plots[2]]
            self.assertIn("Z", stiffness_curves)
            self.assertIn("Z", coherence_curves)
        finally:
            viewer.close()

    def test_viewer_loads_text_path_and_plots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,1\n0.3,0\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                self.assertEqual(viewer.series_list.count(), 1)
                self.assertEqual(viewer.series_list.item(0).text(), "data.csv+ch1")
                self.assertFalse(viewer.series_list.item(0).isSelected())
                self.assertEqual(viewer._plot_curves[viewer.main_plots[0]], {})
                viewer.series_list.item(0).setSelected(True)
                self.assertGreater(len(viewer._plot_curves[viewer.main_plots[0]]), 0)
                viewer.main_plots[0].clear()
                viewer.plot_current()
                self.assertGreater(len(viewer._plot_curves[viewer.main_plots[0]]), 0)
            finally:
                viewer.close()

    def test_continuous_recording_psd_uses_fft_block_and_trans_from_time_data(self):
        session = default_session_config()
        session.acquisition.sample_rate = 1024.0
        session.acquisition.frame_size = 1024
        time_s = np.arange(4096, dtype=float) / session.acquisition.sample_rate
        reference = np.sin(2.0 * np.pi * 32.0 * time_s)
        response = 3.0 * reference
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "recording"
            writer = ContinuousDatWriter(
                output_dir,
                session,
                device_name="Dev1",
                channel_names=["ai0", "ai1"],
                software_version="test",
            )
            writer.start()
            for frame_index in range(4):
                start = frame_index * 1024
                stop = start + 1024
                writer.write_frame(
                    BackendFrame(
                        sample_rate=session.acquisition.sample_rate,
                        channel_names=["ai0", "ai1"],
                        data=np.vstack([reference[start:stop], response[start:stop]]),
                        timestamps=time_s[start:stop],
                        frame_index=frame_index + 1,
                        metadata={},
                    )
                )
            writer.close()

            viewer = AnalysisViewer()
            try:
                viewer._load_path(output_dir / "manifest.json")
                viewer.fs_hint_spin.setValue(512.0)
                dataset = viewer._datasets[0]
                ref_series = dataset.series[0]
                resp_series = dataset.series[1]

                psd_f, psd = viewer._psd_for_series(dataset, ref_series, scale=1.0)
                trans_curve = viewer._transfer_curve(dataset, resp_series)
                viewer.fs_hint_spin.setValue(1024.0)
                psd_f_large, _psd_large = viewer._psd_for_series(dataset, ref_series, scale=1.0)

                self.assertGreater(psd.size, 0)
                self.assertLessEqual(psd_f.size, 256)
                self.assertGreater(psd_f_large.size, psd_f.size)
                self.assertIsNotNone(trans_curve)
                f, trans_db, _label = trans_curve
                target_index = int(np.argmin(np.abs(f - 32.0)))
                self.assertAlmostEqual(trans_db[target_index], 20.0 * np.log10(3.0), delta=0.35)
            finally:
                viewer.close()

    def test_dataset_removal_releases_only_the_matching_time_series_caches(self):
        viewer = AnalysisViewer()
        try:
            viewer._datasets = [
                AnalysisDataset(id=1, path=Path("one.csv"), name="one.csv", sample_rate=1.0),
                AnalysisDataset(id=2, path=Path("two.csv"), name="two.csv", sample_rate=1.0),
            ]
            viewer._time_series_cache = {
                (1, "ai0", None, None, None): (np.array([0.0]), np.array([1.0])),
                (2, "ai0", None, None, None): (np.array([0.0]), np.array([2.0])),
            }
            viewer._bulk_time_series_cache = {
                (1, None, None, None): (np.array([0.0]), {"ai0": np.array([1.0])}),
                (2, None, None, None): (np.array([0.0]), {"ai0": np.array([2.0])}),
            }
            viewer._selected_channel_keys_by_dataset = {1: {"ai0"}, 2: {"ai0"}}
            viewer._derived_result_cache[("result",)] = (np.array([1.0]),)
            viewer._last_derived_results = [{"psd": np.array([1.0])}]

            viewer._delete_datasets_by_ids({1})

            self.assertEqual([dataset.id for dataset in viewer._datasets], [2])
            self.assertEqual({key[0] for key in viewer._time_series_cache}, {2})
            self.assertEqual({key[0] for key in viewer._bulk_time_series_cache}, {2})
            self.assertEqual(set(viewer._selected_channel_keys_by_dataset), {2})
            self.assertEqual(viewer._derived_result_cache, {})
            self.assertIsNone(viewer._last_derived_results)

            viewer._clear_datasets()

            self.assertEqual(viewer._time_series_cache, {})
            self.assertEqual(viewer._bulk_time_series_cache, {})
            self.assertEqual(viewer._selected_channel_keys_by_dataset, {})
        finally:
            viewer.close()

    def test_time_series_alignment_rejects_large_timestamp_gaps(self):
        regular = np.arange(100, dtype=float) / 1000.0
        timestamps = np.concatenate((regular, np.array([1000.0])))
        values = np.arange(timestamps.size, dtype=float)

        aligned = AnalysisViewer._align_time_series_pair(
            timestamps,
            values,
            1000.0,
            timestamps,
            values,
            1000.0,
        )

        self.assertIsNone(aligned)

    def test_two_selected_time_datasets_compute_response_over_input_transfer(self):
        sample_rate = 1024.0
        sample_count = 4096
        time_s = np.arange(sample_count, dtype=float) / sample_rate
        reference_values = np.random.default_rng(42).standard_normal(sample_count)
        response_values = 2.5 * reference_values
        reference_series = AnalysisSeries(
            dataset_id=1,
            channel_index=0,
            channel_key="input",
            display_name="Input",
        )
        response_series = AnalysisSeries(
            dataset_id=2,
            channel_index=0,
            channel_key="response",
            display_name="Response",
        )
        viewer = AnalysisViewer()
        try:
            viewer._datasets = [
                AnalysisDataset(
                    id=1,
                    path=Path("input.csv"),
                    name="input.csv",
                    sample_rate=sample_rate,
                    series=[reference_series],
                    time_s=time_s,
                    channels={"input": reference_values},
                ),
                AnalysisDataset(
                    id=2,
                    path=Path("response.csv"),
                    name="response.csv",
                    sample_rate=sample_rate,
                    series=[response_series],
                    time_s=time_s,
                    channels={"response": response_values},
                ),
            ]
            viewer._refresh_dataset_lists()
            viewer.series_list.blockSignals(True)
            try:
                viewer.series_list.item(0).setSelected(True)
                viewer.series_list.item(1).setSelected(True)
            finally:
                viewer.series_list.blockSignals(False)
            viewer.fs_hint_spin.setValue(512.0)

            viewer.plot_current()

            curves = viewer._plot_curves[viewer.main_plots[2]]
            self.assertEqual(len(curves), 1)
            label, (_frequency, transfer_db) = next(iter(curves.items()))
            self.assertIn("response.csv", label)
            self.assertIn("input.csv", label)
            self.assertAlmostEqual(
                float(np.nanmedian(transfer_db)),
                20.0 * np.log10(2.5),
                delta=0.05,
            )
            self.assertIn("input.csv", viewer.statusBar().currentMessage())
            self.assertIn("response.csv", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_two_selected_complete_vna_channels_keep_their_native_transfer_curves(self):
        sample_rate = 1024.0
        time_s = np.arange(2048, dtype=float) / sample_rate
        frequency = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        datasets: list[AnalysisDataset] = []
        for dataset_id, gain in ((1, 2.0), (2, 3.0)):
            reference = np.sin(2.0 * np.pi * 20.0 * time_s)
            response = gain * reference
            datasets.append(
                AnalysisDataset(
                    id=dataset_id,
                    path=Path(f"complete_{dataset_id}.vna"),
                    name=f"complete_{dataset_id}.vna",
                    sample_rate=sample_rate,
                    series=[
                        AnalysisSeries(
                            dataset_id=dataset_id,
                            channel_index=0,
                            channel_key="ai0",
                            display_name="Reference",
                        ),
                        AnalysisSeries(
                            dataset_id=dataset_id,
                            channel_index=1,
                            channel_key="ai1",
                            display_name="Response",
                        ),
                    ],
                    time_s=time_s,
                    channels={"ai0": reference, "ai1": response},
                    frequency_hz=frequency,
                    frf={"ai0->ai1": np.full(frequency.size, gain, dtype=complex)},
                )
            )
        viewer = AnalysisViewer()
        try:
            viewer._datasets = datasets
            viewer._refresh_dataset_lists()
            viewer.series_list.blockSignals(True)
            try:
                viewer.series_list.item(1).setSelected(True)
                viewer.series_list.item(3).setSelected(True)
            finally:
                viewer.series_list.blockSignals(False)

            viewer.plot_current()

            curves = viewer._plot_curves[viewer.main_plots[2]]
            self.assertEqual(len(curves), 2)
            median_levels = sorted(float(np.nanmedian(values)) for _frequency, values in curves.values())
            np.testing.assert_allclose(
                median_levels,
                sorted([20.0 * np.log10(2.0), 20.0 * np.log10(3.0)]),
                rtol=0.0,
                atol=1e-10,
            )
            self.assertIsNone(viewer._last_time_pair_transfer_description)
        finally:
            viewer.close()

    def test_vna_raw_aspec_psd_matches_main_display_density_scaling(self):
        session = default_session_config()
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {"ai0": np.array([1.0, 2.0], dtype=float)},
            },
            spectra={
                "f": np.array([0.0, 10.0, 20.0], dtype=float),
                "autospectrum": {"ai0": np.array([9.0, 18.0, 36.0], dtype=float)},
            },
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={
                "rbw_hz": 2.0,
                "legacy_runtime_wincor": 2.0 / 3.0,
                "legacy_channels": {"ai0": {"euscale_fac": 3.0, "eu_string": "m/s^2"}},
            },
        )
        dataset = dataset_from_measurement(measurement, session_config=session, name="current")
        viewer = AnalysisViewer()
        try:
            series = dataset.series[0]
            f, psd = viewer._psd_for_series(dataset, series, scale=float(series.scale))

            np.testing.assert_allclose(f, [10.0, 20.0])
            np.testing.assert_allclose(psd, [18.0 * (3.0**2) / 2.0, 36.0 * (3.0**2) / 2.0])
        finally:
            viewer.close()

    def test_foundation_vibration_uses_matlab_aspec_without_window_correction(self):
        session = default_session_config()
        freqs = np.arange(0.0, 101.0, 1.0)
        aspec = np.full_like(freqs, 4.0)
        measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {"ai0": np.array([1.0, 2.0], dtype=float)},
            },
            spectra={"f": freqs, "autospectrum": {"ai0": aspec}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={
                "rbw_hz": 1.0,
                "legacy_runtime_wincor": 0.25,
                "legacy_channels": {"ai0": {"euscale_fac": 2.0, "eu_string": "m/s^2"}},
            },
        )
        dataset = dataset_from_measurement(measurement, session_config=session, name="foundation")
        viewer = AnalysisViewer()
        try:
            centers, velocity = viewer._foundation_vibration_curve(dataset, dataset.series[0])
            expected_f = freqs[1:]
            expected_psd = aspec[1:] * (2.0**2) / 1.0
            expected_centers, expected_velocity = compute_third_octave_velocity_rms(
                expected_f,
                expected_psd,
                1.0,
            )

            np.testing.assert_allclose(centers, expected_centers)
            np.testing.assert_allclose(velocity, expected_velocity)
        finally:
            viewer.close()

    def test_floor_response_foundation_curve_matches_matlab_script_integration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "floor_ascii.txt"
            path.write_text(
                "Data written for 3 signals in a group signal\n"
                "Ref Chan:Resp Chan\t 0-Z:0-Z\t 0-Z:0-Z\t 0-Z:0-Z\n"
                "Channel Names\tI2\tI3\tI4\n"
                "Channel Comments\t\t\t\n"
                "\n"
                "   \t G2, 2\t G3, 3\t G4, 4\n"
                "Frequency\t Mag\tMag\tMag\n"
                "   \tEU\tEU\tEU\n"
                "\n"
                "Units:\n"
                "Hz\tg\tg\tg\n"
                "0.0000\t0.10\t0.20\t0.30\n"
                "1.0000\t0.20\t0.30\t0.40\n"
                "2.0000\t0.40\t0.50\t0.60\n"
                "4.0000\t0.80\t0.90\t1.00\n",
                encoding="utf-8",
            )
            dataset = load_analysis_path(path, fs_hint=100.0)
            viewer = AnalysisViewer()
            try:
                centers, velocity = viewer._foundation_vibration_curve(dataset, dataset.series[0])
                f = np.asarray(dataset.frequency_hz, dtype=float)[1:]
                aspec = np.asarray(dataset.autospectrum[dataset.series[0].channel_key], dtype=float)[1:]
                expected_centers, expected_velocity = compute_third_octave_velocity_rms(f, aspec, dataset.rbw_hz)

                np.testing.assert_allclose(centers, expected_centers)
                np.testing.assert_allclose(velocity, expected_velocity)

                viewer._load_path(path)
                self.assertEqual(viewer.foundation_vib_edit.text(), "1,2,3")
            finally:
                viewer.close()

    def test_same_channel_from_multiple_files_uses_distinct_curve_colors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "first.csv"
            second = Path(tmpdir) / "second.csv"
            first.write_text("0,1\n0.1,2\n0.2,1\n0.3,0\n", encoding="utf-8")
            second.write_text("0,2\n0.1,3\n0.2,2\n0.3,1\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(first)
                viewer._load_path(second)
                for index in range(viewer.series_list.count()):
                    viewer.series_list.item(index).setSelected(True)

                plot = viewer.main_plots[0]
                curve_items = [viewer._plot_item_for_label(plot, label) for label in viewer._plot_curves[plot]]
                curve_colors = [item.opts["pen"].color().name() for item in curve_items if item is not None]

                self.assertEqual(len(curve_colors), 2)
                self.assertEqual(len(set(curve_colors)), 2)
            finally:
                viewer.close()

    def test_viewer_loads_multiple_files_and_matches_matlab_series_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "first.csv"
            second = Path(tmpdir) / "second.csv"
            first.write_text("0,1,2\n0.1,2,3\n0.2,3,4\n", encoding="utf-8")
            second.write_text("0,5\n0.1,6\n0.2,7\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getOpenFileNames",
                    return_value=([str(first), str(second)], ""),
                ), mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QInputDialog.getItem",
                    return_value=("自动识别", True),
                ):
                    viewer._load_file()
                self.assertFalse(hasattr(viewer, "dataset_list"))
                labels = [viewer.series_list.item(index).text() for index in range(viewer.series_list.count())]
                self.assertEqual(labels, ["first.csv+ch1", "first.csv+ch2", "second.csv+ch1"])

                viewer._load_path(first)
                labels = [viewer.series_list.item(index).text() for index in range(viewer.series_list.count())]
                self.assertIn("first.csv+ch1#2", labels)
                self.assertIn("first.csv+ch2#2", labels)
            finally:
                viewer.close()

    def test_viewer_load_file_prompt_can_force_csv_as_psd(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "generic_frequency.csv"
            path.write_text(
                "Frequency,base\n"
                "1,0.25\n"
                "2,0.5\n"
                "4,1.0\n",
                encoding="utf-8",
            )
            viewer = AnalysisViewer()
            try:
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getOpenFileNames",
                    return_value=([str(path)], ""),
                ), mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QInputDialog.getItem",
                    return_value=("PSD数据", True),
                ):
                    viewer._load_file()

                self.assertEqual(len(viewer._datasets), 1)
                dataset = viewer._datasets[0]
                self.assertEqual(dataset.metadata["import_kind"], "psd")
                self.assertIn("base", dataset.autospectrum)
                np.testing.assert_allclose(dataset.frequency_hz, [1.0, 2.0, 4.0])
            finally:
                viewer.close()

    def test_viewer_readme_panel_follows_selected_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            first = folder / "006.csv"
            second = folder / "007.csv"
            first.write_text("0,1\n0.1,2\n", encoding="utf-8")
            second.write_text("0,3\n0.1,4\n", encoding="utf-8")
            (folder / "readme.txt").write_text("006:第一工况\n007:第二工况\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_paths([first, second])
                labels = [viewer.series_list.item(index).text() for index in range(viewer.series_list.count())]
                self.assertEqual(
                    labels,
                    [
                        "006.csv+ch1（第一工况）",
                        "006.csv+ch2（第一工况）",
                        "007.csv+ch1（第二工况）",
                        "007.csv+ch2（第二工况）",
                    ],
                )
                self.assertIn("007", viewer.show_readme_button.toolTip())
                self.assertIn("第二工况", viewer.show_readme_button.toolTip())

                viewer.series_list.item(0).setSelected(True)

                self.assertTrue(viewer.show_readme_button.isEnabled())
                self.assertIn("006", viewer.show_readme_button.toolTip())
                self.assertIn("第一工况", viewer.show_readme_button.toolTip())
                self.assertIn("第一工况", viewer.series_list.item(0).toolTip())
                self.assertEqual(viewer.rename_edit.text(), "第一工况")

                self.assertTrue(viewer.readme_panel.isHidden())
                central_layout = viewer.left_panel.parentWidget().layout()
                self.assertIs(central_layout.itemAt(0).widget(), viewer.readme_panel)
                viewer.show_readme_button.click()
                self.assertFalse(viewer.readme_panel.isHidden())
                self.assertIn("006", viewer.readme_summary_label.text())
                self.assertIn("006:第一工况", viewer.readme_panel_preview.toPlainText())
                viewer.show_readme_button.click()
                self.assertTrue(viewer.readme_panel.isHidden())
            finally:
                viewer.close()

    def test_viewer_readme_panel_handles_missing_or_unmatched_readme(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            path = folder / "009.csv"
            path.write_text("0,1\n0.1,2\n", encoding="utf-8")
            (folder / "readme.txt").write_text("001:其他工况\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)

                self.assertEqual(viewer.series_list.item(0).text(), "009.csv+ch1")
                self.assertTrue(viewer.show_readme_button.isEnabled())
                self.assertIn("009", viewer.show_readme_button.toolTip())
                self.assertIn("未匹配", viewer.show_readme_button.toolTip())
                self.assertIn("001:其他工况", viewer._readme_dialog_text(viewer._dataset_for_readme_panel()))
                viewer.show_readme_button.click()
                self.assertIn("001:其他工况", viewer.readme_panel_preview.toPlainText())
            finally:
                viewer.close()

    def test_load_folder_skips_bad_supported_files_without_modal_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            good = folder / "good.csv"
            bad = folder / "bad.dat"
            good.write_text("0,1,2\n0.1,3,4\n0.2,5,6\n", encoding="utf-8")
            bad.write_bytes(b"\xaf not a python vna dat")
            viewer = AnalysisViewer()
            try:
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getExistingDirectory",
                    return_value=str(folder),
                ), mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QMessageBox.warning"
                ) as warning:
                    viewer._load_folder()

                warning.assert_not_called()
                self.assertEqual(viewer.series_list.count(), 2)
                self.assertIn("失败 1", viewer.statusBar().currentMessage())
            finally:
                viewer.close()

    def test_load_folder_remembers_directory_even_when_no_supported_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            viewer = AnalysisViewer()
            try:
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getExistingDirectory",
                    return_value=str(folder),
                ), mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QMessageBox.warning"
                ):
                    viewer._load_folder()

                self.assertEqual(viewer._last_directory, folder)
            finally:
                viewer.close()

    def test_viewer_uses_vna_axis_items_context_menu_and_data_tips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,1\n0.3,0\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                plot = viewer.main_plots[0]
                self.assertIsInstance(plot.getAxis("bottom"), VnaAxisItem)
                menu, actions = viewer._build_plot_context_menu(plot)
                try:
                    action_texts = [
                        action.text()
                        for action in menu.actions()
                        if not action.isSeparator()
                    ]
                    self.assertEqual(
                        action_texts,
                        [
                            "返回上一缩放",
                            "自动缩放",
                            "数据提示",
                            "读数游标",
                            "清除数据提示",
                            "删除当前曲线",
                            "管理当前图窗曲线",
                            "复制图像",
                        ],
                    )
                    self.assertTrue(actions["data_tip"].isCheckable())
                    self.assertTrue(actions["cursor"].isCheckable())
                    self.assertTrue(actions["manage_curves"].isEnabled())
                finally:
                    menu.close()
                self.assertFalse(plot.getPlotItem().menuEnabled())
                self.assertFalse(plot.getPlotItem().vb.menuEnabled())
                self.assertGreaterEqual(viewer.series_list.minimumHeight(), 80)
                self.assertLessEqual(viewer.series_list.minimumHeight(), 120)
                self.assertFalse(hasattr(viewer, "cursor_button"))
                self.assertFalse(hasattr(viewer, "data_tip_button"))

                viewer._toggle_data_tip_mode(True)
                placed = viewer._place_data_tip(plot, 0.1, 2.0)
                self.assertTrue(placed)
                self.assertEqual(len(viewer._data_tip_items[plot]), 1)
                data_tip = viewer._data_tip_items[plot][0]
                self.assertIsInstance(data_tip["text"], DataTipText)
                self.assertIn("X 0.1", data_tip["text"].toPlainText())

                moved = viewer._drag_data_tip_to_scene_pos(
                    plot,
                    data_tip,
                    plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.2, 1.0)),
                )
                self.assertTrue(moved)
                self.assertAlmostEqual(data_tip["x"], 0.2, places=6)
                label_moved = viewer._drag_data_tip_label_to_scene_pos(
                    plot,
                    data_tip,
                    plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.0, 3.0)),
                )
                self.assertTrue(label_moved)
                self.assertTrue(data_tip["label_anchor_manual"])
                self.assertEqual(data_tip["label_anchor"], (1.05, 1.05))
                self.assertAlmostEqual(data_tip["x"], 0.2, places=6)

                viewer._toggle_data_tip_mode(False)
                cursor_moved = viewer._move_cursor_from_scene_pos(
                    plot,
                    plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.1, 2.0)),
                )
                self.assertTrue(cursor_moved)
                self.assertIsNotNone(viewer._cursor_positions[plot])
                self.assertTrue(viewer._cursor_items[plot]["text"].isVisible())
                self.assertIn("X 0.1", viewer._cursor_items[plot]["text"].toPlainText())
            finally:
                viewer.close()

    def test_data_tip_context_menu_suppresses_plot_context_menu(self):
        viewer = AnalysisViewer()
        try:
            viewer._suppress_plot_context_menu_once()
            self.assertTrue(viewer._suppress_next_plot_context_menu)
        finally:
            viewer.close()

    def test_plot_curve_manager_removes_data_curves_but_protects_reference_lines(self):
        viewer = AnalysisViewer()
        try:
            frequency = np.array([1.0, 2.0, 4.0], dtype=float)
            values = np.array([1.0, 3.0, 2.0], dtype=float)
            plot = viewer.main_plots[0]
            plot.plot(frequency, values, name="测点数据")
            viewer._plot_curves[plot]["测点数据"] = (frequency, values)
            viewer._active_trace[plot] = "测点数据"
            viewer._toggle_data_tip_mode(True)
            self.assertTrue(viewer._place_data_tip(plot, 2.0, 3.0))

            self.assertEqual(viewer._remove_plot_curves(plot, {"测点数据"}), 1)
            self.assertNotIn("测点数据", viewer._plot_curves[plot])
            self.assertEqual(viewer._data_tip_items[plot], [])
            self.assertIsNone(viewer._active_trace[plot])

            plot.plot(frequency, values, name="VC A")
            viewer._plot_curves[plot]["VC A"] = (frequency, values)
            viewer._plot_export_excluded[plot].add("VC A")
            viewer._active_trace[plot] = "VC A"
            self.assertFalse(viewer._curve_info_for(plot, "VC A").removable)
            self.assertFalse(viewer._curve_info_for(plot, "VC A").exportable)
            self.assertEqual(viewer._remove_plot_curves(plot, {"VC A"}), 0)
            self.assertIn("VC A", viewer._plot_curves[plot])
        finally:
            viewer.close()

    def test_analysis_context_menu_selection_style_is_visible(self):
        viewer = AnalysisViewer()
        try:
            self.assertIn("QMenu::item:selected", viewer.styleSheet())
            self.assertIn("background:", viewer.styleSheet())
            self.assertIn("QPushButton:pressed", viewer.styleSheet())
            self.assertIn("QPushButton:checked", viewer.styleSheet())
        finally:
            viewer.close()

    def test_analysis_series_list_keeps_dark_theme_row_colors(self):
        viewer = AnalysisViewer(theme=_dark_analysis_theme())
        try:
            stylesheet = viewer.styleSheet()
            self.assertIn("alternate-background-color: #22304a", stylesheet)
            self.assertIn("QListWidget::item:alternate", stylesheet)
            palette = viewer.series_list.palette()
            self.assertEqual(palette.color(QtGui.QPalette.Base).name(), "#0f172a")
            self.assertEqual(palette.color(QtGui.QPalette.AlternateBase).name(), "#22304a")
            self.assertEqual(palette.color(QtGui.QPalette.Text).name(), "#f5f7fb")
            self.assertEqual(palette.color(QtGui.QPalette.Highlight).name(), "#2563eb")
        finally:
            viewer.close()

    def test_analysis_legend_text_updates_for_dark_theme(self):
        viewer = AnalysisViewer(theme=_dark_analysis_theme())
        try:
            plot = viewer.main_plots[0]
            plot.addLegend()
            plot.plot([1.0, 2.0], [2.0, 3.0], name="trace")

            viewer._apply_plot_theme(plot)

            label = plot.plotItem.legend.items[0][1]
            self.assertEqual(plot.plotItem.legend.opts["labelTextColor"], "#f5f7fb")
            self.assertIn("color:#f5f7fb", label.item.toHtml())
        finally:
            viewer.close()

    def test_legend_auto_moves_away_from_dense_corner(self):
        viewer = AnalysisViewer()
        try:
            plot = viewer.main_plots[0]
            plot.clear()
            if plot.plotItem.legend is None:
                plot.addLegend()
            else:
                plot.plotItem.legend.clear()
            x = np.linspace(0.0, 10.0, 200)
            y = 10.0 - x
            plot.plot(x, y, name="top-left heavy trace")
            viewer._plot_curves[plot] = {"top-left heavy trace": (x, y)}
            viewer._log_modes[plot] = (False, False)

            viewer._auto_range_plot(plot, [x], [y], log_x=False, log_y=False)

            self.assertNotEqual(plot.plotItem.legend._vna_auto_corner, "top_left")
        finally:
            viewer.close()

    def test_cursor_palette_is_readable_on_light_theme(self):
        viewer = AnalysisViewer(theme={"plot_bg": "#ffffff"})
        try:
            palette = viewer._cursor_palette()
            self.assertEqual(palette["line"], "#0f4c81")
            self.assertNotEqual(palette["line"], "#fff176")
        finally:
            viewer.close()

    def test_single_analysis_plot_window_follows_theme(self):
        viewer = AnalysisViewer(theme={"plot_bg": "#ffffff", "window_bg": "#f4f7fb"})
        try:
            plot = viewer.main_plots[0]
            viewer._plot_curves[plot] = {"trace": (np.array([1.0, 2.0]), np.array([3.0, 4.0]))}
            viewer._open_plot_window_for_plot(plot)
            self.assertEqual(len(viewer._single_plot_windows), 1)
            dialog = viewer._single_plot_windows[0]
            detached_plot = dialog.findChildren(type(plot))[0]
            self.assertTrue(dialog.windowFlags() & QtCore.Qt.WindowMaximizeButtonHint)
            self.assertIn("background: #f4f7fb;", dialog.styleSheet())
            self.assertEqual(detached_plot.backgroundBrush().color().name(), "#ffffff")

            viewer.apply_theme({"plot_bg": "#000000", "window_bg": "#111827"})

            self.assertIn("background: #111827;", dialog.styleSheet())
            self.assertEqual(detached_plot.backgroundBrush().color().name(), "#000000")
        finally:
            for dialog in list(viewer._single_plot_windows):
                dialog.close()
            viewer.close()

    def test_export_uses_active_plot_curves_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = Path(tmpdir) / "active.csv"
            viewer = AnalysisViewer()
            try:
                active = viewer.main_plots[1]
                viewer._active_plot = active
                viewer._plot_curves[viewer.main_plots[0]] = {
                    "top": (np.array([1.0]), np.array([2.0])),
                }
                viewer._plot_curves[active] = {
                    "active trace": (np.array([3.0, 4.0]), np.array([5.0, 6.0])),
                }
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getSaveFileName",
                    return_value=(str(destination), ""),
                ):
                    viewer._export_current_csv()
                raw = destination.read_bytes()
                self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
                text = raw.decode("utf-8-sig")
                self.assertIn("active_trace_x,active_trace_y", text)
                self.assertIn("3,5", text)
                self.assertNotIn("top_x", text)
                self.assertEqual(viewer._last_directory, destination.parent)
            finally:
                viewer.close()

    def test_exported_psd_reimports_without_legacy_scaling(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = Path(tmpdir) / "psd.csv"
            viewer = AnalysisViewer()
            try:
                plot = viewer.main_plots[0]
                viewer._active_plot = plot
                viewer._plot_curves[plot] = {
                    "PSD trace": (np.array([1.0, 2.0, 4.0]), np.array([0.25, 0.5, 1.0])),
                }
                viewer._plot_curve_kind[plot] = "psd"
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getSaveFileName",
                    return_value=(str(destination), ""),
                ):
                    viewer._export_current_csv()

                dataset = load_analysis_path(destination, fs_hint=100.0)
                series = dataset.series[0]
                frequency, psd = viewer._psd_for_series(dataset, series, scale=1.0)

                self.assertEqual(dataset.metadata["autospectrum_kind"], "psd")
                np.testing.assert_allclose(frequency, [1.0, 2.0, 4.0])
                np.testing.assert_allclose(psd, [0.25, 0.5, 1.0])
            finally:
                viewer.close()

    def test_exported_transfer_reimports_as_transfer_option(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = Path(tmpdir) / "transfer.csv"
            viewer = AnalysisViewer()
            try:
                plot = viewer.main_plots[0]
                viewer._active_plot = plot
                viewer._plot_curves[plot] = {
                    "Top response": (
                        np.array([1.0, 2.0, 4.0]),
                        np.array([0.0, 6.020599913279624, -6.020599913279624]),
                    ),
                }
                viewer._plot_curve_kind[plot] = "transfer"
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getSaveFileName",
                    return_value=(str(destination), ""),
                ):
                    viewer._export_current_csv()

                dataset = load_analysis_path(destination, fs_hint=100.0)
                viewer._datasets = [dataset]
                options = viewer._derived_transfer_options()

                self.assertIn("Input->Top response", dataset.frf)
                self.assertTrue(any("Input->Top response" in label for label, _data in options))
            finally:
                viewer.close()

    def test_interpolation_button_resamples_frequency_plot_curves(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            plot.addLegend(offset=(4, 2))
            plot.setLogMode(x=True, y=True)
            viewer._log_modes[plot] = (True, True)
            plot.plot(
                np.array([0.15, 0.25, 0.35, 0.45], dtype=float),
                np.array([1.0, 2.0, 4.0, 8.0], dtype=float),
                name="PSD曲线",
            )
            plot.plot(
                np.array([0.15, 0.45], dtype=float),
                np.array([2.0, 2.0], dtype=float),
                name="VC A",
            )
            viewer._plot_curves[plot] = {
                "PSD曲线": (
                    np.array([0.15, 0.25, 0.35, 0.45], dtype=float),
                    np.array([1.0, 2.0, 4.0, 8.0], dtype=float),
                ),
                "VC A": (np.array([0.15, 0.45], dtype=float), np.array([2.0, 2.0], dtype=float)),
            }
            viewer._plot_export_excluded[plot] = {"VC A"}

            with mock.patch(
                "python_vna.ui.analysis_viewer.QtWidgets.QInputDialog.getDouble",
                return_value=(0.1, True),
            ) as get_double:
                viewer.derived_interpolate_buttons[1].click()

            self.assertAlmostEqual(float(get_double.call_args.args[3]), 0.1)
            x_values, y_values = viewer._plot_curves[plot]["PSD曲线"]
            np.testing.assert_allclose(x_values, np.array([0.2, 0.3, 0.4]))
            self.assertGreater(float(y_values[1]), 1.0)
            self.assertLess(float(y_values[1]), 8.0)
            np.testing.assert_allclose(viewer._plot_curves[plot]["VC A"][0], np.array([0.15, 0.45]))
            self.assertIn("已按 0.1 Hz 插值 1 条曲线", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_interpolation_button_resamples_time_plot_curves_by_seconds(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            plot.addLegend(offset=(4, 2))
            plot.setLogMode(x=False, y=False)
            plot.setLabel("bottom", "Time (s)")
            viewer._log_modes[plot] = (False, False)
            plot.plot(
                np.array([0.0, 0.5, 1.0], dtype=float),
                np.array([0.0, 1.0, 0.0], dtype=float),
                name="时域曲线",
            )
            viewer._plot_curves[plot] = {
                "时域曲线": (
                    np.array([0.0, 0.5, 1.0], dtype=float),
                    np.array([0.0, 1.0, 0.0], dtype=float),
                )
            }

            with mock.patch.object(
                viewer,
                "_show_time_interpolation_dialog",
                return_value=(0.25, None, None),
            ) as get_settings:
                viewer.derived_interpolate_buttons[1].click()

            self.assertIs(get_settings.call_args.args[0], plot)
            x_values, y_values = viewer._plot_curves[plot]["时域曲线"]
            np.testing.assert_allclose(x_values, np.array([0.0, 0.25, 0.5, 0.75, 1.0]))
            np.testing.assert_allclose(y_values, np.array([0.0, 0.5, 1.0, 0.5, 0.0]))
            self.assertIn("已按 0.25 s 插值 1 条曲线", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_main_interpolation_button_resamples_time_plot_curves(self):
        viewer = AnalysisViewer()
        try:
            plot = viewer.main_plots[0]
            plot.addLegend(offset=(4, 2))
            plot.setLogMode(x=False, y=False)
            plot.setLabel("bottom", "Samples")
            viewer._log_modes[plot] = (False, False)
            viewer._plot_curve_kind[plot] = "time"
            plot.plot(
                np.array([0.0, 0.5, 1.0], dtype=float),
                np.array([0.0, 1.0, 0.0], dtype=float),
                name="主图时域曲线",
            )
            viewer._plot_curves[plot] = {
                "主图时域曲线": (
                    np.array([0.0, 0.5, 1.0], dtype=float),
                    np.array([0.0, 1.0, 0.0], dtype=float),
                )
            }

            with mock.patch.object(
                viewer,
                "_show_time_interpolation_dialog",
                return_value=(0.25, None, None),
            ) as get_settings:
                viewer.main_interpolate_buttons[0].click()

            self.assertIs(get_settings.call_args.args[0], plot)
            x_values, y_values = viewer._plot_curves[plot]["主图时域曲线"]
            np.testing.assert_allclose(x_values, np.array([0.0, 0.25, 0.5, 0.75, 1.0]))
            np.testing.assert_allclose(y_values, np.array([0.0, 0.5, 1.0, 0.5, 0.0]))
        finally:
            viewer.close()

    def test_time_interpolation_can_use_duration_and_point_count(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            plot.addLegend(offset=(4, 2))
            plot.setLogMode(x=False, y=False)
            plot.setLabel("bottom", "Time (s)")
            viewer._log_modes[plot] = (False, False)
            plot.plot(
                np.array([0.0, 1.0, 2.0], dtype=float),
                np.array([0.0, 2.0, 0.0], dtype=float),
                name="时域曲线",
            )
            viewer._plot_curves[plot] = {
                "时域曲线": (
                    np.array([0.0, 1.0, 2.0], dtype=float),
                    np.array([0.0, 2.0, 0.0], dtype=float),
                )
            }

            viewer._interpolate_plot_curves(
                plot,
                0.5,
                axis_kind="time",
                duration_s=2.0,
                point_count=5,
            )

            x_values, y_values = viewer._plot_curves[plot]["时域曲线"]
            np.testing.assert_allclose(x_values, np.array([0.0, 0.5, 1.0, 1.5, 2.0]))
            np.testing.assert_allclose(y_values, np.array([0.0, 1.0, 2.0, 1.0, 0.0]))
            self.assertIn("已按 5 点插值 1 条曲线", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_psd_synthesized_time_interpolation_regenerates_target_duration_and_points(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            viewer._plot_derived_result_axis(
                plot,
                "近似时域",
                [
                    {
                        "label": "PSD源",
                        "psd": (
                            np.array([5.0, 10.0, 20.0, 40.0], dtype=float),
                            np.array([0.5, 1.0, 0.8, 0.2], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )
            label = next(iter(viewer._plot_curves[plot]))
            self.assertIn(label, viewer._time_curve_psd_sources[plot])

            viewer._interpolate_plot_curves(
                plot,
                0.01,
                axis_kind="time",
                duration_s=2.0,
                point_count=201,
            )

            time_s, values = viewer._plot_curves[plot][label]
            self.assertEqual(time_s.size, 201)
            self.assertEqual(values.size, 201)
            self.assertAlmostEqual(float(time_s[0]), 0.0)
            self.assertAlmostEqual(float(time_s[-1]), 2.0)
            self.assertGreater(float(np.std(values)), 0.0)
        finally:
            viewer.close()

    def test_current_psd_result_plot_can_switch_to_approximate_time(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            viewer._plot_derived_result_axis(
                plot,
                "PSD",
                [
                    {
                        "label": "工作区PSD",
                        "psd": (
                            np.array([10.0, 20.0, 30.0, 40.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.8], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )

            viewer.derived_result_mode_combo.setCurrentText("近似时域")

            self.assertEqual(viewer._plot_curve_kind[plot], "time")
            time_curves = viewer._plot_curves[plot]
            self.assertTrue(any("PSD合成" in label for label in time_curves))
            _label, (time_s, values) = next(iter(time_curves.items()))
            self.assertGreaterEqual(time_s.size, 32)
            self.assertEqual(time_s.size, values.size)
            self.assertGreater(float(np.std(values)), 0.0)
        finally:
            viewer.close()

    def test_approximate_time_axis_synthesizes_when_result_only_has_psd(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            viewer._plot_derived_result_axis(
                plot,
                "近似时域",
                [
                    {
                        "label": "仅PSD",
                        "psd": (
                            np.array([5.0, 10.0, 20.0, 40.0], dtype=float),
                            np.array([0.5, 1.0, 0.8, 0.2], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )

            curves = viewer._plot_curves[plot]
            self.assertEqual(len(curves), 1)
            label, (time_s, values) = next(iter(curves.items()))
            self.assertIn("PSD合成", label)
            self.assertGreaterEqual(time_s.size, 32)
            self.assertEqual(time_s.size, values.size)
        finally:
            viewer.close()

    def test_sync_current_measurement_adds_channels(self):
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {
                    "ai0": np.array([1.0, 2.0], dtype=float),
                    "ai1": np.array([3.0, 4.0], dtype=float),
                },
            },
            spectra={"f": np.array([0.0, 10.0]), "autospectrum": {}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={},
        )
        viewer = AnalysisViewer()
        try:
            synced = viewer.sync_current_measurement(measurement, default_session_config())
            self.assertTrue(synced)
            self.assertEqual(viewer.series_list.count(), 2)
            labels = [viewer.series_list.item(index).text() for index in range(viewer.series_list.count())]
            self.assertEqual(labels, ["Current Measurement+ch1", "Current Measurement+ch2"])
        finally:
            viewer.close()

    def test_sync_current_measurement_filters_disabled_channels(self):
        session = default_session_config()
        session.ai_channels[2].enabled = False
        session.ai_channels[3].enabled = False
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {
                    "ai0": np.array([1.0, 2.0], dtype=float),
                    "ai1": np.array([3.0, 4.0], dtype=float),
                    "ai2": np.array([5.0, 6.0], dtype=float),
                    "ai3": np.array([7.0, 8.0], dtype=float),
                },
            },
            spectra={
                "f": np.array([0.0, 10.0]),
                "autospectrum": {
                    "ai0": np.array([1.0, 2.0]),
                    "ai1": np.array([3.0, 4.0]),
                    "ai2": np.array([5.0, 6.0]),
                    "ai3": np.array([7.0, 8.0]),
                },
            },
            frf={
                "ai0->ai1": np.array([1.0 + 0.0j]),
                "ai0->ai2": np.array([2.0 + 0.0j]),
            },
            coherence={
                "ai0->ai1": np.array([1.0]),
                "ai0->ai2": np.array([0.5]),
            },
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={},
        )
        viewer = AnalysisViewer()
        try:
            viewer.sync_current_measurement(measurement, session)
            dataset = viewer._datasets[0]

            self.assertEqual(dataset.channel_keys, ["ai0", "ai1"])
            self.assertEqual(set(dataset.autospectrum), {"ai0", "ai1"})
            self.assertEqual(set(dataset.frf), {"ai0->ai1"})
            self.assertEqual(viewer.series_list.count(), 2)
        finally:
            viewer.close()

    def test_dataset_from_measurement_filters_disabled_label_keys(self):
        session = default_session_config()
        session.ai_channels[2].label = "disabled accel"
        session.ai_channels[2].enabled = False
        measurement = MeasurementSet(
            sample_rate=100.0,
            time_data={
                "t": np.array([0.0, 0.1], dtype=float),
                "channels": {
                    "Ch 1": np.array([1.0, 2.0], dtype=float),
                    "Ch 2": np.array([3.0, 4.0], dtype=float),
                    "disabled accel": np.array([5.0, 6.0], dtype=float),
                },
            },
            spectra={"f": np.array([0.0, 10.0]), "autospectrum": {}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={},
        )

        dataset = dataset_from_measurement(measurement, session_config=session)

        self.assertEqual(dataset.channel_keys, ["Ch 1", "Ch 2"])

    def test_cursor_drag_callback_updates_the_target_analysis_plot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            rows = [f"{index / 10.0},{np.sin(index / 10.0):.12g}" for index in range(40)]
            path.write_text("\n".join(rows), encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                viewer.main_mode_combos[1].setCurrentText("Time")
                viewer.plot_current()
                top_plot = viewer.main_plots[0]
                target_plot = viewer.main_plots[1]
                scene_pos = target_plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(1.0, np.sin(1.0)))

                moved = target_plot.getPlotItem().vb._on_left_drag(scene_pos)

                self.assertTrue(moved)
                self.assertIsNone(viewer._cursor_positions[top_plot])
                self.assertIsNotNone(viewer._cursor_positions[target_plot])
                self.assertTrue(viewer._cursor_items[target_plot]["text"].isVisible())
            finally:
                viewer.close()

    def test_foundation_vc_checkboxes_are_visually_distinct(self):
        viewer = AnalysisViewer()
        try:
            self.assertEqual(viewer.vc_a_check.objectName(), "vcCheck")
            self.assertFalse(viewer.vc_a_check.isChecked())
            self.assertTrue(viewer.vc_b_check.isChecked())
            self.assertTrue(viewer.vc_c_check.isChecked())
            self.assertFalse(viewer.vc_d_check.isChecked())
            self.assertFalse(viewer.vc_e_check.isChecked())
            self.assertFalse(viewer.vc_f_check.isChecked())
            self.assertIn("QCheckBox#vcCheck::indicator", viewer.styleSheet())
        finally:
            viewer.close()

    def test_foundation_file_selectors_follow_loaded_datasets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = Path(tmpdir) / "vib_measurement_with_a_long_file_name.csv"
            second = Path(tmpdir) / "stiff_measurement_with_a_long_file_name.csv"
            first.write_text("0,1\n0.1,2\n0.2,3\n", encoding="utf-8")
            second.write_text("0,4\n0.1,5\n0.2,6\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_paths([first, second])
                self.assertEqual(viewer.foundation_vib_file_combo.count(), 2)
                self.assertEqual(viewer.foundation_stiff_file_combo.count(), 2)
                self.assertEqual(viewer.foundation_vib_file_combo.currentData(), 1)
                self.assertEqual(viewer.foundation_stiff_file_combo.currentData(), 2)
                self.assertFalse(hasattr(viewer, "foundation_ref_edit"))
                self.assertEqual(viewer.foundation_resp_edit.text(), "4")
                viewer.foundation_resp_edit.setText("2,3")
                viewer.foundation_stiff_file_combo.setCurrentIndex(0)
                self.assertEqual(viewer.foundation_resp_edit.text(), "2,3")

                self.assertGreaterEqual(viewer.foundation_vib_file_combo.minimumWidth(), 140)
                self.assertLessEqual(viewer.foundation_vib_file_combo.maximumWidth(), 260)
                self.assertIn("vib_measurement_with_a_long_file_name", viewer.foundation_vib_file_combo.toolTip())
                self.assertIn(
                    "stiff_measurement_with_a_long_file_name",
                    viewer.foundation_stiff_file_combo.itemData(1, QtCore.Qt.ToolTipRole),
                )
                self.assertGreaterEqual(viewer.foundation_vib_edit.minimumWidth(), 64)
            finally:
                viewer.close()

    def test_analysis_defaults_and_labels_match_requested_layout(self):
        viewer = AnalysisViewer()
        try:
            self.assertEqual(viewer.main_mode_combos[2].currentText(), "Trans")
            self.assertEqual(viewer.export_button.text(), "导出数据")
            self.assertEqual(viewer.hold_button.text(), "保持:关")
            foundation_labels = [
                label.text()
                for label in viewer.foundation_tab.findChildren(QtWidgets.QLabel)
            ]
            self.assertIn("振动文件", foundation_labels)
            self.assertIn("动刚度文件", foundation_labels)
            self.assertEqual(viewer._foundation_reference_channel(), 1)
            self.assertEqual(viewer.tabs.tabText(0), "主界面")
            self.assertEqual(viewer.tabs.tabText(1), "地面振动")
            self.assertEqual(viewer.tabs.tabText(2), "换算")
            self.assertEqual(len(viewer.main_open_buttons), 3)
            self.assertEqual(len(viewer.main_interpolate_buttons), 3)
            self.assertEqual(viewer.main_interpolate_buttons[0].text(), "插值")
            self.assertEqual(len(viewer.derived_interpolate_buttons), 2)
            self.assertEqual(viewer.derived_interpolate_buttons[0].text(), "插值")
            self.assertEqual(len(viewer.foundation_export_buttons), 3)
            self.assertEqual(len(viewer.derived_export_buttons), 2)
            self.assertLessEqual(viewer.width(), 980)
            self.assertLessEqual(viewer.left_panel.maximumWidth(), 300)
            foundation_controls = viewer.foundation_tab.layout().itemAt(0).layout()
            self.assertEqual(foundation_controls.count(), 2)
            self.assertEqual(viewer.derived_result_mode_combo.currentText(), "PSD")
            self.assertEqual(viewer.derived_direction_combo.currentData(), DERIVE_BASE_TO_TOP)
            self.assertAlmostEqual(viewer.derived_transfer_factor_spin.value(), 1.0)
            self.assertAlmostEqual(viewer.derived_input_factor_spin.value(), 1.0)
            self.assertEqual(viewer.derived_transfer_factor_spin.decimals(), 1)
            self.assertEqual(viewer.derived_input_factor_spin.decimals(), 1)
            self.assertFalse(any(checkbox.isChecked() for checkbox in viewer.derived_vc_checks.values()))
            self.assertFalse(viewer.derived_show_source_check.isChecked())
            self.assertTrue(viewer.derived_coherence_correction_check.isChecked())
        finally:
            viewer.close()

    def test_standalone_conversion_viewer_uses_conversion_only_layout(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            self.assertEqual(viewer.windowTitle(), "VNA 换算工具")
            self.assertIsNone(viewer.tabs)
            self.assertFalse(hasattr(viewer, "main_plots"))
            self.assertFalse(hasattr(viewer, "foundation_plots"))
            self.assertFalse(hasattr(viewer, "derived_config_button"))
            self.assertEqual(viewer.derived_plot_button.text(), "计算 / 更新结果")
            self.assertIsNone(viewer.clear_button.parentWidget())
            self.assertIsNotNone(viewer.derived_manage_data_button.parentWidget())
            self.assertFalse(hasattr(viewer, "derived_curve_button"))
            self.assertFalse(hasattr(viewer, "derived_main_plot_button"))
            self.assertIsNone(viewer.derived_config_dialog)
            self.assertIsNone(viewer.derived_curve_dialog)
            self.assertEqual(viewer.left_layout.itemAt(0).widget().title(), "1. 数据")
            self.assertEqual(viewer.left_layout.itemAt(1).widget().title(), "2. 当前选择")
            self.assertEqual(viewer.left_layout.itemAt(2).widget().title(), "3. 工作区曲线")
            self.assertEqual(viewer.left_layout.itemAt(3).widget().title(), "4. 批量与配方")
            self.assertEqual(viewer.derived_batch_calculate_button.text(), "全部计算")
            self.assertEqual(viewer.derived_batch_export_button.text(), "全部导出")
            self.assertEqual(viewer.derived_batch_cancel_button.text(), "取消任务")
            self.assertEqual(viewer.derived_curve_group.title(), "曲线编辑")
            self.assertIsNone(viewer.derived_stitch_enabled_check.parentWidget())
            self.assertFalse(hasattr(viewer, "left_scroll"))
            self.assertFalse(hasattr(viewer, "derived_config_dataset_list"))
            self.assertFalse(viewer._hidden_series_group.isVisible())
            self.assertIsNone(viewer.fs_hint_spin.parentWidget())
            self.assertEqual(viewer.derived_transfer_point_table.maximumHeight(), 260)
            viewer._show_derived_config_dialog()
            self.assertFalse(viewer.derived_settings_stack.isHidden())
            self.assertEqual(viewer.derived_settings_stack.count(), 4)
            viewer._show_settings_panel(0)
            self.assertFalse(viewer.derived_settings_stack.isHidden())
            self.assertEqual(viewer.derived_settings_stack.currentIndex(), 0)
            viewer._show_settings_panel(1)
            self.assertFalse(viewer.derived_settings_stack.isHidden())
            self.assertEqual(viewer.derived_settings_stack.currentIndex(), 1)
            viewer._show_settings_panel(2)
            self.assertFalse(viewer.derived_settings_stack.isHidden())
            self.assertEqual(viewer.derived_settings_stack.currentIndex(), 2)
            viewer._show_settings_panel(2)
            self.assertFalse(viewer.derived_settings_stack.isHidden())
            self.assertGreaterEqual(
                viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",)),
                0,
            )
            self.assertGreaterEqual(
                viewer._combo_index_for_data(viewer.derived_input_series_combo, ("vc_reference", "VC C")),
                0,
            )
        finally:
            viewer.close()

    def test_conversion_curve_editor_fits_small_window_and_scrolls_left_controls(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer.resize(960, 600)
            viewer.show()
            viewer.derived_right_toolbox.setCurrentIndex(1)
            QtWidgets.QApplication.processEvents()
            QtWidgets.QApplication.processEvents()
            self.assertLessEqual(viewer.height(), 620)
            self.assertTrue(hasattr(viewer, "left_panel_scroll"))
            self.assertGreater(viewer.left_panel_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(viewer.left_panel_scroll.horizontalScrollBar().maximum(), 0)
            self.assertLessEqual(
                viewer.left_panel.width(),
                viewer.left_panel_scroll.viewport().width(),
            )
            self.assertLessEqual(viewer.derived_right_toolbox.width(), 390)
            self.assertGreaterEqual(viewer.derived_right_toolbox.width(), 300)
            self.assertTrue(viewer.derived_transfer_point_table.isVisible())
            self.assertGreaterEqual(viewer.derived_transfer_point_table.height(), 120)
            self.assertGreaterEqual(viewer.derived_transfer_edit_button.width(), 100)
        finally:
            viewer.close()

    def test_conversion_batch_targets_validate_calculate_and_export_metadata(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            frequency = np.array([10.0, 20.0, 40.0, 80.0], dtype=float)
            dataset = AnalysisDataset(
                id=1,
                path=Path("batch_psd.csv"),
                name="batch_psd.csv",
                sample_rate=256.0,
                series=[
                    AnalysisSeries(1, 0, "x", "X", unit="g^2/Hz"),
                    AnalysisSeries(1, 1, "y", "Y", unit="g^2/Hz"),
                ],
                frequency_hz=frequency,
                autospectrum={
                    "x": np.array([1.0, 2.0, 3.0, 4.0]),
                    "y": np.array([2.0, 3.0, 4.0, 5.0]),
                },
            )
            viewer._datasets = [dataset]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            manual_index = viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            viewer.derived_transfer_combo.setCurrentIndex(manual_index)
            viewer.derived_batch_target_list.clearSelection()
            for row in range(2):
                viewer.derived_batch_target_list.item(row).setSelected(True)

            viewer._plot_derived()

            self.assertEqual(len(viewer._last_derived_results), 2)
            self.assertFalse(viewer._derived_results_stale)
            self.assertTrue(viewer.derived_batch_export_button.isEnabled())
            self.assertEqual(
                [viewer.derived_batch_status_table.item(row, 1).text() for row in range(2)],
                ["完成", "完成"],
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                destination = Path(temp_dir) / "batch_result.csv"
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getSaveFileName",
                    return_value=(str(destination), ""),
                ):
                    viewer._export_plot_csv(viewer.derived_plots[1])
                self.assertTrue(destination.exists())
                metadata = destination.with_suffix(".json").read_text(encoding="utf-8")
                self.assertIn("vianalysis_processing_metadata_v1", metadata)
                self.assertIn("batch_psd.csv", metadata)
        finally:
            viewer.close()

    def test_conversion_invalid_range_preserves_previous_plot_and_blocks_stale_export(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            frequency = np.array([10.0, 20.0, 40.0, 80.0], dtype=float)
            dataset = AnalysisDataset(
                id=1,
                path=Path("input.csv"),
                name="input.csv",
                sample_rate=256.0,
                series=[AnalysisSeries(1, 0, "x", "X", unit="g^2/Hz")],
                frequency_hz=frequency,
                autospectrum={"x": np.ones(frequency.shape)},
            )
            viewer._datasets = [dataset]
            viewer._refresh_dataset_lists()
            viewer.derived_transfer_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            )
            viewer._plot_derived()
            previous = {
                label: (x.copy(), y.copy())
                for label, (x, y) in viewer._plot_curves[viewer.derived_plots[1]].items()
            }

            viewer.derived_freq_min_edit.setText("70")
            viewer.derived_freq_max_edit.setText("20")
            viewer._plot_derived()

            self.assertTrue(viewer._derived_results_stale)
            self.assertFalse(viewer.derived_export_buttons[1].isEnabled())
            self.assertIn("频率下限必须小于", viewer.derived_issue_label.text())
            self.assertEqual(set(viewer._plot_curves[viewer.derived_plots[1]]), set(previous))
            for label, (x, y) in previous.items():
                np.testing.assert_array_equal(viewer._plot_curves[viewer.derived_plots[1]][label][0], x)
                np.testing.assert_array_equal(viewer._plot_curves[viewer.derived_plots[1]][label][1], y)
        finally:
            viewer.close()

    def test_conversion_text_parameter_waits_for_editing_finished(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._derived_results_stale = False
            viewer.derived_export_buttons[1].setEnabled(True)
            viewer.derived_freq_min_edit.setText("5")
            self.assertFalse(viewer._derived_results_stale)
            viewer.derived_freq_min_edit.editingFinished.emit()
            self.assertTrue(viewer._derived_results_stale)
            self.assertFalse(viewer.derived_export_buttons[1].isEnabled())
        finally:
            viewer.close()

    def test_conversion_curve_edit_undo_redo_and_duplicate_guard(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer.derived_transfer_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            )
            original_f, original_db = viewer._current_transfer_control_points()
            self.assertTrue(
                viewer._set_current_transfer_control_points(
                    np.array([10.0, 20.0, 100.0]),
                    np.array([1.0, 2.0, 3.0]),
                    replot=False,
                )
            )
            viewer._undo_curve_edit()
            np.testing.assert_array_equal(viewer._current_transfer_control_points()[0], original_f)
            np.testing.assert_array_equal(viewer._current_transfer_control_points()[1], original_db)
            viewer._redo_curve_edit()
            np.testing.assert_array_equal(viewer._current_transfer_control_points()[0], [10.0, 20.0, 100.0])
            viewer._clear_current_transfer_edit_points()
            viewer._undo_curve_edit()
            np.testing.assert_array_equal(viewer._current_transfer_control_points()[0], [10.0, 20.0, 100.0])
            self.assertFalse(
                viewer._set_current_transfer_control_points(
                    np.array([10.0, 10.0, 100.0]),
                    np.array([1.0, 2.0, 3.0]),
                    replot=False,
                )
            )
            self.assertIn("重复频率", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_conversion_recipe_rejects_invalid_structure_and_batch_paths_do_not_overwrite(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                recipe_path = root / "invalid_recipe.json"
                recipe_path.write_text('{"transfer": "not-an-object"}', encoding="utf-8")
                with mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QFileDialog.getOpenFileName",
                    return_value=(str(recipe_path), ""),
                ), mock.patch(
                    "python_vna.ui.analysis_viewer.QtWidgets.QMessageBox.warning"
                ) as warning:
                    viewer._load_processing_recipe()
                self.assertTrue(warning.called)

                existing = root / "same.csv"
                existing.write_text("existing", encoding="utf-8")
                reserved: set[Path] = set()
                first = viewer._unique_batch_export_path(root, "same", reserved)
                second = viewer._unique_batch_export_path(root, "same", reserved)
                self.assertEqual(first.name, "same#2.csv")
                self.assertEqual(second.name, "same#3.csv")
        finally:
            viewer.close()

    def test_conversion_visible_multi_file_load_runs_in_background_and_reports_results(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer.show()
            QtWidgets.QApplication.processEvents()
            with tempfile.TemporaryDirectory() as temp_dir:
                first = Path(temp_dir) / "first.csv"
                second = Path(temp_dir) / "second.csv"
                first.write_text("time,x\n0,1\n0.1,2\n", encoding="utf-8")
                second.write_text("time,y\n0,3\n0.1,4\n", encoding="utf-8")

                viewer._dispatch_load_paths([first, second])
                self.assertIsNotNone(viewer._background_load_task)
                timer = QtCore.QElapsedTimer()
                timer.start()
                while viewer._background_load_task is not None and timer.elapsed() < 5000:
                    QtWidgets.QApplication.processEvents()
                    QtCore.QThread.msleep(10)

                self.assertIsNone(viewer._background_load_task)
                self.assertEqual(len(viewer._datasets), 2)
                self.assertEqual([status for _name, status, _detail in viewer._last_load_report], ["成功", "成功"])
                self.assertIn("后台加载完成", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_manual_transfer_psd_edit_workspace_and_stitch_ui_paths(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 40.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        input_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai1": np.sin(2.0 * np.pi * 10.0 * time_s),
                },
            },
            spectra={"f": freqs, "autospectrum": {"ai0": np.array([0.0, 1.0, 1.0, 1.0]), "ai1": np.array([0.0, 9.0, 9.0, 9.0])}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(input_measurement, session_config=session, dataset_id=1, name="input"),
            ]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            manual_index = viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            input_index = viewer._combo_index_for_data(viewer.derived_input_series_combo, "1:ai0")
            viewer.derived_transfer_combo.setCurrentIndex(manual_index)
            viewer._apply_slot_selection("input", "1:ai0")
            self.assertEqual(viewer.derived_input_series_combo.currentIndex(), input_index)
            self.assertNotEqual(viewer._slot_value_labels["input"].toolTip(), "(未选择)")
            viewer._set_current_transfer_control_points(
                np.array([10.0, 40.0], dtype=float),
                np.array([6.0, 6.0], dtype=float),
                replot=False,
            )

            viewer._plot_derived()
            psd_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertGreater(len(psd_curves), 0)
            label, (before_f, before_psd) = next(iter(psd_curves.items()))
            self.assertAlmostEqual(float(np.median(before_psd)), 10.0 ** (6.0 / 10.0), delta=0.05)
            viewer.workspace_add_current_button.click()
            self.assertEqual(len(viewer._workspace_curves), 1)
            self.assertEqual(viewer.workspace_curve_table.rowCount(), 1)

            viewer._active_trace[viewer.derived_plots[1]] = label
            viewer._initialize_psd_edit_points_from_active_curve()
            self.assertIn(label, viewer._psd_edit_points)
            initial_point_count = viewer.derived_transfer_point_table.rowCount()

            viewer._add_transfer_control_point()
            self.assertEqual(viewer.derived_transfer_point_table.rowCount(), initial_point_count + 1)
            self.assertEqual(len(viewer._curve_edit_items[viewer.derived_plots[1]]), initial_point_count + 1)

            viewer.derived_transfer_point_table.selectRow(1)
            viewer._delete_selected_transfer_control_point()
            self.assertEqual(viewer.derived_transfer_point_table.rowCount(), initial_point_count)
            self.assertEqual(len(viewer._curve_edit_items[viewer.derived_plots[1]]), initial_point_count)

            viewer._workspace_operation_sources["a"] = ("current_result_curve", label)
            viewer._workspace_operation_sources["b"] = ("dataset_psd_curve", "1:ai1")
            viewer.workspace_op_type_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.workspace_op_type_combo, "subtract")
            )
            viewer._execute_workspace_operation()
            self.assertEqual(len(viewer._workspace_curves), 2)
            subtract_curve = viewer._workspace_curves[-1]
            self.assertEqual(subtract_curve.curve_type, "相减结果PSD")

            viewer._workspace_operation_sources["a"] = ("workspace_curve", subtract_curve.curve_id)
            viewer._workspace_operation_sources["b"] = ("dataset_psd_curve", "1:ai1")
            viewer.workspace_op_type_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.workspace_op_type_combo, "stitch")
            )
            viewer.workspace_op_order_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.workspace_op_order_combo, "a_first")
            )
            viewer.workspace_op_split_edit.setText("20")
            viewer._execute_workspace_operation()
            self.assertEqual(len(viewer._workspace_curves), 3)
            stitched_curve = viewer._workspace_curves[-1]
            self.assertEqual(stitched_curve.curve_type, "拼合结果PSD")
            self.assertTrue(np.all(stitched_curve.values > 0.0))

            viewer._delete_datasets_by_ids({1})
            self.assertEqual(viewer._datasets, [])
            _ = before_f
        finally:
            viewer.close()

    def test_transfer_control_point_change_replots_transfer_curve_immediately(self):
        session = default_session_config()
        frequency = np.array([0.0, 10.0, 20.0, 40.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {"ai0": np.sin(2.0 * np.pi * 10.0 * time_s)},
            },
            spectra={"f": frequency, "autospectrum": {"ai0": np.ones(frequency.size)}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(measurement, session_config=session, dataset_id=1, name="input"),
            ]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            viewer.derived_transfer_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            )
            viewer.derived_input_series_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.derived_input_series_combo, "1:ai0")
            )
            viewer._set_current_transfer_control_points(
                np.array([10.0, 40.0]),
                np.array([0.0, 0.0]),
                replot=False,
            )
            viewer._plot_derived()

            viewer._set_current_transfer_control_points(
                np.array([10.0, 40.0]),
                np.array([6.0, 6.0]),
                replot=False,
            )
            viewer._update_transfer_edit_preview(viewer.derived_plots[0])
            _label, (_x, preview_db) = next(iter(viewer._plot_curves[viewer.derived_plots[0]].items()))
            self.assertAlmostEqual(float(np.median(preview_db)), 6.0, places=6)

            viewer._set_current_transfer_control_points(
                np.array([10.0, 40.0]),
                np.array([12.0, 12.0]),
            )

            _label, (_x, magnitude_db) = next(iter(viewer._plot_curves[viewer.derived_plots[0]].items()))
            self.assertAlmostEqual(float(np.median(magnitude_db)), 12.0, places=6)
        finally:
            viewer.close()

    def test_mimo_coupling_generates_three_input_psd_curves(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        frf: dict[str, np.ndarray] = {}
        gains = np.array(
            [
                [2.0, 0.5, 0.25],
                [0.2, 3.0, 0.4],
                [0.1, 0.3, 4.0],
            ],
            dtype=float,
        )
        for input_index in range(3):
            for output_index in range(3):
                frf[f"ai{input_index}->ai{output_index}"] = np.full(
                    freqs.shape,
                    gains[output_index, input_index] + 0.0j,
                    dtype=complex,
                )
        measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.zeros_like(time_s),
                    "ai1": np.zeros_like(time_s),
                    "ai2": np.zeros_like(time_s),
                },
            },
            spectra={"f": freqs, "autospectrum": {}},
            frf=frf,
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0},
        )
        target_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.zeros_like(time_s),
                    "ai1": np.zeros_like(time_s),
                    "ai2": np.zeros_like(time_s),
                },
            },
            spectra={
                "f": freqs,
                "autospectrum": {
                    "ai0": np.array([0.0, 4.0, 4.0, 4.0], dtype=float),
                    "ai1": np.array([0.0, 9.0, 9.0, 9.0], dtype=float),
                    "ai2": np.array([0.0, 16.0, 16.0, 16.0], dtype=float),
                },
            },
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(measurement, session_config=session, dataset_id=1, name="mimo"),
                dataset_from_measurement(target_measurement, session_config=session, dataset_id=2, name="targets"),
            ]
            viewer._next_dataset_id = 3
            viewer._refresh_dataset_lists()
            transfer_matrix_data = [
                [
                    (1, f"ai{input_index}->ai{output_index}", f"ai{input_index}", f"ai{output_index}", "stored")
                    for input_index in range(3)
                ]
                for output_index in range(3)
            ]

            ok = viewer._execute_mimo_coupling(
                {
                    "transfers": transfer_matrix_data,
                    "targets": [
                        ("dataset_psd_curve", "2:ai0"),
                        ("dataset_psd_curve", "2:ai1"),
                        ("dataset_psd_curve", "2:ai2"),
                    ],
                    "relation": "independent",
                    "regularization": 0.0,
                    "prefix": "三轴输入",
                }
            )

            self.assertTrue(ok)
            self.assertEqual(len([curve for curve in viewer._workspace_curves if curve.curve_type == "三轴耦合输入PSD"]), 3)
            plot_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any(label.startswith("三轴输入X") for label in plot_curves))
            self.assertTrue(any(label.startswith("校核顶部响应X") for label in plot_curves))
            response_x = plot_curves[next(label for label in plot_curves if label.startswith("校核顶部响应X"))]
            np.testing.assert_allclose(response_x[1], np.array([4.0, 4.0, 4.0]), rtol=1e-10, atol=1e-10)
        finally:
            viewer.close()

    def test_mimo_coupling_respects_top_to_base_direction(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        frf: dict[str, np.ndarray] = {}
        gains = np.array([2.0, 3.0, 4.0], dtype=float)
        for input_index in range(3):
            for output_index in range(3):
                value = gains[input_index] if input_index == output_index else 0.0
                frf[f"ai{input_index}->ai{output_index}"] = np.full(
                    freqs.shape,
                    value + 0.0j,
                    dtype=complex,
                )
        top_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.zeros_like(time_s),
                    "ai1": np.zeros_like(time_s),
                    "ai2": np.zeros_like(time_s),
                },
            },
            spectra={
                "f": freqs,
                "autospectrum": {
                    "ai0": np.array([0.0, 16.0, 16.0, 16.0], dtype=float),
                    "ai1": np.array([0.0, 81.0, 81.0, 81.0], dtype=float),
                    "ai2": np.array([0.0, 256.0, 256.0, 256.0], dtype=float),
                },
            },
            frf=frf,
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(top_measurement, session_config=session, dataset_id=1, name="top_targets"),
            ]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            direction_index = viewer._combo_index_for_data(viewer.derived_direction_combo, DERIVE_TOP_TO_BASE)
            viewer.derived_direction_combo.setCurrentIndex(direction_index)
            transfer_matrix_data = [
                [
                    (1, f"ai{input_index}->ai{output_index}", f"ai{input_index}", f"ai{output_index}", "stored")
                    for input_index in range(3)
                ]
                for output_index in range(3)
            ]

            ok = viewer._execute_mimo_coupling(
                {
                    "transfers": transfer_matrix_data,
                    "targets": [
                        ("dataset_psd_curve", "1:ai0"),
                        ("dataset_psd_curve", "1:ai1"),
                        ("dataset_psd_curve", "1:ai2"),
                    ],
                    "relation": "independent",
                    "regularization": 0.0,
                    "prefix": "base_input",
                }
            )

            self.assertTrue(ok)
            input_curves = {
                label: curve
                for label, curve in viewer._plot_curves[viewer.derived_plots[1]].items()
                if label.startswith("base_input")
            }
            response_curves = {
                label: curve
                for label, curve in viewer._plot_curves[viewer.derived_plots[1]].items()
                if label.startswith("校核地基响应")
            }
            self.assertEqual(len(input_curves), 3)
            self.assertEqual(len(response_curves), 3)
            np.testing.assert_allclose(response_curves["校核地基响应X"][1], [16.0, 16.0, 16.0], rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(response_curves["校核地基响应Y"][1], [81.0, 81.0, 81.0], rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(response_curves["校核地基响应Z"][1], [256.0, 256.0, 256.0], rtol=1e-10, atol=1e-10)
        finally:
            viewer.close()

    def test_mimo_coupling_direction_changes_labels_without_inverting_matrix(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        frf: dict[str, np.ndarray] = {}
        axis_gains = [
            np.array([0.0, 2.0, 4.0, 8.0], dtype=float),
            np.array([0.0, 3.0, 6.0, 12.0], dtype=float),
            np.array([0.0, 5.0, 10.0, 20.0], dtype=float),
        ]
        for input_index in range(3):
            for output_index in range(3):
                values = axis_gains[output_index] if input_index == output_index else np.zeros_like(freqs)
                frf[f"ai{input_index}->ai{output_index}"] = values.astype(complex)
        measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.zeros_like(time_s),
                    "ai1": np.zeros_like(time_s),
                    "ai2": np.zeros_like(time_s),
                },
            },
            spectra={
                "f": freqs,
                "autospectrum": {
                    "ai0": np.array([0.0, 4.0, 4.0, 4.0], dtype=float),
                    "ai1": np.array([0.0, 9.0, 9.0, 9.0], dtype=float),
                    "ai2": np.array([0.0, 16.0, 16.0, 16.0], dtype=float),
                },
            },
            frf=frf,
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(measurement, session_config=session, dataset_id=1, name="directional_demo"),
            ]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            transfer_matrix_data = [
                [
                    (1, f"ai{input_index}->ai{output_index}", f"ai{input_index}", f"ai{output_index}", "stored")
                    for input_index in range(3)
                ]
                for output_index in range(3)
            ]
            config = {
                "transfers": transfer_matrix_data,
                "targets": [
                    ("dataset_psd_curve", "1:ai0"),
                    ("dataset_psd_curve", "1:ai1"),
                    ("dataset_psd_curve", "1:ai2"),
                ],
                "relation": "independent",
                "regularization": 0.0,
                "prefix": "",
            }

            viewer.derived_direction_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.derived_direction_combo, DERIVE_BASE_TO_TOP)
            )
            ok_base_to_top = viewer._execute_mimo_coupling(config)
            self.assertTrue(ok_base_to_top)
            curves_base_to_top = {
                label: curve
                for label, curve in viewer._plot_curves[viewer.derived_plots[1]].items()
                if label.startswith("地基输入")
            }

            viewer.derived_direction_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.derived_direction_combo, DERIVE_TOP_TO_BASE)
            )
            ok_top_to_base = viewer._execute_mimo_coupling(config)
            self.assertTrue(ok_top_to_base)
            curves_top_to_base = {
                label: curve
                for label, curve in viewer._plot_curves[viewer.derived_plots[1]].items()
                if label.startswith("顶部输入")
            }

            self.assertEqual(set(curves_base_to_top), {"地基输入X", "地基输入Y", "地基输入Z"})
            self.assertEqual(set(curves_top_to_base), {"顶部输入X", "顶部输入Y", "顶部输入Z"})
            np.testing.assert_allclose(curves_base_to_top["地基输入X"][1], [1.0, 0.25, 0.0625], rtol=1e-10, atol=1e-10)
            np.testing.assert_allclose(curves_top_to_base["顶部输入X"][1], [1.0, 0.25, 0.0625], rtol=1e-10, atol=1e-10)
            self.assertLess(curves_base_to_top["地基输入X"][1][-1], curves_base_to_top["地基输入X"][1][0])
            self.assertLess(curves_top_to_base["顶部输入X"][1][-1], curves_top_to_base["顶部输入X"][1][0])
        finally:
            viewer.close()

    def test_mimo_result_mode_switch_to_time_keeps_mimo_results(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        frf: dict[str, np.ndarray] = {}
        gains = np.array([2.0, 3.0, 4.0], dtype=float)
        for input_index in range(3):
            for output_index in range(3):
                value = gains[input_index] if input_index == output_index else 0.0
                frf[f"ai{input_index}->ai{output_index}"] = np.full(
                    freqs.shape,
                    value + 0.0j,
                    dtype=complex,
                )
        top_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai1": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai2": np.sin(2.0 * np.pi * 10.0 * time_s),
                },
            },
            spectra={
                "f": freqs,
                "autospectrum": {
                    "ai0": np.array([0.0, 16.0, 16.0, 16.0], dtype=float),
                    "ai1": np.array([0.0, 81.0, 81.0, 81.0], dtype=float),
                    "ai2": np.array([0.0, 256.0, 256.0, 256.0], dtype=float),
                },
            },
            frf=frf,
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(top_measurement, session_config=session, dataset_id=1, name="top_targets"),
            ]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            viewer.derived_direction_combo.setCurrentIndex(
                viewer._combo_index_for_data(viewer.derived_direction_combo, DERIVE_TOP_TO_BASE)
            )
            transfer_matrix_data = [
                [
                    (1, f"ai{input_index}->ai{output_index}", f"ai{input_index}", f"ai{output_index}", "stored")
                    for input_index in range(3)
                ]
                for output_index in range(3)
            ]
            ok = viewer._execute_mimo_coupling(
                {
                    "transfers": transfer_matrix_data,
                    "targets": [
                        ("dataset_psd_curve", "1:ai0"),
                        ("dataset_psd_curve", "1:ai1"),
                        ("dataset_psd_curve", "1:ai2"),
                    ],
                    "relation": "independent",
                    "regularization": 0.0,
                    "prefix": "",
                }
            )
            self.assertTrue(ok)
            plot = viewer.derived_plots[1]
            self.assertEqual(
                set(viewer._plot_curves[plot]),
                {
                    "顶部输入X",
                    "顶部输入Y",
                    "顶部输入Z",
                    "校核地基响应X",
                    "校核地基响应Y",
                    "校核地基响应Z",
                },
            )

            viewer.derived_result_mode_combo.setCurrentText("近似时域")
            viewer._auto_plot_derived_from_control_change()

            self.assertEqual(viewer._plot_curve_kind[plot], "time")
            time_labels = set(viewer._plot_curves[plot])
            self.assertTrue(any(label.startswith("顶部输入X") for label in time_labels))
            self.assertTrue(any(label.startswith("校核地基响应X") for label in time_labels))
            self.assertFalse(any("VC A" in label for label in time_labels))
        finally:
            viewer.close()

    def test_mimo_target_dataset_psd_is_not_reinterpreted_by_quantity_mode(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 40.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        frf: dict[str, np.ndarray] = {}
        for input_index in range(3):
            for output_index in range(3):
                value = 2.0 if input_index == output_index else 0.0
                frf[f"ai{input_index}->ai{output_index}"] = np.full(
                    freqs.shape,
                    value + 0.0j,
                    dtype=complex,
                )
        measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.zeros_like(time_s),
                    "ai1": np.zeros_like(time_s),
                    "ai2": np.zeros_like(time_s),
                },
            },
            spectra={
                "f": freqs,
                "autospectrum": {
                    "ai0": np.array([0.0, 4.0, 16.0, 64.0], dtype=float),
                    "ai1": np.array([0.0, 4.0, 16.0, 64.0], dtype=float),
                    "ai2": np.array([0.0, 4.0, 16.0, 64.0], dtype=float),
                },
            },
            frf=frf,
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0, "autospectrum_kind": "psd"},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(measurement, session_config=session, dataset_id=1, name="quantity_demo"),
            ]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            transfer_matrix_data = [
                [
                    (1, f"ai{input_index}->ai{output_index}", f"ai{input_index}", f"ai{output_index}", "stored")
                    for input_index in range(3)
                ]
                for output_index in range(3)
            ]
            config = {
                "transfers": transfer_matrix_data,
                "targets": [
                    ("dataset_psd_curve", "1:ai0"),
                    ("dataset_psd_curve", "1:ai1"),
                    ("dataset_psd_curve", "1:ai2"),
                ],
                "relation": "independent",
                "regularization": 0.0,
                "prefix": "test_",
            }

            viewer.quantity_combo.setCurrentText("Acceleration")
            self.assertTrue(viewer._execute_mimo_coupling(config))
            accel_curves = {
                label: curve
                for label, curve in viewer._plot_curves[viewer.derived_plots[1]].items()
                if label.startswith("test_")
            }

            viewer.quantity_combo.setCurrentText("Velocity")
            self.assertTrue(viewer._execute_mimo_coupling(config))
            velocity_curves = {
                label: curve
                for label, curve in viewer._plot_curves[viewer.derived_plots[1]].items()
                if label.startswith("test_")
            }

            accel_psd = accel_curves["test_X"][1]
            velocity_psd = velocity_curves["test_X"][1]
            np.testing.assert_allclose(accel_psd, [1.0, 4.0, 16.0], rtol=1e-10, atol=1e-10)
            expected_velocity = accel_psd / ((2.0 * np.pi * np.array([10.0, 20.0, 40.0])) ** 2) * 1e12
            np.testing.assert_allclose(velocity_psd, expected_velocity, rtol=1e-10, atol=1e-10)
        finally:
            viewer.close()

    def test_mimo_coupling_uses_full_target_correlation_from_time_series(self):
        session = default_session_config()
        sample_rate = 256.0
        time_s = np.arange(4096, dtype=float) / sample_rate
        freqs = np.fft.rfftfreq(time_s.size, d=1.0 / sample_rate)
        positive = freqs > 0.0
        freq_axis = np.concatenate(([0.0], freqs[positive]))

        transfer = np.array(
            [
                [1.0 + 0.0j, 0.30 + 0.10j, 0.05 + 0.00j],
                [0.10 - 0.05j, 1.20 + 0.0j, 0.25 - 0.10j],
                [0.05 + 0.02j, 0.20 + 0.06j, 0.90 + 0.0j],
            ],
            dtype=complex,
        )
        frf: dict[str, np.ndarray] = {}
        for input_index in range(3):
            for output_index in range(3):
                values = np.full(freq_axis.shape, 0.0 + 0.0j, dtype=complex)
                values[1:] = transfer[output_index, input_index]
                frf[f"ai{input_index}->ai{output_index}"] = values

        rng = np.random.default_rng(1234)
        input_x = rng.standard_normal(time_s.size)
        input_y = 0.7 * input_x + 0.3 * rng.standard_normal(time_s.size)
        input_z = -0.25 * input_x + 0.4 * input_y + 0.25 * rng.standard_normal(time_s.size)
        inputs = np.vstack([input_x, input_y, input_z])
        outputs = np.real(transfer @ inputs)

        source_measurement = MeasurementSet(
            sample_rate=sample_rate,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.zeros_like(time_s),
                    "ai1": np.zeros_like(time_s),
                    "ai2": np.zeros_like(time_s),
                },
            },
            spectra={"f": freq_axis, "autospectrum": {}},
            frf=frf,
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": float(freq_axis[2] - freq_axis[1]), "legacy_runtime_wincor": 1.0},
        )
        target_measurement = MeasurementSet(
            sample_rate=sample_rate,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": outputs[0],
                    "ai1": outputs[1],
                    "ai2": outputs[2],
                },
            },
            spectra={"f": freq_axis, "autospectrum": {}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": float(freq_axis[2] - freq_axis[1]), "legacy_runtime_wincor": 1.0},
        )

        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(source_measurement, session_config=session, dataset_id=1, name="mimo_transfer"),
                dataset_from_measurement(target_measurement, session_config=session, dataset_id=2, name="mimo_target"),
            ]
            viewer._next_dataset_id = 3
            viewer._refresh_dataset_lists()
            transfer_matrix_data = [
                [
                    (1, f"ai{input_index}->ai{output_index}", f"ai{input_index}", f"ai{output_index}", "stored")
                    for input_index in range(3)
                ]
                for output_index in range(3)
            ]
            with mock.patch.object(
                viewer,
                "_time_domain_cross_psd_matrix_for_targets",
                wraps=viewer._time_domain_cross_psd_matrix_for_targets,
            ) as target_matrix_builder:
                ok = viewer._execute_mimo_coupling(
                    {
                        "transfers": transfer_matrix_data,
                        "targets": [
                            ("dataset_psd_curve", "2:ai0"),
                            ("dataset_psd_curve", "2:ai1"),
                            ("dataset_psd_curve", "2:ai2"),
                        ],
                        "relation": "independent",
                        "regularization": 1e-8,
                        "prefix": "corr_",
                    }
                )

            self.assertTrue(ok)
            self.assertEqual(target_matrix_builder.call_count, 1)
            self.assertIn("时域重算互谱反演", viewer.statusBar().currentMessage())
            plot_curves = viewer._plot_curves[viewer.derived_plots[1]]
            predicted_x = plot_curves["校核顶部响应X"][1]
            predicted_y = plot_curves["校核顶部响应Y"][1]
            predicted_z = plot_curves["校核顶部响应Z"][1]
            target_x = plot_curves["corr_X"][0]

            target_dataset = viewer._dataset_by_id(2)
            self.assertIsNotNone(target_dataset)
            dataset = target_dataset
            assert dataset is not None
            expected = []
            for channel_key in ("ai0", "ai1", "ai2"):
                series = next(series for series in dataset.series if series.channel_key == channel_key)
                f_psd, psd = viewer._psd_for_series(dataset, series, scale=float(series.scale or 1.0))
                expected.append(np.interp(target_x, f_psd, psd, left=0.0, right=0.0))

            np.testing.assert_allclose(predicted_x, expected[0], rtol=0.25, atol=1e-8)
            np.testing.assert_allclose(predicted_y, expected[1], rtol=0.25, atol=1e-8)
            np.testing.assert_allclose(predicted_z, expected[2], rtol=0.25, atol=1e-8)
        finally:
            viewer.close()

    def test_mimo_time_transfer_matrix_from_rows_uses_joint_time_estimator(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            rng = np.random.default_rng(42)
            sample_rate = 1024.0
            time_s = np.arange(8192, dtype=float) / sample_rate
            input_x = rng.standard_normal(time_s.size)
            input_y = 0.75 * input_x + 0.4 * rng.standard_normal(time_s.size)
            input_z = -0.3 * input_x + 0.2 * input_y + 0.5 * rng.standard_normal(time_s.size)
            inputs = np.vstack([input_x, input_y, input_z])
            expected = np.array(
                [
                    [1.0, 0.35, 0.10],
                    [0.20, 1.30, -0.25],
                    [-0.15, 0.45, 0.90],
                ],
                dtype=float,
            )
            outputs = expected @ inputs
            series = [
                AnalysisSeries(dataset_id=1, channel_index=index, channel_key=f"ai{index}", display_name=f"ai{index}")
                for index in range(6)
            ]
            dataset = AnalysisDataset(
                id=1,
                path=Path("time_mimo.vna"),
                name="time_mimo.vna",
                sample_rate=sample_rate,
                series=series,
                time_s=time_s,
                channels={
                    "ai0": inputs[0],
                    "ai1": inputs[1],
                    "ai2": inputs[2],
                    "ai3": outputs[0],
                    "ai4": outputs[1],
                    "ai5": outputs[2],
                },
            )
            viewer._datasets = [dataset]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            preferred_last = (1, "ai2->ai5", "ai2", "ai5", "time")
            option_data = {data for _label, data in viewer._mimo_transfer_options()}
            self.assertIn(preferred_last, option_data)
            self.assertEqual(
                viewer._preferred_mimo_transfer_data(output_index=2, input_index=2),
                preferred_last,
            )
            transfer_rows = [
                [
                    (1, f"ai{input_index}->ai{output_index + 3}", f"ai{input_index}", f"ai{output_index + 3}", "time")
                    for input_index in range(3)
                ]
                for output_index in range(3)
            ]

            result = viewer._mimo_time_transfer_matrix_from_rows(transfer_rows, regularization=0.0)
            self.assertIsNotNone(result)
            assert result is not None
            frequency, transfer_matrix, _labels = result

            self.assertGreater(frequency.size, 10)
            np.testing.assert_allclose(np.median(np.real(transfer_matrix), axis=0), expected, rtol=1e-10, atol=1e-10)
            pair = viewer._transfer_from_mimo_data(transfer_rows[0][0])
            self.assertIsNotNone(pair)
            assert pair is not None
            _pair_f, pair_h, _label, _phase = pair
            self.assertGreater(abs(float(np.median(np.real(pair_h))) - expected[0, 0]), 0.1)
        finally:
            viewer.close()

    def test_mimo_time_recomputed_cross_psd_preserves_complex_pair_direction(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            sample_rate = 256.0
            time_s = np.arange(2048, dtype=float) / sample_rate
            signal_x = np.sin(2.0 * np.pi * 13.0 * time_s)
            signal_y = np.sin(2.0 * np.pi * 13.0 * time_s + np.pi / 3.0)
            signal_z = 0.2 * np.sin(2.0 * np.pi * 19.0 * time_s + np.pi / 5.0)
            dataset = AnalysisDataset(
                id=1,
                path=Path("pair.vna"),
                name="pair.vna",
                sample_rate=sample_rate,
                series=[
                    AnalysisSeries(dataset_id=1, channel_index=0, channel_key="ai0", display_name="X", scale=1.0),
                    AnalysisSeries(dataset_id=1, channel_index=1, channel_key="ai1", display_name="Y", scale=1.0),
                    AnalysisSeries(dataset_id=1, channel_index=2, channel_key="ai2", display_name="Z", scale=1.0),
                ],
                time_s=time_s,
                channels={
                    "ai0": signal_x,
                    "ai1": signal_y,
                    "ai2": signal_z,
                },
            )
            viewer._datasets = [dataset]
            viewer._refresh_dataset_lists()
            grid = np.linspace(1.0, 100.0, 128)
            matrix = viewer._time_domain_cross_psd_matrix_for_targets(dataset, dataset.series, grid)
            self.assertIsNotNone(matrix)
            assert matrix is not None
            peak_index = int(np.argmax(np.abs(matrix[:, 0, 1])))
            self.assertGreater(np.imag(matrix[peak_index, 0, 1]), 0.0)
            self.assertLess(np.imag(matrix[peak_index, 1, 0]), 0.0)
        finally:
            viewer.close()

    def test_mimo_result_time_mode_uses_joint_matrix_synthesis(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            freqs = np.array([10.0, 20.0, 30.0], dtype=float)
            matrix = fully_correlated_psd_matrix(np.array([[1.0, 0.5, 0.25], [1.0, 0.5, 0.25], [1.0, 0.5, 0.25]]))
            viewer._last_derived_results = [
                {
                    "label": "X",
                    "source_label": "target X",
                    "psd": (freqs, np.array([1.0, 0.5, 0.25], dtype=float)),
                    "mimo_time_group": "input",
                    "mimo_axis_index": 0,
                    "mimo_time_matrix": matrix,
                    "mimo_time_frequency": freqs,
                },
                {
                    "label": "Y",
                    "source_label": "target Y",
                    "psd": (freqs, np.array([1.0, 0.5, 0.25], dtype=float)),
                    "mimo_time_group": "input",
                    "mimo_axis_index": 1,
                    "mimo_time_matrix": matrix,
                    "mimo_time_frequency": freqs,
                },
            ]
            viewer._plot_derived_result_axis(
                viewer.derived_plots[1],
                "近似时域",
                viewer._last_derived_results,
                keep_existing=False,
            )
            plot_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any("PSD合成" in label for label in plot_curves))
            self.assertGreaterEqual(len(plot_curves), 2)
        finally:
            viewer.close()

    def test_mimo_common_frequency_grid_accepts_phase_flag_transfer_curves(self):
        curves = [
            (np.array([10.0, 20.0, 30.0], dtype=float), np.ones(3, dtype=complex), "X", True),
            (np.array([12.0, 20.0, 28.0], dtype=float), np.ones(3, dtype=complex), "Y", False),
        ]
        grid = AnalysisViewer._mimo_common_frequency_grid(curves)
        np.testing.assert_allclose(grid, np.array([12.0, 20.0, 28.0], dtype=float))

    def test_mimo_coupling_without_phase_falls_back_to_independent_psd(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        frf: dict[str, np.ndarray] = {}
        gains = np.array([2.0, 3.0, 4.0], dtype=float)
        for input_index in range(3):
            for output_index in range(3):
                value = gains[input_index] if input_index == output_index else 0.0
                frf[f"ai{input_index}->ai{output_index}"] = np.full(
                    freqs.shape,
                    value,
                    dtype=float,
                )
        measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.zeros_like(time_s),
                    "ai1": np.zeros_like(time_s),
                    "ai2": np.zeros_like(time_s),
                },
            },
            spectra={"f": freqs, "autospectrum": {}},
            frf=frf,
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0},
        )
        target_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai1": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai2": np.sin(2.0 * np.pi * 10.0 * time_s),
                },
            },
            spectra={
                "f": freqs,
                "autospectrum": {
                    "ai0": np.array([0.0, 16.0, 16.0, 16.0], dtype=float),
                    "ai1": np.array([0.0, 81.0, 81.0, 81.0], dtype=float),
                    "ai2": np.array([0.0, 256.0, 256.0, 256.0], dtype=float),
                },
            },
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [
                dataset_from_measurement(measurement, session_config=session, dataset_id=1, name="mimo_real_frf"),
                dataset_from_measurement(target_measurement, session_config=session, dataset_id=2, name="mimo_target"),
            ]
            viewer._next_dataset_id = 3
            viewer._refresh_dataset_lists()
            ok = viewer._execute_mimo_coupling(
                {
                    "transfers": [
                        [
                            (1, f"ai{input_index}->ai{output_index}", f"ai{input_index}", f"ai{output_index}", "stored")
                            for input_index in range(3)
                        ]
                        for output_index in range(3)
                    ],
                    "targets": [
                        ("dataset_psd_curve", "2:ai0"),
                        ("dataset_psd_curve", "2:ai1"),
                        ("dataset_psd_curve", "2:ai2"),
                    ],
                    "relation": "independent",
                    "regularization": 0.0,
                    "prefix": "fallback_",
                }
            )
            self.assertTrue(ok)
            self.assertIn("独立PSD近似", viewer.statusBar().currentMessage())
            plot_curves = viewer._plot_curves[viewer.derived_plots[1]]
            np.testing.assert_allclose(plot_curves["校核顶部响应X"][1], [16.0, 16.0, 16.0], rtol=1e-10, atol=1e-10)
        finally:
            viewer.close()

    def test_derived_time_mode_synthesizes_time_curve_from_psd_only(self):
        dataset = AnalysisDataset(
            id=1,
            path=Path("psd_only.vna"),
            name="psd_only.vna",
            sample_rate=256.0,
            series=[
                AnalysisSeries(
                    dataset_id=1,
                    channel_index=0,
                    channel_key="ai0",
                    display_name="PSD Only",
                    unit="",
                    scale=1.0,
                )
            ],
            frequency_hz=np.array([10.0, 20.0, 30.0, 40.0, 50.0], dtype=float),
            autospectrum={"ai0": np.ones(5, dtype=float)},
            rbw_hz=1.0,
            wincor=1.0,
            metadata={"legacy_yapcor_index": 1},
        )
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer._datasets = [dataset]
            viewer._next_dataset_id = 2
            viewer._refresh_dataset_lists()
            manual_index = viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            input_index = viewer._combo_index_for_data(viewer.derived_input_series_combo, "1:ai0")
            viewer.derived_transfer_combo.setCurrentIndex(manual_index)
            viewer.derived_input_series_combo.setCurrentIndex(input_index)
            viewer.derived_result_mode_combo.setCurrentText("近似时域")

            viewer._plot_derived()

            time_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any("PSD合成" in label for label in time_curves))
            _label, (time_synth, values) = next(iter(time_curves.items()))
            self.assertGreaterEqual(time_synth.size, 32)
            self.assertEqual(time_synth.size, values.size)
            self.assertGreater(float(np.std(values)), 0.0)
        finally:
            viewer.close()

    def test_vc_result_mode_switch_uses_band_limited_time_synthesis(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            manual_index = viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            vc_index = viewer._combo_index_for_data(viewer.derived_input_series_combo, ("vc_reference", "VC C"))
            viewer.derived_transfer_combo.setCurrentIndex(manual_index)
            viewer.derived_input_series_combo.setCurrentIndex(vc_index)
            viewer._set_current_transfer_control_points(
                np.array([1.0, 1000.0], dtype=float),
                np.array([0.0, 0.0], dtype=float),
                replot=False,
            )
            viewer._plot_derived()

            with mock.patch.object(
                viewer,
                "_plot_current_psd_curves_as_time",
                side_effect=AssertionError("should replot derived result instead of converting stale PSD plot"),
            ):
                viewer.derived_result_mode_combo.setCurrentText("近似时域")

            self.assertEqual(viewer._plot_curve_kind[viewer.derived_plots[1]], "time")
            self.assertTrue(viewer._time_curve_psd_sources[viewer.derived_plots[1]])
            _label, psd_source = next(iter(viewer._time_curve_psd_sources[viewer.derived_plots[1]].items()))
            self.assertIsNotNone(psd_source[2])
        finally:
            viewer.close()

    def test_vc_time_extension_keeps_band_limited_psd_source(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            manual_index = viewer._combo_index_for_data(viewer.derived_transfer_combo, ("manual_transfer",))
            vc_index = viewer._combo_index_for_data(viewer.derived_input_series_combo, ("vc_reference", "VC C"))
            viewer.derived_transfer_combo.setCurrentIndex(manual_index)
            viewer.derived_input_series_combo.setCurrentIndex(vc_index)
            viewer._set_current_transfer_control_points(
                np.array([1.0, 1000.0], dtype=float),
                np.array([0.0, 0.0], dtype=float),
                replot=False,
            )
            viewer.derived_result_mode_combo.setCurrentText("近似时域")
            viewer._plot_derived()
            plot = viewer.derived_plots[1]
            label = next(iter(viewer._plot_curves[plot]))
            before_source = viewer._time_curve_psd_sources[plot][label]
            self.assertIsNotNone(before_source[2])

            viewer._interpolate_plot_curves(
                plot,
                0.005,
                axis_kind="time",
                duration_s=20.0,
                point_count=4001,
            )

            time_s, values = viewer._plot_curves[plot][label]
            after_source = viewer._time_curve_psd_sources[plot][label]
            self.assertEqual(time_s.size, 4001)
            self.assertAlmostEqual(float(time_s[-1]), 20.0)
            self.assertGreater(float(np.std(values)), 0.0)
            self.assertIsNotNone(after_source[2])
            np.testing.assert_allclose(after_source[0], before_source[0])
        finally:
            viewer.close()

    def test_derived_tab_converts_loaded_transfer_and_input_data(self):
        session = default_session_config()
        freqs = np.array([0.0, 10.0, 20.0, 30.0], dtype=float)
        time_s = np.arange(256, dtype=float) / 256.0
        transfer_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {
                    "ai0": np.sin(2.0 * np.pi * 10.0 * time_s),
                    "ai1": 2.0 * np.sin(2.0 * np.pi * 10.0 * time_s),
                },
            },
            spectra={"f": freqs, "autospectrum": {}},
            frf={"ai0->ai1": np.full(freqs.shape, 2.0 + 0.0j, dtype=complex)},
            coherence={"ai0->ai1": np.full(freqs.shape, 0.25, dtype=float)},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0},
        )
        input_measurement = MeasurementSet(
            sample_rate=256.0,
            time_data={
                "t": time_s,
                "channels": {"ai0": np.sin(2.0 * np.pi * 10.0 * time_s)},
            },
            spectra={"f": freqs, "autospectrum": {"ai0": np.array([0.0, 1.0, 1.0, 1.0])}},
            frf={},
            coherence={},
            cross_spectra={},
            correlations={},
            impulse_responses={},
            metadata={"rbw_hz": 1.0, "legacy_runtime_wincor": 1.0},
        )
        viewer = AnalysisViewer()
        try:
            viewer._datasets = [
                dataset_from_measurement(transfer_measurement, session_config=session, dataset_id=1, name="transfer"),
                dataset_from_measurement(input_measurement, session_config=session, dataset_id=2, name="input"),
            ]
            viewer._next_dataset_id = 3
            viewer._refresh_dataset_lists()
            input_index = viewer._combo_index_for_data(viewer.derived_input_series_combo, "2:ai0")
            viewer.derived_input_series_combo.setCurrentIndex(input_index)

            viewer._plot_derived()

            transfer_curves = viewer._plot_curves[viewer.derived_plots[0]]
            psd_curves = viewer._plot_curves[viewer.derived_plots[1]]
            transfer_data = viewer.derived_transfer_combo.currentData()
            self.assertEqual(transfer_data[0], 1)
            self.assertEqual(transfer_data[1], "ai0->ai1")
            self.assertIn("ai0->ai1", viewer.derived_transfer_combo.currentText())
            self.assertGreater(len(transfer_curves), 0)
            self.assertGreater(len(psd_curves), 0)
            _label, (f, psd) = next(iter(psd_curves.items()))
            np.testing.assert_allclose(f, [10.0, 20.0, 30.0])
            np.testing.assert_allclose(psd, [16.0, 16.0, 16.0])

            viewer.derived_coherence_correction_check.setChecked(False)
            viewer._plot_derived()
            uncorrected_curves = viewer._plot_curves[viewer.derived_plots[1]]
            _label, (f_uncorrected, psd_uncorrected) = next(iter(uncorrected_curves.items()))
            np.testing.assert_allclose(f_uncorrected, [10.0, 20.0, 30.0])
            np.testing.assert_allclose(psd_uncorrected, [4.0, 4.0, 4.0])

            viewer.scale_spin.setValue(10.0)
            viewer.derived_transfer_factor_spin.setValue(0.5)
            viewer.derived_input_factor_spin.setValue(3.0)
            viewer._plot_derived()
            factored_curves = viewer._plot_curves[viewer.derived_plots[1]]
            _label, (f_factored, psd_factored) = next(iter(factored_curves.items()))
            np.testing.assert_allclose(f_factored, [10.0, 20.0, 30.0])
            np.testing.assert_allclose(psd_factored, [9.0, 9.0, 9.0])

            factored_curve_count = len(factored_curves)
            viewer.hold_button.setChecked(True)
            viewer.derived_input_factor_spin.setValue(4.0)
            viewer._auto_plot_derived_from_control_change()
            held_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertGreater(len(held_curves), factored_curve_count)
            viewer.hold_button.setChecked(False)

            viewer.derived_show_source_check.setChecked(True)
            viewer._plot_derived()
            psd_with_source = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any(label.startswith("待换算:") for label in psd_with_source))
            psd_before_roundtrip = {
                label: (np.asarray(curve[0], dtype=float).copy(), np.asarray(curve[1], dtype=float).copy())
                for label, curve in psd_with_source.items()
            }

            viewer.derived_result_mode_combo.setCurrentText("近似时域")
            viewer._plot_derived()
            time_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertGreater(len(time_curves), 0)

            viewer.derived_result_mode_combo.setCurrentText("PSD")
            viewer._plot_derived()
            psd_curves_after_roundtrip = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertEqual(set(psd_curves_after_roundtrip), set(psd_before_roundtrip))
            for label, (expected_f, expected_psd) in psd_before_roundtrip.items():
                actual_f, actual_psd = psd_curves_after_roundtrip[label]
                np.testing.assert_allclose(actual_f, expected_f)
                np.testing.assert_allclose(actual_psd, expected_psd)

            viewer.derived_result_mode_combo.setCurrentText("地基振动")
            viewer.derived_vc_checks["VC A"].setChecked(True)
            viewer._plot_derived()
            foundation_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertIn("VC A", foundation_curves)
            self.assertIn("VC A", viewer._plot_export_excluded[viewer.derived_plots[1]])

            vc_c_index = viewer._combo_index_for_data(
                viewer.derived_input_series_combo,
                ("vc_reference", "VC C"),
            )
            self.assertGreaterEqual(vc_c_index, 0)
            vc_frequencies, vc_velocity = _vc_reference_frequency_velocity("VC C")
            self.assertAlmostEqual(vc_frequencies[0], 1.0)
            self.assertAlmostEqual(vc_frequencies[-1], 1000.0)
            self.assertGreater(vc_frequencies.size, 10)
            np.testing.assert_allclose(vc_velocity, np.full_like(vc_velocity, 12.5))

            viewer.derived_input_series_combo.setCurrentIndex(vc_c_index)
            viewer.derived_result_mode_combo.setCurrentText("地基振动")
            viewer._plot_derived()
            vc_derived_curves = viewer._plot_curves[viewer.derived_plots[1]]
            self.assertTrue(any(label.startswith("VC C") for label in vc_derived_curves))
        finally:
            viewer.close()

    def test_vc_reference_top_to_base_force_keeps_force_psd_units(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            viewer.quantity_combo.setCurrentText("Force")
            self.assertEqual(viewer.quantity_combo.currentText(), "Force")

            center_f, _center_psd = _vc_reference_acceleration_psd("VC C")
            dense_source = _vc_reference_acceleration_psd_for_transfer_grid("VC C", center_f)
            self.assertIsNotNone(dense_source)
            source_f, _source_psd = dense_source
            expected_source = _vc_reference_acceleration_psd_for_transfer_grid("VC C", source_f)
            self.assertIsNotNone(expected_source)
            expected_f, expected_psd = expected_source
            transfer_h = np.full(source_f.shape, 2.0 + 0.0j, dtype=complex)

            result = viewer._derived_result_for_vc_reference(
                "VC C",
                None,
                None,
                None,
                source_f,
                transfer_h,
                direction=DERIVE_TOP_TO_BASE,
                regularization=0.0,
                freq_min=None,
                freq_max=None,
                input_factor=1.0,
            )

            self.assertIsNotNone(result)
            out_f, out_psd = result["psd"]
            np.testing.assert_allclose(out_f, expected_f)
            np.testing.assert_allclose(out_psd, expected_psd / 4.0)
            self.assertNotIn("foundation", result)
            self.assertNotIn("source_psd", result)
            self.assertIn("source_foundation", result)
        finally:
            viewer.close()

    def test_derived_psd_roundtrip_preserves_display_curve_with_edit_profile(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            viewer._plot_derived_result_axis(
                plot,
                "PSD",
                [
                    {
                        "label": "编辑PSD",
                        "psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                        "display_psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )
            viewer._psd_edit_points["编辑PSD"] = (
                np.array([10.0, 80.0], dtype=float),
                np.array([3.0, 3.0], dtype=float),
            )
            viewer._plot_derived_result_axis(
                plot,
                "PSD",
                [
                    {
                        "label": "编辑PSD",
                        "psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                        "display_psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )
            before = {
                label: (np.asarray(curve[0], dtype=float).copy(), np.asarray(curve[1], dtype=float).copy())
                for label, curve in viewer._plot_curves[plot].items()
            }

            viewer._plot_derived_result_axis(
                plot,
                "近似时域",
                [
                    {
                        "label": "编辑PSD",
                        "psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                        "display_psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )
            viewer._plot_derived_result_axis(
                plot,
                "PSD",
                [
                    {
                        "label": "编辑PSD",
                        "psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                        "display_psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 1.5, 0.5], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )
            after = viewer._plot_curves[plot]

            self.assertEqual(set(after), set(before))
            for label, (before_x, before_y) in before.items():
                after_x, after_y = after[label]
                np.testing.assert_allclose(after_x, before_x)
                np.testing.assert_allclose(after_y, before_y)
        finally:
            viewer.close()

    def test_current_psd_edit_button_populates_point_table_for_psd_context(self):
        viewer = AnalysisViewer(derived_only=True)
        try:
            plot = viewer.derived_plots[1]
            viewer._plot_derived_result_axis(
                plot,
                "PSD",
                [
                    {
                        "label": "待编辑PSD",
                        "psd": (
                            np.array([10.0, 20.0, 40.0, 80.0], dtype=float),
                            np.array([1.0, 2.0, 4.0, 8.0], dtype=float),
                        ),
                    }
                ],
                keep_existing=False,
            )
            viewer._active_trace[plot] = "待编辑PSD"

            viewer._initialize_psd_edit_points_from_active_curve()

            self.assertEqual(viewer._curve_point_edit_mode, "psd")
            self.assertEqual(viewer._active_psd_edit_label, "待编辑PSD")
            self.assertGreaterEqual(viewer.derived_transfer_point_table.rowCount(), 2)
            self.assertEqual(viewer.derived_curve_point_label.text(), "PSD修正点")

            viewer.derived_transfer_point_table.item(0, 1).setText("6")
            edited_f, edited_db = viewer._psd_edit_points["待编辑PSD"]

            self.assertAlmostEqual(float(edited_db[0]), 6.0)
            self.assertGreater(float(edited_f[0]), 0.0)
        finally:
            viewer.close()

    def test_imported_two_psd_curves_can_be_used_as_transfer_ratio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "two_psd.csv"
            path.write_text(
                "# python_vna_plot_export=1\n"
                "# plot_kind=psd\n"
                "base_X,base_Y,top_X,top_Y\n"
                "1,1,1,4\n"
                "2,1,2,4\n"
                "4,1,4,4\n",
                encoding="utf-8",
            )
            dataset = load_analysis_path(path, fs_hint=100.0, dataset_id=1)
            viewer = AnalysisViewer(derived_only=True)
            try:
                viewer._datasets = [dataset]
                viewer._next_dataset_id = 2
                viewer._refresh_dataset_lists()

                options = viewer._derived_transfer_options()
                psd_options = [(label, data) for label, data in options if isinstance(data, tuple) and data and data[0] == "psd_pair"]

                self.assertTrue(psd_options)
                label, data = next((item for item in psd_options if "base" in item[0] and "top" in item[0]), psd_options[0])
                viewer.derived_transfer_combo.setCurrentIndex(viewer._combo_index_for_data(viewer.derived_transfer_combo, data))
                selected = viewer._selected_derived_transfer()
                self.assertIsNotNone(selected)
                transfer = viewer._transfer_for_derived(
                    selected[0],
                    selected[1],
                    selected[2],
                    selected[3],
                    selected[4],
                    transfer_factor=1.0,
                    edit_key=tuple(data),
                )

                self.assertIsNotNone(transfer)
                frequency, values, phase_available = transfer
                np.testing.assert_allclose(frequency, [1.0, 2.0, 4.0])
                np.testing.assert_allclose(np.abs(values), [2.0, 2.0, 2.0])
                self.assertFalse(phase_available)
                _ = label
            finally:
                viewer.close()

    def test_hold_and_selection_buttons_show_feedback(self):
        viewer = AnalysisViewer()
        try:
            viewer.hold_button.setChecked(True)
            self.assertEqual(viewer.hold_button.text(), "保持:开")
            self.assertIn("保持已开启", viewer.statusBar().currentMessage())

            viewer.hold_button.setChecked(False)
            self.assertEqual(viewer.hold_button.text(), "保持:关")
            self.assertIn("保持已关闭", viewer.statusBar().currentMessage())

            viewer.series_list.addItem("one")
            viewer.series_list.addItem("two")
            for index in range(viewer.series_list.count()):
                viewer.series_list.item(index).setData(QtCore.Qt.UserRole, index)
            viewer._select_all_series()
            self.assertIn("已选择 2 个通道", viewer.statusBar().currentMessage())
            viewer._select_no_series()
            self.assertIn("已取消选择所有通道", viewer.statusBar().currentMessage())
        finally:
            viewer.close()

    def test_axis_history_range_tracking_does_not_raise(self):
        viewer = AnalysisViewer()
        try:
            plot = viewer.main_plots[0]
            viewer._remember_axis_range(plot)
            viewer._remember_axis_range(plot)
            self.assertEqual(len(viewer._axis_history[plot]), 1)
        finally:
            viewer.close()

    def test_log_auto_range_handles_flat_small_values_without_huge_floor(self):
        viewer = AnalysisViewer()
        try:
            plot = viewer.main_plots[0]
            x = np.array([10.0, 20.0, 40.0, 80.0], dtype=float)
            y = np.full(4, 2.0e-8, dtype=float)

            viewer._auto_range_plot(plot, [x], [y], log_x=True, log_y=True)

            x_range, y_range = plot.viewRange()
            self.assertGreater(x_range[0], 0.9)
            self.assertLess(x_range[1], 2.0)
            self.assertGreater(y_range[0], -9.0)
            self.assertLess(y_range[1], -7.0)
        finally:
            viewer.close()

    def test_option_changes_replot_selected_data_and_spinboxes_have_no_arrows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,3\n0.3,4\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                before = len(viewer.main_plots[0].plotItem.listDataItems())
                viewer.main_plots[0].clear()
                viewer.quantity_combo.setCurrentText("Velocity")
                after = len(viewer.main_plots[0].plotItem.listDataItems())
                viewer.main_plots[0].clear()
                viewer.fs_hint_spin.setValue(8192.0)
                after_fft_block_value_change = len(viewer.main_plots[0].plotItem.listDataItems())
                viewer.fs_hint_spin.editingFinished.emit()
                after_fft_block_editing_finished = len(viewer.main_plots[0].plotItem.listDataItems())
                self.assertGreater(before, 0)
                self.assertGreater(after, 0)
                self.assertEqual(after_fft_block_value_change, 0)
                self.assertGreater(after_fft_block_editing_finished, 0)
                for spin in (
                    viewer.scale_spin,
                    viewer.lowpass_spin,
                    viewer.highpass_spin,
                    viewer.filter_order_spin,
                ):
                    self.assertEqual(spin.buttonSymbols(), QtWidgets.QAbstractSpinBox.NoButtons)
                self.assertEqual(viewer.fs_hint_spin.suffix(), "")
            finally:
                viewer.close()

    def test_refresh_button_reloads_files_and_current_measurement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1,2\n0.1,3,4\n0.2,5,6\n", encoding="utf-8")
            session = default_session_config()
            measurement = MeasurementSet(
                sample_rate=100.0,
                time_data={
                    "t": np.array([0.0, 0.1], dtype=float),
                    "channels": {"ai0": np.array([4.0, 5.0], dtype=float)},
                },
                spectra={"f": np.array([], dtype=float), "autospectrum": {}},
                frf={},
                coherence={},
                cross_spectra={},
                correlations={},
                impulse_responses={},
                metadata={},
            )
            viewer = AnalysisViewer(
                current_measurement_provider=lambda: (measurement, session)
            )
            try:
                viewer._load_path(path)
                path.write_text("0,10,20\n0.1,30,40\n0.2,50,60\n", encoding="utf-8")

                viewer.refresh_data_sources()

                self.assertEqual(len(viewer._datasets), 2)
                current = viewer._datasets[0]
                reloaded = viewer._datasets[1]
                self.assertEqual(current.name, "Current Measurement")
                _time_s, values = reloaded.load_time_series(reloaded.series[0].channel_key)
                np.testing.assert_allclose(values, [10.0, 30.0, 50.0])
                self.assertEqual(viewer.refresh_button.text(), "刷新")
            finally:
                viewer.close()

    def test_condition_text_prefills_rename_without_renaming_until_user_edits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            path = folder / "012.csv"
            path.write_text("0,1\n0.1,2\n0.2,3\n", encoding="utf-8")
            (folder / "readme.txt").write_text("012:冲击测试\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)

                self.assertEqual(viewer.rename_edit.text(), "冲击测试")
                viewer._rename_selected_series_from_editor()
                self.assertEqual(viewer.series_list.item(0).text(), "012.csv+ch1（冲击测试）")

                viewer._rename_selected_series_confirmed()
                self.assertEqual(viewer.series_list.item(0).text(), "冲击测试（冲击测试）")

                viewer.rename_edit.setText("hammer")
                viewer._rename_selected_series_from_editor()
                self.assertEqual(viewer.series_list.item(0).text(), "hammer（冲击测试）")
            finally:
                viewer.close()

    def test_readme_panel_does_not_change_window_width(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            path = folder / "012.csv"
            path.write_text("0,1\n0.1,2\n", encoding="utf-8")
            (folder / "readme.txt").write_text("012:冲击测试\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer.resize(900, 620)
                viewer.show()
                QtWidgets.QApplication.processEvents()
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                QtWidgets.QApplication.processEvents()

                collapsed_size = viewer.size()
                collapsed_minimum_size = viewer.minimumSize()
                viewer.show_readme_button.click()
                QtWidgets.QApplication.processEvents()
                expanded_size = viewer.size()
                self.assertGreater(expanded_size.width(), collapsed_size.width())
                viewer.show_readme_button.click()
                QtWidgets.QApplication.processEvents()
                QtCore.QTimer.singleShot(0, self.app.quit)
                self.app.exec()
                QtWidgets.QApplication.processEvents()

                self.assertEqual(viewer.size(), collapsed_size)
                self.assertEqual(viewer.minimumSize(), collapsed_minimum_size)
            finally:
                viewer.close()

    def test_rename_and_factor_follow_matlab_analysis_panel_behavior(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            path.write_text("0,1\n0.1,2\n0.2,3\n", encoding="utf-8")
            viewer = AnalysisViewer()
            try:
                viewer._load_path(path)
                viewer.series_list.item(0).setSelected(True)
                self.assertEqual(viewer.rename_edit.text(), "data.csv+ch1")
                viewer.rename_edit.setText("hammer")
                viewer._rename_selected_series_from_editor()
                self.assertEqual(viewer.series_list.item(0).text(), "hammer")

                viewer.factor_edit.setText("2.5")
                viewer._set_selected_series_scale_from_editor()
                self.assertAlmostEqual(viewer._datasets[0].series[0].scale, 2.5)
                self.assertEqual(viewer.factor_edit.text(), "2.5")
                self.assertEqual(viewer.rename_edit.text(), "hammer")
                self.assertIn("(*2.5)", viewer.series_list.item(0).text())
            finally:
                viewer.close()

    def test_factor_suffix_uses_relative_to_loaded_scale(self):
        viewer = AnalysisViewer()
        try:
            dataset = AnalysisDataset(
                id=1,
                path=Path("scaled.csv"),
                name="scaled.csv",
                sample_rate=100.0,
                series=[
                    AnalysisSeries(
                        dataset_id=1,
                        channel_index=0,
                        channel_key="ch1",
                        display_name="ch1",
                        scale=2.0,
                    )
                ],
                time_s=np.array([0.0, 0.1], dtype=float),
                channels={"ch1": np.array([1.0, 2.0], dtype=float)},
            )
            viewer._datasets = [dataset]
            viewer._refresh_dataset_lists()
            viewer.series_list.item(0).setSelected(True)
            viewer.factor_edit.setText("4")
            viewer._set_selected_series_scale_from_editor()

            self.assertAlmostEqual(viewer._datasets[0].series[0].scale, 4.0)
            self.assertIn("(*2)", viewer.series_list.item(0).text())
        finally:
            viewer.close()


if __name__ == "__main__":
    unittest.main()
