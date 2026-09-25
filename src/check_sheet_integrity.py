"""新カラーミー商品管理シートの数式整合性チェック

検出する問題:
1. #REF! を含む数式(参照先の削除で壊れたもの)
2. 別行の $J{N} を参照している数式(並び替え時の追従漏れ)

対象範囲: A2:CJ 全行

使い方:
    python -m src.check_sheet_integrity
"""
import argparse
import logging
import re
import sys
from collections import Counter

from .spreadsheet import SpreadsheetClient
from .config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def idx_to_letter(idx: int) -> str:
    result = ''
    idx += 1
    while idx > 0:
        idx -= 1
        result = chr(65 + idx % 26) + result
        idx //= 26
    return result


RANGE_RE = re.compile(r'\$?[A-Z]+\$?\d+\s*:\s*\$?[A-Z]+\$?\d*')
COL_RANGE_RE = re.compile(r'\$?[A-Z]+\s*:\s*\$?[A-Z]+')
CELL_RE = re.compile(r'(?<![\w\$])\$?([A-Z]+)\$?(\d+)')


def find_bad_refs(formula: str, current_row: int) -> list[str]:
    """近隣3行外のセル参照を返す(範囲リテラルは除外)"""
    cleaned = RANGE_RE.sub('', formula)
    cleaned = COL_RANGE_RE.sub('', cleaned)
    refs = CELL_RE.findall(cleaned)
    bad = []
    for col, row_str in refs:
        row_num = int(row_str)
        if row_num == 1:
            continue
        if abs(row_num - current_row) < 3:
            continue
        bad.append(f'{col}{row_num}')
    return bad


def check_sheet(sheet_name: str, ignore_cells: set[str] = None) -> tuple[list, list]:
    """指定シートの数式をスキャン。

    Returns:
        (ref_broken, row_mismatched) の2つのリスト
        - ref_broken: [(row, col_letter, formula_snippet), ...]
        - row_mismatched: [(row, col_letter, formula_snippet, bad_refs), ...]
    """
    ignore_cells = ignore_cells or set()

    client = SpreadsheetClient()
    if not client.connect():
        raise RuntimeError('スプレッドシートへの接続に失敗しました')

    ws = client._spreadsheet.worksheet(sheet_name)
    last_col_letter = idx_to_letter(87)  # A-CJ(88列)
    last_row = ws.row_count

    logger.info(f'シート "{sheet_name}" をスキャン中... (A2:{last_col_letter}{last_row})')
    formulas = ws.get(f'A2:{last_col_letter}{last_row}', value_render_option='FORMULA')

    ref_broken = []
    row_mismatched = []
    for i, row in enumerate(formulas):
        r = i + 2
        for j, val in enumerate(row):
            val_str = str(val)
            if not val_str.startswith('='):
                continue
            col_letter = idx_to_letter(j)
            cell_ref = f'{col_letter}{r}'
            if cell_ref in ignore_cells:
                continue
            if '#REF!' in val_str:
                ref_broken.append((r, col_letter, val_str[:150]))
                continue
            bad = find_bad_refs(val_str, r)
            if bad:
                row_mismatched.append((r, col_letter, val_str[:150], bad))

    return ref_broken, row_mismatched


def get_row_info(sheet_name: str, rows: list[int]) -> dict:
    """指定行の商品ID(G列)と商品名(H列)を取得"""
    if not rows:
        return {}
    client = SpreadsheetClient()
    client.connect()
    ws = client._spreadsheet.worksheet(sheet_name)
    all_values = ws.get_all_values()
    info = {}
    for r in rows:
        if r - 1 < len(all_values):
            row = all_values[r - 1]
            pid = row[6] if len(row) > 6 else ''
            name = row[7][:35] if len(row) > 7 else ''
            info[r] = (pid, name)
    return info


def main():
    parser = argparse.ArgumentParser(description='シート数式整合性チェック')
    parser.add_argument('--sheet', default=Config.SHEET_COLORME_V2,
                        help='対象シート名(デフォルト: 新カラーミー商品管理)')
    parser.add_argument('--ignore', nargs='*', default=[],
                        help='無視するセル(例: J506)')
    args = parser.parse_args()

    ignore_cells = set(args.ignore) | {'J506'}  # J506=J1036 は意図的コピー

    logger.info('=' * 60)
    logger.info(f'整合性チェック開始: {args.sheet}')
    logger.info('=' * 60)

    try:
        ref_broken, row_mismatched = check_sheet(args.sheet, ignore_cells)
    except Exception as e:
        logger.error(f'スキャン失敗: {e}')
        sys.exit(1)

    logger.info('')
    logger.info('--- 結果 ---')
    logger.info(f'#REF! 破損セル: {len(ref_broken)}件')
    logger.info(f'行ズレ参照セル: {len(row_mismatched)}件')

    if ignore_cells:
        logger.info(f'除外セル: {sorted(ignore_cells)}')

    # 詳細出力
    if ref_broken:
        logger.info('')
        logger.info('--- #REF! 破損 (先頭30件) ---')
        rows_affected = sorted(set(b[0] for b in ref_broken))
        info = get_row_info(args.sheet, rows_affected[:30])
        # 行別集計
        row_c = Counter(b[0] for b in ref_broken)
        for r in sorted(row_c.keys())[:30]:
            cols = ','.join(b[1] for b in ref_broken if b[0] == r)
            pid, name = info.get(r, ('', ''))
            logger.info(f'  行{r} [{cols}] ID={pid}: {name}')

    if row_mismatched:
        logger.info('')
        logger.info('--- 行ズレ参照 (先頭30件) ---')
        rows_affected = sorted(set(m[0] for m in row_mismatched))
        info = get_row_info(args.sheet, rows_affected[:30])
        row_c = Counter(m[0] for m in row_mismatched)
        for r in sorted(row_c.keys())[:30]:
            entries = [m for m in row_mismatched if m[0] == r]
            cols = ','.join(m[1] for m in entries)
            refs = ','.join(sorted(set(ref for m in entries for ref in m[3])))
            pid, name = info.get(r, ('', ''))
            logger.info(f'  行{r} [{cols}] → {refs}参照 ID={pid}: {name}')

    logger.info('')
    if not ref_broken and not row_mismatched:
        logger.info('✓ 数式は完全にクリーンです')
    else:
        logger.warning(f'⚠ 修復が必要な行が {len(set(b[0] for b in ref_broken) | set(m[0] for m in row_mismatched))} 行あります')

    logger.info('=' * 60)
    logger.info('整合性チェック完了')
    logger.info('=' * 60)

    # 破損がなければ 0、あれば 1 を返す
    sys.exit(0 if not ref_broken and not row_mismatched else 1)


if __name__ == '__main__':
    main()
