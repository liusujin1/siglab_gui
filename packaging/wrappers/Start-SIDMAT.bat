@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-SIDMAT.ps1" %*
exit /b %errorlevel%
