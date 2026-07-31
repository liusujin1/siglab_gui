param(
    [Parameter(Mandatory=$true)]
    [string]$Version,

    [string]$BasePath = '',

    [string]$BaseUrl = 'https://vna.liusujin.de:8443/pythonvna',

    [ValidateSet('Https', 'Ssh')]
    [string]$PublishTransport = 'Https',

    [string]$PublishApiUrl = 'https://vna.liusujin.de:8443/pythonvna-admin',

    [string]$PublishTokenPath = '',

    [string]$NasHost = 'liusujin.de',

    [int]$NasPort = 22220,

    [string]$NasUser = 'liusu',

    [string]$NasRemotePath = '/volume1/docker/pythonvna-update/www/pythonvna',

    [string]$CredentialPath = '',

    [switch]$FullOnly,

    [switch]$UseExistingArtifacts,

    [switch]$SkipFullUpload,

    [int]$MaxIncrementalBases = 5,

    [switch]$PruneLocalArtifacts,

    [int]$KeepLocalReleases = 2,

    [switch]$SkipRemotePrune,

    [int]$KeepRemoteFullReleases = 2
)

$ErrorActionPreference = 'Stop'

if ($KeepRemoteFullReleases -lt 1) {
    throw 'KeepRemoteFullReleases must be at least 1.'
}

$root = Split-Path -Parent $PSScriptRoot
$distRoot = Join-Path $root 'dist'
$latestPathFile = Join-Path $distRoot 'LATEST_SUITE_PATH.txt'
$buildUpdateScript = Join-Path $PSScriptRoot 'build_vna_suite_update.ps1'
$buildReleaseScript = Join-Path $PSScriptRoot 'sync_worktrees_and_build_suite.ps1'
$manifestScript = Join-Path $PSScriptRoot 'generate_update_manifest.ps1'
$python = Join-Path $root '.venv\Scripts\python.exe'
$defaultCredentialPath = Join-Path $env:APPDATA 'PythonVNA\nas_credential.xml'
$defaultPublishTokenPath = Join-Path $env:APPDATA 'PythonVNA\publisher_token.xml'

