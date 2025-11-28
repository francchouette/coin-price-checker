"""
APMEX スクレイパー
"""

import re
import logging
from typing import Optional

from .base import BaseScraper

logger = logging.getLogger(__name__)


class ApmexScraper(BaseScraper):
    """APMEX用スクレイパー"""

    SHOP_NAME = "APMEX"
    CURRENCY = "USD"
    WAIT_TIME_MS = 8000  # Bot検出対策のため長めに設定
    WAIT_UNTIL = "domcontentloaded"  # networkidleだとタイムアウトするため

    # セレクタ
    NAME_SELECTOR = "h1"
    PRICE_SELECTOR = ".mod-product-pricing .price"
    PRICE_DISCOUNTED_SELECTOR = ".mod-product-pricing .price.discounted"

    def _extract_price(self) -> Optional[float]:
        """
        価格を抽出する

        APMEXは .mod-product-pricing 内に価格を表示
        割引価格がある場合は .price.discounted を優先
        """
        try:
            # まず割引価格を試す
            discounted = self.page.query_selector(self.PRICE_DISCOUNTED_SELECTOR)
            if discounted and discounted.is_visible():
                text = discounted.inner_text().strip()
                price = self._parse_usd_price(text)
                if price:
                    return price

            # 通常価格を試す
            price_element = self.page.query_selector(self.PRICE_SELECTOR)
            if price_element and price_element.is_visible():
                text = price_element.inner_text().strip()
                price = self._parse_usd_price(text)
                if price:
                    return price

            # フォールバック: .mod-product-pricing 全体から探す
            return self._extract_price_fallback()

        except Exception as e:
            logger.error(f"価格抽出エラー (APMEX): {e}")
            return None

    def _extract_price_fallback(self) -> Optional[float]:
        """
        フォールバック: 別の方法で価格を探す
        """
        try:
            pricing_container = self.page.query_selector(".mod-product-pricing")
            if pricing_container:
                text = pricing_container.inner_text().strip()
                # 最初のドル価格を取得
                match = re.search(r'\$([\d,]+\.?\d*)', text)
                if match:
                    price_str = match.group(1).replace(',', '')
                    return float(price_str)

            return None

        except Exception as e:
            logger.error(f"フォールバック価格抽出エラー: {e}")
            return None

    def _parse_usd_price(self, text: str) -> Optional[float]:
        """
        USDの価格テキストをパースする

        Args:
            text: 価格テキスト（例: "$60.35"）

        Returns:
            float: 価格（パース失敗時はNone）
        """
        try:
            match = re.search(r'\$([\d,]+\.?\d*)', text)
            if match:
                price_str = match.group(1).replace(',', '')
                return float(price_str)
            return None
        except (ValueError, IndexError):
            return None

    def _check_stock(self) -> bool:
        """
        在庫状態を確認する

        APMEXは通常、在庫がない場合はページに表示されない
        ため、ページが表示されていれば在庫ありとみなす
        """
        try:
            # "Out of Stock" や "Sold Out" のテキストを探す
            page_text = self.page.inner_text("body")
            out_of_stock_patterns = [
                "out of stock",
                "sold out",
                "unavailable",
            ]
            for pattern in out_of_stock_patterns:
                if pattern in page_text.lower():
                    return False
            return True
        except Exception:
            return True
