"""
新カラーミー商品管理シートの指定行範囲の商品に対して、
仕入れ先（APMEX等）の元画像を取得し、rembg処理してカラーミーに再アップロードする。

用途:
- 過去にrembg未対応で登録した商品の画像を背景処理済み透過PNGに差し替えたい場合
- 既存画像は **削除されない**（追加アップロードのみ）→ 管理画面で手動削除する想定

フロー:
1. 新カラーミー商品管理シートの指定行範囲から G列（カラーミー商品ID）と J列（仕入れ先URL）を取得
2. 商品仕入れ先一覧シートから仕入れ先URLをキーに元画像URL（U-AD列）を引く
3. ColorMeImageUploader でアップロード（rembg処理は自動適用）

使い方:
    python -m src.reupload_product_images --start 1167 --end 1197
    python -m src.reupload_product_images --start 1167 --end 1167 --no-headless  # デバッグ
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.colorme_image_uploader import ColorMeImageUploader
from src.image_filter import select_product_images, MAX_IMAGES_PER_PRODUCT

logger = logging.getLogger(__name__)

CM_SHEET = "新カラーミー商品管理"
SUPPLIER_SHEET = "商品仕入れ先一覧"


def build_supplier_image_map(client: SpreadsheetClient) -> dict[str, list[str]]:
    """
    商品仕入れ先一覧から { 仕入れ先URL: [画像URL1..10] } のマップを構築
    （フィルタは select_product_images 側で実施）
    """
    ws = client._spreadsheet.worksheet(SUPPLIER_SHEET)
    rows = ws.get("A2:AD")
    mapping: dict[str, list[str]] = {}
    for row in rows:
        if len(row) < 3:
            continue
        url = row[2].strip() if len(row) > 2 else ""
        if not url:
            continue
        # U-AD = index 20-29
        images = []
        for i in range(20, 30):
            if i < len(row):
                v = row[i].strip()
                if v.startswith("http"):
                    images.append(v)
        if images:
            mapping[url] = images
    logger.info(f"仕入れ先画像マップ構築: {len(mapping)}URL")
    return mapping


def collect_targets(
    client: SpreadsheetClient,
    start_row: int,
    end_row: int,
    supplier_map: dict[str, list[str]],
) -> list[tuple[int, list[str]]]:
    """
    指定行範囲の商品から (カラーミー商品ID, 画像URLリスト) を収集
    APMEXの場合は obv/rev のみに絞り込む
    """
    ws = client._spreadsheet.worksheet(CM_SHEET)
    rng = f"G{start_row}:J{end_row}"
    rows = ws.get(rng)

    targets: list[tuple[int, list[str]]] = []
    for offset, row in enumerate(rows):
        row_num = start_row + offset
        if len(row) < 4:
            logger.warning(f"行{row_num}: データ不足 {row}")
            continue
        product_id_str = row[0].strip()
        supplier_url = row[3].strip()
        if not product_id_str or not supplier_url:
            logger.warning(f"行{row_num}: 商品IDまたは仕入れ先URLが空")
            continue
        try:
            product_id = int(product_id_str)
        except ValueError:
            logger.warning(f"行{row_num}: 商品IDが数値ではない: {product_id_str}")
            continue
        images = supplier_map.get(supplier_url)
        if not images:
            logger.warning(f"行{row_num}: 仕入れ先URLに画像なし: {supplier_url}")
            continue
        # APMEXは先頭3枚（同一商品ID）のみに絞り込み
        before = len(images)
        images = select_product_images(supplier_url, images)
        if not images:
            logger.warning(f"行{row_num}: 絞り込み後に画像なし: {supplier_url}")
            continue
        if len(images) != before:
            logger.info(f"行{row_num}: 商品ID {product_id} - {before}枚→{len(images)}枚（先頭{MAX_IMAGES_PER_PRODUCT}枚）")
        else:
            logger.info(f"行{row_num}: 商品ID {product_id} - {len(images)}枚")
        targets.append((product_id, images))

    return targets


async def run(start_row: int, end_row: int, headless: bool, limit: int):
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシート接続失敗")
        return False

    supplier_map = build_supplier_image_map(client)
    targets = collect_targets(client, start_row, end_row, supplier_map)

    if not targets:
        logger.error("対象商品なし")
        return False

    if limit > 0:
        targets = targets[:limit]
        logger.info(f"--limit 適用: {len(targets)}件に制限")

    total_images = sum(len(urls) for _, urls in targets)
    logger.info(f"アップロード対象: {len(targets)}商品 / {total_images}枚")

    success = 0
    failed = 0
    async with ColorMeImageUploader(headless=headless) as uploader:
        for i, (pid, urls) in enumerate(targets, 1):
            logger.info(f"=== [{i}/{len(targets)}] 商品ID {pid} ({len(urls)}枚) ===")
            result = await uploader.upload_product_images(pid, urls)
            if result.success:
                success += 1
                logger.info(f"  → 成功: {len(result.uploaded_urls)}URL取得")
            else:
                failed += 1
                logger.error(f"  → 失敗: {result.error_message}")

    logger.info("=" * 50)
    logger.info(f"完了: 成功 {success}件 / 失敗 {failed}件")
    return failed == 0


def main():
    parser = argparse.ArgumentParser(
        description="指定行範囲の商品の画像を仕入れ先から再アップロード（rembg適用）"
    )
    parser.add_argument("--start", type=int, required=True, help="開始行（新カラーミー商品管理）")
    parser.add_argument("--end", type=int, required=True, help="終了行（新カラーミー商品管理）")
    parser.add_argument("--limit", type=int, default=0, help="処理件数上限（0=無制限）")
    parser.add_argument("--no-headless", action="store_true", help="ブラウザ表示")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.start > args.end:
        parser.error("--start は --end 以下で指定してください")

    ok = asyncio.run(run(args.start, args.end, not args.no_headless, args.limit))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
