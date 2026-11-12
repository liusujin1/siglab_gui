from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from python_vna.app import (
    configure_live_plot_rendering,
    default_vna_path,
    load_startup_session,
    parse_args,
    resource_path,
)
from python_vna.conversion_app import parse_args as parse_conversion_args
import sys

from python_vna.daq.device_probe import (
    choose_hardware_backend, discover_hardware, probe_ni_devices_subprocess,
)
from python_vna.daq.base import BackendDevice
from python_vna.models import SavedSession
from python_vna.storage import default_session_config


class AppArgumentTests(unittest.TestCase):
    def test_backend_defaults_to_ni(self):
        args = parse_args([])

        self.assertEqual(args.backend, "ni")

    def test_simulated_backend_can_still_be_selected_explicitly(self):
        args = parse_args(["--backend", "simulated"])

        self.assertEqual(args.backend, "simulated")

    def test_probe_devices_hidden_argument_is_supported(self):
        args = parse_args(["--probe-ni-devices-json"])

        self.assertTrue(args.probe_ni_devices_json)

    def test_default_vna_path_points_to_legacy_default_file(self):
        self.assertEqual(default_vna_path(), resource_path("dsa/vna/default.vna"))

    def test_startup_loads_default_vna_when_present(self):
        fake_path = Path("D:/fake/default.vna")
        loaded = SavedSession(config=default_session_config(), measurement=None, source_path=fake_path)
        with mock.patch.object(Path, "exists", return_value=True), mock.patch(
            "python_vna.app.load_legacy_vna",
            return_value=loaded,
        ) as load_legacy:
            result = load_startup_session(fake_path)

        self.assertIs(result, loaded)
        load_legacy.assert_called_once_with(fake_path)

    def test_startup_falls_back_when_default_vna_missing(self):
        with mock.patch.object(Path, "exists", return_value=False):
            self.assertIsNone(load_startup_session(Path("D:/fake/default.vna")))

    def test_live_plot_rendering_uses_opengl_by_default(self):
        fake_pg = mock.Mock()
        with mock.patch("python_vna.app.require", return_value=fake_pg), mock.patch.dict(
            "os.environ", {}, clear=True
        ):
            enabled = configure_live_plot_rendering()

        self.assertTrue(enabled)
        fake_pg.setConfigOptions.assert_called_once_with(useOpenGL=True, antialias=True)

    def test_live_plot_rendering_can_disable_opengl(self):
        fake_pg = mock.Mock()
        with mock.patch("python_vna.app.require", return_value=fake_pg), mock.patch.dict(
            "os.environ", {"PYTHON_VNA_DISABLE_OPENGL": "1"}, clear=True
        ):
            enabled = configure_live_plot_rendering()

        self.assertFalse(enabled)
        fake_pg.setConfigOptions.assert_called_once_with(useOpenGL=False, antialias=False)

    def test_conversion_app_accepts_startup_paths(self):
        args = parse_conversion_args(["one.vna", "two.vna"])

        self.assertEqual(args.paths, ["one.vna", "two.vna"])

    def test_device_probe_subprocess_parses_backend_devices(self):
        payload = (
            '[{"name":"Dev1","product_type":"NI USB-4431",'
            '"ai_channels":["Dev1/ai0"],"ao_channels":["Dev1/ao0"],'
            '"capability":{"supports_iepe":true,"supports_output":true,'
            '"supports_analog_trigger":true,"supports_pretrigger":true,'
            '"max_ai_sample_rate":102400.0,"max_ao_sample_rate":96000.0}}]'
        )
        completed = mock.Mock(returncode=0, stdout=payload, stderr="")

        with mock.patch("python_vna.daq.device_probe.subprocess.run", return_value=completed):
            devices = probe_ni_devices_subprocess(timeout_s=1.0)

        self.assertEqual(devices[0].name, "Dev1")
        self.assertTrue(devices[0].capability.supports_iepe)

    def test_device_probe_subprocess_uses_exe_argument_when_frozen(self):
        completed = mock.Mock(returncode=0, stdout="[]", stderr="")

        with mock.patch.object(sys, "frozen", True, create=True), mock.patch(
            "python_vna.daq.device_probe.subprocess.run",
            return_value=completed,
        ) as run:
            devices = probe_ni_devices_subprocess(timeout_s=1.0)

        self.assertEqual(devices, [])
        self.assertEqual(run.call_args.args[0], [sys.executable, "--probe-ni-devices-json"])

    def test_hardware_choice_retains_preference_when_both_present(self):
        snapshot = {"ni_devices": [BackendDevice("Dev1", "NI USB-4431")],
                    "usb_devices": [{"index": 0}]}
        self.assertEqual(choose_hardware_backend(snapshot, preferred="usb", current="ni"), "usb")
        self.assertEqual(choose_hardware_backend(snapshot, preferred="ni", current="usb"), "ni")
        self.assertEqual(choose_hardware_backend(snapshot, current="usb"), "usb")
        self.assertEqual(choose_hardware_backend(snapshot), "ni")

    def test_hardware_choice_uses_only_available_saved_usb_index(self):
        snapshot = {"ni_devices": [], "usb_devices": [{"index": 0}]}
        self.assertEqual(choose_hardware_backend(snapshot, preferred="ni"), "usb")
        self.assertIsNone(choose_hardware_backend(snapshot, usb_index=1))
        self.assertIsNone(choose_hardware_backend({}))

    def test_hardware_discovery_survives_missing_ni_driver(self):
        with mock.patch("python_vna.daq.usbdaq.enumerate_candidates",
                        return_value=[{"index": 0}]) as usb, mock.patch(
                "python_vna.daq.device_probe.probe_ni_devices_subprocess",
                side_effect=RuntimeError("NI driver unavailable")):
            result = discover_hardware()
        usb.assert_called_once_with()
        self.assertEqual(result["usb_devices"], [{"index": 0}])
        self.assertEqual(result["ni_devices"], [])
        self.assertEqual(result["errors"], {"ni": "NI driver unavailable"})

    def test_hardware_discovery_survives_missing_usb_driver(self):
        device = BackendDevice("Dev1", "NI USB-4431")
        with mock.patch("python_vna.daq.usbdaq.enumerate_candidates",
                        side_effect=OSError("USB driver unavailable")), mock.patch(
                "python_vna.daq.device_probe.probe_ni_devices_subprocess", return_value=[device]):
            result = discover_hardware()
        self.assertEqual(result["ni_devices"], [device])
        self.assertEqual(result["usb_devices"], [])
        self.assertEqual(result["errors"], {"usb": "USB driver unavailable"})

    def test_hardware_discovery_does_not_mistake_reference_6000_for_vibration_device(self):
        devices = [BackendDevice("Dev4", "USB-6000"), BackendDevice("Dev1", "USB-4431")]
        with mock.patch("python_vna.daq.usbdaq.enumerate_candidates", return_value=[]), mock.patch(
                "python_vna.daq.device_probe.probe_ni_devices_subprocess", return_value=devices):
            result = discover_hardware()
        self.assertEqual(result["ni_devices"], devices[1:])


if __name__ == "__main__":
    unittest.main()
