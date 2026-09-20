param([string]$InstallPath = '')

$ErrorActionPreference = 'Stop'
$reportRoot = ''
$process = $null

try {
    if (-not $InstallPath -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'PythonVNAUpdater.exe'))) {
        $InstallPath = $PSScriptRoot
    }
    if (-not $InstallPath) { $InstallPath = Read-Host 'Software folder (contains PythonVNAUpdater.exe)' }
    $InstallPath = (Get-Item -LiteralPath $InstallPath.Trim('"')).FullName
    $updater = Join-Path $InstallPath 'PythonVNAUpdater.exe'
    $internal = Join-Path $InstallPath '_internal'
    if (-not (Test-Path -LiteralPath $updater -PathType Leaf)) { throw 'PythonVNAUpdater.exe is missing' }
    if (-not (Test-Path -LiteralPath $internal -PathType Container)) { throw '_internal folder is missing' }
    $running = @(Get-Process -Name PythonVNATest,VIanalysis,PythonVNAUpdater,PythonVNAUpdaterRunner,PythonVNAUpdaterProbe -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0) {
        throw ('Close both applications and other updaters first. Running PIDs: ' + (($running | ForEach-Object { $_.Id }) -join ', '))
    }
    if (Test-Path -LiteralPath (Join-Path $InstallPath '.python_vna_update.lock')) {
        throw 'An update lock exists. Keep it and contact support before retrying.'
    }
    $versionText = [IO.File]::ReadAllText((Join-Path $InstallPath 'VERSION.txt'))
    $versionLines = @($versionText.Split([char]10) | Where-Object { $_.StartsWith('Version:') })
    if ($versionLines.Count -ne 1) { throw 'Cannot identify installed version from VERSION.txt' }
    $version = $versionLines[0].Substring(8).Trim()
    if ($version -notmatch '^[0-9]+[.][0-9]+[.][0-9]+$') { throw 'Unsupported installed version format' }
    $manifestUrl = [Environment]::GetEnvironmentVariable('PYTHON_VNA_UPDATE_MANIFEST_URL')
    if (-not $manifestUrl) {
        $config = [IO.File]::ReadAllText((Join-Path $InstallPath 'update_config.json')) | ConvertFrom-Json
        $manifestUrl = [string]$config.manifest_url
    }
    $uri = [uri]$manifestUrl
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @('http', 'https')) {
        throw 'Configure a valid HTTP/HTTPS update manifest URL first'
    }
    $reportRoot = Join-Path $env:TEMP ('PythonVNA_Update_Rescue_' + [guid]::NewGuid().ToString('N'))
    $runtime = Join-Path $reportRoot 'runtime'
    [void][IO.Directory]::CreateDirectory($runtime)
    Write-Host ('Installed version: ' + $version)
    Write-Host ('Update server: ' + $uri.GetLeftPart([UriPartial]::Path))
    Write-Host ('Logs: ' + $reportRoot)
    Write-Host 'Preparing updater WITH app-local DLLs. Please wait...'
    $runner = Join-Path $runtime 'PythonVNAUpdaterRunner.exe'
    Copy-Item -LiteralPath $updater -Destination $runner
    foreach ($dll in @(Get-ChildItem -LiteralPath $InstallPath -File | Where-Object { $_.Extension -ieq '.dll' })) {
        Copy-Item -LiteralPath $dll.FullName -Destination (Join-Path $runtime $dll.Name)
    }
    Copy-Item -LiteralPath $internal -Destination (Join-Path $runtime '_internal') -Recurse
    $arguments = '--manifest-url "{0}" --current-version "{1}" --target-dir "{2}" --cleanup-root "{3}" --wait-seconds 0' -f $uri.AbsoluteUri, $version, $InstallPath, $runtime
    $startOptions = @{
        FilePath = $runner; ArgumentList = $arguments; WorkingDirectory = $runtime
        WindowStyle = 'Hidden'; PassThru = $true
        RedirectStandardOutput = (Join-Path $reportRoot 'stdout.txt')
        RedirectStandardError = (Join-Path $reportRoot 'stderr.txt')
    }
    Write-Host 'Starting update. Do not reopen the applications until it finishes.'
    $process = Start-Process @startOptions
    $null = $process.Handle
    $process.WaitForExit()
    $process.Refresh()
    if ($null -eq $process.ExitCode) { throw 'Could not retrieve updater exit code; inspect UPDATE_LOG.txt before retrying' }
    $exitCode = [int]$process.ExitCode
    $hex = [BitConverter]::ToUInt32([BitConverter]::GetBytes($exitCode), 0).ToString('X8')
    Write-Host ("Updater exit: {0} (0x{1})" -f $exitCode, $hex)
    if ($exitCode -ne 0) { throw 'Update did not complete successfully. Photograph this window and check UPDATE_LOG.txt.' }
    Write-Host 'Updater finished successfully. You may now open the software and check its version.'
    Write-Host 'This launcher does not patch the old in-app update button. Keep it for future updates until a fixed version is installed.'
} catch {
    Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    if ($reportRoot) { Write-Host ('Diagnostic logs: ' + $reportRoot) }
    exit 1
}
