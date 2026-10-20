param([string]$ManifestPath = '', [string]$OutputPath = '', [ValidateRange(1,49000000)][long]$PartSizeBytes = 49000000,
    [ValidateSet('Compact','Loose')][string]$DeliveryLayout = 'Compact')
. "$PSScriptRoot/delivery.ps1"
function Publish-DeliveryDirectory([string]$Source, [string]$Destination) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Test-Path -LiteralPath $Destination) { throw "Output already exists: $Destination" }
        try { [IO.Directory]::Move($Source, $Destination); return }
        catch {
            $nativeError = $_.Exception.GetBaseException().HResult -band 0xffff
            if ($attempt -eq 19 -or $nativeError -notin @(5,32,33)) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
}
if (-not $ManifestPath) { $ManifestPath = Read-Host 'Public release manifest.json path' }
$ManifestPath = (Get-Item -LiteralPath $ManifestPath).FullName
$sourceRoot = Split-Path -Parent $ManifestPath
$manifest = Read-JsonFile $ManifestPath
Assert-Version $manifest.latest
if (-not $OutputPath) {
    $directoryName = "LAN_v$($manifest.latest)"
    if ($DeliveryLayout -eq 'Compact') { $directoryName += '_delivery' }
    $OutputPath = Join-Path $sourceRoot $directoryName
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if (($OutputPath.TrimEnd('\') + '\').StartsWith(([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output must not be inside the tools directory'
}
Assert-NoLink $OutputPath -AllowCloud
$fingerprintText = (Get-Sha256 $ManifestPath) + ':' + $PartSizeBytes + ':' + $DeliveryLayout
foreach ($tool in (Get-ChildItem -LiteralPath $PSScriptRoot -File -Recurse | Sort-Object FullName)) {
    $fingerprintText += ':' + $tool.FullName.Substring($PSScriptRoot.Length) + ':' + (Get-Sha256 $tool.FullName)
}
$algorithm = [Security.Cryptography.SHA256]::Create()
try { $fingerprint = ([BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($fingerprintText)))).Replace('-','').ToLowerInvariant() }
finally { $algorithm.Dispose() }
$finalOutput = $OutputPath
foreach ($item in (Get-Entries $manifest)) {
    if ($null -eq $item -or $item.archive_type -ne 'zip') { throw 'Source manifest must use ZIP packages' }
    $leaf = [IO.Path]::GetFileName(([uri]::UnescapeDataString(($item.url -split '[?#]')[0])))
    if ($leaf -notmatch '^PythonVNA_(Suite|Update)_v[0-9._a-z]+\.zip$') { throw 'Unexpected source package name' }
    $source = Join-Path $sourceRoot $leaf
    if (-not (Test-Path -LiteralPath $source)) { $source = Join-Path $sourceRoot "updates/$leaf" }
    Assert-NoLink $source -AllowCloud
    if ((Get-Item -LiteralPath $source).Length -ne [long]$item.size -or (Get-Sha256 $source) -ne $item.sha256) {
        throw "Source package integrity check failed: $source"
    }
}
if (Test-Path -LiteralPath $finalOutput) {
    if ($DeliveryLayout -eq 'Compact') { $existing = Test-LanDelivery $finalOutput }
    else { [void](Test-LanTransfer $finalOutput); $existing = Read-JsonFile (Join-Path $finalOutput 'transport.json') }
    if ($existing.source_fingerprint -ne $fingerprint) {
        throw "Output already exists with different content or tools: $finalOutput"
    }
    Write-Host "LAN export reused and verified: $finalOutput"
    return
}
$required = [long]16777216
foreach ($item in (Get-Entries $manifest)) { $required += 6 * [long]$item.size }
Assert-LanSpace (Split-Path -Parent $OutputPath) $required
$OutputPath = $finalOutput + '.' + [guid]::NewGuid().ToString('N') + '.staging'
[void][IO.Directory]::CreateDirectory($OutputPath)
try {
foreach ($item in (Get-Entries $manifest)) {
    if ($null -eq $item -or $item.archive_type -ne 'zip') { throw 'Source manifest must use ZIP packages' }
    $leaf = [IO.Path]::GetFileName(([uri]::UnescapeDataString(($item.url -split '[?#]')[0])))
    if ($leaf -notmatch '^PythonVNA_(Suite|Update)_v[0-9._a-z]+\.zip$') { throw 'Unexpected source package name' }
    $source = Join-Path $sourceRoot $leaf
    if (-not (Test-Path -LiteralPath $source)) { $source = Join-Path $sourceRoot "updates/$leaf" }
    if ((Get-Item -LiteralPath $source).Length -ne [long]$item.size -or
        (Get-Sha256 $source) -ne $item.sha256) {
        throw "Source package integrity check failed: $source"
    }
    $temporary = Join-Path $OutputPath 'package.pending'
    $inputZip = [IO.Compression.ZipFile]::OpenRead($source)
    $outputZip = [IO.Compression.ZipFile]::Open($temporary, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($entry in $inputZip.Entries) {
            $name = Assert-ZipPath $entry.FullName
            if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'Source ZIP contains symlink' }
            if (Test-ProtectedConfig $name) { continue }
            $target = $outputZip.CreateEntry($name, [IO.Compression.CompressionLevel]::Optimal)
            $target.LastWriteTime = $entry.LastWriteTime
            if ($name.EndsWith('/')) { continue }
            $inputStream = $entry.Open()
            $outputStream = $target.Open()
            try {
                if ($name.Split('/')[-1] -in @('UPDATE_REMOVED_FILES.txt','UPDATE_CHANGED_FILES.txt')) {
                    $reader = [IO.StreamReader]::new($inputStream)
                    try {
                        $lines = @($reader.ReadToEnd() -split '\r?\n' | Where-Object { $_.Trim() -and -not (Test-ProtectedConfig $_.Trim()) })
                        foreach ($line in $lines) { [void](Assert-ZipPath $line.Trim()) }
                        $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($lines -join "`n") + "`n")
                        $outputStream.Write($bytes, 0, $bytes.Length)
                    } finally { $reader.Dispose() }
                } else { $inputStream.CopyTo($outputStream) }
            } finally { $inputStream.Dispose(); $outputStream.Dispose() }
        }
    } finally { $inputZip.Dispose(); $outputZip.Dispose() }
    $hash = Get-Sha256 $temporary
    $name = 'LAN_' + [IO.Path]::GetFileNameWithoutExtension($leaf) + "_$hash.zip"
    [IO.File]::Move($temporary, (Join-Path $OutputPath $name))
    $item.url = $name
    $item.sha256 = $hash
    $item.size = (Get-Item -LiteralPath (Join-Path $OutputPath $name)).Length
}
Write-JsonFile (Join-Path $OutputPath 'manifest.json') $manifest
[void](Test-LanBundle $OutputPath)
Copy-Item -LiteralPath $PSScriptRoot -Destination (Join-Path $OutputPath 'tools') -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'deploy_to_server.bat') -Destination (Join-Path $OutputPath 'deploy_to_server.bat')
if ($DeliveryLayout -eq 'Compact') {
    Write-Host '[Pack and split] Packing ALL files into one delivery archive'
    $compactOutput = $finalOutput + '.' + [guid]::NewGuid().ToString('N') + '.staging'
    [void][IO.Directory]::CreateDirectory($compactOutput)
    Write-LanDelivery $OutputPath $compactOutput $fingerprint $PartSizeBytes
    Publish-DeliveryDirectory $compactOutput $finalOutput
    try { Remove-LanDeliveryWork $OutputPath (Split-Path -Parent $OutputPath) }
    catch { Write-Warning "Export succeeded; working directory cleanup incomplete: $_" }
    Write-Host "Transfer only $(@(Get-ChildItem -LiteralPath $finalOutput -File).Count) files: archive parts and deploy_to_server.bat"
} else {
    Write-Host '[Split] Preparing loose transport files'
    Write-LanTransfer $OutputPath $fingerprint $PartSizeBytes
    [void](Test-LanTransfer $OutputPath)
    Publish-DeliveryDirectory $OutputPath $finalOutput
}
Write-Host "LAN export verified: $finalOutput"
} catch {
    [IO.File]::WriteAllText((Join-Path $OutputPath 'export-error.log'), ($_ | Out-String))
    Write-Warning "LAN export failed; diagnostic staging retained: $OutputPath; compact staging: $compactOutput"
    throw
}
