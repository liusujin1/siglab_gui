@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose-client.ps1" %*
echo.
echo Please take a photo of the summary above.
pause
