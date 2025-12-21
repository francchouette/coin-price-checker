"""
Bullionstar スクレイパー
"""

import re
import json
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
        self._is_out_of_stock = False  # 在庫切れフラグ

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

        在庫切れ商品の場合はJSON-LD（構造化データ）から価格を取得する
        """
        try:
            # まず価格テーブルから取得を試みる
            price = self._extract_price_from_table()
            if price is not None:
                return price

            # 価格テーブルで見つからない場合、JSON-LDから取得を試みる
            logger.warning("価格テーブルから価格を取得できませんでした。JSON-LDから取得を試みます...")
            price = self._extract_price_from_json_ld()
            if price is not None:
                self._is_out_of_stock = True  # JSON-LDから取得 = 在庫切れの可能性が高い
                return price

            return None

        except Exception as e:
            logger.error(f"価格抽出エラー (Bullionstar): {e}")
            return None

    def _extract_price_from_table(self) -> Optional[float]:
        """価格テーブルから価格を抽出する"""
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

            return None

        except Exception as e:
            logger.error(f"価格テーブル抽出エラー: {e}")
            return None

    def _extract_price_from_json_ld(self) -> Optional[float]:
        """JSON-LD（構造化データ）から価格を抽出する"""
        try:
            # JSON-LDスクリプトを取得
            scripts = self.page.query_selector_all('script[type="application/ld+json"]')

            for script in scripts:
                try:
                    content = script.inner_text()
                    data = json.loads(content)

                    # JSON-LDが配列形式の場合（[{...}, {...}]）
                    items = data if isinstance(data, list) else [data]

                    for item in items:
                        # 辞書でない場合はスキップ
                        if not isinstance(item, dict):
                            continue

                        # Productスキーマを探す
                        if item.get("@type") == "Product":
                            offers = item.get("offers", {})

                            # offersが配列の場合
                            if isinstance(offers, list):
                                for offer in offers:
                                    price = self._parse_offer_price(offer)
                                    if price:
                                        return price
                            # offersがオブジェクトの場合
                            elif isinstance(offers, dict):
                                price = self._parse_offer_price(offers)
                                if price:
                                    return price

                except json.JSONDecodeError:
                    continue

            return None

        except Exception as e:
            logger.error(f"JSON-LD抽出エラー: {e}")
            return None

    def _parse_offer_price(self, offer: dict) -> Optional[float]:
        """オファーデータから価格を抽出する（全通貨対応）"""
        try:
            currency = offer.get("priceCurrency", "")
            price_str = offer.get("price", "")

            if not price_str:
                return None

            # サポートする通貨リスト
            supported_currencies = ["JPY", "USD", "SGD", "EUR", "GBP"]

            if currency in supported_currencies:
                price = float(str(price_str).replace(",", ""))
                if price > 0:
                    self._detected_currency = currency
                    logger.info(f"JSON-LD価格検出: {currency} {price}")
                    return price

            return None

        except (ValueError, TypeError) as e:
            logger.error(f"オファー価格パースエラー: {e}")
            return None

    def _check_stock(self) -> bool:
        """
        在庫状態を確認する

        _is_out_of_stockフラグが設定されている場合は在庫なしを返す
        （JSON-LDから価格を取得した場合 = 価格テーブルがない = 在庫切れ）

        それ以外はstatus属性で判定:
        - IN_STOCK: 在庫あり
        - IN_TRANSIT: 入荷予定（PRE-SALE）- 購入可能なので在庫ありとして扱う
        - UNAVAILABLE: 在庫なし
        """
        # JSON-LDから価格を取得した場合は在庫切れ
        if self._is_out_of_stock:
            logger.info("在庫状態: Out of Stock（JSON-LDから価格取得のため）")
            return False

        try:
            element = self.page.query_selector(self.STOCK_SELECTOR)
            if element:
                status = element.get_attribute("status")
                if status:
                    # IN_STOCK と IN_TRANSIT は在庫あり（購入可能）として扱う
                    is_in_stock = status.upper() in ("IN_STOCK", "IN_TRANSIT")
                    logger.info(f"在庫状態: {'In Stock' if is_in_stock else 'Out of Stock'} (status={status})")
                    return is_in_stock
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
