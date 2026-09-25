"""
新カラーミー商品管理シートの J列（仕入れ先商品URL）を自動補完

「商品仕入れ先一覧」シートをソースとして、G列（カラーミー商品ID）でマッチングし、
J列が空の行にC列（仕入れ先商品URL）を書き込む。

使い方:
  python -m src.fill_supplier_url --dry-run --verbose       # 変更対象を表示のみ
  python -m src.fill_supplier_url --verbose                  # 実際に書き込み
  python -m src.fill_supplier_url --start-row 1145 --dry-run # 特定行以降のみ対象
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

SUPPLIER_SHEET = '商品仕入れ先一覧'
BATCH_SIZE = 30
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


def fill_supplier_urls(
    client: SpreadsheetClient,
    dry_run: bool = False,
    overwrite: bool = False,
    start_row: int = 2,
) -> tuple[int, int]:
    """
    新カラーミー商品管理シートの J列（仕入れ先URL）を補完する。
    商品仕入れ先一覧のS列(カラーミーID)→C列(仕入れ先URL)マップを使用。

    Args:
        client: 接続済み SpreadsheetClient
        dry_run: True で書き込みなし
        overwrite: True で既存値も上書き
        start_row: 処理開始行（1-indexed）

    Returns:
        (補完件数, マップにないID件数)
    """
    # 商品仕入れ先一覧シート読み込み → カラーミーID→URLマップ
    logger.info(f"「{SUPPLIER_SHEET}」シートを読み込み中...")
    sp_sheet = client._spreadsheet.worksheet(SUPPLIER_SHEET)
    sp_data = sp_sheet.get('A2:T10000')

    # S列(index 18): カラーミー商品ID, C列(index 2): 仕入れ先商品URL
    sp_map = {}
    for row in sp_data:
        if len(row) < 19:
            continue
        cm_id = row[18].strip() if len(row) > 18 else ''
        url = row[2].strip() if len(row) > 2 else ''
        if cm_id and cm_id.isdigit() and url:
            sp_map[cm_id] = url

    logger.info(f"マップ構築: {len(sp_map)}件のカラーミーID→URL")

    # 新カラーミー商品管理シート読み込み
    cm_sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    cm_data = cm_sheet.get_all_values()
    logger.info(f"新カラーミー商品管理: {len(cm_data) - 1}行")

    # 差分抽出
    targets = []  # [(row_num, product_id, current_j, new_url), ...]
    no_match = []  # マッチしないID

    for row_idx, row in enumerate(cm_data[1:], start=2):
        if row_idx < start_row:
            continue
        cm_id = get_cell(row, Col.PRODUCT_ID)
        if not cm_id:
            continue
        current_j = get_cell(row, Col.SUPPLIER_URL)

        if not overwrite and current_j:
            continue

        if cm_id in sp_map:
            new_url = sp_map[cm_id]
            if new_url != current_j:
                targets.append((row_idx, cm_id, current_j, new_url))
        else:
            no_match.append((row_idx, cm_id))

    logger.info("")
    logger.info("=== 集計 ===")
    logger.info(f"  補完対象（J列を更新）: {len(targets)}件")
    logger.info(f"  マップにない商品ID: {len(no_match)}件")

    if targets:
        logger.info("")
        logger.info("=== 補完内容（先頭20件）===")
        for row_idx, pid, old, new in targets[:20]:
            old_display = f'"{old[:40]}..."' if old else '(空)'
            logger.info(f"  行{row_idx} [ID:{pid}]")
            logger.info(f"    旧: {old_display}")
            logger.info(f"    新: {new[:70]}")
        if len(targets) > 20:
            logger.info(f"  ... 他 {len(targets) - 20} 件")

    if no_match:
        logger.info("")
        logger.info("=== マップにない商品ID（先頭10件、手動入力が必要）===")
        for row_idx, pid in no_match[:10]:
            product_name = get_cell(cm_data[row_idx - 1], Col.NAME)[:40]
            logger.info(f"  行{row_idx} [ID:{pid}] {product_name}")
        if len(no_match) > 10:
            logger.info(f"  ... 他 {len(no_match) - 10} 件")

    if dry_run:
        logger.info("")
        logger.info("[DRY-RUN] シートへの書き込みは行いません")
        return (len(targets), len(no_match))

    if not targets:
        logger.info("更新対象がないため終了します")
        return (0, len(no_match))

    # バッチ書き込み
    logger.info("")
    logger.info("=== 書き込み実行 ===")
    pending_batch = []
    for row_idx, pid, old, new_url in targets:
        pending_batch.append({
            'range': f'{Col.SUPPLIER_URL.letter}{row_idx}',
            'values': [[new_url]]
        })

    total_written = 0
    for i in range(0, len(pending_batch), BATCH_SIZE):
        batch = pending_batch[i:i + BATCH_SIZE]
        if batch_update_with_retry(cm_sheet, batch):
            total_written += len(batch)
            logger.info(f"  [{total_written}/{len(pending_batch)}] セル更新")
        time.sleep(1)

    logger.info("")
    logger.info(f"=== 補完完了: {total_written}セル更新 ===")
    return (total_written, len(no_match))


def main():
    parser = argparse.ArgumentParser(description="仕入れ先URL（J列）の自動補完")
    parser.add_argument("--dry-run", action="store_true", help="実際の書き込みをせず、変更対象を表示のみ")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    parser.add_argument("--start-row", type=int, default=2, help="処理開始行（ヘッダー含む1-indexed、デフォルト: 2）")
    parser.add_argument("--overwrite", action="store_true", help="既にJ列に値があっても上書き（デフォルトは空欄のみ補完）")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY-RUN（書き込みなし）" if args.dry_run else "本実行"
    logger.info(f"=== 仕入れ先URL補完 開始 [{mode}] ===")
    if args.overwrite:
        logger.info("上書きモード: 既存値があっても上書きします")
    else:
        logger.info("空欄のみ補完モード（デフォルト）")

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    fill_supplier_urls(
        client=client,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        start_row=args.start_row,
    )


if __name__ == "__main__":
    main()
