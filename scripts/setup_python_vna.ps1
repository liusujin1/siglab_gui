param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$ActivateScript = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"

Push-Location $RepoRoot
try {
    & $PythonExe -m venv .venv
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -e .[gui,ni,dev]
    & $VenvPython -m pip install pyinstaller==6.20.0
}
finally {
    Pop-Location
}

Write-Host "Environment prepared."
Write-Host "Activate with: & '$ActivateScript'"
Write-Host "Run with: & '$VenvPython' -m python_vna.app --device Dev1"
