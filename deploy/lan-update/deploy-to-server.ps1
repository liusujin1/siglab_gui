param([string]$BundlePath = '', [switch]$Elevated)
$ErrorActionPreference = 'Stop'
if (-not $BundlePath) { $BundlePath = Split-Path -Parent $PSScriptRoot }
$BundlePath = [IO.Path]::GetFullPath($BundlePath)
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($Elevated) { throw 'Administrator rights were not granted' }
    $command = "& '" + $PSCommandPath.Replace("'", "''") + "' -BundlePath '" + $BundlePath.Replace("'", "''") + "' -Elevated"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    try {
        $process = Start-Process -FilePath "$PSHOME/powershell.exe" -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -Wait -PassThru
        exit $process.ExitCode
    } catch { Write-Host "Elevation cancelled or unavailable; no deployment performed: $_"; exit 1 }
}
$result = 1
$transcribing = $false
try {
    . "$PSScriptRoot/server-deployment.ps1"
    $sites = @(Get-LanSites)
    $configPath = Join-Path $env:ProgramData 'PythonVNA/LANDeployment/deployment.json'
    $root = Resolve-LanDeployment $configPath $sites
    $logs = Join-Path $root 'logs'
    Assert-NoLink $logs
    [void][IO.Directory]::CreateDirectory($logs)
    $logPath = Join-Path $logs ('deploy-' + [guid]::NewGuid().ToString('N') + '.log')
    [void](Start-Transcript -LiteralPath $logPath)
    $transcribing = $true
    & "$PSScriptRoot/import.ps1" -BundlePath $BundlePath -ServerRoot $root
    Write-Host 'DISK PUBLICATION: SUCCEEDED'
    $result = 2
    $settings = Read-JsonFile (Join-Path $root 'server.json')
    $url = "http://$($settings.ip):$($settings.port)/pythonvna/manifest.json"
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15
        $remote = $response.Content | ConvertFrom-Json
        $local = Read-JsonFile (Join-Path $root 'public/pythonvna/manifest.json')
        if (($remote | ConvertTo-Json -Depth 12 -Compress) -ne ($local | ConvertTo-Json -Depth 12 -Compress)) { throw 'HTTP manifest does not match disk publication' }
        Write-Host "HTTP VERIFICATION: SUCCEEDED ($url)"
        $result = 0
    } catch { Write-Warning "HTTP VERIFICATION: FAILED; disk publication remains successful. $_" }
} catch { Write-Host "DEPLOYMENT FAILED: $_" }
finally {
    if ($transcribing) { Stop-Transcript | Out-Null; Write-Host "Log: $logPath" }
    if ($Elevated) { [void](Read-Host 'Press Enter to close') }
}
exit $result
