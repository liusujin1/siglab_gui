from __future__ import annotations

import unittest

import numpy as np

from python_vna.diagnostic.processing_workflow import (
    CurveDescriptor,
    ProcessingRecipe,
    parse_optional_number,
    validate_control_points,
    validate_processing_task,
)


class ProcessingWorkflowTests(unittest.TestCase):
    def test_parse_optional_number_distinguishes_blank_and_invalid(self):
        self.assertEqual(parse_optional_number("", field="frequency_min"), (None, None))
        value, issue = parse_optional_number("abc", field="frequency_min")
        self.assertIsNone(value)
        self.assertEqual(issue.code, "invalid_number")

    def test_validation_reports_range_overlap_and_discarded_points(self):
        report = validate_processing_task(
            transfer_frequency_hz=[2.0, 4.0, 8.0, 16.0],
            transfer_values=[1.0, 1.0, 1.0, 1.0],
            target_frequency_hz=[1.0, 2.0, 4.0, 8.0, 32.0],
            requested_min_hz=3.0,
            requested_max_hz=12.0,
            direction="base_to_top",
            regularization_floor=0.0,
            target_unit="g^2/Hz",
            phase_available=True,
            result_mode="PSD",
            allow_dimensionless=False,
        )
        self.assertTrue(report.can_run)
        self.assertEqual(report.effective_frequency_min_hz, 3.0)
        self.assertEqual(report.effective_frequency_max_hz, 12.0)
        self.assertEqual(report.valid_points, 2)
        self.assertEqual(report.discarded_points, 3)

    def test_validation_blocks_reversed_range_zero_inverse_and_unconfirmed_unit(self):
        report = validate_processing_task(
            transfer_frequency_hz=[1.0, 2.0, 3.0],
            transfer_values=[1.0, 0.0, 1.0],
            target_frequency_hz=[1.0, 2.0, 3.0],
            requested_min_hz=3.0,
            requested_max_hz=2.0,
            direction="top_to_base",
            regularization_floor=0.0,
            target_unit="",
            phase_available=False,
            result_mode="近似时域",
            allow_dimensionless=False,
        )
        codes = {issue.code for issue in report.errors}
        self.assertIn("reversed_frequency_range", codes)
        self.assertIn("near_zero_transfer", codes)
        self.assertIn("unit_confirmation_required", codes)
        self.assertIn("statistical_time_only", {issue.code for issue in report.warnings})

    def test_control_points_reject_duplicate_frequencies(self):
        frequency, values, issues = validate_control_points([1.0, 2.0, 2.0], [0.0, 1.0, 2.0])
        self.assertEqual(frequency.tolist(), [1.0, 2.0])
        self.assertEqual(values.tolist(), [0.0, 1.0])
        self.assertIn("duplicate_frequency", {issue.code for issue in issues})

    def test_recipe_serialization_keeps_reproducibility_fields(self):
        descriptor = CurveDescriptor("H", "transfer", frequency_min_hz=1.0, frequency_max_hz=100.0, point_count=100)
        recipe = ProcessingRecipe(
            transfer=descriptor,
            targets=(CurveDescriptor("target", "series", unit="g", sample_rate_hz=1000.0),),
            direction="top_to_base",
            transfer_factor=2.0,
            input_factor=3.0,
            frequency_min_hz=2.0,
            frequency_max_hz=80.0,
            regularization_floor=1e-6,
            quantity="Acceleration",
            result_mode="PSD",
            coherence_correction=True,
            allow_dimensionless=False,
            interpolation={"frequency_resolution_hz": 0.5},
            curve_edits={"frequency_hz": [1.0, 100.0], "values_db": [0.0, 0.0]},
        )
        payload = recipe.to_dict()
        self.assertEqual(payload["direction"], "top_to_base")
        self.assertEqual(payload["targets"][0]["sample_rate_hz"], 1000.0)
        self.assertEqual(payload["interpolation"]["frequency_resolution_hz"], 0.5)


if __name__ == "__main__":
    unittest.main()
