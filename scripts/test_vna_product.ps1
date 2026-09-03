param(
    [ValidateSet('All', 'shared', 'python_vna_test', 'vianalysis')]
    [string]$Product = 'All',

    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $root 'config\vna_suite.json'
$python = Join-Path $root '.venv\Scripts\python.exe'
$preflight = Join-Path $PSScriptRoot 'preflight_vna_suite.ps1'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment was not found: $python"
}

& $preflight -ManifestPath $manifestPath -Product $Product
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

if ($Product -eq 'All') {
    $testPaths = @('tests')
}
else {
    $testPaths = New-Object System.Collections.Generic.List[string]
    $shared = @($manifest.areas | Where-Object { $_.id -eq 'shared' })[0]
    foreach ($testPath in @($shared.tests)) {
        if (-not $testPaths.Contains([string]$testPath)) {
            $testPaths.Add([string]$testPath)
        }
    }
    if ($Product -ne 'shared') {
        $selected = @($manifest.areas | Where-Object { $_.id -eq $Product })[0]
        foreach ($testPath in @($selected.tests)) {
            if (-not $testPaths.Contains([string]$testPath)) {
                $testPaths.Add([string]$testPath)
            }
        }
    }
}

$pytestArgs = @('-m', 'pytest')
if ($Quiet) {
    $pytestArgs += '-q'
}
$pytestArgs += @($testPaths)

Push-Location $root
try {
    & $python @pytestArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Tests for '$Product' failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
