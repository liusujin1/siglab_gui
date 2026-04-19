$ErrorActionPreference = "Stop"

$codexHome = "C:\Users\41929\.codex"
$indexPath = Join-Path $codexHome "session_index.jsonl"
$sessionsRoot = Join-Path $codexHome "sessions"
$stateFiles = @("state_5.sqlite", "state_5.sqlite-wal", "state_5.sqlite-shm")
$targetIds = @(
  "019d8ab4-2cd9-7473-84fa-0402d8f67b8e",
  "019d8fa2-3e79-7b91-a4f8-7b22e61a7015"
)

Write-Host "Step 1/5: checking Codex processes..."
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "*odex*" }
if ($running -ne $null) {
  Write-Host "Codex is still running. Exit Codex completely (including tray) and run again." -ForegroundColor Yellow
  $running | Select-Object ProcessName, Id, StartTime | Format-Table -AutoSize
  exit 1
}

if (-not (Test-Path $sessionsRoot)) {
  throw ("sessions folder not found: " + $sessionsRoot)
}

Write-Host "Step 2/5: rebuilding session_index.jsonl..."
$files = Get-ChildItem -Path $sessionsRoot -Recurse -File -Filter "*.jsonl" | Sort-Object LastWriteTime
$lines = New-Object System.Collections.ArrayList

foreach ($f in $files) {
  $id = $null
  $name = $null

  foreach ($line in Get-Content -Path $f.FullName -Encoding UTF8) {
    try { $o = $line | ConvertFrom-Json } catch { continue }

    if (($id -eq $null) -and ($o.type -eq "session_meta")) {
      $id = $o.payload.id
    }

    if (($o.type -eq "response_item") -and ($o.payload.type -eq "message") -and ($o.payload.role -eq "user")) {
      $txtObj = $o.payload.content | Where-Object { $_.type -eq "input_text" } | Select-Object -First 1
      if ($txtObj -ne $null) {
        $txt = $txtObj.text
        if (($txt -ne $null) -and ($txt -notmatch "^<environment_context>")) {
          $flat = ($txt -replace "`r|`n", " ").Trim()
          if ($flat.Length -gt 60) { $flat = $flat.Substring(0, 60) }
          if ($name -eq $null) { $name = $flat }
        }
      }
    }

    if (($id -ne $null) -and ($name -ne $null)) { break }
  }

  if ($id -ne $null) {
    if (($name -eq $null) -or ($name -eq "")) { $name = "Recovered Session" }
    if ($id -eq "019d8ab4-2cd9-7473-84fa-0402d8f67b8e") { $name = "vna优化" }
    if ($id -eq "019d8fa2-3e79-7b91-a4f8-7b22e61a7015") { $name = "找回本地对话" }
    $updated = (Get-Date).ToUniversalTime().ToString("o")
    $jsonLine = '{"id":"' + $id + '","thread_name":"' + $name + '","updated_at":"' + $updated + '"}'
    [void]$lines.Add($jsonLine)
  }
}

if (Test-Path $indexPath) {
  $bak = $indexPath + ".bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
  Copy-Item -LiteralPath $indexPath -Destination $bak -Force
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($indexPath, [string[]]$lines.ToArray(), $utf8NoBom)

Write-Host "Step 3/5: backing up and rotating state db files..."
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
foreach ($sf in $stateFiles) {
  $p = Join-Path $codexHome $sf
  if (Test-Path $p) {
    Copy-Item -LiteralPath $p -Destination ($p + ".bak-" + $stamp) -Force
    Rename-Item -LiteralPath $p -NewName ($sf + ".old-" + $stamp) -Force
  }
}

Write-Host "Step 4/5: verifying target session files..."
foreach ($sid in $targetIds) {
  $m = Get-ChildItem -Path $sessionsRoot -Recurse -File -Filter ("*" + $sid + "*.jsonl") -ErrorAction SilentlyContinue
  if ($m) {
    Write-Host ("FOUND " + $sid)
  } else {
    Write-Host ("MISSING " + $sid) -ForegroundColor Yellow
  }
}

Write-Host "Step 5/5: done. Open Codex again." -ForegroundColor Green
