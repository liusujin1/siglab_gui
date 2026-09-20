param(
    [string]$InstallPath = '',
    [string]$ReportRoot = '',
    [switch]$SkipNetwork,
    [switch]$SkipRuntime
)

$ErrorActionPreference = 'Stop'
$results = [Collections.Generic.List[string]]::new()
$reportCreated = $false

function Write-Result([string]$Name, [string]$Value) {
    $line = '{0}: {1}' -f $Name, $Value
    $results.Add($line)
    Write-Host $line
}

function Read-HttpBytes([uri]$Uri, [int]$Limit, [switch]$Direct, [switch]$Sample) {
    if ($Uri.Scheme -notin @('http', 'https')) { throw 'Only HTTP/HTTPS network URLs are supported' }
    $request = [Net.HttpWebRequest]::Create($Uri)
    $request.Timeout = 10000
    $request.ReadWriteTimeout = 10000
    $request.UserAgent = 'PythonVNA-LAN-Diagnostic'
    if ($Direct) { $request.Proxy = $null }
    if ($Sample) { $request.AddRange(0, $Limit - 1) }
    $response = $null
    $stream = $null
    try {
        $response = $request.GetResponse()
        $stream = $response.GetResponseStream()
        $buffer = [byte[]]::new($Limit + 1)
        $count = 0
        $maximum = if ($Sample) { $Limit } else { $Limit + 1 }
        while ($count -lt $maximum) {
            $read = $stream.Read($buffer, $count, $maximum - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -gt $Limit) { throw 'Manifest exceeds diagnostic size limit' }
        $bytes = [byte[]]::new($count)
        [Array]::Copy($buffer, $bytes, $count)
        return [pscustomobject]@{ Bytes = $bytes; Status = [int]$response.StatusCode }
    } catch {
        if ($_.Exception.Response) { $_.Exception.Response.Close() }
        throw
    } finally {
        if ($stream) { $stream.Dispose() }
        if ($response) { $response.Close() }
    }
}

function Test-UpdaterRuntime([string]$Name, [string]$Executable, [string]$ManifestUrl) {
    $target = Join-Path $ReportRoot $Name
    [void][IO.Directory]::CreateDirectory($target)
    $stdout = Join-Path $target 'stdout.txt'
    $stderr = Join-Path $target 'stderr.txt'
    $arguments = '--manifest-url "{0}" --current-version 0.0.0 --target-dir "{1}" --wait-seconds 0' -f $ManifestUrl, $target
    try {
        $startOptions = @{
            FilePath = $Executable; ArgumentList = $arguments
            WorkingDirectory = (Split-Path -Parent $Executable); WindowStyle = 'Hidden'
            RedirectStandardOutput = $stdout; RedirectStandardError = $stderr; PassThru = $true
        }
        $process = Start-Process @startOptions
        $null = $process.Handle
        if (-not $process.WaitForExit(45000)) {
            try { $process.Kill(); [void]$process.WaitForExit(5000) } catch {}
            Write-Result $Name 'TIMEOUT (45 seconds); no real update was requested'
            return
        }
        $process.Refresh()
        $exitCode = $process.ExitCode
        if ($null -eq $exitCode) { throw 'Could not retrieve updater exit code' }
        $hex = [BitConverter]::ToUInt32([BitConverter]::GetBytes([int]$exitCode), 0).ToString('X8')
        $log = Join-Path $target 'UPDATE_LOG.txt'
        $noUpdate = (Test-Path -LiteralPath $log) -and ([IO.File]::ReadAllText($log).Contains('No update applied:'))
        Write-Result $Name ("exit={0} (0x{1}); no-update log={2}" -f $exitCode, $hex, $noUpdate)
        if ((Test-Path -LiteralPath $stderr) -and (Get-Item -LiteralPath $stderr).Length -gt 0) {
            Write-Result ($Name + '_DETAIL') ([IO.File]::ReadAllText($stderr).Trim())
        }
    } catch {
        Write-Result $Name ('FAILED: ' + $_.Exception.Message)
    }
}

try {
    if (-not $InstallPath -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'PythonVNAUpdater.exe'))) {
        $InstallPath = $PSScriptRoot
    }
    if (-not $InstallPath) { $InstallPath = Read-Host 'Software folder (contains PythonVNAUpdater.exe)' }
    $InstallPath = (Get-Item -LiteralPath $InstallPath.Trim('"')).FullName
    $updater = Join-Path $InstallPath 'PythonVNAUpdater.exe'
    if (-not (Test-Path -LiteralPath $updater -PathType Leaf)) { throw "Updater not found: $updater" }
    if (-not $ReportRoot) { $ReportRoot = Join-Path $env:TEMP ('PythonVNA_Diagnostic_' + [guid]::NewGuid().ToString('N')) }
    $ReportRoot = [IO.Path]::GetFullPath($ReportRoot)
    if (Test-Path -LiteralPath $ReportRoot) { throw 'ReportRoot must be a new directory' }
    [void][IO.Directory]::CreateDirectory($ReportRoot)
    $reportCreated = $true
    Write-Result 'MODE' 'DIAGNOSE ONLY - no installation, no config changes, no real update'
    Write-Result 'SOFTWARE' $InstallPath
    Write-Result 'WINDOWS' ([Environment]::OSVersion.VersionString)
    Write-Result 'REPORT' $ReportRoot
    $version = ''
    $versionFile = Join-Path $InstallPath 'VERSION.txt'
    if (Test-Path -LiteralPath $versionFile) {
        $match = [regex]::Match([IO.File]::ReadAllText($versionFile), '(?m)^Version: *([0-9]+[.][0-9]+[.][0-9]+)')
        if ($match.Success) { $version = $match.Groups[1].Value }
    }
    Write-Result 'VERSION' $version
    $dlls = @(Get-ChildItem -LiteralPath $InstallPath -File | Where-Object { $_.Extension -ieq '.dll' })
    Write-Result 'ROOT_DLLS' (($dlls | ForEach-Object { $_.Name }) -join ', ')
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')) {
        Write-Result $name ([string][bool][Environment]::GetEnvironmentVariable($name))
    }
    if (-not $SkipNetwork) {
        try {
            $manifestUrl = [Environment]::GetEnvironmentVariable('PYTHON_VNA_UPDATE_MANIFEST_URL')
            if (-not $manifestUrl) {
                $config = [IO.File]::ReadAllText((Join-Path $InstallPath 'update_config.json')) | ConvertFrom-Json
                $manifestUrl = [string]$config.manifest_url
            }
            $uri = [uri]$manifestUrl
            if (-not $uri.IsAbsoluteUri) { throw 'Manifest URL is not absolute' }
            Write-Result 'MANIFEST_URL' $uri.GetLeftPart([UriPartial]::Path)
            foreach ($direct in @($false, $true)) {
                $mode = if ($direct) { 'DIRECT' } else { 'DEFAULT_PROXY' }
                try {
                    $response = Read-HttpBytes $uri 2097152 -Direct:$direct
                    $manifest = [Text.Encoding]::UTF8.GetString($response.Bytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
                    if (-not $manifest.latest) { throw 'Manifest is missing latest' }
                    Write-Result ('MANIFEST_' + $mode) ('HTTP ' + $response.Status + '; latest=' + $manifest.latest)
                    $package = @($manifest.updates | Where-Object { $_.from -eq $version -and $_.to -eq $manifest.latest } | Select-Object -First 1)
                    $item = if ($package.Count -gt 0) { $package[0] } else { $manifest.full }
                    if (-not $item.url) { throw 'No applicable package URL' }
                    $packageUri = [uri]::new($uri, [string]$item.url)
                    $sample = Read-HttpBytes $packageUri 4 -Direct:$direct -Sample
                    $signature = [BitConverter]::ToString($sample.Bytes)
                    Write-Result ('PACKAGE_' + $mode) ('HTTP ' + $sample.Status + '; first bytes=' + $signature)
                    if ($signature -ne '50-4B-03-04') { Write-Result ('PACKAGE_' + $mode + '_WARNING') 'Response does not start with an ordinary ZIP file header' }
                } catch { Write-Result ('NETWORK_' + $mode) ('FAILED: ' + $_.Exception.Message) }
            }
        } catch { Write-Result 'NETWORK' ('FAILED: ' + $_.Exception.Message) }
    }
    if (-not $SkipRuntime) {
        Write-Host 'Preparing isolated runtime. This may take several minutes...'
        $runtime = Join-Path $ReportRoot 'runtime'
        [void][IO.Directory]::CreateDirectory($runtime)
        $runner = Join-Path $runtime 'PythonVNAUpdaterProbe.exe'
        Copy-Item -LiteralPath $updater -Destination $runner
        $internal = Join-Path $InstallPath '_internal'
        if (-not (Test-Path -LiteralPath $internal -PathType Container)) { throw 'Missing _internal folder' }
        Copy-Item -LiteralPath $internal -Destination (Join-Path $runtime '_internal') -Recurse
        $probeManifest = Join-Path $ReportRoot 'probe-manifest.json'
        [IO.File]::WriteAllText($probeManifest, '{"latest":"0.0.0","updates":[]}', [Text.UTF8Encoding]::new($false))
        $probeUrl = ([uri]$probeManifest).AbsoluteUri
        Test-UpdaterRuntime 'RUNTIME_OLD_LAYOUT' $runner $probeUrl
        if (-not (Test-Path -LiteralPath $runner)) { Copy-Item -LiteralPath $updater -Destination $runner }
        foreach ($dll in $dlls) { Copy-Item -LiteralPath $dll.FullName -Destination (Join-Path $runtime $dll.Name) }
        Test-UpdaterRuntime 'RUNTIME_WITH_ROOT_DLLS' $runner $probeUrl
    }
} catch {
    Write-Result 'DIAGNOSTIC_ERROR' $_.Exception.Message
} finally {
    Write-Host ''
    Write-Host '========== PHOTO THIS SUMMARY =========='
    foreach ($line in $results) { Write-Host $line }
    if ($reportCreated) {
        [IO.File]::WriteAllLines((Join-Path $ReportRoot 'RESULT.txt'), $results, [Text.UTF8Encoding]::new($false))
    }
    Write-Host 'No software files were replaced. Temporary diagnostic files are retained at REPORT.'
}
