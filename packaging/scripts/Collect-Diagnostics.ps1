param([string]$Profile = '')
. (Join-Path $PSScriptRoot 'TestKit.Common.ps1')
$root = Get-SigLabSuiteRoot
if (-not $Profile) { $Profile = Join-Path $root 'config\test-local.json' }
elseif (-not [IO.Path]::IsPathRooted($Profile)) { $Profile = Join-Path $root $Profile }
$config = Import-SigLabProfile $Profile
Set-SigLabEnvironment $config $root
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$work = Join-Path $env:SIGLAB_LOCAL_DATA_ROOT "diagnostics\SigLab-Diagnostics-$stamp"
New-Item -ItemType Directory -Force -Path $work | Out-Null
Copy-Item (Join-Path $root 'manifest\build-info.json') $work -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root 'manifest\versions.txt') $work -ErrorAction SilentlyContinue
@{
    collected_utc = [DateTime]::UtcNow.ToString('o')
    os_version = [Environment]::OSVersion.VersionString
    process_architecture = [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString()
    profile = [string]$config.profile
    backend = [string]$config.backend
    server_scope = if ((Split-SigLabEndpoint ([string]$config.server))[0] -in @('127.0.0.1','localhost','::1')) { 'loopback' } else { 'remote-redacted' }
    serial_configured = [bool]$config.serial_port
} | ConvertTo-Json | Set-Content (Join-Path $work 'system.json') -Encoding UTF8
$log = Join-Path $env:SIGLAB_LOCAL_DATA_ROOT 'logs\communication_server.log'
if (Test-Path $log) {
    $text = (Get-Content $log -Tail 500) -join "`r`n"
    $text = $text -replace '(?i)(token|secret|password)\s*[:=]\s*\S+', '$1=<redacted>'
    $text = $text -replace '\b(?:\d{1,3}\.){3}\d{1,3}\b', '<redacted-ip>'
    $text = $text -replace '\bCOM\d+\b', '<redacted-port>'
    $text | Set-Content (Join-Path $work 'communication_server.sanitized.log') -Encoding UTF8
}
$zip = "$work.zip"
Compress-Archive -Path (Join-Path $work '*') -DestinationPath $zip -Force
Remove-Item -LiteralPath $work -Recurse -Force
Write-Host "Sanitized diagnostics: $zip"
