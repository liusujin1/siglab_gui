from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from python_vna.app import default_vna_path, load_startup_session, parse_args, resource_path
from python_vna.conversion_app import parse_args as parse_conversion_args
import sys

from python_vna.daq.device_probe import probe_ni_devices_subprocess
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


if __name__ == "__main__":
    unittest.main()
