#!/bin/bash
# 登録プロセス リアルタイムモニタリング（5秒間隔で自動更新）
LOG="/private/tmp/claude-501/-Users-abehiroshi-cursor-coin-price-check-script/tasks/bf80c42.output"
INTERVAL=5

while true; do
    clear
    echo "=========================================="
    echo " 登録プロセス リアルタイムモニタリング"
    echo " $(date '+%Y-%m-%d %H:%M:%S')  (${INTERVAL}秒間隔)"
    echo "=========================================="

    # プロセス確認
    if pgrep -f "register_adopted_products" > /dev/null; then
        echo "状態: ✅ 稼働中"
    else
        echo "状態: ⛔ 停止"
    fi
    echo ""

    # 成功/失敗カウント
    SUCCESS=$(grep -c "登録成功: カラーミー商品ID" "$LOG" 2>/dev/null || echo 0)
    FAIL=$(grep -c "登録失敗:" "$LOG" 2>/dev/null || echo 0)
    TOTAL=157
    PCT=$((SUCCESS * 100 / TOTAL))

    # プログレスバー
    BAR_LEN=30
    FILLED=$((PCT * BAR_LEN / 100))
    EMPTY=$((BAR_LEN - FILLED))
    BAR=$(printf '%0.s█' $(seq 1 $FILLED 2>/dev/null) 2>/dev/null)
    BAR="${BAR}$(printf '%0.s░' $(seq 1 $EMPTY 2>/dev/null) 2>/dev/null)"
    echo "進捗: [${BAR}] ${SUCCESS}/${TOTAL}件 (${PCT}%)"
    echo "失敗: ${FAIL}件"
    echo ""

    # ブラウザ起動回数
    BROWSER=$(grep -c "ブラウザを起動中" "$LOG" 2>/dev/null || echo 0)
    LOGIN=$(grep -c "ログイン成功" "$LOG" 2>/dev/null || echo 0)
    echo "ブラウザ起動: ${BROWSER}回 / ログイン: ${LOGIN}回"
    echo ""

    # 最新の処理
    echo "--- 最新の処理 ---"
    LATEST=$(grep "処理中:" "$LOG" 2>/dev/null | tail -1 | sed 's/.*処理中: /  /')
    LATEST_OK=$(grep "登録成功: カラーミー商品ID" "$LOG" 2>/dev/null | tail -1 | sed 's/.*登録成功/  登録成功/')
    echo "$LATEST"
    echo "$LATEST_OK"
    echo ""

    # 直近のログ5行
    echo "--- 直近のログ ---"
    tail -5 "$LOG" 2>/dev/null | sed 's/^/  /'

    # Ctrl+Cで終了
    echo ""
    echo "(Ctrl+C で終了)"
    sleep $INTERVAL
done
