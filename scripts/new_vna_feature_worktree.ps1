param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('python_vna_test', 'vianalysis', 'shared')]
    [string]$Product,

    [Parameter(Mandatory=$true)]
    [string]$TaskName,

    [string]$BaseRef = 'HEAD',

    [string]$WorktreeRoot = '.worktrees',

    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'

$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$preflight = Join-Path $PSScriptRoot 'preflight_vna_suite.ps1'

$slug = $TaskName.Trim().ToLowerInvariant() -replace '[^a-z0-9]+', '-'
$slug = $slug.Trim('-')
if ([string]::IsNullOrWhiteSpace($slug)) {
    throw 'TaskName must contain at least one ASCII letter or digit.'
}

$productSlug = $Product.Replace('_', '-')
$branch = "codex/$productSlug-$slug"
$worktreeBase = if ([System.IO.Path]::IsPathRooted($WorktreeRoot)) {
    [System.IO.Path]::GetFullPath($WorktreeRoot)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $root $WorktreeRoot))
}
$target = Join-Path $worktreeBase "$productSlug-$slug"

$preflightArgs = @{
    Product = $Product
}
if (-not $PlanOnly) {
    $preflightArgs.RequireClean = $true
}
& $preflight @preflightArgs

Write-Host "Product: $Product"
Write-Host "Branch: $branch"
Write-Host "Worktree: $target"
Write-Host "Base ref: $BaseRef"

if ($PlanOnly) {
    Write-Host 'Plan only; no branch or worktree was created.'
    exit 0
}

if (Test-Path -LiteralPath $target) {
    throw "Worktree path already exists: $target"
}

& git -C $root show-ref --verify --quiet "refs/heads/$branch"
if ($LASTEXITCODE -eq 0) {
    throw "Branch already exists: $branch"
}

New-Item -ItemType Directory -Path $worktreeBase -Force | Out-Null
& git -C $root worktree add -b $branch $target $BaseRef
if ($LASTEXITCODE -ne 0) {
    throw "git worktree add failed with exit code $LASTEXITCODE"
}

Write-Host ''
Write-Host 'Feature worktree created.' -ForegroundColor Green
Write-Host "Run product tests with:"
Write-Host "  powershell -NoProfile -File scripts\test_vna_product.ps1 -Product $Product"