function Resolve-RepoPath {
    param([Parameter(Mandatory=$true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $root $Path))
}

function Read-LatestReleasePath {
    if (-not (Test-Path -LiteralPath $latestPathFile)) {
        return ''
    }
    return [System.IO.File]::ReadAllText($latestPathFile).Trim()
}

function Get-ReleaseVersion {
    param([Parameter(Mandatory=$true)][string]$ReleasePath)

    $leaf = Split-Path -Leaf $ReleasePath
    $match = [regex]::Match($leaf, '^PythonVNA_Suite_v(.+)$')
    if ($match.Success) {
        return $match.Groups[1].Value
    }

    $versionPath = Join-Path $ReleasePath 'VERSION.txt'
    if (Test-Path -LiteralPath $versionPath) {
        $text = [System.IO.File]::ReadAllText($versionPath)
        $match = [regex]::Match($text, '(?m)^Version:\s*(.+?)\s*$')
        if ($match.Success) {
            return $match.Groups[1].Value.Trim()
        }
    }

    throw "Could not determine release version from $ReleasePath"
}

function Compare-VersionStrings {
    param(
        [Parameter(Mandatory=$true)][string]$Left,
        [Parameter(Mandatory=$true)][string]$Right
    )

    try {
        return ([version]$Left).CompareTo([version]$Right)
    }
    catch {
        return [string]::Compare($Left, $Right, [System.StringComparison]::OrdinalIgnoreCase)
    }
}

function Get-ReleaseVersionFromName {
    param([Parameter(Mandatory=$true)][string]$ReleasePath)

    $leaf = Split-Path -Leaf $ReleasePath
    $match = [regex]::Match($leaf, '^PythonVNA_Suite_v(.+)$')
    if ($match.Success) {
        return $match.Groups[1].Value
    }
    return ''
}

function Find-PreviousReleasePath {
    param([Parameter(Mandatory=$true)][string]$TargetVersion)

    $bestPath = ''
    $bestVersion = ''
    Get-ChildItem -LiteralPath $distRoot -Directory -Filter 'PythonVNA_Suite_v*' -ErrorAction SilentlyContinue | ForEach-Object {
        $candidateVersion = Get-ReleaseVersionFromName -ReleasePath $_.FullName
        if ([string]::IsNullOrWhiteSpace($candidateVersion)) {
            return
        }
        if ((Compare-VersionStrings -Left $candidateVersion -Right $TargetVersion) -ge 0) {
            return
        }
        if ([string]::IsNullOrWhiteSpace($bestVersion) -or (Compare-VersionStrings -Left $candidateVersion -Right $bestVersion) -gt 0) {
            $bestVersion = $candidateVersion
            $bestPath = $_.FullName
        }
    }
    return $bestPath
}

function Find-PreviousReleasePaths {
    param(
        [Parameter(Mandatory=$true)][string]$TargetVersion,
        [int]$MaximumCount = 5
    )

    $items = New-Object System.Collections.Generic.List[object]
    Get-ChildItem -LiteralPath $distRoot -Directory -Filter 'PythonVNA_Suite_v*' -ErrorAction SilentlyContinue | ForEach-Object {
        $candidateVersion = Get-ReleaseVersionFromName -ReleasePath $_.FullName
        if ([string]::IsNullOrWhiteSpace($candidateVersion)) {
            return
        }
        if ((Compare-VersionStrings -Left $candidateVersion -Right $TargetVersion) -ge 0) {
            return
        }
        $items.Add([pscustomobject]@{
            Version = $candidateVersion
            Path = $_.FullName
        })
    }
    return @(
        $items |
            Sort-Object @{Expression = { [version]$_.Version }; Descending = $true} |
            Select-Object -First ([Math]::Max(1, $MaximumCount)) |
            ForEach-Object { $_.Path }
    )
}

function Get-FullReleaseArchivePath {
    param([Parameter(Mandatory=$true)][string]$ReleaseVersion)

    $sevenZipArchive = Join-Path $distRoot "PythonVNA_Suite_v$ReleaseVersion.7z"
    if (Test-Path -LiteralPath $sevenZipArchive -PathType Leaf) {
        return $sevenZipArchive
    }
    $zipArchive = Join-Path $distRoot "PythonVNA_Suite_v$ReleaseVersion.zip"
    if (Test-Path -LiteralPath $zipArchive -PathType Leaf) {
        return $zipArchive
    }
    throw "Full release archive was not found for v${ReleaseVersion}: $zipArchive or $sevenZipArchive"
}

function Get-ReleaseItemVersion {
    param([Parameter(Mandatory=$true)][string]$Name)

    $match = [regex]::Match($Name, '^PythonVNA_Suite_v(.+)\.(zip|7z)$')
    if ($match.Success) {
        return $match.Groups[1].Value
    }

    $match = [regex]::Match($Name, '^PythonVNA_Suite_v([^.]+(?:\.[^.]+)*)$')
    if ($match.Success) {
        return $match.Groups[1].Value
    }

    return ''
}

function Remove-LocalReleaseArtifacts {
    param(
        [Parameter(Mandatory=$true)][string]$CurrentVersion,
        [int]$KeepCount = 2
    )

    $keepCount = [Math]::Max(1, $KeepCount)
    $releaseItems = New-Object System.Collections.Generic.List[object]

    Get-ChildItem -LiteralPath $distRoot -Force -ErrorAction SilentlyContinue | ForEach-Object {
        $version = Get-ReleaseItemVersion -Name $_.Name
        if ([string]::IsNullOrWhiteSpace($version)) {
            return
        }
        $releaseItems.Add([pscustomobject]@{
            Name = $_.Name
            Version = $version
            FullName = $_.FullName
            IsDirectory = $_.PSIsContainer
        })
    }

    $versionsToKeep = @(
        $releaseItems |
            Select-Object -ExpandProperty Version -Unique |
            Sort-Object { [version]$_ } -Descending |
            Select-Object -First $keepCount
    )

    $removed = New-Object System.Collections.Generic.List[string]
    foreach ($item in $releaseItems) {
        if ($versionsToKeep -contains $item.Version) {
            continue
        }
        if ($item.IsDirectory) {
            Remove-Item -LiteralPath $item.FullName -Recurse -Force
        }
        else {
            Remove-Item -LiteralPath $item.FullName -Force
        }
        $removed.Add($item.Name)
    }

    $updateArchivePattern = '^PythonVNA_Update_v(.+)_to_v(.+)\.(zip|7z)$'
    Get-ChildItem -LiteralPath (Join-Path $distRoot 'updates') -File -ErrorAction SilentlyContinue | ForEach-Object {
        $match = [regex]::Match($_.Name, $updateArchivePattern)
        if (-not $match.Success) {
            return
        }
        $fromVersion = $match.Groups[1].Value
        $toVersion = $match.Groups[2].Value
        if (($versionsToKeep -contains $fromVersion) -and ($versionsToKeep -contains $toVersion)) {
            return
        }
        Remove-Item -LiteralPath $_.FullName -Force
        $removed.Add("updates\$($_.Name)")
    }

    return @($removed.ToArray())
}

function Normalize-Version {
    param([Parameter(Mandatory=$true)][string]$Value)

    $versionText = $Value.Trim()
    if ($versionText.StartsWith('v', [System.StringComparison]::OrdinalIgnoreCase)) {
        $versionText = $versionText.Substring(1)
    }
    if ($versionText -notmatch '^\d+(\.\d+){1,3}([A-Za-z0-9._-]+)?$') {
        throw "Invalid version '$Value'. Use a value such as 3.1.5 or v3.1.5."
    }
    return $versionText
}

function Test-RemoteHttpFile {
    param([Parameter(Mandatory=$true)][string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Head -Uri $Url -TimeoutSec 20
        return ([int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 400)
    }
    catch {
        return $false
    }
}

function Resolve-CredentialPath {
    if (-not [string]::IsNullOrWhiteSpace($CredentialPath)) {
        return Resolve-RepoPath -Path $CredentialPath
    }
    return $defaultCredentialPath
}

function Resolve-PublishTokenPath {
    if (-not [string]::IsNullOrWhiteSpace($PublishTokenPath)) {
        return Resolve-RepoPath -Path $PublishTokenPath
    }
    return $defaultPublishTokenPath
}

function Read-PublishToken {
    $token = $env:PYTHONVNA_PUBLISH_TOKEN
    if (-not [string]::IsNullOrWhiteSpace($token)) {
        return $token
    }
    $tokenPath = Resolve-PublishTokenPath
    if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
        throw "HTTPS publisher token was not found: $tokenPath"
    }
    $stored = Import-Clixml -LiteralPath $tokenPath
    if ($stored -is [System.Management.Automation.PSCredential]) {
        return $stored.GetNetworkCredential().Password
    }
    if ($stored -is [System.Security.SecureString]) {
        $credential = [pscredential]::new('pythonvna-publisher', $stored)
        return $credential.GetNetworkCredential().Password
    }
    throw "Unsupported HTTPS publisher token format: $tokenPath"
}

function Invoke-HttpsPublish {
    param(
        [Parameter(Mandatory=$true)][string[]]$Files,
        [Parameter(Mandatory=$true)][string]$ApiUrl,
        [Parameter(Mandatory=$true)][string]$Token,
        [Parameter(Mandatory=$true)][string]$ManifestUrl,
        [Parameter(Mandatory=$true)][string]$ExpectedVersion
    )

    Add-Type -AssemblyName System.Net.Http
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromMinutes(30)
    $client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Token)
    $apiRoot = $ApiUrl.TrimEnd('/')
    try {
        $health = $client.GetAsync("$apiRoot/health").GetAwaiter().GetResult()
        try {
            if (-not $health.IsSuccessStatusCode) {
                $body = $health.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                throw "HTTPS publisher health check failed ($([int]$health.StatusCode)): $body"
            }
        }
        finally {
            $health.Dispose()
        }

        foreach ($filePath in $Files) {
            $file = Get-Item -LiteralPath $filePath
            $name = $file.Name
            $escapedName = [Uri]::EscapeDataString($name)
            $targetUrl = "$apiRoot/files/$escapedName"
            $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()

            $metadata = $client.GetAsync($targetUrl).GetAwaiter().GetResult()
            try {
                if ($metadata.IsSuccessStatusCode) {
                    $remote = $metadata.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
                    if ([int64]$remote.size -eq $file.Length -and [string]$remote.sha256 -eq $hash) {
                        Write-Host "Skipping $name; HTTPS publisher file already matches ($($file.Length) bytes)"
                        continue
                    }
                }
                elseif ([int]$metadata.StatusCode -ne 404) {
                    $body = $metadata.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                    throw "HTTPS publisher metadata check failed for $name ($([int]$metadata.StatusCode)): $body"
                }
            }
            finally {
                $metadata.Dispose()
            }

            Write-Host "Uploading $name over HTTPS ($($file.Length) bytes)"
            $stream = [System.IO.File]::OpenRead($file.FullName)
            $content = [System.Net.Http.StreamContent]::new($stream)
            $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Put, $targetUrl)
            try {
                $content.Headers.ContentLength = $file.Length
                $request.Content = $content
                [void]$request.Headers.TryAddWithoutValidation('X-SHA256', $hash)
                $response = $client.SendAsync($request).GetAwaiter().GetResult()
                try {
                    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                    if (-not $response.IsSuccessStatusCode) {
                        throw "HTTPS upload failed for $name ($([int]$response.StatusCode)): $body"
                    }
                    $result = $body | ConvertFrom-Json
                    if ([string]$result.sha256 -ne $hash -or [int64]$result.size -ne $file.Length) {
                        throw "HTTPS publisher verification mismatch for $name"
                    }
                    Write-Host "Uploaded and verified $name"
                    if ($result.removed -and $result.removed.Count -gt 0) {
                        Write-Host "Remote archive cleanup removed $($result.removed.Count) files and freed $([Math]::Round([int64]$result.freed_bytes / 1MB, 2)) MiB"
                        foreach ($removedName in $result.removed) {
                            Write-Host "  removed $removedName"
                        }
                    }
                }
                finally {
                    $response.Dispose()
                }
            }
            finally {
                $request.Dispose()
                $content.Dispose()
                $stream.Dispose()
            }
        }
    }
    finally {
        $client.Dispose()
    }

    $published = $null
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        $cacheToken = [Uri]::EscapeDataString("$ExpectedVersion-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())")
        $published = Invoke-RestMethod -Uri "$ManifestUrl`?_=$cacheToken" -Headers @{'Cache-Control'='no-cache, no-store'} -TimeoutSec 30
        if ([string]$published.latest -eq $ExpectedVersion) {
            break
        }
        if ($attempt -lt 5) {
            Start-Sleep -Seconds 2
        }
    }
    if ([string]$published.latest -ne $ExpectedVersion) {
        throw "Published manifest latest mismatch. Expected $ExpectedVersion, got $($published.latest). URL: $ManifestUrl"
    }
    Write-Host "Verified manifest latest: $ExpectedVersion"
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment was not found: $python"
}

