from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "vna_suite.json"


def _matches(path: str, pattern: str) -> bool:
    normalized_path = path.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/")
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3].rstrip("/")
        if normalized_path == prefix or normalized_path.startswith(f"{prefix}/"):
            return True
    return fnmatch.fnmatchcase(normalized_path.lower(), normalized_pattern.lower())


class RepositoryConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_manifest_assigns_every_python_source_and_test_exactly_once(self):
        areas = self.manifest["areas"]
        paths = sorted(
            path.relative_to(ROOT).as_posix()
            for parent in (ROOT / "python_vna", ROOT / "tests")
            for path in parent.rglob("*.py")
        )

        assignments = {
            path: [
                area["id"]
                for area in areas
                if any(_matches(path, pattern) for pattern in area["patterns"])
            ]
            for path in paths
        }

        self.assertEqual(
            {path: owners for path, owners in assignments.items() if len(owners) != 1},
            {},
        )

    def test_manifest_required_paths_exist(self):
        required = {
            self.manifest["version_files"]["project"],
            self.manifest["version_files"]["package"],
            self.manifest["build"]["spec"],
            self.manifest["build"]["script"],
            self.manifest["build"]["preflight"],
            self.manifest["build"]["archive_script"],
        }
        for area in self.manifest["areas"]:
            required.update(area.get("tests", []))
            if "entrypoint" in area:
                required.add(area["entrypoint"])

        self.assertEqual(
            sorted(path for path in required if not (ROOT / path).exists()),
            [],
        )

    def test_canonical_version_files_match(self):
        pyproject = (ROOT / self.manifest["version_files"]["project"]).read_text(
            encoding="utf-8"
        )
        package = (ROOT / self.manifest["version_files"]["package"]).read_text(
            encoding="utf-8"
        )
        project_version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
        package_version = re.search(
            r'^__version__\s*=\s*"([^"]+)"', package, re.MULTILINE
        )

        self.assertIsNotNone(project_version)
        self.assertIsNotNone(package_version)
        self.assertEqual(project_version.group(1), package_version.group(1))

    def test_normal_build_and_publish_scripts_do_not_copy_legacy_worktrees(self):
        canonical_entrypoints = [
            ROOT / "build_vna_suite_release.bat",
            ROOT / "scripts" / "build_vna_suite_update.ps1",
            ROOT / "scripts" / "publish_vna_suite_update.ps1",
        ]
        for path in canonical_entrypoints:
            with self.subTest(path=path.name):
                self.assertNotIn(
                    "sync_worktrees_and_build_suite.ps1",
                    path.read_text(encoding="utf-8"),
                )

    def test_legacy_sync_requires_explicit_migration_switch(self):
        text = (ROOT / "scripts" / "sync_worktrees_and_build_suite.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("LegacyMigration", text)
        self.assertIn("LegacyMigration is required", text)

    def test_publish_repairs_a_missing_full_archive_from_release_folder(self):
        text = (ROOT / "scripts" / "publish_vna_suite_update.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("ensure_vna_suite_archive.ps1", text)
        self.assertRegex(
            text,
            r"&\s+\$ensureArchiveScript\s+-ReleasePath\s+\$latestRelease",
        )

    def test_publish_prefers_compact_7z_incremental_archives(self):
        text = (ROOT / "scripts" / "publish_vna_suite_update.ps1").read_text(
            encoding="utf-8"
        )
        helper_start = text.index("function Get-IncrementalArchivePath")
        helper_end = text.index("function Get-ReleaseItemVersion", helper_start)
        helper = text[helper_start:helper_end]

        self.assertLess(helper.index('"$archiveStem.7z"'), helper.index('"$archiveStem.zip"'))
        self.assertIn("Get-IncrementalArchivePath -BaseVersion", text)
        self.assertNotIn("'-SkipSevenZip'", text)

    def test_vianalysis_bundle_collects_scipy_array_api_compat_modules(self):
        text = (ROOT / "PythonVNA_Suite.spec").read_text(encoding="utf-8")
        self.assertIn(
            'SCIPY_ARRAY_API_HIDDENIMPORTS = collect_submodules("scipy._external.array_api_compat")',
            text,
        )
        analysis_start = text.index("analysis_vianalysis = _build_analysis(")
        analysis_end = text.index("pyz_vianalysis =", analysis_start)
        self.assertIn("SCIPY_ARRAY_API_HIDDENIMPORTS", text[analysis_start:analysis_end])

    def test_build_isolates_pyinstaller_from_codex_runtime_tools(self):
        text = (ROOT / "scripts" / "build_vna_suite.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("function Get-IsolatedPyInstallerPath", text)
        self.assertIn(".cache\\codex-runtimes", text)
        self.assertRegex(
            text,
            r"\$env:PATH\s*=\s*Get-IsolatedPyInstallerPath[\s\S]+?&\s+\$pyinstaller",
        )
        self.assertIn("$env:PATH = $originalPath", text)


if __name__ == "__main__":
    unittest.main()
