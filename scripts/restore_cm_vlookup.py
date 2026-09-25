"""新カラーミー商品管理シートの M-S列 (仕入れ先価格関連) を VLOOKUP 数式に戻す

問題: CMシートのM-S列が直接値化されており、商品仕入れ先一覧の最新値が反映されない

対象:
  - CMシート J列(URL) が 商品仕入れ先一覧 C列 にマッチする
  - CMシート N列 が直接値 (VLOOKUP数式でない)
  - CMシート N列 の値と 商品仕入れ先一覧 K列 の値が乖離している

処理:
  - M-S列 7列を VLOOKUP 数式に戻す（在庫状況/価格/前回価格/変動率/通貨/為替種類/為替）

使い方:
  python scripts/restore_cm_vlookup.py --dry-run   # 対象表示のみ
  python scripts/restore_cm_vlookup.py             # 本実行
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

# CMシート → 商品仕入れ先一覧 のVLOOKUPマッピング
VLOOKUP_MAP = [
    ('M', 'J'),  # 在庫状況
    ('N', 'K'),  # 価格（現地通貨）
    ('O', 'Q'),  # 前回価格
    ('P', 'R'),  # 変動率
    ('Q', 'L'),  # 通貨
    ('R', 'M'),  # 為替種類
    ('S', 'N'),  # 為替レート
]


def _to_float(v) -> float:
    try:
        s = str(v).replace(",", "").replace("¥", "").replace("$", "").strip()
        return float(s) if s else 0.0
    except (ValueError, TypeError):
        return 0.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    client = SpreadsheetClient()
    client.connect()

    # 商品仕入れ先一覧 → URL→K列(価格) のマップ
    logger.info("商品仕入れ先一覧 読込中...")
    sp = client._spreadsheet.worksheet('商品仕入れ先一覧')
    sp_data = sp.get_all_values()
    sp_by_url = {}
    for r in sp_data[1:]:
        url = r[2].strip() if len(r) > 2 else ''
        k = r[10] if len(r) > 10 else ''
        if url:
            sp_by_url[url] = k  # 価格文字列
    logger.info(f"仕入れ先URL: {len(sp_by_url)}件")

    # CMシート読込
    logger.info("CMシート読込中...")
    cm = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    rows = cm.get_all_values()
    n_formulas = cm.get(f'N2:N{len(rows)}', value_render_option='FORMULA')

    # 対象行を抽出
    targets = []
    for idx, row in enumerate(rows[1:], start=2):
        j_url = get_cell(row, Col.SUPPLIER_URL)
        if not j_url:
            continue
        # 仕入れ先一覧に登録があるか
        sp_price = sp_by_url.get(j_url)
        if sp_price is None:
            continue  # 参照先なし → スキップ
        # N列が既に数式なら OK
        n_form = n_formulas[idx-2][0] if idx-2 < len(n_formulas) and n_formulas[idx-2] else ''
        if str(n_form).startswith('='):
            continue
        # 値比較 (乖離があれば対象)
        cm_n = get_cell(row, Col.SUPPLIER_PRICE)
        cm_v = _to_float(cm_n)
        sp_v = _to_float(sp_price)
        if sp_v == 0:
            continue
        diff_ratio = abs(cm_v - sp_v) / sp_v if sp_v > 0 else 1.0
        if diff_ratio > 0.01:  # 1%以上乖離
            targets.append((idx, j_url, cm_n, sp_price, diff_ratio))

    logger.info(f"修復対象: {len(targets)}件（N列直接値 かつ 仕入れ先K列と1%以上乖離）")
    for r_num, url, cm_v, sp_v, ratio in targets[:15]:
        logger.info(f"  行{r_num}: CM N={cm_v} / 仕入れ K={sp_v} (乖離{ratio*100:.1f}%)")
    if len(targets) > 15:
        logger.info(f"  ... 他 {len(targets)-15}件")

    if args.dry_run:
        logger.info("[DRY-RUN] 実行しません")
        return

    if not targets:
        logger.info("修復対象なし")
        return

    # M-S列 7列を VLOOKUP 数式に戻す
    updates = []
    for r_num, _, _, _, _ in targets:
        for cm_col, sp_col in VLOOKUP_MAP:
            formula = (
                f"=IFERROR(INDEX('商品仕入れ先一覧'!${sp_col}:${sp_col},"
                f"MATCH($J{r_num},'商品仕入れ先一覧'!$C:$C,0)),\"\")"
            )
            updates.append({'range': f'{cm_col}{r_num}', 'values': [[formula]]})

    logger.info(f"更新セル数: {len(updates)} ({len(targets)}行 × 7列)")
    CHUNK = 300
    total = 0
    for i in range(0, len(updates), CHUNK):
        chunk = updates[i:i+CHUNK]
        cm.batch_update(chunk, value_input_option='USER_ENTERED')
        total += len(chunk)
        logger.info(f"  進捗: {total}/{len(updates)}")
        time.sleep(0.5)
    logger.info(f"完了: {len(targets)}行の M-S列 を VLOOKUP に戻しました")


if __name__ == "__main__":
    main()
