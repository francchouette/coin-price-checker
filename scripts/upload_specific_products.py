"""
指定したカラーミー商品IDに対して、APMEXシートから画像URLを取得してアップロードする。

新規登録した商品にすぐ画像をつけたい時など、特定IDに絞って実行したい時に使う。

使い方:
    python scripts/upload_specific_products.py --ids 191795715,191795716,...
    python scripts/upload_specific_products.py --ids-file ids.txt
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.colorme_image_uploader import ColorMeImageUploader
from src.image_filter import select_product_images
from src.bs_sheet_columns import Col, get_cell

logger = logging.getLogger(__name__)


def collect_targets_for_ids(client, source: str, target_ids: set[int]):
    """
    指定IDに対応する行をシートから探し、(product_id, image_urls) のリストを返す
    """
    import re
    sheet_name = Config.SHEET_APMEX_PRODUCTS if source == "ap" else Config.SHEET_BULLIONSTAR_PRODUCTS
    ws = client._spreadsheet.worksheet(sheet_name)
    data = ws.get_all_values()

    targets = []
    for row_idx, row in enumerate(data[1:], start=2):
        colorme_url = get_cell(row, Col.COLORME_URL)
        if not colorme_url:
            continue
        m = re.search(r"pid=(\d+)", colorme_url)
        if not m:
            continue
        pid = int(m.group(1))
        if pid not in target_ids:
            continue

        # 画像URL収集
        raw_urls = []
        for i in range(10):
            idx = Col.IMAGE_1.index + i
            v = row[idx].strip() if idx < len(row) and row[idx] else ""
            if v and v not in raw_urls:
                raw_urls.append(v)

        product_url = get_cell(row, Col.PRODUCT_URL)
        urls = select_product_images(product_url, raw_urls)
        if not urls:
            logger.warning(f"  行{row_idx} ID={pid}: 画像URLが取得できませんでした")
            continue

        name = get_cell(row, Col.PRODUCT_NAME)
        targets.append((pid, urls, row_idx, name))

    found_ids = {t[0] for t in targets}
    missing = target_ids - found_ids
    if missing:
        logger.warning(f"以下のIDがシートで見つかりませんでした: {sorted(missing)}")

    return targets


async def run(target_ids: set[int], source: str, headless: bool):
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("シート接続失敗")
        return False

    targets = collect_targets_for_ids(client, source, target_ids)
    if not targets:
        logger.error("対象なし")
        return False

    logger.info(f"アップロード対象: {len(targets)}件")
    for pid, urls, row, name in targets:
        logger.info(f"  ID {pid} (行{row}): {len(urls)}枚 - {name[:40]}")

    success = 0
    failed = 0
    async with ColorMeImageUploader(headless=headless) as uploader:
        for i, (pid, urls, row, name) in enumerate(targets, 1):
            logger.info(f"=== [{i}/{len(targets)}] ID {pid}: {name[:40]} ===")
            r = await uploader.upload_product_images(pid, urls)
            if r.success:
                success += 1
                logger.info(f"  → 成功 ({len(r.uploaded_urls)}URL取得)")
            else:
                failed += 1
                logger.error(f"  → 失敗: {r.error_message}")

    logger.info(f"完了: 成功 {success} / 失敗 {failed}")
    return failed == 0


def main():
    p = argparse.ArgumentParser(description="指定カラーミー商品IDに画像アップロード")
    p.add_argument("--ids", type=str, help="カンマ区切りの商品IDリスト")
    p.add_argument("--ids-file", type=str, help="商品IDが1行ずつ書かれたファイル")
    p.add_argument("--source", choices=["bs", "ap"], default="ap")
    p.add_argument("--no-headless", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    ids = set()
    if args.ids:
        ids.update(int(x.strip()) for x in args.ids.split(",") if x.strip().isdigit())
    if args.ids_file:
        with open(args.ids_file) as f:
            for line in f:
                s = line.strip()
                if s.isdigit():
                    ids.add(int(s))
    if not ids:
        p.error("--ids または --ids-file が必要です")

    ok = asyncio.run(run(ids, args.source, not args.no_headless))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