$Version = Normalize-Version -Value $Version
$fullOnlyMode = [bool]$FullOnly
$reuseExistingRelease = $false
$fastMode = [bool]$SkipFullUpload

$previousRelease = ''
if (-not [string]::IsNullOrWhiteSpace($BasePath)) {
    $previousRelease = Resolve-RepoPath -Path $BasePath
}
elseif (-not $fullOnlyMode) {
    $previousRelease = Read-LatestReleasePath
}

if (-not $fullOnlyMode -and [string]::IsNullOrWhiteSpace($BasePath)) {
    $latestCandidateVersion = ''
    if (-not [string]::IsNullOrWhiteSpace($previousRelease) -and (Test-Path -LiteralPath $previousRelease -PathType Container)) {
        $latestCandidateVersion = Get-ReleaseVersionFromName -ReleasePath $previousRelease
        if ([string]::IsNullOrWhiteSpace($latestCandidateVersion)) {
            $latestCandidateVersion = Get-ReleaseVersion -ReleasePath $previousRelease
        }
    }
    if ([string]::IsNullOrWhiteSpace($previousRelease) -or [string]::IsNullOrWhiteSpace($latestCandidateVersion) -or (Compare-VersionStrings -Left $latestCandidateVersion -Right $Version) -ge 0) {
        $olderRelease = Find-PreviousReleasePath -TargetVersion $Version
        if (-not [string]::IsNullOrWhiteSpace($olderRelease)) {
            $previousRelease = $olderRelease
            Write-Host "Using previous release baseline: $previousRelease"
        }
    }
}

