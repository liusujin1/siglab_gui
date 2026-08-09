@echo off
rem =====================================================================
rem  python_sidmat GUI launcher (mock or serial backend)
rem  Usage:  open_gui.bat                -> GUI
rem  =====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
for %%I in ("%~dp0.") do set "SIDMAT_ROOT=%%~fI"
for %%I in ("%~dp0..\python_samba") do set "SAMBA_ROOT=%%~fI"

echo Starting python_sidmat GUI...
echo Dir: %CD%
echo.

rem ---- locate a Python that can run PySide6 ---------------------------
set "PYEXE="

rem 1) project virtual environment (preferred)
rem    The remote test machine keeps python_samba's GUI dependencies here.
if exist "%SAMBA_ROOT%\.venv\Scripts\python.exe" set "PYEXE=%SAMBA_ROOT%\.venv\Scripts\python.exe"

rem 2) py launcher
where py >nul 2>&1
if not defined PYEXE if %ERRORLEVEL% EQU 0 (
  for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)

rem 3) python on PATH
if not defined PYEXE (
  where python >nul 2>&1
  if %ERRORLEVEL% EQU 0 (
    for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
  )
)

rem 4) common install locations
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "C:\Python314\python.exe" set "PYEXE=C:\Python314\python.exe"

if not defined PYEXE (
  echo ERROR: no usable Python installation was found.
  echo Install Python 3.11+ then run:
  echo   py -3 -m pip install -e "%SAMBA_ROOT%"
  echo   py -3 -m pip install -e "%SIDMAT_ROOT%[gui,dev,mat]"
  pause
  exit /b 1
)
echo Using Python: %PYEXE%
echo.

rem ---- run directly from the two source trees -------------------------
rem  This also works when the packages have not been installed editable.
if defined PYTHONPATH (
  set "PYTHONPATH=%SIDMAT_ROOT%\src;%SAMBA_ROOT%\src;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%SIDMAT_ROOT%\src;%SAMBA_ROOT%\src"
)
echo PYTHONPATH: %PYTHONPATH%
echo.

rem ---- check dependencies ---------------------------------------------
"%PYEXE%" -c "import python_samba, python_sidmat" >nul 2>&1
if errorlevel 1 (
  echo MISSING dependency: python_samba / python_sidmat
  echo Run:  "%PYEXE%" -m pip install -e "%SAMBA_ROOT%"
  pause
  exit /b 1
)
"%PYEXE%" -c "import PySide6, pyqtgraph, numpy" >nul 2>&1
if errorlevel 1 (
  echo MISSING dependency: PySide6 / pyqtgraph / numpy
  echo Run:  "%PYEXE%" -m pip install "PySide6>=6.6" "pyqtgraph>=0.13" "numpy>=1.24" "scipy>=1.10"
  pause
  exit /b 1
)

rem ---- launch GUI -------------------------------------------------------
"%PYEXE%" -m python_sidmat.app
if errorlevel 1 (
  echo.
  echo GUI exited with an error. See the traceback above.
  pause
  exit /b 1
)
endlocal
exit /b 0
