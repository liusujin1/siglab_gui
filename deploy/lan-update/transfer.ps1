. "$PSScriptRoot/common.ps1"

function Assert-LanSpace([string]$Folder, [long]$RequiredBytes) {
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Folder)))
    if ($drive.AvailableFreeSpace -lt $RequiredBytes) {
        throw "Insufficient disk space: $Folder; required=$RequiredBytes; available=$($drive.AvailableFreeSpace)"
    }
}

function Assert-DeliveryFiles([string]$Folder) {
    Assert-NoLink $Folder -AllowCloud
    foreach ($item in (Get-ChildItem -LiteralPath $Folder -Force)) {
        Assert-NoLink $item.FullName -AllowCloud
        if ($item.PSIsContainer) { Assert-DeliveryFiles $item.FullName }
        elseif ($item.Length -gt 49000000) { throw "Delivery file exceeds 49000000 bytes: $($item.FullName)" }
    }
}

function Write-LanTransfer([string]$Folder, [string]$SourceFingerprint, [long]$PartSizeBytes = 49000000) {
    if ($PartSizeBytes -lt 1 -or $PartSizeBytes -gt 49000000) { throw 'Invalid part size' }
    $manifest = Test-LanBundle $Folder
    $files = @()
    $buffer = [byte[]]::new(1048576)
    foreach ($entry in (Get-Entries $manifest)) {
        $original = Join-Path $Folder $entry.url
        $parts = @()
        if ([long]$entry.size -le $PartSizeBytes) {
            $parts += [ordered]@{name=$entry.url; index=1; size=[long]$entry.size; sha256=$entry.sha256}
        } else {
            $inputStream = [IO.File]::OpenRead($original)
            try {
                $index = 0
                while ($inputStream.Position -lt $inputStream.Length) {
                    $index++
                    $name = '{0}.part{1:D5}' -f $entry.url, $index
                    $path = Join-Path $Folder $name
                    $outputStream = [IO.File]::Open($path, [IO.FileMode]::CreateNew)
                    try {
                        $remaining = [Math]::Min($PartSizeBytes, $inputStream.Length - $inputStream.Position)
                        while ($remaining -gt 0) {
                            $count = $inputStream.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                            if ($count -eq 0) { throw "Unexpected end of package: $original" }
                            $outputStream.Write($buffer, 0, $count)
                            $remaining -= $count
                        }
                    } finally { $outputStream.Dispose() }
                    $parts += [ordered]@{name=$name; index=$index; size=(Get-Item -LiteralPath $path).Length; sha256=(Get-Sha256 $path)}
                }
            } finally { $inputStream.Dispose() }
            [IO.File]::Delete($original)
        }
        $files += [ordered]@{name=$entry.url; size=[long]$entry.size; sha256=$entry.sha256; parts=@($parts)}
    }
    $supportFiles = @()
    foreach ($support in (Get-ChildItem -LiteralPath (Join-Path $Folder 'tools') -File -Recurse)) {
        $supportFiles += [ordered]@{name=$support.FullName.Substring($Folder.TrimEnd('\').Length + 1).Replace('\','/'); size=$support.Length; sha256=(Get-Sha256 $support.FullName)}
    }
    $launcher = Join-Path $Folder 'deploy_to_server.bat'
    $supportFiles += [ordered]@{name='deploy_to_server.bat'; size=(Get-Item -LiteralPath $launcher).Length; sha256=(Get-Sha256 $launcher)}
    Write-JsonFile (Join-Path $Folder 'transport.json') ([ordered]@{
        schema=1; max_file_bytes=49000000; part_size_bytes=$PartSizeBytes
        manifest_sha256=(Get-Sha256 (Join-Path $Folder 'manifest.json'))
        source_fingerprint=$SourceFingerprint; files=@($files); support_files=@($supportFiles)
    })
}

function Test-LanTransfer([string]$Folder) {
    Assert-DeliveryFiles $Folder
    $manifest = Read-LanManifest $Folder
    $transport = Read-JsonFile (Join-Path $Folder 'transport.json')
    if ($transport.schema -ne 1 -or $transport.max_file_bytes -notin @(45000000,49000000) -or
        $transport.part_size_bytes -notmatch '^\d+$' -or [long]$transport.part_size_bytes -lt 1 -or
        [long]$transport.part_size_bytes -gt [long]$transport.max_file_bytes -or $transport.source_fingerprint -notmatch '^[a-f0-9]{64}$' -or
        $transport.manifest_sha256 -ne (Get-Sha256 (Join-Path $Folder 'manifest.json'))) {
        throw 'Invalid transport.json schema, size or manifest hash'
    }
    $entries = @(Get-Entries $manifest)
    if (@($transport.files).Count -ne $entries.Count) { throw 'Transport package count mismatch' }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($support in @($transport.support_files)) {
        $name = Assert-ZipPath ([string]$support.name)
        if (($name -ne 'deploy_to_server.bat' -and -not $name.StartsWith('tools/')) -or -not $seen.Add($name)) {
            throw "Invalid support file: $name"
        }
        $path = Join-Path $Folder $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing support file: $name" }
        if ((Get-Item -LiteralPath $path).Length -ne $support.size -or (Get-Sha256 $path) -ne $support.sha256) {
            throw "Support file integrity check failed: $name"
        }
    }
    foreach ($required in @('deploy_to_server.bat','tools/deploy-to-server.ps1','tools/transfer.ps1','tools/import.ps1','tools/common.ps1','tools/server-deployment.ps1')) {
        if (-not $seen.Contains($required)) { throw "Missing support file reference: $required" }
    }
    for ($fileIndex = 0; $fileIndex -lt $entries.Count; $fileIndex++) {
        $entry = $entries[$fileIndex]
        $file = $transport.files[$fileIndex]
        if ($file.name -cne $entry.url -or $file.size -ne $entry.size -or $file.sha256 -cne $entry.sha256) {
            throw "Transport package mismatch: $($file.name)"
        }
        $expectedCount = [long][Math]::Ceiling([decimal]$entry.size / [long]$transport.part_size_bytes)
        if (@($file.parts).Count -ne $expectedCount) { throw "Missing or extra parts: $($file.name)" }
        $algorithm = [Security.Cryptography.SHA256]::Create()
        $buffer = [byte[]]::new(1048576)
        try {
            for ($partIndex = 0; $partIndex -lt $expectedCount; $partIndex++) {
                $part = $file.parts[$partIndex]
                $name = $entry.url
                if ($expectedCount -gt 1) { $name = '{0}.part{1:D5}' -f $entry.url, ($partIndex + 1) }
                $expectedSize = [Math]::Min([long]$transport.part_size_bytes, [long]$entry.size - $partIndex * [long]$transport.part_size_bytes)
                if ($part.name -cne $name -or $part.index -ne ($partIndex + 1) -or
                    $part.size -ne $expectedSize -or $part.sha256 -notmatch '^[a-f0-9]{64}$' -or -not $seen.Add($part.name)) {
                    throw "Invalid part order, path, size or duplicate reference: $($part.name)"
                }
                $path = Join-Path $Folder $name
                if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing part: $name" }
                if ((Get-Item -LiteralPath $path).Length -ne $expectedSize -or (Get-Sha256 $path) -ne $part.sha256) {
                    throw "Part integrity check failed: $name"
                }
                $stream = [IO.File]::OpenRead($path)
                try {
                    while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                        [void]$algorithm.TransformBlock($buffer, 0, $count, $buffer, 0)
                    }
                } finally { $stream.Dispose() }
            }
            [void]$algorithm.TransformFinalBlock([byte[]]::new(0), 0, 0)
            $hash = ([BitConverter]::ToString($algorithm.Hash)).Replace('-', '').ToLowerInvariant()
            if ($hash -ne $entry.sha256) { throw "Complete package hash mismatch: $($entry.url)" }
        } finally { $algorithm.Dispose() }
    }
    return $manifest
}

function Restore-LanTransfer([string]$Folder, [string]$Destination) {
    $manifest = Test-LanTransfer $Folder
    $transport = Read-JsonFile (Join-Path $Folder 'transport.json')
    foreach ($file in $transport.files) {
        Write-Host "[Merge] $($file.name)"
        $outputStream = [IO.File]::Open((Join-Path $Destination $file.name), [IO.FileMode]::CreateNew)
        try {
            foreach ($part in $file.parts) {
                $inputStream = [IO.File]::OpenRead((Join-Path $Folder $part.name))
                try { $inputStream.CopyTo($outputStream, 1048576) } finally { $inputStream.Dispose() }
            }
        } finally { $outputStream.Dispose() }
    }
    Write-JsonFile (Join-Path $Destination 'manifest.json') $manifest
    [void](Test-LanBundle $Destination)
}
