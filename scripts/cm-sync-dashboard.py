#!/usr/bin/env python3
"""
コイン価格管理 ダッシュボード

ローカルWebサーバーでブラウザから各種タスクの管理・モニタリングができます。

タスク:
  1. カラーミー同期（1行ずつ即時同期: ダウンロード+スクレイピング+数式復元+カラーミー同期）
  2. ブリオンスター商品取得（商品スクレイピング → スプレッドシート保存）
  3. 価格のみ同期（仕入れ先価格取得 → 価格・在庫・表示のみカラーミーへ同期）

使い方:
    python scripts/cm-sync-dashboard.py
    → ブラウザで http://localhost:8765 を開く
"""

import http.server
import json
import os
import re
import signal
import subprocess
import sys
import glob
from datetime import datetime
from pathlib import Path

PORT = 8765
PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"
BENCHMARK_SCRIPT = PROJECT_DIR / "scripts" / "check_row3.py"

# .env ファイルから環境変数を読み込み
_env_file = PROJECT_DIR / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key = _key.strip()
                _val = _val.strip().strip('"').strip("'")
                if _key and _key not in os.environ:
                    os.environ[_key] = _val

def _find_python() -> str:
    """Python実行パスを取得（Windows/macOS両対応）"""
    env_path = os.environ.get("PYTHON_PATH")
    if env_path:
        return env_path
    if sys.platform == "win32":
        return sys.executable
    return subprocess.check_output(["which", "python3"], text=True).strip()

PYTHON = _find_python()


def _subprocess_env() -> dict:
    """サブプロセス用の環境変数を構築（.envの値を含む）"""
    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    if sys.platform == "win32":
        adc_path = Path(os.environ.get("APPDATA", "")) / "gcloud" / "application_default_credentials.json"
    else:
        adc_path = Path.home() / '.config' / 'gcloud' / 'application_default_credentials.json'
    env.setdefault('GOOGLE_APPLICATION_CREDENTIALS', str(adc_path))
    return env

# ロックファイル用の一時ディレクトリ（Windows/macOS両対応）
# macOSでは tempfile.gettempdir() が /var/folders/... を返すが、
# シェルスクリプトは /tmp/ を使うため、/tmp/ に統一する
import tempfile
_TEMP_DIR = Path("/tmp") if sys.platform != "win32" else Path(tempfile.gettempdir())

# カラーミー同期
CM_SCRIPT = PROJECT_DIR / "scripts" / "cm-sync-prices.sh"
CM_LOCK = _TEMP_DIR / "cm-sync-prices.lock"
CM_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.coin-price-checker.cm-sync.plist"

# ブリオンスター商品取得
BS_SCRIPT = PROJECT_DIR / "scripts" / "bs-scrape.sh"
BS_LOCK = _TEMP_DIR / "bs-scrape.lock"
BS_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.coin-price-checker.bs-scrape.plist"

# APMEX商品取得
AP_SCRIPT = PROJECT_DIR / "scripts" / "ap-scrape.sh"
AP_LOCK = _TEMP_DIR / "ap-scrape.lock"
AP_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.coin-price-checker.ap-scrape.plist"

# カラーミー登録（BS/APMEX）
BS_REG_SCRIPT = PROJECT_DIR / "scripts" / "bs-register.sh"
BS_REG_LOCK = Path("/tmp/bs-register.lock")
AP_REG_SCRIPT = PROJECT_DIR / "scripts" / "ap-register.sh"
AP_REG_LOCK = Path("/tmp/ap-register.lock")

# 価格のみ同期
PO_SCRIPT = PROJECT_DIR / "scripts" / "cm-price-only-sync.sh"
PO_LOCK = _TEMP_DIR / "cm-price-only-sync.lock"
PO_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.coin-price-checker.price-only.plist"

# 商品説明同期
DS_LOCK = _TEMP_DIR / "cm-desc-sync.lock"

# スケジュール設定（タスクキー → 設定）
_SCHED_CONFIG = {
    'cm': {
        'win_name': 'coin-price-checker-cm-sync',
        'script': CM_SCRIPT,
        'label': 'com.coin-price-checker.cm-sync',
        'plist': CM_PLIST,
        'default_interval': 240,
    },
    'bs': {
        'win_name': 'coin-price-checker-bs-scrape',
        'script': BS_SCRIPT,
        'label': 'com.coin-price-checker.bs-scrape',
        'plist': BS_PLIST,
        'default_interval': 360,
    },
    'ap': {
        'win_name': 'coin-price-checker-ap-scrape',
        'script': AP_SCRIPT,
        'label': 'com.coin-price-checker.ap-scrape',
        'plist': AP_PLIST,
        'default_interval': 360,
    },
    'po': {
        'win_name': 'coin-price-checker-price-only',
        'script': PO_SCRIPT,
        'label': 'com.coin-price-checker.price-only',
        'plist': PO_PLIST,
        'default_interval': 240,
    },
}

def _generate_plist(task_key: str, interval_minutes: int) -> Path:
    """LaunchAgent plist ファイルを動的生成"""
    cfg = _SCHED_CONFIG[task_key]
    interval_seconds = interval_minutes * 60
    home = str(Path.home())
    pyenv_shims = f"{home}/.pyenv/shims"
    log_prefix = {
        'cm': 'cm', 'bs': 'bs', 'ap': 'ap', 'po': 'price-only',
    }.get(task_key, task_key)
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{cfg['label']}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{cfg['script']}</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval_seconds}</integer>
    <key>WorkingDirectory</key>
    <string>{PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{pyenv_shims}:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>{home}</string>
        <key>LANG</key>
        <string>ja_JP.UTF-8</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{PROJECT_DIR}/logs/launchd-{log_prefix}-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{PROJECT_DIR}/logs/launchd-{log_prefix}-stderr.log</string>
    <key>TimeOut</key>
    <integer>14400</integer>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""
    plist_path = cfg['plist']
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)
    return plist_path

def _sched_enable(task_key: str, interval_minutes: int = 0) -> None:
    """定期実行を有効にする（Windows: schtasks / macOS: launchctl）"""
    cfg = _SCHED_CONFIG[task_key]
    if interval_minutes <= 0:
        interval_minutes = cfg['default_interval']
    if sys.platform == 'win32':
        name = cfg['win_name']
        script = str(cfg['script'])
        tr = f'"{PYTHON}" -u "{script}"' if task_key in ('cm', 'po') else f'bash "{script}"'
        subprocess.run([
            'schtasks', '/Create', '/F',
            '/TN', name, '/TR', tr,
            '/SC', 'MINUTE', '/MO', str(interval_minutes),
            '/ST', '00:00',
        ], capture_output=True)
    else:
        plist_path = cfg['plist']
        # 既に有効なら一度アンロード
        subprocess.run(['launchctl', 'unload', str(plist_path)], capture_output=True)
        _generate_plist(task_key, interval_minutes)
        subprocess.run(['launchctl', 'load', str(plist_path)], capture_output=True)

def _sched_disable(task_key: str) -> None:
    """定期実行を無効にする"""
    cfg = _SCHED_CONFIG[task_key]
    if sys.platform == 'win32':
        subprocess.run(['schtasks', '/Delete', '/F', '/TN', cfg['win_name']], capture_output=True)
    else:
        subprocess.run(['launchctl', 'unload', str(cfg['plist'])], capture_output=True)

def _sched_is_enabled(task_key: str) -> bool:
    """定期実行が有効かどうかを確認する"""
    cfg = _SCHED_CONFIG[task_key]
    if sys.platform == 'win32':
        r = subprocess.run(['schtasks', '/Query', '/TN', cfg['win_name']], capture_output=True)
        return r.returncode == 0
    else:
        try:
            out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True)
            return cfg['label'] in out.stdout
        except Exception:
            return False

def _sched_get_interval(task_key: str) -> int:
    """現在の定期実行間隔（分）を取得"""
    cfg = _SCHED_CONFIG[task_key]
    if sys.platform == 'win32':
        try:
            r = subprocess.run(['schtasks', '/Query', '/TN', cfg['win_name'], '/XML'],
                               capture_output=True, text=True)
            if r.returncode == 0:
                m = re.search(r'<Interval>PT(\d+)M</Interval>', r.stdout)
                if m:
                    return int(m.group(1))
                m = re.search(r'<Interval>PT(\d+)H</Interval>', r.stdout)
                if m:
                    return int(m.group(1)) * 60
        except Exception:
            pass
    else:
        plist_path = cfg['plist']
        if plist_path.exists():
            try:
                content = plist_path.read_text()
                # 新形式: StartInterval（秒数）
                m = re.search(r'<key>StartInterval</key>\s*<integer>(\d+)</integer>', content)
                if m:
                    return int(m.group(1)) // 60
                # 旧形式: StartCalendarInterval（固定時刻配列）→ Hour要素の数から間隔を推定
                hours = re.findall(r'<key>Hour</key>\s*<integer>(\d+)</integer>', content)
                if len(hours) >= 2:
                    return 1440 // len(hours)  # 24時間 ÷ 実行回数
            except Exception:
                pass
    return cfg['default_interval']


# ========================================
# VPN (WireGuard) 管理
# ========================================

_WG_CONF_DIRS = [
    Path('/usr/local/etc/wireguard'),
    Path('/opt/homebrew/etc/wireguard'),
]

def _vpn_is_connected() -> bool:
    """WireGuard VPN接続状態を確認"""
    try:
        result = subprocess.run(['wg', 'show', 'interfaces'],
                                capture_output=True, text=True, timeout=5)
        return bool(result.stdout.strip())
    except Exception:
        return False

def _vpn_get_active_iface() -> str:
    """アクティブなWireGuardインターフェース名を取得"""
    try:
        result = subprocess.run(['wg', 'show', 'interfaces'],
                                capture_output=True, text=True, timeout=5)
        ifaces = result.stdout.strip().split()
        return ifaces[0] if ifaces else ''
    except Exception:
        return ''

def _vpn_get_conf_name() -> str:
    """利用可能なWireGuard設定ファイル名を取得（なければ wg0）"""
    for d in _WG_CONF_DIRS:
        if d.exists():
            confs = sorted(d.glob('*.conf'))
            if confs:
                return confs[0].stem
    return 'wg0'

