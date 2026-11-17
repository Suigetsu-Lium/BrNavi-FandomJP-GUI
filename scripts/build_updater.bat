
@echo off
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
pushd "%ROOT%"
if errorlevel 1 exit /b 1

set "PYTHON=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"

echo.
echo [1/3] Detecting Python Tcl/Tk libraries...

set "PYTHON_BASE_FILE=%TEMP%\brnavi_python_base_%RANDOM%.txt"
"%PYTHON%" -c "import sys; print(sys.base_prefix)" > "%PYTHON_BASE_FILE%"
if errorlevel 1 goto missing_dependencies

set /p PYTHON_BASE=<"%PYTHON_BASE_FILE%"
del "%PYTHON_BASE_FILE%" >nul 2>&1

set "TCL_LIBRARY=%PYTHON_BASE%\tcl\tcl8.6"
set "TK_LIBRARY=%PYTHON_BASE%\tcl\tk8.6"

if not exist "%TCL_LIBRARY%\init.tcl" goto missing_tcl
if not exist "%TK_LIBRARY%\tk.tcl" goto missing_tk

echo.
echo [2/3] Checking dependencies...

"%PYTHON%" -c "import PyInstaller, tkinter, _tkinter, cryptography"
if errorlevel 1 goto missing_dependencies

echo.
echo [3/3] Building standalone Updater.exe...

"%PYTHON%" -m PyInstaller ^
    --clean ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name Updater ^
    --distpath "dist\Updater" ^
    --workpath "build\pyinstaller-updater" ^
    --specpath "build\spec-updater" ^
    --paths "%ROOT%" ^
    --hidden-import=tkinter ^
    --hidden-import=_tkinter ^
    --collect-all tkinter ^
    "updater\main.py"

if errorlevel 1 goto build_failed

if not exist "dist\Updater\Updater.exe" goto output_missing

echo.
echo ========================================
echo Updater build completed successfully.
echo Output: dist\Updater\Updater.exe
echo ========================================
echo.

popd
exit /b 0

:missing_tcl
echo.
echo ERROR: Tcl init.tcl was not found.
echo Expected: %TCL_LIBRARY%\init.tcl
echo.
popd
exit /b 1

:missing_tk
echo.
echo ERROR: Tk tk.tcl was not found.
echo Expected: %TK_LIBRARY%\tk.tcl
echo.
popd
exit /b 1

:missing_dependencies
echo.
echo ERROR: Python, PyInstaller, Tkinter, _tkinter or cryptography is unavailable.
echo Check the Python environment and installed dependencies.
echo.
popd
exit /b 1

:build_failed
echo.
echo ERROR: Updater build failed.
echo Review the error messages above.
echo.
popd
exit /b 1

:output_missing
echo.
echo ERROR: Updater.exe was not generated.
echo.
popd
exit /b 1
