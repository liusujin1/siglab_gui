from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
