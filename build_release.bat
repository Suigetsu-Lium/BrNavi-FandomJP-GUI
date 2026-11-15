@echo off
chcp 65001 > nul
setlocal EnableExtensions

for %%I in ("%~dp0.") do set "ROOT=%%~fI"
pushd "%ROOT%"
if errorlevel 1 exit /b 1

rem Prefer the repository virtual environment; otherwise use PATH Python.
set "PYTHON=python"
if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"

echo [1/4] ビルド環境を確認しています...
"%PYTHON%" -c "import PyInstaller, webview, groq, curl_cffi, bleach, markdown, pygments, requests, bs4, tzdata"
if errorlevel 1 goto missing_dependencies

for /f "delims=" %%V in ('"%PYTHON%" -c "from modules.version import VERSION; print(VERSION)"') do set "APP_VERSION=%%V"
if not defined APP_VERSION goto version_failed

echo.
echo ================================================
echo   BrNavi Fandom JP - GUI Build
echo   Version: v%APP_VERSION%
echo ================================================
echo.

rem Remove previous GUI output and any obsolete CLI build output.
if exist "dist\BrNavi-GUI" rmdir /s /q "dist\BrNavi-GUI"
if exist "dist\BrNavi-CLI" rmdir /s /q "dist\BrNavi-CLI"

echo [2/4] GUIアプリ gui_app.py をビルドしています...
"%PYTHON%" -m PyInstaller --clean --noconfirm --onedir --contents-directory . --windowed --name BrNavi-GUI --distpath "dist\BrNavi-GUI" --workpath "build\pyinstaller-gui" --specpath "build\spec-gui" --paths "%ROOT%" --collect-submodules modules --collect-all webview --collect-all groq --collect-all curl_cffi --collect-all bleach --collect-all markdown --collect-all pygments --collect-all tzdata --collect-all requests --collect-all bs4 "gui_app.py"
if errorlevel 1 goto build_failed

echo.
echo [3/4] GUI用のデータ保存先を準備しています...
call :stage_data "dist\BrNavi-GUI\BrNavi-GUI"
if errorlevel 1 goto staging_failed

echo.
echo [4/4] README、ライセンス、バージョン情報を配置しています...
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
echo ビルドが完了しました。
echo.
echo GUI: dist\BrNavi-GUI\BrNavi-GUI\BrNavi-GUI.exe
echo.
echo CLI版はビルド対象外です。Pythonから従来どおり実行できます。
echo 配布時は BrNavi-GUI フォルダー全体をまとめてください。
echo exe単体では起動できません。
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
echo 必要な依存パッケージがありません。
echo 使用中のPython環境で次を実行してから再試行してください:
echo   "%PYTHON%" -m pip install -r requirements.txt
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
