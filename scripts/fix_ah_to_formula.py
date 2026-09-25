"""新カラーミー商品管理シートの AH列（原価）を =AE{行} の数式に統一する

問題: AH列が古いスナップショット値のため、AE（販売価格）が下がると
      AH(原価) > AI(税込販売価格) という見た目の不整合が発生する。

対処: AH = =AE{row} の数式にして、常に最新のAEと一致させる。
      カラーミー側への影響なし（sync_colorme_products は AE のみ参照）。

使い方:
    python scripts/fix_ah_to_formula.py --dry-run  # 確認のみ
    python scripts/fix_ah_to_formula.py            # 実行
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient


def main():
    parser = argparse.ArgumentParser(description="AH列を =AE 数式に統一")
    parser.add_argument("--dry-run", action="store_true", help="変更内容のみ表示")
    args = parser.parse_args()

    client = SpreadsheetClient()
    client.connect()
    ws = client._spreadsheet.worksheet('新カラーミー商品管理')

    # データ最終行
    ah_values = ws.col_values(34)
    last_row = len(ah_values)
    print(f"データ最終行: {last_row}")

    # 数式状態取得
    res = ws.spreadsheet.values_get(
        f"'新カラーミー商品管理'!AH2:AH{last_row}",
        params={'valueRenderOption': 'FORMULA'}
    ).get('values', [])

    # 値の行のみ更新対象
    updates = []
    for offset, r in enumerate(res):
        row_num = offset + 2  # 1-indexed, ヘッダ分+1
        cell_value = r[0] if r else ""
        s = str(cell_value).strip()
        if not s:
            continue  # 空欄はスキップ
        if s.startswith("="):
            continue  # 既に数式
        # 値 → 数式に置換
        updates.append({
            'range': f"新カラーミー商品管理!AH{row_num}",
            'values': [[f"=AE{row_num}"]],
        })

    print(f"更新対象（値→数式）: {len(updates)}件")
    if updates[:5]:
        print("例（先頭5件）:")
        for u in updates[:5]:
            print(f"  {u['range']}: {u['values'][0][0]}")

    if args.dry_run:
        print("\n[ドライラン] 実際の更新は行いません")
        return

    if not updates:
        print("更新対象なし")
        return

    print("\n更新中...")
    resp = ws.spreadsheet.values_batch_update({
        'data': updates,
        'valueInputOption': 'USER_ENTERED',
    })
    print(f"更新完了: {resp.get('totalUpdatedCells', 0)}セル")


if __name__ == "__main__":
    main()
