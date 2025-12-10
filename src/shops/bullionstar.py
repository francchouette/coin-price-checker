"""
Bullionstar スクレイパー
"""

import re
import logging
from typing import Optional

from .base import BaseScraper, ScrapedData

logger = logging.getLogger(__name__)


class BullionstarScraper(BaseScraper):
    """Bullionstar用スクレイパー"""

    SHOP_NAME = "Bullionstar"
    CURRENCY = "JPY"  # デフォルトをJPYに変更
    WAIT_TIME_MS = 5000  # Bot検出対策のため長めに設定

    # セレクタ
    NAME_SELECTOR = "h1"
    PRICE_TABLE_SELECTOR = ".info tr"
    STOCK_SELECTOR = ".product-default-wrap.product-price-update"

    def __init__(self, page):
        super().__init__(page)
        self._detected_currency = None  # 検出された通貨（インスタンス変数）
        self._currency_set = False  # 通貨設定済みフラグ

    def _set_currency_cookie(self):
        """JPY表示用のCookieを設定する"""
        if self._currency_set:
            return

        try:
            # Bullionstarの通貨設定Cookieを追加
            context = self.page.context
            context.add_cookies([
                {
                    "name": "currency",
                    "value": "JPY",
                    "domain": ".bullionstar.com",
                    "path": "/"
                }
            ])
            self._currency_set = True
            logger.info("Bullionstar: JPY通貨Cookieを設定しました")
        except Exception as e:
            logger.warning(f"通貨Cookie設定エラー: {e}")

    def scrape(self, url: str) -> ScrapedData:
        """
        商品ページをスクレイピングする（JPY通貨Cookie設定付き）
        """
        # ページアクセス前にJPY通貨Cookieを設定
        self._set_currency_cookie()
        # 親クラスのscrapeを呼び出す
        return super().scrape(url)

    def _extract_price(self) -> Optional[float]:
        """
        価格を抽出する

        Bullionstarは数量別価格テーブルで表示される
        最初の価格行（1-9個の価格）を取得
        複数の通貨（JPY, USD, SGD等）に対応

        在庫切れ商品でも価格テーブルがあれば価格を取得する
        （在庫状態は別途_check_stockで確認）
        """
        try:
            rows = self.page.query_selector_all(self.PRICE_TABLE_SELECTOR)

            # 複数通貨パターン: US$, S$, ¥, €, £
            # US$を先に試す（$だけだとS$の一部とマッチする可能性があるため）
            patterns = [
                (r'US\$([\d,]+\.?\d*)', 'USD'),    # USD (US$表記)
                (r'¥([\d,]+\.?\d*)', 'JPY'),       # JPY
                (r'S\$([\d,]+\.?\d*)', 'SGD'),     # SGD
                (r'€([\d,]+\.?\d*)', 'EUR'),       # EUR
                (r'£([\d,]+\.?\d*)', 'GBP'),       # GBP
            ]

            for row in rows:
                text = row.inner_text().strip()

                # ヘッダー行はスキップ
                if "Quantity" in text or "Price" in text:
                    continue

                # 数量パターンがある行のみ処理（"1 - 9" や "1 - 99" など）
                if not re.search(r'\d+\s*-\s*\d+', text):
                    continue

                for pattern, currency in patterns:
                    match = re.search(pattern, text)
                    if match:
                        price_str = match.group(1).replace(',', '')
                        price = float(price_str)
                        if price > 0:
                            self._detected_currency = currency
                            logger.info(f"価格検出: {currency} {price} ({text[:50]}...)")
                            return price

            # 価格テーブルで見つからない場合
            logger.warning("価格テーブルから価格を取得できませんでした")
            return None

        except Exception as e:
            logger.error(f"価格抽出エラー (Bullionstar): {e}")
            return None

    def _check_stock(self) -> bool:
        """
        在庫状態を確認する

        status属性で判定:
        - IN_STOCK: 在庫あり
        - UNAVAILABLE: 在庫なし
        """
        try:
            element = self.page.query_selector(self.STOCK_SELECTOR)
            if element:
                status = element.get_attribute("status")
                if status:
                    return status.upper() == "IN_STOCK"
            return True  # 判定できない場合は在庫ありとみなす
        except Exception as e:
            logger.error(f"在庫状態確認エラー: {e}")
            return True

    def _get_currency(self) -> str:
        """
        検出された通貨を返す

        価格抽出時に検出された通貨があればそれを返す
        なければデフォルト（USD）を返す
        """
        return self._detected_currency or self.CURRENCY