if (-not $fullOnlyMode -and -not [string]::IsNullOrWhiteSpace($previousRelease) -and -not (Test-Path -LiteralPath $previousRelease -PathType Container)) {
    Write-Warning "Base release folder was not found: $previousRelease. Falling back to full-only publish."
    $fullOnlyMode = $true
    $previousRelease = ''
}

if (-not $fullOnlyMode -and -not [string]::IsNullOrWhiteSpace($previousRelease)) {
    $previousVersion = Get-ReleaseVersion -ReleasePath $previousRelease
    if ((Compare-VersionStrings -Left $Version -Right $previousVersion) -eq 0) {
        $existingArchive = Join-Path $distRoot "$(Split-Path -Leaf $previousRelease).7z"
        if (Test-Path -LiteralPath $existingArchive -PathType Leaf) {
            Write-Warning "Latest release is already v$Version. Publishing the existing full release only. Pass -BasePath to build an incremental package from an older release."
            $fullOnlyMode = $true
            $reuseExistingRelease = $true
            $previousRelease = ''
        }
        else {
            Write-Warning "Latest release is already v$Version, but the full archive is missing. Rebuilding a full release."
            $fullOnlyMode = $true
            $previousRelease = ''
        }
    }
}

if ($UseExistingArtifacts) {
    Write-Host "Publishing existing release artifacts for v$Version..."
}
elseif ($fullOnlyMode) {
    if ($reuseExistingRelease) {
        Write-Host "Publishing existing full release v$Version..."
    }
    else {
        Write-Host "Building full release v$Version..."
        $buildReleaseArgs = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', $buildReleaseScript,
            '-Apply',
            '-Build',
            '-Version', $Version
        )
        if ($fastMode) {
            $buildReleaseArgs += @('-SkipTests', '-SkipReleaseArchives')
        }
        & powershell @buildReleaseArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Full release build failed with exit code $LASTEXITCODE"
        }
    }
}
else {
    if ([string]::IsNullOrWhiteSpace($previousRelease)) {
        throw "No previous release found. Pass -BasePath or use -FullOnly."
    }
    $previousVersion = Get-ReleaseVersion -ReleasePath $previousRelease
    if ((Compare-VersionStrings -Left $Version -Right $previousVersion) -le 0) {
        throw "Version $Version must be newer than the base version $previousVersion."
    }
    Write-Host "Building incremental release v$Version from $previousRelease..."
    $buildUpdateArgs = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $buildUpdateScript,
        '-Version', $Version,
        '-BasePath', $previousRelease
    )
    if ($fastMode) {
        $buildUpdateArgs += '-SkipSevenZip'
    }
    & powershell @buildUpdateArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Incremental build failed with exit code $LASTEXITCODE"
    }
}

