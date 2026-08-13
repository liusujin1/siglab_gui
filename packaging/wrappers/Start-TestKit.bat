@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-TestKit.ps1" %*
exit /b %errorlevel%
