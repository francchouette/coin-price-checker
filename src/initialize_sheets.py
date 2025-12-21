"""
新シート初期化スクリプト

3つの新シートを作成し、スプレッドシートの左側に配置する:
1. 商品仕入れ先一覧
2. 新カラーミー商品初期登録一覧
3. 新カラーミー商品管理
"""

import logging
import sys

from .spreadsheet import SpreadsheetClient
from .config import Config

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """メイン処理"""
    logger.info("=== 新シート初期化開始 ===")

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

    # 新シートを作成
    result = client.create_new_sheets()

    logger.info("=== 結果 ===")
    logger.info(f"商品仕入れ先一覧: {'作成/確認済み' if result['suppliers'] else '失敗'}")
    logger.info(f"新カラーミー商品初期登録一覧: {'作成/確認済み' if result['initial'] else '失敗'}")
    logger.info(f"新カラーミー商品管理: {'作成/確認済み' if result['management'] else '失敗'}")

    if all(result.values()):
        logger.info("=== 新シート初期化完了 ===")
    else:
        logger.warning("=== 一部のシート作成に失敗しました ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
