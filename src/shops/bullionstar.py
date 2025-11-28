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

        注意: 在庫切れ商品は価格テーブルが表示されないため、
        フォールバックは使用せずNoneを返す（誤った価格を記録しないため）
        """
        try:
            # まず在庫状態を確認 - 在庫切れの場合は価格取得をスキップ
            if not self._check_stock():
                logger.info("在庫切れのため価格取得をスキップ")
                return None

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
                            logger.info(f"価格検出: {currency} {price} ({text[:50]}...)")
                            return price

            # 価格テーブルで見つからない場合はNoneを返す
            # （フォールバックは誤った価格を拾うリスクがあるため使用しない）
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
