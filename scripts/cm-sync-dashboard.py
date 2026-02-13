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

PYTHON = os.environ.get("PYTHON_PATH") or subprocess.check_output(["which", "python3"], text=True).strip()


def _subprocess_env() -> dict:
    """サブプロセス用の環境変数を構築（.envの値を含む）"""
    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    env.setdefault('GOOGLE_APPLICATION_CREDENTIALS',
                   str(Path.home() / '.config' / 'gcloud' / 'application_default_credentials.json'))
    return env

# カラーミー同期
CM_SCRIPT = PROJECT_DIR / "scripts" / "cm-sync-prices.sh"
CM_LOCK = Path("/tmp/cm-sync-prices.lock")
CM_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.coin-price-checker.cm-sync.plist"

# ブリオンスター商品取得
BS_SCRIPT = PROJECT_DIR / "scripts" / "bs-scrape.sh"
BS_LOCK = Path("/tmp/bs-scrape.lock")
BS_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.coin-price-checker.bs-scrape.plist"

# 価格のみ同期
PO_SCRIPT = PROJECT_DIR / "scripts" / "cm-price-only-sync.sh"
PO_LOCK = Path("/tmp/cm-price-only-sync.lock")
PO_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.coin-price-checker.price-only.plist"

HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>コイン価格管理</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, 'Hiragino Sans', sans-serif; background: #f5f5f7; color: #1d1d1f; }
  .container { max-width: 960px; margin: 40px auto; padding: 0 20px; }
  h1 { font-size: 24px; font-weight: 600; margin-bottom: 24px; }

  .tasks { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
  @media (max-width: 1080px) { .tasks { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 720px) { .tasks { grid-template-columns: 1fr; } }

  .task-card { background: #fff; border-radius: 12px; padding: 20px;
               box-shadow: 0 1px 3px rgba(0,0,0,0.08); display: flex; flex-direction: column; gap: 14px; }
  .task-card h2 { font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 8px; }

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
  .btn-sm { flex: 0; min-width: 50px; padding: 5px 10px; font-size: 12px; }

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
<div class="container">
  <h1>コイン価格管理</h1>

  <div class="tasks">

    <!-- ======== カラーミー同期 ======== -->
    <div class="task-card">
      <h2>カラーミー同期</h2>

      <div>
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="cm-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="cm-last" class="status-value">...</span>
        </div>
      </div>

      <div id="cm-progress" class="progress-wrap">
        <div id="cm-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="cm-progress-bar" class="progress-bar cm" style="width:0%"></div></div>
        <div id="cm-progress-text" class="progress-text"></div>
      </div>

      <div class="btn-group">
        <button class="btn btn-primary" id="btn-cm-run" onclick="doAction('cm','run')">フルスペック同期</button>
        <button class="btn btn-danger" id="btn-cm-stop" onclick="doAction('cm','stop')" disabled>停止</button>
      </div>
      <div style="font-size:11px;color:#86868b;margin-top:-6px">
        1行ずつ: ダウンロード → スクレイピング → 数式復元 → カラーミーAPI同期
      </div>

      <div class="sched-row">
        <span class="sched-label">定期実行（4時間ごと）</span>
        <span id="cm-sched-status" class="badge badge-gray">...</span>
        <button class="btn btn-success btn-sm" onclick="doAction('cm-sched','enable')">ON</button>
        <button class="btn btn-secondary btn-sm" onclick="doAction('cm-sched','disable')">OFF</button>
      </div>

      <div class="log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('cm','clear-logs')">リセット</button>
      </div>
      <div id="cm-log" class="log-box"></div>
    </div>

    <!-- ======== ブリオンスター商品取得 ======== -->
    <div class="task-card">
      <h2>ブリオンスター商品取得</h2>

      <div>
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="bs-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="bs-last" class="status-value">...</span>
        </div>
      </div>

      <div id="bs-progress" class="progress-wrap">
        <div id="bs-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="bs-progress-bar" class="progress-bar bs" style="width:0%"></div></div>
        <div id="bs-progress-text" class="progress-text"></div>
      </div>

      <div class="btn-group">
        <button class="btn btn-orange" id="btn-bs-run" onclick="doAction('bs','run')">商品取得開始</button>
        <button class="btn btn-danger" id="btn-bs-stop" onclick="doAction('bs','stop')" disabled>停止</button>
      </div>

      <div class="sched-row">
        <span class="sched-label">定期実行（6時間ごと）</span>
        <span id="bs-sched-status" class="badge badge-gray">...</span>
        <button class="btn btn-success btn-sm" onclick="doAction('bs-sched','enable')">ON</button>
        <button class="btn btn-secondary btn-sm" onclick="doAction('bs-sched','disable')">OFF</button>
      </div>

      <div class="log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('bs','clear-logs')">リセット</button>
      </div>
      <div id="bs-log" class="log-box"></div>
    </div>

    <!-- ======== 価格のみ同期 ======== -->
    <div class="task-card">
      <h2>価格のみ同期</h2>

      <div>
        <div class="status-row">
          <span class="status-label">状態</span>
          <span id="po-status" class="badge badge-gray">...</span>
        </div>
        <div class="status-row">
          <span class="status-label">前回</span>
          <span id="po-last" class="status-value">...</span>
        </div>
      </div>

      <div id="po-progress" class="progress-wrap">
        <div id="po-progress-step" class="progress-step"></div>
        <div class="progress-bar-bg"><div id="po-progress-bar" class="progress-bar po" style="width:0%"></div></div>
        <div id="po-progress-text" class="progress-text"></div>
      </div>

      <div class="btn-group">
        <button class="btn btn-success" id="btn-po-run" onclick="doAction('po','run')">Step1+2 全実行</button>
        <button class="btn btn-primary" id="btn-po-sync" onclick="doAction('po','sync-only')">Step2のみ</button>
        <button class="btn btn-danger" id="btn-po-stop" onclick="doAction('po','stop')" disabled>停止</button>
      </div>
      <div style="font-size:11px;color:#86868b;margin-top:-6px">
        Step1: 仕入れ先スクレイピング → Step2: カラーミーAPI同期
      </div>

      <div class="sched-row">
        <span class="sched-label">定期実行（4時間ごと）</span>
        <span id="po-sched-status" class="badge badge-gray">...</span>
        <button class="btn btn-success btn-sm" onclick="doAction('po-sched','enable')">ON</button>
        <button class="btn btn-secondary btn-sm" onclick="doAction('po-sched','disable')">OFF</button>
      </div>

      <div class="bench-input-row">
        <span class="sched-label">ベンチマーク</span>
        <input type="number" id="bench-row" value="3" min="2" max="1000" class="bench-input">
        <span style="font-size:13px;color:#86868b">行目</span>
        <button class="btn btn-secondary btn-sm" id="btn-bench" onclick="runBenchmark()">確認</button>
      </div>

      <div class="log-header">
        <span>ログ</span>
        <button class="btn-text" onclick="doAction('po','clear-logs')">リセット</button>
      </div>
      <div id="po-log" class="log-box"></div>
    </div>

  </div>
</div>

<div id="toast" class="toast"></div>

<!-- ベンチマークモーダル -->
<div id="bench-modal" class="modal-overlay" onclick="if(event.target===this)closeBenchmark()">
  <div class="modal">
    <div class="modal-header">
      <h3 id="bench-modal-title">ベンチマーク確認</h3>
      <button class="modal-close" onclick="closeBenchmark()">&times;</button>
    </div>
    <div class="modal-body" id="bench-modal-body">
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
      document.getElementById('btn-cm-stop').disabled = false;
    } else {
      cmEl.textContent = '停止中';
      cmEl.className = 'badge badge-yellow';
      document.getElementById('btn-cm-run').disabled = false;
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
      document.getElementById('btn-bs-stop').disabled = false;
    } else {
      bsEl.textContent = '停止中';
      bsEl.className = 'badge badge-yellow';
      document.getElementById('btn-bs-run').disabled = false;
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

    // --- 価格のみ同期 ---
    const poEl = document.getElementById('po-status');
    if (data.po.running) {
      poEl.textContent = '実行中';
      poEl.className = 'badge badge-blue';
      document.getElementById('btn-po-run').disabled = true;
      document.getElementById('btn-po-sync').disabled = true;
      document.getElementById('btn-po-stop').disabled = false;
    } else {
      poEl.textContent = '停止中';
      poEl.className = 'badge badge-yellow';
      document.getElementById('btn-po-run').disabled = false;
      document.getElementById('btn-po-sync').disabled = false;
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
    const cmSchedEl = document.getElementById('cm-sched-status');
    if (data.cm_schedule) {
      cmSchedEl.textContent = '有効';
      cmSchedEl.className = 'badge badge-green';
    } else {
      cmSchedEl.textContent = '無効';
      cmSchedEl.className = 'badge badge-red';
    }
    const bsSchedEl = document.getElementById('bs-sched-status');
    if (data.bs_schedule) {
      bsSchedEl.textContent = '有効';
      bsSchedEl.className = 'badge badge-green';
    } else {
      bsSchedEl.textContent = '無効';
      bsSchedEl.className = 'badge badge-red';
    }
    const poSchedEl = document.getElementById('po-sched-status');
    if (data.po_schedule) {
      poSchedEl.textContent = '有効';
      poSchedEl.className = 'badge badge-green';
    } else {
      poSchedEl.textContent = '無効';
      poSchedEl.className = 'badge badge-red';
    }

    // 何か実行中なら更新頻度を上げる
    const anyRunning = data.cm.running || data.bs.running || data.po.running;
    setRefreshRate(anyRunning ? 5000 : 10000);

  } catch(e) { /* ignore */ }
}

function setRefreshRate(ms) {
  if (refreshTimer && refreshTimer._ms === ms) return;
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, ms);
  refreshTimer._ms = ms;
}

