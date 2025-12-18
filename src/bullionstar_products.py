"""
Bullionstar 商品ページ一覧取得スクリプト

Bullionstarの全商品ページURLとカテゴリー情報を取得し、
スプレッドシートの「ブリオンスター商品ページ一覧」シートに保存する。

取得情報:
- 商品名
- URL
- 最上位カテゴリ
- 親カテゴリ
- 子カテゴリ
- 取得日

次回実行時は上書きせず、新しいデータを下に追加する。

BullionstarのAPIを使用して全ページネーションを正しく処理する。
"""

import logging
import sys
import time
import random
import requests
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


@dataclass
class BullionstarProduct:
    """Bullionstar商品データ"""
    name: str
    url: str
    top_category: str      # 最上位カテゴリ (Gold, Silver, Platinum等)
    parent_category: str   # 親カテゴリ (Gold Bars, Gold Coins等)
    child_category: str    # 子カテゴリ (詳細分類)
    fetched_at: str        # 取得日時


# カテゴリー構造定義
# 最上位カテゴリー → 親カテゴリー → APIカテゴリー名
CATEGORY_STRUCTURE = {
    "Gold": {
        "Gold Bars": "gold-bars",
        "Gold Coins": "gold-coins",
        "Numismatics Gold": "numismatics-collectibles-gold-coins",
        "Gold Jewellery": "gold-jewellery",
    },
    "Silver": {
        "Silver Bars": "silver-bars",
        "Silver Coins": "silver-coins-rounds-wafers",
        "Numismatics Silver": "numismatics-collectibles-silver-coins",
    },
    "Platinum": {
        "Platinum Bars": "platinum-bars",
        "Platinum Coins": "platinum-coins",
    },
    "Copper": {
        "Copper Products": "copper",
    },
    "Jewellery": {
        "Jewellery": "jewellery",
    },
    "Supplies": {
        "Coin Supplies": "supplies",
    },
}


class BullionstarProductScraper:
    """Bullionstar商品ページスクレイパー（API版）"""

    BASE_URL = "https://www.bullionstar.com"
    API_URL = "https://www.bullionstar.com/product/filter/desktop"

    # 待機時間設定
    MIN_WAIT = 1.0
    MAX_WAIT = 2.0

    # ロケーションID (1=シンガポール)
    LOCATION_ID = 1

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def _wait(self):
        """ランダムな待機"""
        wait_time = random.uniform(self.MIN_WAIT, self.MAX_WAIT)
        time.sleep(wait_time)

    def get_all_products(self) -> list[BullionstarProduct]:
        """
        全カテゴリの商品ページ一覧を取得

        Returns:
            list[BullionstarProduct]: 商品リスト
        """
        all_products = []
        seen_urls = set()
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

        for top_category, subcategories in CATEGORY_STRUCTURE.items():
            logger.info(f"\n=== 最上位カテゴリ: {top_category} ===")

            for parent_category, category_name in subcategories.items():
                logger.info(f"  親カテゴリ: {parent_category}")

                products = self._fetch_category_products(
                    category_name=category_name,
                    top_category=top_category,
                    parent_category=parent_category,
                    timestamp=timestamp
                )

                # 重複を除外して追加
                for product in products:
                    if product.url not in seen_urls:
                        seen_urls.add(product.url)
                        all_products.append(product)

                logger.info(f"    → {len(products)}件取得（累計: {len(all_products)}件）")
                self._wait()

        logger.info(f"\n商品ページ取得完了: {len(all_products)}件")
        return all_products

    def _fetch_category_products(
        self,
        category_name: str,
        top_category: str,
        parent_category: str,
        timestamp: str
    ) -> list[BullionstarProduct]:
        """
        カテゴリーの商品一覧をAPIから取得（全ページ）

        Args:
            category_name: APIカテゴリー名 (gold-bars 等)
            top_category: 最上位カテゴリ名
            parent_category: 親カテゴリ名
            timestamp: 取得日時

        Returns:
            list[BullionstarProduct]: 商品リスト
        """
        products = []
        page_num = 1

        while True:
            try:
                logger.info(f"    ページ{page_num}を取得中...")

                params = {
                    "locationId": self.LOCATION_ID,
                    "name": category_name,
                    "page": page_num,
                    "sortType": "popular",
                    "sortDirection": "desc",
                }

                response = self._session.get(self.API_URL, params=params, timeout=30)
                response.raise_for_status()

                data = response.json()

                if not data.get("success"):
                    logger.warning(f"    API応答エラー: {data}")
                    break

                result = data.get("result", {})
                groups = result.get("groups", [])

                if not groups:
                    logger.info(f"    ページ{page_num}: 商品なし - 終了")
                    break

                page_products = []

                for group in groups:
                    group_products = group.get("products", [])
                    for prod in group_products:
                        product = self._parse_api_product(
                            prod=prod,
                            top_category=top_category,
                            parent_category=parent_category,
                            timestamp=timestamp
                        )
                        if product:
                            page_products.append(product)

                if not page_products:
                    logger.info(f"    ページ{page_num}: パース可能な商品なし - 終了")
                    break

                products.extend(page_products)
                logger.info(f"    ページ{page_num}: {len(page_products)}件取得")

                # 次ページがあるかチェック
                next_page = result.get("nextPage")
                if not next_page:
                    logger.info(f"    最終ページ: {page_num}")
                    break

                page_num += 1
                self._wait()

            except requests.RequestException as e:
                logger.error(f"    API取得エラー: {e}")
                break
            except Exception as e:
                logger.error(f"    カテゴリ取得エラー: {e}")
                break

        return products

    def _parse_api_product(
        self,
        prod: dict,
        top_category: str,
        parent_category: str,
        timestamp: str
    ) -> Optional[BullionstarProduct]:
        """
        API応答の商品データをパース

        Args:
            prod: 商品データ辞書
            top_category: 最上位カテゴリ
            parent_category: 親カテゴリ
            timestamp: 取得日時

        Returns:
            BullionstarProduct: 商品データ（パース失敗時はNone）
        """
        try:
            url_name = prod.get("urlName", "")
            title = prod.get("title", "")

            if not url_name or not title:
                return None

            url = f"{self.BASE_URL}/buy/product/{url_name}"

            # 子カテゴリーはAPIデータから取得（あれば）
            child_category = prod.get("subCategory", "") or ""

            return BullionstarProduct(
                name=title,
                url=url,
                top_category=top_category,
                parent_category=parent_category,
                child_category=child_category,
                fetched_at=timestamp
            )

        except Exception as e:
            logger.warning(f"商品パースエラー: {e}")
            return None


