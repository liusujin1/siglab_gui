from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from python_vna.update_client import (
    cleanup_stale_updater_runner,
    config_path,
    launch_updater,
    load_update_settings,
    select_update,
)
from python_vna import updater
from python_vna.updater import main as updater_main


class UpdateClientTests(unittest.TestCase):
    def test_load_update_settings_from_suite_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path(root).write_text(
                json.dumps(
                    {
                        "manifest_url": "https://nas.example.com/pythonvna/manifest.json",
                        "channel": "stable",
                    }
                ),
                encoding="utf-8",
            )

            settings = load_update_settings(root)

        self.assertIsNotNone(settings)
        assert settings is not None
        self.assertEqual(settings.manifest_url, "https://nas.example.com/pythonvna/manifest.json")
        self.assertEqual(settings.channel, "stable")

    def test_select_update_prefers_matching_incremental_package(self) -> None:
        manifest = {
            "latest": "3.1.5",
            "full": {
                "url": "PythonVNA_Suite_v3.1.5.7z",
                "sha256": "f" * 64,
                "size": 100,
                "archive_type": "7z",
            },
            "updates": [
                {
                    "from": "3.1.4",
                    "to": "3.1.5",
                    "url": "PythonVNA_Update_v3.1.4_to_v3.1.5.zip",
                    "sha256": "a" * 64,
                    "size": 20,
                    "archive_type": "zip",
                }
            ],
        }

        decision = select_update(
            manifest,
            current_version="3.1.4",
            manifest_url="https://nas.example.com/pythonvna/manifest.json",
        )

        self.assertTrue(decision.available)
        self.assertIsNotNone(decision.package)
        assert decision.package is not None
        self.assertEqual(decision.package.kind, "incremental")
        self.assertEqual(
            decision.package.url,
            "https://nas.example.com/pythonvna/PythonVNA_Update_v3.1.4_to_v3.1.5.zip",
        )

    def test_select_update_falls_back_to_full_package(self) -> None:
        manifest = {
            "latest": "3.1.6",
            "full": {
                "url": "PythonVNA_Suite_v3.1.6.7z",
                "sha256": "b" * 64,
                "size": 100,
                "archive_type": "7z",
            },
            "updates": [],
        }

        decision = select_update(manifest, current_version="3.1.4")

        self.assertTrue(decision.available)
        self.assertIsNotNone(decision.package)
        assert decision.package is not None
        self.assertEqual(decision.package.kind, "full")

    def test_updater_applies_incremental_zip_without_copying_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "suite"
            target.mkdir()
            (target / "VERSION.txt").write_text("old", encoding="utf-8")
            package_dir = root / "package"
            package_dir.mkdir()
            (package_dir / "VERSION.txt").write_text("new", encoding="utf-8")
            (package_dir / "UPDATE_INFO.txt").write_text("metadata", encoding="utf-8")
            archive = root / "update.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for path in package_dir.rglob("*"):
                    zf.write(path, path.relative_to(package_dir))
            import hashlib

            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "latest": "3.1.5",
                        "updates": [
                            {
                                "from": "3.1.4",
                                "to": "3.1.5",
                                "url": archive.as_uri(),
                                "sha256": digest,
                                "archive_type": "zip",
                                "size": archive.stat().st_size,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = updater_main(
                [
                    "--manifest-url",
                    manifest.as_uri(),
                    "--current-version",
                    "3.1.4",
                    "--target-dir",
                    str(target),
                    "--wait-seconds",
                    "0",
                ]
            )

            self.assertEqual(result, 0)
            self.assertEqual((target / "VERSION.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse((target / "UPDATE_INFO.txt").exists())

    def test_updater_falls_back_to_full_zip_when_no_incremental_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "suite"
            target.mkdir()
            (target / "VERSION.txt").write_text("old", encoding="utf-8")
            package_root = root / "PythonVNA_Suite_v3.2.1"
            package_root.mkdir()
            (package_root / "VERSION.txt").write_text("new full", encoding="utf-8")
            (package_root / "VIanalysis.exe").write_text("new app", encoding="utf-8")
            archive = root / "full.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for path in package_root.rglob("*"):
                    zf.write(path, path.relative_to(root))
            import hashlib

            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "latest": "3.2.1",
                        "full": {
                            "url": archive.as_uri(),
                            "sha256": digest,
                            "archive_type": "zip",
                            "size": archive.stat().st_size,
                        },
                        "updates": [
                            {
                                "from": "3.2.0",
                                "to": "3.2.1",
                                "url": "https://example.invalid/update.zip",
                                "sha256": "a" * 64,
                                "archive_type": "zip",
                                "size": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = updater_main(
                [
                    "--manifest-url",
                    manifest.as_uri(),
                    "--current-version",
                    "3.1.8",
                    "--target-dir",
                    str(target),
                    "--wait-seconds",
                    "0",
                ]
            )

            self.assertEqual(result, 0)
            self.assertEqual((target / "VERSION.txt").read_text(encoding="utf-8"), "new full")
            self.assertEqual((target / "VIanalysis.exe").read_text(encoding="utf-8"), "new app")

    def test_copy_tree_overlay_skips_running_updater_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "PythonVNAUpdater.exe").write_text("new updater", encoding="utf-8")
            (source / "VIanalysis.exe").write_text("new app", encoding="utf-8")
            (target / "PythonVNAUpdater.exe").write_text("old updater", encoding="utf-8")
            (target / "VIanalysis.exe").write_text("old app", encoding="utf-8")

            updater.copy_tree_overlay(
                source,
                target,
                skip_names={"PythonVNAUpdater.exe"},
            )

            self.assertEqual((target / "PythonVNAUpdater.exe").read_text(encoding="utf-8"), "old updater")
            self.assertEqual((target / "VIanalysis.exe").read_text(encoding="utf-8"), "new app")

    def test_cleanup_stale_updater_runner_removes_unused_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = root / "PythonVNAUpdaterRunner.exe"
            runner.write_text("stale runner", encoding="utf-8")

            cleanup_stale_updater_runner(root)

            if os.name == "nt":
                self.assertFalse(runner.exists())
            else:
                self.assertTrue(runner.exists())

    def test_should_cleanup_runner_only_matches_frozen_runner(self) -> None:
        runner = Path("C:/PythonVNA/PythonVNAUpdaterRunner.exe")
        updater_exe = Path("C:/PythonVNA/PythonVNAUpdater.exe")

        with mock.patch.object(updater.os, "name", "nt"), mock.patch.object(
            updater.sys, "frozen", True, create=True
        ):
            self.assertTrue(updater.should_cleanup_runner(runner))
            self.assertFalse(updater.should_cleanup_runner(updater_exe))

    def test_launch_updater_uses_isolated_runtime_when_frozen_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            updater_exe = root / "PythonVNAUpdater.exe"
            updater_exe.write_text("updater", encoding="utf-8")
            internal = root / "_internal"
            internal.mkdir()
            (internal / "keyword.pyc").write_text("runtime", encoding="utf-8")

            with mock.patch("python_vna.update_client.os.name", "nt"), mock.patch.object(
                sys, "frozen", True, create=True
            ), mock.patch("python_vna.update_client.subprocess.Popen") as popen:
                launch_updater(
                    manifest_url="https://example.com/manifest.json",
                    current_version="3.1.14",
                    target_dir=root,
                    restart_executable="VIanalysis.exe",
                )

            args = popen.call_args.args[0]
            cwd = Path(popen.call_args.kwargs["cwd"])
            self.assertEqual(Path(args[0]).parent, cwd)
            self.assertEqual(Path(args[0]).name, "PythonVNAUpdaterRunner.exe")
            self.assertIn("--cleanup-root", args)
            self.assertTrue((cwd / "_internal" / "keyword.pyc").exists())
            self.assertIn("--restart", args)

    def test_copy_file_with_retry_reports_progress_while_waiting_for_unlock(self) -> None:
        source = Path("C:/tmp/source.bin")
        target = Path("C:/tmp/target.bin")
        events: list[tuple[int, int, str]] = []

        class DummyProgress:
            def set_progress(self, current: int, total: int, text: str) -> None:
                events.append((current, total, text))

            def set_busy(self, text: str) -> None:
                events.append((0, 0, text))

        with mock.patch("python_vna.updater.shutil.copy2", side_effect=[PermissionError("busy"), None]), mock.patch(
            "python_vna.updater.time.sleep"
        ):
            updater.copy_file_with_retry(
                source,
                target,
                attempts=2,
                progress=DummyProgress(),
                current=3,
                total=10,
                display_path="_internal/keyword.pyc",
            )

        self.assertTrue(events)
        self.assertIn("等待文件释放", events[0][2])
        self.assertIn("_internal/keyword.pyc", events[0][2])


if __name__ == "__main__":
    unittest.main()
