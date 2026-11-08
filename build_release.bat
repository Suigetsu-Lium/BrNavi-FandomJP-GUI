@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0"

echo ================================================
echo   Backrooms Navigation System - Build
echo ================================================
echo.

set DIST_DIR=dist\BackroomsNavigationSystem_FandomJP

:: 前回のゴミを削除
if exist "%DIST_DIR%" (
    echo 前回のビルドフォルダをクリーンアップ中...
    rd /s /q "%DIST_DIR%"
)

echo.
echo [1/3] PyInstallerでビルドしています...
pyinstaller --clean --noconfirm --onedir --console ^
    --name BackroomsNavigationSystem_FandomJP ^
    --contents-directory . ^
    --paths modules ^
    --collect-submodules modules ^
    BackroomsNavigationSystem_FandomJP.py

if errorlevel 1 (
    echo.
    echo ❌ ビルドに失敗しました。
    pause
    exit /b 1
)

echo.
echo [2/3] 必要なデータフォルダを配置しています...

if exist "parsed_index" (
    echo   - parsed_index をコピー中...
    xcopy /E /I /Y "parsed_index" "%DIST_DIR%\parsed_index\" > nul
) else (
    mkdir "%DIST_DIR%\parsed_index" 2>nul
)

if exist "level_lists" (
    echo   - level_lists をコピー中...
    xcopy /E /I /Y "level_lists" "%DIST_DIR%\level_lists\" > nul
) else (
    mkdir "%DIST_DIR%\level_lists" 2>nul
)

if exist "index_html" (
    echo   - index_html をコピー中...
    xcopy /E /I /Y "index_html" "%DIST_DIR%\index_html\" > nul
) else (
    mkdir "%DIST_DIR%\index_html" 2>nul
)

if not exist "%DIST_DIR%\data" mkdir "%DIST_DIR%\data" 2>nul
if not exist "%DIST_DIR%\extracted_fandom_levels" mkdir "%DIST_DIR%\extracted_fandom_levels" 2>nul

echo.
echo [3/3] ビルドが正常に完了しました！
echo.
echo 出力先:
echo %DIST_DIR%\
echo.
pause
