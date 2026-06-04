@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "APP_DIR=%ROOT_DIR%.worktrees\vna_diagnostic_current"
set "PYTHON=%ROOT_DIR%.venv\Scripts\python.exe"

if not exist "%APP_DIR%\python_vna\diagnostic\app.py" (
    echo [ERROR] Diagnostic worktree was not found:
    echo %APP_DIR%
    pause
    exit /b 1
)

if not exist "%PYTHON%" (
    set "PYTHON=python"
)

cd /d "%APP_DIR%"
"%PYTHON%" -m python_vna.diagnostic.app %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Diagnostic app exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
