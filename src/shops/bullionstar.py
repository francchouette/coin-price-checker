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
    CURRENCY = "USD"  # デフォルト（実際はサイト表示に依存）
    WAIT_TIME_MS = 5000  # Bot検出対策のため長めに設定
    _detected_currency = None  # 検出された通貨

    # セレクタ
    NAME_SELECTOR = "h1"
    PRICE_TABLE_SELECTOR = ".info tr"
    STOCK_SELECTOR = ".product-default-wrap.product-price-update"

    def _extract_price(self) -> Optional[float]:
        """
        価格を抽出する

        Bullionstarは数量別価格テーブルで表示される
        最初の価格行（1-9個の価格）を取得
        複数の通貨（JPY, USD, SGD等）に対応
        """
        try:
            rows = self.page.query_selector_all(self.PRICE_TABLE_SELECTOR)

            for row in rows:
                text = row.inner_text().strip()

                # ヘッダー行はスキップ
                if "Quantity" in text or "Price" in text:
                    continue

                # 複数通貨パターン: ¥, $, S$, €, £
                patterns = [
                    r'¥([\d,]+\.?\d*)',      # JPY
                    r'S\$([\d,]+\.?\d*)',    # SGD
                    r'\$([\d,]+\.?\d*)',     # USD
                    r'€([\d,]+\.?\d*)',      # EUR
                    r'£([\d,]+\.?\d*)',      # GBP
                ]

                for pattern in patterns:
                    match = re.search(pattern, text)
                    if match:
                        price_str = match.group(1).replace(',', '')
                        price = float(price_str)
                        if price > 0:
                            # 通貨を検出してログ出力
                            logger.info(f"価格検出: {text[:50]}...")
                            return price

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
            # 通貨パターン
            currency_patterns = [
                (r'¥([\d,]+\.?\d*)', '¥'),
                (r'S\$([\d,]+\.?\d*)', 'S$'),
                (r'\$([\d,]+\.?\d*)', '$'),
                (r'€([\d,]+\.?\d*)', '€'),
                (r'£([\d,]+\.?\d*)', '£'),
            ]

            # プロモーション価格を試す
            price_new = self.page.query_selector(".price-new")
            if price_new:
                text = price_new.inner_text().strip()
                for pattern, symbol in currency_patterns:
                    match = re.search(pattern, text)
                    if match:
                        price_str = match.group(1).replace(',', '')
                        return float(price_str)

            # ページ全体のテキストから価格を探す
            body_text = self.page.inner_text("body")
            for pattern, symbol in currency_patterns:
                matches = re.findall(pattern, body_text)
                for price_str in matches:
                    price = float(price_str.replace(',', ''))
                    # 妥当な価格範囲（10以上）
                    if price > 10:
                        logger.info(f"フォールバックで価格検出: {symbol}{price_str}")
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
