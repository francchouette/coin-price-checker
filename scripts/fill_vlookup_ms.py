"""新カラーミー商品管理シートの M-S列にVLOOKUP数式を追加（行1168-1207）"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient

client = SpreadsheetClient()
client.connect()
ws = client._spreadsheet.worksheet('新カラーミー商品管理')

# CMシート列 → 仕入れ先一覧列
COL_MAP = [
    ('M', 'J'),  # 在庫状況
    ('N', 'K'),  # 現在価格
    ('O', 'Q'),  # 前回価格
    ('P', 'R'),  # 変動率
    ('Q', 'L'),  # 通貨
    ('R', 'M'),  # 為替種類
    ('S', 'N'),  # 為替レート
]

START, END = 1168, 1207
updates = []
for row in range(START, END + 1):
    row_vals = []
    for cm_col, sp_col in COL_MAP:
        formula = (
            f"=IFERROR(INDEX('商品仕入れ先一覧'!${sp_col}:${sp_col},"
            f"MATCH($J{row},'商品仕入れ先一覧'!$C:$C,0)),\"\")"
        )
        row_vals.append(formula)
    updates.append({
        'range': f"新カラーミー商品管理!M{row}:S{row}",
        'values': [row_vals],
    })

print(f"書き込み対象: {len(updates)}行 (M-S列 = 各行7セル)")
print("\n例:")
print(f"  M{START}: {updates[0]['values'][0][0]}")
print(f"  S{START}: {updates[0]['values'][0][6]}")
print()

resp = ws.spreadsheet.values_batch_update({
    'data': updates,
    'valueInputOption': 'USER_ENTERED',
})
print(f"更新完了: {resp.get('totalUpdatedCells', 0)}セル")
