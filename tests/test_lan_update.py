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
        if name == 'export.ps1' and '-DeliveryLayout' not in arguments:
            arguments = (*arguments, '-DeliveryLayout', 'Loose')
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
        original_manifest_bytes = (bundle / 'manifest.json').read_bytes()
        package = bundle / original['full']['url']
        data = package.read_bytes()
        package.write_bytes(data[:-10])
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server, success=False)
        self.assertEqual(before, current_path.read_bytes())
        package.write_bytes(data)
        changed = dict(original, generated_at='different')
        (bundle / 'manifest.json').write_text(json.dumps(changed))
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server, success=False)
        (bundle / 'manifest.json').write_bytes(original_manifest_bytes)
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
        self.run_script('export.ps1', '-ManifestPath', clean, '-OutputPath', bundle)
        (bundle / 'transport.json').unlink()
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
        (alternate / 'transport.json').unlink()
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

    def test_split_boundaries_and_streamed_import(self):
        source = self.make_source()
        reference = self.export(source)
        manifest = json.loads((reference / 'manifest.json').read_bytes())
        boundary = manifest['full']['size']
        for threshold in (boundary + 1, boundary, boundary - 1, 100):
            with self.subTest(threshold=threshold):
                bundle = self.folder / f'中文 delivery {threshold}'
                self.run_script('export.ps1', '-ManifestPath', source, '-OutputPath', bundle,
                                '-PartSizeBytes', threshold)
                transport = json.loads((bundle / 'transport.json').read_bytes())
                for entry in transport['files']:
                    assembled = b''.join((bundle / part['name']).read_bytes() for part in entry['parts'])
                    self.assertEqual(assembled, (reference / entry['name']).read_bytes())
                    self.assertEqual((bundle / entry['name']).exists(), entry['size'] <= threshold)
                    self.assertTrue(all(part['size'] <= threshold for part in entry['parts']))
                self.assertTrue(all(path.stat().st_size <= 49_000_000 for path in bundle.rglob('*') if path.is_file()))
                if threshold == 100:
                    transport['max_file_bytes'] = 45_000_000
                    (bundle / 'transport.json').write_text(json.dumps(transport))
                server = self.folder / f'中文 server {threshold}'
                self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
                self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
                for entry in transport['files']:
                    self.assertEqual((server / 'public/pythonvna' / entry['name']).read_bytes(),
                                     (reference / entry['name']).read_bytes())
                self.assertEqual(list((server / 'staging').iterdir()), [])

    def test_invalid_parts_preserve_live_release(self):
        source = self.make_source()
        bundle = self.folder / 'split'
        self.run_script('export.ps1', '-ManifestPath', source, '-OutputPath', bundle, '-PartSizeBytes', 100)
        server = self.folder / 'server'
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
        current = server / 'public/pythonvna/manifest.json'
        before = current.read_bytes()
        metadata_path = bundle / 'transport.json'
        metadata_bytes = metadata_path.read_bytes()
        original = json.loads(metadata_bytes)
        first_path = bundle / original['files'][0]['parts'][0]['name']
        first_bytes = first_path.read_bytes()
        for fault in ('missing', 'corrupt', 'order', 'duplicate', 'path', 'version', 'size', 'whole_hash'):
            with self.subTest(fault=fault):
                metadata = json.loads(metadata_bytes)
                parts = metadata['files'][0]['parts']
                if fault == 'missing':
                    first_path.unlink()
                elif fault == 'corrupt':
                    first_path.write_bytes(b'x' * len(first_bytes))
                elif fault == 'order':
                    parts.reverse()
                elif fault == 'duplicate':
                    parts[1] = parts[0].copy()
                elif fault == 'path':
                    parts[0]['name'] = '../outside.part'
                elif fault == 'version':
                    metadata['files'][0]['name'] = metadata['files'][0]['name'].replace('1.0.2', '9.0.0')
                elif fault == 'size':
                    parts[0]['size'] += 1
                else:
                    first_path.write_bytes(b'x' * len(first_bytes))
                    parts[0]['sha256'] = hashlib.sha256(first_path.read_bytes()).hexdigest()
                metadata_path.write_text(json.dumps(metadata))
                self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server, success=False)
                self.assertEqual(before, current.read_bytes())
                first_path.write_bytes(first_bytes)
                metadata_path.write_bytes(metadata_bytes)
        self.assertGreaterEqual(len(list((server / 'logs').glob('*.log'))), 9)

    def test_real_49mb_transfer_boundaries(self):
        bundle = self.folder / 'boundary delivery'
        bundle.mkdir()
        shutil.copytree(TOOLS, bundle / 'tools')
        shutil.copyfile(TOOLS / 'deploy_to_server.bat', bundle / 'deploy_to_server.bat')
        entries = []
        for index, size in enumerate((48_999_999, 49_000_000, 49_000_001, 98_000_001)):
            temporary = bundle / 'package.zip'
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
                with archive.open('payload.bin', 'w') as stream:
                    remaining = size - 120
                    block = bytes(1_000_000)
                    while remaining:
                        count = min(len(block), remaining)
                        stream.write(block[:count])
                        remaining -= count
            self.assertEqual(temporary.stat().st_size, size)
            with temporary.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            stem = 'LAN_PythonVNA_Suite_v2.0.0' if index == 0 else f'LAN_PythonVNA_Update_v1.0.{index}_to_v2.0.0'
            name = f'{stem}_{digest}.zip'
            temporary.rename(bundle / name)
            entry = dict(url=name, sha256=digest, size=size, archive_type='zip')
            if index:
                entry.update({'from': f'1.0.{index}', 'to': '2.0.0'})
            entries.append(entry)
        (bundle / 'manifest.json').write_text(json.dumps(dict(product='PythonVNA Suite', channel='stable',
                                                            latest='2.0.0', full=entries[0], updates=entries[1:])))
        command = ". '" + str(TOOLS / 'transfer.ps1') + "'; Write-LanTransfer '" + str(bundle) + "' ('0' * 64); $null=Test-LanTransfer '" + str(bundle) + "'"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        transport = json.loads((bundle / 'transport.json').read_bytes())
        self.assertEqual([len(entry['parts']) for entry in transport['files']], [1, 1, 2, 3])
        self.assertEqual(transport['max_file_bytes'], 49_000_000)
        self.assertTrue(all(path.stat().st_size <= 49_000_000 for path in bundle.rglob('*') if path.is_file()))
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', self.folder / 'server')
        for entry in entries:
            with (self.folder / 'server/public/pythonvna' / entry['url']).open('rb') as stream:
                self.assertEqual(hashlib.file_digest(stream, 'sha256').hexdigest(), entry['sha256'])

    def test_insufficient_space_and_existing_deployment_validation(self):
        command = ". '" + str(TOOLS / 'transfer.ps1') + "'; Assert-LanSpace '" + str(self.folder) + "' 9223372036854775807"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Insufficient disk space', result.stderr)
        bundle = self.export(self.make_source())
        server = self.folder / 'space server'
        self.run_script('import.ps1', '-BundlePath', bundle, '-ServerRoot', server)
        before = (server / 'public/pythonvna/manifest.json').read_bytes()
        command = ". '" + str(TOOLS / 'transfer.ps1') + "'; $code=[IO.File]::ReadAllText('" + str(TOOLS / 'import.ps1') + "'); $code=$code.Replace('. " + '"$PSScriptRoot/transfer.ps1"' + "','').Replace('Assert-LanSpace $ServerRoot $required','Assert-LanSpace $ServerRoot ([long]::MaxValue)'); & ([scriptblock]::Create($code)) -BundlePath '" + str(bundle) + "' -ServerRoot '" + str(server) + "'"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Insufficient disk space', result.stderr)
        self.assertEqual(before, (server / 'public/pythonvna/manifest.json').read_bytes())
        self.assertEqual(list((server / 'staging').iterdir()), [])
        root = self.folder / 'custom server'
        root.mkdir()
        (root / '.pythonvna-lan-root').write_text('PythonVNA LAN v1')
        (root / 'server.json').write_text(json.dumps(dict(ip='127.0.0.1', port=8095, subnet='127.0.0.0/8')))
        command = ". '" + str(TOOLS / 'server-deployment.ps1') + "'; $sites=@([pscustomobject]@{physical='" + str(root / 'public') + "';bindings=@('127.0.0.1:8095:')}); Test-LanDeployment '" + str(root) + "' $sites"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.folder / 'saved-deployment.json'
        saved.write_text(json.dumps(dict(schema=1, server_root=str(root))))
        saved_command = command + "; Resolve-LanDeployment '" + str(saved) + "' $sites"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', saved_command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command.replace("bindings=@('127.0.0.1:8095:')", "bindings=@('127.0.0.1:9999:')")], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_post_publication_export_and_independent_retry(self):
        source = self.make_source()
        script = ROOT / 'scripts/export_published_lan_update.ps1'
        command = [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-ManifestPath', str(source)]
        skipped = subprocess.run(command + ['-SkipLanExport'], capture_output=True, text=True)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertFalse((source.parent / 'LAN_v1.0.2_delivery').exists())
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LAN EXPORT: SUCCEEDED', result.stdout)
        (source.parent / 'LAN_v1.0.2_delivery/deploy_to_server.bat').unlink()
        failed = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('PUBLIC PUBLICATION remains successful', failed.stdout)

    def run_compact_launcher(self, bundle, success=True):
        import os

        environment = dict(os.environ, TEMP=str(self.folder), TMP=str(self.folder))
        result = subprocess.run(
            ['cmd.exe', '/d', '/c', str(bundle / 'deploy_to_server.bat'), '--extract-only'],
            env=environment, input='\n', capture_output=True, text=True, errors='replace', timeout=120,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_compact_delivery_contains_only_parts_and_one_launcher(self):
        import base64
        import re

        source = self.make_source()
        for threshold in (49_000_000, 45_000_000, 8000):
            with self.subTest(threshold=threshold):
                bundle = self.folder / f'中文 compact delivery {threshold}'
                self.run_script('export.ps1', '-ManifestPath', source, '-OutputPath', bundle,
                                '-PartSizeBytes', threshold, '-DeliveryLayout', 'Compact')
                launcher = (bundle / 'deploy_to_server.bat').read_text(encoding='utf-8')
                metadata = json.loads(base64.b64decode(re.search(r"^\$deliveryMetadataBase64 = '([^']+)'$", launcher, re.M)[1]))
                self.assertEqual({path.name for path in bundle.iterdir()},
                                 {'deploy_to_server.bat', *(part['name'] for part in metadata['parts'])})
                self.assertFalse(any(path.is_dir() for path in bundle.iterdir()))
                self.assertTrue(all(path.stat().st_size <= 49_000_000 for path in bundle.iterdir()))
                self.assertEqual(len(metadata['parts']), (metadata['archive_size'] + threshold - 1) // threshold)
                self.run_script('export.ps1', '-ManifestPath', source, '-OutputPath', bundle,
                                '-PartSizeBytes', threshold, '-DeliveryLayout', 'Compact')
                before = {path.name: path.read_bytes() for path in bundle.iterdir()}
                result = self.run_compact_launcher(bundle)
                self.assertIn('EXTRACTED_BUNDLE=', result.stdout)
                restored = sorted(self.folder.glob('PythonVNA-Delivery-*/bundle'), key=lambda path: path.stat().st_mtime)[-1]
                self.assertTrue((restored / 'tools/import.ps1').exists())
                self.assertTrue((restored / 'manifest.json').exists())
                server = self.folder / f'compact server {threshold}'
                self.run_script('import.ps1', '-BundlePath', restored, '-ServerRoot', server)
                manifest = json.loads((restored / 'manifest.json').read_bytes())
                for entry in [manifest['full'], *manifest['updates']]:
                    self.assertEqual((restored / entry['url']).read_bytes(), (server / 'public/pythonvna' / entry['url']).read_bytes())
                self.assertEqual(before, {path.name: path.read_bytes() for path in bundle.iterdir()})

    def test_reject_part_sizes_above_49mb(self):
        source = self.make_source()
        for layout in ('Compact', 'Loose'):
            with self.subTest(layout=layout):
                self.run_script('export.ps1', '-ManifestPath', source,
                                '-OutputPath', self.folder / layout,
                                '-PartSizeBytes', 49_000_001, '-DeliveryLayout', layout,
                                success=False)
                self.assertFalse((self.folder / layout).exists())

    def test_compact_missing_or_corrupt_parts_fail_without_deployment(self):
        source = self.make_source()
        bundle = self.folder / 'compact'
        self.run_script('export.ps1', '-ManifestPath', source, '-OutputPath', bundle,
                        '-PartSizeBytes', 8000, '-DeliveryLayout', 'Compact')
        part = sorted(bundle.glob('*.part*'))[0]
        before = part.read_bytes()
        part.unlink()
        missing = self.run_compact_launcher(bundle, success=False)
        self.assertIn('Missing delivery part:', missing.stdout)
        part.write_bytes(b'x' * len(before))
        corrupt = self.run_compact_launcher(bundle, success=False)
        self.assertIn('Delivery part integrity check failed:', corrupt.stdout)
        self.assertFalse(list(self.folder.glob('PythonVNA-Delivery-*/bundle')))
        self.assertEqual(len(list(self.folder.glob('PythonVNA-Delivery-*/delivery-error.log'))), 2)

    def test_compact_payload_parser_rejects_unsafe_zip_paths(self):
        bundle = self.folder / 'unsafe compact'
        bundle.mkdir()
        part = bundle / 'LAN_v1.0.2.zip.part00001'
        with zipfile.ZipFile(part, 'w') as archive:
            archive.writestr('../outside.txt', 'unsafe')
        metadata = dict(schema=2, version='1.0.2', source_fingerprint='0' * 64, part_size_bytes=45_000_000,
                        archive_sha256=hashlib.sha256(part.read_bytes()).hexdigest(), archive_size=part.stat().st_size,
                        expanded_size=6, parts=[dict(name=part.name, index=1, size=part.stat().st_size,
                                                    sha256=hashlib.sha256(part.read_bytes()).hexdigest())])
        metadata_path = self.folder / 'metadata.json'
        metadata_path.write_text(json.dumps(metadata))
        work = self.folder / 'work'
        work.mkdir()
        command = ". '" + str(TOOLS / 'delivery.ps1') + "'; $metadata=Read-JsonFile '" + str(metadata_path) + "'; Expand-LanDelivery '" + str(bundle) + "' $metadata '" + str(work) + "'"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsafe ZIP path:', result.stderr)
        self.assertFalse((work / 'outside.txt').exists())

    def test_diagnostic_probes_manifest_and_selected_zip_without_changing_install(self):
        webroot = self.folder / 'web'
        webroot.mkdir()
        with zipfile.ZipFile(webroot / 'update.zip', 'w') as archive:
            archive.writestr('app.txt', 'new')
        manifest = {'latest': '1.0.2', 'full': {'url': 'missing-full.zip'},
                    'updates': [{'from': '1.0.1', 'to': '1.0.2', 'url': 'update.zip'}]}
        (webroot / 'manifest.json').write_text(json.dumps(manifest))
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(webroot))
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        installed = self.folder / 'software with spaces'
        installed.mkdir()
        (installed / 'PythonVNAUpdater.exe').write_bytes(b'not executed in network-only mode')
        (installed / 'VERSION.txt').write_text('Version: 1.0.1\n')
        (installed / 'update_config.json').write_text(json.dumps({
            'manifest_url': f'http://127.0.0.1:{server.server_port}/manifest.json'}))
        before = {path.name: path.read_bytes() for path in installed.iterdir()}
        report = self.folder / 'report'
        self.run_script('diagnose-client.ps1', '-InstallPath', installed, '-ReportRoot', report, '-SkipRuntime')
        summary = (report / 'RESULT.txt').read_text(encoding='utf-8')
        self.assertIn('VERSION: 1.0.1', summary)
        self.assertIn('MANIFEST_DIRECT: HTTP 200; latest=1.0.2', summary)
        self.assertIn('first bytes=50-4B-03-04', summary)
        self.assertNotIn('FAILED:', summary)
        self.assertEqual(before, {path.name: path.read_bytes() for path in installed.iterdir()})

    def test_diagnostic_rejects_existing_report_without_overwriting_it(self):
        installed = self.folder / 'installed'
        installed.mkdir()
        (installed / 'PythonVNAUpdater.exe').touch()
        report = self.folder / 'report'
        report.mkdir()
        result_file = report / 'RESULT.txt'
        result_file.write_text('keep')
        self.run_script('diagnose-client.ps1', '-InstallPath', installed, '-ReportRoot', report,
                        '-SkipRuntime', '-SkipNetwork')
        self.assertEqual(result_file.read_text(), 'keep')

    def test_rescue_launcher_preserves_update_lock(self):
        installed = self.folder / 'installed'
        installed.mkdir()
        (installed / 'PythonVNAUpdater.exe').touch()
        (installed / '_internal').mkdir()
        lock = installed / '.python_vna_update.lock'
        lock.write_text('keep')
        self.run_script('start-update-with-runtime.ps1', '-InstallPath', installed, success=False)
        self.assertEqual(lock.read_text(), 'keep')

    def test_rescue_launcher_copies_root_dlls_and_passes_existing_settings(self):
        import os

        installed = self.folder / 'software with spaces'
        installed.mkdir()
        internal = installed / '_internal'
        internal.mkdir()
        (internal / 'runtime.txt').write_text('runtime')
        (installed / 'msvcp140.dll').write_text('dependency')
        (installed / 'VERSION.txt').write_bytes(b'PythonVNA Suite\r\nVersion: 3.2.23\r\n')
        config = installed / 'update_config.json'
        config.write_text('{"manifest_url":"http://server.invalid:8095/pythonvna/manifest.json"}')
        source = self.folder / 'Probe.cs'
        source.write_text('''using System;
using System.IO;
public class Probe {
    public static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        if (!File.Exists(Path.Combine(root, "msvcp140.dll"))) return 10;
        if (!File.Exists(Path.Combine(root, "_internal", "runtime.txt"))) return 11;
        string target = args[Array.IndexOf(args, "--target-dir") + 1];
        File.WriteAllLines(Path.Combine(target, "LAUNCH_ARGUMENTS.txt"), args);
        return 0;
    }
}''')
        compile_result = subprocess.run([
            POWERSHELL, '-NoProfile', '-Command',
            "Add-Type -Path '" + str(source) + "' -OutputAssembly '" +
            str(installed / 'PythonVNAUpdater.exe') + "' -OutputType ConsoleApplication",
        ], capture_output=True, text=True, timeout=30)
        self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
        environment = dict(os.environ, TEMP=str(self.folder), TMP=str(self.folder))
        environment.pop('PYTHON_VNA_UPDATE_MANIFEST_URL', None)
        result = subprocess.run([
            POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(TOOLS / 'start-update-with-runtime.ps1'), '-InstallPath', str(installed),
        ], env=environment, capture_output=True, text=True, errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        arguments = (installed / 'LAUNCH_ARGUMENTS.txt').read_text().splitlines()
        self.assertEqual(arguments[arguments.index('--current-version') + 1], '3.2.23')
        self.assertEqual(arguments[arguments.index('--manifest-url') + 1],
                         'http://server.invalid:8095/pythonvna/manifest.json')
        self.assertEqual(arguments[arguments.index('--target-dir') + 1], str(installed))
        self.assertEqual(Path(arguments[arguments.index('--cleanup-root') + 1]).name, 'runtime')
        self.assertIn('0x00000000', result.stdout)
        self.assertEqual(json.loads(config.read_text())['manifest_url'],
                         'http://server.invalid:8095/pythonvna/manifest.json')
