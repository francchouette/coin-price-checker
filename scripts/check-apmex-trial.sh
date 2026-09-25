#!/bin/bash
# APMEX除外の試験運用 結果確認（2026-09-11〜）
#
# 除外設定: scripts/cm-sync-prices.sh の CM_SYNC_SKIP_SCRAPE_DOMAINS="apmex.com"
# 戻す場合: その1行をコメントアウトするだけ
#
# 使い方: bash scripts/check-apmex-trial.sh

cd "$(dirname "$0")/.." || exit 1
LOGS=logs

echo "=============================================="
echo " APMEX除外 試験運用チェック  $(date '+%Y-%m-%d %H:%M')"
echo "=============================================="

echo
echo "[設定] 除外が有効か"
grep -q '^export CM_SYNC_SKIP_SCRAPE_DOMAINS' scripts/cm-sync-prices.sh \
  && echo "  ✅ 有効: $(grep '^export CM_SYNC_SKIP_SCRAPE_DOMAINS' scripts/cm-sync-prices.sh)" \
  || echo "  ⏹ 無効（従来どおり全URLをスクレイピング）"

echo
echo "[実行状況]"
if [ -f /tmp/cm-sync-prices.lock ] && kill -0 "$(cat /tmp/cm-sync-prices.lock 2>/dev/null)" 2>/dev/null; then
  echo "  同期実行中（$(ls -t $LOGS/sync-all-*.log | head -1 | xargs grep -oE '\[[0-9]+/[0-9]+\]' | tail -1)）"
else
  echo "  停止中"
fi

echo
echo "[① スクレイピング結果の推移]  失敗140件 → 0件 / 除外141件 になれば成功"
printf "  %-32s %s\n" "ログ" "結果"
for f in $(ls -tr $LOGS/sync-all-*.log | tail -6); do
  r=$(grep -a "スクレイピング結果" "$f" | tail -1 | sed -E 's/.*スクレイピング結果: //')
  printf "  %-32s %s\n" "$(basename "$f")" "${r:-（未完了）}"
done

echo
echo "[② 所要時間]  約4時間 → 約3時間40分 に短縮されれば成功"
for f in $(ls -tr $LOGS/cm-sync-*.log | tail -6); do
  t=$(grep -a "合計所要時間" "$f" | tail -1 | sed -E 's/.*合計所要時間: //')
  printf "  %-32s %s\n" "$(basename "$f")" "${t:-（未完了）}"
done

echo
echo "[③ APMEX価格の鮮度]  当日の日付が並んでいれば ap-scrape が正常"
python - <<'PY' 2>&1 | grep -v "No project ID"
import sys, collections
sys.path.insert(0, '.')
from src.spreadsheet import SpreadsheetClient
from src.config import Config
sc = SpreadsheetClient()
sc.connect()
rows = sc._spreadsheet.worksheet(Config.SHEET_SUPPLIERS).get_all_values()
c = collections.defaultdict(collections.Counter)
for r in rows[1:]:
    if len(r) < 16 or not r[2].strip():
        continue
    c[r[3].strip() or '(空)'][r[15][:10] or '(空)'] += 1
for site in sorted(c):
    top = ', '.join(f'{d}:{n}件' for d, n in c[site].most_common(3))
    print(f'  {site:<14} {top}')
PY

echo
echo "[④ 価格カスケード]  [深刻]0件を維持していれば健全"
python -m src.check_price_cascade --limit 0 2>&1 \
  | grep -v "No project ID" | grep -E "対象商品|\[深刻\]|\[警告\]|✓|△|⚠"

echo
echo "=============================================="
echo " ①②が改善し ③④が維持されていれば試験成功"
echo " 崩れていれば cm-sync-prices.sh の export 行をコメントアウト"
echo "=============================================="
