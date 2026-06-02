from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from python_vna.app import default_vna_path, load_startup_session, parse_args
from python_vna.conversion_app import parse_args as parse_conversion_args
from python_vna.models import SavedSession
from python_vna.storage import default_session_config


class AppArgumentTests(unittest.TestCase):
    def test_backend_defaults_to_ni(self):
        args = parse_args([])

        self.assertEqual(args.backend, "ni")

    def test_simulated_backend_can_still_be_selected_explicitly(self):
        args = parse_args(["--backend", "simulated"])

        self.assertEqual(args.backend, "simulated")

    def test_default_vna_path_points_to_legacy_default_file(self):
        self.assertEqual(default_vna_path(), Path("D:/SynologyDrive/codex/vna/dsa/vna/default.vna"))

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


if __name__ == "__main__":
    unittest.main()
