@echo off
setlocal
cd /d "%~dp0"

python -m pip install -e ".[gui,build]"
if errorlevel 1 exit /b %errorlevel%

python -m PyInstaller --clean --noconfirm PythonSambaCommServer.spec
if errorlevel 1 exit /b %errorlevel%

echo.
echo Built: %CD%\dist\PythonSambaCommServer.exe
endlocal
