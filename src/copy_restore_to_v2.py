"""
復元データシートから新カラーミー商品管理シートへデータをコピー

新カラーミー商品管理_復元データ → 新カラーミー商品管理
"""

import logging
import sys
from .spreadsheet import SpreadsheetClient
from .config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== 復元データのコピー開始 ===")

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    try:
        # 復元データシートを取得
        source_sheet = client._spreadsheet.worksheet("新カラーミー商品管理_復元データ")
        target_sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

        # 復元データを取得
        all_data = source_sheet.get_all_values()
        if len(all_data) < 1:
            logger.info("復元データがありません")
            return

        logger.info(f"復元データ: {len(all_data)}行 x {len(all_data[0])}列")

        # ターゲットシートをクリア（ヘッダー以外）
        logger.info("ターゲットシートのデータをクリア中...")
        target_sheet.batch_clear(['A2:CH1000'])

        # 86列ヘッダーを設定
        logger.info("86列ヘッダーを設定中...")
        target_sheet.update('A1:CH1', [Config.COLORME_V2_HEADERS])

        # 復元データ（ヘッダー以外）をコピー
        # 81列データを一旦そのままコピー（後でマイグレーションする）
        data_rows = all_data[1:]
        if data_rows:
            logger.info(f"データ行をコピー中... ({len(data_rows)}行)")

            # 81列データをそのままA-CC列にコピー
            end_col = 'CC'  # 81列目
            range_str = f'A2:{end_col}{len(data_rows) + 1}'
            target_sheet.update(range_str, data_rows, value_input_option='USER_ENTERED')

            logger.info(f"コピー完了: {len(data_rows)}行")

    except Exception as e:
        logger.error(f"エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    logger.info("=== コピー終了 ===")


if __name__ == "__main__":
    main()
