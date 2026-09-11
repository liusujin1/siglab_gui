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
    def test_backup_cleanup_failure_does_not_report_applied_update_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "app.txt").write_text("original")
            (staging / "app.txt").write_text("new")
            with mock.patch.object(updater.shutil, "rmtree", side_effect=PermissionError("backup busy")):
                updater.apply_update(staging, target)
            self.assertEqual((target / "app.txt").read_text(), "new")
            self.assertIn("backup busy", (target / "UPDATE_LOG.txt").read_text())
            backup = next(root.glob(".python_vna_rollback_*"))
            self.assertTrue((backup / "RECOVERY.json").exists())

    def test_backup_cleanup_failure_does_not_mask_original_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "app.txt").write_text("original")
            (staging / "app.txt").write_text("new")
            with mock.patch.object(updater.shutil, "copy2", side_effect=OSError("backup full")), mock.patch.object(
                updater.shutil, "rmtree", side_effect=PermissionError("cleanup busy")
            ):
                with self.assertRaisesRegex(OSError, "backup full"):
                    updater.apply_update(staging, target)
            self.assertEqual((target / "app.txt").read_text(), "original")

    def test_updater_failure_rolls_back_and_reports_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "suite"
            target.mkdir()
            (target / "app.txt").write_text("original")
            archive = root / "update.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("app.txt", "new")
            manifest = {
                "latest": "9.0.1",
                "full": {"url": archive.as_uri(), "sha256": updater.hashlib.sha256(archive.read_bytes()).hexdigest()},
            }

            def fail_copy(source, destination, **kwargs):
                destination.write_text("partial")
                raise OSError("disk full")

            with mock.patch.object(updater, "fetch_manifest", return_value=manifest), mock.patch.object(
                updater, "ProgressReporter"
            ), mock.patch.object(updater, "show_error") as error, mock.patch.object(
                updater, "restart_app"
            ) as restart, mock.patch.object(updater, "copy_file_with_retry", side_effect=fail_copy):
                result = updater.main([
                    "--manifest-url", "file:///unused.json", "--current-version", "9.0.0",
                    "--target-dir", str(target), "--wait-seconds", "0",
                ])
            self.assertEqual(result, 1)
            self.assertEqual((target / "app.txt").read_text(), "original")
            self.assertIn("disk full", (target / "UPDATE_LOG.txt").read_text(encoding="utf-8"))
            error.assert_called_once()
            restart.assert_not_called()

    def test_transaction_restores_deleted_and_partially_written_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "old.txt").write_text("old")
            (target / "app.txt").write_text("original")
            (staging / "UPDATE_REMOVED_FILES.txt").write_text("old.txt\n")
            (staging / "app.txt").write_text("new")

            def fail_copy(source, destination, **kwargs):
                destination.write_text("partial")
                raise OSError("disk full")

            with mock.patch.object(updater, "copy_file_with_retry", side_effect=fail_copy):
                with self.assertRaisesRegex(OSError, "disk full"):
                    updater.apply_update(staging, target)
            self.assertEqual((target / "old.txt").read_text(), "old")
            self.assertEqual((target / "app.txt").read_text(), "original")
            self.assertFalse((target / ".python_vna_update.lock").exists())
            self.assertEqual(list(root.glob(".python_vna_rollback_*")), [])

    def test_transaction_removes_new_partial_file_and_created_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            (staging / "new" / "nested").mkdir(parents=True)
            (staging / "new" / "nested" / "app.txt").write_text("new")

            def fail_copy(source, destination, **kwargs):
                destination.write_text("partial")
                raise OSError("write failed")

            with mock.patch.object(updater, "copy_file_with_retry", side_effect=fail_copy):
                with self.assertRaises(OSError):
                    updater.apply_update(staging, target)
            self.assertEqual(list(target.iterdir()), [])

    def test_transaction_cancel_after_deletion_restores_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "old.txt").write_text("old")
            (staging / "UPDATE_REMOVED_FILES.txt").write_text("old.txt\n")
            progress = mock.Mock()
            progress.set_progress.side_effect = updater.UpdateCancelled("cancel")
            with self.assertRaises(updater.UpdateCancelled):
                updater.apply_update(staging, target, progress=progress)
            self.assertEqual((target / "old.txt").read_text(), "old")

    def test_transaction_validates_entire_removed_list_before_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "old.txt").write_text("old")
            (staging / "UPDATE_REMOVED_FILES.txt").write_text("old.txt\n../outside.txt\n")
            with self.assertRaisesRegex(RuntimeError, "escapes update root"):
                updater.apply_update(staging, target)
            self.assertEqual((target / "old.txt").read_text(), "old")

    def test_transaction_preserves_backup_if_rollback_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "app.txt").write_text("original")
            (staging / "app.txt").write_text("new")
            real_copy = updater.shutil.copy2

            def fail_restore(source, destination, **kwargs):
                if Path(source).parent.name.startswith(".python_vna_rollback_"):
                    raise PermissionError("locked")
                return real_copy(source, destination, **kwargs)

            with mock.patch.object(updater, "copy_file_with_retry", side_effect=OSError("write failed")), mock.patch.object(
                updater.shutil, "copy2", side_effect=fail_restore
            ):
                with self.assertRaisesRegex(RuntimeError, "rollback incomplete"):
                    updater.apply_update(staging, target)
            backups = list(root.glob(".python_vna_rollback_*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "0").read_text(), "original")
            self.assertIn(str(target / "app.txt"), json.loads((backups[0] / "RECOVERY.json").read_text()))
            self.assertTrue((target / ".python_vna_update.lock").exists())

    def test_transaction_backup_failure_does_not_mutate_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "app.txt").write_text("original")
            (staging / "app.txt").write_text("new")
            with mock.patch.object(updater.shutil, "copy2", side_effect=OSError("backup full")):
                with self.assertRaisesRegex(OSError, "backup full"):
                    updater.apply_update(staging, target)
            self.assertEqual((target / "app.txt").read_text(), "original")
            self.assertEqual(list(root.glob(".python_vna_rollback_*")), [])

    def test_transaction_cannot_replace_or_remove_its_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            lock_name = ".python_vna_update.lock"
            (staging / lock_name).write_text("malicious")
            with self.assertRaisesRegex(RuntimeError, "replace the update lock"):
                updater.apply_update(staging, target)
            (staging / lock_name).unlink()
            (staging / "UPDATE_REMOVED_FILES.txt").write_text(lock_name)
            with self.assertRaisesRegex(RuntimeError, "remove the update lock"):
                updater.apply_update(staging, target)

    def test_transaction_late_cancellation_restores_replaced_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "app.txt").write_text("original")
            (staging / "app.txt").write_text("new")
            progress = mock.Mock()

            def cancel_after_apply(*args):
                self.assertEqual((target / "app.txt").read_text(), "new")
                progress.check_cancelled.side_effect = updater.UpdateCancelled("late cancel")

            progress.set_progress.side_effect = cancel_after_apply
            with self.assertRaises(updater.UpdateCancelled):
                updater.apply_update(staging, target, progress=progress)
            self.assertEqual((target / "app.txt").read_text(), "original")

    def test_transaction_lock_prevents_concurrent_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            lock = target / ".python_vna_update.lock"
            lock.write_text("other process")
            with self.assertRaisesRegex(RuntimeError, "Another update"):
                updater.apply_update(target / "unused", target)
            self.assertEqual(lock.read_text(), "other process")

    def test_transaction_success_deletes_replaces_and_protects_running_updater(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, staging = root / "suite", root / "staging"
            target.mkdir()
            staging.mkdir()
            (target / "old.txt").write_text("old")
            (target / "runner.exe").write_text("running")
            (staging / "UPDATE_REMOVED_FILES.txt").write_text("old.txt\nrunner.exe\n")
            (staging / "app.txt").write_text("new")
            (staging / "runner.exe").write_text("replacement")
            updater.apply_update(staging, target, skip_names={"RUNNER.EXE"})
            self.assertFalse((target / "old.txt").exists())
            self.assertEqual((target / "app.txt").read_text(), "new")
            self.assertEqual((target / "runner.exe").read_text(), "running")
            self.assertFalse((target / "UPDATE_REMOVED_FILES.txt").exists())

    def test_restart_cannot_escape_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "suite"
            target.mkdir()
            outside = Path(tmp) / "outside.exe"
            outside.touch()
            for name in (str(outside), "../outside.exe"):
                with self.subTest(name=name), mock.patch.object(updater.subprocess, "Popen") as launch:
                    with self.assertRaisesRegex(RuntimeError, "escapes update root"):
                        updater.restart_app(target, name)
                    launch.assert_not_called()

    def test_restart_uses_existing_application_inside_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            executable = target / "VIanalysis.exe"
            executable.touch()
            with mock.patch.object(updater.subprocess, "Popen") as launch:
                updater.restart_app(target, executable.name)
                launch.assert_called_once_with([str(executable.resolve())], cwd=str(target), close_fds=True)

    def test_removed_file_list_cannot_escape_to_similarly_named_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "suite"
            sibling = root / "suite_old"
            staging = root / "staging"
            target.mkdir()
            sibling.mkdir()
            staging.mkdir()
            sibling_file = sibling / "keep.txt"
            sibling_file.write_text("keep", encoding="utf-8")
            (staging / "UPDATE_REMOVED_FILES.txt").write_text(
                "../suite_old/keep.txt\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "escapes update root"):
                updater.apply_removed_files(staging, target)

            self.assertTrue(sibling_file.exists())

    def test_zip_extraction_rejects_parent_directory_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "unsafe.zip"
            destination = root / "staging"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../outside.txt", "unsafe")

            with self.assertRaisesRegex(RuntimeError, "escapes update root"):
                updater.extract_archive(archive, destination, "zip")

            self.assertFalse((root / "outside.txt").exists())

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
