param([string]$ServerUrl = '', [string]$InstallPath = '')
. "$PSScriptRoot/common.ps1"
if (-not $ServerUrl) { $ServerUrl = Read-Host 'Server URL (example: http://192.168.1.100:8095)' }
if (-not $InstallPath) { $InstallPath = Read-Host 'Software folder containing PythonVNATest.exe / VIanalysis.exe' }
$resolvedInstallPath = Resolve-Path -LiteralPath $InstallPath
if ($resolvedInstallPath.Provider.Name -ne 'FileSystem') { throw 'Supply a filesystem installation folder' }
# .NET file APIs need a native path, not a PowerShell provider-qualified UNC path.
$InstallPath = $resolvedInstallPath.ProviderPath
if (-not (Test-Path -LiteralPath (Join-Path $InstallPath 'PythonVNATest.exe')) -and
    -not (Test-Path -LiteralPath (Join-Path $InstallPath 'VIanalysis.exe'))) { throw 'Not a suite installation folder' }
$uri = [uri]$ServerUrl
if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @('http','https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
    throw 'Supply an HTTP(S) server origin or manifest URL without credentials/query/fragment'
}
if ($uri.AbsolutePath -eq '/' -or -not $uri.AbsolutePath) { $ServerUrl = $ServerUrl.TrimEnd('/') + '/pythonvna/manifest.json' }
elseif (-not $uri.AbsolutePath.EndsWith('/manifest.json')) { throw 'Supply a server origin or full manifest URL' }
$response = Invoke-WebRequest -UseBasicParsing -Uri $ServerUrl -TimeoutSec 15 -MaximumRedirection 0
$manifest = $response.Content | ConvertFrom-Json
Assert-Version $manifest.latest
if ($manifest.product -ne 'PythonVNA Suite' -or $manifest.channel -ne 'stable' -or -not $manifest.full) { throw 'Invalid server manifest' }
foreach ($entry in (Get-Entries $manifest)) {
    if ($entry.archive_type -ne 'zip' -or $entry.url -notmatch '^LAN_PythonVNA_(Suite|Update)_v[0-9._a-z]+_[a-f0-9]{64}\.zip$') {
        throw 'Server does not provide LAN-safe relative ZIP packages'
    }
}
$download = [uri]::new([uri]$ServerUrl, [string]$manifest.full.url)
[void](Invoke-WebRequest -UseBasicParsing -Uri $download -Method Head -TimeoutSec 15 -MaximumRedirection 0)
$configPath = Join-Path $InstallPath 'update_config.json'
$settings = [pscustomobject]@{manifest_url=$ServerUrl; channel='stable'}
if (Test-Path -LiteralPath $configPath) {
    Copy-Item -LiteralPath $configPath -Destination ($configPath + '.' + [guid]::NewGuid().ToString('N') + '.bak')
}
$temporary = Join-Path $InstallPath ([guid]::NewGuid().ToString('N') + '.pending')
Write-JsonFile $temporary $settings
Set-AtomicFile $temporary $configPath
if ($env:PYTHON_VNA_UPDATE_MANIFEST_URL) { Write-Warning 'PYTHON_VNA_UPDATE_MANIFEST_URL overrides this configuration; remove it before starting the app' }
Write-Host "Client configured: $ServerUrl . Restart the application and check for updates."
