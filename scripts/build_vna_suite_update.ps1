param(
    [ValidateSet('All', 'Test', 'Diagnostic')]
    [string[]]$Source = @('All'),

    [string]$Version = '',

    [string]$BasePath = '',

    [string]$NewPath = '',

    [string[]]$ExcludeRelativePath = @('PythonVNAUpdaterRunner.exe', '_internal\base_library.zip'),

    [switch]$SkipBuild,

    [switch]$SkipSevenZip,

    [switch]$ForceIncremental
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $root 'dist'
$updatesRoot = Join-Path $distRoot 'updates'
$latestPathFile = Join-Path $distRoot 'LATEST_SUITE_PATH.txt'
$syncScript = Join-Path $PSScriptRoot 'sync_worktrees_and_build_suite.ps1'
$buildScript = Join-Path $PSScriptRoot 'build_vna_suite.ps1'

function Find-7Zip {
    $candidates = @(
        (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
        (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe'),
        '7z.exe'
    )
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($resolved -and (Test-Path -LiteralPath $resolved.Source)) {
            return $resolved.Source
        }
    }
    return $null
}

function Normalize-Version {
    param([Parameter(Mandatory=$true)][string]$Value)

    $versionText = $Value.Trim()
    if ($versionText.StartsWith('v', [System.StringComparison]::OrdinalIgnoreCase)) {
        $versionText = $versionText.Substring(1)
    }
    if ($versionText -notmatch '^\d+(\.\d+){1,3}([A-Za-z0-9._-]+)?$') {
        throw "Invalid version '$Value'. Use a value such as 3.0.5 or v3.0.5."
    }
    return $versionText
}

function Compare-VersionNumber {
    param(
        [Parameter(Mandatory=$true)][string]$Left,
        [Parameter(Mandatory=$true)][string]$Right
    )

    $leftParts = @([regex]::Matches($Left, '\d+') | ForEach-Object { [int]$_.Value })
    $rightParts = @([regex]::Matches($Right, '\d+') | ForEach-Object { [int]$_.Value })
    $count = [Math]::Max($leftParts.Count, $rightParts.Count)
    for ($i = 0; $i -lt $count; $i++) {
        $leftValue = if ($i -lt $leftParts.Count) { $leftParts[$i] } else { 0 }
        $rightValue = if ($i -lt $rightParts.Count) { $rightParts[$i] } else { 0 }
        if ($leftValue -lt $rightValue) {
            return -1
        }
        if ($leftValue -gt $rightValue) {
            return 1
        }
    }
    return 0
}

function Resolve-DistPath {
    param([Parameter(Mandatory=$true)][string]$Path)

    $fullPath = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $root $Path))
    }
    $distFull = [System.IO.Path]::GetFullPath($distRoot).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($distFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path must be inside dist: $Path"
    }
    return $fullPath
}

function Read-LatestPath {
    if (-not (Test-Path -LiteralPath $latestPathFile)) {
        return ''
    }
    $path = [System.IO.File]::ReadAllText($latestPathFile).Trim()
    if ([string]::IsNullOrWhiteSpace($path)) {
        return ''
    }
    return Resolve-DistPath -Path $path
}

function Get-ReleaseVersion {
    param([Parameter(Mandatory=$true)][string]$ReleasePath)

    $leaf = Split-Path -Leaf $ReleasePath
    $leafMatch = [regex]::Match($leaf, '^PythonVNA_Suite_v(.+)$')
    if ($leafMatch.Success) {
        return $leafMatch.Groups[1].Value
    }

    $versionPath = Join-Path $ReleasePath 'VERSION.txt'
    if (Test-Path -LiteralPath $versionPath) {
        $text = [System.IO.File]::ReadAllText($versionPath)
        $match = [regex]::Match($text, '(?m)^Version:\s*(.+?)\s*$')
        if ($match.Success) {
            return $match.Groups[1].Value.Trim()
        }
    }

    return $leaf
}

