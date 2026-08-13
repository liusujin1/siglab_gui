param(
    [Parameter(Mandatory=$true)][string]$StageRoot,
    [int]$TimeoutSeconds = 30
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $StageRoot).Path
$env:QT_QPA_PLATFORM = 'offscreen'
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("SigLabSmoke-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
$env:LOCALAPPDATA = Join-Path $tempRoot 'LocalAppData'
$env:SIGLAB_LOCAL_DATA_ROOT = Join-Path $tempRoot 'Runtime'
$env:SIGLAB_DATA_ROOT = Join-Path $tempRoot 'Data'
$profile = Join-Path $root 'config\test-mock.json'
$server = Join-Path $root 'apps\CommServer\PythonSambaCommServer.exe'

function Run-Smoke([string]$Exe) {
    $process = Start-Process -FilePath $Exe -ArgumentList @(
        '--smoke-test', '--suite-config', ('"{0}"' -f $profile),
        '--comm-server-exe', ('"{0}"' -f $server)
    ) -WorkingDirectory $tempRoot -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        [void]$process.CloseMainWindow()
        throw "Smoke test timed out: $Exe"
    }
    if ($process.ExitCode -ne 0) { throw "Smoke test failed ($($process.ExitCode)): $Exe" }
}

function Get-FreeLoopbackEndpoint {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
    return "127.0.0.1:$port"
}

try {
    Run-Smoke (Join-Path $root 'apps\Samba\Samba.exe')
    Run-Smoke (Join-Path $root 'apps\SIDMAT\SIDMAT.exe')
    foreach ($gui in @('Samba\Samba.exe', 'SIDMAT\SIDMAT.exe')) {
        $endpoint = Get-FreeLoopbackEndpoint
        $process = Start-Process -FilePath (Join-Path $root "apps\$gui") -ArgumentList @(
            '--comm-server-autostart-smoke', $endpoint,
            '--comm-server-exe', ('"{0}"' -f $server)
        ) -WorkingDirectory $tempRoot -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000) -or $process.ExitCode -ne 0) {
            throw "Packaged auto-start smoke failed: $gui"
        }
    }
    $serverProcess = Start-Process -FilePath $server -ArgumentList @(
        '--no-auto-start', '--no-firewall-prompt', '--exit-after', '750'
    ) -WorkingDirectory $tempRoot -PassThru
    if (-not $serverProcess.WaitForExit($TimeoutSeconds * 1000) -or
        $serverProcess.ExitCode -ne 0) { throw 'Communication Server smoke test failed.' }
    Write-Host 'Frozen executable smoke tests passed.'
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
