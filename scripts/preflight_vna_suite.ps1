param(
    [string]$ManifestPath = 'config\vna_suite.json',

    [string]$Product = 'All',

    [switch]$RequireClean,

    [switch]$CheckLegacyWorktrees,

    [switch]$FailOnLegacyDirty
)

$ErrorActionPreference = 'Stop'

$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$rootPrefix = $root.TrimEnd('\') + '\'
$errors = New-Object System.Collections.Generic.List[string]

function Resolve-CanonicalPath {
    param([Parameter(Mandatory=$true)][string]$RelativePath)

    $full = if ([System.IO.Path]::IsPathRooted($RelativePath)) {
        [System.IO.Path]::GetFullPath($RelativePath)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $root $RelativePath))
    }
    if (-not $full.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -and $full -ne $root) {
        throw "Path escapes canonical repository root: $RelativePath"
    }
    return $full
}

function Normalize-RepoPath {
    param([Parameter(Mandatory=$true)][string]$Path)

    return $Path.Replace('\', '/').TrimStart('./')
}

function Convert-GlobToRegex {
    param([Parameter(Mandatory=$true)][string]$Pattern)

    $normalized = Normalize-RepoPath -Path $Pattern
    $escaped = [regex]::Escape($normalized)
    $escaped = $escaped.Replace('\*\*', '__DOUBLE_STAR__')
    $escaped = $escaped.Replace('\*', '[^/]*')
    $escaped = $escaped.Replace('__DOUBLE_STAR__', '.*')
    $escaped = $escaped.Replace('\?', '[^/]')
    return '^' + $escaped + '$'
}

function Test-PathPattern {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Pattern
    )

    $normalizedPath = Normalize-RepoPath -Path $Path
    $normalizedPattern = Normalize-RepoPath -Path $Pattern
    if ($normalizedPattern.EndsWith('/**')) {
        $prefix = $normalizedPattern.Substring(0, $normalizedPattern.Length - 3).TrimEnd('/')
        if ($normalizedPath -eq $prefix -or $normalizedPath.StartsWith($prefix + '/', [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return [regex]::IsMatch(
        $normalizedPath,
        (Convert-GlobToRegex -Pattern $normalizedPattern),
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
}

function Test-AnyPattern {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [object[]]$Patterns = @()
    )

    foreach ($pattern in @($Patterns)) {
        if (Test-PathPattern -Path $Path -Pattern ([string]$pattern)) {
            return $true
        }
    }
    return $false
}

function Get-StatusPath {
    param([Parameter(Mandatory=$true)][string]$StatusLine)

    if ($StatusLine.Length -le 3) {
        return ''
    }
    $path = $StatusLine.Substring(3).Trim()
    if ($path.Contains(' -> ')) {
        $path = $path.Split(@(' -> '), [System.StringSplitOptions]::None)[-1]
    }
    return (Normalize-RepoPath -Path $path.Trim('"'))
}

$manifestFullPath = Resolve-CanonicalPath -RelativePath $ManifestPath
if (-not (Test-Path -LiteralPath $manifestFullPath -PathType Leaf)) {
    throw "Suite ownership manifest was not found: $manifestFullPath"
}

$manifest = Get-Content -LiteralPath $manifestFullPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1) {
    $errors.Add("Unsupported manifest schema version: $($manifest.schema_version)")
}
if ([string]$manifest.repository.source_of_truth -ne 'canonical_root') {
    $errors.Add('Manifest repository.source_of_truth must be canonical_root.')
}
if ([string]$manifest.repository.canonical_root -ne '.') {
    $errors.Add('Manifest canonical_root must be the repository root (.).')
}

$requiredPaths = @(
    [string]$manifest.version_files.project,
    [string]$manifest.version_files.package,
    [string]$manifest.build.spec,
    [string]$manifest.build.script,
    [string]$manifest.build.preflight,
    [string]$manifest.build.archive_script
)

$areas = @($manifest.areas)
$areaIds = @{}
$patternOwners = @{}
foreach ($area in $areas) {
    $areaId = [string]$area.id
    if ([string]::IsNullOrWhiteSpace($areaId)) {
        $errors.Add('Every ownership area must have a non-empty id.')
        continue
    }
    if ($areaIds.ContainsKey($areaId.ToLowerInvariant())) {
        $errors.Add("Duplicate ownership area id: $areaId")
    }
    else {
        $areaIds[$areaId.ToLowerInvariant()] = $area
    }

    foreach ($patternValue in @($area.patterns)) {
        $pattern = Normalize-RepoPath -Path ([string]$patternValue)
        $key = $pattern.ToLowerInvariant()
        if ($patternOwners.ContainsKey($key)) {
            $errors.Add("Ownership pattern '$pattern' is declared by both '$($patternOwners[$key])' and '$areaId'.")
        }
        else {
            $patternOwners[$key] = $areaId
        }
    }

    if ($area.PSObject.Properties.Name -contains 'entrypoint') {
        $requiredPaths += [string]$area.entrypoint
    }
    foreach ($testPath in @($area.tests)) {
        $requiredPaths += [string]$testPath
    }
}

if ($Product -ne 'All' -and -not $areaIds.ContainsKey($Product.ToLowerInvariant())) {
    $errors.Add("Unknown product or area '$Product'. Valid values: All, $(@($areaIds.Keys | Sort-Object) -join ', ').")
}

foreach ($relativePath in @($requiredPaths | Sort-Object -Unique)) {
    if ([string]::IsNullOrWhiteSpace($relativePath)) {
        $errors.Add('Manifest contains an empty required path.')
        continue
    }
    $fullPath = Resolve-CanonicalPath -RelativePath $relativePath
    if (-not (Test-Path -LiteralPath $fullPath)) {
        $errors.Add("Required canonical path is missing: $relativePath")
    }
}

$projectVersion = ''
$packageVersion = ''
$pyprojectPath = Resolve-CanonicalPath -RelativePath ([string]$manifest.version_files.project)
$packagePath = Resolve-CanonicalPath -RelativePath ([string]$manifest.version_files.package)
if (Test-Path -LiteralPath $pyprojectPath) {
    $match = [regex]::Match([System.IO.File]::ReadAllText($pyprojectPath), '(?m)^version\s*=\s*"([^"]+)"')
    if ($match.Success) {
        $projectVersion = $match.Groups[1].Value
    }
    else {
        $errors.Add("Could not read project version from $($manifest.version_files.project).")
    }
}
if (Test-Path -LiteralPath $packagePath) {
    $match = [regex]::Match([System.IO.File]::ReadAllText($packagePath), '(?m)^__version__\s*=\s*"([^"]+)"')
    if ($match.Success) {
        $packageVersion = $match.Groups[1].Value
    }
    else {
        $errors.Add("Could not read package version from $($manifest.version_files.package).")
    }
}
if ($projectVersion -and $packageVersion -and $projectVersion -ne $packageVersion) {
    $errors.Add("Version mismatch: project=$projectVersion package=$packageVersion")
}

$managedFiles = New-Object System.Collections.Generic.List[string]
foreach ($topLevel in @('python_vna', 'tests')) {
    $topLevelPath = Resolve-CanonicalPath -RelativePath $topLevel
    if (-not (Test-Path -LiteralPath $topLevelPath -PathType Container)) {
        continue
    }
    foreach ($file in Get-ChildItem -LiteralPath $topLevelPath -Recurse -File -Filter '*.py') {
        if (-not $file.FullName.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $managedFiles.Add((Normalize-RepoPath -Path $file.FullName.Substring($rootPrefix.Length)))
    }
}

foreach ($relativePath in @($managedFiles | Sort-Object -Unique)) {
    $owners = New-Object System.Collections.Generic.List[string]
    foreach ($area in $areas) {
        if (Test-AnyPattern -Path $relativePath -Patterns @($area.patterns)) {
            $owners.Add([string]$area.id)
        }
    }
    if ($owners.Count -eq 0) {
        $errors.Add("Unowned Python source or test file: $relativePath")
    }
    elseif ($owners.Count -gt 1) {
        $errors.Add("File has multiple ownership areas ($($owners -join ', ')): $relativePath")
    }
}

if ($RequireClean) {
    $statusLines = @(& git -C $root status --porcelain=v1 --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        $errors.Add('Could not inspect canonical repository git status.')
    }
    else {
        $meaningfulStatus = @()
        foreach ($statusLine in $statusLines) {
            $statusPath = Get-StatusPath -StatusLine ([string]$statusLine)
            if (-not $statusPath) {
                continue
            }
            if (-not (Test-AnyPattern -Path $statusPath -Patterns @($manifest.generated))) {
                $meaningfulStatus += $statusLine
            }
        }
        if ($meaningfulStatus.Count -gt 0) {
            $errors.Add("Canonical repository is not clean ($($meaningfulStatus.Count) changed paths). Commit or stash before creating a feature worktree.")
        }
    }
}

$legacyDirtyCount = 0
if ($CheckLegacyWorktrees -or $FailOnLegacyDirty) {
    foreach ($legacy in @($manifest.legacy_worktrees)) {
        $legacyPath = Resolve-CanonicalPath -RelativePath ([string]$legacy.path)
        if (-not (Test-Path -LiteralPath $legacyPath -PathType Container)) {
            Write-Warning "Legacy worktree is absent: $($legacy.id) ($($legacy.path))"
            continue
        }
        $safeDirectory = $legacyPath.Replace('\', '/')
        $statusLines = @(& git -c "safe.directory=$safeDirectory" -C $legacyPath status --porcelain=v1 --untracked-files=all)
        if ($LASTEXITCODE -ne 0) {
            $errors.Add("Could not inspect legacy worktree: $($legacy.id)")
            continue
        }
        if ($statusLines.Count -gt 0) {
            $legacyDirtyCount += $statusLines.Count
            $message = "Legacy worktree '$($legacy.id)' has $($statusLines.Count) dirty paths. It is migration-only and is not used by builds."
            if ($FailOnLegacyDirty) {
                $errors.Add($message)
            }
            else {
                Write-Warning $message
            }
        }
    }
}

if ($errors.Count -gt 0) {
    Write-Host ''
    Write-Host 'PythonVNA suite preflight failed:' -ForegroundColor Red
    foreach ($message in $errors) {
        Write-Host "  - $message" -ForegroundColor Red
    }
    throw "Preflight failed with $($errors.Count) error(s)."
}

Write-Host 'PythonVNA suite preflight passed.' -ForegroundColor Green
Write-Host "Canonical root: $root"
Write-Host "Version: $projectVersion"
Write-Host "Managed Python files: $($managedFiles.Count)"
Write-Host "Ownership areas: $(@($areaIds.Keys | Sort-Object) -join ', ')"
if ($CheckLegacyWorktrees -or $FailOnLegacyDirty) {
    Write-Host "Legacy dirty paths reported: $legacyDirtyCount"
}