def save_products_to_spreadsheet(products: list[BullionstarProduct]) -> bool:
    """
    商品をスプレッドシートに保存（追加モード）

    既存のデータは上書きせず、新しいデータを下に追加する。

    Args:
        products: Bullionstar商品リスト

    Returns:
        bool: 成功時True
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    sheet_name = Config.SHEET_BULLIONSTAR_PRODUCTS

    try:
        # シートを取得または作成
        try:
            sheet = client._spreadsheet.worksheet(sheet_name)
            logger.info(f"既存シート '{sheet_name}' を使用")
        except Exception:
            # シートがなければ作成
            sheet = client._spreadsheet.add_worksheet(
                title=sheet_name,
                rows=5000,
                cols=10
            )
            # ヘッダーを追加
            headers = Config.BULLIONSTAR_PRODUCT_HEADERS
            sheet.update('A1:F1', [headers])
            logger.info(f"シート '{sheet_name}' を作成しました")

        # 既存データの確認（ヘッダー行があるかチェック）
        existing_data = sheet.get_all_values()
        if not existing_data:
            # ヘッダーを追加
            headers = Config.BULLIONSTAR_PRODUCT_HEADERS
            sheet.update('A1:F1', [headers])
            logger.info("ヘッダー行を追加")

        # 商品データを行形式に変換
        new_rows = []
        for product in products:
            new_rows.append([
                product.name,           # A: 商品名
                product.url,            # B: URL
                product.top_category,   # C: 最上位カテゴリ
                product.parent_category, # D: 親カテゴリ
                product.child_category, # E: 子カテゴリ
                product.fetched_at,     # F: 取得日
            ])

        if new_rows:
            # 既存データの下に追加
            sheet.append_rows(new_rows, value_input_option='RAW')
            logger.info(f"スプレッドシートに {len(new_rows)} 件追加しました")
        else:
            logger.info("追加する商品はありませんでした")

        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        return False


def fetch_bullionstar_products() -> list[BullionstarProduct]:
    """
    Bullionstarから商品ページ一覧を取得

    Returns:
        list[BullionstarProduct]: 商品リスト
    """
    scraper = BullionstarProductScraper()
    return scraper.get_all_products()


def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bullionstar商品ページ一覧を取得してスプレッドシートに保存"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="詳細ログ出力"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ドライラン（スプレッドシートに保存しない）"
    )
    parser.add_argument(
        "--category",
        type=str,
        help="特定のカテゴリのみ取得（Gold, Silver, Platinum, Copper, Jewellery, Supplies）"
    )

    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logger.info("=" * 60)
    logger.info("Bullionstar 商品ページ一覧取得開始")
    logger.info("=" * 60)

    start_time = datetime.now()

    # 設定検証
    errors = Config.validate()
    if errors and not args.dry_run:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # 特定カテゴリのみの場合、CATEGORY_STRUCTUREをフィルタリング
    global CATEGORY_STRUCTURE
    if args.category:
        if args.category in CATEGORY_STRUCTURE:
            CATEGORY_STRUCTURE = {args.category: CATEGORY_STRUCTURE[args.category]}
            logger.info(f"カテゴリを {args.category} に限定")
        else:
            logger.error(f"無効なカテゴリ: {args.category}")
            logger.error(f"有効なカテゴリ: {', '.join(CATEGORY_STRUCTURE.keys())}")
            sys.exit(1)

    # 商品ページ一覧を取得
    products = fetch_bullionstar_products()

    if not products:
        logger.warning("商品を取得できませんでした")
        return 1

    logger.info(f"\n取得した商品数: {len(products)}件")

    # 結果を表示
    logger.info("\n取得した商品（最初の10件）:")
    for i, product in enumerate(products[:10], 1):
        logger.info(f"  {i}. {product.name}")
        logger.info(f"     URL: {product.url}")
        logger.info(f"     カテゴリ: {product.top_category} > {product.parent_category}")

    if len(products) > 10:
        logger.info(f"  ... 他 {len(products) - 10} 件")

    # スプレッドシートに保存
    if args.dry_run:
        logger.info("\n[ドライラン] スプレッドシートへの保存をスキップ")
    else:
        logger.info("\n" + "=" * 60)
        logger.info("スプレッドシートに保存")
        logger.info("=" * 60)

        if save_products_to_spreadsheet(products):
            logger.info("スプレッドシートへの保存完了")
        else:
            logger.error("スプレッドシートへの保存失敗")
            return 1

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("\n" + "=" * 60)
    logger.info(f"処理完了")
    logger.info(f"  取得件数: {len(products)}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒（{elapsed/60:.1f}分）")
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
