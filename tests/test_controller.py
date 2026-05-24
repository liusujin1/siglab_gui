from __future__ import annotations

import unittest

from python_vna.controller import VnaController
from python_vna.daq import BackendCapability, BackendDevice


class ControllerDeviceSelectionTests(unittest.TestCase):
    def test_preferred_device_chooses_usb4431_ahead_of_usb6000(self):
        devices = [
            BackendDevice(
                name="Dev1",
                product_type="NI USB-6000",
                ai_channels=["Dev1/ai0"],
                ao_channels=[],
            ),
            BackendDevice(
                name="Dev2",
                product_type="NI USB-4431",
                ai_channels=["Dev2/ai0", "Dev2/ai1", "Dev2/ai2", "Dev2/ai3"],
                ao_channels=["Dev2/ao0"],
                capability=BackendCapability(
                    supports_iepe=True,
                    supports_output=True,
                ),
            ),
        ]

        self.assertEqual(VnaController.preferred_device(devices), "Dev2")

    def test_preferred_device_uses_usb4431_capabilities_as_fallback(self):
        devices = [
            BackendDevice(
                name="Dev1",
                product_type="NI USB-6000",
                ai_channels=["Dev1/ai0"],
                ao_channels=[],
            ),
            BackendDevice(
                name="DevX",
                product_type="NI Sound and Vibration Device",
                ai_channels=["DevX/ai0", "DevX/ai1", "DevX/ai2", "DevX/ai3"],
                ao_channels=["DevX/ao0"],
                capability=BackendCapability(
                    supports_iepe=True,
                    supports_output=True,
                ),
            ),
        ]

        self.assertEqual(VnaController.preferred_device(devices), "DevX")


if __name__ == "__main__":
    unittest.main()
