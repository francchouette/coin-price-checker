"""新カラーミー商品管理シートの価格カスケード健全性チェック

check_sheet_integrity.py が「数式の壊れ方」(#REF!/行ズレ)を見るのに対し、
こちらは「数式そのものが消えている」「参照先が無い」といった
価格が計算できなくなる状態を検出する。

検出する問題:
  [深刻] 販売価格(AE)が 0 または ¥660以下 → 同期で価格更新がスキップされ続ける
  [深刻] J列(仕入れ先URL)はあるのに 商品仕入れ先一覧 に該当URLが無い（孤立行）
  [警告] 必須の数式列に数式が入っていない（S/K/L/M/N/T/V/AA/AB/AE/AI 等）
  [警告] 手入力列 W(マージン率)/Y(送料)/Z(諸経費) が空
  [警告] セルにエラー値(#REF! #DIV/0! #N/A #VALUE!)が出ている
  [情報] J列が空（仕入れ先に紐づかない商品。無償提供品など）

背景:
  2026-09-09 に行820/821 の S・W・X・Y・Z列が値ごと消え、販売価格が0になっていた。
  価格0は sync_colorme_products 側でスキップされるため表面化せず、
  該当商品だけ価格が凍結されたまま気づけない状態だった。

使い方:
    python -m src.check_price_cascade
    python -m src.check_price_cascade --quiet          # 問題のみ表示
    python -m src.check_price_cascade --ignore-rows 1224,1225
"""
import argparse
import logging
import re
import sys
from collections import Counter

from .spreadsheet import SpreadsheetClient
from .config import Config
from .cm_sheet_columns import Col

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

# 価格が660円以下 = カスケード失敗の典型（N空→原価250→適正537→AE600→AI660）
PRICE_FLOOR = 660

ERROR_VALUES = ('#REF!', '#DIV/0!', '#N/A', '#VALUE!', '#NAME?', '#NUM!')

# 数式が入っているべき列
# Q(取引通貨)は静的値("SGD"等)の行が大半で、価格計算(T=N*S)にも使われないため対象外
FORMULA_COLS = [
    Col.SUPPLIER_NAME,        # K
    Col.SUPPLIER_SITE,        # L
    Col.SUPPLIER_STOCK,       # M
    Col.SUPPLIER_PRICE,       # N
    Col.EXCHANGE_RATE,        # S
    Col.PURCHASE_PRICE_JPY,   # T
    Col.PURCHASE_TOTAL,       # V
    Col.TOTAL_COST,           # AA
    Col.PROPER_PRICE,         # AB
    Col.SALES_PRICE,          # AE
    Col.TAX_INCLUDED_PRICE,   # AI
]

# 手入力で埋まっているべき列
MANUAL_COLS = [
    Col.MARGIN_RATE,  # W
    Col.SHIPPING,     # Y
    Col.FEE,          # Z
]


def _cell(rows, i, col):
    row = rows[i] if i < len(rows) else []
    return str(row[col.index]) if col.index < len(row) else ''


def _num(value):
    try:
        return float(str(value).replace(',', '').strip() or 0)
    except ValueError:
        return None


def check(sheet_name: str, ignore_rows: set) -> dict:
    client = SpreadsheetClient()
    if not client.connect():
        raise RuntimeError('スプレッドシートへの接続に失敗しました')

    ws = client._spreadsheet.worksheet(sheet_name)
    last_col = Col.last_column_letter()
    last_row = ws.row_count
    logger.info(f'シート "{sheet_name}" を読み込み中... (A2:{last_col}{last_row})')
    values = ws.get(f'A2:{last_col}{last_row}', value_render_option='FORMATTED_VALUE')
    formulas = ws.get(f'A2:{last_col}{last_row}', value_render_option='FORMULA')

    supplier_ws = client._spreadsheet.worksheet(Config.SHEET_SUPPLIERS)
    supplier_urls = {
        (r[2].strip() if len(r) > 2 else '')
        for r in supplier_ws.get_all_values()[1:]
    }
    supplier_urls.discard('')
    logger.info(f'商品仕入れ先一覧: URL {len(supplier_urls)}件')

    critical, warning, info = [], [], []

    for i in range(len(values)):
        row_num = i + 2
        if row_num in ignore_rows:
            continue
        product_id = _cell(values, i, Col.PRODUCT_ID).strip()
        if not product_id:
            continue

        name = _cell(values, i, Col.NAME)[:38]
        url = _cell(values, i, Col.SUPPLIER_URL).strip()
        price_update = _cell(values, i, Col.PRICE_UPDATE).strip()

        if not url:
            info.append((row_num, product_id, name, 'J列が空（仕入れ先に紐づかない商品）'))
            continue

        if url not in supplier_urls:
            critical.append((row_num, product_id, name,
                             '商品仕入れ先一覧にURLが無い（孤立行→価格計算不能）'))

        sales_price = _num(_cell(values, i, Col.SALES_PRICE))
        if sales_price is not None and sales_price <= PRICE_FLOOR:
            critical.append((row_num, product_id, name,
                             f'販売価格AE={sales_price:,.0f} '
                             f'（価格更新={price_update or "?"}／同期でスキップされ続けます）'))

        missing_formula = [c.letter for c in FORMULA_COLS
                           if not _cell(formulas, i, c).strip().startswith('=')]
        if missing_formula:
            warning.append((row_num, product_id, name,
                            f'数式が消えている列: {",".join(missing_formula)}'))

        missing_manual = [c.letter for c in MANUAL_COLS if not _cell(values, i, c).strip()]
        if missing_manual:
            warning.append((row_num, product_id, name,
                            f'手入力列が空: {",".join(missing_manual)}'))

        bad_cells = []
        for j, cell in enumerate(values[i] if i < len(values) else []):
            if any(e in str(cell) for e in ERROR_VALUES):
                bad_cells.append(f'{_letter(j)}={str(cell).strip()}')
        if bad_cells:
            warning.append((row_num, product_id, name,
                            f'エラー値: {" ".join(bad_cells[:4])}'))

    return {'critical': critical, 'warning': warning, 'info': info,
            'total': sum(1 for i in range(len(values))
                         if _cell(values, i, Col.PRODUCT_ID).strip())}