function Get-FileMap {
    param([Parameter(Mandatory=$true)][string]$ReleasePath)

    $releaseFull = [System.IO.Path]::GetFullPath($ReleasePath).TrimEnd('\') + '\'
    $map = @{}
    Get-ChildItem -LiteralPath $ReleasePath -Recurse -File | ForEach-Object {
        $relativePath = $_.FullName.Substring($releaseFull.Length)
        $key = $relativePath.Replace('/', '\').ToLowerInvariant()
        $map[$key] = [pscustomobject]@{
            RelativePath = $relativePath.Replace('/', '\')
            FullName = $_.FullName
            Length = $_.Length
        }
    }
    return $map
}

function Get-FileHashString {
    param([Parameter(Mandatory=$true)][string]$Path)

    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function New-CleanDirectory {
    param([Parameter(Mandatory=$true)][string]$Path)

    $distFull = [System.IO.Path]::GetFullPath($distRoot).TrimEnd('\') + '\'
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($distFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove directory outside dist: $Path"
    }
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path | Out-Null
}

function Copy-RelativeFile {
    param(
        [Parameter(Mandatory=$true)][string]$SourcePath,
        [Parameter(Mandatory=$true)][string]$DestinationRoot,
        [Parameter(Mandatory=$true)][string]$RelativePath
    )

    $destinationPath = Join-Path $DestinationRoot $RelativePath
    $destinationParent = Split-Path -Parent $destinationPath
    if (-not (Test-Path -LiteralPath $destinationParent)) {
        New-Item -ItemType Directory -Path $destinationParent | Out-Null
    }
    Copy-Item -LiteralPath $SourcePath -Destination $destinationPath -Force
}

function Write-ListFile {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [object[]]$Items = @()
    )

    $safeItems = @($Items)
    $text = ($safeItems | Sort-Object) -join [Environment]::NewLine
    if ($text.Length -gt 0) {
        $text += [Environment]::NewLine
    }
    [System.IO.File]::WriteAllText($Path, $text, [System.Text.UTF8Encoding]::new($false))
}

if (-not (Test-Path -LiteralPath $syncScript)) {
    throw "Missing sync/build script: $syncScript"
}
if (-not (Test-Path -LiteralPath $buildScript)) {
    throw "Missing build script: $buildScript"
}

$baselineBeforeBuild = ''
if (-not [string]::IsNullOrWhiteSpace($BasePath)) {
    $baselineBeforeBuild = Resolve-DistPath -Path $BasePath
}
else {
    $baselineBeforeBuild = Read-LatestPath
}

if (-not $SkipBuild) {
    Push-Location $root
    try {
        $buildArgs = @('-File', $syncScript, '-Apply', '-Build', '-Source')
        $buildArgs += $Source
        if (-not [string]::IsNullOrWhiteSpace($Version)) {
            $buildArgs += @('-Version', (Normalize-Version -Value $Version))
        }
        if ($SkipSevenZip) {
            $buildArgs += '-SkipReleaseArchives'
        }
        & powershell -NoProfile -ExecutionPolicy Bypass @buildArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Suite build failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

$baseRelease = $baselineBeforeBuild
if ([string]::IsNullOrWhiteSpace($baseRelease)) {
    throw "No base release was found. Pass -BasePath or build one full release first."
}
if (-not (Test-Path -LiteralPath $baseRelease -PathType Container)) {
    throw "Base release folder does not exist: $baseRelease"
}

$newRelease = ''
if (-not [string]::IsNullOrWhiteSpace($NewPath)) {
    $newRelease = Resolve-DistPath -Path $NewPath
}
else {
    $newRelease = Read-LatestPath
}
if ([string]::IsNullOrWhiteSpace($newRelease)) {
    throw "No new release was found. Pass -NewPath or run a build first."
}
if (-not (Test-Path -LiteralPath $newRelease -PathType Container)) {
    throw "New release folder does not exist: $newRelease"
}
if ([System.IO.Path]::GetFullPath($baseRelease).TrimEnd('\').Equals(
        [System.IO.Path]::GetFullPath($newRelease).TrimEnd('\'),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Base release and new release are the same folder: $newRelease"
}

$baseVersion = Get-ReleaseVersion -ReleasePath $baseRelease
$newVersion = Get-ReleaseVersion -ReleasePath $newRelease
$effectiveExcludeRelativePath = New-Object System.Collections.Generic.List[string]
foreach ($relativePath in $ExcludeRelativePath) {
    if (-not [string]::IsNullOrWhiteSpace($relativePath)) {
        $effectiveExcludeRelativePath.Add($relativePath)
    }
}
if ((Compare-VersionNumber -Left $baseVersion -Right '3.1.6') -lt 0) {
    $effectiveExcludeRelativePath.Add('PythonVNAUpdater.exe')
}
$updateName = "PythonVNA_Update_v${baseVersion}_to_v${newVersion}"
$updateDir = Join-Path $updatesRoot $updateName
$archivePath = Join-Path $updatesRoot "$updateName.7z"
$zipArchivePath = Join-Path $updatesRoot "$updateName.zip"

New-CleanDirectory -Path $updateDir

$baseMap = Get-FileMap -ReleasePath $baseRelease
$newMap = Get-FileMap -ReleasePath $newRelease
$excludeKeys = @{}
foreach ($relativePath in $effectiveExcludeRelativePath) {
    if (-not [string]::IsNullOrWhiteSpace($relativePath)) {
        $excludeKeys[$relativePath.Replace('/', '\').ToLowerInvariant()] = $true
    }
}

$added = New-Object System.Collections.Generic.List[string]
$changed = New-Object System.Collections.Generic.List[string]
$removed = New-Object System.Collections.Generic.List[string]

foreach ($key in $newMap.Keys) {
    if ($excludeKeys.ContainsKey($key)) {
        continue
    }
    $newItem = $newMap[$key]
    if (-not $baseMap.ContainsKey($key)) {
        $added.Add($newItem.RelativePath)
        Copy-RelativeFile -SourcePath $newItem.FullName -DestinationRoot $updateDir -RelativePath $newItem.RelativePath
        continue
    }

    $baseItem = $baseMap[$key]
    if ($baseItem.Length -ne $newItem.Length) {
        $changed.Add($newItem.RelativePath)
        Copy-RelativeFile -SourcePath $newItem.FullName -DestinationRoot $updateDir -RelativePath $newItem.RelativePath
        continue
    }

    $baseHash = Get-FileHashString -Path $baseItem.FullName
    $newHash = Get-FileHashString -Path $newItem.FullName
    if ($baseHash -ne $newHash) {
        $changed.Add($newItem.RelativePath)
        Copy-RelativeFile -SourcePath $newItem.FullName -DestinationRoot $updateDir -RelativePath $newItem.RelativePath
    }
}

foreach ($key in $baseMap.Keys) {
    if ($excludeKeys.ContainsKey($key)) {
        continue
    }
    if (-not $newMap.ContainsKey($key)) {
        $removed.Add($baseMap[$key].RelativePath)
    }
}

$internalChanges = @()
$internalChanges += $added | Where-Object { $_.StartsWith('_internal\', [System.StringComparison]::OrdinalIgnoreCase) }
$internalChanges += $changed | Where-Object { $_.StartsWith('_internal\', [System.StringComparison]::OrdinalIgnoreCase) }
$internalChanges += $removed | Where-Object { $_.StartsWith('_internal\', [System.StringComparison]::OrdinalIgnoreCase) }

Write-ListFile -Path (Join-Path $updateDir 'UPDATE_CHANGED_FILES.txt') -Items @($added.ToArray() + $changed.ToArray())
Write-ListFile -Path (Join-Path $updateDir 'UPDATE_REMOVED_FILES.txt') -Items @($removed.ToArray())

$safeIncremental = ($removed.Count -eq 0)
$builtAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
$instructions = @(
    'PythonVNA Suite incremental update'
    "From: $baseVersion"
    "To: $newVersion"
    "Built: $builtAt"
    ''
    "Base folder: $baseRelease"
    "New folder: $newRelease"
    ''
    "Changed/new files: $($added.Count + $changed.Count)"
    "Removed files: $($removed.Count)"
    "Shared dependency changes: $($internalChanges.Count)"
    "Excluded files: $($effectiveExcludeRelativePath.ToArray() -join ', ')"
    "Safe for overlay update: $safeIncremental"
    ''
    'Instructions:'
    '1. Close VIanalysis.exe and PythonVNATest.exe before updating.'
    '2. Extract this archive into the existing suite folder and overwrite files.'
    '3. If UPDATE_REMOVED_FILES.txt is non-empty, delete those relative paths from the target folder.'
    '4. If Safe for overlay update is False, prefer the full release archive instead.'
) -join [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $updateDir 'UPDATE_INFO.txt'), $instructions, [System.Text.UTF8Encoding]::new($false))

if (Test-Path -LiteralPath $zipArchivePath) {
    Remove-Item -LiteralPath $zipArchivePath -Force
}

Compress-Archive -Path (Join-Path $updateDir '*') -DestinationPath $zipArchivePath -CompressionLevel Optimal

$sevenZip = $null
if (-not $SkipSevenZip) {
    $sevenZip = Find-7Zip
}

if ($sevenZip) {
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Push-Location $updateDir
    try {
        & $sevenZip a -t7z -mx=9 -m0=LZMA2 -md=64m -mfb=273 -ms=on $archivePath .\* | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "7-Zip failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}
else {
    if ($SkipSevenZip) {
        Write-Host "Skipping incremental 7z archive."
    }
    else {
        Write-Warning "7-Zip was not found. Skipping .7z incremental archive creation."
    }
}

$zipArchive = Get-Item -LiteralPath $zipArchivePath
Write-Host "Incremental update zip: $zipArchivePath"
Write-Host "Update zip size: $([Math]::Round($zipArchive.Length / 1MB, 2)) MiB"
if (Test-Path -LiteralPath $archivePath) {
    $archive = Get-Item -LiteralPath $archivePath
    Write-Host "Incremental update 7z: $archivePath"
    Write-Host "Update 7z size: $([Math]::Round($archive.Length / 1MB, 2)) MiB"
}
Write-Host "Changed/new files: $($added.Count + $changed.Count)"
Write-Host "Removed files: $($removed.Count)"
Write-Host "Shared dependency changes: $($internalChanges.Count)"
Write-Host "Safe for overlay update: $safeIncremental"
