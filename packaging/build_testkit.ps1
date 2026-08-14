param(
    [string]$Version = '0.1.0-beta.5',
    [string]$PythonVersion = '3.12',
    [string]$OutputRoot = '',
    [switch]$SkipTests,
    [switch]$SkipSmoke
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repo = (Split-Path -Parent $PSScriptRoot)
if (-not $OutputRoot) { $OutputRoot = Join-Path $repo 'release' }
$output = [IO.Path]::GetFullPath($OutputRoot)
$buildRoot = Join-Path $repo '.build\testkit'
$venv = Join-Path $buildRoot 'venv'
$work = Join-Path $buildRoot 'pyinstaller'
$dist = Join-Path $buildRoot 'dist'
$stageName = "SigLab-TestKit-$Version"
$stage = Join-Path $output $stageName
$zip = Join-Path $output "$stageName-win-x64.zip"

function Run([string]$File, [string[]]$Arguments, [string]$WorkingDirectory = $repo) {
    Write-Host "> $File $($Arguments -join ' ')" -ForegroundColor Cyan
    Push-Location $WorkingDirectory
    try {
        & $File @Arguments
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    if ($code -ne 0) {
        throw "Command failed with exit code ${code}: $File"
    }
}

$launcher = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $launcher) { throw 'Python launcher py.exe is required on the build machine.' }
$probe = & py.exe "-$PythonVersion" -c "import struct,sys; print(sys.version); print(struct.calcsize('P')*8)" 2>&1
if ($LASTEXITCODE -ne 0 -or $probe[-1] -ne '64') {
    throw "CPython $PythonVersion x64 is required. Installed probe: $($probe -join ' ')"
}

New-Item -ItemType Directory -Force -Path $buildRoot, $output | Out-Null
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    Run 'py.exe' @("-$PythonVersion", '-m', 'venv', $venv)
}
$python = Join-Path $venv 'Scripts\python.exe'
Run $python @('-m', 'pip', 'install', '--disable-pip-version-check', '-r',
    (Join-Path $repo 'packaging\requirements-win-x64.lock'))
