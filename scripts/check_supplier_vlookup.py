"""新カラーミー商品管理シートのVLOOKUP参照切れを検出

CMシートのJ列(仕入れ先URL)にURLがあるのに、
VLOOKUP参照先の商品仕入れ先一覧に該当URLがない行を検出する。

症状:
- K列(仕入れ先商品名)/L列(仕入れ先サイト)/M列(在庫状況)/N列(仕入価格) が空
- 結果、AA/AB/AE/AF列の価格計算が壊れる
- カラーミー側で異常な低価格で販売される危険

原因:
- ブリオンスター/APMEX商品ページ一覧から商品仕入れ先一覧への同期漏れ
- CMシート側にJ列URLがあるが、BSマスタでB=未登録のまま
- CMシート側のJ列URLがBSマスタに存在しない（過去の削除等）

使い方:
  python scripts/check_supplier_vlookup.py             # 検出のみ
  python scripts/check_supplier_vlookup.py --verbose   # 詳細表示
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.config import Config
from src.cm_sheet_columns import Col, get_cell, get_cell_int

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", action="store_true", help="詳細表示（各行の状態）")
    args = p.parse_args()

    client = SpreadsheetClient()
    client.connect()

    # 商品仕入れ先一覧のURLセット
    logger.info("商品仕入れ先一覧を読込中...")
    sp = client._spreadsheet.worksheet('商品仕入れ先一覧')
    sp_rows = sp.get_all_values()
    sp_urls = set()
    for r in sp_rows[1:]:
        if len(r) > 2 and r[2].strip():
            sp_urls.add(r[2].strip())
    logger.info(f"仕入れ先一覧URL: {len(sp_urls)}件")

    # CMシート走査
    logger.info("新カラーミー商品管理シートを読込中...")
    cm = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    cm_rows = cm.get_all_values()

    broken = []  # J列URLあり、仕入れ先一覧に該当なし
    partial = []  # J列URLあり、仕入れ先一覧に該当あり、でもVLOOKUPが空
    for idx, row in enumerate(cm_rows[1:], start=2):
        pid = get_cell(row, Col.PRODUCT_ID)
        j_url = get_cell(row, Col.SUPPLIER_URL)
        if not j_url or not pid:
            continue
        # J列がエラー値(#N/A等)は対象外（別問題）
        if j_url.startswith('#'):
            broken.append((idx, pid, j_url, 'J列がエラー値', get_cell(row, Col.NAME)[:40]))
            continue
        # 仕入れ先一覧に該当URLがあるか
        if j_url not in sp_urls:
            broken.append((idx, pid, j_url, '仕入れ先一覧に不在', get_cell(row, Col.NAME)[:40]))
            continue
        # 該当ありでもVLOOKUP列が空 → データ不整合
        k = get_cell(row, Col.SUPPLIER_NAME)
        n = get_cell_int(row, Col.SUPPLIER_PRICE)
        if not k or n == 0:
            partial.append((idx, pid, j_url, f'K={k[:20]!r} N={n}', get_cell(row, Col.NAME)[:40]))

    # 結果表示
    print()
    print("=" * 70)
    print(f"チェック結果")
    print("=" * 70)
    print(f"仕入れ先一覧に該当URLがない: {len(broken)}行")
    print(f"URLはあるがVLOOKUP列が空:    {len(partial)}行")
    print()

    if broken:
        print(f"■ 参照先URLが仕入れ先一覧にない ({len(broken)}件)")
        show = broken if args.verbose else broken[:20]
        for idx, pid, url, reason, name in show:
            print(f"  行{idx} ID={pid} [{reason}]")
            print(f"    J列: {url}")
            print(f"    商品: {name}")
        if not args.verbose and len(broken) > 20:
            print(f"  ... 他 {len(broken)-20}件（--verbose で全表示）")
        print()

    if partial:
        print(f"■ URLは存在するがVLOOKUPデータが空 ({len(partial)}件)")
        show = partial if args.verbose else partial[:20]
        for idx, pid, url, detail, name in show:
            print(f"  行{idx} ID={pid} [{detail}]")
            print(f"    J列: {url}")
            print(f"    商品: {name}")
        if not args.verbose and len(partial) > 20:
            print(f"  ... 他 {len(partial)-20}件（--verbose で全表示）")
        print()

    if not broken and not partial:
        print("✓ VLOOKUP参照切れなし。正常です。")
    else:
        print("対処方法:")
        print("  1. カラーミー側で該当商品を非表示 or 在庫0にして被害を防ぐ")
        print("  2. BSマスタ/APMEX一覧を確認し、該当URLの登録状況を修正")
        print("  3. python -m src.sync_supplier_list で商品仕入れ先一覧に同期")
        print("  4. CMシートのVLOOKUPが復活したことを確認")

    # 終了コード（CI等で使用）
    sys.exit(1 if (broken or partial) else 0)


if __name__ == "__main__":
    main()
