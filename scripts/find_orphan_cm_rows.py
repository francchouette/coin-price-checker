"""新カラーミー商品管理シートで、カラーミーAPI側に存在しない商品行を検出/削除

カラーミーで削除済の商品がシートに残骸として残るケースを掃除する。

使い方:
  python scripts/find_orphan_cm_rows.py                    # dry-run（検出のみ）
  python scripts/find_orphan_cm_rows.py --csv orphan.csv   # CSV出力
  python scripts/find_orphan_cm_rows.py --delete           # 実際に削除
"""

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.colorme import ColorMeClient
from src.config import Config
from src.cm_sheet_columns import Col, get_cell

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--delete", action="store_true", help="検出後、シートから行を削除する（指定しない場合はdry-run）")
    p.add_argument("--csv", type=str, default="", help="検出結果をCSV出力")
    args = p.parse_args()

    logger.info("カラーミーAPI から全商品ID取得中...")
    cm = ColorMeClient()
    products = cm.get_all_products(limit=20000)
    api_ids = {p["id"] for p in products}
    logger.info(f"API商品: {len(api_ids)}件")

    logger.info("シート読込中...")
    sc = SpreadsheetClient()
    sc.connect()
    sheet = sc._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    rows = sheet.get_all_values()

    orphans = []  # (row_idx, product_id, name, supplier_url)
    empty_id_rows = []  # G列が空の行
    for r_idx, r in enumerate(rows[1:], start=2):
        pid_str = get_cell(r, Col.PRODUCT_ID)
        if not pid_str:
            # ヘッダ以外で全て空の行はスキップ、商品ID空のみは別途記録
            non_empty = sum(1 for v in r if v and v.strip())
            if non_empty > 0:
                empty_id_rows.append((r_idx, get_cell(r, Col.NAME)[:40]))
            continue
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if pid not in api_ids:
            name = get_cell(r, Col.NAME)[:50]
            supplier_url = get_cell(r, Col.SUPPLIER_URL)[:60]
            orphans.append((r_idx, pid, name, supplier_url))

    logger.info("")
    logger.info("=" * 60)
    logger.info(f"検出結果: カラーミーAPIに存在しない商品 = {len(orphans)}件")
    logger.info(f"参考: G列(商品ID)空でデータあり行 = {len(empty_id_rows)}件")
    logger.info("=" * 60)

    if orphans:
        logger.info("")
        logger.info("=== 削除候補（カラーミーAPIに存在しない）===")
        for r_idx, pid, name, surl in orphans:
            logger.info(f"  行{r_idx} ID={pid} : {name}  [仕入URL:{surl}]")

    if empty_id_rows:
        logger.info("")
        logger.info("=== 参考: G列(商品ID)空の行 ===")
        for r_idx, name in empty_id_rows[:20]:
            logger.info(f"  行{r_idx} : {name}")
        if len(empty_id_rows) > 20:
            logger.info(f"  ... 他 {len(empty_id_rows)-20} 件")

    if args.csv and orphans:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["row", "product_id", "name", "supplier_url"])
            for r in orphans:
                w.writerow(r)
        logger.info(f"CSV保存: {args.csv}")

    if not args.delete:
        logger.info("")
        logger.info("[DRY-RUN] 削除しません。実行するには --delete を付けてください")
        return

    if not orphans:
        logger.info("削除対象なし")
        return

    # 削除実行（行番号降順で削除する＝下から）
    logger.info("")
    logger.info(f"=== 削除実行: {len(orphans)}行 ===")
    sorted_orphans = sorted(orphans, key=lambda x: x[0], reverse=True)
    deleted = 0
    for r_idx, pid, name, _ in sorted_orphans:
        try:
            sheet.delete_rows(r_idx)
            deleted += 1
            logger.info(f"  削除: 行{r_idx} ID={pid} {name[:30]}")
            time.sleep(0.5)  # API制限対策
        except Exception as e:
            logger.error(f"  削除失敗 行{r_idx}: {e}")

    logger.info("")
    logger.info(f"完了: {deleted}/{len(orphans)}行削除")


if __name__ == "__main__":
    main()
