"""
仕入れ先価格取得スクリプト

商品仕入れ先一覧シートに登録された商品の価格を取得し、更新する。
対応サイト:
- Bullionstar (Singapore, USA, New Zealand)
- APMEX
- Britannia

価格取得は外部API/スクレイピングで行い、為替レートを適用して日本円換算価格を計算する。
"""

import argparse
import logging
import sys
import time
import random
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

from .spreadsheet import SpreadsheetClient
from .config import Config

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


class ExchangeRateFetcher:
    """為替レート取得クラス"""

    @staticmethod
    def get_rates() -> dict[str, float]:
        """
        現在の為替レートを取得

        Returns:
            dict: 通貨ペア → レート の辞書
                  例: {"USD_クレカ": 155.0, "SGD_クレカ": 115.0, ...}
        """
        rates = {}

        # TODO: 実際の為替レートAPIから取得
        # 現時点ではデフォルト値を使用
        default_rates = {
            "USD": 155.0,
            "SGD": 115.0,
            "EUR": 165.0,
            "GBP": 195.0,
            "NZD": 92.0,
        }

        for currency, rate in default_rates.items():
            # クレカレート（通常レート + 手数料）
            rates[f"{currency}_クレカ"] = rate * 1.02
            # Wiseレート（ほぼ実勢レート）
            rates[f"{currency}_Wise"] = rate

        return rates


