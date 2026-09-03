param(
    [Parameter(Mandatory=$true)]
    [string]$ReleasePath,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $root 'dist'
$distPrefix = [System.IO.Path]::GetFullPath($distRoot).TrimEnd('\') + '\'

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
        if ($resolved -and (Test-Path -LiteralPath $resolved.Source -PathType Leaf)) {
            return $resolved.Source
        }
    }
    return $null
}

$release = [System.IO.Path]::GetFullPath($ReleasePath)
if (-not $release.StartsWith($distPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release folder must be inside dist: $release"
}
if (-not (Test-Path -LiteralPath $release -PathType Container)) {
    throw "Release folder was not found: $release"
}

$releaseName = Split-Path -Leaf $release
if ($releaseName -notmatch '^PythonVNA_Suite_v.+$') {
    throw "Unexpected release folder name: $releaseName"
}

$archivePath = Join-Path $distRoot "$releaseName.7z"
if ((Test-Path -LiteralPath $archivePath -PathType Leaf) -and -not $Force) {
    Write-Host "Using existing full release archive: $archivePath"
    Write-Output $archivePath
    exit 0
}

$sevenZip = Find-7Zip
if (-not $sevenZip) {
    throw '7-Zip was not found. Install 7-Zip before creating a full release archive.'
}

if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}

Write-Host "Creating full release archive from: $release"
& $sevenZip a -t7z -mx=9 -m0=LZMA2 -md=256m -mfb=273 -ms=on $archivePath $release | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "7-Zip failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
    throw "7-Zip did not create the expected archive: $archivePath"
}

$archive = Get-Item -LiteralPath $archivePath
Write-Host "Archive: $archivePath"
Write-Host "Archive size: $([Math]::Round($archive.Length / 1MB, 2)) MiB"
Write-Output $archivePath
