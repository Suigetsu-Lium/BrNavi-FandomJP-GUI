@echo off
setlocal EnableExtensions

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
pushd "%ROOT%"
if errorlevel 1 exit /b 1

rem Keep PYTHON unquoted here; quote it only when invoking it.
set "PYTHON=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"

echo [1/4] Checking the build environment...
"%PYTHON%" -c "import PyInstaller, webview, groq, curl_cffi, bleach, markdown, pygments, requests, bs4, tzdata"
if errorlevel 1 goto missing_dependencies

set "VERSION_FILE=%TEMP%\BrNavi_VERSION_%RANDOM%.txt"
"%PYTHON%" -c "from modules.version import VERSION; print(VERSION)" > "%VERSION_FILE%"
if errorlevel 1 goto version_failed
set /p APP_VERSION=<"%VERSION_FILE%"
del "%VERSION_FILE%" > nul 2>&1
if not defined APP_VERSION goto version_failed

echo.
echo ================================================
echo   BrNavi Fandom JP - GUI Build
echo   Version: v%APP_VERSION%
echo ================================================
echo.

rem Remove previous GUI output and any obsolete CLI output.
if exist "dist\BrNavi-GUI" rmdir /s /q "dist\BrNavi-GUI"
if exist "dist\BrNavi-CLI" rmdir /s /q "dist\BrNavi-CLI"

echo [2/4] Building gui_app.py...
"%PYTHON%" -m PyInstaller --clean --noconfirm --onedir --contents-directory . --windowed --name BrNavi-GUI --distpath "dist\BrNavi-GUI" --workpath "build\pyinstaller-gui" --specpath "build\spec-gui" --paths "%ROOT%" --collect-submodules modules --collect-all webview --collect-all groq --collect-all curl_cffi --collect-all bleach --collect-all markdown --collect-all pygments --collect-all tzdata --collect-all requests --collect-all bs4 "gui_app.py"
if errorlevel 1 goto build_failed

echo.
echo [3/4] Preparing GUI data directories...
call :stage_data "dist\BrNavi-GUI\BrNavi-GUI"
if errorlevel 1 goto staging_failed

echo.
echo [4/4] Copying documentation and license files...
set "TARGET=dist\BrNavi-GUI\BrNavi-GUI"

copy /y "README.md" "%TARGET%\README.md" > nul
if errorlevel 1 goto staging_failed
copy /y "LICENSE" "%TARGET%\LICENSE" > nul
if errorlevel 1 goto staging_failed
copy /y "THIRD_PARTY_NOTICES.md" "%TARGET%\THIRD_PARTY_NOTICES.md" > nul
if errorlevel 1 goto staging_failed
if not exist "%TARGET%\docs" mkdir "%TARGET%\docs"
if errorlevel 1 goto staging_failed
copy /y "docs\architecture.md" "%TARGET%\docs\architecture.md" > nul
if errorlevel 1 goto staging_failed

echo.
echo ================================================
echo Build completed.
echo.
echo GUI: dist\BrNavi-GUI\BrNavi-GUI\BrNavi-GUI.exe
echo.
echo The CLI is not built. It can still be run from Python.
echo Distribute the entire BrNavi-GUI folder, not the EXE alone.
echo ================================================
popd
exit /b 0

:stage_data
set "TARGET=%~1"
for %%D in (parsed_index level_lists index_html downloaded_fandom_html extracted_fandom_levels downloaded_images) do (
    if not exist "%TARGET%\%%D" mkdir "%TARGET%\%%D"
    if errorlevel 1 exit /b 1
)

if not exist "%TARGET%\database\ai" mkdir "%TARGET%\database\ai"
if errorlevel 1 exit /b 1
> "%TARGET%\VERSION.txt" echo v%APP_VERSION%
if errorlevel 1 exit /b 1
exit /b 0

:missing_dependencies
echo.
echo Required Python packages are missing.
echo Install them with:
echo   "%PYTHON%" -m pip install -r requirements.txt
popd
exit /b 1

:version_failed
echo.
echo Could not read the application version from modules\version.py.
if defined VERSION_FILE del "%VERSION_FILE%" > nul 2>&1
popd
exit /b 1

:build_failed
echo.
echo PyInstaller build failed. Review the error messages above.
popd
exit /b 1

:staging_failed
echo.
echo Failed to prepare the distribution files. Check disk space and permissions.
popd
exit /b 1