$latestRelease = Read-LatestReleasePath
if ([string]::IsNullOrWhiteSpace($latestRelease) -or -not (Test-Path -LiteralPath $latestRelease -PathType Container)) {
    throw "Latest release folder was not found after build."
}

$latestVersion = Get-ReleaseVersion -ReleasePath $latestRelease
$releaseArchive = Get-FullReleaseArchivePath -ReleaseVersion $latestVersion
$releaseArchiveUrl = "$BaseUrl/$(Split-Path -Leaf $releaseArchive)"

$manifestParams = @{
    ReleasePath = $latestRelease
    BaseUrl = $BaseUrl
    OutputPath = (Join-Path $distRoot 'manifest.json')
}
$uploadFiles = New-Object System.Collections.Generic.List[string]
$uploadFullArchive = -not [bool]$SkipFullUpload
if ($SkipFullUpload) {
    if (Test-RemoteHttpFile -Url $releaseArchiveUrl) {
        Write-Warning "Skipping full archive upload because the remote full archive already exists: $releaseArchiveUrl"
    }
    else {
        Write-Warning "Remote full archive is missing, so fast mode will upload it to keep cross-version updates working: $releaseArchiveUrl"
        $uploadFullArchive = $true
    }
}

if ($uploadFullArchive) {
    $uploadFiles.Add($releaseArchive)
}

if (-not $fullOnlyMode) {
    if ([string]::IsNullOrWhiteSpace($previousRelease)) {
        throw "No previous release found. Pass -BasePath or use -FullOnly."
    }

    $baseReleasePaths = New-Object System.Collections.Generic.List[string]
    if ($UseExistingArtifacts -and -not [string]::IsNullOrWhiteSpace($previousRelease)) {
        $baseReleasePaths.Add($previousRelease)
    }
    elseif ($fastMode -and -not [string]::IsNullOrWhiteSpace($previousRelease)) {
        $baseReleasePaths.Add($previousRelease)
    }
    elseif (-not [string]::IsNullOrWhiteSpace($BasePath)) {
        $baseReleasePaths.Add($previousRelease)
    }
    else {
        foreach ($path in (Find-PreviousReleasePaths -TargetVersion $latestVersion -MaximumCount $MaxIncrementalBases)) {
            $baseReleasePaths.Add($path)
        }
    }
    if ($baseReleasePaths.Count -eq 0) {
        $baseReleasePaths.Add($previousRelease)
    }

    $seenBaseVersions = @{}
    $manifestBasePaths = New-Object System.Collections.Generic.List[string]
    $manifestUpdateArchives = New-Object System.Collections.Generic.List[string]
    foreach ($baseRelease in $baseReleasePaths) {
        $baseVersion = Get-ReleaseVersion -ReleasePath $baseRelease
        if ($seenBaseVersions.ContainsKey($baseVersion)) {
            continue
        }
        $seenBaseVersions[$baseVersion] = $true
        $updateArchive = Join-Path $distRoot "updates\PythonVNA_Update_v${baseVersion}_to_v${latestVersion}.zip"
        if (-not $UseExistingArtifacts) {
            Write-Host "Building direct incremental update v$baseVersion -> v$latestVersion..."
            $directBuildArgs = @(
                '-NoProfile',
                '-ExecutionPolicy', 'Bypass',
                '-File', $buildUpdateScript,
                '-SkipBuild',
                '-BasePath', $baseRelease,
                '-NewPath', $latestRelease
            )
            if ($fastMode) {
                $directBuildArgs += '-SkipSevenZip'
            }
            & powershell @directBuildArgs
            if ($LASTEXITCODE -ne 0) {
                throw "Direct incremental build failed with exit code $LASTEXITCODE"
            }
        }
        if (-not (Test-Path -LiteralPath $updateArchive -PathType Leaf)) {
            throw "Incremental update archive was not found: $updateArchive"
        }
        $manifestBasePaths.Add($baseRelease)
        $manifestUpdateArchives.Add($updateArchive)
        $uploadFiles.Add($updateArchive)
    }
    $manifestParams['BasePath'] = $manifestBasePaths.ToArray()
    $manifestParams['UpdateArchivePath'] = $manifestUpdateArchives.ToArray()
}