def _letter(idx: int) -> str:
    s = ''
    idx += 1
    while idx > 0:
        idx -= 1
        s = chr(65 + idx % 26) + s
        idx //= 26
    return s


def _kind(detail: str) -> str:
    """明細を種類別集計用のキーに正規化する"""
    if detail.startswith('エラー値:'):
        errs = sorted({e for e in ERROR_VALUES if e in detail})
        cols = sorted({m.group(1) for m in re.finditer(r'([A-Z]{1,2})=', detail)})
        return f'エラー値 {",".join(errs)}（{",".join(cols)}列）'
    return detail.split('：')[0].split(':')[0] + ':' + detail.split(':', 1)[1] \
        if ':' in detail else detail


def _report(title: str, items: list, limit: int = 30):
    if not items:
        return
    logger.info('')
    logger.info(f'--- {title}（{len(items)}件） ---')
    counts = Counter(_kind(d) for _, _, _, d in items)
    logger.info('  [種類別集計]')
    for kind, n in counts.most_common():
        logger.info(f'    {n:>4}件  {kind}')
    logger.info(f'  [明細 先頭{min(limit, len(items))}件]')
    for row_num, pid, name, detail in items[:limit]:
        logger.info(f'  行{row_num} [ID:{pid}] {name}')
        logger.info(f'      {detail}')
    if len(items) > limit:
        logger.info(f'  ...他 {len(items) - limit}件（--limit で増やせます）')


def main():
    parser = argparse.ArgumentParser(description='価格カスケード健全性チェック')
    parser.add_argument('--sheet', default=Config.SHEET_COLORME_V2)
    parser.add_argument('--quiet', action='store_true', help='情報レベルを表示しない')
    parser.add_argument('--ignore-rows', default='', help='除外する行番号（カンマ区切り）')
    parser.add_argument('--limit', type=int, default=30, help='明細の表示件数（デフォルト30）')
    args = parser.parse_args()

    ignore_rows = {int(r.strip()) for r in args.ignore_rows.split(',') if r.strip().isdigit()}

    logger.info('=' * 60)
    logger.info(f'価格カスケードチェック開始: {args.sheet}')
    logger.info('=' * 60)

    try:
        result = check(args.sheet, ignore_rows)
    except Exception as e:
        logger.error(f'チェック失敗: {e}')
        sys.exit(2)

    logger.info('')
    logger.info('--- 結果 ---')
    logger.info(f'対象商品: {result["total"]}件')
    logger.info(f'[深刻] {len(result["critical"])}件  価格が計算できていない')
    logger.info(f'[警告] {len(result["warning"])}件  数式や手入力値の欠落')
    logger.info(f'[情報] {len(result["info"])}件  仕入れ先に紐づかない商品')

    _report('[深刻] 価格が計算できていない', result['critical'], args.limit)
    _report('[警告] 数式・手入力値の欠落', result['warning'], args.limit)
    if not args.quiet:
        _report('[情報] 仕入れ先に紐づかない商品', result['info'], limit=10)

    logger.info('')
    if not result['critical'] and not result['warning']:
        logger.info('✓ 価格カスケードは健全です')
    elif not result['critical']:
        logger.info('△ 深刻な問題はありませんが、警告を確認してください')
    else:
        logger.warning('⚠ 価格が計算できていない商品があります。修復してください')
    logger.info('=' * 60)

    sys.exit(1 if result['critical'] else 0)


if __name__ == '__main__':
    main()
