#!/bin/bash
# ================================================================
# カラーミー同期スクリプト（1行ずつ即時同期）
#
# 処理フロー（1商品ごとに繰り返し）:
#   1. カラーミーAPIから全商品を一括ダウンロード
#   2. 仕入れ先サイトから価格・在庫をスクレイピング
#   3. シートの全77列を更新（数式は保護）
#   4. 欠落した数式を復元
#   5. 数式で再計算されたAE列（販売価格）を読み戻し
#   6. カラーミーAPIにフルスペック同期（価格・在庫・カテゴリ・説明等）
#
# 途中停止しても、処理済みの商品はカラーミーに反映済み。
#
# 使用方法:
#   手動実行: bash scripts/cm-sync-prices.sh
#   launchd:  自動実行（4時間ごと）
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
LOCK_FILE="/tmp/cm-sync-prices.lock"

# Google ADC（ローカル認証）
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"

# Python出力バッファリングを無効化（リアルタイムでログに反映するため）
export PYTHONUNBUFFERED=1

# === ログ設定 ===
mkdir -p "${LOG_DIR}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="${LOG_DIR}/cm-sync-${TIMESTAMP}.log"

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
log "カラーミー同期（1行ずつ即時同期）開始"
log "=========================================="

OVERALL_START=$(date +%s)

# --- ダウンロード + スクレイピング + 数式復元 + カラーミー同期 ---
log ""
log "カラーミー商品ダウンロード + スクレイピング + カラーミーAPI同期"
log "-------------------------------------------"

DETAIL_LOG="${LOG_DIR}/sync-all-${TIMESTAMP}.log"
if "${PYTHON}" -m src.download_colorme_products --fetch-prices --sync --verbose > "${DETAIL_LOG}" 2>&1; then
    OVERALL_END=$(date +%s)
    OVERALL_ELAPSED=$(( OVERALL_END - OVERALL_START ))
    log "処理完了 (所要時間: $((OVERALL_ELAPSED / 60))分$((OVERALL_ELAPSED % 60))秒)"

    # 結果をログに記録
    grep -E "(スクレイピング結果|シート更新完了|カラーミー同期成功|カラーミー同期失敗|カラーミー同期スキップ)" "${DETAIL_LOG}" | while read -r line; do
        log "  ${line##*- }"
    done
else
    EXIT_CODE=$?
    OVERALL_END=$(date +%s)
    OVERALL_ELAPSED=$(( OVERALL_END - OVERALL_START ))
    log "WARNING: 一部エラーあり (exit code: ${EXIT_CODE})"
    log "詳細: ${DETAIL_LOG}"
fi

# --- サマリー ---
log ""
log "=========================================="
log "カラーミー同期（フルスペック）完了"
log "合計所要時間: $((OVERALL_ELAPSED / 60))分$((OVERALL_ELAPSED % 60))秒"
log "=========================================="

# --- 古いログの削除（30日以上前）---
find "${LOG_DIR}" -name "*.log" -mtime +30 -delete 2>/dev/null || true
