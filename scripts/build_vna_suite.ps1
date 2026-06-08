$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $root '.venv\Scripts\pyinstaller.exe'
$spec = Join-Path $root 'PythonVNA_Suite.spec'
$distRoot = Join-Path $root 'dist'
$dist = Join-Path $distRoot 'PythonVNA_Suite'
$latestPathFile = Join-Path $distRoot 'LATEST_SUITE_PATH.txt'

function Get-SuiteVersion {
    $initPath = Join-Path $root 'python_vna\__init__.py'
    $pyprojectPath = Join-Path $root 'pyproject.toml'

    if (Test-Path -LiteralPath $initPath) {
        $initText = [System.IO.File]::ReadAllText($initPath)
        $match = [regex]::Match($initText, '(?m)^__version__\s*=\s*"([^"]+)"')
        if ($match.Success) {
            return $match.Groups[1].Value
        }
    }

    if (Test-Path -LiteralPath $pyprojectPath) {
        $pyprojectText = [System.IO.File]::ReadAllText($pyprojectPath)
        $match = [regex]::Match($pyprojectText, '(?m)^version\s*=\s*"([^"]+)"')
        if ($match.Success) {
            return $match.Groups[1].Value
        }
    }

    throw "Could not find suite version in python_vna\__init__.py or pyproject.toml"
}

function Assert-GeneratedDistPath {
    param([Parameter(Mandatory=$true)][string]$Path)

    $distRootFull = [System.IO.Path]::GetFullPath($distRoot).TrimEnd('\') + '\'
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    if (-not $pathFull.StartsWith($distRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch path outside dist directory: $Path"
    }
    if ((Split-Path -Leaf $pathFull) -notmatch '^PythonVNA_Suite(_v.+)?$') {
        throw "Refusing to touch unexpected dist directory: $Path"
    }
}

if (-not (Test-Path $python)) {
    throw "Python virtual environment was not found: $python"
}
if (-not (Test-Path $pyinstaller)) {
    throw "PyInstaller was not found: $pyinstaller"
}
if (-not (Test-Path $spec)) {
    throw "Suite spec was not found: $spec"
}

Push-Location $root
try {
    & $python -m pytest tests
    if ($LASTEXITCODE -ne 0) {
        throw "pytest failed with exit code $LASTEXITCODE"
    }

    & $python scripts\generate_suite_icons.py
    if ($LASTEXITCODE -ne 0) {
        throw "icon generation failed with exit code $LASTEXITCODE"
    }

    & $pyinstaller --clean --noconfirm $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    if (-not (Test-Path $dist)) {
        throw "Expected suite output directory was not created: $dist"
    }

    $suiteVersion = Get-SuiteVersion
    $releaseName = "PythonVNA_Suite_v$suiteVersion"
    $releaseDist = Join-Path $distRoot $releaseName
    Assert-GeneratedDistPath -Path $dist
    Assert-GeneratedDistPath -Path $releaseDist

    $builtAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    $versionInfo = @(
        'PythonVNA Suite'
        "Version: $suiteVersion"
        "Built: $builtAt"
        ''
        'Executables:'
        'VIanalysis.exe = vibration diagnostic UI'
        'PythonVNATest.exe = VNA test/acquisition UI'
        'PythonVNADiagnostic.exe = VNA conversion/analysis UI'
    ) -join [Environment]::NewLine
    [System.IO.File]::WriteAllText((Join-Path $dist 'VERSION.txt'), $versionInfo, [System.Text.UTF8Encoding]::new($false))

    if (Test-Path -LiteralPath $releaseDist) {
        Remove-Item -LiteralPath $releaseDist -Recurse -Force
    }
    Move-Item -LiteralPath $dist -Destination $releaseDist
    [System.IO.File]::WriteAllText($latestPathFile, $releaseDist, [System.Text.UTF8Encoding]::new($false))

    $exeBytes = (Get-ChildItem -LiteralPath $releaseDist -Filter '*.exe' | Measure-Object -Property Length -Sum).Sum
    $sharedBytes = (Get-ChildItem -LiteralPath (Join-Path $releaseDist '_internal') -Recurse -File | Measure-Object -Property Length -Sum).Sum
    $totalBytes = $exeBytes + $sharedBytes
    $totalMiB = [Math]::Round($totalBytes / 1MB, 2)
    $estimatedSeparateMiB = [Math]::Round(($exeBytes + ($sharedBytes * 3)) / 1MB, 2)
    $estimatedSavedMiB = [Math]::Round(($sharedBytes * 2) / 1MB, 2)
    Write-Host "Built $releaseDist"
    Write-Host "Version: $suiteVersion"
    Write-Host "Total size: $totalMiB MiB"
    Write-Host "Shared dependency size: $([Math]::Round($sharedBytes / 1MB, 2)) MiB"
    Write-Host "Estimated three separate folders: $estimatedSeparateMiB MiB"
    Write-Host "Estimated saved by sharing: $estimatedSavedMiB MiB"
    Write-Host "Largest files:"
    Get-ChildItem -LiteralPath $releaseDist -Recurse -File |
        Sort-Object Length -Descending |
        Select-Object -First 20 @{Name='MiB';Expression={[Math]::Round($_.Length / 1MB, 2)}}, FullName |
        Format-Table -AutoSize
}
finally {
    Pop-Location
}
