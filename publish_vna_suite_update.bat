@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set "SCRIPT=%~dp0scripts\publish_vna_suite_update.ps1"
if not exist "%SCRIPT%" (
    echo Missing script: %SCRIPT%
    pause
    exit /b 1
)

set "VERSION=%~1"
if "%VERSION%"=="" (
    echo Enter new release version, for example 3.1.5 or v3.1.5.
    set /p VERSION=Version: 
)

if "%VERSION%"=="" (
    echo Version is required.
    pause
    exit /b 1
)

set "MODE="
echo.
echo Mode:
echo   [Enter] 1  standard   Build full release, build incremental update, publish both.
echo           2  existing   Publish existing dist artifacts only; no rebuild.
echo           3  fast       Skip tests; reuse the online full archive when present, otherwise build/upload it.
echo           4  full       Force full-only publish.
set /p MODE=Mode: 

if "%MODE%"=="" set "MODE=1"
if "%MODE%"=="1" set "MODE=standard"
if "%MODE%"=="2" set "MODE=existing"
if "%MODE%"=="3" set "MODE=fast"
if "%MODE%"=="4" set "MODE=full"

set "BASE_PATH="
if /I "%MODE%"=="existing" (
    echo.
    echo Optional: enter a base release folder for the incremental manifest.
    echo Leave blank to auto-pick the newest older dist\PythonVNA_Suite_v* folder.
    set /p BASE_PATH=BasePath: 
)

set "PRUNE_LOCAL="
echo.
echo Prune old local release folders and archives after a successful publish?
echo   [Enter] no
echo   y       keep only the newest 2 local releases and matching update archives
set /p PRUNE_LOCAL=PruneLocal: 

echo.
echo Building and publishing PythonVNA Suite...
echo HTTPS manifest: https://vna.liusujin.de:8443/pythonvna/manifest.json
echo.

set "EXTRA_ARGS="
if /I "%PRUNE_LOCAL%"=="y" (
    set "EXTRA_ARGS=!EXTRA_ARGS! -PruneLocalArtifacts"
)

if /I "%MODE%"=="full" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Version "%VERSION%" -FullOnly !EXTRA_ARGS!
) else if /I "%MODE%"=="existing" (
    if "%BASE_PATH%"=="" (
        powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Version "%VERSION%" -UseExistingArtifacts !EXTRA_ARGS!
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Version "%VERSION%" -BasePath "%BASE_PATH%" -UseExistingArtifacts !EXTRA_ARGS!
    )
) else if /I "%MODE%"=="fast" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Version "%VERSION%" -SkipFullUpload !EXTRA_ARGS!
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Version "%VERSION%" !EXTRA_ARGS!
)

set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
    echo Publish failed with exit code %EXITCODE%.
) else (
    echo Publish finished successfully.
)
pause
exit /b %EXITCODE%
