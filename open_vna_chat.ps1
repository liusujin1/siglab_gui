$ErrorActionPreference = "Stop"

$cli = "$env:USERPROFILE\.codex\.tmp\codex-cli-new.exe"
if (-not (Test-Path $cli)) {
  throw "CLI not found: $cli"
}

& $cli resume --cd "D:\SynologyDrive\codex\vna" "019d8ab4-2cd9-7473-84fa-0402d8f67b8e"
