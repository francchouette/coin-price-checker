#!/bin/bash
# ================================================================
# APMEX採用商品 カラーミー登録スクリプト
#
# 実行内容:
#   APMEX商品ページ一覧で A列=「採用」の商品を
#   カラーミーAPIで自動登録
#
# 使用方法:
#   手動実行: bash scripts/ap-register.sh
#   ダッシュボード: 「カラーミー登録」ボタンから実行
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
LOCK_FILE="/tmp/ap-register.lock"

# Python出力バッファリングを無効化（リアルタイムでログに反映するため）
export PYTHONUNBUFFERED=1

# Google ADC（ローカル認証）
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"

# === ログ設定 ===
mkdir -p "${LOG_DIR}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="${LOG_DIR}/ap-register-${TIMESTAMP}.log"

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
log "APMEX採用商品 カラーミー登録 開始"
log "=========================================="

START=$(date +%s)

if "${PYTHON}" -m src.register_adopted_products --source ap --verbose >> "${LOG_FILE}" 2>&1; then
    END=$(date +%s)
    ELAPSED=$(( END - START ))
    log ""
    log "=========================================="
    log "APMEX採用商品 カラーミー登録 完了"
    log "所要時間: $((ELAPSED / 60))分$((ELAPSED % 60))秒"
    log "=========================================="
else
    EXIT_CODE=$?
    log ""
    log "ERROR: カラーミー登録が失敗しました (exit code: ${EXIT_CODE})"
    log "詳細: ${LOG_FILE}"
    exit 1
fi

# --- 古いログの削除（30日以上前）---
find "${LOG_DIR}" -name "ap-register-*.log" -mtime +30 -delete 2>/dev/null || true
