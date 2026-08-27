Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-SigLabSuiteRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

function Expand-SigLabPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    return [Environment]::ExpandEnvironmentVariables($Value)
}

function Import-SigLabProfile([string]$ProfilePath) {
    $resolved = (Resolve-Path -LiteralPath $ProfilePath).Path
    $profile = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$profile.schema -ne 1) { throw 'Profile must use schema 1.' }
    if ($profile.backend -notin @('server', 'serial', 'mock')) {
        throw "Unsupported backend '$($profile.backend)'."
    }
    if ([int]$profile.baudrate -le 0) { throw 'Baudrate must be positive.' }
    $profile | Add-Member -NotePropertyName _path -NotePropertyValue $resolved -Force
    return $profile
}

function Set-SigLabEnvironment($Profile, [string]$SuiteRoot) {
    # Do not inherit developer/legacy Qt scale overrides.  Both GUIs use one
    # deterministic pixel-based policy so Samba and SIDMAT render consistently.
    foreach ($name in @(
        'QT_SCALE_FACTOR', 'QT_SCREEN_SCALE_FACTORS',
        'QT_AUTO_SCREEN_SCALE_FACTOR', 'QT_SCALE_FACTOR_ROUNDING_POLICY',
        'SIGLAB_RESPECT_QT_SCALE'
    )) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
    $env:QT_ENABLE_HIGHDPI_SCALING = '0'
    $env:SIGLAB_SUITE_ROOT = $SuiteRoot
    $env:SIGLAB_SUITE_CONFIG = $Profile._path
    $env:SIGLAB_BACKEND = [string]$Profile.backend
    $env:SIGLAB_SERVER_ENDPOINT = [string]$Profile.server
    $env:SIGLAB_SERIAL_PORT = [string]$Profile.serial_port
    $env:SIGLAB_BAUDRATE = [string]$Profile.baudrate
    $env:SIGLAB_COMM_SERVER_EXE = Join-Path $SuiteRoot 'apps\CommServer\PythonSambaCommServer.exe'

    $dataRoot = Expand-SigLabPath ([string]$Profile.data_root)
    if (-not $dataRoot) { $dataRoot = Join-Path $env:USERPROFILE 'Documents\SigLabSuite' }
    $localRoot = if ($Profile.PSObject.Properties.Name -contains 'local_data_root') {
        Expand-SigLabPath ([string]$Profile.local_data_root)
    } else { $null }
    if (-not $localRoot) { $localRoot = Join-Path $env:LOCALAPPDATA 'SigLabSuite' }
    $env:SIGLAB_DATA_ROOT = $dataRoot
    $env:SIGLAB_LOCAL_DATA_ROOT = $localRoot

    Remove-Item Env:SIGLAB_TOKEN_FILE -ErrorAction SilentlyContinue
    if ($Profile.token_file) {
        $env:SIGLAB_TOKEN_FILE = Expand-SigLabPath ([string]$Profile.token_file)
    }
    foreach ($path in @(
        (Join-Path $dataRoot 'Data'), (Join-Path $dataRoot 'Exports'),
        (Join-Path $dataRoot 'TestResults'), (Join-Path $localRoot 'logs'),
        (Join-Path $localRoot 'config'), (Join-Path $localRoot 'recovery'),
        (Join-Path $localRoot 'diagnostics')
    )) { New-Item -ItemType Directory -Force -Path $path | Out-Null }
}

function Split-SigLabEndpoint([string]$Endpoint) {
    if ($Endpoint -notmatch '^(?<host>\[[^\]]+\]|[^:]+):(?<port>\d+)$') {
        throw "Invalid server endpoint '$Endpoint'."
    }
    $hostName = $Matches.host.Trim('[', ']')
    $port = [int]$Matches.port
    if ($port -lt 1 -or $port -gt 65535) { throw 'Server port is out of range.' }
    return @($hostName, $port)
}

function Test-SigLabTcpEndpoint([string]$Endpoint, [int]$TimeoutMs = 500) {
    $parts = Split-SigLabEndpoint $Endpoint
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($parts[0], $parts[1])
        return ($task.Wait($TimeoutMs) -and $client.Connected)
    } catch { return $false } finally { $client.Dispose() }
}

function Read-SigLabExact($Stream, [int]$Count) {
    $buffer = [byte[]]::new($Count)
    $offset = 0
    while ($offset -lt $Count) {
        $read = $Stream.Read($buffer, $offset, $Count - $offset)
        if ($read -le 0) { throw 'Communication Server closed the connection.' }
        $offset += $read
    }
    return ,$buffer
}

function Send-SigLabProtocolMessage($Stream, $Message) {
    $json = $Message | ConvertTo-Json -Compress -Depth 8
    $payload = [Text.Encoding]::UTF8.GetBytes($json)
    $length = [BitConverter]::GetBytes([Net.IPAddress]::HostToNetworkOrder($payload.Length))
    $Stream.Write($length, 0, $length.Length)
    $Stream.Write($payload, 0, $payload.Length)
    $Stream.Flush()
}

function Receive-SigLabProtocolMessage($Stream) {
    $header = Read-SigLabExact $Stream 4
    $length = [Net.IPAddress]::NetworkToHostOrder([BitConverter]::ToInt32($header, 0))
    if ($length -lt 2 -or $length -gt 16777216) { throw 'Invalid server response length.' }
    $payload = Read-SigLabExact $Stream $length
    return ([Text.Encoding]::UTF8.GetString($payload) | ConvertFrom-Json)
}

function Stop-SigLabCommunicationServer([string]$Endpoint, [string]$TokenFile = '') {
    $parts = Split-SigLabEndpoint $Endpoint
    $token = ''
    if ($TokenFile) { $token = (Get-Content -LiteralPath $TokenFile -Raw).Trim() }
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect($parts[0], $parts[1])
        $client.ReceiveTimeout = 5000
        $client.SendTimeout = 5000
        $stream = $client.GetStream()
        Send-SigLabProtocolMessage $stream @{
            id = 1; op = 'hello'; protocol = 1; name = 'siglab-testkit-stop'
            pid = $PID; instance = [guid]::NewGuid().ToString(); token = $token
        }
        $hello = Receive-SigLabProtocolMessage $stream
        if (-not $hello.ok) { throw "Server rejected stop request: $($hello.error.message)" }
        Send-SigLabProtocolMessage $stream @{
            id = 2; op = 'shutdown'; client_id = [string]$hello.result.client_id
        }
        $reply = Receive-SigLabProtocolMessage $stream
        if (-not $reply.ok) { throw "Server rejected shutdown: $($reply.error.message)" }
    } finally { $client.Dispose() }
}