class BullionstarPriceFetcher:
    """Bullionstar価格取得クラス"""

    API_BASE = "https://www.bullionstar.com"
    API_PATH = "/product/filter/desktop"

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })

    def get_price(self, url: str) -> Optional[dict]:
        """
        商品URLから価格を取得

        Args:
            url: 商品URL

        Returns:
            dict: {"price": float, "currency": str, "in_stock": bool} or None
        """
        # URLからロケーションを判定
        if "bullionstar.com" in url and ".us" not in url and ".co.nz" not in url:
            base_url = "https://www.bullionstar.com"
            location_id = 1
            currency = "SGD"
        elif "bullionstar.us" in url:
            base_url = "https://www.bullionstar.us"
            location_id = 2
            currency = "USD"
        elif "bullionstar.co.nz" in url:
            base_url = "https://www.bullionstar.co.nz"
            location_id = 3
            currency = "NZD"
        else:
            logger.warning(f"未対応のBullionstar URL: {url}")
            return None

        try:
            # 商品ページから価格を取得（APIで全商品を取得して検索）
            api_url = f"{base_url}{self.API_PATH}"
            response = self._session.get(
                api_url,
                params={"locationId": location_id, "page": 1},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            # 商品を検索
            result = data.get("result", {})
            for group in result.get("groups", []):
                for product in group.get("products", []):
                    product_url = product.get("url", "")
                    if product_url.startswith("/"):
                        product_url = f"{base_url}{product_url}"

                    if product_url == url or url.endswith(product.get("url", "")):
                        price = product.get("buyPrice", 0) or product.get("price", 0)
                        in_stock = product.get("inStock", True)

                        return {
                            "price": float(price),
                            "currency": currency,
                            "in_stock": in_stock,
                        }

            logger.warning(f"商品が見つかりません: {url}")
            return None

        except requests.RequestException as e:
            logger.error(f"Bullionstar価格取得エラー: {e}")
            return None


class SupplierPriceFetcher:
    """仕入れ先価格取得統合クラス"""

    def __init__(self):
        self.bullionstar = BullionstarPriceFetcher()
        self.exchange_rates = ExchangeRateFetcher.get_rates()

    def fetch_price(self, url: str, site: str) -> Optional[dict]:
        """
        仕入れ先サイトから価格を取得

        Args:
            url: 商品URL
            site: サイト名 (bullionstar, apmex, britannia)

        Returns:
            dict: 価格情報 or None
        """
        site_lower = site.lower()

        if "bullionstar" in site_lower:
            return self.bullionstar.get_price(url)
        elif "apmex" in site_lower:
            # TODO: APMEX価格取得実装
            logger.warning("APMEX価格取得は未実装です")
            return None
        elif "britannia" in site_lower:
            # TODO: Britannia価格取得実装
            logger.warning("Britannia価格取得は未実装です")
            return None
        else:
            logger.warning(f"未対応のサイト: {site}")
            return None

    def calculate_jpy_price(
        self,
        price: float,
        currency: str,
        exchange_type: str = "クレカ"
    ) -> tuple[float, float]:
        """
        現地通貨価格を日本円に換算

        Args:
            price: 現地通貨価格
            currency: 通貨コード (USD, SGD, etc.)
            exchange_type: 為替種類 (クレカ, Wise)

        Returns:
            tuple: (日本円価格, 為替レート)
        """
        rate_key = f"{currency}_{exchange_type}"
        rate = self.exchange_rates.get(rate_key, 0)

        if rate == 0:
            logger.warning(f"為替レートが見つかりません: {rate_key}")
            return 0, 0

        jpy_price = price * rate
        return jpy_price, rate


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="仕入れ先価格取得")
    parser.add_argument("--site", type=str, help="対象サイト (bullionstar, apmex, britannia, all)")
    parser.add_argument("--dry-run", action="store_true", help="ドライラン（更新しない）")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=== 仕入れ先価格取得開始 ===")

    # 設定の検証
    errors = Config.validate()
    if errors and not args.dry_run:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    # 価格取得クラス初期化
    fetcher = SupplierPriceFetcher()

    # 仕入れ先商品一覧を取得
    logger.info("仕入れ先商品一覧を取得中...")
    suppliers = client.get_supplier_products()
    logger.info(f"対象商品数: {len(suppliers)}件")

    if not suppliers:
        logger.info("価格取得対象の商品がありません")
        return

    # サイトフィルタリング
    target_site = args.site.lower() if args.site else "all"
    if target_site != "all":
        suppliers = [
            s for s in suppliers
            if target_site in s.get("supplier_site", "").lower()
        ]
        logger.info(f"サイト '{target_site}' でフィルタ後: {len(suppliers)}件")

    # 価格を取得・更新
    success_count = 0
    fail_count = 0
    skip_count = 0

    for supplier in suppliers:
        url = supplier.get("supplier_product_url", "")
        site = supplier.get("supplier_site", "")
        product_name = supplier.get("supplier_product_name", "不明")[:30]

        if not url:
            skip_count += 1
            continue

        logger.info(f"処理中: {product_name}...")

        # 価格取得
        price_info = fetcher.fetch_price(url, site)

        if not price_info:
            fail_count += 1
            logger.warning(f"  → 価格取得失敗")
            continue

        price = price_info["price"]
        currency = price_info["currency"]
        in_stock = price_info["in_stock"]

        # 為替種類を取得（デフォルトはクレカ）
        exchange_type = supplier.get("exchange_type", "クレカ") or "クレカ"

        # 日本円換算
        jpy_price, rate = fetcher.calculate_jpy_price(price, currency, exchange_type)

        # 前回価格との比較
        prev_price = supplier.get("current_price", 0) or 0

        change_rate = 0
        if prev_price > 0:
            change_rate = ((price - prev_price) / prev_price) * 100

        logger.info(
            f"  → 価格: {currency} {price:.2f} = ¥{jpy_price:,.0f} "
            f"({'在庫あり' if in_stock else '在庫なし'})"
        )
        if change_rate != 0:
            logger.info(f"     変動率: {change_rate:+.2f}%")

        # 更新データを準備
        update_data = {
            "現在価格（現地通貨）": price,
            "取引通貨": currency,
            "在庫状況": "In Stock" if in_stock else "Out of Stock",
            "為替種類": exchange_type,
            "為替レート": rate,
            "日本円換算価格": int(jpy_price),
            "最終価格更新日時": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
            "前回価格（現地通貨）": prev_price if prev_price > 0 else "",
            "価格変動率": round(change_rate, 2) if change_rate != 0 else "",
        }

        if args.dry_run:
            logger.info(f"  → [ドライラン] 更新スキップ")
            success_count += 1
        else:
            # スプレッドシートを更新
            if client.update_supplier_price(url, update_data):
                success_count += 1
                logger.info(f"  → 更新成功")
            else:
                fail_count += 1
                logger.error(f"  → 更新失敗")

        # レート制限対策
        time.sleep(random.uniform(0.5, 1.5))

    # 結果サマリー
    logger.info("=== 結果 ===")
    logger.info(f"成功: {success_count}件")
    logger.info(f"失敗: {fail_count}件")
    logger.info(f"スキップ: {skip_count}件")

    if fail_count > 0:
        logger.warning("一部の価格取得に失敗しました")

    logger.info("=== 仕入れ先価格取得完了 ===")


if __name__ == "__main__":
    main()
