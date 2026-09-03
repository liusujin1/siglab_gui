@echo off
setlocal

cd /d "%~dp0"

set "PRODUCT=%~1"
set "TASK_NAME=%~2"

if "%PRODUCT%"=="" (
    echo Product:
    echo   1  PythonVNATest
    echo   2  VIanalysis
    echo   3  shared core
    set /p PRODUCT=Product: 
)

if "%PRODUCT%"=="1" set "PRODUCT=python_vna_test"
if "%PRODUCT%"=="2" set "PRODUCT=vianalysis"
if "%PRODUCT%"=="3" set "PRODUCT=shared"

if "%TASK_NAME%"=="" set /p TASK_NAME=Short task name: 

if "%PRODUCT%"=="" exit /b 1
if "%TASK_NAME%"=="" exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\new_vna_feature_worktree.ps1" -Product "%PRODUCT%" -TaskName "%TASK_NAME%"
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" pause
exit /b %EXITCODE%
