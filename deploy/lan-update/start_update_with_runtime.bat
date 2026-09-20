@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-update-with-runtime.ps1" %*
set "result=%errorlevel%"
echo.
echo Exit code: %result%
pause
exit /b %result%