Write-Host "Generating update manifest..."
& $manifestScript @manifestParams

$configPath = Join-Path $root 'update_config.json'
if (Test-Path -LiteralPath $configPath) {
    $uploadFiles.Add($configPath)
}
$uploadFiles.Add((Join-Path $distRoot 'manifest.json'))

if ($PublishTransport -eq 'Https') {
    $publishToken = Read-PublishToken
    Invoke-HttpsPublish `
        -Files @($uploadFiles.ToArray()) `
        -ApiUrl $PublishApiUrl `
        -Token $publishToken `
        -ManifestUrl "$BaseUrl/manifest.json" `
        -ExpectedVersion $latestVersion

    Write-Host "Published PythonVNA Suite v$latestVersion over HTTPS"
    Write-Host "Manifest: $BaseUrl/manifest.json"
    if ($PruneLocalArtifacts) {
        $removedItems = Remove-LocalReleaseArtifacts -CurrentVersion $latestVersion -KeepCount $KeepLocalReleases
        if ($removedItems.Count -gt 0) {
            Write-Host "Pruned local artifacts (kept latest $KeepLocalReleases releases):"
            foreach ($item in $removedItems) {
                Write-Host "  removed $item"
            }
        }
        else {
            Write-Host "No local artifacts needed pruning."
        }
    }
    exit 0
}

$password = $env:PYTHONVNA_NAS_PASSWORD
if ([string]::IsNullOrWhiteSpace($password)) {
    $storedCredentialPath = Resolve-CredentialPath
    if (Test-Path -LiteralPath $storedCredentialPath -PathType Leaf) {
        $storedCredential = Import-Clixml -LiteralPath $storedCredentialPath
        if ($storedCredential.UserName -ne $NasUser) {
            Write-Warning "Stored NAS credential user '$($storedCredential.UserName)' does not match '$NasUser'. Prompting for password."
        }
        else {
            $password = $storedCredential.GetNetworkCredential().Password
        }
    }
}
if ([string]::IsNullOrWhiteSpace($password)) {
    $secure = Read-Host "NAS SSH password for $NasUser@$NasHost" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

$paramikoPath = Join-Path $env:TEMP 'codex_paramiko'
if (-not (Test-Path -LiteralPath $paramikoPath)) {
    New-Item -ItemType Directory -Path $paramikoPath | Out-Null
}
$env:PYTHONPATH = $paramikoPath
try {
    & $python -c "import paramiko"
}
catch {
    Write-Host "Installing paramiko into $paramikoPath ..."
    & $python -m pip install --target $paramikoPath paramiko
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install paramiko into $paramikoPath"
    }
}

$verifyUrls = New-Object System.Collections.Generic.List[string]
$verifyUrls.Add("$BaseUrl/manifest.json")
if ($uploadFullArchive -or $SkipFullUpload) {
    $verifyUrls.Add($releaseArchiveUrl)
}
if (-not $fullOnlyMode) {
    foreach ($archivePath in @($manifestUpdateArchives.ToArray())) {
        $verifyUrls.Add("$BaseUrl/$(Split-Path -Leaf $archivePath)")
    }
}

$payload = @{
    host = $NasHost
    port = $NasPort
    username = $NasUser
    password = $password
    remote_path = $NasRemotePath
    files = @($uploadFiles.ToArray())
    verify_urls = @($verifyUrls.ToArray())
    manifest_url = "$BaseUrl/manifest.json"
    expected_latest = $latestVersion
    manifest_local_path = (Join-Path $distRoot 'manifest.json')
    prune_remote = (-not $SkipRemotePrune)
    keep_remote_full_releases = $KeepRemoteFullReleases
} | ConvertTo-Json -Depth 8

$publishCode = @'
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

import paramiko


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def run(
    client,
    command: str,
    password: str,
    *,
    sudo: bool = False,
    timeout: int = 180,
    input_path: Path | None = None,
) -> str:
    full_command = command
    if sudo:
        full_command = "sudo -S -p '' sh -lc " + shell_quote(command)
    stdin, stdout, stderr = client.exec_command(full_command, timeout=timeout)
    if sudo:
        stdin.write(password + "\n")
        stdin.flush()
    if input_path is not None:
        with input_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                stdin.channel.sendall(chunk)
        stdin.channel.shutdown_write()
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if code != 0:
        raise RuntimeError(f"command failed {code}: {command}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    return out


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remote_hash_command(remote_path: str, expected_size: int) -> str:
    quoted = shell_quote(remote_path)
    return (
        f"if [ -f {quoted} ] && [ \"$(wc -c < {quoted} | tr -d ' ')\" = {shell_quote(str(expected_size))} ]; then "
        f"if command -v sha256sum >/dev/null 2>&1; then sha256sum {quoted} | awk '{{print $1}}'; "
        f"elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 {quoted} | awk '{{print $2}}'; "
        "else echo SIZE_ONLY_MATCH; fi; "
        "fi"
    )


def remote_file_matches(client, password: str, local_path: Path, remote_path: str) -> bool:
    expected_size = local_path.stat().st_size
    try:
        remote_hash = run(
            client,
            remote_hash_command(remote_path, expected_size),
            password,
            sudo=True,
            timeout=120,
        ).strip().splitlines()[-1:]
    except Exception:
        return False
    if not remote_hash:
        return False
    value = remote_hash[0].strip().lower()
    if value == "size_only_match":
        return True
    return value == file_sha256(local_path)


def upload(
    client,
    password: str,
    local_path: Path,
    remote_path: str,
    *,
    use_sftp: bool,
) -> bool:
    size = local_path.stat().st_size
    if remote_file_matches(client, password, local_path, remote_path):
        print(f"Skipping {local_path.name}; remote file already matches ({size} bytes)")
        return use_sftp
    tmp_path = f"/tmp/pythonvna_publish_{os.getpid()}_{local_path.name}.tmp"
    timeout = max(240, size // 500000 + 120)
    print(f"Uploading {local_path.name} ({size} bytes)")
    uploaded = False
    if use_sftp:
        try:
            sftp = client.open_sftp()
            try:
                sftp.put(str(local_path), tmp_path)
                uploaded = True
            finally:
                sftp.close()
        except Exception as exc:
            use_sftp = False
            print(f"SFTP upload unavailable ({exc}); using chunked SSH stream for this and remaining files")
    if not uploaded:
        # /tmp is writable by the SSH user, so the file stream never shares stdin
        # with sudo's password prompt. This also avoids base64's 33% size overhead.
        run(
            client,
            f"cat > {shell_quote(tmp_path)}",
            password,
            timeout=max(240, size // 300000 + 120),
            input_path=local_path,
        )
    if not remote_file_matches(client, password, local_path, tmp_path):
        run(client, f"rm -f {shell_quote(tmp_path)}", password, sudo=True, timeout=30)
        raise RuntimeError(f"Uploaded file verification failed: {local_path.name}")
    try:
        run(
            client,
            f"mv {shell_quote(tmp_path)} {shell_quote(remote_path)} && chmod 644 {shell_quote(remote_path)}",
            password,
            sudo=True,
            timeout=timeout,
        )
    except Exception:
        run(client, f"rm -f {shell_quote(tmp_path)}", password, sudo=True, timeout=30)
        raise
    if not remote_file_matches(client, password, local_path, remote_path):
        raise RuntimeError(f"Published file verification failed: {local_path.name}")
    print(f"Uploaded and verified {local_path.name}")
    return use_sftp


def manifest_archive_names(manifest_path: Path) -> set[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = {"manifest.json", "update_config.json"}

    def add_url(item) -> None:
        if not isinstance(item, dict):
            return
        value = str(item.get("url", "")).strip()
        if value:
            name = Path(unquote(urlparse(value).path)).name
            if name:
                names.add(name)

    add_url(manifest.get("full"))
    updates = manifest.get("updates", [])
    if isinstance(updates, list):
        for item in updates:
            add_url(item)
    elif isinstance(updates, dict):
        add_url(updates)
    add_url(manifest.get("incremental"))
    return names


def version_key(value: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", value))
    return parts or (0,)


def prune_remote_archives(
    client,
    password: str,
    remote_path: str,
    manifest_path: Path,
    keep_full_count: int,
) -> None:
    listing = run(
        client,
        f"find {shell_quote(remote_path)} -maxdepth 1 -type f -printf '%f|%s\\n'",
        password,
        sudo=True,
        timeout=60,
    )
    files: dict[str, int] = {}
    for line in listing.splitlines():
        name, separator, size_text = line.rpartition("|")
        if not separator or not name:
            continue
        try:
            files[name] = int(size_text)
        except ValueError:
            continue

    full_pattern = re.compile(r"^PythonVNA_Suite_v(.+)\.(?:7z|zip)$", re.IGNORECASE)
    update_pattern = re.compile(r"^PythonVNA_Update_v.+_to_v.+\.(?:7z|zip)$", re.IGNORECASE)
    full_archives = []
    for name in files:
        match = full_pattern.match(name)
        if match:
            full_archives.append((version_key(match.group(1)), name))
    full_archives.sort(key=lambda item: item[0], reverse=True)

    keep = manifest_archive_names(manifest_path)
    keep.update(name for _version, name in full_archives[: max(1, keep_full_count)])
    candidates = {
        name
        for name in files
        if full_pattern.match(name) or update_pattern.match(name)
    }
    removed = sorted(candidates - keep)
    if not removed:
        print("Remote archive cleanup: no obsolete files found")
        return

    targets = " ".join(shell_quote(remote_path + "/" + name) for name in removed)
    run(client, f"rm -f -- {targets}", password, sudo=True, timeout=120)
    freed = sum(files[name] for name in removed)
    print(
        f"Remote archive cleanup removed {len(removed)} files "
        f"and freed {freed / (1024 * 1024):.2f} MiB"
    )
    for name in removed:
        print(f"  removed {name}")


payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    hostname=payload["host"],
    port=int(payload["port"]),
    username=payload["username"],
    password=payload["password"],
    timeout=15,
    banner_timeout=15,
    auth_timeout=15,
)
client.get_transport().set_keepalive(30)

remote_path = payload["remote_path"].rstrip("/")
run(client, f"mkdir -p {shell_quote(remote_path)} && chmod 755 {shell_quote(remote_path)}", payload["password"], sudo=True)

use_sftp = True
for item in payload["files"]:
    local_path = Path(item)
    if not local_path.exists():
        raise FileNotFoundError(local_path)
    use_sftp = upload(
        client,
        payload["password"],
        local_path,
        remote_path + "/" + local_path.name,
        use_sftp=use_sftp,
    )

if payload.get("prune_remote", True):
    prune_remote_archives(
        client,
        payload["password"],
        remote_path,
        Path(payload["manifest_local_path"]),
        int(payload.get("keep_remote_full_releases", 2)),
    )

client.close()

manifest_url = payload["manifest_url"]
expected_latest = payload["expected_latest"]
published_latest = ""
for attempt in range(6):
    cache_token = f"{expected_latest}-{time.time_ns()}"
    cache_bust_url = manifest_url + ("&" if "?" in manifest_url else "?") + "_=" + cache_token
    request = urllib.request.Request(
        cache_bust_url,
        headers={"User-Agent": "PythonVNA-Publisher", "Cache-Control": "no-cache, no-store"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        published_manifest = json.loads(response.read().decode("utf-8"))
    published_latest = str(published_manifest.get("latest", "")).strip()
    if published_latest == expected_latest:
        break
    if attempt < 5:
        print(
            f"Published manifest still reports {published_latest or '<empty>'}; "
            f"retrying HTTP verification ({attempt + 1}/5)"
        )
        time.sleep(2)
if published_latest != expected_latest:
    raise RuntimeError(
        f"Published manifest latest mismatch. Expected {expected_latest}, got {published_latest}. "
        f"URL: {manifest_url}"
    )
print(f"Verified manifest latest: {published_latest}")
'@

$publishScriptPath = Join-Path $env:TEMP 'pythonvna_publish_helper.py'
$payloadPath = Join-Path $env:TEMP 'pythonvna_publish_payload.json'
try {
    [System.IO.File]::WriteAllText($publishScriptPath, $publishCode, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($payloadPath, $payload, [System.Text.UTF8Encoding]::new($false))
    & $python $publishScriptPath $payloadPath
}
catch {
    if ($_.Exception.Message -match 'paramiko') {
        throw "Missing Python package paramiko. Install it with: .\.venv\Scripts\python.exe -m pip install paramiko"
    }
    throw
}
finally {
    Remove-Item -LiteralPath $publishScriptPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $payloadPath -Force -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -ne 0) {
    throw "Remote publish helper failed with exit code $LASTEXITCODE"
}

Write-Host "Published PythonVNA Suite v$latestVersion"
Write-Host "Manifest: $BaseUrl/manifest.json"

if ($PruneLocalArtifacts) {
    $removedItems = Remove-LocalReleaseArtifacts -CurrentVersion $latestVersion -KeepCount $KeepLocalReleases
    if ($removedItems.Count -gt 0) {
        Write-Host "Pruned local artifacts (kept latest $KeepLocalReleases releases):"
        foreach ($item in $removedItems) {
            Write-Host "  removed $item"
        }
    }
    else {
        Write-Host "No local artifacts needed pruning."
    }
}
