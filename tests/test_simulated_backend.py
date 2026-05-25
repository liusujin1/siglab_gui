from __future__ import annotations

import unittest

import numpy as np

from python_vna.controller import VnaController
from python_vna.daq import SimulatedDaqBackend
from python_vna.storage import default_session_config


class SimulatedBackendTests(unittest.TestCase):
    def test_simulated_backend_produces_frames(self):
        backend = SimulatedDaqBackend()
        controller = VnaController(backend, default_session_config())
        controller.configure()
        controller.start()
        measurement = controller.read_and_process()
        controller.stop()
        controller.close()

        self.assertEqual(measurement.sample_rate, 2560.0)
        self.assertIn("ai0", measurement.time_data["channels"])
        self.assertTrue(measurement.spectra["f"].shape[0] > 0)

    def test_simulated_backend_outputs_voltage_not_engineering_scaled_data(self):
        baseline_session = default_session_config()
        scaled_session = default_session_config()
        scaled_session.ai_channels[0].sensitivity = 800.0

        baseline = SimulatedDaqBackend()
        scaled = SimulatedDaqBackend()
        baseline.configure(baseline_session)
        scaled.configure(scaled_session)
        baseline.start()
        scaled.start()
        baseline_frame = baseline.read_frame()
        scaled_frame = scaled.read_frame()
        baseline.stop()
        scaled.stop()
        baseline.close()
        scaled.close()

        np.testing.assert_allclose(scaled_frame.data[0], baseline_frame.data[0])


if __name__ == "__main__":
    unittest.main()