def _vpn_enable() -> dict:
    """VPN接続を開始"""
    conf = _vpn_get_conf_name()
    try:
        result = subprocess.run(['sudo', 'wg-quick', 'up', conf],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return {'ok': True, 'message': f'VPN ({conf}) 接続しました'}
        return {'ok': False, 'message': result.stderr.strip() or f'エラー (code {result.returncode})'}
    except Exception as e:
        return {'ok': False, 'message': str(e)}

def _vpn_disable() -> dict:
    """VPN接続を切断"""
    iface = _vpn_get_active_iface() or _vpn_get_conf_name()
    try:
        result = subprocess.run(['sudo', 'wg-quick', 'down', iface],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return {'ok': True, 'message': f'VPN ({iface}) 切断しました'}
        return {'ok': False, 'message': result.stderr.strip() or f'エラー (code {result.returncode})'}
    except Exception as e:
        return {'ok': False, 'message': str(e)}

HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>コイン価格管理</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, 'Hiragino Sans', sans-serif; background: #f5f5f7; color: #1d1d1f; }
  .container { max-width: 1200px; margin: 40px auto; padding: 0 20px; }
  h1 { font-size: 24px; font-weight: 600; margin-bottom: 24px; }

  .tasks { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 720px) { .tasks { grid-template-columns: 1fr; } }

  .task-card { background: #fff; border-radius: 12px; padding: 20px;
               box-shadow: 0 1px 3px rgba(0,0,0,0.08); display: flex; flex-direction: column; gap: 14px; }
  .task-card h2 { font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
  .sheet-badge { font-size: 10px; font-weight: 500; color: #fff; background: #6e6e73; border-radius: 4px; padding: 2px 6px; white-space: nowrap; }
  details.data-flow { margin-top: -4px; }
  details.data-flow summary { font-size: 11px; color: #6e6e73; cursor: pointer; user-select: none; }
  details.data-flow summary:hover { color: #1d1d1f; }
  details.data-flow .flow-table { font-size: 10px; color: #515154; margin-top: 6px; border-collapse: collapse; width: 100%; }
  details.data-flow .flow-table th { text-align: left; padding: 3px 6px; background: #f5f5f7; border-bottom: 1px solid #e5e5ea; font-weight: 600; }
  details.data-flow .flow-table td { padding: 3px 6px; border-bottom: 1px solid #f0f0f2; }
  details.data-flow .flow-table .dir-sheet { color: #0071e3; font-weight: 600; }
  details.data-flow .flow-table .dir-api { color: #bf4800; font-weight: 600; }
  details.data-flow .flow-table .dir-none { color: #86868b; }

  .status-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; }
  .status-row + .status-row { border-top: 1px solid #f0f0f0; }
  .status-label { font-size: 14px; color: #86868b; }
  .status-value { font-size: 14px; font-weight: 500; }

  .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
  .badge-green { background: #e8f5e9; color: #2e7d32; }
  .badge-red { background: #fce4ec; color: #c62828; }
  .badge-yellow { background: #fff8e1; color: #f57f17; }
  .badge-blue { background: #e3f2fd; color: #1565c0; }
  .badge-gray { background: #f5f5f5; color: #86868b; }

  .progress-wrap { display: none; }
  .progress-wrap.active { display: block; }
  .progress-bar-bg { background: #e5e5ea; border-radius: 6px; height: 8px; overflow: hidden; margin: 6px 0; }
  .progress-bar { height: 100%; border-radius: 6px; transition: width 0.5s; }
  .progress-bar.cm { background: #007aff; }
  .progress-bar.bs { background: #ff9500; }
  .progress-bar.po { background: #34c759; }
  .progress-bar.ap { background: #af52de; }
  .progress-step { font-size: 14px; font-weight: 500; }
  .progress-text { font-size: 12px; color: #86868b; }

  .btn-group { display: flex; gap: 8px; flex-wrap: wrap; }
  .btn { flex: 1; min-width: 80px; padding: 10px 12px; border: none; border-radius: 10px;
         font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s;
         display: flex; align-items: center; justify-content: center; gap: 4px; }
  .btn:hover { filter: brightness(0.95); }
  .btn:active { transform: scale(0.98); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-primary { background: #007aff; color: #fff; }
  .btn-orange { background: #ff9500; color: #fff; }
  .btn-danger { background: #ff3b30; color: #fff; }
  .btn-success { background: #34c759; color: #fff; }
  .btn-purple { background: #af52de; color: #fff; }
  .btn-secondary { background: #e5e5ea; color: #1d1d1f; }

  .log-box { background: #1d1d1f; color: #e5e5ea; border-radius: 10px; padding: 12px;
             font-family: 'SF Mono', 'Menlo', monospace; font-size: 11px; line-height: 1.5;
             height: 240px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; flex-shrink: 0; }
  .log-box:empty::after { content: 'ログはまだありません'; color: #86868b; }

  .log-header { display: flex; justify-content: space-between; align-items: center; }
  .log-header span { font-size: 13px; color: #86868b; }
  .btn-text { background: none; border: none; color: #ff3b30; font-size: 12px; cursor: pointer;
              padding: 2px 6px; border-radius: 4px; }
  .btn-text:hover { background: #fce4ec; }

  .sched-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-top: 1px solid #f0f0f0; }
  .sched-label { font-size: 13px; color: #86868b; flex: 1; }
  .interval-select { padding: 4px 8px; border: 1px solid #e5e5ea; border-radius: 8px;
                     font-size: 12px; color: #1d1d1f; background: #fff; cursor: pointer; flex-shrink: 0; }
  .interval-select:focus { outline: none; border-color: #007aff; }
  .btn-sm { flex: 0; min-width: 50px; padding: 5px 10px; font-size: 12px; }

  .toggle-switch { position: relative; display: inline-block; width: 44px; height: 26px; flex-shrink: 0; cursor: pointer; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-slider { position: absolute; inset: 0; background: #e5e5ea; border-radius: 26px; transition: background 0.2s; }
  .toggle-slider::before { content: ''; position: absolute; width: 20px; height: 20px; left: 3px; bottom: 3px;
    background: #fff; border-radius: 50%; box-shadow: 0 1px 3px rgba(0,0,0,0.2); transition: transform 0.2s; }
  .toggle-switch input:checked + .toggle-slider { background: #34c759; }
  .toggle-switch input:checked + .toggle-slider::before { transform: translateX(18px); }

  .vpn-bar { background: #fff; border-radius: 12px; padding: 14px 20px;
             box-shadow: 0 1px 3px rgba(0,0,0,0.08); display: flex;
             justify-content: space-between; align-items: center; margin-bottom: 20px; }
  .vpn-bar-left { display: flex; align-items: center; gap: 10px; }
  .vpn-dot { width: 10px; height: 10px; border-radius: 50%; background: #e5e5ea; flex-shrink: 0; transition: background 0.3s; }
  .vpn-dot.connected { background: #34c759; box-shadow: 0 0 0 3px rgba(52,199,89,0.2); }
  .vpn-dot.disconnected { background: #ff3b30; }
  .vpn-title { font-size: 15px; font-weight: 600; }
  .vpn-iface { font-size: 12px; color: #86868b; font-family: 'SF Mono', 'Menlo', monospace; }
  .vpn-status { font-size: 14px; color: #86868b; }

  .toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%);
           background: #1d1d1f; color: #fff; padding: 12px 24px; border-radius: 10px;
           font-size: 14px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }

  /* ベンチマークモーダル */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 200;
                   display: none; align-items: center; justify-content: center; }
  .modal-overlay.active { display: flex; }
  .modal { background: #fff; border-radius: 14px; width: 520px; max-width: 95vw; max-height: 85vh;
           overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.2); }
  .modal-header { display: flex; justify-content: space-between; align-items: center;
                  padding: 16px 20px; border-bottom: 1px solid #f0f0f0; position: sticky; top: 0; background: #fff;
                  border-radius: 14px 14px 0 0; z-index: 1; }
  .modal-header h3 { font-size: 16px; font-weight: 600; }
  .modal-close { background: none; border: none; font-size: 20px; cursor: pointer; color: #86868b;
                 width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center;
                 justify-content: center; }
  .modal-close:hover { background: #f5f5f5; }
  .modal-body { padding: 16px 20px; }
  .modal-loading { text-align: center; padding: 40px; color: #86868b; font-size: 14px; }

  .diag-section { margin-bottom: 16px; }
  .diag-section-title { font-size: 12px; font-weight: 600; color: #86868b; text-transform: uppercase;
                        letter-spacing: 0.5px; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #f0f0f0; }
  .diag-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 13px; }
  .diag-key { color: #86868b; }
  .diag-val { font-weight: 500; font-family: 'SF Mono', 'Menlo', monospace; font-size: 12px; }
  .diag-val.warn { color: #f57f17; }
  .diag-val.error { color: #c62828; }
  .diag-val.ok { color: #2e7d32; }

  .diag-result { padding: 12px; border-radius: 10px; margin-top: 12px; font-size: 14px; font-weight: 600; text-align: center; }
  .diag-result.match { background: #e8f5e9; color: #2e7d32; }
  .diag-result.mismatch { background: #fce4ec; color: #c62828; }
  .diag-result.no-data { background: #fff8e1; color: #f57f17; }

  .bench-input-row { display: flex; gap: 8px; align-items: center; padding: 8px 0; border-top: 1px solid #f0f0f0; }
  .bench-input { width: 55px; padding: 6px; border: 1px solid #e5e5ea; border-radius: 8px;
                 font-size: 14px; text-align: center; }
  .bench-input:focus { outline: none; border-color: #007aff; }
</style>
</head>
<body>
<div class="container" data-component="page-container">
  <h1 data-component="page-title">コイン価格管理</h1>

  <!-- VPN ステータスバー -->
  <div class="vpn-bar" data-component="vpn-status-bar">
    <div class="vpn-bar-left" data-component="vpn-status-info">
      <span class="vpn-dot" id="vpn-dot"></span>
      <span class="vpn-title">VPN</span>
      <span class="vpn-status" id="vpn-status">確認中...</span>
      <span class="vpn-iface" id="vpn-iface"></span>
    </div>
    <label class="toggle-switch" data-component="vpn-toggle-switch">
      <input type="checkbox" id="vpn-toggle" onchange="toggleVPN(this.checked)">
      <span class="toggle-slider"></span>
    </label>
  </div>

  <div class="tasks" data-component="task-grid">

    <!-- ======== カラーミー同期 ======== -->
    <div class="task-card" data-component="card-cm-sync">
      <h2 data-component="card-cm-sync-title">カラーミー同期 <span class="sheet-badge">新カラーミー商品管理</span></h2>

      <div data-component="card-cm-sync-status">
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="cm-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="cm-last" class="status-value">...</span>
        </div>
      </div>

      <div id="cm-progress" class="progress-wrap" data-component="card-cm-sync-progress">
        <div id="cm-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="cm-progress-bar" class="progress-bar cm" style="width:0%"></div></div>
        <div id="cm-progress-text" class="progress-text"></div>
      </div>

      <div class="btn-group" data-component="card-cm-sync-actions">
        <button class="btn btn-primary" id="btn-cm-run" onclick="cmRunWithFields()">シート→API同期</button>
        <button class="btn btn-secondary" id="btn-cm-full" onclick="cmRunFull()">フルスペック</button>
        <button class="btn btn-danger" id="btn-cm-stop" onclick="doAction('cm','stop')" disabled>停止</button>
      </div>
      <div style="font-size:11px;color:#86868b;margin-top:-6px;line-height:1.5" data-component="card-cm-sync-description">
        <b>シート→API同期</b>: スプレッドシートの値を直接カラーミーAPIに送信（高速）<br>
        <b>フルスペック</b>: APIダウンロード→スクレイピング→シート更新→API同期（2〜3時間）
      </div>
      <details class="data-flow" style="margin-top:2px">
        <summary>同期項目の選択</summary>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 12px;margin-top:6px;font-size:11px">
          <label><input type="checkbox" class="cm-field" value="price" checked> 価格（AE列）</label>
          <label><input type="checkbox" class="cm-field" value="name" checked> 商品名（H列）</label>
          <label><input type="checkbox" class="cm-field" value="description" checked> 商品説明（BA-BB列）</label>
          <label><input type="checkbox" class="cm-field" value="category" checked> カテゴリ・グループ（AK,AM列）</label>
          <label><input type="checkbox" class="cm-field" value="model" checked> 型番（AO列）</label>
          <label><input type="checkbox" class="cm-field" value="stock" checked> 在庫数（AP列）</label>
          <label><input type="checkbox" class="cm-field" value="display" checked> 表示状態（B列）</label>
          <label><input type="checkbox" class="cm-field" value="stock_settings" checked> 在庫管理設定（AQ-AV列）</label>
          <label><input type="checkbox" class="cm-field" value="shipping" checked> 送料（AW列）</label>
          <label><input type="checkbox" class="cm-field" value="seo" checked> SEO（BO-BQ列）</label>
          <label><input type="checkbox" class="cm-field" value="options" checked> オプション（BR-BT列）</label>
        </div>
        <div style="margin-top:6px;display:flex;gap:8px">
          <button class="btn-text" onclick="document.querySelectorAll('.cm-field').forEach(c=>c.checked=true)">全選択</button>
          <button class="btn-text" onclick="document.querySelectorAll('.cm-field').forEach(c=>c.checked=false)">全解除</button>
        </div>
      </details>
      <details class="data-flow">
        <summary>データフロー設定</summary>
        <table class="flow-table">
          <tr><th>項目</th><th>列</th><th>ダウンロード時</th><th>同期時</th></tr>
          <tr><td>商品名</td><td>H</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>表示状態</td><td>B</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>商品説明</td><td>BA</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>簡易説明</td><td>BB</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>型番</td><td>AO</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>価格</td><td>AE</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>在庫数</td><td>AP</td><td class="dir-sheet">シート優先</td><td>連動ON時のみ</td></tr>
          <tr><td>在庫管理</td><td>AQ-AV</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>送料</td><td>AW</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>カテゴリID</td><td>AK</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>グループID</td><td>AM</td><td class="dir-sheet">シート優先</td><td>APIへ送信</td></tr>
          <tr><td>画像</td><td>BE-BN</td><td class="dir-sheet">シート優先</td><td class="dir-none">送信しない</td></tr>
        </table>
        <div style="font-size:10px;color:#86868b;margin-top:4px">
          <span class="dir-sheet">シート優先</span> = 初回はAPIの値で登録、以降はシートの値を保持
        </div>
      </details>

      <div class="sched-row" data-component="card-cm-sync-schedule">
        <span class="sched-label">定期実行</span>
        <select id="cm-interval" class="interval-select" onchange="changeInterval('cm')">
          <option value="30">30分</option><option value="60">1時間</option>
          <option value="120">2時間</option><option value="180">3時間</option>
          <option value="240">4時間</option><option value="360">6時間</option>
          <option value="480">8時間</option><option value="720">12時間</option>
          <option value="1440">24時間</option>
        </select>
        <label class="toggle-switch">
          <input type="checkbox" id="cm-sched-toggle" onchange="toggleSched('cm', this.checked)">
          <span class="toggle-slider"></span>
        </label>
      </div>

      <div class="log-header" data-component="card-cm-sync-log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('cm','clear-logs')">リセット</button>
      </div>
      <div id="cm-log" class="log-box" data-component="card-cm-sync-log"></div>
    </div>

    <!-- ======== ブリオンスター商品取得 ======== -->
    <div class="task-card" data-component="card-bs-fetch">
      <h2 data-component="card-bs-fetch-title">ブリオンスター商品取得 <span class="sheet-badge">ブリオンスター商品ページ一覧</span></h2>

      <div data-component="card-bs-fetch-status">
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="bs-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="bs-last" class="status-value">...</span>
        </div>
      </div>

      <div id="bs-progress" class="progress-wrap" data-component="card-bs-fetch-progress">
        <div id="bs-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="bs-progress-bar" class="progress-bar bs" style="width:0%"></div></div>
        <div id="bs-progress-text" class="progress-text"></div>
      </div>

      <div class="btn-group" data-component="card-bs-fetch-actions">
        <button class="btn btn-orange" id="btn-bs-run" onclick="doAction('bs','run')">商品取得開始</button>
        <button class="btn btn-secondary" id="btn-bs-register" onclick="doAction('bs','register')">カラーミー登録</button>
        <button class="btn btn-secondary" id="btn-bs-images" onclick="doAction('bs','images-only')">画像のみ</button>
        <button class="btn btn-danger" id="btn-bs-stop" onclick="doAction('bs','stop')" disabled>停止</button>
      </div>
      <div style="font-size:11px;color:#86868b;margin-top:-6px;line-height:1.5" data-component="card-bs-fetch-description">
        <b>商品取得</b>: BullionstarAPIから商品一覧・価格・在庫を取得しシートに保存<br>
        <b>カラーミー登録</b>: A列が「採用」の商品をカラーミーAPIに登録 → <span style="background:#e8e8ed;border-radius:3px;padding:0 4px;font-size:10px">商品仕入れ先一覧</span> に自動同期<br>
        <b>画像のみ</b>: 登録済み商品に画像アップロード
      </div>
      <details class="data-flow">
        <summary>データフロー設定</summary>
        <table class="flow-table">
          <tr><th>ボタン</th><th>方向</th><th>説明</th></tr>
          <tr><td>商品取得</td><td>Bullionstar→シート</td><td>商品一覧・価格・在庫をシートに書き込み</td></tr>
          <tr><td>カラーミー登録</td><td>シート→API</td><td>採用商品を初期登録（1回限り）</td></tr>
          <tr><td>画像のみ</td><td>シート→API</td><td>画像URLをブラウザ経由でアップロード</td></tr>
        </table>
        <div style="font-size:10px;color:#86868b;margin-top:4px">
          初期登録専用。登録後の運用は「カラーミー同期」「価格のみ同期」で管理
        </div>
      </details>

      <div class="sched-row" data-component="card-bs-fetch-schedule">
        <span class="sched-label">定期実行</span>
        <select id="bs-interval" class="interval-select" onchange="changeInterval('bs')">
          <option value="30">30分</option><option value="60">1時間</option>
          <option value="120">2時間</option><option value="180">3時間</option>
          <option value="240">4時間</option><option value="360">6時間</option>
          <option value="480">8時間</option><option value="720">12時間</option>
          <option value="1440">24時間</option>
        </select>
        <label class="toggle-switch">
          <input type="checkbox" id="bs-sched-toggle" onchange="toggleSched('bs', this.checked)">
          <span class="toggle-slider"></span>
        </label>
      </div>

      <div class="log-header" data-component="card-bs-fetch-log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('bs','clear-logs')">リセット</button>
      </div>
      <div id="bs-log" class="log-box" data-component="card-bs-fetch-log"></div>
    </div>

    <!-- ======== APMEX商品取得 ======== -->
    <div class="task-card" data-component="card-ap-fetch">
      <h2 data-component="card-ap-fetch-title">APMEX商品取得 <span class="sheet-badge">APMEX商品ページ一覧</span></h2>

      <div data-component="card-ap-fetch-status">
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="ap-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="ap-last" class="status-value">...</span>
        </div>
      </div>

      <div id="ap-progress" class="progress-wrap" data-component="card-ap-fetch-progress">
        <div id="ap-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="ap-progress-bar" class="progress-bar ap" style="width:0%"></div></div>
        <div id="ap-progress-text" class="progress-text"></div>
      </div>

      <div class="btn-group" data-component="card-ap-fetch-actions">
        <button class="btn btn-purple" id="btn-ap-run" onclick="doAction('ap','run')">商品取得開始</button>
        <button class="btn btn-secondary" id="btn-ap-fill-ai" onclick="doAction('ap','fill-ai')">AI生成</button>
        <button class="btn btn-secondary" id="btn-ap-register" onclick="doAction('ap','register')">カラーミー登録</button>
        <button class="btn btn-secondary" id="btn-ap-images" onclick="doAction('ap','images-only')">画像のみ</button>
        <button class="btn btn-danger" id="btn-ap-stop" onclick="doAction('ap','stop')" disabled>停止</button>
      </div>
      <div style="font-size:11px;color:#86868b;margin-top:-6px;line-height:1.5" data-component="card-ap-fetch-description">
        <b>商品取得</b>: APMEXから商品一覧・価格・在庫をスクレイピングしシートに保存<br>
        <b>カラーミー登録</b>: 採用商品をカラーミーAPIに登録 → <span style="background:#e8e8ed;border-radius:3px;padding:0 4px;font-size:10px">商品仕入れ先一覧</span> に自動同期<br>
        <b>画像のみ</b>: 登録済み商品に画像アップロード
      </div>
      <details class="data-flow">
        <summary>データフロー設定</summary>
        <table class="flow-table">
          <tr><th>ボタン</th><th>方向</th><th>説明</th></tr>
          <tr><td>商品取得</td><td>APMEX→シート</td><td>商品一覧・価格・在庫をシートに書き込み</td></tr>
          <tr><td>AI生成</td><td>AI→シート</td><td>既存商品の説明文をAIで生成</td></tr>
          <tr><td>カラーミー登録</td><td>シート→API</td><td>採用商品を初期登録（1回限り）</td></tr>
          <tr><td>画像のみ</td><td>シート→API</td><td>画像URLをブラウザ経由でアップロード</td></tr>
        </table>
        <div style="font-size:10px;color:#86868b;margin-top:4px">
          初期登録専用。登録後の運用は「カラーミー同期」「価格のみ同期」で管理
        </div>
      </details>

      <div class="sched-row" data-component="card-ap-fetch-schedule">
        <span class="sched-label">定期実行</span>
        <select id="ap-interval" class="interval-select" onchange="changeInterval('ap')">
          <option value="30">30分</option><option value="60">1時間</option>
          <option value="120">2時間</option><option value="180">3時間</option>
          <option value="240">4時間</option><option value="360">6時間</option>
          <option value="480">8時間</option><option value="720">12時間</option>
          <option value="1440">24時間</option>
        </select>
        <label class="toggle-switch">
          <input type="checkbox" id="ap-sched-toggle" onchange="toggleSched('ap', this.checked)">
          <span class="toggle-slider"></span>
        </label>
      </div>

      <div class="log-header" data-component="card-ap-fetch-log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('ap','clear-logs')">リセット</button>
      </div>
      <div id="ap-log" class="log-box" data-component="card-ap-fetch-log"></div>
    </div>

    <!-- ======== 価格のみ同期 ======== -->
    <div class="task-card" data-component="card-po-sync">
      <h2 data-component="card-po-sync-title">価格のみ同期 <span class="sheet-badge">新カラーミー商品管理</span></h2>

      <div data-component="card-po-sync-status">
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="po-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="po-last" class="status-value">...</span>
        </div>
      </div>

      <div id="po-progress" class="progress-wrap" data-component="card-po-sync-progress">
        <div id="po-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="po-progress-bar" class="progress-bar po" style="width:0%"></div></div>
        <div id="po-progress-text" class="progress-text"></div>
      </div>

      <div class="btn-group" data-component="card-po-sync-actions">
        <button class="btn btn-success" id="btn-po-run" onclick="doAction('po','run')">実行</button>
        <button class="btn btn-danger" id="btn-po-stop" onclick="doAction('po','stop')" disabled>停止</button>
      </div>
      <div style="font-size:11px;color:#86868b;margin-top:-6px;line-height:1.5" data-component="card-po-sync-description">
        仕入れ先サイトから最新の価格・在庫をスクレイピング → シートのM-Q列・S列を更新 → 数式再計算 → カラーミーAPIに価格・在庫・表示を同期
      </div>
      <details class="data-flow">
        <summary>データフロー設定</summary>
        <table class="flow-table">
          <tr><th>項目</th><th>方向</th><th>説明</th></tr>
          <tr><td>仕入れ先価格</td><td>仕入れ先→シート</td><td>M-Q列・S列をスクレイピング結果で更新</td></tr>
          <tr><td>販売価格</td><td>シート→API</td><td>数式で再計算されたAE列の値を送信</td></tr>
          <tr><td>在庫</td><td>シート→API</td><td>D列=ON時、仕入れ先在庫に連動</td></tr>
          <tr><td>表示状態</td><td>シート→API</td><td>E列=連動時、仕入れ先在庫に連動</td></tr>
        </table>
        <div style="font-size:10px;color:#86868b;margin-top:4px">
          カラーミーからのダウンロードなし。シートの値をそのままAPIに送信
        </div>
      </details>

      <div class="sched-row" data-component="card-po-sync-schedule">
        <span class="sched-label">定期実行</span>
        <select id="po-interval" class="interval-select" onchange="changeInterval('po')">
          <option value="30">30分</option><option value="60">1時間</option>
          <option value="120">2時間</option><option value="180">3時間</option>
          <option value="240">4時間</option><option value="360">6時間</option>
          <option value="480">8時間</option><option value="720">12時間</option>
          <option value="1440">24時間</option>
        </select>
        <label class="toggle-switch">
          <input type="checkbox" id="po-sched-toggle" onchange="toggleSched('po', this.checked)">
          <span class="toggle-slider"></span>
        </label>
      </div>

      <div class="bench-input-row" data-component="card-po-sync-benchmark">
        <span class="sched-label">ベンチマーク</span>
        <input type="number" id="bench-row" value="3" min="2" max="1000" class="bench-input">
        <span style="font-size:13px;color:#86868b">行目</span>
        <button class="btn btn-secondary btn-sm" id="btn-bench" onclick="runBenchmark()">確認</button>
      </div>

      <div class="log-header" data-component="card-po-sync-log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('po','clear-logs')">リセット</button>
      </div>
      <div id="po-log" class="log-box" data-component="card-po-sync-log"></div>
    </div>

    <!-- ======== 商品説明同期 ======== -->
    <div class="task-card" data-component="card-ds-sync">
      <h2 data-component="card-ds-sync-title">商品説明同期 <span class="sheet-badge">新カラーミー商品管理</span></h2>

      <div data-component="card-ds-sync-status">
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="ds-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="ds-last" class="status-value">...</span>
        </div>
      </div>

      <div id="ds-progress" class="progress-wrap" data-component="card-ds-sync-progress">
        <div id="ds-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="ds-progress-bar" class="progress-bar cm" style="width:0%"></div></div>
        <div id="ds-progress-text" class="progress-text"></div>
      </div>

      <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px" data-component="card-ds-sync-filter">
        <span style="font-size:13px;color:#86868b">対象:</span>
        <select id="ds-filter" style="padding:6px 8px;border:1px solid #e5e5ea;border-radius:8px;font-size:13px">
          <option value="all">全商品</option>
          <option value="24" selected>直近24時間</option>
          <option value="72">直近3日間</option>
          <option value="168">直近1週間</option>
        </select>
      </div>

      <div class="btn-group" data-component="card-ds-sync-actions">
        <button class="btn btn-primary" id="btn-ds-run" onclick="dsRun()">説明を同期</button>
        <button class="btn btn-danger" id="btn-ds-stop" onclick="doAction('ds','stop')" disabled>停止</button>
      </div>
      <div style="font-size:11px;color:#86868b;margin-top:-6px" data-component="card-ds-sync-description">
        カラーミー管理画面の商品説明 → スプレッドシート BA・BB列に反映
      </div>
      <details class="data-flow">
        <summary>データフロー設定</summary>
        <table class="flow-table">
          <tr><th>項目</th><th>列</th><th>方向</th><th>説明</th></tr>
          <tr><td>商品説明</td><td>BA</td><td>API→シート</td><td class="dir-sheet">シートが空の場合のみAPIの値を書き込み</td></tr>
          <tr><td>簡易説明</td><td>BB</td><td>API→シート</td><td class="dir-sheet">シートが空の場合のみAPIの値を書き込み</td></tr>
        </table>
        <div style="font-size:10px;color:#86868b;margin-top:4px">
          シートに既存の説明文がある場合は上書きしない（シート優先）
        </div>
      </details>

      <div class="log-header" data-component="card-ds-sync-log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('ds','clear-logs')">リセット</button>
      </div>
      <div id="ds-log" class="log-box" data-component="card-ds-sync-log"></div>
    </div>

  </div>
</div>

<div id="toast" class="toast" data-component="toast-notification"></div>

<!-- ベンチマークモーダル -->
<div id="bench-modal" class="modal-overlay" data-component="benchmark-modal" onclick="if(event.target===this)closeBenchmark()">
  <div class="modal" data-component="benchmark-modal-content">
    <div class="modal-header" data-component="benchmark-modal-header">
      <h3 id="bench-modal-title">ベンチマーク確認</h3>
      <button class="modal-close" onclick="closeBenchmark()">&times;</button>
    </div>
    <div class="modal-body" id="bench-modal-body" data-component="benchmark-modal-body">
      <div class="modal-loading">読み込み中...</div>
    </div>
  </div>
</div>

<script>
let refreshTimer = null;

async function api(endpoint) {
  const res = await fetch('/api/' + endpoint);
  return res.json();
}

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

function updateLog(el, text) {
  const prev = el.textContent;
  el.textContent = text || '';
  // 内容が更新されたら常に末尾にスクロール
  if (text !== prev) {
    el.scrollTop = el.scrollHeight;
  }
}

async function refresh() {
  try {
    const data = await api('status');

    // --- カラーミー同期 ---
    const cmEl = document.getElementById('cm-status');
    if (data.cm.running) {
      cmEl.textContent = '実行中';
      cmEl.className = 'badge badge-blue';
      document.getElementById('btn-cm-run').disabled = true;
      document.getElementById('btn-cm-full').disabled = true;
      document.getElementById('btn-cm-stop').disabled = false;
    } else {
      cmEl.textContent = '停止中';
      cmEl.className = 'badge badge-yellow';
      document.getElementById('btn-cm-run').disabled = false;
      document.getElementById('btn-cm-full').disabled = false;
      document.getElementById('btn-cm-stop').disabled = true;
    }
    document.getElementById('cm-last').textContent = data.cm.last_summary || 'なし';
    document.getElementById('cm-last').style.color = data.cm.last_success ? '#2e7d32' : (data.cm.last_summary ? '#c62828' : '#1d1d1f');

    updateLog(document.getElementById('cm-log'), data.cm.log);

    const cmProg = document.getElementById('cm-progress');
    if (data.cm.running && data.cm.progress) {
      cmProg.classList.add('active');
      document.getElementById('cm-progress-step').textContent = data.cm.progress.step || '';
      document.getElementById('cm-progress-bar').style.width = (data.cm.progress.percent || 0) + '%';
      document.getElementById('cm-progress-text').textContent = data.cm.progress.detail || '';
    } else {
      cmProg.classList.remove('active');
    }

    // --- ブリオンスター ---
    const bsEl = document.getElementById('bs-status');
    if (data.bs.running) {
      bsEl.textContent = '実行中';
      bsEl.className = 'badge badge-blue';
      document.getElementById('btn-bs-run').disabled = true;
      document.getElementById('btn-bs-register').disabled = true;
      document.getElementById('btn-bs-images').disabled = true;
      document.getElementById('btn-bs-stop').disabled = false;
    } else {
      bsEl.textContent = '停止中';
      bsEl.className = 'badge badge-yellow';
      document.getElementById('btn-bs-run').disabled = false;
      document.getElementById('btn-bs-register').disabled = false;
      document.getElementById('btn-bs-images').disabled = false;
      document.getElementById('btn-bs-stop').disabled = true;
    }
    document.getElementById('bs-last').textContent = data.bs.last_summary || 'なし';
    document.getElementById('bs-last').style.color = data.bs.last_success ? '#2e7d32' : (data.bs.last_summary ? '#c62828' : '#1d1d1f');

    updateLog(document.getElementById('bs-log'), data.bs.log);

    const bsProg = document.getElementById('bs-progress');
    if (data.bs.running && data.bs.progress) {
      bsProg.classList.add('active');
      document.getElementById('bs-progress-step').textContent = data.bs.progress.step || '';
      document.getElementById('bs-progress-bar').style.width = (data.bs.progress.percent || 0) + '%';
      document.getElementById('bs-progress-text').textContent = data.bs.progress.detail || '';
    } else {
      bsProg.classList.remove('active');
    }

    // --- APMEX ---
    const apEl = document.getElementById('ap-status');
    if (data.ap.running) {
      apEl.textContent = '実行中';
      apEl.className = 'badge badge-blue';
      document.getElementById('btn-ap-run').disabled = true;
      document.getElementById('btn-ap-fill-ai').disabled = true;
      document.getElementById('btn-ap-register').disabled = true;
      document.getElementById('btn-ap-images').disabled = true;
      document.getElementById('btn-ap-stop').disabled = false;
    } else {
      apEl.textContent = '停止中';
      apEl.className = 'badge badge-yellow';
      document.getElementById('btn-ap-run').disabled = false;
      document.getElementById('btn-ap-fill-ai').disabled = false;
      document.getElementById('btn-ap-register').disabled = false;
      document.getElementById('btn-ap-images').disabled = false;
      document.getElementById('btn-ap-stop').disabled = true;
    }
    document.getElementById('ap-last').textContent = data.ap.last_summary || 'なし';
    document.getElementById('ap-last').style.color = data.ap.last_success ? '#2e7d32' : (data.ap.last_summary ? '#c62828' : '#1d1d1f');

    updateLog(document.getElementById('ap-log'), data.ap.log);

    const apProg = document.getElementById('ap-progress');
    if (data.ap.running && data.ap.progress) {
      apProg.classList.add('active');
      document.getElementById('ap-progress-step').textContent = data.ap.progress.step || '';
      document.getElementById('ap-progress-bar').style.width = (data.ap.progress.percent || 0) + '%';
      document.getElementById('ap-progress-text').textContent = data.ap.progress.detail || '';
    } else {
      apProg.classList.remove('active');
    }

    // --- 価格のみ同期 ---
    const poEl = document.getElementById('po-status');
    if (data.po.running) {
      poEl.textContent = '実行中';
      poEl.className = 'badge badge-blue';
      document.getElementById('btn-po-run').disabled = true;
      document.getElementById('btn-po-stop').disabled = false;
    } else {
      poEl.textContent = '停止中';
      poEl.className = 'badge badge-yellow';
      document.getElementById('btn-po-run').disabled = false;
      document.getElementById('btn-po-stop').disabled = true;
    }
    document.getElementById('po-last').textContent = data.po.last_summary || 'なし';
    document.getElementById('po-last').style.color = data.po.last_success ? '#2e7d32' : (data.po.last_summary ? '#c62828' : '#1d1d1f');

    updateLog(document.getElementById('po-log'), data.po.log);

    const poProg = document.getElementById('po-progress');
    if (data.po.running && data.po.progress) {
      poProg.classList.add('active');
      document.getElementById('po-progress-step').textContent = data.po.progress.step || '';
      document.getElementById('po-progress-bar').style.width = (data.po.progress.percent || 0) + '%';
      document.getElementById('po-progress-text').textContent = data.po.progress.detail || '';
    } else {
      poProg.classList.remove('active');
    }

    // --- 定期実行 ---
    const setToggle = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
    const setInterval_ = (id, val) => { const el = document.getElementById(id); if (el) el.value = String(val); };
    setToggle('cm-sched-toggle', data.cm_schedule);
    setToggle('bs-sched-toggle', data.bs_schedule);
    setToggle('ap-sched-toggle', data.ap_schedule);
    setToggle('po-sched-toggle', data.po_schedule);
    setInterval_('cm-interval', data.cm_interval);
    setInterval_('bs-interval', data.bs_interval);
    setInterval_('ap-interval', data.ap_interval);
    setInterval_('po-interval', data.po_interval);

    // --- 商品説明同期 ---
    const dsEl = document.getElementById('ds-status');
    if (data.ds) {
      if (data.ds.running) {
        dsEl.textContent = '実行中';
        dsEl.className = 'badge badge-blue';
        document.getElementById('btn-ds-run').disabled = true;
        document.getElementById('btn-ds-stop').disabled = false;
      } else {
        dsEl.textContent = '停止中';
        dsEl.className = 'badge badge-yellow';
        document.getElementById('btn-ds-run').disabled = false;
        document.getElementById('btn-ds-stop').disabled = true;
      }
      document.getElementById('ds-last').textContent = data.ds.last_summary || 'なし';
      document.getElementById('ds-last').style.color = data.ds.last_success ? '#2e7d32' : (data.ds.last_summary ? '#c62828' : '#1d1d1f');
      updateLog(document.getElementById('ds-log'), data.ds.log);

      const dsProg = document.getElementById('ds-progress');
      if (data.ds.running && data.ds.progress) {
        dsProg.classList.add('active');
        document.getElementById('ds-progress-step').textContent = data.ds.progress.step || '';
        document.getElementById('ds-progress-bar').style.width = (data.ds.progress.percent || 0) + '%';
        document.getElementById('ds-progress-text').textContent = data.ds.progress.detail || '';
      } else {
        dsProg.classList.remove('active');
      }
    }

    // --- VPN ---
    if (data.vpn !== undefined) {
      const dot = document.getElementById('vpn-dot');
      const status = document.getElementById('vpn-status');
      const iface = document.getElementById('vpn-iface');
      if (data.vpn) {
        dot.className = 'vpn-dot connected';
        status.textContent = '接続中';
        status.style.color = '#2e7d32';
        iface.textContent = data.vpn_iface ? '(' + data.vpn_iface + ')' : '';
      } else {
        dot.className = 'vpn-dot disconnected';
        status.textContent = '未接続';
        status.style.color = '#ff3b30';
        iface.textContent = '';
      }
      setToggle('vpn-toggle', data.vpn);
    }

    // 何か実行中なら更新頻度を上げる
    const anyRunning = data.cm.running || data.bs.running || data.ap.running || data.po.running || (data.ds && data.ds.running);
    setRefreshRate(anyRunning ? 5000 : 10000);

  } catch(e) { /* ignore */ }
}

function setRefreshRate(ms) {
  if (refreshTimer && refreshTimer._ms === ms) return;
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, ms);
  refreshTimer._ms = ms;
}

async function cmRunWithFields() {
  const checks = document.querySelectorAll('.cm-field:checked');
  const fields = Array.from(checks).map(c => c.value).join(',');
  if (!fields) { showToast('同期項目を1つ以上選択してください', 'error'); return; }
  const allChecks = document.querySelectorAll('.cm-field');
  const suffix = checks.length < allChecks.length ? '?fields=' + encodeURIComponent(fields) : '';
  doAction('cm', 'run-fast' + suffix);
}
async function cmRunFull() {
  const checks = document.querySelectorAll('.cm-field:checked');
  const fields = Array.from(checks).map(c => c.value).join(',');
  if (!fields) { showToast('同期項目を1つ以上選択してください', 'error'); return; }
  const allChecks = document.querySelectorAll('.cm-field');
  const suffix = checks.length < allChecks.length ? '?fields=' + encodeURIComponent(fields) : '';
  doAction('cm', 'run' + suffix);
}

async function doAction(task, action) {
  const labels = {
    'cm-run': 'フルスペック同期を開始しています...',
    'cm-run-fast': 'シート→API同期を開始しています...',
    'cm-stop': '停止しています...',
    'bs-run': '商品取得を開始しています...',
    'bs-register': 'カラーミー登録を開始しています...',
    'bs-images-only': '画像アップロードを開始しています...',
    'bs-stop': '停止しています...',
    'ap-run': 'APMEX商品取得を開始しています...',
    'ap-fill-ai': 'AI生成を開始しています...',
    'ap-register': 'カラーミー登録を開始しています...',
    'ap-images-only': '画像アップロードを開始しています...',
    'ap-stop': '停止しています...',
    'ap-clear-logs': 'ログをリセットしました',
    'ap-sched-enable': 'APMEX定期実行を有効にしました',
    'ap-sched-disable': 'APMEX定期実行を無効にしました',
    'po-run': 'スクレイピング+カラーミー同期を開始しています...',
    'po-stop': '停止しています...',
    'cm-clear-logs': 'ログをリセットしました',
    'bs-clear-logs': 'ログをリセットしました',
    'po-clear-logs': 'ログをリセットしました',
    'ds-run': '商品説明同期を開始しています...',
    'ds-stop': '停止しています...',
    'ds-clear-logs': 'ログをリセットしました',
    'cm-sched-enable': 'カラーミー定期実行を有効にしました',
    'cm-sched-disable': 'カラーミー定期実行を無効にしました',
    'bs-sched-enable': 'BS定期実行を有効にしました',
    'bs-sched-disable': 'BS定期実行を無効にしました',
    'po-sched-enable': '価格のみ定期実行を有効にしました',
    'po-sched-disable': '価格のみ定期実行を無効にしました',
  };
  const key = task + '-' + action.split('?')[0];
  toast(labels[key] || '処理中...');

  // ボタン無効化
  const btnRun = document.getElementById('btn-' + task + '-run');
  const btnFull = document.getElementById('btn-' + task + '-full');
  const btnStop = document.getElementById('btn-' + task + '-stop');
  if (btnRun) btnRun.disabled = true;
  if (btnFull) btnFull.disabled = true;

  await api(task + '/' + action);
  setTimeout(refresh, 1500);
}

function toggleSched(task, enabled) {
  if (enabled) {
    const interval = document.getElementById(task + '-interval').value;
    doAction(task + '-sched', 'enable?interval=' + interval);
  } else {
    doAction(task + '-sched', 'disable');
  }
}

function changeInterval(task) {
  const toggle = document.getElementById(task + '-sched-toggle');
  if (toggle && toggle.checked) {
    const interval = document.getElementById(task + '-interval').value;
    doAction(task + '-sched', 'enable?interval=' + interval);
    toast('インターバルを変更しました');
  }
}

async function toggleVPN(enabled) {
  toast(enabled ? 'VPN接続中...' : 'VPN切断中...');
  document.getElementById('vpn-toggle').disabled = true;
  try {
    const result = await api('vpn/' + (enabled ? 'enable' : 'disable'));
    if (result && !result.ok) {
      toast('エラー: ' + (result.message || '不明なエラー'));
    } else {
      toast(result.message || (enabled ? 'VPN接続しました' : 'VPN切断しました'));
    }
  } catch(e) {
    toast('エラーが発生しました');
  }
  document.getElementById('vpn-toggle').disabled = false;
  setTimeout(refresh, 1000);
}

// 商品説明同期
async function dsRun() {
  const filter = document.getElementById('ds-filter').value;
  toast('商品説明同期を開始しています...');
  document.getElementById('btn-ds-run').disabled = true;
  await api('ds/run?filter=' + filter);
  setTimeout(refresh, 1500);
}

// ベンチマーク
async function runBenchmark() {
  const row = document.getElementById('bench-row').value || 3;
  const modal = document.getElementById('bench-modal');
  const body = document.getElementById('bench-modal-body');
  const title = document.getElementById('bench-modal-title');

  title.textContent = row + '行目 ベンチマーク確認';
  body.innerHTML = '<div class="modal-loading">読み込み中...</div>';
  modal.classList.add('active');
  document.getElementById('btn-bench').disabled = true;

  try {
    const data = await api('po/benchmark?row=' + row);
    body.innerHTML = renderBenchmark(data);
  } catch(e) {
    body.innerHTML = '<div class="modal-loading" style="color:#c62828">エラー: ' + e.message + '</div>';
  }
  document.getElementById('btn-bench').disabled = false;
}

function closeBenchmark() {
  document.getElementById('bench-modal').classList.remove('active');
}

function renderBenchmark(d) {
  if (d.error) return '<div class="diag-result no-data">' + d.error + '</div>';

  let h = '';
  const op = d.operation;
  const sup = d.supplier;
  const pc = d.price_chain;
  const cm = d.colorme;

  // 商品名
  h += '<div style="font-size:15px;font-weight:600;margin-bottom:12px">' + esc(d.name || '(名前なし)') + '</div>';

  // 操作設定
  h += '<div class="diag-section"><div class="diag-section-title">操作設定</div>';
  h += diagRow('A列 同期モード', op.sync_mode, op.sync_mode === '更新' ? 'ok' : 'warn');
  h += diagRow('B列 掲載設定', op.display_setting);
  const puOff = op.price_update.toUpperCase() === 'OFF';
  h += diagRow('C列 価格更新', op.price_update || '(空=ON)', puOff ? 'error' : 'ok');
  h += diagRow('D列 在庫連動', op.stock_sync);
  h += diagRow('E列 表示連動', op.display_sync);
  h += diagRow('F列 同期ステータス', op.sync_status || '(空)');
  h += diagRow('G列 商品ID', String(d.product_id || 0), d.product_id > 0 ? '' : 'error');
  h += '</div>';

  // 仕入れ先
  h += '<div class="diag-section"><div class="diag-section-title">仕入れ先情報</div>';
  h += diagRow('M列 在庫状況', sup.stock, sup.stock.toLowerCase().includes('out') ? 'warn' : 'ok');
  h += diagRow('N列 仕入価格', sup.price + ' ' + sup.currency);
  h += diagRow('O列 前回価格', sup.prev_price || '(空)');
  h += diagRow('P列 変動率', sup.change_rate || '(空)');
  h += '</div>';

  // 価格チェーン
  h += '<div class="diag-section"><div class="diag-section-title">価格計算チェーン</div>';
  h += diagRow('R列 為替種類', pc.exchange_type || '(空)');
  h += diagRow('S列 為替レート', pc.exchange_rate || '(空)');
  h += diagRow('T列 仕入額JPY', pc.purchase_jpy || '(空)');
  h += diagRow('W列 マージン率', pc.margin_rate || '(空)');
  h += diagRow('AA列 合計原価', pc.total_cost || '(空)');
  h += diagRow('AB列 適正価格', fmt(pc.proper_price) + '円', pc.proper_price > 0 ? '' : 'warn');
  h += diagRow('AE列 販売価格', fmt(pc.sales_price) + '円', pc.sales_price > 0 ? 'ok' : 'warn');
  h += diagRow('同期される価格', fmt(pc.final_price) + '円 (' + pc.price_source + ')',
               pc.final_price > 0 ? 'ok' : 'error');
  h += '</div>';

  // 数式
  if (d.formulas && Object.keys(d.formulas).length > 0) {
    h += '<div class="diag-section"><div class="diag-section-title">数式確認</div>';
    for (const [key, val] of Object.entries(d.formulas)) {
      const tag = val.is_formula ? '数式' : '値';
      h += diagRow(key, '[' + tag + '] ' + esc(val.value), val.is_formula ? '' : 'warn');
    }
    h += '</div>';
  }

  // カラーミー
  if (cm && !cm.error) {
    const dsMap = {showing:'掲載する',hidden:'掲載しない',showing_for_members:'会員のみ表示',sale_for_members:'会員のみ購入可'};
    h += '<div class="diag-section"><div class="diag-section-title">カラーミー現在値</div>';
    h += diagRow('販売価格', fmt(cm.sales_price) + '円');
    h += diagRow('定価', fmt(cm.price) + '円');
    h += diagRow('在庫数', String(cm.stocks));
    h += diagRow('表示状態', (dsMap[cm.display_state] || cm.display_state));
    h += '</div>';

    // 比較結果
    if (d.match === true) {
      h += '<div class="diag-result match">価格一致: ' + fmt(pc.final_price) + '円</div>';
    } else if (d.match === false) {
      h += '<div class="diag-result mismatch">価格不一致: シート ' + fmt(pc.final_price) + '円 / カラーミー '
           + fmt(cm.sales_price) + '円（差額: ' + (d.diff >= 0 ? '+' : '') + fmt(d.diff) + '円）</div>';
    }
  } else if (cm && cm.error) {
    h += '<div class="diag-result no-data">カラーミーAPI: ' + esc(cm.error) + '</div>';
  } else {
    h += '<div class="diag-result no-data">商品IDが未設定です</div>';
  }

  return h;
}

function diagRow(key, val, cls) {
  return '<div class="diag-row"><span class="diag-key">' + esc(key) + '</span>'
       + '<span class="diag-val' + (cls ? ' ' + cls : '') + '">' + esc(val) + '</span></div>';
}

function fmt(n) { return n != null ? Number(n).toLocaleString() : '0'; }
function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

// 初期読み込み
refresh();
setRefreshRate(10000);
</script>
</body>
</html>
"""


def _parse_register_log(full: str, latest: str, running: bool) -> tuple:
    """登録/画像アップロードログを解析して (last_summary, last_success) を返す"""
    if 'カラーミー登録 完了' in full or '処理完了' in full:
        m = re.search(r'所要時間: (.+)', full)
        t = m.group(1) if m else _calc_elapsed_from_log(latest)
        # 画像アップロード結果
        img_m = re.search(r'画像アップロード結果: 成功=(\d+)件, 失敗=(\d+)件, スキップ=(\d+)件', full)
        reg_m = re.search(r'カラーミー登録結果: 成功=(\d+)件', full)
        if img_m:
            label = f'画像UP完了 (成功:{img_m.group(1)},失敗:{img_m.group(2)},スキップ:{img_m.group(3)}, {t})'
        elif reg_m:
            label = f'登録完了 (成功:{reg_m.group(1)}件, {t})'
        else:
            label = f'登録完了 ({t})' if t else '登録完了'
        return label, True
    elif 'ERROR' in full:
        t = _calc_elapsed_from_log(latest)
        return (f'登録エラー ({t})' if t else '登録エラー'), False
    elif running:
        return '実行中...', False
    # ログはあるが完了/エラーのどちらでもない（プロセスが異常終了した可能性）
    elif full.strip():
        t = _calc_elapsed_from_log(latest)
        return (f'中断 ({t})' if t else '中断'), False
    return '', False


def _calc_elapsed_from_log(log_file: str) -> str:
    """ログファイル名のタイムスタンプとファイル更新日時から経過時間を算出"""
    try:
        from datetime import datetime
        basename = Path(log_file).stem
        m = re.search(r'(\d{8}_\d{6})$', basename)
        if m:
            start = datetime.strptime(m.group(1), '%Y%m%d_%H%M%S')
            end = datetime.fromtimestamp(os.path.getmtime(log_file))
            total_seconds = int((end - start).total_seconds())
            if total_seconds < 0:
                return ''
            if total_seconds < 60:
                return f'{total_seconds}秒'
            elif total_seconds < 3600:
                mins = total_seconds // 60
                secs = total_seconds % 60
                return f'{mins}分{secs}秒'
            else:
                hours = total_seconds // 3600
                mins = (total_seconds % 3600) // 60
                return f'{hours}時間{mins}分'
    except Exception:
        pass
    return ''


def _is_running(lock_file: Path) -> bool:
    """ロックファイルからプロセスが実行中か確認（子プロセスも検出）"""
    if not lock_file.exists():
        return False
    try:
        pid = int(lock_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # シェルスクリプトは終了したが子プロセス(Python)がまだ動いている場合
        try:
            result = subprocess.run(
                ['pgrep', '-P', str(pid)], capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except Exception:
            pass
        return False
    except (ValueError, PermissionError):
        return False


def _get_latest_log(pattern: str, tail_lines: int = 30):
    """最新のログファイルを取得"""
    files = sorted(glob.glob(str(LOG_DIR / pattern)), reverse=True)
    if not files:
        return None, ""
    try:
        with open(files[0], 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return files[0], ''.join(lines[-tail_lines:])
    except Exception:
        return files[0], ""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._serve_html()
        elif self.path.startswith('/api/'):
            self._handle_api()
        else:
            self.send_error(404)

    def _serve_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(HTML.encode('utf-8'))

    def _respond_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _parse_query_int(self, param_name: str, default: int = 0) -> int:
        """クエリパラメータから整数値を取得"""
        qs = self.path.split('?')
        if len(qs) > 1:
            for param in qs[1].split('&'):
                if param.startswith(param_name + '='):
                    try:
                        return int(param.split('=')[1])
                    except ValueError:
                        pass
        return default

    def _handle_api(self):
        path = self.path.split('/api/')[1].split('?')[0]
        parts = path.split('/')

        if path == 'status':
            self._respond_json(self._get_status())
        elif parts == ['cm', 'run']:
            # フルスペック同期（download_colorme_products.py経由）
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            sync_fields = qs.get('fields', [''])[0]
            self._respond_json(self._cm_run(sync_fields=sync_fields))
        elif parts == ['cm', 'run-fast']:
            # シート→API直接同期（sync_colorme_products.py）
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            sync_fields = qs.get('fields', [''])[0]
            self._respond_json(self._cm_run_fast(sync_fields=sync_fields))
        elif parts == ['cm', 'stop']:
            self._respond_json(self._cm_stop())
        elif parts == ['cm', 'clear-logs']:
            self._respond_json(self._clear_logs('cm'))
        elif parts == ['bs', 'run']:
            self._respond_json(self._bs_run())
        elif parts == ['bs', 'register']:
            self._respond_json(self._bs_register())
        elif parts == ['bs', 'images-only']:
            self._respond_json(self._bs_images_only())
        elif parts == ['bs', 'stop']:
            self._respond_json(self._bs_stop())
        elif parts == ['bs', 'clear-logs']:
            self._respond_json(self._clear_logs('bs'))
        elif parts == ['cm-sched', 'enable']:
            _sched_enable('cm', self._parse_query_int('interval'))
            self._respond_json({'ok': True})
        elif parts == ['cm-sched', 'disable']:
            _sched_disable('cm')
            self._respond_json({'ok': True})
        elif parts == ['ap', 'run']:
            self._respond_json(self._ap_run())
        elif parts == ['ap', 'fill-ai']:
            self._respond_json(self._ap_fill_ai())
        elif parts == ['ap', 'register']:
            self._respond_json(self._ap_register())
        elif parts == ['ap', 'images-only']:
            self._respond_json(self._ap_images_only())
        elif parts == ['ap', 'stop']:
            self._respond_json(self._ap_stop())
        elif parts == ['ap', 'clear-logs']:
            self._respond_json(self._clear_logs('ap'))
        elif parts == ['ap-sched', 'enable']:
            _sched_enable('ap', self._parse_query_int('interval'))
            self._respond_json({'ok': True})
        elif parts == ['ap-sched', 'disable']:
            _sched_disable('ap')
            self._respond_json({'ok': True})
        elif parts == ['po', 'run']:
            self._respond_json(self._po_run())
        elif parts == ['po', 'stop']:
            self._respond_json(self._po_stop())
        elif parts == ['po', 'clear-logs']:
            self._respond_json(self._clear_logs('po'))
        elif parts[0] == 'po' and parts[1] == 'benchmark':
            row = self._parse_query_int('row', 3)
            self._respond_json(self._po_benchmark(row))
        elif parts == ['bs-sched', 'enable']:
            _sched_enable('bs', self._parse_query_int('interval'))
            self._respond_json({'ok': True})
        elif parts == ['bs-sched', 'disable']:
            _sched_disable('bs')
            self._respond_json({'ok': True})
        elif parts == ['po-sched', 'enable']:
            _sched_enable('po', self._parse_query_int('interval'))
            self._respond_json({'ok': True})
        elif parts == ['po-sched', 'disable']:
            _sched_disable('po')
            self._respond_json({'ok': True})
        elif parts[0] == 'ds' and parts[1] == 'run':
            qs = self.path.split('?')
            filt = 'all'
            if len(qs) > 1:
                for param in qs[1].split('&'):
                    if param.startswith('filter='):
                        filt = param.split('=')[1]
            self._respond_json(self._ds_run(filt))
        elif parts == ['ds', 'stop']:
            self._respond_json(self._ds_stop())
        elif parts == ['ds', 'clear-logs']:
            self._respond_json(self._clear_logs('ds'))
        elif parts == ['vpn', 'enable']:
            self._respond_json(_vpn_enable())
        elif parts == ['vpn', 'disable']:
            self._respond_json(_vpn_disable())
        else:
            self.send_error(404)

    # ========================================
    # ステータス取得
    # ========================================

    def _get_status(self):
        # 定期実行
        cm_schedule = _sched_is_enabled('cm')
        bs_schedule = _sched_is_enabled('bs')
        ap_schedule = _sched_is_enabled('ap')
        po_schedule = _sched_is_enabled('po')

        # VPN
        vpn_connected = _vpn_is_connected()
        vpn_iface = _vpn_get_active_iface() if vpn_connected else ''

        return {
            'cm_schedule': cm_schedule,
            'bs_schedule': bs_schedule,
            'ap_schedule': ap_schedule,
            'po_schedule': po_schedule,
            'cm_interval': _sched_get_interval('cm'),
            'bs_interval': _sched_get_interval('bs'),
            'ap_interval': _sched_get_interval('ap'),
            'po_interval': _sched_get_interval('po'),
            'vpn': vpn_connected,
            'vpn_iface': vpn_iface,
            'cm': self._cm_status(),
            'bs': self._bs_status(),
            'ap': self._ap_status(),
            'po': self._po_status(),
            'ds': self._ds_status(),
        }

    # --- カラーミー同期ステータス ---
    def _cm_status(self):
        running = _is_running(CM_LOCK)

        # 詳細ログ: 実行中は現在のステップログ、完了後はサマリー+最終ステップログ
        log_content = self._cm_detailed_log()

        # 前回結果
        log_files = sorted(glob.glob(str(LOG_DIR / "cm-sync-*.log")), reverse=True)
        last_summary = ""
        last_success = False
        if log_files:
            try:
                with open(log_files[0], 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if '同期' in full and '完了' in full:
                    m = re.search(r'合計所要時間: (.+)', full)
                    t = m.group(1) if m else _calc_elapsed_from_log(log_files[0])
                    last_summary = f'完了 ({t})' if t else '完了'
                    last_success = True
                elif 'ERROR' in full:
                    t = _calc_elapsed_from_log(log_files[0])
                    last_summary = f'エラーあり ({t})' if t else 'エラーあり'
                elif running:
                    last_summary = '実行中...'
            except Exception:
                pass

        progress = self._cm_progress() if running else None

        return {
            'running': running,
            'last_summary': last_summary,
            'last_success': last_success,
            'log': log_content,
            'progress': progress,
        }

    def _cm_detailed_log(self) -> str:
        """CM同期の詳細ログを構築"""
        TAIL = 60

        # メインログのヘッダー部分（開始時刻等）
        _, main_log = _get_latest_log("cm-sync-*.log", 8)
        header = main_log.strip()

        # シート→API直接同期の場合: cm-sync-*.log に全ログが入っている
        if 'シート→API同期開始' in header:
            _, full_log = _get_latest_log("cm-sync-*.log", TAIL)
            return full_log.strip() if full_log else header

        # 詳細ログ: sync-all-*.log（新形式）またはstep1/step2（旧形式）
        detail = ""
        sync_all_logs = sorted(glob.glob(str(LOG_DIR / "sync-all-*.log")), reverse=True)
        if sync_all_logs:
            try:
                with open(sync_all_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                if lines:
                    detail = ''.join(lines[-TAIL:])
            except Exception:
                pass

        # 旧形式のログにフォールバック
        if not detail:
            for pattern in ["step2-*.log", "restore-*.log", "step1-*.log"]:
                logs = sorted(glob.glob(str(LOG_DIR / pattern)), reverse=True)
                if logs:
                    try:
                        with open(logs[0], 'r', encoding='utf-8', errors='replace') as f:
                            lines = f.readlines()
                        if lines:
                            detail = ''.join(lines[-TAIL:])
                            break
                    except Exception:
                        pass

        if header and detail:
            return header + "\n\n--- 詳細ログ ---\n" + detail
        elif detail:
            return detail
        elif header:
            return header
        return ""

    def _cm_progress(self):
        """カラーミー同期の進捗"""
        # シート→API直接同期: cm-sync-*.log に [N/M] パターン
        cm_logs = sorted(glob.glob(str(LOG_DIR / "cm-sync-*.log")), reverse=True)
        if cm_logs:
            try:
                with open(cm_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                if 'シート→API同期開始' in content:
                    matches = re.findall(r'\[(\d+)/(\d+)\]', content)
                    if matches:
                        current, total = int(matches[-1][0]), int(matches[-1][1])
                        pct = (current * 100 // total) if total > 0 else 0
                        ok = content.count('更新成功')
                        fail = content.count('更新失敗')
                        detail = f'{current}/{total}件'
                        if ok or fail:
                            detail += f' (成功:{ok} 失敗:{fail})'
                        return {'step': 'シート→API同期', 'percent': pct, 'detail': detail}
                    if '更新対象:' in content:
                        m = re.search(r'更新対象: (\d+)件', content)
                        total = m.group(1) if m else '?'
                        return {'step': 'シート→API同期', 'percent': 3, 'detail': f'更新対象: {total}件'}
                    return {'step': 'シート読み込み中...', 'percent': 1, 'detail': ''}
            except Exception:
                pass

        # フルスペック: sync-all-*.log（1行ずつ即時同期）
        sync_all_logs = sorted(glob.glob(str(LOG_DIR / "sync-all-*.log")), reverse=True)
        if sync_all_logs:
            try:
                with open(sync_all_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                # [N/M] パターンで進捗を取得
                matches = re.findall(r'\[(\d+)/(\d+)\]', content)
                if matches:
                    current, total = int(matches[-1][0]), int(matches[-1][1])
                    pct = (current * 100 // total) if total > 0 else 0
                    cm_ok = content.count('カラーミー同期成功')
                    cm_fail = content.count('カラーミー同期失敗')
                    detail = f'{current}/{total}件'
                    if cm_ok or cm_fail:
                        detail += f' (同期成功:{cm_ok} 失敗:{cm_fail})'
                    return {'step': 'ダウンロード+スクレイピング+同期', 'percent': pct, 'detail': detail}
                # まだ商品ループに入っていない
                if '商品を取得中' in content or '取得した商品数' in content:
                    return {'step': 'カラーミーAPIから商品取得中...', 'percent': 5, 'detail': ''}
                return {'step': '準備中...', 'percent': 0, 'detail': ''}
            except Exception:
                pass

        # 旧形式: step1/step2 ログにフォールバック
        step1_logs = sorted(glob.glob(str(LOG_DIR / "step1-*.log")), reverse=True)
        if step1_logs:
            try:
                with open(step1_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                if 'カラーミー商品ダウンロード完了' in content:
                    step2_logs = sorted(glob.glob(str(LOG_DIR / "step2-*.log")), reverse=True)
                    if step2_logs:
                        with open(step2_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                            s2 = f.read()
                        success = s2.count('→ 更新成功')
                        fail = s2.count('→ 更新失敗')
                        m = re.search(r'更新対象: (\d+)件', s2)
                        total = int(m.group(1)) if m else 0
                        done = success + fail
                        pct = (done * 100 // total) if total > 0 else 0
                        return {'step': 'Step 2: カラーミー同期', 'percent': pct,
                                'detail': f'{done}/{total}件 (成功:{success} 失敗:{fail})'}
                    return {'step': 'Step 1.5: 数式復元', 'percent': 50, 'detail': ''}
                else:
                    success = content.count('取得成功:')
                    fail = content.count('スクレイピング失敗:')
                    done = success + fail
                    m = re.search(r'スクレイピング対象URL: (\d+)件', content)
                    total = int(m.group(1)) if m else 666
                    pct = (done * 100 // total) if total > 0 else 0
                    return {'step': 'Step 1: ダウンロード+スクレイピング', 'percent': pct,
                            'detail': f'{done}/{total}件 (成功:{success} 失敗:{fail})'}
            except Exception:
                pass
        return {'step': '準備中...', 'percent': 0, 'detail': ''}

    # --- ブリオンスターステータス ---
    def _bs_status(self):
        running = _is_running(BS_LOCK) or _is_running(BS_REG_LOCK)

        # 最新ログ（スクレイピングと登録の両方から最新を表示）
        _, log1 = _get_latest_log("bs-scrape-*.log", 60)
        _, log2 = _get_latest_log("bs-register-*.log", 60)
        log_files_scrape = sorted(glob.glob(str(LOG_DIR / "bs-scrape-*.log")), reverse=True)
        log_files_register = sorted(glob.glob(str(LOG_DIR / "bs-register-*.log")), reverse=True)
        latest_scrape = log_files_scrape[0] if log_files_scrape else ""
        latest_register = log_files_register[0] if log_files_register else ""
        # mtimeで新しい方のログを表示
        if latest_register and latest_scrape:
            if os.path.getmtime(latest_register) >= os.path.getmtime(latest_scrape):
                log_content = log2
            else:
                log_content = log1
        elif latest_register:
            log_content = log2
        else:
            log_content = log1

        # 前回結果: スクレイピングと登録の最新ログから判定
        last_summary = ""
        last_success = False
        # 最新のログファイルを特定（mtimeで比較）
        all_log_files = sorted(
            glob.glob(str(LOG_DIR / "bs-scrape-*.log")) + glob.glob(str(LOG_DIR / "bs-register-*.log")),
            key=lambda f: os.path.getmtime(f),
            reverse=True,
        )
        if all_log_files:
            latest = all_log_files[0]
            is_register = 'bs-register-' in latest
            try:
                with open(latest, 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if is_register:
                    last_summary, last_success = _parse_register_log(full, latest, running)
                else:
                    if '処理完了' in full:
                        m = re.search(r'取得件数: (\d+)件', full)
                        count = m.group(1) if m else '?'
                        m2 = re.search(r'所要時間: (.+)', full)
                        t = m2.group(1) if m2 else _calc_elapsed_from_log(latest)
                        last_summary = f'完了 ({count}件, {t})' if t else f'完了 ({count}件)'
                        last_success = True
                    elif 'ERROR' in full or 'エラー' in full:
                        t = _calc_elapsed_from_log(latest)
                        last_summary = f'エラーあり ({t})' if t else 'エラーあり'
                    elif running:
                        last_summary = '実行中...'
            except Exception:
                pass

        progress = self._bs_progress() if running else None

        return {
            'running': running,
            'last_summary': last_summary,
            'last_success': last_success,
            'log': log_content,
            'progress': progress,
        }

    def _bs_progress(self):
        """ブリオンスター商品取得の進捗

        フェーズ:
          0-10%  : API商品一覧取得中
          10%    : スクレイピング準備中
          10-100%: 価格・在庫スクレイピング（メインフェーズ）
        """
        log_files = sorted(glob.glob(str(LOG_DIR / "bs-scrape-*.log")), reverse=True)
        if not log_files:
            return {'step': '準備中...', 'percent': 0, 'detail': ''}
        try:
            with open(log_files[0], 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            # Phase 3: スクレイピング中 [N/M] パターン → 10-100%
            matches = re.findall(r'\[(\d+)/(\d+)\]', content)
            if matches:
                current, total = int(matches[-1][0]), int(matches[-1][1])
                # 10%〜100% にマッピング（API取得フェーズの後）
                if total > 0:
                    pct = 10 + (current * 90 // total)
                else:
                    pct = 10

                # 中間保存のカウント
                saves = re.findall(r'中間保存完了: (\d+)件', content)
                detail = f'{current}/{total}件'
                if saves:
                    detail += f' (保存済:{saves[-1]}件)'
                return {'step': '価格・在庫スクレイピング', 'percent': min(pct, 100), 'detail': detail}

            # Phase 2: API取得完了 → スクレイピング準備中
            if 'スクレイピング対象' in content or '商品ページ取得完了' in content:
                m = re.search(r'スクレイピング対象: (\d+)件', content)
                detail = f'{m.group(1)}件を処理予定' if m else ''
                return {'step': 'スクレイピング準備中...', 'percent': 10, 'detail': detail}

            # Phase 1: API商品一覧取得中 → 0-10%
            m = re.findall(r'累計: (\d+)件', content)
            if m:
                total_m = re.search(r'商品総数: (\d+)件', content)
                total_est = int(total_m.group(1)) if total_m else 1500
                fetched = int(m[-1])
                pct = min(fetched * 10 // total_est, 9)  # 最大9%（10%はPhase2）
                return {'step': 'API商品一覧取得中', 'percent': pct, 'detail': f'累計 {fetched}件'}

            return {'step': '開始中...', 'percent': 2, 'detail': ''}
        except Exception:
            pass
        return {'step': '準備中...', 'percent': 0, 'detail': ''}

    # --- APMEXステータス ---
    def _ap_status(self):
        running = _is_running(AP_LOCK) or _is_running(AP_REG_LOCK)

        # 最新ログ（スクレイピングと登録の両方から最新を表示）
        _, log1 = _get_latest_log("ap-scrape-*.log", 60)
        _, log2 = _get_latest_log("ap-register-*.log", 60)
        log_files_scrape = sorted(glob.glob(str(LOG_DIR / "ap-scrape-*.log")), reverse=True)
        log_files_register = sorted(glob.glob(str(LOG_DIR / "ap-register-*.log")), reverse=True)
        latest_scrape = log_files_scrape[0] if log_files_scrape else ""
        latest_register = log_files_register[0] if log_files_register else ""
        # mtimeで新しい方のログを表示
        if latest_register and latest_scrape:
            if os.path.getmtime(latest_register) >= os.path.getmtime(latest_scrape):
                log_content = log2
            else:
                log_content = log1
        elif latest_register:
            log_content = log2
        else:
            log_content = log1

        # 前回結果: スクレイピングと登録の最新ログから判定
        last_summary = ""
        last_success = False
        all_log_files = sorted(
            glob.glob(str(LOG_DIR / "ap-scrape-*.log")) + glob.glob(str(LOG_DIR / "ap-register-*.log")),
            key=lambda f: os.path.getmtime(f),
            reverse=True,
        )
        if all_log_files:
            latest = all_log_files[0]
            is_register = 'ap-register-' in latest
            try:
                with open(latest, 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if is_register:
                    last_summary, last_success = _parse_register_log(full, latest, running)
                else:
                    if 'APMEX商品取得 完了' in full:
                        m = re.search(r'所要時間: (.+)', full)
                        t = m.group(1) if m else _calc_elapsed_from_log(latest)
                        m2 = re.search(r'新規追加: (\d+)件', full)
                        count = m2.group(1) if m2 else '?'
                        last_summary = f'完了 ({count}件, {t})' if t else f'完了 ({count}件)'
                        last_success = True
                    elif 'ERROR' in full or 'エラー' in full:
                        t = _calc_elapsed_from_log(latest)
                        last_summary = f'エラーあり ({t})' if t else 'エラーあり'
                    elif running:
                        last_summary = '実行中...'
            except Exception:
                pass

        progress = self._ap_progress() if running else None

        return {
            'running': running,
            'last_summary': last_summary,
            'last_success': last_success,
            'log': log_content,
            'progress': progress,
        }

    def _ap_progress(self):
        """APMEX商品取得の進捗"""
        log_files = sorted(glob.glob(str(LOG_DIR / "ap-scrape-*.log")), reverse=True)
        if not log_files:
            return {'step': '準備中...', 'percent': 0, 'detail': ''}
        try:
            with open(log_files[0], 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            # 詳細取得完了
            m_done = re.search(r'詳細取得完了: 成功=(\d+)件, 失敗=(\d+)件', content)
            if m_done:
                ok, fail = m_done.group(1), m_done.group(2)
                return {'step': '詳細取得完了', 'percent': 100,
                        'detail': f'成功:{ok}件 失敗:{fail}件'}

            # 詳細ページ取得中: [N/M] パターン → 50-100%
            # 新形式: [N/M] と価格情報が別の行にあるため、[N/M] のみでマッチ
            if '詳細ページ取得対象' in content:
                matches = re.findall(r'\[(\d+)/(\d+)\]', content)
                if matches:
                    current, total = int(matches[-1][0]), int(matches[-1][1])
                    pct = 50 + (current * 50 // total) if total > 0 else 50
                    ok = content.count('価格=$')
                    fail = content.count('HTTPエラー') + content.count('Cloudflare検出') + content.count('リクエストエラー') + content.count('404 Not Found')
                    detail = f'{current}/{total}件 (成功:{ok}'
                    if fail:
                        detail += f' 失敗:{fail}'
                    detail += ')'
                    return {'step': '詳細ページ取得', 'percent': min(pct, 99), 'detail': detail}

            # スプレッドシート保存中
            if '保存中:' in content or '最終保存' in content:
                m = re.search(r'新規追加: (\d+)件', content)
                if m:
                    return {'step': '保存完了', 'percent': 100, 'detail': f'新規{m.group(1)}件'}
                return {'step': 'スプレッドシート保存中', 'percent': 95, 'detail': ''}

            # AI生成中
            if 'AI判定結果' in content or 'Gemini API' in content:
                return {'step': 'AI生成+保存中', 'percent': 90, 'detail': ''}

            # カテゴリ取得中: ページN パターン → 0-50%
            pages = re.findall(r'ページ(\d+): (\d+)件', content)
            cats_done = len(re.findall(r'→ .+: 累計 \d+件', content))
            cats_total = len(re.findall(r'=== カテゴリ:', content))
            if pages:
                if cats_total > 0:
                    pct = cats_done * 50 // max(cats_total, 1)
                else:
                    pct = 10
                total_products = re.findall(r'商品一覧取得完了: (\d+)件', content)
                if total_products:
                    return {'step': '商品一覧取得完了', 'percent': 48,
                            'detail': f'{total_products[-1]}件'}
                cum = re.findall(r'累計 (\d+)件', content)
                detail = f'{cum[-1]}件取得済み' if cum else ''
                return {'step': 'カテゴリ取得中', 'percent': min(pct, 49), 'detail': detail}

            # 為替レート取得
            if '為替レート' in content:
                return {'step': '為替レート取得中', 'percent': 48, 'detail': ''}

            # 商品一覧取得開始
            if 'カテゴリ:' in content:
                return {'step': '商品一覧取得中', 'percent': 5, 'detail': ''}

            return {'step': '開始中...', 'percent': 2, 'detail': ''}
        except Exception:
            pass
        return {'step': '準備中...', 'percent': 0, 'detail': ''}

    # ========================================
    # カラーミー同期アクション
    # ========================================

    def _cm_run(self, sync_fields: str = ''):
        if _is_running(CM_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        env = _subprocess_env()
        if sync_fields:
            env['SYNC_FIELDS'] = sync_fields
        subprocess.Popen(['bash', str(CM_SCRIPT)], cwd=str(PROJECT_DIR),
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        msg = '同期を開始しました'
        if sync_fields:
            msg += f'（項目: {sync_fields}）'
        return {'ok': True, 'message': msg}

    def _cm_run_fast(self, sync_fields: str = ''):
        """シート→API直接同期（sync_colorme_products.pyを直接実行）"""
        if _is_running(CM_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        env = _subprocess_env()
        LOG_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = LOG_DIR / f"cm-sync-{timestamp}.log"
        lock_file = str(CM_LOCK)
        sync_fields_opt = f' --sync-fields {sync_fields}' if sync_fields else ''
        # ロックファイルの管理とログ書き込みを含むシェルコマンド
        shell_cmd = (
            f'echo $$ > "{lock_file}" && '
            f'trap \'rm -f "{lock_file}"\' EXIT && '
            f'echo "[{timestamp}] シート→API同期開始" > "{log_file}" && '
            f'echo "[{timestamp}] 同期項目: {sync_fields or "全項目"}" >> "{log_file}" && '
            f'"{PYTHON}" -u -m src.sync_colorme_products --verbose{sync_fields_opt} >> "{log_file}" 2>&1'
        )
        subprocess.Popen(
            ['bash', '-c', shell_cmd], cwd=str(PROJECT_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        msg = 'シート→API同期を開始しました'
        if sync_fields:
            msg += f'（項目: {sync_fields}）'
        return {'ok': True, 'message': msg}

    def _cm_stop(self):
        killed = False
        if CM_LOCK.exists():
            try:
                pid = int(CM_LOCK.read_text().strip())
                # プロセスグループごとkill（子プロセスも含む）
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                killed = True
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass
        # フォールバック: 個別プロセスをkill
        for proc_name in ['src.download_colorme_products', 'src.sync_colorme_products', 'src.restore_formulas']:
            subprocess.run(['pkill', '-f', proc_name], capture_output=True)
        try:
            CM_LOCK.unlink(missing_ok=True)
        except Exception:
            pass
        return {'ok': True, 'message': '停止しました'}

    # ========================================
    # ブリオンスター商品取得アクション
    # ========================================

    def _bs_run(self):
        if _is_running(BS_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        subprocess.Popen(['bash', str(BS_SCRIPT)], cwd=str(PROJECT_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {'ok': True, 'message': '商品取得を開始しました'}

    def _bs_register(self):
        if _is_running(BS_LOCK) or _is_running(BS_REG_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        subprocess.Popen(['bash', str(BS_REG_SCRIPT)], cwd=str(PROJECT_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {'ok': True, 'message': 'カラーミー登録を開始しました'}

    def _bs_images_only(self):
        if _is_running(BS_LOCK) or _is_running(BS_REG_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        env = _subprocess_env()
        LOG_DIR.mkdir(exist_ok=True)
        timestamp = subprocess.check_output(['date', '+%Y%m%d_%H%M%S'], text=True).strip()
        log_file = LOG_DIR / f"bs-register-{timestamp}.log"
        with open(log_file, 'w') as f:
            f.write(f"[{timestamp}] BS画像アップロード開始\n")
        with open(log_file, 'a') as f:
            proc = subprocess.Popen(
                [PYTHON, '-m', 'src.register_adopted_products', '--source', 'bs', '--images-only', '--verbose'],
                cwd=str(PROJECT_DIR), env=env, stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        BS_REG_LOCK.write_text(str(proc.pid))
        return {'ok': True, 'message': 'BS画像アップロードを開始しました'}

    def _bs_stop(self):
        for lock in [BS_LOCK, BS_REG_LOCK]:
            if lock.exists():
                try:
                    pid = int(lock.read_text().strip())
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except (ValueError, ProcessLookupError, PermissionError, OSError):
                    pass
        subprocess.run(['pkill', '-f', 'src.bullionstar_products'], capture_output=True)
        subprocess.run(['pkill', '-f', 'src.register_adopted_products'], capture_output=True)
        for lock in [BS_LOCK, BS_REG_LOCK]:
            try:
                lock.unlink(missing_ok=True)
            except Exception:
                pass
        return {'ok': True, 'message': '停止しました'}

    # ========================================
    # APMEX商品取得アクション
    # ========================================

    def _ap_run(self):
        if _is_running(AP_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        subprocess.Popen(['bash', str(AP_SCRIPT)], cwd=str(PROJECT_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {'ok': True, 'message': 'APMEX商品取得を開始しました'}

    def _ap_fill_ai(self):
        """既存行にAI生成データを埋める（Phase2）"""
        if _is_running(AP_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        env = _subprocess_env()
        LOG_DIR.mkdir(exist_ok=True)
        timestamp = subprocess.check_output(['date', '+%Y%m%d_%H%M%S'], text=True).strip()
        log_file = LOG_DIR / f"ap-scrape-{timestamp}.log"
        with open(log_file, 'w') as f:
            f.write(f"[{timestamp}] APMEX AI生成（Phase2）開始\n")
        with open(log_file, 'a') as f:
            proc = subprocess.Popen(
                [PYTHON, '-m', 'src.apmex_products', '--fill-ai', '--verbose'],
                cwd=str(PROJECT_DIR), env=env, stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        AP_LOCK.write_text(str(proc.pid))
        return {'ok': True, 'message': 'AI生成を開始しました'}

    def _ap_register(self):
        if _is_running(AP_LOCK) or _is_running(AP_REG_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        subprocess.Popen(['bash', str(AP_REG_SCRIPT)], cwd=str(PROJECT_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {'ok': True, 'message': 'カラーミー登録を開始しました'}

    def _ap_images_only(self):
        if _is_running(AP_LOCK) or _is_running(AP_REG_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        env = _subprocess_env()
        LOG_DIR.mkdir(exist_ok=True)
        timestamp = subprocess.check_output(['date', '+%Y%m%d_%H%M%S'], text=True).strip()
        log_file = LOG_DIR / f"ap-register-{timestamp}.log"
        with open(log_file, 'w') as f:
            f.write(f"[{timestamp}] AP画像アップロード開始\n")
        with open(log_file, 'a') as f:
            proc = subprocess.Popen(
                [PYTHON, '-m', 'src.register_adopted_products', '--source', 'ap', '--images-only', '--verbose'],
                cwd=str(PROJECT_DIR), env=env, stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        AP_REG_LOCK.write_text(str(proc.pid))
        return {'ok': True, 'message': 'AP画像アップロードを開始しました'}

    def _ap_stop(self):
        for lock in [AP_LOCK, AP_REG_LOCK]:
            if lock.exists():
                try:
                    pid = int(lock.read_text().strip())
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except (ValueError, ProcessLookupError, PermissionError, OSError):
                    pass
        subprocess.run(['pkill', '-f', 'src.apmex_products'], capture_output=True)
        subprocess.run(['pkill', '-f', 'src.register_adopted_products'], capture_output=True)
        for lock in [AP_LOCK, AP_REG_LOCK]:
            try:
                lock.unlink(missing_ok=True)
            except Exception:
                pass
        return {'ok': True, 'message': '停止しました'}

    # ========================================
    # 価格のみ同期ステータス・アクション
    # ========================================

    def _po_status(self):
        running = _is_running(PO_LOCK)

        # 詳細ログ
        log_content = self._po_detailed_log()

        # 前回結果: メインログ（Step1+2）とStep2単独ログの両方から最新を判定
        main_logs = sorted(glob.glob(str(LOG_DIR / "cm-price-only-*.log")), reverse=True)
        step2_logs = sorted(glob.glob(str(LOG_DIR / "price-only-step2-*.log")), reverse=True)
        last_summary = ""
        last_success = False

        # メインログとStep2ログの最新を比較して新しい方を優先
        latest_main = main_logs[0] if main_logs else None
        latest_step2 = step2_logs[0] if step2_logs else None
        if latest_main and latest_step2:
            use_step2 = os.path.getmtime(latest_step2) > os.path.getmtime(latest_main)
        elif latest_step2:
            use_step2 = True
        else:
            use_step2 = False

        if use_step2 and latest_step2:
            try:
                with open(latest_step2, 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if '価格のみ同期完了' in full or '同期完了' in full:
                    m_ok = re.search(r'更新成功: (\d+)件', full)
                    m_fail = re.search(r'更新失敗: (\d+)件', full)
                    count = m_ok.group(1) if m_ok else '?'
                    fail = m_fail.group(1) if m_fail else '0'
                    t = _calc_elapsed_from_log(latest_step2)
                    parts = [f'成功:{count}件']
                    if fail != '0':
                        parts.append(f'失敗:{fail}件')
                    if t:
                        parts.append(t)
                    last_summary = f'Step2完了 ({", ".join(parts)})'
                    last_success = True
                elif 'ERROR' in full:
                    t = _calc_elapsed_from_log(latest_step2)
                    last_summary = f'エラーあり ({t})' if t else 'エラーあり'
                elif running:
                    last_summary = '実行中...'
            except Exception:
                pass
        elif latest_main:
            try:
                with open(latest_main, 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if '軽量版）完了' in full:
                    m = re.search(r'合計所要時間: (.+)', full)
                    t = m.group(1) if m else _calc_elapsed_from_log(latest_main)
                    last_summary = f'完了 ({t})' if t else '完了'
                    last_success = True
                elif 'ERROR' in full:
                    t = _calc_elapsed_from_log(latest_main)
                    last_summary = f'エラーあり ({t})' if t else 'エラーあり'
                elif running:
                    last_summary = '実行中...'
            except Exception:
                pass

        progress = self._po_progress() if running else None

        return {
            'running': running,
            'last_summary': last_summary,
            'last_success': last_success,
            'log': log_content,
            'progress': progress,
        }

    def _po_detailed_log(self) -> str:
        """価格のみ同期の詳細ログを構築"""
        TAIL = 60

        # メインログ
        _, main_log = _get_latest_log("cm-price-only-*.log", 8)
        header = main_log.strip()

        # ステップログ
        step2_logs = sorted(glob.glob(str(LOG_DIR / "price-only-step2-*.log")), reverse=True)
        step1_logs = sorted(glob.glob(str(LOG_DIR / "price-only-step1-*.log")), reverse=True)

        detail = ""
        if step2_logs:
            try:
                with open(step2_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                if lines:
                    detail = ''.join(lines[-TAIL:])
            except Exception:
                pass

        if not detail and step1_logs:
            try:
                with open(step1_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                if lines:
                    detail = ''.join(lines[-TAIL:])
            except Exception:
                pass

        if header and detail:
            return header + "\n\n--- 詳細ログ ---\n" + detail
        elif detail:
            return detail
        elif header:
            return header
        return ""

    def _po_progress(self):
        """価格のみ同期の進捗"""
        step1_logs = sorted(glob.glob(str(LOG_DIR / "price-only-step1-*.log")), reverse=True)
        if not step1_logs:
            return {'step': '準備中...', 'percent': 0, 'detail': ''}
        try:
            with open(step1_logs[0], 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            # 完了チェック
            if '完了 ===' in content:
                scrape_ok = content.count('取得成功:')
                cm_ok = content.count('カラーミー同期成功')
                cm_fail = content.count('カラーミー同期失敗')
                return {'step': '完了', 'percent': 100,
                        'detail': f'スクレイピング:{scrape_ok}件 / CM同期:{cm_ok}件 失敗:{cm_fail}件'}

            # 進行中: [N/M] パターンで進捗計算
            matches = re.findall(r'\[(\d+)/(\d+)\]', content)
            if matches:
                current, total = int(matches[-1][0]), int(matches[-1][1])
                pct = (current * 100 // total) if total > 0 else 0
                scrape_ok = content.count('取得成功:')
                cm_ok = content.count('カラーミー同期成功')
                cm_fail = content.count('カラーミー同期失敗')
                detail = f'{current}/{total}件 (取得:{scrape_ok}'
                if cm_ok or cm_fail:
                    detail += f' CM:{cm_ok}'
                    if cm_fail:
                        detail += f' 失敗:{cm_fail}'
                detail += ')'
                return {'step': 'スクレイピング+同期', 'percent': min(pct, 99),
                        'detail': detail}

            m = re.search(r'スクレイピング対象: (\d+)件', content)
            if m:
                return {'step': 'スクレイピング準備', 'percent': 2,
                        'detail': f'{m.group(1)}件を処理予定'}

            return {'step': '準備中...', 'percent': 1, 'detail': ''}
        except Exception:
            pass
        return {'step': '準備中...', 'percent': 0, 'detail': ''}

    def _po_run(self):
        if _is_running(PO_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        subprocess.Popen(['bash', str(PO_SCRIPT)], cwd=str(PROJECT_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return {'ok': True, 'message': 'スクレイピング+カラーミー同期を開始しました'}

    def _po_stop(self):
        if PO_LOCK.exists():
            try:
                pid = int(PO_LOCK.read_text().strip())
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass
        for proc_name in ['src.fetch_supplier_prices', 'src.sync_colorme_products']:
            subprocess.run(['pkill', '-f', proc_name], capture_output=True)
        try:
            PO_LOCK.unlink(missing_ok=True)
        except Exception:
            pass
        return {'ok': True, 'message': '停止しました'}

    # ========================================
    # 商品説明同期ステータス・アクション
    # ========================================

    def _ds_status(self):
        running = _is_running(DS_LOCK)

        # ログ
        _, log_content = _get_latest_log("desc-sync-*.log", 40)

        # 前回結果
        last_summary = ""
        last_success = False
        log_files = sorted(glob.glob(str(LOG_DIR / "desc-sync-*.log")), reverse=True)
        if log_files:
            try:
                with open(log_files[0], 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if '完了:' in full or '更新完了' in full:
                    t = _calc_elapsed_from_log(log_files[0])
                    m = re.search(r'完了: (\d+)件更新', full)
                    if m:
                        count_str = f'{m.group(1)}件更新'
                    else:
                        m2 = re.search(r'(\d+)件の商品説明を反映', full)
                        count_str = f'{m2.group(1)}件' if m2 else None
                    if count_str and t:
                        last_summary = f'完了 ({count_str}, {t})'
                    elif count_str:
                        last_summary = f'完了 ({count_str})'
                    elif t:
                        last_summary = f'完了 ({t})'
                    else:
                        last_summary = '完了'
                    last_success = True
                elif '対象商品がありません' in full:
                    t = _calc_elapsed_from_log(log_files[0])
                    last_summary = f'対象なし (0件, {t})' if t else '対象なし (0件)'
                    last_success = True
                elif 'ERROR' in full or 'エラー' in full:
                    t = _calc_elapsed_from_log(log_files[0])
                    last_summary = f'エラーあり ({t})' if t else 'エラーあり'
                elif running:
                    last_summary = '実行中...'
            except Exception:
                pass

        progress = None
        if running and log_content:
            progress = {'step': '同期中...', 'percent': 50, 'detail': ''}
            m = re.search(r'全商品数: (\d+)件', log_content)
            if m:
                progress['detail'] = f'全{m.group(1)}件から対象を抽出中'
            m2 = re.search(r'対象商品数: (\d+)件', log_content)
            if m2:
                progress['step'] = 'スプレッドシート反映中'
                progress['percent'] = 70
                progress['detail'] = f'{m2.group(1)}件の説明を反映中'
            if '更新完了' in log_content or '完了:' in log_content:
                progress['step'] = '完了'
                progress['percent'] = 100

        return {
            'running': running,
            'last_summary': last_summary,
            'last_success': last_success,
            'log': log_content,
            'progress': progress,
        }

    def _ds_run(self, filter_val: str = 'all'):
        if _is_running(DS_LOCK):
            return {'ok': False, 'message': '既に実行中です'}

        env = _subprocess_env()
        LOG_DIR.mkdir(exist_ok=True)
        timestamp = subprocess.check_output(['date', '+%Y%m%d_%H%M%S'], text=True).strip()
        log_file = LOG_DIR / f"desc-sync-{timestamp}.log"

        # フィルター引数を構築
        if filter_val == 'all':
            filter_args = ['--all']
        else:
            filter_args = ['--hours', filter_val]

        with open(log_file, 'w') as f:
            f.write(f"[{timestamp}] 商品説明同期 開始 (フィルター: {filter_val})\n")
        with open(log_file, 'a') as f:
            proc = subprocess.Popen(
                [PYTHON, '-m', 'src.download_colorme_descriptions'] + filter_args,
                cwd=str(PROJECT_DIR), env=env, stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        DS_LOCK.write_text(str(proc.pid))
        return {'ok': True, 'message': '商品説明同期を開始しました'}

    def _ds_stop(self):
        if DS_LOCK.exists():
            try:
                pid = int(DS_LOCK.read_text().strip())
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass
        subprocess.run(['pkill', '-f', 'src.download_colorme_descriptions'], capture_output=True)
        try:
            DS_LOCK.unlink(missing_ok=True)
        except Exception:
            pass
        return {'ok': True, 'message': '停止しました'}

    def _po_benchmark(self, row: int = 3):
        """指定行のベンチマーク確認（check_row3.pyをサブプロセスで実行）"""
        env = _subprocess_env()
        try:
            result = subprocess.run(
                [PYTHON, str(BENCHMARK_SCRIPT), '--row', str(row), '--json'],
                capture_output=True, text=True, timeout=30, cwd=str(PROJECT_DIR), env=env,
            )
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout.strip())
            else:
                return {'error': result.stderr.strip() or f'終了コード: {result.returncode}'}
        except subprocess.TimeoutExpired:
            return {'error': 'タイムアウト（30秒）'}
        except Exception as e:
            return {'error': str(e)}

    # ========================================
    # ログリセット
    # ========================================

    def _clear_logs(self, task: str):
        """指定タスクのログファイルを全削除"""
        patterns = {
            'cm': ['cm-sync-*.log', 'sync-all-*.log', 'step1-*.log', 'step2-*.log', 'restore-*.log'],
            'bs': ['bs-scrape-*.log', 'bs-register-*.log'],
            'ap': ['ap-scrape-*.log', 'ap-register-*.log'],
            'po': ['cm-price-only-*.log', 'price-only-step1-*.log', 'price-only-step2-*.log'],
            'ds': ['desc-sync-*.log'],
        }
        deleted = 0
        for pattern in patterns.get(task, []):
            for f in glob.glob(str(LOG_DIR / pattern)):
                try:
                    os.remove(f)
                    deleted += 1
                except Exception:
                    pass
        return {'ok': True, 'message': f'{deleted}件のログを削除しました'}


def main():
    os.chdir(PROJECT_DIR)
    LOG_DIR.mkdir(exist_ok=True)

    server = http.server.HTTPServer(('127.0.0.1', PORT), DashboardHandler)
    print(f"コイン価格管理ダッシュボード: http://localhost:{PORT}")
    print("終了するには Ctrl+C を押してください")

    import webbrowser
    webbrowser.open(f'http://localhost:{PORT}')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nダッシュボードを終了します")
        server.server_close()


if __name__ == '__main__':
    main()
