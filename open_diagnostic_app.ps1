$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = $root
if (-not (Test-Path (Join-Path $appRoot 'python_vna\diagnostic\app.py'))) {
    $appRoot = Join-Path $root '.worktrees\vna_diagnostic_current'
}
if (-not (Test-Path (Join-Path $appRoot 'python_vna\diagnostic\app.py'))) {
    throw "Diagnostic worktree was not found: $appRoot"
}

Set-Location $appRoot

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = Join-Path $root '..\..\.venv\Scripts\python.exe'
}
if (-not (Test-Path $python)) {
    throw "Python virtual environment was not found: $python"
}

& $python -m python_vna.diagnostic.app @args
