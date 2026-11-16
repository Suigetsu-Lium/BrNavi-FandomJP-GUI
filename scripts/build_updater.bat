@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
pushd "%ROOT%"
if errorlevel 1 exit /b 1
set "PYTHON=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
echo [1/2] Checking PyInstaller and Tkinter...
"%PYTHON%" -c "import PyInstaller, tkinter, cryptography"
if errorlevel 1 goto missing_dependencies
echo [2/2] Building standalone Updater.exe...
"%PYTHON%" -m PyInstaller --clean --noconfirm --onefile --windowed --name Updater --distpath "dist\Updater" --workpath "build\pyinstaller-updater" --specpath "build\spec-updater" --paths "%ROOT%" "updater\main.py"
if errorlevel 1 goto build_failed
echo.
echo Updater build completed: dist\Updater\Updater.exe
popd
exit /b 0
:missing_dependencies
echo PyInstaller or Tkinter is unavailable. Check the Python environment.
popd
exit /b 1
:build_failed
echo Updater build failed. Review the error messages above.
popd
exit /b 1
