"""APMEX商品ページ一覧で A=採用 かつ B≠登録済 かつ D列空 の商品に
日本語カラーミー商品名（D列）を AI 生成して埋める

使い方:
  python scripts/fill_d_for_adopted_apmex.py --dry-run   # 対象表示のみ
  python scripts/fill_d_for_adopted_apmex.py             # 本実行
  python scripts/fill_d_for_adopted_apmex.py --limit 5   # 件数制限
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.config import Config
from src.bs_sheet_columns import Col, get_cell
from src.add_product import JapaneseProductNameGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--overwrite", action="store_true", help="D列に既存値があっても上書き")
    args = p.parse_args()

    client = SpreadsheetClient()
    client.connect()
    sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_PRODUCTS)
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        logger.info("データなし")
        return

    # 絞り込み: A=採用 かつ B≠登録済 かつ D列空 かつ G列(仕入れ先商品名)あり
    targets = []
    for idx, row in enumerate(rows[1:], start=2):
        adopt = get_cell(row, Col.ADOPTED_FLAG)
        registered = get_cell(row, Col.REGISTRATION_STATUS)
        cm_name = get_cell(row, Col.CM_PRODUCT_NAME)
        product_name = get_cell(row, Col.PRODUCT_NAME)
        if adopt == "採用" and registered != "登録済" and product_name:
            if cm_name and not args.overwrite:
                continue
            targets.append((idx, row, product_name))

    if args.limit:
        targets = targets[:args.limit]

    logger.info(f"対象: {len(targets)}件 (A=採用 かつ B≠登録済 かつ D空)")
    if not targets:
        return

    if args.dry_run:
        for r_idx, _, name in targets[:20]:
            logger.info(f"  行{r_idx}: {name[:60]}")
        if len(targets) > 20:
            logger.info(f"  ... 他 {len(targets)-20}件")
        logger.info("[DRY-RUN] 実行しません")
        return

    # AI生成器初期化
    name_gen = JapaneseProductNameGenerator()

    success = 0
    failed = 0
    updates = []
    for i, (r_idx, row, product_name) in enumerate(targets, 1):
        info = {
            "name": product_name,
            "specs": get_cell(row, Col.SPECS) or "",
            "description": get_cell(row, Col.DESC_EN) or "",
            "country": get_cell(row, Col.COUNTRY) or "",
            "url": get_cell(row, Col.PRODUCT_URL) or "",
        }
        try:
            cm_name = name_gen.generate(info, quantity=1)
            if cm_name and cm_name.strip():
                updates.append({
                    "range": f"{Config.SHEET_APMEX_PRODUCTS}!{Col.CM_PRODUCT_NAME.letter}{r_idx}",
                    "values": [[cm_name]],
                })
                success += 1
                logger.info(f"  [{i}/{len(targets)}] 行{r_idx}: {cm_name[:55]}")
            else:
                failed += 1
                logger.warning(f"  [{i}/{len(targets)}] 行{r_idx}: 生成失敗（空）")
        except Exception as e:
            failed += 1
            logger.warning(f"  [{i}/{len(targets)}] 行{r_idx}: 例外 {e}")

        # 30件ごとにフラッシュ
        if len(updates) >= 30:
            sheet.spreadsheet.values_batch_update({"data": updates, "valueInputOption": "USER_ENTERED"})
            updates = []
            time.sleep(0.5)

    # 残り
    if updates:
        sheet.spreadsheet.values_batch_update({"data": updates, "valueInputOption": "USER_ENTERED"})

    logger.info(f"完了: 成功={success}件 / 失敗={failed}件")


if __name__ == "__main__":
    main()
