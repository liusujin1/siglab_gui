param([string]$Profile = '', [switch]$AllowRemote)
. (Join-Path $PSScriptRoot 'TestKit.Common.ps1')
$root = Get-SigLabSuiteRoot
if (-not $Profile) { $Profile = Join-Path $root 'config\test-local.json' }
elseif (-not [IO.Path]::IsPathRooted($Profile)) { $Profile = Join-Path $root $Profile }
$config = Import-SigLabProfile $Profile
Set-SigLabEnvironment $config $root

if (Get-Process -Name Samba, SIDMAT -ErrorAction SilentlyContinue) {
    throw 'SAMBA or SIDMAT is still running. Stop measurement/logging/real-time curves, close both GUI windows normally, then retry. The server was not stopped.'
}

$parts = Split-SigLabEndpoint ([string]$config.server)
$isLocal = $parts[0] -in @('127.0.0.1', 'localhost', '::1')
if (-not $isLocal -and -not $AllowRemote) {
    throw 'Refusing to stop a remote server without -AllowRemote.'
}
if (-not (Test-SigLabTcpEndpoint ([string]$config.server))) {
    Write-Host 'Communication Server is not running.'
    exit 0
}
$tokenFile = if ($config.token_file) { Expand-SigLabPath ([string]$config.token_file) } else { '' }
Stop-SigLabCommunicationServer ([string]$config.server) $tokenFile
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while ((Test-SigLabTcpEndpoint ([string]$config.server) 200) -and
       [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
if (Test-SigLabTcpEndpoint ([string]$config.server) 200) {
    throw 'Server acknowledged shutdown but is still listening; use the tray Exit command.'
}
Write-Host 'SigLab TestKit stopped cleanly.'
