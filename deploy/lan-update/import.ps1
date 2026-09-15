param([string]$BundlePath = '', [string]$ServerRoot = 'C:\PythonVNAUpdate', [int]$KeepReleases = 2)
. "$PSScriptRoot/common.ps1"
if (-not $BundlePath) { $BundlePath = Read-Host 'USB bundle folder (contains manifest.json)' }
if ($KeepReleases -lt 2) { throw 'KeepReleases must be at least 2' }
$BundlePath = (Resolve-Path -LiteralPath $BundlePath).Path
$ServerRoot = Initialize-LanRoot $ServerRoot
$lockPath = Join-Path $ServerRoot 'import.lock'
$lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    $manifest = Test-LanBundle $BundlePath
    $public = Join-Path $ServerRoot 'public/pythonvna'
    $currentPath = Join-Path $public 'manifest.json'
    Assert-NoLink $currentPath
    if (Test-Path -LiteralPath $currentPath) {
        $current = Read-JsonFile $currentPath
        if ([version]$manifest.latest -lt [version]$current.latest) { throw 'Downgrades are not allowed' }
        if ($manifest.latest -eq $current.latest -and
            ($manifest | ConvertTo-Json -Depth 12 -Compress) -ne ($current | ConvertTo-Json -Depth 12 -Compress)) {
            throw 'Same version has different content; publish a new version'
        }
    }
    $stage = Join-Path $ServerRoot ('staging/' + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($stage)
    foreach ($entry in (Get-Entries $manifest)) {
        Copy-Item -LiteralPath (Join-Path $BundlePath $entry.url) -Destination (Join-Path $stage $entry.url)
    }
    Write-JsonFile (Join-Path $stage 'manifest.json') $manifest
    [void](Test-LanBundle $stage)
    foreach ($entry in (Get-Entries $manifest)) {
        $destination = Join-Path $public $entry.url
        Assert-NoLink $destination
        if (Test-Path -LiteralPath $destination) {
            if ((Get-Sha256 $destination) -ne $entry.sha256) {
                throw 'Existing immutable package has a different hash'
            }
        } else {
            $publicPending = Join-Path $public ([guid]::NewGuid().ToString('N') + '.pending')
            [IO.File]::Copy((Join-Path $stage $entry.url), $publicPending)
            [IO.File]::Move($publicPending, $destination)
        }
    }
    $historyPath = Join-Path $ServerRoot "history/$($manifest.latest).json"
    Assert-NoLink $historyPath
    if (Test-Path -LiteralPath $currentPath) {
        $oldHistory = Join-Path $ServerRoot "history/$($current.latest).json"
        Assert-Version $current.latest
        Assert-NoLink $oldHistory
        Write-JsonFile $oldHistory $current
    }
    Write-JsonFile $historyPath $manifest
    $manifestPending = Join-Path $public ([guid]::NewGuid().ToString('N') + '.pending')
    [IO.File]::Copy((Join-Path $stage 'manifest.json'), $manifestPending)
    Set-AtomicFile $manifestPending $currentPath
    Write-Host "Published LAN version $($manifest.latest)"
    try {
        $histories = @(Get-ChildItem -LiteralPath (Join-Path $ServerRoot 'history') -File |
            Where-Object { $_.Name -match '^\d+\.\d+\.\d+(\.\d+)?\.json$' -and
                -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and [version]$_.BaseName -le [version]$manifest.latest } |
            Sort-Object { [version]$_.BaseName } -Descending)
        $keep = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($history in @($histories | Select-Object -First $KeepReleases)) {
            foreach ($entry in (Get-Entries (Read-JsonFile $history.FullName))) { [void]$keep.Add($entry.url) }
        }
        foreach ($file in (Get-ChildItem -LiteralPath $public -File)) {
            if ($file.Name -match '^LAN_PythonVNA_(Suite|Update)_v[0-9._a-z]+_[a-f0-9]{64}\.zip$' -and
                -not $keep.Contains($file.Name) -and -not ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                Remove-Item -LiteralPath $file.FullName -Force
                Write-Host "Removed obsolete LAN package: $($file.Name)"
            }
        }
        foreach ($history in @($histories | Select-Object -Skip $KeepReleases)) { Remove-Item -LiteralPath $history.FullName }
        foreach ($file in (Get-ChildItem -LiteralPath $stage -File)) { Remove-Item -LiteralPath $file.FullName }
        [IO.Directory]::Delete($stage)
    } catch { Write-Warning "Publication succeeded; cleanup incomplete: $_" }
} finally { $lock.Dispose() }