async function doAction(task, action) {
  const labels = {
    'cm-run': '同期を開始しています...',
    'cm-stop': '停止しています...',
    'bs-run': '商品取得を開始しています...',
    'bs-stop': '停止しています...',
    'po-run': 'スクレイピング+カラーミー同期を開始しています...',
    'po-sync-only': 'カラーミー同期のみ（Step2）を開始しています...',
    'po-stop': '停止しています...',
    'cm-clear-logs': 'ログをリセットしました',
    'bs-clear-logs': 'ログをリセットしました',
    'po-clear-logs': 'ログをリセットしました',
    'cm-sched-enable': 'カラーミー定期実行を有効にしました',
    'cm-sched-disable': 'カラーミー定期実行を無効にしました',
    'bs-sched-enable': 'BS定期実行を有効にしました',
    'bs-sched-disable': 'BS定期実行を無効にしました',
    'po-sched-enable': '価格のみ定期実行を有効にしました',
    'po-sched-disable': '価格のみ定期実行を無効にしました',
  };
  const key = task + '-' + action;
  toast(labels[key] || '処理中...');

  // ボタン無効化
  const btnRun = document.getElementById('btn-' + task + '-run');
  const btnStop = document.getElementById('btn-' + task + '-stop');
  if (btnRun) btnRun.disabled = true;

  await api(task + '/' + action);
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


def _is_running(lock_file: Path) -> bool:
    """ロックファイルからプロセスが実行中か確認"""
    if not lock_file.exists():
        return False
    try:
        pid = int(lock_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
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

    def _handle_api(self):
        path = self.path.split('/api/')[1].split('?')[0]
        parts = path.split('/')

        if path == 'status':
            self._respond_json(self._get_status())
        elif parts == ['cm', 'run']:
            self._respond_json(self._cm_run())
        elif parts == ['cm', 'stop']:
            self._respond_json(self._cm_stop())
        elif parts == ['cm', 'clear-logs']:
            self._respond_json(self._clear_logs('cm'))
        elif parts == ['bs', 'run']:
            self._respond_json(self._bs_run())
        elif parts == ['bs', 'stop']:
            self._respond_json(self._bs_stop())
        elif parts == ['bs', 'clear-logs']:
            self._respond_json(self._clear_logs('bs'))
        elif parts == ['cm-sched', 'enable']:
            subprocess.run(['launchctl', 'load', str(CM_PLIST)], capture_output=True)
            self._respond_json({'ok': True})
        elif parts == ['cm-sched', 'disable']:
            subprocess.run(['launchctl', 'unload', str(CM_PLIST)], capture_output=True)
            self._respond_json({'ok': True})
        elif parts == ['po', 'run']:
            self._respond_json(self._po_run())
        elif parts == ['po', 'sync-only']:
            self._respond_json(self._po_sync_only())
        elif parts == ['po', 'stop']:
            self._respond_json(self._po_stop())
        elif parts == ['po', 'clear-logs']:
            self._respond_json(self._clear_logs('po'))
        elif parts[0] == 'po' and parts[1] == 'benchmark':
            row = 3
            qs = self.path.split('?')
            if len(qs) > 1:
                for param in qs[1].split('&'):
                    if param.startswith('row='):
                        try:
                            row = int(param.split('=')[1])
                        except ValueError:
                            pass
            self._respond_json(self._po_benchmark(row))
        elif parts == ['bs-sched', 'enable']:
            subprocess.run(['launchctl', 'load', str(BS_PLIST)], capture_output=True)
            self._respond_json({'ok': True})
        elif parts == ['bs-sched', 'disable']:
            subprocess.run(['launchctl', 'unload', str(BS_PLIST)], capture_output=True)
            self._respond_json({'ok': True})
        elif parts == ['po-sched', 'enable']:
            subprocess.run(['launchctl', 'load', str(PO_PLIST)], capture_output=True)
            self._respond_json({'ok': True})
        elif parts == ['po-sched', 'disable']:
            subprocess.run(['launchctl', 'unload', str(PO_PLIST)], capture_output=True)
            self._respond_json({'ok': True})
        else:
            self.send_error(404)

    # ========================================
    # ステータス取得
    # ========================================

    def _get_status(self):
        # 定期実行
        try:
            out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True)
            cm_schedule = 'com.coin-price-checker.cm-sync' in out.stdout
            bs_schedule = 'com.coin-price-checker.bs-scrape' in out.stdout
            po_schedule = 'com.coin-price-checker.price-only' in out.stdout
        except Exception:
            cm_schedule = False
            bs_schedule = False
            po_schedule = False

        return {
            'cm_schedule': cm_schedule,
            'bs_schedule': bs_schedule,
            'po_schedule': po_schedule,
            'cm': self._cm_status(),
            'bs': self._bs_status(),
            'po': self._po_status(),
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
                    t = m.group(1) if m else ''
                    last_summary = f'完了 ({t})' if t else '完了'
                    last_success = True
                elif 'ERROR' in full:
                    last_summary = 'エラーあり'
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
        # 新形式: sync-all-*.log（1行ずつ即時同期）
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
        running = _is_running(BS_LOCK)

        # 最新ログ
        _, log_content = _get_latest_log("bs-scrape-*.log", 60)

        # 前回結果
        log_files = sorted(glob.glob(str(LOG_DIR / "bs-scrape-*.log")), reverse=True)
        last_summary = ""
        last_success = False
        if log_files:
            try:
                with open(log_files[0], 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if '処理完了' in full:
                    m = re.search(r'取得件数: (\d+)件', full)
                    count = m.group(1) if m else '?'
                    m2 = re.search(r'所要時間: (.+)', full)
                    t = m2.group(1) if m2 else ''
                    last_summary = f'完了 ({count}件, {t})' if t else f'完了 ({count}件)'
                    last_success = True
                elif 'ERROR' in full or 'エラー' in full:
                    last_summary = 'エラーあり'
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

    # ========================================
    # カラーミー同期アクション
    # ========================================

    def _cm_run(self):
        if _is_running(CM_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        subprocess.Popen(['bash', str(CM_SCRIPT)], cwd=str(PROJECT_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'ok': True, 'message': '同期を開始しました'}

    def _cm_stop(self):
        if CM_LOCK.exists():
            try:
                pid = int(CM_LOCK.read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
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
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'ok': True, 'message': '商品取得を開始しました'}

    def _bs_stop(self):
        if BS_LOCK.exists():
            try:
                pid = int(BS_LOCK.read_text().strip())
                # bash の子プロセス（python）も停止するため、プロセスグループごと停止
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass
        subprocess.run(['pkill', '-f', 'src.bullionstar_products'], capture_output=True)
        try:
            BS_LOCK.unlink(missing_ok=True)
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

        # 前回結果
        log_files = sorted(glob.glob(str(LOG_DIR / "cm-price-only-*.log")), reverse=True)
        last_summary = ""
        last_success = False
        if log_files:
            try:
                with open(log_files[0], 'r', encoding='utf-8', errors='replace') as f:
                    full = f.read()
                if '軽量版）完了' in full:
                    m = re.search(r'合計所要時間: (.+)', full)
                    t = m.group(1) if m else ''
                    last_summary = f'完了 ({t})' if t else '完了'
                    last_success = True
                elif 'ERROR' in full:
                    last_summary = 'エラーあり'
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
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'ok': True, 'message': 'スクレイピング+カラーミー同期を開始しました'}

    def _po_sync_only(self):
        """Step2のみ: シートの現在値でカラーミーに同期"""
        if _is_running(PO_LOCK):
            return {'ok': False, 'message': '既に実行中です'}
        env = _subprocess_env()
        LOG_DIR.mkdir(exist_ok=True)
        timestamp = subprocess.check_output(['date', '+%Y%m%d_%H%M%S'], text=True).strip()
        log_file = LOG_DIR / f"price-only-step2-{timestamp}.log"
        with open(log_file, 'w') as f:
            subprocess.Popen(
                [PYTHON, '-m', 'src.sync_colorme_products', '--price-only', '--verbose'],
                cwd=str(PROJECT_DIR), env=env, stdout=f, stderr=subprocess.STDOUT,
            )
        return {'ok': True, 'message': 'カラーミー同期のみ（Step2）を開始しました'}

    def _po_stop(self):
        if PO_LOCK.exists():
            try:
                pid = int(PO_LOCK.read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        for proc_name in ['src.fetch_supplier_prices', 'src.sync_colorme_products']:
            subprocess.run(['pkill', '-f', proc_name], capture_output=True)
        try:
            PO_LOCK.unlink(missing_ok=True)
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
            'bs': ['bs-scrape-*.log'],
            'po': ['cm-price-only-*.log', 'price-only-step1-*.log', 'price-only-step2-*.log'],
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
