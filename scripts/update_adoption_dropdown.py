"""BS/APMEX商品ページ一覧のA列(採用フラグ)のドロップダウンに「除外」を追加

既存のデータ検証ルールを上書きし、A列全体に新ドロップダウンを適用する。

使い方:
  python scripts/update_adoption_dropdown.py            # 両シートに適用
  python scripts/update_adoption_dropdown.py --sheet bs # ブリオンスターのみ
  python scripts/update_adoption_dropdown.py --sheet ap # APMEXのみ
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

ADOPTION_VALUES = ["採用", "予約", "未採用", "検討中", "NG", "除外", "作業完了", "保留"]


def update_sheet_validation(client: SpreadsheetClient, sheet_name: str):
    """指定シートのA列(採用フラグ)のドロップダウンを更新"""
    try:
        sheet = client._spreadsheet.worksheet(sheet_name)
        sheet_id = sheet.id
        row_count = sheet.row_count

        request = {
            'setDataValidation': {
                'range': {
                    'sheetId': sheet_id,
                    'startRowIndex': 1,         # ヘッダー行を除く（2行目から）
                    'endRowIndex': row_count,
                    'startColumnIndex': 0,      # A列
                    'endColumnIndex': 1,
                },
                'rule': {
                    'condition': {
                        'type': 'ONE_OF_LIST',
                        'values': [{'userEnteredValue': v} for v in ADOPTION_VALUES],
                    },
                    'showCustomUi': True,
                    'strict': False,
                },
            }
        }

        client._spreadsheet.batch_update({'requests': [request]})
        logger.info(f"  ✅ {sheet_name}: A列 ({row_count - 1}行) に新ドロップダウン適用完了")
        logger.info(f"     値: {' / '.join(ADOPTION_VALUES)}")
    except Exception as e:
        logger.error(f"  ❌ {sheet_name}: エラー - {e}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sheet", choices=["bs", "ap", "all"], default="all", help="対象シート (bs/ap/all)")
    args = p.parse_args()

    client = SpreadsheetClient()
    client.connect()

    targets = []
    if args.sheet in ("all", "bs"):
        targets.append(Config.SHEET_BULLIONSTAR_PRODUCTS)
    if args.sheet in ("all", "ap"):
        targets.append(Config.SHEET_APMEX_PRODUCTS)

    logger.info(f"ドロップダウン更新対象シート: {len(targets)}件")
    for sheet_name in targets:
        update_sheet_validation(client, sheet_name)


if __name__ == "__main__":
    main()
