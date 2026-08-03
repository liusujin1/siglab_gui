@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

echo Starting python_samba GUI...
echo Dir: %CD%
echo.

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 goto use_py

set "PYEXE="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Python\PythonCore\3.12\InstallPath" /v ExecutablePath 2^>nul ^| findstr /i "ExecutablePath"') do set "PYEXE=%%B"
if defined PYEXE if exist "%PYEXE%" goto use_python_exe
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
  goto use_python_exe
)

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 goto use_python
echo ERROR: no usable Python installation was found
echo Install Python 3.12 and then install PySide6 and pyserial.
pause
exit /b 1

:use_py
py -3 -m python_samba.cli gui
if errorlevel 1 goto fail
exit /b 0

:use_python_exe
"%PYEXE%" -m python_samba.cli gui
if errorlevel 1 goto fail
exit /b 0

:use_python
python -m python_samba.cli gui
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Launch failed. Install dependencies then retry:
if defined PYEXE echo   "%PYEXE%" -m pip install PySide6 pyserial
if not defined PYEXE echo   py -3 -m pip install PySide6 pyserial
echo.
pause
exit /b 1
