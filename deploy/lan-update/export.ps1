param([string]$ManifestPath = '', [string]$OutputPath = '')
. "$PSScriptRoot/common.ps1"
if (-not $ManifestPath) { $ManifestPath = Read-Host 'Public release manifest.json path' }
$ManifestPath = (Resolve-Path -LiteralPath $ManifestPath).Path
$sourceRoot = Split-Path -Parent $ManifestPath
$manifest = Read-JsonFile $ManifestPath
Assert-Version $manifest.latest
if (-not $OutputPath) { $OutputPath = Join-Path $sourceRoot "LAN_v$($manifest.latest)" }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if (($OutputPath.TrimEnd('\') + '\').StartsWith(([IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output must not be inside the tools directory'
}
if (Test-Path -LiteralPath $OutputPath) { throw "Output already exists (choose an empty new path): $OutputPath" }
[void][IO.Directory]::CreateDirectory($OutputPath)
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
Write-Host "LAN export verified: $OutputPath"
