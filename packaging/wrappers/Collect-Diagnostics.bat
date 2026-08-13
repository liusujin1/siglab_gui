@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Collect-Diagnostics.ps1" %*
exit /b %errorlevel%
