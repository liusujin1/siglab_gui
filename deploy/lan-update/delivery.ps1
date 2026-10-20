. "$PSScriptRoot/transfer.ps1"
. "$PSScriptRoot/delivery-runtime.ps1"

function Get-LanDeliveryMetadata([string]$Folder) {
    $launcher = Join-Path $Folder 'deploy_to_server.bat'
    Assert-NoLink $launcher -AllowCloud
    $text = [IO.File]::ReadAllText($launcher)
    if ($text -notmatch '(?m)^\$deliveryMetadataBase64 = ''([A-Za-z0-9+/=]+)''\r?$') { throw 'Invalid compact delivery launcher or old delivery format' }
    return ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Matches[1])) | ConvertFrom-Json)
}

function New-LanDeliveryLauncher($Metadata) {
    $base64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($Metadata | ConvertTo-Json -Depth 12 -Compress)))
    $batch = @'
@echo off
setlocal
set "PYTHONVNA_DELIVERY_SELF=%~f0"
set "PYTHONVNA_DELIVERY_ELEVATED="
set "PYTHONVNA_DELIVERY_MODE=%~1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$text=[IO.File]::ReadAllText($env:PYTHONVNA_DELIVERY_SELF); $marker=[Environment]::NewLine+'# POWERSHELL_PAYLOAD'+[Environment]::NewLine; & ([scriptblock]::Create($text.Substring($text.IndexOf($marker)+$marker.Length)))"
set "RESULT=%ERRORLEVEL%"
echo Deployment exit code: %RESULT%
pause
exit /b %RESULT%
# POWERSHELL_PAYLOAD
'@
    $runner = @'
$deliveryMetadata = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($deliveryMetadataBase64)) | ConvertFrom-Json
$result = Invoke-LanDeliveryDeployment $env:PYTHONVNA_DELIVERY_SELF $deliveryMetadata -ExtractOnly:($env:PYTHONVNA_DELIVERY_MODE -eq '--extract-only')
if ($env:PYTHONVNA_DELIVERY_ELEVATED -eq '1') { [void](Read-Host 'Press Enter to close') }
exit $result
'@
    $lines = @($batch, ('$deliveryMetadataBase64 = ' + "'" + $base64 + "'"),
        [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'common.ps1')),
        [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'delivery-runtime.ps1')), $runner)
    return (($lines -join [Environment]::NewLine) -replace '\r?\n', [Environment]::NewLine)
}

function Test-LanDelivery([string]$Folder) {
    Assert-DeliveryFiles $Folder
    $metadata = Get-LanDeliveryMetadata $Folder
    Test-LanDeliveryParts $Folder $metadata
    $expectedLauncher = New-LanDeliveryLauncher $metadata
    if ([IO.File]::ReadAllText((Join-Path $Folder 'deploy_to_server.bat')) -cne $expectedLauncher) { throw 'Compact delivery launcher integrity check failed' }
    if (@(Get-ChildItem -LiteralPath $Folder -Force).Count -ne (@($metadata.parts).Count + 1)) { throw 'Unexpected files or directories in compact delivery' }
    return $metadata
}

function Write-LanDelivery([string]$Folder, [string]$Destination, [string]$SourceFingerprint, [long]$PartSizeBytes) {
    if ($PartSizeBytes -lt 1 -or $PartSizeBytes -gt 49000000) { throw 'Invalid delivery part size' }
    $manifest = Test-LanBundle $Folder
    $expandedSize = [long](Get-ChildItem -LiteralPath $Folder -File -Recurse | Measure-Object -Property Length -Sum).Sum
    Assert-LanSpace $Destination (4 * $expandedSize + 16777216)
    $archivePath = Join-Path $Destination 'delivery.pending'
    [IO.Compression.ZipFile]::CreateFromDirectory($Folder, $archivePath, [IO.Compression.CompressionLevel]::Optimal, $false)
    $metadata = [ordered]@{
        schema=2; version=$manifest.latest; source_fingerprint=$SourceFingerprint; part_size_bytes=$PartSizeBytes
        archive_size=(Get-Item -LiteralPath $archivePath).Length; archive_sha256=(Get-Sha256 $archivePath)
        expanded_size=$expandedSize
        parts=@()
    }
    $inputStream = [IO.File]::OpenRead($archivePath)
    $buffer = [byte[]]::new(1048576)
    try {
        $index = 0
        while ($inputStream.Position -lt $inputStream.Length) {
            $index++
            $name = 'LAN_v{0}.zip.part{1:D5}' -f $manifest.latest, $index
            $path = Join-Path $Destination $name
            $outputStream = [IO.File]::Open($path, [IO.FileMode]::CreateNew)
            try {
                $remaining = [Math]::Min($PartSizeBytes, $inputStream.Length - $inputStream.Position)
                while ($remaining -gt 0) {
                    $count = $inputStream.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                    if ($count -eq 0) { throw 'Unexpected end of delivery archive' }
                    $outputStream.Write($buffer, 0, $count)
                    $remaining -= $count
                }
            } finally { $outputStream.Dispose() }
            $metadata.parts += [ordered]@{name=$name; index=$index; size=(Get-Item -LiteralPath $path).Length; sha256=(Get-Sha256 $path)}
        }
    } finally { $inputStream.Dispose() }
    [IO.File]::Delete($archivePath)
    [IO.File]::WriteAllText((Join-Path $Destination 'deploy_to_server.bat'), (New-LanDeliveryLauncher $metadata), [Text.UTF8Encoding]::new($false))
    [void](Test-LanDelivery $Destination)
}
