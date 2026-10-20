@echo off
setlocal
set "DEPLOY_SCRIPT=%~dp0tools\deploy-to-server.ps1"
if not exist "%DEPLOY_SCRIPT%" set "DEPLOY_SCRIPT=%~dp0deploy-to-server.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DEPLOY_SCRIPT%"
set "RESULT=%ERRORLEVEL%"
echo Deployment exit code: %RESULT%
pause
exit /b %RESULT%
