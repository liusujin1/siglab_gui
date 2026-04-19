$ErrorActionPreference = "Stop"

$cli = "$env:USERPROFILE\.codex\.tmp\codex-cli-new.exe"
if (-not (Test-Path $cli)) {
  throw "CLI not found: $cli"
}

& $cli resume --cd "D:\SynologyDrive\codex\vna" "019d8fa2-3e79-7b91-a4f8-7b22e61a7015"