# A reused build venv may still contain beta.3's SciPy.  Remove it explicitly
# so tests and PyInstaller analysis prove the beta.5 runtime is independent.
Run $python @('-m', 'pip', 'uninstall', '-y', 'scipy')
$sitePackages = [IO.Path]::GetFullPath((& $python -c `
    "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim())
$venvRoot = [IO.Path]::GetFullPath($venv).TrimEnd('\') + '\'
if (-not $sitePackages.StartsWith($venvRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean SciPy residue outside the build venv: $sitePackages"
}
$scipyResidue = Join-Path $sitePackages 'scipy'
if (Test-Path -LiteralPath $scipyResidue) {
    Remove-Item -LiteralPath $scipyResidue -Recurse -Force
}
Run $python @('-c',
    "import importlib.util; assert importlib.util.find_spec('scipy') is None, 'SciPy residue remains in beta.5 build environment'")
Run $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '-e',
    (Join-Path $repo 'python_samba'), '-e', (Join-Path $repo 'python_sidmat'))

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = 'offscreen'
    # The legacy-alignment GUI suite constructs very large widget trees. Run
    # each file in a fresh process, and shard test_extra_pages, so Qt releases
    # native widgets between groups instead of accumulating several GB.
    $extra = Join-Path $repo 'python_samba\tests\test_extra_pages.py'
    foreach ($testFile in Get-ChildItem (Join-Path $repo 'python_samba\tests') -Filter 'test_*.py') {
        if ($testFile.FullName -eq $extra) { continue }
        Run $python @('-m', 'pytest', '-q', $testFile.FullName)
    }
    $extraGroups = @(
        'not gui_builds_all_pages and not window_initial_geometry and not formal_gui_patch and not pneumatic_matrix and not saveload and not saved_label and not open_setup and not connect_is_fast and not page_change and not reference_position and not bggsc and not visible_logging and not reference_refresh and not adc_set and not status_event and not pneumatic_filter_cell and not new_alignment and not visible_page_timer and not proximity_si and not loop_matrix and not filter_dialog and not digio and not pneumatic_expanders',
        'gui_builds_all_pages or window_initial_geometry or formal_gui_patch or pneumatic_matrix or saveload or saved_label or open_setup or connect_is_fast or page_change or reference_position or bggsc or visible_logging',
        'reference_refresh or adc_set or status_event or pneumatic_filter_cell or new_alignment or visible_page_timer or proximity_si or loop_matrix or filter_dialog or digio or pneumatic_expanders'
    )
    foreach ($expression in $extraGroups) {
        Run $python @('-m', 'pytest', '-q', $extra, '-k', $expression)
    }
    foreach ($testFile in Get-ChildItem (Join-Path $repo 'python_sidmat\tests') -Filter 'test_*.py') {
        Run $python @('-m', 'pytest', '-q', $testFile.FullName)
    }
    Run $python @('-m', 'compileall', '-q',
        (Join-Path $repo 'python_samba\src'), (Join-Path $repo 'python_sidmat\src'))
}

# Generate reproducible, per-application PE icons before PyInstaller reads
# either spec. The generator uses only the Python standard library.
Run $python @(
    (Join-Path $repo 'packaging\generate_testkit_icons.py'),
    '--output', (Join-Path $repo 'packaging\assets')
)

Remove-Item -LiteralPath $work, $dist, $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $work, $dist | Out-Null
foreach ($spec in @('SigLabSuite.spec', 'PythonSambaCommServer.spec')) {
    Run $python @(
        '-m', 'PyInstaller', '--clean', '--noconfirm',
        '--workpath', (Join-Path $work ([IO.Path]::GetFileNameWithoutExtension($spec))),
        '--distpath', $dist, (Join-Path $repo "packaging\$spec")
    )
}

New-Item -ItemType Directory -Force -Path `
    (Join-Path $stage 'apps'), (Join-Path $stage 'apps\CommServer'),
    (Join-Path $stage 'manifest') | Out-Null
Copy-Item (Join-Path $dist 'SigLabSuite') (Join-Path $stage 'apps\SigLabSuite') -Recurse
Copy-Item (Join-Path $dist 'PythonSambaCommServer.exe') `
    (Join-Path $stage 'apps\CommServer\PythonSambaCommServer.exe')
Copy-Item (Join-Path $repo 'packaging\config') (Join-Path $stage 'config') -Recurse
Copy-Item (Join-Path $repo 'packaging\assets') (Join-Path $stage 'assets') -Recurse
Copy-Item (Join-Path $repo 'packaging\samples') (Join-Path $stage 'samples') -Recurse
Copy-Item (Join-Path $repo 'packaging\docs') (Join-Path $stage 'docs') -Recurse
Copy-Item (Join-Path $repo 'python_samba\THIRD_PARTY_NOTICES.md') `
    (Join-Path $stage 'docs\THIRD_PARTY_NOTICES.md')
Copy-Item (Join-Path $repo 'packaging\scripts') (Join-Path $stage 'scripts') -Recurse
Copy-Item (Join-Path $repo 'packaging\smoke_test.ps1') (Join-Path $stage 'scripts\smoke_test.ps1')
Copy-Item (Join-Path $repo 'packaging\wrappers\*.bat') $stage

# Validate the actual hook output, not just the spec's requested excludes.
$guiInternal = Join-Path $stage 'apps\SigLabSuite\_internal'
$blockedNames = @(
    'opengl32sw.dll',
    'qt6pdf.dll', 'qt6pdfwidgets.dll', 'qt6qml.dll', 'qt6qmlmeta.dll',
    'qt6qmlmodels.dll', 'qt6qmlworkerscript.dll', 'qt6quick.dll',
    'qt6quick3d.dll', 'qt6quickcontrols2.dll', 'qt6quickwidgets.dll',
    'qt6test.dll', 'qt6virtualkeyboard.dll', 'qtpdf.pyd', 'qtpdfwidgets.pyd', 'qtqml.pyd',
    'qtquick.pyd', 'qtquick3d.pyd', 'qtquickcontrols2.pyd',
    'qtquickwidgets.pyd', 'qttest.pyd', 'qtvirtualkeyboard.pyd'
)
$blockedFiles = @(Get-ChildItem -LiteralPath $guiInternal -Recurse -File |
    Where-Object { $blockedNames -contains $_.Name.ToLowerInvariant() })
$blockedDirectories = @(
    (Join-Path $guiInternal 'OpenGL'),
    (Join-Path $guiInternal 'pyqtgraph\opengl'),
    (Join-Path $guiInternal '_patches'),
    (Join-Path $guiInternal 'scipy'),
    (Join-Path $guiInternal 'scipy.libs')
) | Where-Object { Test-Path -LiteralPath $_ }
if ($blockedFiles -or $blockedDirectories) {
    throw "Slim GUI payload contains forbidden Qt/OpenGL/duplicate patch resources: $((@($blockedFiles.FullName) + @($blockedDirectories)) -join ', ')"
}
if (-not (Test-Path -LiteralPath (Join-Path $guiInternal 'python_samba_patches'))) {
    throw 'Canonical python_samba_patches payload is missing.'
}
$translationRoot = Join-Path $guiInternal 'PySide6\translations'
$unexpectedTranslations = @(Get-ChildItem -LiteralPath $translationRoot -Filter '*.qm' -File |
    Where-Object { $_.Name.ToLowerInvariant() -notmatch '_(en|zh_cn)\.qm$' })
if ($unexpectedTranslations) {
    throw "Unexpected Qt translations remain: $($unexpectedTranslations.Name -join ', ')"
}
$platformRoot = Join-Path $guiInternal 'PySide6\plugins\platforms'
$unexpectedPlatforms = @(Get-ChildItem -LiteralPath $platformRoot -File |
    Where-Object { $_.Name.ToLowerInvariant() -notin @('qwindows.dll', 'qoffscreen.dll') })
if ($unexpectedPlatforms) {
    throw "Unexpected Qt platform plugins remain: $($unexpectedPlatforms.Name -join ', ')"
}
$imageRoot = Join-Path $guiInternal 'PySide6\plugins\imageformats'
$unexpectedImages = @(Get-ChildItem -LiteralPath $imageRoot -File |
    Where-Object { $_.Name.ToLowerInvariant() -notin @('qjpeg.dll', 'qico.dll') })
if ($unexpectedImages) {
    throw "Unexpected Qt image plugins remain: $($unexpectedImages.Name -join ', ')"
}

$archiveViewer = Join-Path $venv 'Scripts\pyi-archive_viewer.exe'
$serverArchive = Join-Path $stage 'apps\CommServer\PythonSambaCommServer.exe'
$serverListing = (& $archiveViewer --list --recursive --brief $serverArchive 2>&1) -join "`n"
$serverForbidden = @(
    'opengl32sw', 'Qt6Pdf', 'Qt6Qml', 'Qt6Quick',
    'Qt6Test', 'Qt6VirtualKeyboard', 'pyqtgraph[/\\]opengl',
    '(^|[/\\])OpenGL([/\\]|$)'
)
foreach ($pattern in $serverForbidden) {
    if ($serverListing -match $pattern) {
        throw "Standalone Communication Server contains forbidden payload matching: $pattern"
    }
}

$commit = (& git -C $repo rev-parse HEAD).Trim()
$branch = (& git -C $repo branch --show-current).Trim()
$buildInfo = [ordered]@{
    schema = 1; suite_version = $Version; target = 'win-x64'
    build_utc = [DateTime]::UtcNow.ToString('o'); git_commit = $commit
    git_branch = $branch; python = (& $python --version 2>&1).ToString()
    signed = $false; upx = $false; slimming_profile = 'numpy-signal-mat-v5-v1'
}
$buildInfo | ConvertTo-Json | Set-Content (Join-Path $stage 'manifest\build-info.json') -Encoding UTF8
@(
    "SigLab TestKit $Version", "Git $commit", (& $python --version 2>&1),
    (& $python -c "import python_samba; print('python-samba '+python_samba.__version__)"),
    (& $python -c "import python_sidmat; print('python-sidmat '+python_sidmat.__version__)"),
    '', 'Locked build dependencies:',
    (& $python -m pip freeze --exclude-editable)
) | Set-Content (Join-Path $stage 'manifest\versions.txt') -Encoding UTF8

$forbidden = Get-ChildItem -LiteralPath $stage -Recurse -File |
    Where-Object Extension -in '.json','.txt','.md','.ps1','.bat','.csv' |
    Select-String -Pattern 'BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|100\.68\.231\.23|Amiesuser' -List
if ($forbidden) { throw "Sensitive content detected in staged release: $($forbidden.Path -join ', ')" }

if (-not $SkipSmoke) {
    & (Join-Path $repo 'packaging\smoke_test.ps1') -StageRoot $stage
    if ($LASTEXITCODE -ne 0) { throw 'Frozen executable smoke tests failed.' }
}

$hashLines = Get-ChildItem -LiteralPath $stage -Recurse -File |
    Where-Object FullName -ne (Join-Path $stage 'manifest\files.sha256') |
    Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($stage.Length + 1).Replace('\', '/')
        '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower(), $relative
    }
$hashLines | Set-Content (Join-Path $stage 'manifest\files.sha256') -Encoding ASCII

Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -LiteralPath $stage -DestinationPath $zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
"$zipHash  $([IO.Path]::GetFileName($zip))" |
    Set-Content "$zip.sha256" -Encoding ASCII
Write-Host "Release: $zip" -ForegroundColor Green
Write-Host "SHA256: $zipHash" -ForegroundColor Green
