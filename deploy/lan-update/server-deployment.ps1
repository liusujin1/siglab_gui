. "$PSScriptRoot/common.ps1"

function Get-LanSites {
    $assembly = Join-Path $env:windir 'System32/inetsrv/Microsoft.Web.Administration.dll'
    if (-not (Test-Path -LiteralPath $assembly)) { throw 'Existing IIS installation not found; no changes made' }
    Add-Type -Path $assembly
    $manager = [Microsoft.Web.Administration.ServerManager]::new()
    try {
        foreach ($site in $manager.Sites) {
            $physical = [Environment]::ExpandEnvironmentVariables($site.Applications['/'].VirtualDirectories['/'].PhysicalPath)
            [pscustomobject]@{name=$site.Name; physical=[IO.Path]::GetFullPath($physical).TrimEnd('\'); bindings=@($site.Bindings | ForEach-Object { $_.BindingInformation })}
        }
    } finally { $manager.Dispose() }
}

function Test-LanDeployment([string]$Root, $Sites) {
    $resolved = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    Assert-NoLink $resolved
    $marker = Join-Path $resolved '.pythonvna-lan-root'
    $settingsPath = Join-Path $resolved 'server.json'
    Assert-NoLink $marker
    Assert-NoLink $settingsPath
    if (-not (Test-Path -LiteralPath $marker) -or [IO.File]::ReadAllText($marker) -ne 'PythonVNA LAN v1') { throw "Invalid deployment marker: $resolved" }
    $settings = Read-JsonFile $settingsPath
    $address = $null
    if (-not [Net.IPAddress]::TryParse([string]$settings.ip, [ref]$address) -or
        $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $settings.port -notmatch '^\d+$' -or [int]$settings.port -lt 1 -or [int]$settings.port -gt 65535 -or -not $settings.subnet) {
        throw "Invalid server.json: $settingsPath"
    }
    $public = Join-Path $resolved 'public'
    Assert-NoLink $public
    $matches = @($Sites | Where-Object { $_.physical -ieq $public -and $_.bindings -contains "$($settings.ip):$($settings.port):" })
    if ($matches.Count -ne 1) { throw "IIS site path/binding is not unique or does not match server.json: $resolved" }
    return $resolved
}

function Resolve-LanDeployment([string]$ConfigPath, $Sites) {
    Assert-NoLink $ConfigPath
    if (Test-Path -LiteralPath $ConfigPath) {
        return (Test-LanDeployment (Read-JsonFile $ConfigPath).server_root $Sites)
    }
    $candidates = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in (@('C:\PythonVNAUpdate') + @($Sites | ForEach-Object { Split-Path -Parent $_.physical }))) {
        try { [void]$candidates.Add((Test-LanDeployment $candidate $Sites)) } catch { }
    }
    if ($candidates.Count -eq 1) { $root = @($candidates)[0] }
    else {
        $root = Read-Host 'Existing LAN server root (contains server.json and .pythonvna-lan-root)'
        $root = Test-LanDeployment $root $Sites
    }
    $directory = Split-Path -Parent $ConfigPath
    Assert-NoLink $directory
    [void][IO.Directory]::CreateDirectory($directory)
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $identity = [Security.Principal.SecurityIdentifier]::new($sid)
        $rule = [Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $directory -AclObject $acl
    $pending = Join-Path $directory ([guid]::NewGuid().ToString('N') + '.pending')
    Write-JsonFile $pending ([ordered]@{schema=1; server_root=$root})
    Set-AtomicFile $pending $ConfigPath
    return $root
}
