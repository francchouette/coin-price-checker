"""
カラーミーCSVシートから新カラーミー商品管理シートへSEO項目を反映

「product0414変更後」シートから以下を読み取り、「新カラーミー商品管理」シートへ反映:
  - A列: 商品ID
  - BA列: タイトル → 新カラーミー商品管理 BO列（ページタイトル）
  - BC列: ページ概要 → 新カラーミー商品管理 BP列（メタディスクリプション）

キーワード（BB列）は対象外。

使い方:
  python -m src.update_seo_from_csv --dry-run --verbose        # 変更対象を表示のみ
  python -m src.update_seo_from_csv --verbose                   # 実際に書き込み
  python -m src.update_seo_from_csv --csv-sheet シート名 --dry-run
"""

import argparse
import copy
import logging
import sys
import time

from .spreadsheet import SpreadsheetClient
from .config import Config
from .cm_sheet_columns import Col, get_cell

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_CSV_SHEET = 'product0414変更後'
BATCH_SIZE = 20
MAX_RETRIES = 3


def batch_update_with_retry(sheet, batch_data: list) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = copy.deepcopy(batch_data)
            sheet.batch_update(data, value_input_option='USER_ENTERED')
            return True
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 10 * attempt
                logger.warning(f"  シート更新エラー（リトライ {attempt}/{MAX_RETRIES}、{wait}秒後）: {e}")
                time.sleep(wait)
            else:
                logger.error(f"  シート更新エラー（全リトライ失敗）: {e}")
                return False


def main():
    parser = argparse.ArgumentParser(description="CSVシートからSEO項目（ページタイトル・メタディス）を反映")
    parser.add_argument("--csv-sheet", default=DEFAULT_CSV_SHEET, help=f"CSVシート名（デフォルト: {DEFAULT_CSV_SHEET}）")
    parser.add_argument("--dry-run", action="store_true", help="実際の書き込みをせず、変更対象を表示のみ")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY-RUN（書き込みなし）" if args.dry_run else "本実行"
    logger.info(f"=== SEO反映 開始 [{mode}] ===")

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    # CSVシート読み込み
    try:
        csv_sheet = client._spreadsheet.worksheet(args.csv_sheet)
    except Exception:
        logger.error(f"CSVシート「{args.csv_sheet}」が見つかりません")
        sys.exit(1)

    logger.info(f"CSVシート: {args.csv_sheet} ({csv_sheet.row_count}行)")
    csv_data = csv_sheet.get_all_values()

    # 商品ID -> (タイトル, ページ概要) マップ
    # A列: 商品ID (index 0), BA列: タイトル (index 52), BC列: ページ概要 (index 54)
    csv_map = {}  # product_id -> (title, meta_desc)
    for row in csv_data[1:]:  # ヘッダースキップ
        if len(row) < 55:
            continue
        product_id = row[0].strip()
        if not product_id or not product_id.isdigit():
            continue
        title = row[52].strip() if len(row) > 52 else ""
        meta_desc = row[54].strip() if len(row) > 54 else ""
        csv_map[product_id] = (title, meta_desc)

    logger.info(f"CSV読み込み: {len(csv_map)}件の商品ID")

    # 新カラーミー商品管理シート読み込み
    cm_sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    cm_data = cm_sheet.get_all_values()
    logger.info(f"新カラーミー商品管理: {len(cm_data) - 1}行")

    # 差分抽出
    updates = []  # [(row_num, product_id, field, old, new), ...]
    pending_batch = []
    matched = 0
    title_changes = 0
    meta_changes = 0

    for row_idx, row in enumerate(cm_data[1:], start=2):
        product_id = get_cell(row, Col.PRODUCT_ID)
        if not product_id or product_id not in csv_map:
            continue

        matched += 1
        csv_title, csv_meta = csv_map[product_id]
        sheet_title = get_cell(row, Col.PAGE_TITLE)
        sheet_meta = get_cell(row, Col.META_DESC)

        # タイトル（BO列）
        if csv_title and csv_title != sheet_title:
            title_changes += 1
            updates.append((row_idx, product_id, 'タイトル', sheet_title, csv_title))
            pending_batch.append({
                'range': f'{Col.PAGE_TITLE.letter}{row_idx}',
                'values': [[csv_title]]
            })

        # メタディスクリプション（BP列）
        if csv_meta and csv_meta != sheet_meta:
            meta_changes += 1
            updates.append((row_idx, product_id, 'メタディス', sheet_meta, csv_meta))
            pending_batch.append({
                'range': f'{Col.META_DESC.letter}{row_idx}',
                'values': [[csv_meta]]
            })

    logger.info("")
    logger.info(f"=== 差分集計 ===")
    logger.info(f"  シート総行数: {len(cm_data) - 1}")
    logger.info(f"  CSVと一致する商品ID: {matched}件")
    logger.info(f"  タイトル更新対象: {title_changes}件")
    logger.info(f"  メタディス更新対象: {meta_changes}件")
    logger.info(f"  合計セル更新: {len(pending_batch)}セル")

    if not updates:
        logger.info("更新対象はありません")
        return

    # 変更内容を表示
    logger.info("")
    logger.info("=== 変更内容（先頭20件）===")
    for row_idx, pid, field, old, new in updates[:20]:
        logger.info(f"  行{row_idx} [ID:{pid}] {field}:")
        logger.info(f"    旧: {old[:80]}")
        logger.info(f"    新: {new[:80]}")
    if len(updates) > 20:
        logger.info(f"  ... 他 {len(updates) - 20} 件")

    if args.dry_run:
        logger.info("")
        logger.info("[DRY-RUN] シートへの書き込みは行いません")
        return

    # バッチ書き込み
    logger.info("")
    logger.info("=== 書き込み実行 ===")
    total_written = 0
    for i in range(0, len(pending_batch), BATCH_SIZE):
        batch = pending_batch[i:i + BATCH_SIZE]
        if batch_update_with_retry(cm_sheet, batch):
            total_written += len(batch)
            logger.info(f"  [{total_written}/{len(pending_batch)}] セル更新")
        time.sleep(1)

    logger.info("")
    logger.info(f"=== SEO反映 完了: {total_written}セル更新 ===")


if __name__ == "__main__":
    main()
