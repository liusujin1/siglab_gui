$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
Write-Host "Starting python_samba GUI..."
Write-Host "Dir: $(Get-Location)"

$pythonExe = $null
$pythonArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
  $pythonExe = (Get-Command py).Source
  $pythonArgs = @("-3")
} else {
  $registered = Get-ItemPropertyValue `
    -LiteralPath "HKCU:\Software\Python\PythonCore\3.12\InstallPath" `
    -Name ExecutablePath -ErrorAction SilentlyContinue
  $pathPython = Get-Command python -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty Source
  $candidates = @(
    $registered,
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    $pathPython
  ) | Where-Object {
    $_ -and (Test-Path -LiteralPath $_) -and
      $_ -notlike "*\Microsoft\WindowsApps\python.exe"
  }
  $pythonExe = $candidates | Select-Object -First 1
}

if (-not $pythonExe) {
  Write-Host "Launch failed: a real Python installation was not found."
  Write-Host "Install Python 3.12, then run: python -m pip install PySide6 pyserial"
  Read-Host "Press Enter to exit"
  exit 1
}

& $pythonExe @pythonArgs -m python_samba.cli gui
if ($LASTEXITCODE -ne 0) {
  Write-Host "Launch failed. Install dependencies with:"
  Write-Host "  `"$pythonExe`" -m pip install PySide6 pyserial"
  Read-Host "Press Enter to exit"
}
