"""
Bullionstar スクレイパー
"""

import re
import logging
from typing import Optional

from .base import BaseScraper

logger = logging.getLogger(__name__)


class BullionstarScraper(BaseScraper):
    """Bullionstar用スクレイパー"""

    SHOP_NAME = "Bullionstar"
    CURRENCY = "JPY"
    WAIT_TIME_MS = 5000  # Bot検出対策のため長めに設定

    # セレクタ
    NAME_SELECTOR = "h1"
    PRICE_TABLE_SELECTOR = ".info tr"
    STOCK_SELECTOR = ".product-default-wrap.product-price-update"

    def _extract_price(self) -> Optional[float]:
        """
        価格を抽出する

        Bullionstarは数量別価格テーブルで表示される
        最初の価格行（1-9個の価格）を取得
        """
        try:
            rows = self.page.query_selector_all(self.PRICE_TABLE_SELECTOR)

            for row in rows:
                text = row.inner_text().strip()

                # "1 - 9    ¥126,112.25" のような形式を探す
                # ヘッダー行（"Quantity Price"）はスキップ
                if "Quantity" in text or "Price" in text:
                    continue

                # 円記号を含む価格を抽出
                match = re.search(r'¥([\d,]+\.?\d*)', text)
                if match:
                    price_str = match.group(1).replace(',', '')
                    return float(price_str)

            # テーブルで見つからない場合、他のセレクタを試す
            return self._extract_price_fallback()

        except Exception as e:
            logger.error(f"価格抽出エラー (Bullionstar): {e}")
            return None

    def _extract_price_fallback(self) -> Optional[float]:
        """
        フォールバック: 別のセレクタで価格を探す
        """
        try:
            # プロモーション価格を試す
            price_new = self.page.query_selector(".price-new")
            if price_new:
                text = price_new.inner_text().strip()
                match = re.search(r'¥([\d,]+\.?\d*)', text)
                if match:
                    price_str = match.group(1).replace(',', '')
                    return float(price_str)

            # ページ内の全span要素から円価格を探す
            spans = self.page.query_selector_all("span")
            for span in spans:
                if not span.is_visible():
                    continue
                text = span.inner_text().strip()
                if text.startswith("¥") and len(text) > 2:
                    match = re.search(r'¥([\d,]+\.?\d*)', text)
                    if match:
                        price_str = match.group(1).replace(',', '')
                        price = float(price_str)
                        # 極端に小さい価格は除外（アクセサリなど）
                        if price > 1000:
                            return price

            return None

        except Exception as e:
            logger.error(f"フォールバック価格抽出エラー: {e}")
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
