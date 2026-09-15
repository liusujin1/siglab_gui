from __future__ import annotations

import functools
import hashlib
import http.server
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile

from python_vna.update_client import select_update
from python_vna.updater import apply_update, extract_archive, normalize_staging_root


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'deploy' / 'lan-update'
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(POWERSHELL, 'Requires Windows PowerShell 5.1')
class LanUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vna_lan_test_')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def run_script(self, name, *arguments, success=True):
        result = subprocess.run(
            [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
             '-File', str(TOOLS / name), *map(str, arguments)],
            capture_output=True, text=True, errors='replace', timeout=120,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def make_source(self, version='1.0.2', base='1.0.1', unsafe=None):
        source = self.folder / ('source-' + version)
        source.mkdir()
        entries = []
        for kind in ('Suite', 'Update'):
            stem = f'PythonVNA_{kind}_v{version}' if kind == 'Suite' else f'PythonVNA_Update_v{base}_to_v{version}'
            path = source / (stem + '.zip')
            prefix = f'PythonVNA_Suite_v{version}/' if kind == 'Suite' else ''
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(prefix + 'PythonVNATest.exe', b'new-exe')
                archive.writestr(prefix + 'VERSION.txt', f'Version: {version}\n')
                archive.writestr(prefix + 'update_config.json', '{"manifest_url":"https://public.invalid"}')
                archive.writestr(prefix + '_internal/update_config.json', '{}')
                if kind == 'Update':
                    archive.writestr('UPDATE_REMOVED_FILES.txt', 'obsolete.txt\nupdate_config.json\n_internal\\update_config.json\n')
                if unsafe:
                    archive.writestr(unsafe, 'bad')
            entry = dict(url=f'https://public.invalid/{path.name}', archive_type='zip',
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)
            if kind == 'Update':
                entry.update({'from': base, 'to': version, 'safe_overlay': False})
            entries.append(entry)
        manifest = dict(product='PythonVNA Suite', channel='stable', latest=version,
                        generated_at='2026-09-15T00:00:00Z', full=entries[0], updates=[entries[1]])
        path = source / 'manifest.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        return path

    def export(self, manifest, suffix=''):
        bundle = self.folder / ('bundle-' + manifest.parent.name + suffix)
        self.run_script('export.ps1', '-ManifestPath', manifest, '-OutputPath', bundle)
        return bundle

    def test_export_is_deterministic_and_preserves_client_config(self):
        source = self.make_source()
        before = source.read_bytes()
        bundle = self.export(source)
        second = self.export(source, '-again')
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual((bundle / 'manifest.json').read_bytes(), (second / 'manifest.json').read_bytes())
        manifest = json.loads((bundle / 'manifest.json').read_bytes())
        for item in [manifest['full'], *manifest['updates']]:
            archive_path = bundle / item['url']
            self.assertEqual(item['sha256'], hashlib.sha256(archive_path.read_bytes()).hexdigest())
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertFalse(any(name.lower().endswith('update_config.json') for name in archive.namelist()))
            target = self.folder / ('target-' + str(len(item['url'])))
            target.mkdir(exist_ok=True)
            config = target / 'update_config.json'
            config.write_text('LAN-CONFIG', encoding='utf-8')
            (target / 'obsolete.txt').write_text('old')
            staging = self.folder / ('extract-' + str(len(item['url'])))
            extract_archive(archive_path, staging, 'zip')
            apply_update(normalize_staging_root(staging), target)
            self.assertEqual(config.read_text(), 'LAN-CONFIG')
            self.assertEqual((target / 'PythonVNATest.exe').read_bytes(), b'new-exe')
        incremental = select_update(manifest, current_version='1.0.1', manifest_url='http://server:8095/pythonvna/manifest.json')
        full = select_update(manifest, current_version='0.0.1', manifest_url='http://server:8095/pythonvna/manifest.json')
        self.assertEqual(incremental.package.kind, 'incremental')
        self.assertEqual(full.package.kind, 'full')
        self.assertTrue(full.package.url.startswith('http://server:8095/pythonvna/LAN_'))

    def test_import_idempotency_failure_and_two_release_retention(self):
        server = self.folder / 'server'
        bundle = self.export(self.make_source())
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
        current_path = server / 'public/pythonvna/manifest.json'
        before = current_path.read_bytes()
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
        self.assertEqual(before, current_path.read_bytes())
        original = json.loads((bundle / 'manifest.json').read_bytes())
        package = bundle / original['full']['url']
        data = package.read_bytes()
        package.write_bytes(data[:-10])
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server, success=False)
        self.assertEqual(before, current_path.read_bytes())
        package.write_bytes(data)
        changed = dict(original, generated_at='different')
        (bundle / 'manifest.json').write_text(json.dumps(changed))
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server, success=False)
        (bundle / 'manifest.json').write_text(json.dumps(original))
        interrupted = server / 'staging/interrupted'
        interrupted.mkdir()
        (interrupted / 'package.pending').write_bytes(b'incomplete')
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
        self.assertEqual(before, current_path.read_bytes())
        newer = self.export(self.make_source('1.0.3', '1.0.2'))
        self.run_script('import.ps1', '-BundlePath', newer, '-ServerRoot', server)
        self.assertTrue((server / 'public/pythonvna' / original['full']['url']).exists())
        newest = self.export(self.make_source('1.0.4', '1.0.3'))
        self.run_script('import.ps1', '-BundlePath', newest, '-ServerRoot', server)
        self.assertFalse((server / 'public/pythonvna' / original['full']['url']).exists())
        self.assertEqual(sorted(path.name for path in (server / 'history').glob('*.json')), ['1.0.3.json','1.0.4.json'])
        before_downgrade = current_path.read_bytes()
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server, success=False)
        self.assertEqual(before_downgrade, current_path.read_bytes())

    def test_reject_unsafe_source_and_existing_output(self):
        unsafe = self.make_source(unsafe='../escape.txt')
        self.run_script('export.ps1', '-ManifestPath', unsafe, '-OutputPath', self.folder / 'unsafe', success=False)
        clean = self.make_source('2.0.0')
        bundle = self.export(clean)
        self.run_script('export.ps1', '-ManifestPath', clean, '-OutputPath', bundle, success=False)

    def test_reject_invalid_import_paths_and_config_payload(self):
        bundle = self.export(self.make_source())
        manifest_path = bundle / 'manifest.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['full']['url'] = '../outside.zip'
        manifest_path.write_text(json.dumps(manifest))
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', self.folder / 'server', success=False)

        original = json.loads((self.folder / 'source-1.0.2/manifest.json').read_bytes())
        alternate = self.export(self.folder / 'source-1.0.2/manifest.json', '-config')
        altered = json.loads((alternate / 'manifest.json').read_bytes())
        entry = altered['full']
        archive_path = alternate / entry['url']
        with zipfile.ZipFile(archive_path, 'a') as archive:
            archive.writestr('update_config.json', '{}')
        entry['sha256'] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        entry['size'] = archive_path.stat().st_size
        new_name = f"LAN_PythonVNA_Suite_v{original['latest']}_{entry['sha256']}.zip"
        archive_path.rename(alternate / new_name)
        entry['url'] = new_name
        (alternate / 'manifest.json').write_text(json.dumps(altered))
        result = self.run_script('import.ps1', '-BundlePath', alternate, '-ServerRoot', self.folder / 'server', success=False)
        self.assertIn('must not replace update_config.json', result.stderr)

    def test_full_only_and_nonempty_root_are_supported_safely(self):
        source = self.make_source()
        manifest = json.loads(source.read_bytes())
        manifest['updates'] = []
        source.write_text(json.dumps(manifest))
        bundle = self.export(source)
        server = self.folder / 'server'
        server.mkdir()
        unrelated = server / 'user-data.txt'
        unrelated.write_text('keep')
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server, success=False)
        self.assertEqual(unrelated.read_text(), 'keep')
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', self.folder / 'empty-server')

    def test_interruption_at_manifest_commit_keeps_previous_release(self):
        import ctypes
        from ctypes import wintypes

        bundle = self.export(self.make_source())
        server = self.folder / 'server'
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
        current = server / 'public/pythonvna/manifest.json'
        before = current.read_bytes()
        next_bundle = self.export(self.make_source('1.0.3', '1.0.2'))
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(current), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            self.run_script('import.ps1', '-BundlePath', next_bundle, '-ServerRoot', server, success=False)
            self.assertEqual(current.read_bytes(), before)
        finally:
            kernel.CloseHandle(handle)
        self.run_script('import.ps1', '-BundlePath', next_bundle, '-ServerRoot', server)
        self.assertEqual(json.loads(current.read_bytes())['latest'], '1.0.3')

    def test_configure_client_validates_server_and_backs_up(self):
        bundle = self.export(self.make_source())
        server_root = self.folder / 'server'
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server_root)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(server_root / 'public'))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        target = self.folder / 'installed'
        target.mkdir()
        (target / 'PythonVNATest.exe').write_bytes(b'fake')
        config = target / 'update_config.json'
        config.write_text('original')
        origin = f'http://127.0.0.1:{server.server_port}'
        self.run_script('configure-client.ps1', '-ServerUrl', origin, '-InstallPath', target)
        self.assertEqual(json.loads(config.read_bytes())['manifest_url'], origin + '/pythonvna/manifest.json')
        self.assertEqual(next(target.glob('*.bak')).read_text(), 'original')
        before = config.read_bytes()
        self.run_script('configure-client.ps1', '-ServerUrl', origin + '/bad/manifest.json', '-InstallPath', target, success=False)
        self.assertEqual(config.read_bytes(), before)

    def test_scripts_parse_in_windows_powershell(self):
        result = subprocess.run(
            [POWERSHELL, '-NoProfile', '-Command',
             '$ErrorActionPreference="Stop"; Get-ChildItem -LiteralPath ' + "'" + str(TOOLS) + "'" +
             ' -Filter *.ps1 | ForEach-Object { $tokens=$null; $errors=$null; '
             '[void][Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors); '
             'if($errors){throw $errors} }'], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
