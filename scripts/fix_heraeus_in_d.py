"""APMEX商品ページ一覧で D列に「Heraeus」単独表記がある行のみD列を再生成

対象: A=採用 かつ B≠登録済 かつ D列に "Heraeus" を含むが "Argor Heraeus" は含まない

使い方:
  python scripts/fix_heraeus_in_d.py --dry-run
  python scripts/fix_heraeus_in_d.py
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
    args = p.parse_args()

    client = SpreadsheetClient()
    client.connect()
    sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_PRODUCTS)
    rows = sheet.get_all_values()

    # 絞り込み: A=採用 かつ B≠登録済 かつ D列に "Heraeus" 含むが "Argor Heraeus" は含まない
    targets = []
    for idx, row in enumerate(rows[1:], start=2):
        adopt = get_cell(row, Col.ADOPTED_FLAG)
        registered = get_cell(row, Col.REGISTRATION_STATUS)
        cm_name = get_cell(row, Col.CM_PRODUCT_NAME)
        product_name = get_cell(row, Col.PRODUCT_NAME)
        if adopt != "採用" or registered == "登録済":
            continue
        if not cm_name or not product_name:
            continue
        # "Heraeus" を含むが "Argor Heraeus" は含まない（既に "Argor Heraeus" 表記の行はスキップ）
        if "Heraeus" not in cm_name:
            continue
        if "Argor Heraeus" in cm_name:
            continue
        targets.append((idx, row, cm_name, product_name))

    logger.info(f"対象: {len(targets)}件 (D列に Heraeus 単独表記)")
    if not targets:
        return

    for r_idx, _, cur, src in targets[:20]:
        logger.info(f"  行{r_idx} 現: {cur[:70]}")

    if args.dry_run:
        logger.info("[DRY-RUN] 実行しません")
        return

    name_gen = JapaneseProductNameGenerator()
    success = 0
    updates = []
    for r_idx, row, cur, product_name in targets:
        info = {
            "name": product_name,
            "specs": get_cell(row, Col.SPECS) or "",
            "description": get_cell(row, Col.DESC_EN) or "",
            "country": get_cell(row, Col.COUNTRY) or "",
            "url": get_cell(row, Col.PRODUCT_URL) or "",
        }
        try:
            new_name = name_gen.generate(info, quantity=1)
            if new_name and new_name.strip():
                updates.append({
                    "range": f"{Config.SHEET_APMEX_PRODUCTS}!{Col.CM_PRODUCT_NAME.letter}{r_idx}",
                    "values": [[new_name]],
                })
                success += 1
                logger.info(f"  行{r_idx}: {new_name[:70]}")
        except Exception as e:
            logger.warning(f"  行{r_idx} 例外: {e}")

    if updates:
        sheet.spreadsheet.values_batch_update({"data": updates, "valueInputOption": "USER_ENTERED"})

    logger.info(f"完了: {success}件更新")


if __name__ == "__main__":
    main()
