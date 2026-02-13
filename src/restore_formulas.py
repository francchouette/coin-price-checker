"""
クリアされた数式を復元するスクリプト

1. シートをFORMULAモードで読み取り
2. 数式があるべきセルで数式が消えている行を特定
3. テンプレート行から数式パターンを抽出し、全行に復元

安全性:
- 同一行内の計算式（T, V, AA-AD, AI, AJ）は無条件に復元
- 別シート参照式（J, U, W, X）は値が空の行のみ復元（既存URL値を壊さない）
"""

import argparse
import copy
import logging
import re
import time

from .spreadsheet import SpreadsheetClient
from .config import Config
from .cm_sheet_columns import Col

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 同一行内の計算式列（安全に復元可能）
SAFE_FORMULA_COLS = {
    Col.PURCHASE_PRICE_JPY.index,   # T: =N*S
    Col.PURCHASE_TOTAL.index,       # V: =T*U
    Col.SHIPPING.index,             # Y: 送料（数式）
    Col.FEE.index,                  # Z: 諸経費（数式）
    Col.TOTAL_COST.index,           # AA: =V+Y+Z
    Col.PROPER_PRICE.index,         # AB: =AA/(2-W)+Y+Z
    Col.GROSS_PROFIT.index,         # AC: =AB-AA
    Col.GROSS_PROFIT_RATE.index,    # AD: =(AB-AA)/AB
    Col.SALES_PRICE.index,          # AE: 販売価格（数式で計算）
    Col.TAX_INCLUDED_PRICE.index,   # AI: =AE*1.1
    Col.TAX_AMOUNT.index,           # AJ: =AI-AE
}

# 別シート参照式列（値が空の行のみ復元）
CROSS_SHEET_COLS = {
    Col.SUPPLIER_URL.index,     # J: =VLOOKUP(...)
    Col.QUANTITY.index,         # U: =vlookup(...)
    Col.MARGIN_RATE.index,      # W: =vlookup(...)
    Col.MARGIN_AMOUNT.index,    # X: =vlookup(...)
}


def adjust_formula_row(formula: str, template_row: int, target_row: int) -> str:
    """数式内の行番号をテンプレート行からターゲット行に変換"""
    if not formula or not formula.startswith("="):
        return formula

    def replace_row(match):
        col_part = match.group(1)
        row_part = match.group(2)
        if row_part.startswith("$"):
            return match.group(0)  # 絶対参照はそのまま
        if int(row_part) == template_row:
            return f"{col_part}{target_row}"
        return match.group(0)

    pattern = r'(\$?[A-Z]+)(\$?\d+)'
    return re.sub(pattern, replace_row, formula)


