param([string]$Profile = '')
. (Join-Path $PSScriptRoot 'TestKit.Common.ps1')
$root = Get-SigLabSuiteRoot
if (-not $Profile) { $Profile = Join-Path $root 'config\test-local.json' }
elseif (-not [IO.Path]::IsPathRooted($Profile)) { $Profile = Join-Path $root $Profile }
$config = Import-SigLabProfile $Profile
Set-SigLabEnvironment $config $root
$failures = 0
function Check([bool]$Ok, [string]$Text) {
    if ($Ok) { Write-Host "[PASS] $Text" -ForegroundColor Green }
    else { Write-Host "[FAIL] $Text" -ForegroundColor Red; $script:failures++ }
}
Check ([Environment]::Is64BitOperatingSystem) 'Windows x64 operating system'
Check ($PSVersionTable.PSVersion.Major -ge 5) 'PowerShell 5 or later'
Check (Test-Path (Join-Path $root 'apps\SigLabSuite\Samba.exe')) 'Samba.exe present'
Check (Test-Path (Join-Path $root 'apps\SigLabSuite\SIDMAT.exe')) 'SIDMAT.exe present'
Check (Test-Path (Join-Path $root 'apps\CommServer\PythonSambaCommServer.exe')) 'CommServer present'
Check (Test-Path (Join-Path $root 'apps\SigLabSuite\_internal\PySide6\plugins\platforms\qwindows.dll')) 'Shared Qt platform plugin present'
try {
    $probe = Join-Path $env:SIGLAB_LOCAL_DATA_ROOT 'preflight.write-test'
    [IO.File]::WriteAllText($probe, 'ok'); Remove-Item $probe -Force
    Check $true 'Per-user runtime directory is writable'
} catch { Check $false "Per-user runtime directory is writable: $($_.Exception.Message)" }
$ports = @(Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue | Select-Object -ExpandProperty DeviceID)
if ($config.backend -eq 'mock') { Write-Host '[INFO] Mock profile does not require a serial port.' }
else { Check ($ports -contains [string]$config.serial_port) "Serial port $($config.serial_port) is enumerated" }
if ($config.backend -eq 'server') {
    if (Test-SigLabTcpEndpoint ([string]$config.server)) {
        Write-Host "[INFO] Existing Communication Server detected at $($config.server)."
    } else { Write-Host '[INFO] No server is running; Connect will auto-start the packaged component.' }
}
if ($failures) { exit 1 }
Write-Host 'Preflight passed.'
