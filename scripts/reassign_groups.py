#!/usr/bin/env python3
"""
新カラーミー商品管理シート 2-92行目のグループIDを新体系で再付与するスクリプト

CategoryDetector（AI判定）を使って、商品名とURLから:
- 大カテゴリーID (AK列)
- 大カテゴリー名称 (AL列)
- グループID (AM列)
- グループ名 (AN列)
を再付与する。
"""
import copy
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spreadsheet import SpreadsheetClient
from src.colorme import ColorMeClient
from src.config import Config
from src.cm_sheet_columns import Col
from src.add_product import CategoryDetector

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

    # カラーミーAPI → CategoryDetector初期化
    colorme = ColorMeClient()
    categories = colorme.get_categories()
    groups = colorme.get_groups()
    detector = CategoryDetector(categories, groups, colorme)
    print(f"CategoryDetector初期化: {len(categories)}カテゴリー, {len(groups)}グループ")

    # カテゴリー名称マップ
    category_name_map = {
        2977963: "ゴールド（金）",
        2977964: "シルバー（銀）",
        2977965: "プラチナ",
        2977966: "パラジウム",
        2977967: "カッパー（銅）",
    }

    # グループ名称マップ
    group_name_map = {}
    for grp in groups:
        grp_id = grp.get("id", 0)
        grp_name = grp.get("name", "")
        if grp_id and grp_name:
            group_name_map[grp_id] = grp_name

    print(f"グループ名称マップ: {len(group_name_map)}件")
    print()

    # 各行を処理
    batch_updates = []
    for sheet_row in range(START_ROW, END_ROW + 1):
        idx = sheet_row - 1  # 0-indexed
        if idx >= len(data):
            break

        row = data[idx]
        name = row[Col.NAME.index] if Col.NAME.index < len(row) else ""
        url = row[Col.SUPPLIER_URL.index] if Col.SUPPLIER_URL.index < len(row) else ""

        if not name:
            print(f"  [{sheet_row}] 商品名なし → スキップ")
            continue

        # AI判定
        try:
            cat_big, cat_small, group_ids = detector.detect(name, url)
        except Exception as e:
            print(f"  [{sheet_row}] 判定エラー: {e}")
            continue

        # 値を構築
        cat_id_str = str(cat_big) if cat_big else ""
        cat_name_str = category_name_map.get(cat_big, "") if cat_big else ""

        if group_ids:
            grp_ids_str = "'" + ",".join(str(g) for g in group_ids)
            grp_names = [group_name_map.get(g, f"ID:{g}") for g in group_ids]
            grp_names_str = ",".join(n for n in grp_names if n)
        else:
            grp_ids_str = ""
            grp_names_str = ""

        # 現在値と比較
        old_grp = row[Col.GROUP_IDS.index] if Col.GROUP_IDS.index < len(row) else ""
        print(f"  [{sheet_row}] {name[:40]}")
        print(f"         旧グループ: {old_grp}")
        print(f"         新グループ: {grp_ids_str} ({grp_names_str})")
        print(f"         カテゴリ: {cat_id_str} ({cat_name_str})")

        # バッチに追加
        batch_updates.append({'range': f'{Col.CATEGORY_ID_BIG.letter}{sheet_row}', 'values': [[cat_id_str]]})
        batch_updates.append({'range': f'{Col.CATEGORY_NAME_BIG.letter}{sheet_row}', 'values': [[cat_name_str]]})
        batch_updates.append({'range': f'{Col.GROUP_IDS.letter}{sheet_row}', 'values': [[grp_ids_str]]})
        batch_updates.append({'range': f'{Col.GROUP_NAMES.letter}{sheet_row}', 'values': [[grp_names_str]]})

        # API制限対策（Gemini）
        time.sleep(0.5)

    if not batch_updates:
        print("\n更新対象なし")
        return

    # バッチ書き込み
    print(f"\n=== {len(batch_updates) // 4}行分のグループIDを更新します ===")
    CHUNK_SIZE = 100
    for i in range(0, len(batch_updates), CHUNK_SIZE):
        chunk = batch_updates[i:i + CHUNK_SIZE]
        for attempt in range(3):
            try:
                data_copy = copy.deepcopy(chunk)
                sheet.batch_update(data_copy, value_input_option='USER_ENTERED')
                print(f"  書き込み完了: {min(i + CHUNK_SIZE, len(batch_updates))}/{len(batch_updates)}セル")
                break
            except Exception as e:
                if attempt < 2:
                    print(f"  リトライ: {e}")
                    time.sleep(10)
                else:
                    raise
        time.sleep(1)

    print(f"\n=== 完了: {len(batch_updates) // 4}行のグループIDを新体系に更新しました ===")


if __name__ == "__main__":
    main()
