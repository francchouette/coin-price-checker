#!/usr/bin/env python3
"""新カラーミー商品管理シート 2-92行目のグループID現状確認"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spreadsheet import SpreadsheetClient
from src.config import Config
from src.cm_sheet_columns import Col

client = SpreadsheetClient()
if not client.connect():
    print("接続失敗")
    sys.exit(1)

sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
data = sheet.get_all_values()

print(f"全行数: {len(data)} (ヘッダー含む)")
print(f"\n{'行':>3} | {'商品名':40s} | {'大カテゴリID':>10} | {'大カテゴリ名':15s} | {'グループID':20s} | {'グループ名':30s} | {'仕入れ先URL':50s}")
print("-" * 180)

for i in range(1, min(92, len(data))):  # 2行目〜92行目（index 1〜91）
    row = data[i]
    name = row[Col.NAME.index][:40] if Col.NAME.index < len(row) else ""
    cat_id = row[Col.CATEGORY_ID_BIG.index] if Col.CATEGORY_ID_BIG.index < len(row) else ""
    cat_name = row[Col.CATEGORY_NAME_BIG.index][:15] if Col.CATEGORY_NAME_BIG.index < len(row) else ""
    grp_ids = row[Col.GROUP_IDS.index][:20] if Col.GROUP_IDS.index < len(row) else ""
    grp_names = row[Col.GROUP_NAMES.index][:30] if Col.GROUP_NAMES.index < len(row) else ""
    sup_url = row[Col.SUPPLIER_URL.index][:50] if Col.SUPPLIER_URL.index < len(row) else ""

    print(f"{i+1:3d} | {name:40s} | {cat_id:>10s} | {cat_name:15s} | {grp_ids:20s} | {grp_names:30s} | {sup_url:50s}")
