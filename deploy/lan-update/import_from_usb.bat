@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0import.ps1" %*
set "result=%errorlevel%"
echo Exit code: %result%
pause
exit /b %result%
