@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ダッシュボードを起動中...

:: .env 読み込み
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (.env) do (
        set "%%a=%%b"
    )
)

:: 既存プロセスを停止
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)

:: ダッシュボード起動
start /b python scripts/cm-sync-dashboard.py

:: 3秒待ってブラウザを開く
timeout /t 3 /nobreak >nul
start http://localhost:8765

echo.
echo ダッシュボードが起動しました。
echo ブラウザで http://localhost:8765 を開いてください。
echo.
echo このウィンドウを閉じるとダッシュボードも停止します。
echo.
pause
