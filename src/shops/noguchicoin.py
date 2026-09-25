"""
野口コイン（noguchicoin.co.jp）スクレイパー

カラーミーショップ（Shop-Pro）ベースのサイト。
ページ内の var Colorme = {...} JavaScriptオブジェクトから商品情報を取得する。
"""

import json
import logging
import re
from typing import Optional

from playwright.sync_api import Page

from .base import BaseScraper, ScrapedData

logger = logging.getLogger(__name__)


class NoguchicoinScraper(BaseScraper):
    """野口コインのスクレイパー"""

    SHOP_NAME = "Noguchicoin"
    CURRENCY = "JPY"
    WAIT_TIME_MS = 3000
    WAIT_UNTIL = "domcontentloaded"

    def scrape(self, url: str) -> ScrapedData:
        """var Colorme オブジェクトから商品情報を取得"""
        try:
            logger.info(f"スクレイピング開始: {url}")

            self.page.goto(url, wait_until=self.WAIT_UNTIL, timeout=60000)
            self.page.wait_for_timeout(self.WAIT_TIME_MS)

            # var Colorme = {...} をページから抽出
            colorme_data = self._extract_colorme_object()
            if not colorme_data:
                return ScrapedData(
                    product_name="",
                    price=0.0,
                    currency=self.CURRENCY,
                    url=url,
                    in_stock=False,
                    error="Colormeオブジェクトを取得できませんでした",
                )

            product = colorme_data.get("product", {})
            product_name = product.get("name", "")
            sales_price = product.get("sales_price", 0)
            stock_num = product.get("stock_num", 0)

            if not product_name:
                return ScrapedData(
                    product_name="",
                    price=0.0,
                    currency=self.CURRENCY,
                    url=url,
                    in_stock=False,
                    error="商品名を取得できませんでした",
                )

            price = float(sales_price) if sales_price else 0.0
            self._stock_num = int(stock_num) if stock_num is not None else 0
            in_stock = self._stock_num > 0

            logger.info(f"スクレイピング成功: {product_name} - ¥{price:,.0f} (在庫: {self._stock_num})")

            return ScrapedData(
                product_name=product_name,
                price=price,
                currency=self.CURRENCY,
                url=url,
                in_stock=in_stock,
            )

        except Exception as e:
            logger.error(f"スクレイピングエラー: {url} - {e}")
            return ScrapedData(
                product_name="",
                price=0.0,
                currency=self.CURRENCY,
                url=url,
                in_stock=False,
                error=str(e),
            )

    def _extract_colorme_object(self) -> Optional[dict]:
        """ページ内の var Colorme = {...} を抽出してdictに変換"""
        try:
            # JavaScript経由で直接取得を試みる
            result = self.page.evaluate("() => typeof Colorme !== 'undefined' ? Colorme : null")
            if result:
                return result
        except Exception:
            pass

        # フォールバック: HTMLソースから正規表現で抽出
        try:
            html = self.page.content()
            match = re.search(r'var\s+Colorme\s*=\s*({.*?})\s*;', html, re.DOTALL)
            if match:
                return json.loads(match.group(1))
        except Exception as e:
            logger.warning(f"Colormeオブジェクトの正規表現抽出に失敗: {e}")

        return None

    def _extract_price(self) -> Optional[float]:
        """BaseScraper互換（scrapeをオーバーライドしているため直接は呼ばれない）"""
        return None
