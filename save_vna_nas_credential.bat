@echo off
setlocal

cd /d "%~dp0"

set "SCRIPT=%~dp0scripts\save_vna_nas_credential.ps1"
if not exist "%SCRIPT%" (
    echo Missing script: %SCRIPT%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
    echo Saving credential failed with exit code %EXITCODE%.
) else (
    echo Credential saved.
)
pause
exit /b %EXITCODE%
