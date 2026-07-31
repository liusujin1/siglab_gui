param(
    [ValidateSet('All', 'Test', 'Diagnostic')]
    [string[]]$Source = @('All'),

    [switch]$Apply,
    [switch]$Build,

    [string]$Version = '',

    [string[]]$ExtraPath = @(),

    [switch]$SkipTests,

    [switch]$SkipReleaseArchives
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot

$worktrees = @{
    Test = Join-Path $root '.worktrees\vna_diagnose_isolated'
    Diagnostic = Join-Path $root '.worktrees\vna_diagnostic_current'
}

# Current suite naming:
# - Test       -> PythonVNATest.exe
# - Diagnostic -> VIanalysis.exe
$syncPaths = @{
    Test = @(
        'python_vna\app.py',
        'python_vna\condition_notes.py',
        'python_vna\continuous_recording.py',
        'python_vna\daq\device_probe.py',
        'python_vna\daq\ni.py',
        'python_vna\ui\main_window.py',
        'tests\test_app.py',
        'tests\test_condition_notes.py',
        'tests\test_continuous_recording.py',
        'tests\test_main_window.py',
        'tests\test_signal_pipeline.py'
    )
    Diagnostic = @(
        'python_vna\analysis_algorithms.py',
        'python_vna\analysis_curve_editing.py',
        'python_vna\analysis_data.py',
        'python_vna\analysis_derivation.py',
        'python_vna\diagnostic\__init__.py',
        'python_vna\diagnostic\app.py',
        'python_vna\diagnostic\data.py',
        'python_vna\diagnostic\pages.py',
        'python_vna\diagnostic\shell.py',
        'python_vna\ui\analysis_viewer.py',
        'python_vna\ui\diagnostic_theme.py',
        'tests\test_analysis_viewer.py',
        'tests\test_diagnostic_app.py'
    )
}

function Resolve-WorkspacePath {
    param(
        [Parameter(Mandatory=$true)][string]$Base,
        [Parameter(Mandatory=$true)][string]$RelativePath
    )

    $baseFull = [System.IO.Path]::GetFullPath($Base).TrimEnd('\') + '\'
    $full = [System.IO.Path]::GetFullPath((Join-Path $Base $RelativePath))
    if (-not $full.StartsWith($baseFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes workspace root: $RelativePath"
    }
    return $full
}

function Test-DifferentPath {
    param(
        [Parameter(Mandatory=$true)][string]$SourcePath,
        [Parameter(Mandatory=$true)][string]$DestinationPath
    )

    if (-not (Test-Path -LiteralPath $SourcePath)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $DestinationPath)) {
        return $true
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & git diff --no-index --quiet -- $SourcePath $DestinationPath *> $null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -eq 0) {
        return $false
    }
    if ($exitCode -eq 1) {
        return $true
    }
    throw "git diff failed for $SourcePath -> $DestinationPath"
}

function Show-DiffStat {
    param(
        [Parameter(Mandatory=$true)][string]$SourcePath,
        [Parameter(Mandatory=$true)][string]$DestinationPath
    )

    if (Test-Path -LiteralPath $DestinationPath) {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & git diff --no-index --stat -- $DestinationPath $SourcePath 2>$null
            $exitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        if ($exitCode -gt 1) {
            throw "git diff --stat failed for $SourcePath -> $DestinationPath"
        }
        return
    }

    $kind = if (Test-Path -LiteralPath $SourcePath -PathType Container) { 'directory' } else { 'file' }
    Write-Host "  new $kind"
}

function Copy-PathIntoWorkspace {
    param(
        [Parameter(Mandatory=$true)][string]$SourcePath,
        [Parameter(Mandatory=$true)][string]$DestinationPath
    )

    if (Test-Path -LiteralPath $SourcePath -PathType Container) {
        if (-not (Test-Path -LiteralPath $DestinationPath)) {
            New-Item -ItemType Directory -Path $DestinationPath | Out-Null
        }
        $sourceRoot = [System.IO.Path]::GetFullPath($SourcePath).TrimEnd('\') + '\'
        Get-ChildItem -LiteralPath $SourcePath -Recurse -Force -File |
            Where-Object {
                $_.FullName -notmatch '\\__pycache__\\' -and
                $_.Extension -notin @('.pyc', '.pyo')
            } |
            ForEach-Object {
                $relativePath = $_.FullName.Substring($sourceRoot.Length)
                $targetPath = Join-Path $DestinationPath $relativePath
                $targetParent = Split-Path -Parent $targetPath
                if (-not (Test-Path -LiteralPath $targetParent)) {
                    New-Item -ItemType Directory -Path $targetParent | Out-Null
                }
                Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Force
            }
        return
    }

    $parent = Split-Path -Parent $DestinationPath
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
}

function Normalize-Version {
    param([Parameter(Mandatory=$true)][string]$Value)

    $versionText = $Value.Trim()
    if ($versionText.StartsWith('v', [System.StringComparison]::OrdinalIgnoreCase)) {
        $versionText = $versionText.Substring(1)
    }
    if ($versionText -notmatch '^\d+(\.\d+){1,3}([A-Za-z0-9._-]+)?$') {
        throw "Invalid version '$Value'. Use a value such as 2.9.3 or v2.9.3."
    }
    return $versionText
}

function Update-VersionFiles {
    param([Parameter(Mandatory=$true)][string]$NewVersion)

    $pyprojectPath = Resolve-WorkspacePath -Base $root -RelativePath 'pyproject.toml'
    $initPath = Resolve-WorkspacePath -Base $root -RelativePath 'python_vna\__init__.py'

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    $pyprojectText = [System.IO.File]::ReadAllText($pyprojectPath)
    $pyprojectVersionPattern = '(?m)^version\s*=\s*"[^"]+"'
    if (-not [regex]::IsMatch($pyprojectText, $pyprojectVersionPattern)) {
        throw "Could not find project version in $pyprojectPath"
    }
    $updatedPyproject = [regex]::Replace(
        $pyprojectText,
        $pyprojectVersionPattern,
        "version = `"$NewVersion`"",
        1
    )
    if ($updatedPyproject -ne $pyprojectText) {
        [System.IO.File]::WriteAllText($pyprojectPath, $updatedPyproject, $utf8NoBom)
    }
    else {
        Write-Host "Project version already set to $NewVersion"
    }

    $initText = [System.IO.File]::ReadAllText($initPath)
    $packageVersionPattern = '(?m)^__version__\s*=\s*"[^"]+"'
    if (-not [regex]::IsMatch($initText, $packageVersionPattern)) {
        throw "Could not find package version in $initPath"
    }
    $updatedInit = [regex]::Replace(
        $initText,
        $packageVersionPattern,
        "__version__ = `"$NewVersion`"",
        1
    )
    if ($updatedInit -ne $initText) {
        [System.IO.File]::WriteAllText($initPath, $updatedInit, $utf8NoBom)
    }
    else {
        Write-Host "Package version already set to $NewVersion"
    }

    Write-Host "Set suite version to $NewVersion"
}

function Apply-SuiteLocalPatches {
    $mainWindowPath = Resolve-WorkspacePath -Base $root -RelativePath 'python_vna\ui\main_window.py'
    if (-not (Test-Path -LiteralPath $mainWindowPath)) {
        return
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $text = [System.IO.File]::ReadAllText($mainWindowPath)
    $patched = $text.Replace(
        'if self.orientation in {"left", "right"} and hasattr(self, "enableAutoSIPrefix"):',
        'if hasattr(self, "enableAutoSIPrefix"):'
    )
    if ($patched -ne $text) {
        [System.IO.File]::WriteAllText($mainWindowPath, $patched, $utf8NoBom)
        Write-Host "Applied suite local patch: disable VnaAxisItem auto SI prefix on all axes"
    }
}

$selectedSources = if ($Source -contains 'All') {
    @('Test', 'Diagnostic')
}
else {
    $Source
}

$plan = New-Object System.Collections.Generic.List[object]
$destinations = @{}

foreach ($name in $selectedSources) {
    $worktree = $worktrees[$name]
    if (-not (Test-Path -LiteralPath $worktree)) {
        throw "Worktree not found for ${name}: $worktree"
    }

    $paths = @($syncPaths[$name])
    if ($ExtraPath.Count -gt 0) {
        $paths += $ExtraPath
    }

    foreach ($relativePath in $paths) {
        $sourcePath = Resolve-WorkspacePath -Base $worktree -RelativePath $relativePath
        if (-not (Test-Path -LiteralPath $sourcePath)) {
            Write-Warning "Skipping missing source path: $name -> $relativePath"
            continue
        }

        $destinationPath = Resolve-WorkspacePath -Base $root -RelativePath $relativePath
        $key = $destinationPath.ToLowerInvariant()
        if ($destinations.ContainsKey($key)) {
            $existing = $destinations[$key]
            if (-not (Test-DifferentPath -SourcePath $sourcePath -DestinationPath $existing.SourcePath)) {
                continue
            }
            throw "Destination selected more than once: $relativePath from $($existing.SourceName) and $name"
        }
        $destinations[$key] = [pscustomobject]@{
            SourceName = $name
            SourcePath = $sourcePath
        }

        if (Test-DifferentPath -SourcePath $sourcePath -DestinationPath $destinationPath) {
            $plan.Add([pscustomobject]@{
                SourceName = $name
                RelativePath = $relativePath
                SourcePath = $sourcePath
                DestinationPath = $destinationPath
            })
        }
    }
}

if ($plan.Count -eq 0) {
    Write-Host "No mapped differences found."
}
else {
    Write-Host "Mapped differences:"
    foreach ($item in $plan) {
        Write-Host ""
        Write-Host "[$($item.SourceName)] $($item.RelativePath)"
        Show-DiffStat -SourcePath $item.SourcePath -DestinationPath $item.DestinationPath
    }
}

if (-not $Apply) {
    Write-Host ""
    Write-Host "Preview only. Re-run with -Apply to copy mapped files into the suite project."
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $previewVersion = Normalize-Version -Value $Version
        Write-Host "Preview only. Re-run with -Apply to set version to $previewVersion."
    }
}
else {
    foreach ($item in $plan) {
        Copy-PathIntoWorkspace -SourcePath $item.SourcePath -DestinationPath $item.DestinationPath
        Write-Host "Copied [$($item.SourceName)] $($item.RelativePath)"
    }
    Apply-SuiteLocalPatches
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $newVersion = Normalize-Version -Value $Version
        Update-VersionFiles -NewVersion $newVersion
    }
}

if ($Build) {
    if (-not $Apply -and $plan.Count -gt 0) {
        Write-Host ""
        Write-Host "Building current suite project without applying previewed worktree differences."
    }
    $buildArgs = @()
    if ($SkipTests) {
        $buildArgs += '-SkipTests'
    }
    if ($SkipReleaseArchives) {
        $buildArgs += '-SkipReleaseArchives'
    }
    & (Join-Path $PSScriptRoot 'build_vna_suite.ps1') @buildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Suite build failed with exit code $LASTEXITCODE"
    }
}
