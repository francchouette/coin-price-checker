"""新カラーミー商品管理シートの AM列(グループID) 破損データを修復

破損例: '3190091,3190093 で書いたが、Google Sheets が連結数値 31900913190093 として保存

修復: カラーミーAPIから group_ids を取得し、value_input_option='RAW' で文字列として上書き

使い方:
  python scripts/fix_corrupted_group_ids.py --dry-run    # 確認のみ
  python scripts/fix_corrupted_group_ids.py              # 実行
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.colorme import ColorMeClient
from src.config import Config
from src.cm_sheet_columns import Col

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    logger.info("カラーミーAPI から全商品取得中...")
    cm = ColorMeClient()
    products = cm.get_all_products(limit=20000)
    api_groups = {p["id"]: (p.get("group_ids") or []) for p in products}
    logger.info(f"API商品: {len(api_groups)}件")

    logger.info("シート読込中...")
    sc = SpreadsheetClient()
    sc.connect()
    sheet = sc._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    rows = sheet.get_all_values()
    # AM列の現在値（UNFORMATTED）も取得して破損検知
    am_values = sheet.get(f"{Col.GROUP_IDS.letter}2:{Col.GROUP_IDS.letter}{len(rows)}", value_render_option="UNFORMATTED_VALUE")

    targets = []  # (row_n, pid, current, new_str)
    skipped_no_api = []
    for r_idx, r in enumerate(rows[1:], start=2):
        pid_str = r[Col.PRODUCT_ID.index] if Col.PRODUCT_ID.index < len(r) else ""
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)

        current_raw = ""
        am_row_idx = r_idx - 2
        if am_row_idx < len(am_values) and am_values[am_row_idx]:
            current_raw = am_values[am_row_idx][0]

        if pid not in api_groups:
            continue
        api_gids = api_groups[pid]
        api_str = ",".join(str(g) for g in api_gids) if api_gids else ""

        # シート側の現在文字列化（数値型 or 文字列）
        if isinstance(current_raw, float):
            current_str = f"{current_raw:.0f}" if current_raw == int(current_raw) else repr(current_raw)
        else:
            current_str = str(current_raw).lstrip("'").strip()

        # 完全一致なら何もしない
        if current_str == api_str:
            continue
        # APIに無くシートにも無い → 何もしない
        if not api_str and not current_str:
            continue

        targets.append((r_idx, pid, current_str, api_str))

    logger.info(f"修復対象: {len(targets)}件")
    if targets[:10]:
        logger.info("先頭10件:")
        for r_idx, pid, cur, new in targets[:10]:
            cur_show = cur[:50]
            new_show = new[:50] if new else "(空)"
            logger.info(f"  行{r_idx} ID={pid}: 現在={cur_show} → 新={new_show}")

    if args.dry_run:
        logger.info("[DRY-RUN] 書き込みは行いません")
        return

    if not targets:
        logger.info("修復対象なし")
        return

    # RAW でバッチ書き込み
    updates = [
        {"range": f"{Config.SHEET_COLORME_V2}!{Col.GROUP_IDS.letter}{r_idx}", "values": [[new]]}
        for r_idx, pid, cur, new in targets
    ]
    CHUNK = 500
    total = 0
    for i in range(0, len(updates), CHUNK):
        chunk = updates[i:i+CHUNK]
        sheet.spreadsheet.values_batch_update({
            "data": chunk,
            "valueInputOption": "RAW",
        })
        total += len(chunk)
        logger.info(f"  進捗: {total}/{len(updates)}件")
        time.sleep(0.5)

    logger.info(f"完了: {total}件のAM列を修復")


if __name__ == "__main__":
    main()
