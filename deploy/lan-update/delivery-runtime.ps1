function Test-LanDeliveryParts([string]$Folder, $Metadata) {
    if ($Metadata.schema -ne 2 -or $Metadata.source_fingerprint -notmatch '^[a-f0-9]{64}$' -or
        $Metadata.archive_sha256 -notmatch '^[a-f0-9]{64}$' -or $Metadata.archive_size -notmatch '^\d+$' -or
        [long]$Metadata.archive_size -lt 1 -or $Metadata.expanded_size -notmatch '^\d+$' -or
        $Metadata.part_size_bytes -notmatch '^\d+$' -or [long]$Metadata.part_size_bytes -lt 1 -or
        [long]$Metadata.part_size_bytes -gt 49000000) { throw 'Invalid delivery metadata' }
    Assert-Version $Metadata.version
    $count = [long][Math]::Ceiling([decimal]$Metadata.archive_size / [long]$Metadata.part_size_bytes)
    if (@($Metadata.parts).Count -ne $count) { throw 'Missing or extra delivery parts' }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $buffer = [byte[]]::new(1048576)
    try {
    for ($index = 0; $index -lt $count; $index++) {
        $part = $Metadata.parts[$index]
        $expectedName = 'LAN_v{0}.zip.part{1:D5}' -f $Metadata.version, ($index + 1)
        $expectedSize = [Math]::Min([long]$Metadata.part_size_bytes, [long]$Metadata.archive_size - $index * [long]$Metadata.part_size_bytes)
        if ($part.name -cne $expectedName -or $part.index -ne ($index + 1) -or $part.size -ne $expectedSize -or
            $part.sha256 -notmatch '^[a-f0-9]{64}$') { throw "Invalid delivery part order, path or size: $($part.name)" }
        $path = Join-Path $Folder $expectedName
        Assert-NoLink $path -AllowCloud
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing delivery part: $expectedName" }
        if ((Get-Item -LiteralPath $path).Length -ne $expectedSize -or (Get-Sha256 $path) -ne $part.sha256) {
            throw "Delivery part integrity check failed: $expectedName"
        }
        $stream = [IO.File]::OpenRead($path)
        try {
            while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                [void]$algorithm.TransformBlock($buffer, 0, $read, $buffer, 0)
            }
        } finally { $stream.Dispose() }
    }
    [void]$algorithm.TransformFinalBlock([byte[]]::new(0), 0, 0)
    $hash = ([BitConverter]::ToString($algorithm.Hash)).Replace('-','').ToLowerInvariant()
    if ($hash -ne $Metadata.archive_sha256) { throw 'Complete delivery archive hash mismatch' }
    } finally { $algorithm.Dispose() }
}

function Expand-LanDelivery([string]$Folder, $Metadata, [string]$WorkRoot) {
    Write-Host '[Validate] Checking whole-directory delivery parts'
    Test-LanDeliveryParts $Folder $Metadata
    Assert-NoLink $WorkRoot -AllowCloud
    if (@(Get-ChildItem -LiteralPath $WorkRoot -Force).Count) { throw "Extraction work directory must be empty: $WorkRoot" }
    $required = [decimal]$Metadata.archive_size + [decimal]$Metadata.expanded_size + 16777216
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($WorkRoot)))
    if ($drive.AvailableFreeSpace -lt $required) { throw "Insufficient delivery staging disk space: required=$required; available=$($drive.AvailableFreeSpace)" }
    $archivePath = Join-Path $WorkRoot 'delivery.zip'
    $outputStream = [IO.File]::Open($archivePath, [IO.FileMode]::CreateNew)
    try {
        foreach ($part in $Metadata.parts) {
            Write-Host "[Merge delivery] $($part.name)"
            $inputStream = [IO.File]::OpenRead((Join-Path $Folder $part.name))
            try { $inputStream.CopyTo($outputStream, 1048576) } finally { $inputStream.Dispose() }
        }
    } finally { $outputStream.Dispose() }
    if ((Get-Item -LiteralPath $archivePath).Length -ne [long]$Metadata.archive_size -or
        (Get-Sha256 $archivePath) -ne $Metadata.archive_sha256) { throw 'Complete delivery archive hash mismatch' }
    $bundle = Join-Path $WorkRoot 'bundle'
    [void][IO.Directory]::CreateDirectory($bundle)
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $expandedSize = [decimal]0
        foreach ($entry in $archive.Entries) {
            $name = Assert-ZipPath $entry.FullName
            if (-not $names.Add($name.TrimEnd('/')) -or
                ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000)) { throw "Duplicate or linked delivery entry: $name" }
            $expandedSize += $entry.Length
        }
        if ($expandedSize -ne [decimal]$Metadata.expanded_size) { throw 'Delivery expanded size mismatch' }
        Write-Host '[Extract] Restoring packages, manifest and deployment tools'
        foreach ($entry in $archive.Entries) {
            $name = Assert-ZipPath $entry.FullName
            $destination = Join-Path $bundle $name
            if ($name.EndsWith('/')) { [void][IO.Directory]::CreateDirectory($destination); continue }
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $destination))
            $outputStream = [IO.File]::Open($destination, [IO.FileMode]::CreateNew)
            try {
                $inputStream = $entry.Open()
                try { $inputStream.CopyTo($outputStream, 1048576) } finally { $inputStream.Dispose() }
            } finally { $outputStream.Dispose() }
        }
    } finally { $archive.Dispose() }
    [void](Test-LanBundle $bundle)
    return $bundle
}

