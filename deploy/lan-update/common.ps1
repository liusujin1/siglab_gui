$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Read-JsonFile([string]$Path) {
    return ([IO.File]::ReadAllText($Path) | ConvertFrom-Json)
}

function Get-Sha256([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}

function Write-JsonFile([string]$Path, $Value) {
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 12) + "`n", [Text.UTF8Encoding]::new($false))
}

function Assert-NoLink([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Linked or cloud-managed path is not supported here: $cursor"
            }
        }
        $cursor = Split-Path -Parent $cursor
    }
}

function Assert-Version([string]$Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') { throw "Invalid version: $Version" }
}

function Assert-ZipPath([string]$Name) {
    $normalized = $Name.Replace('\', '/')
    if (-not $normalized -or $normalized.StartsWith('/') -or $normalized.Contains(':')) {
        throw "Unsafe ZIP path: $Name"
    }
    foreach ($part in $normalized.TrimEnd('/').Split('/')) {
        if (-not $part -or $part -in @('.', '..') -or $part -match '[<>"|?*\x00-\x1f]' -or
            $part -match '[. ]$' -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            throw "Unsafe ZIP path: $Name"
        }
    }
    if ($normalized.Split('/') -contains '.python_vna_update.lock') { throw 'ZIP contains updater lock' }
    return $normalized
}

function Test-ProtectedConfig([string]$Name) {
    return ($Name.Replace('\','/').Split('/')[-1] -ieq 'update_config.json')
}

function Test-LanZip([string]$Path) {
    $archive = [IO.Compression.ZipFile]::OpenRead($Path)
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    try {
        foreach ($entry in $archive.Entries) {
            $name = Assert-ZipPath $entry.FullName
            if (-not $names.Add($name.TrimEnd('/'))) { throw "Duplicate ZIP path: $name" }
            if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw "ZIP symlink: $name" }
            if (Test-ProtectedConfig $name) { throw 'LAN ZIP must not replace update_config.json' }
            if ($name.EndsWith('/')) { continue }
            $stream = $entry.Open()
            try {
                if ($name.Split('/')[-1] -eq 'UPDATE_REMOVED_FILES.txt') {
                    $reader = [IO.StreamReader]::new($stream)
                    try {
                        foreach ($line in ($reader.ReadToEnd() -split '\r?\n')) {
                            if (-not $line.Trim()) { continue }
                            $relative = Assert-ZipPath $line.Trim()
                            if (Test-ProtectedConfig $relative) { throw 'LAN ZIP must not delete update_config.json' }
                        }
                    } finally { $reader.Dispose() }
                } else { $stream.CopyTo([IO.Stream]::Null) }
            } finally { $stream.Dispose() }
        }
    } finally { $archive.Dispose() }
}

function Get-Entries($Manifest) {
    return @($Manifest.full) + @($Manifest.updates)
}

function Test-LanBundle([string]$Folder) {
    $manifest = Read-JsonFile (Join-Path $Folder 'manifest.json')
    Assert-Version $manifest.latest
    if ($manifest.product -ne 'PythonVNA Suite' -or $manifest.channel -ne 'stable' -or -not $manifest.full) {
        throw 'Invalid LAN product/channel/full package'
    }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in (Get-Entries $manifest)) {
        if ($null -eq $entry) { throw 'Missing package entry' }
        if ($entry.url -notmatch '^LAN_PythonVNA_(Suite|Update)_v[0-9._a-z]+_[a-f0-9]{64}\.zip$' -or
            $entry.archive_type -ne 'zip' -or $entry.sha256 -notmatch '^[a-f0-9]{64}$') {
            throw 'Invalid LAN package name, hash or archive type'
        }
        if (-not $seen.Add($entry.url)) { throw 'Duplicate package reference' }
        if (-not $entry.url.EndsWith("_$($entry.sha256).zip")) { throw 'Package name/hash mismatch' }
        $path = Join-Path $Folder $entry.url
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing package: $path" }
        if ((Get-Item -LiteralPath $path).Length -ne [long]$entry.size -or
            (Get-Sha256 $path) -ne $entry.sha256) {
            throw "Package integrity check failed: $path"
        }
        Test-LanZip $path
    }
    if ($manifest.full.url -notlike "LAN_PythonVNA_Suite_v$($manifest.latest)_*") { throw 'Full package version mismatch' }
    $bases = [Collections.Generic.HashSet[string]]::new()
    foreach ($entry in @($manifest.updates)) {
        Assert-Version $entry.from
        if ($entry.to -ne $manifest.latest -or [version]$entry.from -ge [version]$manifest.latest -or
            -not $bases.Add($entry.from) -or
            $entry.url -notlike "LAN_PythonVNA_Update_v$($entry.from)_to_v$($entry.to)_*") {
            throw 'Invalid incremental version route'
        }
    }
    return $manifest
}

function Initialize-LanRoot([string]$Root) {
    $resolved = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if ($resolved -eq [IO.Path]::GetPathRoot($resolved).TrimEnd('\')) { throw 'A drive root is not allowed' }
    Assert-NoLink $resolved
    $marker = Join-Path $resolved '.pythonvna-lan-root'
    if (-not (Test-Path -LiteralPath $marker)) {
        if ((Test-Path -LiteralPath $resolved) -and @(Get-ChildItem -LiteralPath $resolved -Force).Count) {
            throw 'Server root must be empty or initialized by this tool'
        }
        [void][IO.Directory]::CreateDirectory($resolved)
        [IO.File]::WriteAllText($marker, 'PythonVNA LAN v1')
    }
    if ([IO.File]::ReadAllText($marker) -ne 'PythonVNA LAN v1') { throw 'Invalid server root marker' }
    foreach ($part in @('public/pythonvna','history','staging')) {
        Assert-NoLink (Join-Path $resolved $part)
        [void][IO.Directory]::CreateDirectory((Join-Path $resolved $part))
    }
    return $resolved
}

function Set-AtomicFile([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) {
        [IO.File]::Replace($Source, $Destination, [Management.Automation.Language.NullString]::Value)
    } else { [IO.File]::Move($Source, $Destination) }
}
