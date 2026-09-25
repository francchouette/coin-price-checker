"""
競合価格取得スクリプト

新カラーミー商品管理シートのBZ列（競合商品URL）から
商品名・価格・在庫状況をスクレイピングし、CA-CC列に書き込む。

使用方法:
  python -m src.fetch_competitor_prices                # 全件取得
  python -m src.fetch_competitor_prices --limit 5      # 5件のみ（テスト用）
  python -m src.fetch_competitor_prices --verbose       # 詳細ログ
"""

import argparse
import copy
import logging
import sys
import time

from .spreadsheet import SpreadsheetClient
from .config import Config
from .scraper import ScraperManager, detect_shop_from_url
from .shops import ScrapedData
from .cm_sheet_columns import Col, get_cell

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 10
MAX_RETRIES = 3


def batch_update_with_retry(sheet, batch_data: list) -> bool:
    """リトライ付きバッチ更新"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = copy.deepcopy(batch_data)
            sheet.batch_update(data, value_input_option='USER_ENTERED')
            return True
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 10 * attempt
                logger.warning(f"  シート更新エラー（リトライ {attempt}/{MAX_RETRIES}、{wait}秒後）: {e}")
                time.sleep(wait)
            else:
                logger.error(f"  シート更新エラー（全リトライ失敗）: {e}")
                return False


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="競合価格取得")
    parser.add_argument("--limit", type=int, default=0, help="処理件数制限（0=無制限）")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=== 競合価格取得 開始 ===")

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

    sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

    # シートデータ読み込み
    logger.info("シートデータを読み込み中...")
    existing = sheet.get_all_values()

    if len(existing) <= 1:
        logger.info("データがありません")
        return

    logger.info(f"シート行数: {len(existing) - 1}件（ヘッダー除く）")

    # BZ列（競合商品URL）からスクレイピング対象を収集
    targets = []  # [(row_num, url), ...]
    for row_idx in range(1, len(existing)):
        row = existing[row_idx]
        url = get_cell(row, Col.COMPETITOR_URL)
        if not url or not url.startswith("http"):
            continue
        row_num = row_idx + 1  # 1-indexed
        targets.append((row_num, url))

    if not targets:
        logger.info("競合URLがありません")
        return

    if args.limit > 0:
        targets = targets[:args.limit]

    logger.info(f"スクレイピング対象: {len(targets)}件")

    # スクレイピング + シート更新
    success = 0
    fail = 0
    price_cache = {}  # URL -> (ScrapedData, stock_num)
    pending_updates = []

    def flush_updates():
        nonlocal pending_updates
        if not pending_updates:
            return
        if batch_update_with_retry(sheet, pending_updates):
            logger.debug(f"  [シート保存] {len(pending_updates)}セルを更新")
        pending_updates = []

    with ScraperManager() as scraper_manager:
        for i, (row_num, url) in enumerate(targets):
            logger.info(f"[{i+1}/{len(targets)}] 行{row_num}: {url[:60]}")

            # キャッシュチェック
            if url in price_cache:
                data, stock_num = price_cache[url]
                logger.info(f"  キャッシュヒット: {data.product_name[:30]}")
            else:
                # ショップ判定
                shop_name = detect_shop_from_url(url)
                if not shop_name:
                    logger.warning(f"  未対応のショップURL: {url}")
                    fail += 1
                    continue

                # スクレイピング
                scraper = scraper_manager.get_scraper(shop_name)
                if not scraper:
                    logger.error(f"  スクレイパーが見つかりません: {shop_name}")
                    fail += 1
                    continue

                data = scraper.scrape(url)
                if data.error:
                    logger.warning(f"  エラー: {data.error}")
                    fail += 1
                    continue

                stock_num = getattr(scraper, '_stock_num', 0)
                price_cache[url] = (data, stock_num)

            # CA列: 競合商品名
            pending_updates.append({
                'range': f'{Col.COMPETITOR_NAME.letter}{row_num}',
                'values': [[data.product_name]],
            })
            # CB列: 競合価格
            pending_updates.append({
                'range': f'{Col.COMPETITOR_PRICE.letter}{row_num}',
                'values': [[int(data.price) if data.price == int(data.price) else data.price]],
            })
            # CC列: 競合在庫数
            pending_updates.append({
                'range': f'{Col.COMPETITOR_STOCK.letter}{row_num}',
                'values': [[stock_num]],
            })

            success += 1
            logger.info(f"  ✓ {data.product_name[:30]} - ¥{data.price:,.0f} (在庫: {stock_num})")

            # バッチ書き込み
            if success % BATCH_SIZE == 0:
                flush_updates()
                time.sleep(1)

    # 残りをフラッシュ
    flush_updates()

    logger.info("=== 競合価格取得 完了 ===")
    logger.info(f"成功: {success}件 / 失敗: {fail}件")


if __name__ == "__main__":
    main()
