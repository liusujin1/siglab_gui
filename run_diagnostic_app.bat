@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "APP_DIR=%ROOT_DIR%"
if not exist "%APP_DIR%python_vna\diagnostic\app.py" (
    set "APP_DIR=%ROOT_DIR%.worktrees\vna_diagnostic_current"
)
set "PYTHON=%ROOT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    set "PYTHON=%ROOT_DIR%..\..\.venv\Scripts\python.exe"
)
if not exist "%PYTHON%" (
    set "PYTHON=python"
)
set "LOG_FILE=%APP_DIR%\diagnostic_launcher.log"
echo [%DATE% %TIME%] ROOT=%ROOT_DIR% APP=%APP_DIR% PYTHON=%PYTHON% >> "%LOG_FILE%"

if not exist "%APP_DIR%\python_vna\diagnostic\app.py" (
    echo [ERROR] Diagnostic worktree was not found:
    echo %APP_DIR%
    echo [%DATE% %TIME%] ERROR missing app dir: %APP_DIR% >> "%LOG_FILE%"
    pause
    exit /b 1
)

cd /d "%APP_DIR%"
"%PYTHON%" -m python_vna.diagnostic.app %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Diagnostic app exited with code %EXIT_CODE%.
    echo See log:
    echo %LOG_FILE%
    echo [%DATE% %TIME%] ERROR exit code %EXIT_CODE% >> "%LOG_FILE%"
    pause
)

exit /b %EXIT_CODE%
