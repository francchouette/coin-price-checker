#!/bin/bash
# カラーミー同期プロセス リアルタイムモニタリング
LOG_FILE="/tmp/cm-sync-prices-step1.log"
STEP2_LOG="/tmp/cm-sync-prices-step2.log"

# 色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# macOS互換: grep -oP の代替
extract_number() {
    # $1: パターン前のテキスト, $2: ファイル
    # 例: extract_number "全商品数: " file.log → 数値を返す
    grep "$1" "$2" 2>/dev/null | tail -1 | sed "s/.*$1//" | sed 's/[^0-9].*//' | head -1
}

draw_bar() {
    local current=$1
    local total=$2
    local width=40
    if [ -z "$total" ] || [ "$total" -eq 0 ] 2>/dev/null; then
        printf '['; for i in $(seq 1 $width); do printf '░'; done; printf ']  0%%'
        return
    fi
    local filled=$(( current * width / total ))
    local empty=$(( width - filled ))
    local pct=$(( current * 100 / total ))
    printf '['
    for i in $(seq 1 $filled); do printf '█'; done
    for i in $(seq 1 $empty); do printf '░'; done
    printf '] %3d%%' "$pct"
}

while true; do
    clear
    echo -e "${BOLD}=========================================="
    echo -e " カラーミー同期 リアルタイムモニタリング"
    echo -e " $(date '+%Y-%m-%d %H:%M:%S')  (5秒間隔)"
    echo -e "==========================================${NC}"

    # プロセス状態チェック
    if pgrep -f "src.download_colorme_products" > /dev/null 2>&1; then
        echo -e "状態: ${GREEN}✅ Step 1 稼働中（ダウンロード+価格取得）${NC}"
    elif pgrep -f "src.sync_colorme_products" > /dev/null 2>&1; then
        echo -e "状態: ${GREEN}✅ Step 2 稼働中（カラーミー同期）${NC}"
    else
        if [ -f "$STEP2_LOG" ] && grep -q "カラーミー商品同期完了" "$STEP2_LOG" 2>/dev/null; then
            echo -e "状態: ${GREEN}✅ 全ステップ完了${NC}"
        elif [ -f "$LOG_FILE" ] && grep -q "カラーミー商品ダウンロード完了" "$LOG_FILE" 2>/dev/null; then
            echo -e "状態: ${YELLOW}⏸ Step 1 完了、Step 2 待機中${NC}"
        else
            echo -e "状態: ${RED}❌ プロセス停止${NC}"
        fi
    fi
    echo ""

    # === Step 1: ダウンロード+価格取得 ===
    if [ -f "$LOG_FILE" ]; then
        echo -e "${BOLD}--- Step 1: カラーミーダウンロード+価格取得 ---${NC}"

        # 商品取得数
        TOTAL_PRODUCTS=$(extract_number "全商品数: " "$LOG_FILE")
        API_FETCHED=$(grep "合計 " "$LOG_FILE" 2>/dev/null | tail -1 | sed 's/.*合計 //' | sed 's/[^0-9].*//')

        if [ -n "$TOTAL_PRODUCTS" ]; then
            echo -e "カラーミーAPI: ${CYAN}${API_FETCHED:-0}/${TOTAL_PRODUCTS}件${NC} 取得済み"
        fi

        # スクレイピング進捗
        SCRAPE_TOTAL=$(grep "ユニーク" "$LOG_FILE" 2>/dev/null | tail -1 | sed 's/.*ユニーク//' | sed 's/[^0-9].*//')
        SCRAPE_SUCCESS=$(grep -c "取得成功:" "$LOG_FILE" 2>/dev/null)
        SCRAPE_FAIL=$(grep -c "スクレイピング失敗:" "$LOG_FILE" 2>/dev/null)
        # 空文字やエラー対策
        SCRAPE_SUCCESS=${SCRAPE_SUCCESS:-0}
        SCRAPE_FAIL=${SCRAPE_FAIL:-0}
        SCRAPE_DONE=$((SCRAPE_SUCCESS + SCRAPE_FAIL))

        if [ -n "$SCRAPE_TOTAL" ] && [ "$SCRAPE_TOTAL" -gt 0 ] 2>/dev/null; then
            echo ""
            echo -e "スクレイピング:"
            echo -ne "  進捗: "
            draw_bar "$SCRAPE_DONE" "$SCRAPE_TOTAL"
            echo -e "  ${SCRAPE_DONE}/${SCRAPE_TOTAL}件"
            echo -e "  成功: ${GREEN}${SCRAPE_SUCCESS}${NC}件  失敗: ${RED}${SCRAPE_FAIL}${NC}件"
        fi

        # 為替レート
        if grep -q "為替レート取得完了" "$LOG_FILE" 2>/dev/null; then
            echo ""
            echo -e "為替レート: ${GREEN}取得済み${NC}"
        fi

        # シート書き込み
        UPDATED=$(extract_number "既存商品の更新完了: " "$LOG_FILE")
        ADDED=$(extract_number "新規商品の追加完了: " "$LOG_FILE")
        if [ -n "$UPDATED" ] || [ -n "$ADDED" ]; then
            echo ""
            echo -e "シート書き込み: 更新=${GREEN}${UPDATED:-0}${NC}件  追加=${CYAN}${ADDED:-0}${NC}件"
        fi

        # 完了チェック
        if grep -q "カラーミー商品ダウンロード完了" "$LOG_FILE" 2>/dev/null; then
            echo -e "\n${GREEN}✅ Step 1 完了${NC}"
        fi
    else
        echo -e "${YELLOW}Step 1: ログファイル待機中...${NC}"
    fi

    echo ""

    # === Step 2: カラーミー同期 ===
    if [ -f "$STEP2_LOG" ]; then
        echo -e "${BOLD}--- Step 2: カラーミー同期 ---${NC}"

        SYNC_TOTAL=$(extract_number "更新対象: " "$STEP2_LOG")
        SYNC_SUCCESS=$(grep -c "→ 更新成功" "$STEP2_LOG" 2>/dev/null)
        SYNC_FAIL=$(grep -c "→ 更新失敗" "$STEP2_LOG" 2>/dev/null)
        SYNC_SUCCESS=${SYNC_SUCCESS:-0}
        SYNC_FAIL=${SYNC_FAIL:-0}
        SYNC_DONE=$((SYNC_SUCCESS + SYNC_FAIL))

        if [ -n "$SYNC_TOTAL" ] && [ "$SYNC_TOTAL" -gt 0 ] 2>/dev/null; then
            echo -ne "  進捗: "
            draw_bar "$SYNC_DONE" "$SYNC_TOTAL"
            echo -e "  ${SYNC_DONE}/${SYNC_TOTAL}件"
            echo -e "  成功: ${GREEN}${SYNC_SUCCESS}${NC}件  失敗: ${RED}${SYNC_FAIL}${NC}件"
        fi

        # シート保存
        SHEET_SAVED=$(grep -c "シート保存" "$STEP2_LOG" 2>/dev/null)
        SHEET_SAVED=${SHEET_SAVED:-0}
        if [ "$SHEET_SAVED" -gt 0 ] 2>/dev/null; then
            echo -e "  シート保存: ${SHEET_SAVED}回"
        fi

        # 完了チェック
        if grep -q "カラーミー商品同期完了" "$STEP2_LOG" 2>/dev/null; then
            echo -e "\n${GREEN}✅ Step 2 完了${NC}"
        fi
    fi

    echo ""
    echo -e "${BOLD}--- 直近ログ (5行) ---${NC}"
    if [ -f "$STEP2_LOG" ] && [ -s "$STEP2_LOG" ]; then
        tail -5 "$STEP2_LOG" | sed 's/^/  /'
    elif [ -f "$LOG_FILE" ]; then
        tail -5 "$LOG_FILE" | sed 's/^/  /'
    fi

    echo ""
    echo -e "${YELLOW}Ctrl+C で終了${NC}"
    sleep 5
done
