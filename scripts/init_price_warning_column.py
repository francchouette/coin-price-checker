"""新カラーミー商品管理シートに CD列(価格警告)を初期化

- ヘッダー行(CD1) に「価格警告」をセット
- 既存全行のCD列に警告数式を挿入（既存値がある場合はスキップ、--overwrite で強制上書き）

数式: =IF(AND(CB{r}>0,AI{r}>0,AI{r}>CB{r}*1.3),"⚠ +"&ROUND((AI{r}/CB{r}-1)*100,1)&"%","")

使い方:
  python scripts/init_price_warning_column.py --dry-run
  python scripts/init_price_warning_column.py
  python scripts/init_price_warning_column.py --overwrite
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.config import Config
from src.cm_sheet_columns import Col, get_cell

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def build_formula(row_num: int) -> str:
    r = row_num
    return (
        f'=IF(AND(CB{r}>0,AI{r}>0,AI{r}>CB{r}*1.2),'
        f'"⚠ +"&ROUND((AI{r}/CB{r}-1)*100,1)&"%","")'
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--overwrite", action="store_true", help="既存値があっても数式で上書き")
    args = p.parse_args()

    sc = SpreadsheetClient()
    sc.connect()
    sheet = sc._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

    # シート列数チェック→不足なら拡張（dry-runでも列追加自体はデータに影響しないので実行）
    needed_cols = Col.TOTAL_COLUMNS
    if sheet.col_count < needed_cols:
        diff = needed_cols - sheet.col_count
        logger.info(f"列数不足 ({sheet.col_count} → {needed_cols})。{diff}列を追加します（空列）")
        sheet.add_cols(diff)

    # ヘッダー (CD1) を設定
    header_letter = Col.PRICE_WARNING.letter  # 'CD'
    cur_header = sheet.get(f"{header_letter}1", value_render_option="FORMATTED_VALUE")
    cur_header_val = cur_header[0][0] if cur_header and cur_header[0] else ""
    if cur_header_val != Col.PRICE_WARNING.name:
        logger.info(f"ヘッダー設定: {header_letter}1 = '{Col.PRICE_WARNING.name}' (旧: '{cur_header_val}')")
        if not args.dry_run:
            sheet.update(f"{header_letter}1", [[Col.PRICE_WARNING.name]], value_input_option="USER_ENTERED")
    else:
        logger.info(f"ヘッダー既存: '{cur_header_val}'")

    # 既存行のCD列を取得
    all_rows = sheet.get_all_values()
    data_rows = len(all_rows) - 1
    logger.info(f"データ行数: {data_rows}")

    if data_rows <= 0:
        logger.info("データ行なし。終了")
        return

    cd_values = sheet.get(f"{header_letter}2:{header_letter}{len(all_rows)}", value_render_option="FORMULA")

    targets = []  # (row_num, current, new)
    for i, r in enumerate(all_rows[1:], start=2):
        # G列の商品IDが空の行はスキップ
        if not get_cell(r, Col.PRODUCT_ID):
            continue
        cd_idx = i - 2
        cur = cd_values[cd_idx][0] if cd_idx < len(cd_values) and cd_values[cd_idx] else ""
        if cur and not args.overwrite:
            continue
        new = build_formula(i)
        if cur != new:
            targets.append((i, cur, new))

    logger.info(f"数式挿入対象: {len(targets)}件")
    if targets[:5]:
        logger.info("先頭5件:")
        for row_n, cur, new in targets[:5]:
            cur_show = cur[:50] if cur else "(空)"
            logger.info(f"  行{row_n}: {cur_show} → {new[:80]}")

    if args.dry_run:
        logger.info("[DRY-RUN] 書き込みは行いません")
        return

    if not targets:
        logger.info("対象なし")
        return

    updates = [
        {"range": f"{Config.SHEET_COLORME_V2}!{header_letter}{row_n}", "values": [[new]]}
        for row_n, cur, new in targets
    ]
    CHUNK = 500
    total = 0
    for i in range(0, len(updates), CHUNK):
        chunk = updates[i:i+CHUNK]
        sheet.spreadsheet.values_batch_update({
            "data": chunk,
            "valueInputOption": "USER_ENTERED",  # 数式として評価
        })
        total += len(chunk)
        logger.info(f"  進捗: {total}/{len(updates)}件")
        time.sleep(0.5)

    logger.info(f"完了: {total}件のCD列に数式挿入")


if __name__ == "__main__":
    main()
