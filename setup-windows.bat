@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo   コイン価格管理システム - Windows セットアップ
echo ============================================================
echo.

:: ===== 管理者権限チェック =====
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [情報] 管理者権限で再起動します...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

:: ===== 1. Python チェック・インストール =====
echo [1/5] Python を確認中...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   Python が見つかりません。インストールします...
    echo.

    :: winget が使えるか確認
    winget --version >nul 2>&1
    if %errorlevel% equ 0 (
        echo   winget で Python 3.11 をインストール中...
        winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    ) else (
        echo   ========================================
        echo   Python を手動でインストールしてください:
        echo   https://www.python.org/downloads/
        echo.
        echo   ※ インストール時に「Add Python to PATH」に
        echo     必ずチェックを入れてください
        echo   ========================================
        echo.
        pause
        exit /b 1
    )

    :: PATH を再読み込み
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts"
)

python --version
echo   OK
echo.

:: ===== 2. pip パッケージインストール =====
echo [2/5] Python パッケージをインストール中...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo   [エラー] パッケージのインストールに失敗しました
    pause
    exit /b 1
)
echo   OK
echo.

:: ===== 3. Playwright ブラウザインストール =====
echo [3/5] Playwright ブラウザをインストール中...
python -m playwright install chromium
echo   OK
echo.

:: ===== 4. Google Cloud SDK チェック・インストール =====
echo [4/5] Google Cloud SDK を確認中...
gcloud --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   Google Cloud SDK が見つかりません。インストールします...
    echo.

    winget --version >nul 2>&1
    if %errorlevel% equ 0 (
        echo   winget で Google Cloud SDK をインストール中...
        winget install Google.CloudSDK --accept-package-agreements --accept-source-agreements
    ) else (
        echo   ========================================
        echo   Google Cloud SDK を手動でインストールしてください:
        echo   https://cloud.google.com/sdk/docs/install
        echo   ========================================
        echo.
        pause
        exit /b 1
    )
)
echo   OK
echo.

:: ===== 5. .env ファイル作成 =====
echo [5/5] 環境設定ファイルを確認中...
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo   .env ファイルを作成しました
    ) else (
        (
            echo SPREADSHEET_ID=1flJBKOu6MP--XmXctF5IfM5aLtyggH87dCQXu4qPOUE
            echo COLORME_ACCESS_TOKEN=1524f830f51836d36ff8eb32f71755ed996a05c737a822cf72d1474a167b0320
        ) > .env
        echo   .env ファイルを作成しました
    )
) else (
    echo   .env ファイルは既に存在します
)

:: ログディレクトリ作成
if not exist logs mkdir logs
if not exist cache mkdir cache

echo.

:: ===== Google Cloud 認証 =====
echo ============================================================
echo   Google Cloud 認証
echo ============================================================
echo.
echo   ブラウザが開きます。Googleアカウントでログインしてください。
echo   （初回のみ必要です）
echo.
pause

gcloud auth application-default login --scopes="openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets"
gcloud auth application-default set-quota-project coin-price-tracker-479614

:: ===== ショートカット作成 =====
echo.
echo デスクトップにショートカットを作成中...
set "DESKTOP=%USERPROFILE%\Desktop"
set "SCRIPT_DIR=%~dp0"

:: ダッシュボード起動ショートカット
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\コイン価格管理.lnk'); $s.TargetPath = '%SCRIPT_DIR%ダッシュボード起動.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.IconLocation = 'shell32.dll,14'; $s.Description = 'コイン価格管理ダッシュボード'; $s.Save()"
echo   「コイン価格管理」ショートカットをデスクトップに作成しました
echo.

:: ===== 完了 =====
echo ============================================================
echo   セットアップ完了！
echo ============================================================
echo.
echo   デスクトップの「コイン価格管理」をダブルクリックで起動できます。
echo   ブラウザで http://localhost:8765 を開いてください。
echo.
pause
