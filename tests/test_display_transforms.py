from __future__ import annotations

import unittest

import numpy as np

from python_vna.display_transforms import (
    align_vector_to_values,
    legacy_frequency_int_vector,
    legacy_j_factor,
    transform_curve,
    transform_legacy_autospectrum,
)


class DisplayTransformTests(unittest.TestCase):
    def test_phase_modes_match_matlab_wrapped_and_unwrapped_meanings(self):
        values = np.exp(1j * np.deg2rad(np.array([170.0, -170.0, -160.0], dtype=float)))

        wrapped = transform_curve(values, "phase")
        unwrapped = transform_curve(values, "phase_u")

        self.assertAlmostEqual(wrapped[0], 170.0, places=6)
        self.assertAlmostEqual(wrapped[1], -170.0, places=6)
        self.assertAlmostEqual(unwrapped[1], 190.0, places=6)
        self.assertAlmostEqual(unwrapped[2], 200.0, places=6)

    def test_legacy_frequency_integration_vectors_follow_plot_vna_indices(self):
        freqs = np.array([0.0, 10.0, 20.0], dtype=float)

        displacement = legacy_frequency_int_vector(freqs, 2)
        acceleration = legacy_frequency_int_vector(freqs, 4)

        self.assertTrue(np.isnan(displacement[0]))
        self.assertTrue(np.isnan(acceleration[0]))
        np.testing.assert_allclose(displacement[1:], 1.0 / (2.0 * np.pi * freqs[1:]) ** 2)
        np.testing.assert_allclose(acceleration[1:], (2.0 * np.pi * freqs[1:]) ** 2)
        self.assertEqual(legacy_j_factor(2), -1.0j)
        self.assertEqual(legacy_j_factor(4), 1.0j)

    def test_align_vector_to_values_keeps_shortest_length(self):
        aligned = align_vector_to_values(np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0]))

        np.testing.assert_allclose(aligned, np.array([1.0, 2.0]))
        self.assertEqual(align_vector_to_values(3.0, np.array([1.0, 2.0])), 3.0)

    def test_legacy_autospectrum_applies_engineering_db_rbw_and_window_scaling(self):
        values = np.array([8.0, 18.0], dtype=float)

        db_rms = transform_legacy_autospectrum(
            values,
            "dB",
            rbw_hz=2.0,
            euscale_fac=3.0,
            db_ref=6.0,
            units_value=np.sqrt(2.0),
            wincor=4.0,
            yapcor_index=2,
        )
        rms_per_root_hz = transform_legacy_autospectrum(
            values,
            "linear_per_sqrt_hz",
            rbw_hz=2.0,
            euscale_fac=3.0,
            db_ref=6.0,
            units_value=1.0,
            wincor=4.0,
            yapcor_index=2,
        )

        np.testing.assert_allclose(
            db_rms,
            10.0 * np.log10(4.0 * ((3.0 * np.sqrt(2.0) / 6.0) ** 2) * values),
        )
        np.testing.assert_allclose(
            rms_per_root_hz,
            np.sqrt((4.0 / 2.0) * (3.0 ** 2) * values),
        )


if __name__ == "__main__":
    unittest.main()