def main():
    parser = argparse.ArgumentParser(description="クリアされた数式を復元")
    parser.add_argument("--dry-run", action="store_true", help="実際の書き込みは行わない")
    parser.add_argument("--check-only", action="store_true", help="状態確認のみ（書き込みなし）")
    parser.add_argument("--include-cross-sheet", action="store_true",
                        help="別シート参照式（J,U,W,X列）も復元する（空セルのみ）")
    args = parser.parse_args()

    logger.info("=== 数式復元スクリプト ===")

    # スプレッドシート接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシート接続失敗")
        return
    sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

    # FORMULAモードで全データ読み取り
    logger.info("シートデータ読み取り中（FORMULAモード）...")
    formula_data = sheet.get_all_values(value_render_option='FORMULA')
    logger.info(f"読み取り完了: {len(formula_data)}行")

    if len(formula_data) < 3:
        logger.error("データが少なすぎます")
        return

    # 通常値も取得（cross-sheet判定用）
    computed_data = None
    if args.include_cross_sheet:
        logger.info("通常値読み取り中（cross-sheet判定用）...")
        computed_data = sheet.get_all_values()

    data_rows = formula_data[1:]  # ヘッダーを除く

    # 復元対象の列を決定
    target_col_indices = set(SAFE_FORMULA_COLS)
    if args.include_cross_sheet:
        target_col_indices |= CROSS_SHEET_COLS

    # --- 列ごとの数式状態をスキャン ---
    logger.info("\n=== 列ごとの数式保有率 ===")
    formula_columns = {}

    all_cols = Col.all_columns()
    for col_idx in range(min(Col.TOTAL_COLUMNS, len(formula_data[0]))):
        formula_count = 0
        first_formula = None
        first_formula_sheet_row = None

        for row_idx, row in enumerate(data_rows):
            sheet_row = row_idx + 2
            if col_idx < len(row):
                val = str(row[col_idx]) if row[col_idx] is not None else ""
                if val.startswith("="):
                    formula_count += 1
                    if first_formula is None:
                        first_formula = val
                        first_formula_sheet_row = sheet_row

        if formula_count == 0:
            continue

        total = len(data_rows)
        pct = formula_count * 100 // total

        if pct < 10:
            continue

        col_letter = all_cols[col_idx].letter if col_idx < len(all_cols) else f"#{col_idx}"
        col_name = all_cols[col_idx].name if col_idx < len(all_cols) else "不明"

        # 復元対象かどうか
        is_target = col_idx in target_col_indices
        is_cross_sheet = col_idx in CROSS_SHEET_COLS

        # 数式がない行を特定
        missing_rows = []
        for row_idx, row in enumerate(data_rows):
            sheet_row = row_idx + 2
            val = str(row[col_idx]) if (col_idx < len(row) and row[col_idx] is not None) else ""
            if val.startswith("="):
                continue  # 数式あり → OK

            # cross-sheet列: 既に値がある場合はスキップ（URLや数値を壊さない）
            if is_cross_sheet:
                computed_val = ""
                if computed_data and (row_idx + 1) < len(computed_data):
                    comp_row = computed_data[row_idx + 1]
                    if col_idx < len(comp_row):
                        computed_val = str(comp_row[col_idx]).strip()
                if computed_val:
                    continue  # 値がある → 復元しない

            missing_rows.append(sheet_row)

        missing = len(missing_rows)
        marker = " ★復元対象" if is_target and missing > 0 else ""
        if is_cross_sheet and not args.include_cross_sheet:
            marker = " (別シート参照・スキップ)"

        logger.info(f"  {col_letter}列({col_name}): {formula_count}/{total}行に数式あり ({pct}%) 欠落:{total - formula_count}行{marker}")

        if is_target and missing > 0:
            formula_columns[col_idx] = {
                "template_formula": first_formula,
                "template_row": first_formula_sheet_row,
                "missing_rows": missing_rows,
                "col_letter": col_letter,
                "col_name": col_name,
            }

    if not formula_columns:
        logger.info("\n復元が必要な列はありません。")
        return

    # --- 復元対象のサマリー ---
    logger.info("\n=== 復元対象 ===")
    total_restore = 0
    for col_idx, info in sorted(formula_columns.items()):
        missing = len(info["missing_rows"])
        logger.info(f"  {info['col_letter']}列({info['col_name']}): {missing}行を復元")
        logger.info(f"    テンプレート数式（行{info['template_row']}）: {info['template_formula'][:80]}")
        total_restore += missing

    logger.info(f"\n合計: {total_restore}セルの数式を復元します")

    if args.check_only:
        logger.info("--check-only: 確認のみで終了")
        return

    # --- 数式復元バッチ構築 ---
    batch_data = []
    for col_idx, info in sorted(formula_columns.items()):
        template_formula = info["template_formula"]
        template_row = info["template_row"]
        col_letter = info["col_letter"]

        for target_row in info["missing_rows"]:
            restored_formula = adjust_formula_row(template_formula, template_row, target_row)
            batch_data.append({
                'range': f'{col_letter}{target_row}',
                'values': [[restored_formula]]
            })

    if not batch_data:
        logger.info("復元対象がありません")
        return

    if args.dry_run:
        logger.info(f"\n--dry-run: {len(batch_data)}セルの復元内容:")
        for item in batch_data[:20]:
            logger.info(f"  {item['range']}: {item['values'][0][0][:80]}")
        if len(batch_data) > 20:
            logger.info(f"  ... 他{len(batch_data) - 20}件")
        return

    # --- バッチ書き込み ---
    logger.info(f"\n{len(batch_data)}セルの数式を復元中...")
    CHUNK_SIZE = 100
    for i in range(0, len(batch_data), CHUNK_SIZE):
        chunk = batch_data[i:i + CHUNK_SIZE]
        for attempt in range(3):
            try:
                data_copy = copy.deepcopy(chunk)
                sheet.batch_update(data_copy, value_input_option='USER_ENTERED')
                logger.info(f"  書き込み完了: {i + len(chunk)}/{len(batch_data)}セル")
                break
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"  エラー、リトライ: {e}")
                    time.sleep(10)
                else:
                    logger.error(f"  3回失敗: {e}")
                    raise
        time.sleep(1)

    logger.info(f"\n=== 数式復元完了: {len(batch_data)}セル ===")


if __name__ == "__main__":
    main()
