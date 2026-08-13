param([string]$Profile = '')
. (Join-Path $PSScriptRoot 'TestKit.Common.ps1')
$root = Get-SigLabSuiteRoot
if (-not $Profile) { $Profile = Join-Path $root 'config\test-local.json' }
elseif (-not [IO.Path]::IsPathRooted($Profile)) { $Profile = Join-Path $root $Profile }
$config = Import-SigLabProfile $Profile
Set-SigLabEnvironment $config $root
$exe = Join-Path $root 'apps\SigLabSuite\SIDMAT.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "SIDMAT component is missing: $exe" }
Start-Process -FilePath $exe -WorkingDirectory $env:SIGLAB_DATA_ROOT -ArgumentList @(
    '--suite-config', ('"{0}"' -f $config._path),
    '--comm-server-exe', ('"{0}"' -f $env:SIGLAB_COMM_SERVER_EXE)
)