function Remove-LanDeliveryWork([string]$Path, [string]$Parent) {
    $resolved = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $expectedParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if ((Split-Path -Parent $resolved) -ine $expectedParent -or
        (Split-Path -Leaf $resolved) -notmatch '(?:^PythonVNA-Delivery-|\.)[a-f0-9]{32}(?:\.staging)?$') {
        throw "Refusing to remove unmanaged delivery staging: $resolved"
    }
    Assert-NoLink $resolved -AllowCloud
    $pending = [Collections.Generic.Queue[string]]::new()
    $pending.Enqueue($resolved)
    while ($pending.Count) {
        foreach ($item in (Get-ChildItem -LiteralPath $pending.Dequeue() -Force)) {
            Assert-NoLink $item.FullName -AllowCloud
            if ($item.PSIsContainer) { $pending.Enqueue($item.FullName) }
        }
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Invoke-LanDeliveryDeployment([string]$LauncherPath, $Metadata, [switch]$ExtractOnly) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $ExtractOnly -and -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if ($env:PYTHONVNA_DELIVERY_ELEVATED -eq '1') { throw 'Administrator rights were not granted' }
        $command = '$env:PYTHONVNA_DELIVERY_SELF = ' + "'" + $LauncherPath.Replace("'", "''") + "'; "
        $command += '$env:PYTHONVNA_DELIVERY_ELEVATED = ''1''; $text=[IO.File]::ReadAllText($env:PYTHONVNA_DELIVERY_SELF); '
        $command += '$marker=[Environment]::NewLine+''# POWERSHELL_PAYLOAD''+[Environment]::NewLine; & ([scriptblock]::Create($text.Substring($text.IndexOf($marker)+$marker.Length)))'
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        try {
            $process = Start-Process -FilePath "$PSHOME/powershell.exe" -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -Wait -PassThru
            return $process.ExitCode
        } catch { Write-Host "Elevation cancelled or unavailable; no deployment performed: $_"; return 1 }
    }
    $work = Join-Path ([IO.Path]::GetTempPath()) ('PythonVNA-Delivery-' + [guid]::NewGuid().ToString('N'))
    Assert-NoLink $work
    [void][IO.Directory]::CreateDirectory($work)
    try {
        $bundle = Expand-LanDelivery (Split-Path -Parent $LauncherPath) $Metadata $work
        if ($ExtractOnly) { Write-Host "EXTRACTED_BUNDLE=$bundle"; return 0 }
        & "$PSHOME/powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $bundle 'tools/deploy-to-server.ps1') -BundlePath $bundle
        $result = $LASTEXITCODE
        if ($result -in @(0,2)) {
            try { Remove-LanDeliveryWork $work ([IO.Path]::GetTempPath()) }
            catch { Write-Warning "Publication succeeded; delivery staging cleanup incomplete: $_" }
        } else { throw "Deployment failed with exit code $result; restored delivery: $bundle" }
        return $result
    } catch {
        $log = Join-Path $work 'delivery-error.log'
        [IO.File]::WriteAllText($log, ($_ | Out-String))
        Write-Host "DELIVERY FAILED: $_"
        Write-Host "Log and staging retained: $work"
        return 1
    }
}
