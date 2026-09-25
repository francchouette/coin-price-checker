"""
新カラーミー商品管理シートの指定行にSEO項目を生成・書き込み

Vertex AI Gemini を使って、指定行の BO/BP/BQ 列を生成。
商品名（H列）・販売価格（AE列）・商品説明（BA列）・型番（AO列）を入力情報として使用。

使い方:
  # 行1137-1166を生成（ドライラン）
  python -m src.generate_seo_for_rows --start-row 1137 --end-row 1166 --dry-run

  # 本実行
  python -m src.generate_seo_for_rows --start-row 1137 --end-row 1166

  # 個別商品ID指定
  python -m src.generate_seo_for_rows --product-ids 191490424,191490457
"""

import argparse
import copy
import logging
import sys
import time

from .spreadsheet import SpreadsheetClient
from .config import Config
from .cm_sheet_columns import Col, get_cell, get_cell_int
from .add_product import SEOGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 30
MAX_RETRIES = 3


def batch_update_with_retry(sheet, batch_data: list) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            sheet.batch_update(copy.deepcopy(batch_data), value_input_option='USER_ENTERED')
            return True
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 10 * attempt
                logger.warning(f"  バッチ更新エラー（リトライ {attempt}/{MAX_RETRIES}、{wait}秒後）: {e}")
                time.sleep(wait)
            else:
                logger.error(f"  バッチ更新エラー（全リトライ失敗）: {e}")
                return False


def main():
    parser = argparse.ArgumentParser(description="シート指定行のSEO項目を生成")
    parser.add_argument("--start-row", type=int, help="開始行（1-indexed、ヘッダー含む）")
    parser.add_argument("--end-row", type=int, help="終了行（含む）")
    parser.add_argument("--product-ids", type=str, default="",
                        help="特定商品IDをカンマ区切り指定（--start-rowより優先）")
    parser.add_argument("--dry-run", action="store_true", help="生成のみ表示、シート書き込みなし")
    parser.add_argument("--overwrite", action="store_true",
                        help="既存値があっても上書き（デフォルト: 空欄行のみ）")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY-RUN" if args.dry_run else "本実行"
    logger.info(f"=== SEO項目生成 開始 [{mode}] ===")

    # SEOGenerator 初期化
    seo_gen = SEOGenerator()
    if not seo_gen.genai_model:
        logger.error("Vertex AI Gemini が初期化できませんでした")
        logger.error("認証: gcloud auth application-default login を確認")
        sys.exit(1)

    # スプレッドシート接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシート接続失敗")
        sys.exit(1)
    sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    all_data = sheet.get_all_values()

    # 対象行を決定
    target_rows = []  # [(row_num, row), ...]
    if args.product_ids:
        target_pids = set(p.strip() for p in args.product_ids.split(",") if p.strip())
        for row_idx, row in enumerate(all_data[1:], start=2):
            pid = get_cell(row, Col.PRODUCT_ID)
            if pid in target_pids:
                target_rows.append((row_idx, row))
    elif args.start_row is not None and args.end_row is not None:
        for row_idx, row in enumerate(all_data[1:], start=2):
            if args.start_row <= row_idx <= args.end_row:
                target_rows.append((row_idx, row))
    else:
        logger.error("--start-row/--end-row または --product-ids を指定してください")
        sys.exit(1)

    logger.info(f"対象行数: {len(target_rows)}件")

    # 既存値スキップ判定
    process_rows = []
    skipped = 0
    for row_idx, row in target_rows:
        if not args.overwrite:
            bo = get_cell(row, Col.PAGE_TITLE)
            bp = get_cell(row, Col.META_DESC)
            bq = get_cell(row, Col.META_KEYWORDS)
            if bo and bp and bq:
                skipped += 1
                continue
        process_rows.append((row_idx, row))

    logger.info(f"処理対象: {len(process_rows)}件（既存値ありスキップ: {skipped}件）")
    logger.info("")

    # SEO生成 + バッチ書き込み
    success = 0
    fail = 0
    pending_batch = []

    for i, (row_idx, row) in enumerate(process_rows, start=1):
        pid = get_cell(row, Col.PRODUCT_ID)
        name = get_cell(row, Col.NAME)
        sales_price = get_cell_int(row, Col.SALES_PRICE)
        expl = get_cell(row, Col.EXPL)
        model_number = get_cell(row, Col.MODEL_NUMBER)

        logger.info(f"[{i}/{len(process_rows)}] 行{row_idx} ID:{pid} {name[:35]}")

        seo_info = {
            "name": name,
            "price": sales_price,
            "description": expl,
            "specs": model_number,
        }

        try:
            page_title, meta_desc, meta_kw = seo_gen.generate(seo_info)
            if not (page_title or meta_desc or meta_kw):
                logger.warning(f"  → 生成失敗（空）")
                fail += 1
                continue

            logger.info(f"  ページタイトル: {page_title[:60]}")
            logger.info(f"  メタディス     : {meta_desc[:60]}")
            logger.info(f"  キーワード     : {meta_kw[:60]}")

            if not args.dry_run:
                if page_title:
                    pending_batch.append({'range': f'{Col.PAGE_TITLE.letter}{row_idx}', 'values': [[page_title]]})
                if meta_desc:
                    pending_batch.append({'range': f'{Col.META_DESC.letter}{row_idx}', 'values': [[meta_desc]]})
                if meta_kw:
                    pending_batch.append({'range': f'{Col.META_KEYWORDS.letter}{row_idx}', 'values': [[meta_kw]]})

            success += 1
        except Exception as e:
            logger.error(f"  → エラー: {e}")
            fail += 1

        # バッチ書き込み
        if not args.dry_run and len(pending_batch) >= BATCH_SIZE * 3:
            if batch_update_with_retry(sheet, pending_batch):
                logger.info(f"  [シート保存] {len(pending_batch)}セル")
            pending_batch = []
            time.sleep(1)

    # 残りをフラッシュ
    if not args.dry_run and pending_batch:
        if batch_update_with_retry(sheet, pending_batch):
            logger.info(f"  [最終シート保存] {len(pending_batch)}セル")

    logger.info("")
    logger.info(f"=== 完了 ===")
    logger.info(f"  成功: {success}件 / 失敗: {fail}件")
    if args.dry_run:
        logger.info("  [DRY-RUN] シート書き込みは行いませんでした")


if __name__ == "__main__":
    main()
