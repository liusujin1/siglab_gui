param(
    [Parameter(Mandatory=$true)]
    [string]$Version
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pyprojectPath = Join-Path $root 'pyproject.toml'
$packagePath = Join-Path $root 'python_vna\__init__.py'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$versionText = $Version.Trim()
if ($versionText.StartsWith('v', [System.StringComparison]::OrdinalIgnoreCase)) {
    $versionText = $versionText.Substring(1)
}
if ($versionText -notmatch '^\d+(\.\d+){1,3}([A-Za-z0-9._-]+)?$') {
    throw "Invalid version '$Version'. Use a value such as 3.2.11 or v3.2.11."
}

function Update-VersionAssignment {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Pattern,
        [Parameter(Mandatory=$true)][string]$Replacement
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Version file was not found: $Path"
    }
    $text = [System.IO.File]::ReadAllText($Path)
    if (-not [regex]::IsMatch($text, $Pattern)) {
        throw "Could not find version assignment in $Path"
    }
    $assignmentRegex = [regex]::new($Pattern)
    $updated = $assignmentRegex.Replace($text, $Replacement, 1)
    if ($updated -ne $text) {
        [System.IO.File]::WriteAllText($Path, $updated, $utf8NoBom)
        Write-Host "Updated $Path"
    }
}

Update-VersionAssignment `
    -Path $pyprojectPath `
    -Pattern '(?m)^version\s*=\s*"[^"]+"' `
    -Replacement "version = `"$versionText`""
Update-VersionAssignment `
    -Path $packagePath `
    -Pattern '(?m)^__version__\s*=\s*"[^"]+"' `
    -Replacement "__version__ = `"$versionText`""

Write-Host "PythonVNA Suite version: $versionText"
