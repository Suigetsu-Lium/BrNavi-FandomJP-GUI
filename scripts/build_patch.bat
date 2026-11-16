@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
pushd "%ROOT%"
if errorlevel 1 exit /b 1
set "PYTHON=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if "%~4"=="" (
    echo Usage:
    echo   scripts\build_patch.bat ^<from-release-dir^> ^<to-release-dir^> ^<output-zip^> [Updater.exe]
    echo.
    echo Optional fourth argument adds Updater.exe for legacy-installation bootstrap.
    popd
    exit /b 2
)
set "FROM_DIR=%~1"
set "TO_DIR=%~2"
set "OUTPUT=%~3"
if "%~4"=="" (
    "%PYTHON%" -m updater.build_patch --from-dir "%FROM_DIR%" --to-dir "%TO_DIR%" --output "%OUTPUT%"
) else (
    "%PYTHON%" -m updater.build_patch --from-dir "%FROM_DIR%" --to-dir "%TO_DIR%" --bootstrap-updater "%~4" --output "%OUTPUT%"
)
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
