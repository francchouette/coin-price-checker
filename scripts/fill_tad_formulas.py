"""新カラーミー商品管理シートの T-AD列・AI-AJ列に計算式を追加（行1167-1207）

APMEX商品行は ブリオンスター商品ページ一覧 を参照できないため、固定値ベースで計算。

T  (仕入れ額)   = N×S
U  (数量)       = 1
V  (仕入れ合計) = T×U
W  (マージン率) = 1.12
X  (マージン額) = 0
Y  (送料)       = 150
Z  (諸経費)     = 100
AA (合計原価)   = V+Y+Z
AB (適正価格)   = AA/(2-W)+Y+Z
AC (粗利額)     = AB-AA
AD (粗利率)     = (AB-AA)/AB
AI (税込販売)   = AE×1.1
AJ (消費税額)   = AI-AE
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient

client = SpreadsheetClient()
client.connect()
ws = client._spreadsheet.worksheet('新カラーミー商品管理')

START, END = 1167, 1207

t_ad_updates = []
ai_aj_updates = []

for r in range(START, END + 1):
    t_ad_updates.append({
        'range': f"新カラーミー商品管理!T{r}:AD{r}",
        'values': [[
            f"=N{r}*S{r}",      # T: 仕入れ額
            1,                   # U: 数量
            f"=T{r}*U{r}",      # V: 仕入れ合計
            1.12,                # W: マージン率
            0,                   # X: マージン額
            150,                 # Y: 送料
            100,                 # Z: 諸経費
            f"=V{r}+Y{r}+Z{r}",  # AA: 合計原価
            f"=AA{r}/(2-W{r})+Y{r}+Z{r}",  # AB: 適正価格
            f"=AB{r}-AA{r}",     # AC: 粗利額
            f"=(AB{r}-AA{r})/AB{r}",  # AD: 粗利率
        ]],
    })
    ai_aj_updates.append({
        'range': f"新カラーミー商品管理!AI{r}:AJ{r}",
        'values': [[
            f"=AE{r}*1.1",       # AI: 消費税込
            f"=AI{r}-AE{r}",     # AJ: 消費税額
        ]],
    })

print(f"T-AD更新: {len(t_ad_updates)}行 × 11列 = {len(t_ad_updates)*11}セル")
print(f"AI-AJ更新: {len(ai_aj_updates)}行 × 2列 = {len(ai_aj_updates)*2}セル")
print(f"サンプル T{START}: {t_ad_updates[0]['values'][0]}")
print()

resp = ws.spreadsheet.values_batch_update({
    'data': t_ad_updates + ai_aj_updates,
    'valueInputOption': 'USER_ENTERED',
})
print(f"更新完了: {resp.get('totalUpdatedCells', 0)}セル")
