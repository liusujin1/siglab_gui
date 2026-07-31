param(
    [Parameter(Mandatory=$true)]
    [string]$ReleasePath,

    [string[]]$BasePath = @(),

    [string[]]$UpdateArchivePath = @(),

    [string]$BaseUrl = '',

    [string]$OutputPath = ''
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $root 'dist'

function Resolve-PathStrict {
    param([Parameter(Mandatory=$true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $root $Path))
}

function Get-ReleaseVersion {
    param([Parameter(Mandatory=$true)][string]$Path)

    $versionPath = Join-Path $Path 'VERSION.txt'
    if (Test-Path -LiteralPath $versionPath) {
        $text = [System.IO.File]::ReadAllText($versionPath)
        $match = [regex]::Match($text, '(?m)^Version:\s*(.+?)\s*$')
        if ($match.Success) {
            return $match.Groups[1].Value.Trim()
        }
    }
    $leaf = Split-Path -Leaf $Path
    $match = [regex]::Match($leaf, '^PythonVNA_Suite_v(.+)$')
    if ($match.Success) {
        return $match.Groups[1].Value
    }
    throw "Could not determine release version from $Path"
}

function Get-ArchiveInfo {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Url
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Archive not found: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    $extension = $item.Extension.TrimStart('.').ToLowerInvariant()
    [ordered]@{
        url = $Url
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        size = $item.Length
        archive_type = $extension
    }
}

function Join-Url {
    param(
        [Parameter(Mandatory=$true)][string]$BaseUrl,
        [Parameter(Mandatory=$true)][string]$Leaf
    )

    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        return $Leaf
    }
    return $BaseUrl.TrimEnd('/') + '/' + $Leaf
}

function Expand-ListParameter {
    param([string[]]$Values)

    $expanded = New-Object System.Collections.Generic.List[string]
    foreach ($value in @($Values)) {
        if ([string]::IsNullOrWhiteSpace($value)) {
            continue
        }
        $expanded.Add($value)
    }
    return @($expanded.ToArray())
}

$release = Resolve-PathStrict -Path $ReleasePath
if (-not (Test-Path -LiteralPath $release -PathType Container)) {
    throw "Release folder not found: $release"
}

$version = Get-ReleaseVersion -Path $release
$releaseLeaf = Split-Path -Leaf $release
$fullZipArchive = Join-Path $distRoot "$releaseLeaf.zip"
$fullSevenZipArchive = Join-Path $distRoot "$releaseLeaf.7z"
$fullArchive = if (Test-Path -LiteralPath $fullSevenZipArchive -PathType Leaf) {
    $fullSevenZipArchive
}
else {
    $fullZipArchive
}
$manifestPath = if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    Join-Path $distRoot 'manifest.json'
}
else {
    Resolve-PathStrict -Path $OutputPath
}

$manifest = [ordered]@{
    product = 'PythonVNA Suite'
    channel = 'stable'
    latest = $version
    generated_at = (Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz')
    full = Get-ArchiveInfo -Path $fullArchive -Url (Join-Url -BaseUrl $BaseUrl -Leaf (Split-Path -Leaf $fullArchive))
    updates = @()
}

$basePaths = @(Expand-ListParameter -Values $BasePath)
$updateArchivePaths = @(Expand-ListParameter -Values $UpdateArchivePath)
if ($basePaths.Count -ne $updateArchivePaths.Count) {
    throw "BasePath count ($($basePaths.Count)) must match UpdateArchivePath count ($($updateArchivePaths.Count))."
}

if ($basePaths.Count -gt 0) {
    $updates = New-Object System.Collections.Generic.List[object]
    for ($i = 0; $i -lt $basePaths.Count; $i++) {
        $base = Resolve-PathStrict -Path $basePaths[$i]
        $updateArchive = Resolve-PathStrict -Path $updateArchivePaths[$i]
        $baseVersion = Get-ReleaseVersion -Path $base
        $updateInfo = Get-ArchiveInfo -Path $updateArchive -Url (Join-Url -BaseUrl $BaseUrl -Leaf (Split-Path -Leaf $updateArchive))
        $updateInfo['from'] = $baseVersion
        $updateInfo['to'] = $version
        $updateInfo['safe_overlay'] = $true
        $updates.Add($updateInfo)
    }
    $manifest['updates'] = @($updates.ToArray())
}

$parent = Split-Path -Parent $manifestPath
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
}

$json = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($manifestPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
Write-Host "Manifest: $manifestPath"
Write-Host "Latest: $version"
