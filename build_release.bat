@echo off
chcp 65001 > nul
setlocal

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
pushd "%ROOT%"

set "PYTHON=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"

"%PYTHON%" -c "import PyInstaller, webview, groq, curl_cffi, bleach, markdown, pygments"
if errorlevel 1 goto missing_dependencies

for /f "delims=" %%V in ('%PYTHON% -c "from modules.version import VERSION; print(VERSION)"') do set "APP_VERSION=%%V"
if not defined APP_VERSION goto version_failed

echo ================================================
echo   BrNavi Fandom JP - GUI / CLI Build
echo   Version: v%APP_VERSION%
echo ================================================
echo.

for %%D in ("dist\BrNavi-GUI" "dist\BrNavi-CLI") do (
    if exist "%%~D" rmdir /s /q "%%~D"
)

echo [1/4] GUIアプリをビルドしています...
"%PYTHON%" -m PyInstaller --clean --noconfirm --onedir --contents-directory . --windowed --name BrNavi-GUI --distpath "dist\BrNavi-GUI" --workpath "build\pyinstaller-gui" --specpath "build\spec-gui" --paths "%ROOT%" --collect-submodules modules --collect-all webview --collect-all groq --collect-all curl_cffi --collect-all bleach --collect-all markdown --collect-all pygments --collect-all tzdata "gui_app.py"
if errorlevel 1 goto build_failed

echo.
echo [2/4] CLIアプリをビルドしています...
"%PYTHON%" -m PyInstaller --clean --noconfirm --onedir --contents-directory . --console --name BrNavi-CLI --distpath "dist\BrNavi-CLI" --workpath "build\pyinstaller-cli" --specpath "build\spec-cli" --paths "%ROOT%" --collect-submodules modules --collect-all curl_cffi "BackroomsNavigationSystem_FandomJP.py"
if errorlevel 1 goto build_failed

echo.
echo [3/4] 配布データを配置しています...
call :stage_data "dist\BrNavi-GUI\BrNavi-GUI"
if errorlevel 1 goto staging_failed
call :stage_data "dist\BrNavi-CLI\BrNavi-CLI"
if errorlevel 1 goto staging_failed

echo.
echo [4/4] README、ライセンス、バージョン情報を配置しています...
for %%T in ("dist\BrNavi-GUI\BrNavi-GUI" "dist\BrNavi-CLI\BrNavi-CLI") do (
    copy /y "README.md" "%%~T\README.md" > nul
    if errorlevel 1 goto staging_failed
    copy /y "LICENSE" "%%~T\LICENSE" > nul
    if errorlevel 1 goto staging_failed
    copy /y "THIRD_PARTY_NOTICES.md" "%%~T\THIRD_PARTY_NOTICES.md" > nul
    if errorlevel 1 goto staging_failed
    if not exist "%%~T\docs" mkdir "%%~T\docs"
    if errorlevel 1 goto staging_failed
    copy /y "docs\architecture.md" "%%~T\docs\architecture.md" > nul
    if errorlevel 1 goto staging_failed
)

echo.
echo ビルドが完了しました。
echo GUI: dist\BrNavi-GUI\BrNavi-GUI\BrNavi-GUI.exe
echo CLI: dist\BrNavi-CLI\BrNavi-CLI\BrNavi-CLI.exe
echo.
echo 各フォルダーをまとめて配布してください。exe単体では起動できません。
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
echo 必要な依存パッケージがありません。
echo Python環境で次を実行してから再試行してください:
echo   python -m pip install -r requirements.txt
popd
exit /b 1

:version_failed
echo.
echo modules\version.py からバージョンを取得できません。
popd
exit /b 1

:build_failed
echo.
echo PyInstallerのビルドに失敗しました。上のエラーを確認してください。
popd
exit /b 1

:staging_failed
echo.
echo 配布データの配置に失敗しました。ディスク容量と書き込み権限を確認してください。
popd
exit /b 1
