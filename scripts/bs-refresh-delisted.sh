#!/bin/bash
# 一覧から消えたブリオンスター採用商品の在庫・価格を再取得する（1日1回・03:00）
#
# bs-scrape.sh（bullionstar_products --fetch-prices）はカテゴリ一覧に載っている商品しか
# 更新しないため、在庫切れで一覧から消えた商品はここで個別に取得する。
# 対象は新カラーミー商品管理に載っている Bullionstar 商品だけ（通常300件弱・45分程度）。
#
# bs-scrape が走っている間は待つ（同じシートに書くため同時実行しない）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -f "${PROJECT_DIR}/.env" ]; then
    set -a
    source "${PROJECT_DIR}/.env"
    set +a
fi

PYTHON="${PYTHON_PATH:-$(which python3)}"
LOG_DIR="${PROJECT_DIR}/logs"
LOCK_FILE="/tmp/bs-refresh.lock"
BS_SCRAPE_LOCK="/tmp/bs-scrape.lock"
WAIT_MAX_MIN=90   # bs-scrape の終了をこの分数まで待つ

export PYTHONUNBUFFERED=1
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"

mkdir -p "${LOG_DIR}"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="${LOG_DIR}/bs-refresh-${TIMESTAMP}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

is_alive() {
    local pid
    pid=$(cat "$1" 2>/dev/null) || return 1
    kill -0 "${pid}" 2>/dev/null
}

if [ -f "${LOCK_FILE}" ] && is_alive "${LOCK_FILE}"; then
    log "ERROR: 既に実行中です (PID: $(cat "${LOCK_FILE}"))"
    exit 1
fi
echo $$ > "${LOCK_FILE}"
trap 'rm -f "${LOCK_FILE}"' EXIT

# bs-scrape が走っていたら終わるまで待つ
waited=0
while [ -f "${BS_SCRAPE_LOCK}" ] && is_alive "${BS_SCRAPE_LOCK}"; do
    if [ "${waited}" -ge "${WAIT_MAX_MIN}" ]; then
        log "ERROR: bs-scrape が ${WAIT_MAX_MIN} 分経っても終わらないため中止します"
        exit 1
    fi
    [ "${waited}" -eq 0 ] && log "bs-scrape 実行中のため待機します (PID: $(cat "${BS_SCRAPE_LOCK}"))"
    sleep 60
    waited=$((waited + 1))
done
[ "${waited}" -gt 0 ] && log "bs-scrape 終了を確認（${waited}分待機）"

cd "${PROJECT_DIR}"

log "=========================================="
log "一覧から消えたブリオンスター商品の再取得 開始"
log "=========================================="
START=$(date +%s)

if "${PYTHON}" -m src.bs_refresh_delisted >> "${LOG_FILE}" 2>&1; then
    END=$(date +%s)
    ELAPSED=$(( END - START ))
    log ""
    log "=========================================="
    log "再取得 完了"
    log "所要時間: $((ELAPSED / 60))分$((ELAPSED % 60))秒"
    grep -E "在庫あり|在庫切れ|在庫状況が変わった商品|スクレイピング失敗|BSシートに行なし" "${LOG_FILE}" | sed 's/^/  /' | tee -a "${LOG_FILE}" >/dev/null
    log "=========================================="
else
    EXIT_CODE=$?
    log ""
    log "ERROR: 再取得が失敗しました (exit code: ${EXIT_CODE})"
    log "詳細: ${LOG_FILE}"
    exit 1
fi

find "${LOG_DIR}" -name "bs-refresh-*.log" -mtime +30 -delete 2>/dev/null || true
find "${LOG_DIR}" -name "bs-refresh-backup-*.json" -mtime +30 -delete 2>/dev/null || true
