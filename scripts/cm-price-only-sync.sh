#!/bin/bash
# ================================================================
# カラーミー価格同期スクリプト（軽量版）
#
# 処理フロー:
#   1. 商品仕入れ先一覧同期 (BS/APMEXマスタ→仕入れ先一覧)
#      → CMシートのVLOOKUP数式で自動的にAE値が再計算される
#   2. カラーミー同期 (シートAE→カラーミー、価格・在庫・表示)
#   3. 競合価格取得
#
# 前提:
#   BS/APMEXマスタは別cron (bs-scrape / ap-scrape 6時間ごと) で最新化されている。
#   本スクリプトは「マスタ→カラーミー」の伝達のみを担当し、スクレイピングは行わない。
#
# 使用方法:
#   手動実行: bash scripts/cm-price-only-sync.sh
#   launchd:  自動実行（8時間ごと）
# ================================================================

set -euo pipefail

# === 設定 ===
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# .env から環境変数を読み込み
if [ -f "${PROJECT_DIR}/.env" ]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

PYTHON="${PYTHON_PATH:-$(which python3)}"
LOG_DIR="${PROJECT_DIR}/logs"
LOCK_FILE="/tmp/cm-price-only-sync.lock"

# Google ADC（ローカル認証）
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"

# Python出力バッファリングを無効化（リアルタイムでログに反映するため）
export PYTHONUNBUFFERED=1

# === ログ設定 ===
mkdir -p "${LOG_DIR}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="${LOG_DIR}/cm-price-only-${TIMESTAMP}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

# === 排他制御（二重実行防止）===
if [ -f "${LOCK_FILE}" ]; then
    LOCK_PID=$(cat "${LOCK_FILE}" 2>/dev/null)
    if kill -0 "${LOCK_PID}" 2>/dev/null; then
        log "ERROR: 別のプロセスが実行中です (PID: ${LOCK_PID})"
        exit 1
    else
        log "WARNING: 古いロックファイルを削除します (PID: ${LOCK_PID} は存在しない)"
        rm -f "${LOCK_FILE}"
    fi
fi
echo $$ > "${LOCK_FILE}"
trap 'rm -f "${LOCK_FILE}"' EXIT

# === 実行開始 ===
cd "${PROJECT_DIR}"
log "=========================================="
log "カラーミー価格同期（軽量版）開始"
log "=========================================="
log "※BS/APMEXスクレイピングは別cronで実行済みを前提"

OVERALL_START=$(date +%s)

# --- Step 1: 商品仕入れ先一覧同期 (BS/APMEXマスタ → 仕入れ先一覧) ---
# CMシートのM-S列はVLOOKUP数式で仕入れ先一覧を参照している。
# 仕入れ先一覧が更新されればシートAE値が自動で再計算される。
log ""
log "[1/3] 商品仕入れ先一覧同期（BS/APMEXマスタ → 仕入れ先一覧）"
log "-------------------------------------------"

SUPPLIER_SYNC_LOG="${LOG_DIR}/price-only-step1-${TIMESTAMP}.log"
STEP_START=$(date +%s)
if "${PYTHON}" -m src.sync_supplier_list --source all > "${SUPPLIER_SYNC_LOG}" 2>&1; then
    STEP_END=$(date +%s)
    ELAPSED=$(( STEP_END - STEP_START ))
    log "仕入れ先一覧同期完了 (所要時間: $((ELAPSED / 60))分$((ELAPSED % 60))秒)"
    grep -E "(新規追加|価格更新|登録済.*商品数)" "${SUPPLIER_SYNC_LOG}" | while read -r line; do
        log "  ${line##*- }"
    done
else
    EXIT_CODE=$?
    log "WARNING: 仕入れ先一覧同期でエラーあり (exit code: ${EXIT_CODE})"
    log "詳細: ${SUPPLIER_SYNC_LOG}"
fi

# --- Step 2: カラーミー同期 (シートAE → カラーミー) ---
# シートの数式で再計算された最新AE値をカラーミーに送信する。
log ""
log "[2/3] カラーミー同期（シート → カラーミー、価格・在庫・表示）"
log "-------------------------------------------"

CM_SYNC_LOG="${LOG_DIR}/price-only-step2-${TIMESTAMP}.log"
STEP_START=$(date +%s)
if "${PYTHON}" -m src.sync_colorme_products --price-only --verbose > "${CM_SYNC_LOG}" 2>&1; then
    STEP_END=$(date +%s)
    ELAPSED=$(( STEP_END - STEP_START ))
    log "カラーミー同期完了 (所要時間: $((ELAPSED / 60))分$((ELAPSED % 60))秒)"
    grep -E "(更新成功|更新失敗|スキップ)" "${CM_SYNC_LOG}" | tail -3 | while read -r line; do
        log "  ${line##*- }"
    done
else
    EXIT_CODE=$?
    log "WARNING: カラーミー同期でエラーあり (exit code: ${EXIT_CODE})"
    log "詳細: ${CM_SYNC_LOG}"
fi

# --- Step 3: 競合価格取得 ---
log ""
log "[3/3] 競合価格取得"
log "-------------------------------------------"

COMPETITOR_LOG="${LOG_DIR}/price-only-competitor-${TIMESTAMP}.log"
STEP_START=$(date +%s)
if "${PYTHON}" -m src.fetch_competitor_prices --verbose > "${COMPETITOR_LOG}" 2>&1; then
    STEP_END=$(date +%s)
    ELAPSED=$(( STEP_END - STEP_START ))
    log "競合価格取得完了 (所要時間: $((ELAPSED / 60))分$((ELAPSED % 60))秒)"
    grep -E "(成功:|失敗:)" "${COMPETITOR_LOG}" | tail -1 | while read -r line; do
        log "  ${line##*- }"
    done
else
    EXIT_CODE=$?
    log "WARNING: 競合価格取得で一部エラーあり (exit code: ${EXIT_CODE})"
    log "詳細: ${COMPETITOR_LOG}"
fi

# --- サマリー ---
OVERALL_END=$(date +%s)
OVERALL_ELAPSED=$(( OVERALL_END - OVERALL_START ))
log ""
log "=========================================="
log "カラーミー価格同期（軽量版）完了"
log "合計所要時間: $((OVERALL_ELAPSED / 60))分$((OVERALL_ELAPSED % 60))秒"
log "=========================================="

# --- 古いログの削除（30日以上前）---
find "${LOG_DIR}" -name "*.log" -mtime +30 -delete 2>/dev/null || true
