#!/usr/bin/env python3
"""
新カラーミー商品管理シート 2-92行目のグループIDをカラーミーAPIに同期するスクリプト

reassign_groups.py でシートに書き込んだグループIDを、カラーミーAPIに反映する。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spreadsheet import SpreadsheetClient
from src.colorme import ColorMeClient
from src.config import Config
from src.cm_sheet_columns import Col

# 対象行（シート上の行番号）
START_ROW = 2
END_ROW = 92


def main():
    # スプレッドシート接続
    client = SpreadsheetClient()
    if not client.connect():
        print("スプレッドシート接続失敗")
        sys.exit(1)

    sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    data = sheet.get_all_values()
    print(f"シート読み取り完了: {len(data)}行")

    # カラーミーAPI
    colorme = ColorMeClient()

    # 各行を処理
    success = 0
    fail = 0
    skip = 0

    for sheet_row in range(START_ROW, END_ROW + 1):
        idx = sheet_row - 1  # 0-indexed
        if idx >= len(data):
            break

        row = data[idx]

        # 商品ID取得
        product_id_str = row[Col.PRODUCT_ID.index] if Col.PRODUCT_ID.index < len(row) else ""
        try:
            product_id = int(product_id_str)
        except (ValueError, TypeError):
            product_id = 0

        if product_id <= 0:
            skip += 1
            continue

        name = row[Col.NAME.index][:40] if Col.NAME.index < len(row) else ""
        grp_ids_raw = row[Col.GROUP_IDS.index] if Col.GROUP_IDS.index < len(row) else ""
        cat_id_raw = row[Col.CATEGORY_ID_BIG.index] if Col.CATEGORY_ID_BIG.index < len(row) else ""

        # グループIDをパース（'3151001,3151002 形式）
        grp_str = grp_ids_raw.lstrip("'").strip()
        group_ids = [int(g.strip()) for g in grp_str.split(",") if g.strip().isdigit() and int(g.strip()) > 0] if grp_str else []

        # カテゴリーIDをパース
        try:
            category_id = int(cat_id_raw) if cat_id_raw else 0
        except (ValueError, TypeError):
            category_id = 0

        if not group_ids and category_id <= 0:
            print(f"  [{sheet_row}] {name} → グループ・カテゴリともに空、スキップ")
            skip += 1
            continue

        # 更新データ構築
        updates = {}
        if group_ids:
            updates["group_ids"] = group_ids
        if category_id > 0:
            updates["category_id_big"] = category_id

        print(f"  [{sheet_row}] {name} (ID:{product_id}) → グループ:{group_ids}, カテゴリ:{category_id}")

        # API更新
        if colorme.update_product(product_id, updates):
            success += 1
        else:
            fail += 1

        # API制限対策
        time.sleep(0.3)

    print(f"\n=== 完了 ===")
    print(f"成功: {success}件, 失敗: {fail}件, スキップ: {skip}件")

    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
