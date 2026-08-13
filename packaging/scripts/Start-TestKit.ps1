param([string]$Profile = '')
& (Join-Path $PSScriptRoot 'Start-Samba.ps1') -Profile $Profile
& (Join-Path $PSScriptRoot 'Start-SIDMAT.ps1') -Profile $Profile
