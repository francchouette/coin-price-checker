"""
スクレイパー基底クラス
"""

import re
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

logger = logging.getLogger(__name__)


@dataclass
class ScrapedData:
    """スクレイピング結果のデータクラス"""
    product_name: str
    price: float
    currency: str
    url: str
    in_stock: bool = True
    error: Optional[str] = None


class BaseScraper(ABC):
    """スクレイパーの基底クラス"""

    # サブクラスでオーバーライド
    SHOP_NAME: str = ""
    CURRENCY: str = ""
    WAIT_TIME_MS: int = 3000  # ミリ秒
    WAIT_UNTIL: str = "networkidle"  # ページ読み込み待機戦略

    # セレクタ
    NAME_SELECTOR: str = "h1"
    PRICE_SELECTOR: str = ""
    STOCK_SELECTOR: str = ""

    def __init__(self, page: Page):
        """
        Args:
            page: Playwrightのページオブジェクト
        """
        self.page = page

    def scrape(self, url: str) -> ScrapedData:
        """
        商品ページをスクレイピングする

        Args:
            url: 商品ページURL

        Returns:
            ScrapedData: スクレイピング結果
        """
        try:
            logger.info(f"スクレイピング開始: {url}")

            # ページにアクセス
            self.page.goto(url, wait_until=self.WAIT_UNTIL, timeout=60000)
            self.page.wait_for_timeout(self.WAIT_TIME_MS)

            # 商品名を取得
            product_name = self._extract_product_name()
            if not product_name:
                return ScrapedData(
                    product_name="",
                    price=0.0,
                    currency=self.CURRENCY,
                    url=url,
                    in_stock=False,
                    error="商品名を取得できませんでした"
                )

            # 在庫状態を確認
            in_stock = self._check_stock()

            # 価格を取得
            price = self._extract_price()
            if price is None:
                return ScrapedData(
                    product_name=product_name,
                    price=0.0,
                    currency=self.CURRENCY,
                    url=url,
                    in_stock=in_stock,
                    error="価格を取得できませんでした"
                )

            logger.info(f"スクレイピング成功: {product_name} - {self.CURRENCY} {price}")

            return ScrapedData(
                product_name=product_name,
                price=price,
                currency=self.CURRENCY,
                url=url,
                in_stock=in_stock
            )

        except Exception as e:
            logger.error(f"スクレイピングエラー: {url} - {e}")
            return ScrapedData(
                product_name="",
                price=0.0,
                currency=self.CURRENCY,
                url=url,
                in_stock=False,
                error=str(e)
            )

    def _extract_product_name(self) -> Optional[str]:
        """商品名を抽出する"""
        try:
            element = self.page.query_selector(self.NAME_SELECTOR)
            if element:
                return element.inner_text().strip()
            return None
        except Exception as e:
            logger.error(f"商品名抽出エラー: {e}")
            return None

    @abstractmethod
    def _extract_price(self) -> Optional[float]:
        """価格を抽出する（サブクラスで実装）"""
        pass

    def _check_stock(self) -> bool:
        """在庫状態を確認する（デフォルトはTrue）"""
        return True

    @staticmethod
    def parse_price(text: str, currency_symbol: str = "") -> Optional[float]:
        """
        価格テキストを数値に変換する

        Args:
            text: 価格テキスト（例: "¥126,112.25", "$60.35"）
            currency_symbol: 通貨記号（例: "¥", "$"）

        Returns:
            float: 価格（変換失敗時はNone）
        """
        try:
            # 通貨記号を含むパターン
            if currency_symbol:
                pattern = re.escape(currency_symbol) + r'([\d,]+\.?\d*)'
            else:
                # 一般的な価格パターン
                pattern = r'[\$¥€£]([\d,]+\.?\d*)'

            match = re.search(pattern, text)
            if match:
                price_str = match.group(1).replace(',', '')
                return float(price_str)

            # 数値のみのパターン
            numbers = re.findall(r'[\d,]+\.?\d*', text)
            if numbers:
                price_str = numbers[0].replace(',', '')
                return float(price_str)

            return None
        except (ValueError, IndexError) as e:
            logger.error(f"価格パースエラー: {text} - {e}")
            return None
