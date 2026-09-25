"""長期在庫切れ（仕入れ先OOS）商品のマーキング

仕入れ先で長期間 Out of Stock が続いている商品を洗い出し、手動での削除判断に使う。
削除は行わない（運用メモ「不要商品を完全削除する手順」に従い、カラーミー→シートの順で手動）。

新カラーミー商品管理シートに3列を追加して記録する（既存のA-CJ列には一切触れない）:
    CK列 仕入先OOS開始日   最後に In Stock だった日の翌日
    CL列 OOS日数          実行時点での経過日数（静的な数値。数式は使わない）
    CM列 長期OOS          しきい値以上なら「要確認」

数式を使わない理由:
    TODAY() のような揮発性関数を1200行に入れるとシート全体の再計算が重くなるため、
    日数はこのスクリプトが実行のたびに静的な値として書き込む。

初回は --backfill でログから過去にさかのぼって開始日を復元できる。
logs/sync-all-*.log には商品ごとの「在庫状態: In Stock / Out of Stock」が記録されている。

使い方:
    python -m src.mark_long_oos --backfill --dry-run   # 初回: ログから復元して確認
    python -m src.mark_long_oos --backfill             # 初回: 実行
    python -m src.mark_long_oos                        # 以降: 同期後に実行
    python -m src.mark_long_oos --threshold 90         # しきい値変更
"""
import argparse
import datetime
import glob
import logging
import os
import re
import subprocess
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

DEFAULT_THRESHOLD = 60
MARK = '要確認'

# 既存の定義列(A-CJ=88列)の直後に追加する。既存コードはA-CJしか読み書きしないため干渉しない。
COL_OOS_SINCE = Col.TOTAL_COLUMNS          # CK
COL_OOS_DAYS = Col.TOTAL_COLUMNS + 1       # CL
COL_OOS_MARK = Col.TOTAL_COLUMNS + 2       # CM
HEADERS = ('仕入先OOS開始日', 'OOS日数', '長期OOS')

IN_STOCK = 'In Stock'
OUT_OF_STOCK = 'Out of Stock'


def idx_to_letter(idx: int) -> str:
    s = ''
    idx += 1
    while idx > 0:
        idx -= 1
        s = chr(65 + idx % 26) + s
        idx //= 26
    return s


def parse_logs(logs_dir: str) -> tuple[dict, str]:
    """sync-all-*.log から 商品ID → 最後に In Stock だった日付(YYYYMMDD) を復元する。

    Returns:
        (last_in_stock, 最古のログ日付, ログに現れた商品IDの集合)
    """
    files = sorted(glob.glob(os.path.join(logs_dir, 'sync-all-*.log')))
    if not files:
        raise RuntimeError(f'ログが見つかりません: {logs_dir}/sync-all-*.log')

    logger.info(f'ログ解析中: {len(files)}ファイル')
    # grep で必要な2種類の行だけ取り出す（1.5GB を全部Pythonで読むと遅いため）
    pattern = r'\(ID: [0-9]+\)|在庫状態: (In Stock|Out of Stock)'
    last_in, seen = {}, set()
    date_re = re.compile(r'sync-all-(\d{8})_')

    for path in files:
        m = date_re.search(os.path.basename(path))
        if not m:
            continue
        day = m.group(1)
        try:
            out = subprocess.run(['grep', '-aoE', pattern, path],
                                 capture_output=True, text=True, timeout=120).stdout
        except subprocess.TimeoutExpired:
            logger.warning(f'  タイムアウトのためスキップ: {os.path.basename(path)}')
            continue

        product_id = None
        for line in out.splitlines():
            if line.startswith('(ID:'):
                product_id = re.sub(r'\D', '', line)
            elif product_id:
                seen.add(product_id)
                if IN_STOCK in line:
                    if last_in.get(product_id, '') < day:
                        last_in[product_id] = day
                product_id = None

    oldest = date_re.search(os.path.basename(files[0])).group(1)
    logger.info(f'  ログに現れた商品: {len(seen)}件 / うち In Stock の記録あり: {len(last_in)}件')
    logger.info(f'  ログ期間: {oldest} 〜')
    return last_in, oldest, seen


def _to_date(yyyymmdd: str) -> datetime.date:
    return datetime.date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))


def _cell(row, idx):
    return str(row[idx]).strip() if idx < len(row) else ''


