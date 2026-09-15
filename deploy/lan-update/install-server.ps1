param(
    [string]$ServerIP = '', [string]$AllowedSubnet = '',
    [string]$ServerRoot = 'C:\PythonVNAUpdate',
    [ValidateRange(1024,65535)][int]$Port = 8095,
    [string]$FeatureSource = ''
)
. "$PSScriptRoot/common.ps1"
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run install_server.bat as administrator'
}
if (-not $ServerIP) { $ServerIP = Read-Host 'Fixed server IPv4 address' }
if (-not $AllowedSubnet) { $AllowedSubnet = Read-Host 'Allowed LAN IPv4 CIDR (example: 192.168.1.0/24)' }
$address = [Net.IPAddress]::Parse($ServerIP)
if ($address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
    -not (Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -eq $ServerIP)) { throw 'Server IP must belong to this server' }
$cidr = $AllowedSubnet.Split('/')
if ($cidr.Count -ne 2 -or $cidr[1] -notmatch '^\d+$' -or [int]$cidr[1] -lt 1 -or [int]$cidr[1] -gt 32) {
    throw 'AllowedSubnet must be IPv4 CIDR with prefix 1..32'
}
$network = [Net.IPAddress]::Parse($cidr[0])
if ($network.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'Only IPv4 is supported' }
$networkBytes = $network.GetAddressBytes()
$maskBytes = New-Object byte[] 4
for ($index = 0; $index -lt 4; $index++) {
    $bits = [Math]::Max(0, [Math]::Min(8, [int]$cidr[1] - 8 * $index))
    $maskBytes[$index] = [byte](256 - [Math]::Pow(2, 8 - $bits))
    $networkBytes[$index] = $networkBytes[$index] -band $maskBytes[$index]
}
$networkAddress = $networkBytes -join '.'
$subnetMask = $maskBytes -join '.'
$ServerRoot = Initialize-LanRoot $ServerRoot
$settingsPath = Join-Path $ServerRoot 'server.json'
$siteName = 'PythonVNA-LAN-Update'
$binding = "${ServerIP}:${Port}:"
$public = Join-Path $ServerRoot 'public'
if (-not (Test-Path -LiteralPath $settingsPath) -and (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
    throw "Port $Port is occupied; choose another port"
}
if (Test-Path -LiteralPath $settingsPath) {
    $saved = Read-JsonFile $settingsPath
    if ($saved.ip -ne $ServerIP -or $saved.port -ne $Port -or $saved.subnet -ne $AllowedSubnet) {
        throw 'Existing deployment settings differ; do not overwrite an active server'
    }
}
$features = @('IIS-WebServerRole','IIS-WebServer','IIS-CommonHttpFeatures','IIS-StaticContent',
    'IIS-RequestFiltering','IIS-IPSecurity','IIS-ManagementScriptingTools')
$featureArgs = @{Online=$true; FeatureName=$features; All=$true; LimitAccess=$true; NoRestart=$true}
if ($FeatureSource) { $featureArgs.Source = $FeatureSource }
try { $result = Enable-WindowsOptionalFeature @featureArgs }
catch { throw "Offline IIS installation failed. Supply matching Server 2019 media using -FeatureSource (for example WIM:E:\sources\install.wim:2). No online fallback was used. $_" }
if (@($result | Where-Object RestartNeeded).Count) { throw 'Windows requires a restart; restart and run this script again' }
Add-Type -Path "$env:windir\System32\inetsrv\Microsoft.Web.Administration.dll"
$manager = [Microsoft.Web.Administration.ServerManager]::new()
try {
    $site = $manager.Sites[$siteName]
    if ($site -and (-not (Test-Path -LiteralPath $settingsPath) -or
        $site.Applications['/'].VirtualDirectories['/'].PhysicalPath -ne $public)) { throw 'IIS site name belongs to another deployment' }
    foreach ($other in $manager.Sites) {
        if ($other.Name -eq $siteName) { continue }
        foreach ($otherBinding in $other.Bindings) {
            if ($otherBinding.EndPoint -and $otherBinding.EndPoint.Port -eq $Port) { throw "IIS port conflict with $($other.Name)" }
        }
    }
    if (-not $site) {
        if ($manager.ApplicationPools[$siteName]) { throw 'Application pool name is already in use' }
        $pool = $manager.ApplicationPools.Add($siteName)
        $pool.ManagedRuntimeVersion = ''
        $pool.ProcessModel.IdentityType = [Microsoft.Web.Administration.ProcessModelIdentityType]::ApplicationPoolIdentity
        $site = $manager.Sites.Add($siteName, 'http', $binding, $public)
        $site.Applications['/'].ApplicationPoolName = $siteName
        $site.ServerAutoStart = $false
    } elseif ($site.Bindings.Count -ne 1 -or $site.Bindings[0].BindingInformation -ne $binding) {
        throw 'Existing site binding changed outside this tool'
    }
    $config = $manager.GetApplicationHostConfiguration()
    $config.GetSection('system.webServer/directoryBrowse',$siteName)['enabled'] = $false
    $config.GetSection('system.webServer/defaultDocument',$siteName)['enabled'] = $false
    $anonymous = $config.GetSection('system.webServer/security/authentication/anonymousAuthentication',$siteName)
    $anonymous['enabled'] = $true
    $anonymous['userName'] = ''
    $handlers = $config.GetSection('system.webServer/handlers',$siteName)
    $handlers['accessPolicy'] = 'Read'
    $collection = $handlers.GetCollection()
    $collection.Clear()
    $handler = $collection.CreateElement('add')
    $handler['name'] = 'LanStaticFiles'
    $handler['path'] = '*'
    $handler['verb'] = 'GET,HEAD'
    $handler['modules'] = 'StaticFileModule'
    $handler['resourceType'] = 'File'
    $handler['requireAccess'] = 'Read'
    $collection.Add($handler)
    $static = $config.GetSection('system.webServer/staticContent',$siteName)
    $mime = $static.GetCollection()
    $mime.Clear()
    foreach ($pair in @(@('.json','application/json'),@('.zip','application/zip'))) {
        $mapping = $mime.CreateElement('mimeMap')
        $mapping['fileExtension'] = $pair[0]
        $mapping['mimeType'] = $pair[1]
        $mime.Add($mapping)
    }
    $static.GetChildElement('clientCache')['cacheControlMode'] = 'DisableCache'
    $security = $config.GetSection('system.webServer/security/ipSecurity',$siteName)
    $security['allowUnlisted'] = $false
    $rules = $security.GetCollection()
    $rules.Clear()
    foreach ($rule in @(@($networkAddress,$subnetMask),@($ServerIP,'255.255.255.255'))) {
        if ($rule[0] -eq $networkAddress -and $rule[1] -eq $subnetMask -and $rules.Count) { continue }
        $allow = $rules.CreateElement('add')
        $allow['ipAddress'] = $rule[0]
        $allow['subnetMask'] = $rule[1]
        $allow['allowed'] = $true
        $rules.Add($allow)
    }
    $filter = $config.GetSection('system.webServer/security/requestFiltering',$siteName)
    $verbs = $filter.GetChildElement('verbs')
    $verbs['allowUnlisted'] = $false
    $verbCollection = $verbs.GetCollection()
    $verbCollection.Clear()
    foreach ($verb in @('GET','HEAD')) {
        $entry = $verbCollection.CreateElement('add'); $entry['verb']=$verb; $entry['allowed']=$true; $verbCollection.Add($entry)
    }
    $extensions = $filter.GetChildElement('fileExtensions')
    $extensions['allowUnlisted'] = $false
    $extensionCollection = $extensions.GetCollection()
    $extensionCollection.Clear()
    foreach ($extension in @('.zip','.json')) {
        $entry = $extensionCollection.CreateElement('add'); $entry['fileExtension']=$extension; $entry['allowed']=$true; $extensionCollection.Add($entry)
    }
    $site.ServerAutoStart = $true
    $manager.CommitChanges()
    Write-JsonFile $settingsPath ([ordered]@{ip=$ServerIP; port=$Port; subnet=$AllowedSubnet})
    & icacls.exe $public /grant "IIS AppPool\${siteName}:(OI)(CI)(RX)" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Failed to grant read-only IIS access' }
    $firewallName = 'PythonVNA-LAN-Update'
    $existingRule = Get-NetFirewallRule -Name $firewallName -ErrorAction SilentlyContinue
    if ($existingRule) {
        Set-NetFirewallRule -Name $firewallName -Enabled True -Direction Inbound -Action Allow -Profile Any -Protocol TCP -LocalAddress $ServerIP -LocalPort $Port -RemoteAddress $AllowedSubnet | Out-Null
    } else {
        New-NetFirewallRule -Name $firewallName -DisplayName $firewallName -Enabled True -Direction Inbound -Action Allow -Profile Any -Protocol TCP -LocalAddress $ServerIP -LocalPort $Port -RemoteAddress $AllowedSubnet | Out-Null
    }
    Set-Service W3SVC -StartupType Automatic
    Start-Service W3SVC
    if ($site.State -ne [Microsoft.Web.Administration.ObjectState]::Started) { [void]$site.Start() }
} finally { $manager.Dispose() }
Write-Host "Server ready. Import a bundle, then configure clients: http://${ServerIP}:${Port}/pythonvna/manifest.json"
