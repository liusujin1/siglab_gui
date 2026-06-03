@echo off
setlocal

cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=D:\SynologyDrive\codex\vna\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m python_vna.conversion_app %*
if errorlevel 1 pause
