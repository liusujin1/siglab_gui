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

function Wait-PackagedServerExit([string]$ServerExe, [string]$Endpoint) {
    $serverPath = [IO.Path]::GetFullPath($ServerExe)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $matching = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -eq 'PythonSambaCommServer.exe' -and
                $_.ExecutablePath -and
                [string]::Equals(
                    [IO.Path]::GetFullPath([string]$_.ExecutablePath),
                    $serverPath,
                    [StringComparison]::OrdinalIgnoreCase
                ) -and
                [string]$_.CommandLine -like "*--listen $Endpoint*"
            })
        if (-not $matching) { return }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Communication Server processes did not exit after shutdown: $Endpoint"
}

try {
    Run-Smoke (Join-Path $root 'apps\SigLabSuite\Samba.exe')
    Run-Smoke (Join-Path $root 'apps\SigLabSuite\SIDMAT.exe')
    foreach ($gui in @('Samba.exe', 'SIDMAT.exe')) {
        $endpoint = Get-FreeLoopbackEndpoint
        $process = Start-Process -FilePath (Join-Path $root "apps\SigLabSuite\$gui") -ArgumentList @(
            '--comm-server-autostart-smoke', $endpoint,
            '--comm-server-exe', ('"{0}"' -f $server)
        ) -WorkingDirectory $tempRoot -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000) -or $process.ExitCode -ne 0) {
            throw "Packaged auto-start smoke failed: $gui"
        }
        Wait-PackagedServerExit $server $endpoint
    }
    $serverProcess = Start-Process -FilePath $server -ArgumentList @(
        '--no-auto-start', '--no-firewall-prompt', '--exit-after', '750'
    ) -WorkingDirectory $tempRoot -PassThru
    if (-not $serverProcess.WaitForExit($TimeoutSeconds * 1000) -or
        $serverProcess.ExitCode -ne 0) { throw 'Communication Server smoke test failed.' }

    # The server is intentionally a self-contained one-file program so it can
    # be copied by itself to the controller PC. Verify that deployment shape,
    # not merely execution beside the GUI suite.
    $standaloneDir = Join-Path $tempRoot 'StandaloneCommServer'
    New-Item -ItemType Directory -Force -Path $standaloneDir | Out-Null
    $standaloneServer = Join-Path $standaloneDir 'PythonSambaCommServer.exe'
    Copy-Item -LiteralPath $server -Destination $standaloneServer
    $serverProcess = Start-Process -FilePath $standaloneServer -ArgumentList @(
        '--no-auto-start', '--no-firewall-prompt', '--exit-after', '750'
    ) -WorkingDirectory $standaloneDir -PassThru
    if (-not $serverProcess.WaitForExit($TimeoutSeconds * 1000) -or
        $serverProcess.ExitCode -ne 0) {
        throw 'Standalone Communication Server smoke test failed.'
    }
    Write-Host 'Frozen executable smoke tests passed.'
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
