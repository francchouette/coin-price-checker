"""新カラーミー商品管理シートの行アンカー付き参照を列全体参照に置き換える

背景:
    行ヘッダのドラッグ移動を行うと、数式内の行アンカー($付き行番号)がGoogleシート側で
    自動的に書き換えられ、参照範囲が静かに縮む/ズレる。
        $J$2:$J$1045 → $J$2:$J$1044 (先頭行を移動)
        $J$2:$J$1045 → $J$3:$J$1045 (中間行を先頭へ移動)
        H$2:H        → H$3:H
    列全体参照($J:$J / H:H)には行アンカーが無いため、ドラッグでも並び替えでも変化しない。

対象:
    BD列(備考)  : COUNTIFS($J$2:$J$N, ..., $U$2:$U$N, 1) → COUNTIFS($J:$J, ..., $U:$U, 1)
    CE列(重複)  : COUNTIF/FILTER の H$2:H → H:H
    J504        : 他行参照 =J1034 → 実値(並び替えで壊れるため)

BD列/CE列はカラーミーへ送信されないシート内部専用列。

使い方:
    python -m scripts.fix_anchored_ranges --dry-run
    python -m scripts.fix_anchored_ranges --backup
"""
import argparse
import logging
import re
import sys
import time

sys.path.insert(0, '/Users/user/coin-price-checker')
from src.spreadsheet import SpreadsheetClient
from src.config import Config
from src.cm_sheet_columns import Col

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

FIRST_ROW = 2
CROSS_ROW_CELL = 'J504'  # 他行参照(=J1034)を実値化する対象

# BD列: $J$2:$J$N / $U$2:$U$N → $J:$J / $U:$U
BD_PATTERNS = [
    (re.compile(r'\$J\$\d+:\$J\$\d+'), '$J:$J'),
    (re.compile(r'\$U\$\d+:\$U\$\d+'), '$U:$U'),
]
# 置換後に行アンカーが残っていないか検査するパターン(criteria の J500 等は行アンカー無しなのでOK)
ANCHOR_LEFT = re.compile(r'\$[A-Z]{1,2}\$\d+|[A-Z]{1,2}\$\d+')


def idx_to_letter(idx: int) -> str:
    s = ''
    idx += 1
    while idx > 0:
        idx -= 1
        s = chr(65 + idx % 26) + s
        idx //= 26
    return s


def convert(formula: str, patterns) -> str:
    out = formula
    for pat, rep in patterns:
        out = pat.sub(rep, out)
    return out


def collect(formulas, col_idx, patterns, label):
    """(row, old, new) のリストと、想定外だった数式のリストを返す"""
    changes, unexpected = [], []
    for i, row in enumerate(formulas):
        sheet_row = i + FIRST_ROW
        val = row[col_idx] if col_idx < len(row) else ''
        val = str(val) if val is not None else ''
        if not val.startswith('='):
            continue
        new = convert(val, patterns)
        if new == val:
            # 変換対象が無い数式 → 想定外の形なので報告(触らない)
            if ANCHOR_LEFT.search(val):
                unexpected.append((sheet_row, val[:120]))
            continue
        if ANCHOR_LEFT.search(new):
            unexpected.append((sheet_row, f'変換後もアンカー残存: {new[:110]}'))
            continue
        changes.append((sheet_row, val, new))
    logger.info(f'{label}: 変換対象 {len(changes)}件 / 想定外 {len(unexpected)}件')
    return changes, unexpected


def to_batch(changes, col_idx):
    """連続行をまとめて batch_update 用データにする"""
    letter = idx_to_letter(col_idx)
    data, run = [], []

    def flush():
        if not run:
            return
        start = run[0][0]
        end = run[-1][0]
        data.append({'range': f'{letter}{start}:{letter}{end}',
                     'values': [[new] for _, _, new in run]})

    for ch in changes:
        if run and ch[0] != run[-1][0] + 1:
            flush()
            run.clear()
        run.append(ch)
    flush()
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='書き込みせず差分のみ表示')
    ap.add_argument('--backup', action='store_true', help='実行前にシートを複製してバックアップ')
    ap.add_argument('--sheet', default=Config.SHEET_COLORME_V2)
    args = ap.parse_args()

    client = SpreadsheetClient()
    if not client.connect():
        raise SystemExit('スプレッドシートへの接続に失敗しました')
    ws = client._spreadsheet.worksheet(args.sheet)

    last_col = idx_to_letter(Col.TOTAL_COLUMNS - 1)
    last_row = ws.row_count
    logger.info(f'読み込み中: {args.sheet} A{FIRST_ROW}:{last_col}{last_row}')
    formulas = ws.get(f'A{FIRST_ROW}:{last_col}{last_row}', value_render_option='FORMULA')
    values = ws.get(f'A{FIRST_ROW}:{last_col}{last_row}', value_render_option='UNFORMATTED_VALUE')

    bd_changes, bd_bad = collect(formulas, Col.MEMO.index, BD_PATTERNS, 'BD列(備考)')

    # J504: 他行参照を実値化
    j_row = int(CROSS_ROW_CELL[1:])
    j_idx = j_row - FIRST_ROW
    j_formula = ''
    j_value = ''
    if 0 <= j_idx < len(formulas):
        r = formulas[j_idx]
        j_formula = str(r[Col.SUPPLIER_URL.index]) if Col.SUPPLIER_URL.index < len(r) else ''
        rv = values[j_idx]
        j_value = str(rv[Col.SUPPLIER_URL.index]) if Col.SUPPLIER_URL.index < len(rv) else ''
    j_target = j_formula.startswith('=') and j_value

    for label, bad in (('BD列', bd_bad),):
        if bad:
            logger.warning(f'--- {label} 想定外の数式(変更しません) ---')
            for r, v in bad[:10]:
                logger.warning(f'   行{r}: {v}')

    logger.info('')
    logger.info('--- 変更サンプル ---')
    for label, ch in (('BD', bd_changes),):
        if ch:
            r, old, new = ch[0]
            logger.info(f'{label} 行{r}')
            logger.info(f'   前: {old[:130]}')
            logger.info(f'   後: {new[:130]}')
    if j_target:
        logger.info(f'{CROSS_ROW_CELL}')
        logger.info(f'   前: {j_formula}')
        logger.info(f'   後: {j_value}')

    total = len(bd_changes) + (1 if j_target else 0)
    logger.info('')
    logger.info(f'変更セル合計: {total}件')

    if args.dry_run:
        logger.info('ドライランのため書き込みは行いませんでした')
        return

    if not total:
        logger.info('変更対象がありません')
        return

    if args.backup:
        name = f'{args.sheet}_bk_{time.strftime("%Y%m%d_%H%M%S")}'
        client._spreadsheet.duplicate_sheet(ws.id, new_sheet_name=name)
        logger.info(f'バックアップ作成: {name}')

    batch = to_batch(bd_changes, Col.MEMO.index)
    if j_target:
        batch.append({'range': CROSS_ROW_CELL, 'values': [[j_value]]})

    logger.info(f'書き込み中: {len(batch)}レンジ')
    for i in range(0, len(batch), 50):
        chunk = batch[i:i + 50]
        ws.batch_update(chunk, value_input_option='USER_ENTERED')
        logger.info(f'  {min(i + 50, len(batch))}/{len(batch)} レンジ完了')
        time.sleep(1)

    logger.info('完了')


if __name__ == '__main__':
    main()
