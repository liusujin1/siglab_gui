param(
    [string]$NasUser = 'liusu',
    [string]$CredentialPath = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($CredentialPath)) {
    $CredentialPath = Join-Path $env:APPDATA 'PythonVNA\nas_credential.xml'
}

$parent = Split-Path -Parent $CredentialPath
if (-not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
}

$secure = Read-Host "NAS SSH password for $NasUser" -AsSecureString
$credential = [pscredential]::new($NasUser, $secure)
$credential | Export-Clixml -LiteralPath $CredentialPath

Write-Host "Saved encrypted NAS credential for $NasUser to:"
Write-Host $CredentialPath
Write-Host "This credential can only be decrypted by the current Windows user on this computer."