def main():
    parser = argparse.ArgumentParser(description='長期在庫切れ商品のマーキング')
    parser.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD,
                        help=f'マークする日数（デフォルト{DEFAULT_THRESHOLD}日）')
    parser.add_argument('--backfill', action='store_true',
                        help='ログから過去にさかのぼって開始日を復元する（初回のみ）')
    parser.add_argument('--logs-dir', default=os.path.join(os.path.dirname(__file__), '..', 'logs'))
    parser.add_argument('--dry-run', action='store_true', help='書き込まず差分のみ表示')
    parser.add_argument('--sheet', default=Config.SHEET_COLORME_V2)
    args = parser.parse_args()

    today = datetime.date.today()

    last_in, oldest_log, seen_in_logs = ({}, None, set())
    if args.backfill:
        last_in, oldest_log, seen_in_logs = parse_logs(os.path.abspath(args.logs_dir))

    client = SpreadsheetClient()
    if not client.connect():
        raise SystemExit('スプレッドシートへの接続に失敗しました')
    ws = client._spreadsheet.worksheet(args.sheet)

    # 追加列の確保（ドライラン時は拡張しないので、以降その列は「無い」前提で扱う）
    need_cols = COL_OOS_MARK + 1
    cols_exist = ws.col_count >= need_cols
    if not cols_exist:
        logger.info(f'列を拡張: {ws.col_count} → {need_cols}'
                    + ('（ドライランのため実行しません）' if args.dry_run else ''))
        if not args.dry_run:
            ws.add_cols(need_cols - ws.col_count)
            cols_exist = True

    first_letter = idx_to_letter(COL_OOS_SINCE)
    last_letter = idx_to_letter(COL_OOS_MARK)

    header = []
    if cols_exist:
        got = ws.get(f'{first_letter}1:{last_letter}1')
        header = got[0] if got else []
    if [_cell(header, i) for i in range(3)] != list(HEADERS):
        logger.info(f'ヘッダを設定: {first_letter}1:{last_letter}1 = {HEADERS}')
        if not args.dry_run:
            ws.update(values=[list(HEADERS)], range_name=f'{first_letter}1:{last_letter}1',
                      value_input_option='USER_ENTERED')

    logger.info('シート読み込み中...')
    main_cols = ws.get(f'A2:{Col.last_column_letter()}{ws.row_count}')
    oos_cols = ws.get(f'{first_letter}2:{last_letter}{ws.row_count}') if cols_exist else []

    updates, stats, no_history = [], Counter(), []

    for i, row in enumerate(main_cols):
        row_num = i + 2
        product_id = _cell(row, Col.PRODUCT_ID.index)
        if not product_id:
            continue
        stock = _cell(row, Col.SUPPLIER_STOCK.index)
        cur = oos_cols[i] if i < len(oos_cols) else []
        cur_since, cur_days, cur_mark = (_cell(cur, 0), _cell(cur, 1), _cell(cur, 2))

        if stock == IN_STOCK:
            new = ['', '', '']
            stats['在庫あり'] += 1
        elif stock == OUT_OF_STOCK:
            since = cur_since
            if not since:
                if product_id in last_in:
                    since = (_to_date(last_in[product_id]) + datetime.timedelta(days=1)).isoformat()
                elif args.backfill and product_id in seen_in_logs:
                    # ログ期間中ずっとOOS → 実際はもっと古い可能性がある（下限値）
                    since = _to_date(oldest_log).isoformat()
                elif args.backfill:
                    no_history.append((row_num, product_id, _cell(row, Col.NAME.index)[:38]))
                    since = ''
                else:
                    since = today.isoformat()
            if since:
                days = (today - datetime.date.fromisoformat(since)).days
                new = [since, str(days), MARK if days >= args.threshold else '']
                stats[f'OOS {args.threshold}日以上' if days >= args.threshold else 'OOS 期間内'] += 1
            else:
                new = ['', '', '']
                stats['OOS だが履歴なし'] += 1
        else:
            # M列が空（仕入れ先を追跡できていない）→ 触らない
            stats['在庫状況が空'] += 1
            continue

        if [cur_since, cur_days, cur_mark] != new:
            updates.append((row_num, new))

    logger.info('')
    logger.info('--- 集計 ---')
    for k, v in stats.most_common():
        logger.info(f'  {k:<22}: {v}件')
    logger.info(f'  更新が必要な行           : {len(updates)}件')

    marked = [(r, n) for r, n in updates if n[2] == MARK]
    if marked:
        logger.info('')
        logger.info(f'--- {args.threshold}日以上OOS（先頭20件）---')
        for row_num, n in sorted(marked, key=lambda x: -int(x[1][1]))[:20]:
            row = main_cols[row_num - 2]
            logger.info(f'  行{row_num} [{_cell(row, Col.PRODUCT_ID.index)}] '
                        f'{n[1]}日 {_cell(row, Col.DISPLAY_SETTING.index)} '
                        f'{_cell(row, Col.NAME.index)[:34]}')

    if no_history:
        logger.info('')
        logger.info(f'--- OOSだがログに履歴が無い商品: {len(no_history)}件（先頭10件）---')
        logger.info('    スクレイピング対象外の可能性があります')
        for row_num, pid, name in no_history[:10]:
            logger.info(f'  行{row_num} [{pid}] {name}')

    if args.dry_run:
        logger.info('')
        logger.info('ドライランのため書き込みは行いませんでした')
        return

    if not updates:
        logger.info('更新対象はありません')
        return

    # 連続行をまとめて書き込む
    batch, run = [], []

    def flush():
        if run:
            batch.append({
                'range': f'{first_letter}{run[0][0]}:{last_letter}{run[-1][0]}',
                'values': [n for _, n in run],
            })

    for item in updates:
        if run and item[0] != run[-1][0] + 1:
            flush()
            run.clear()
        run.append(item)
    flush()

    logger.info(f'書き込み中: {len(batch)}レンジ / {len(updates)}行')
    for i in range(0, len(batch), 50):
        ws.batch_update(batch[i:i + 50], value_input_option='USER_ENTERED')
    logger.info('完了')


if __name__ == '__main__':
    main()
