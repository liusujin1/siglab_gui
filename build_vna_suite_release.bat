@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set "SCRIPT=%~dp0scripts\sync_worktrees_and_build_suite.ps1"
if not exist "%SCRIPT%" (
    echo Missing script: %SCRIPT%
    pause
    exit /b 1
)

set "VERSION=%~1"
if "%VERSION%"=="" (
    echo Enter release version, for example 2.9.3 or v2.9.3.
    echo Leave empty to keep the current version.
    set /p VERSION=Version: 
)

echo.
echo Syncing worktrees and building PythonVNA suite...
echo.

if "%VERSION%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Apply -Build
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Apply -Build -Version "%VERSION%"
)

set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
    echo Build failed with exit code %EXITCODE%.
) else (
    set "LATEST_PATH_FILE=%~dp0dist\LATEST_SUITE_PATH.txt"
    if exist "!LATEST_PATH_FILE!" (
        set /p OUTPUT_PATH=<"!LATEST_PATH_FILE!"
    ) else (
        set "OUTPUT_PATH=%~dp0dist\PythonVNA_Suite"
    )
    echo Build finished successfully.
    echo Output: !OUTPUT_PATH!
    echo Version file: !OUTPUT_PATH!\VERSION.txt
)
pause
exit /b %EXITCODE%
