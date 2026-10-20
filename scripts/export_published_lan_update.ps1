param([Parameter(Mandatory=$true)][string]$ManifestPath, [switch]$SkipLanExport)
$ErrorActionPreference = 'Stop'
Write-Host 'PUBLIC PUBLICATION: SUCCEEDED'
if ($SkipLanExport) { Write-Host 'LAN EXPORT: SKIPPED'; return }
try {
    & "$PSScriptRoot/../deploy/lan-update/export.ps1" -ManifestPath $ManifestPath
    Write-Host 'LAN EXPORT: SUCCEEDED'
} catch {
    Write-Warning 'PUBLIC PUBLICATION remains successful; LAN EXPORT FAILED. Retry export.ps1 only; do not upload again.'
    throw
}
