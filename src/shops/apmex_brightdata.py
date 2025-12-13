"""
APMEX スクレイパー（Bright Data Browser API版）

Bright Data Browser APIを使用してAPMEXのボット対策を回避する。
"""

import re
import logging
import asyncio
from typing import Optional
from dataclasses import dataclass

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .base import ScrapedData

logger = logging.getLogger(__name__)


@dataclass
class BrightDataConfig:
    """Bright Data設定"""
    ws_endpoint: str
    timeout: int = 120000
    max_retries: int = 3


class ApmexBrightDataScraper:
    """APMEX用スクレイパー（Bright Data Browser API版）"""

    SHOP_NAME = "APMEX"
    CURRENCY = "USD"

    def __init__(self, config: BrightDataConfig):
        """
        Args:
            config: Bright Data設定
        """
        self.config = config
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._connected = False

    async def connect(self):
        """Bright Data Browser APIに接続"""
        if self._connected:
            return

        self._playwright = await async_playwright().start()
        logger.info(f"Bright Data Browser APIに接続中... (タイムアウト: {self.config.timeout/1000}秒)")

        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    self.config.ws_endpoint,
                    timeout=self.config.timeout
                )
                logger.info("Bright Data Browser APIに接続しました")

                # デフォルトコンテキストを使用
                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                    pages = self._context.pages
                    if pages:
                        self._page = pages[0]
                    else:
                        self._page = await self._context.new_page()
                else:
                    self._context = await self._browser.new_context()
                    self._page = await self._context.new_page()

                self._connected = True
                logger.info("ページ準備完了")
                return

            except Exception as e:
                last_error = e
                error_msg = str(e)
                if "503" in error_msg or "No free browsers" in error_msg:
                    wait_time = 60 * (attempt + 1)
                    logger.warning(
                        f"ブラウザプールが空いていません。{wait_time}秒後にリトライします... "
                        f"(試行 {attempt + 1}/{self.config.max_retries})"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Browser API接続エラー: {e}")
                    raise

        logger.error(f"Browser API接続エラー（リトライ上限）: {last_error}")
        raise last_error

    async def close(self):
        """接続を閉じる"""
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._connected = False

    async def scrape(self, url: str) -> ScrapedData:
        """
        商品ページをスクレイピングする

        Args:
            url: 商品ページURL

        Returns:
            ScrapedData: スクレイピング結果
        """
        try:
            if not self._connected:
                await self.connect()

            logger.info(f"スクレイピング開始 (Bright Data): {url}")

            # ページにアクセス
            await self._page.goto(url, timeout=60000, wait_until="domcontentloaded")

            # 商品情報が読み込まれるまで待機
            try:
                await self._page.wait_for_selector(
                    '.mod-product-pricing, h1, .product-title',
                    timeout=30000
                )
            except Exception:
                logger.warning("価格セレクタが見つかりません、追加待機します")

            await self._page.wait_for_timeout(3000)

            # HTMLを取得してパース
            html = await self._page.content()
            return self._parse_html(html, url)

        except Exception as e:
            logger.error(f"スクレイピングエラー (APMEX Bright Data): {url} - {e}")
            return ScrapedData(
                product_name="",
                price=0.0,
                currency=self.CURRENCY,
                url=url,
                in_stock=False,
                error=str(e)
            )

    def _parse_html(self, html: str, url: str) -> ScrapedData:
        """
        HTMLをパースして商品情報を抽出

        Args:
            html: HTML文字列
            url: 商品URL

        Returns:
            ScrapedData: スクレイピング結果
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Cloudflareチャレンジチェック
            if "Just a moment" in html or "challenge-platform" in html:
                return ScrapedData(
                    product_name="",
                    price=0.0,
                    currency=self.CURRENCY,
                    url=url,
                    in_stock=False,
                    error="Cloudflareチャレンジが解決されませんでした"
                )

            # 商品名を取得
            product_name = self._extract_product_name(soup)
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
            in_stock = self._check_stock(soup)

            # 価格を取得
            price = self._extract_price(soup)

            if price is None:
                if not in_stock:
                    logger.info(f"在庫切れ商品: {product_name}")
                    return ScrapedData(
                        product_name=product_name,
                        price=0.0,
                        currency=self.CURRENCY,
                        url=url,
                        in_stock=False,
                        error=None
                    )
                return ScrapedData(
                    product_name=product_name,
                    price=0.0,
                    currency=self.CURRENCY,
                    url=url,
                    in_stock=in_stock,
                    error="価格を取得できませんでした"
                )

            logger.info(f"スクレイピング成功: {product_name} - USD {price}")

            return ScrapedData(
                product_name=product_name,
                price=price,
                currency=self.CURRENCY,
                url=url,
                in_stock=in_stock
            )

        except Exception as e:
            logger.error(f"HTMLパースエラー: {e}")
            return ScrapedData(
                product_name="",
                price=0.0,
                currency=self.CURRENCY,
                url=url,
                in_stock=False,
                error=str(e)
            )

    def _extract_product_name(self, soup: BeautifulSoup) -> Optional[str]:
        """商品名を抽出"""
        try:
            # h1タグを優先
            h1 = soup.select_one('h1')
            if h1:
                name = h1.get_text(strip=True)
                if name and len(name) > 3:
                    return name

            # 他のセレクタを試す
            selectors = [
                '.product-title',
                '.mod-product-title',
                '[data-testid="product-title"]',
            ]
            for selector in selectors:
                elem = soup.select_one(selector)
                if elem:
                    name = elem.get_text(strip=True)
                    if name and len(name) > 3:
                        return name

            return None
        except Exception as e:
            logger.error(f"商品名抽出エラー: {e}")
            return None

    def _extract_price(self, soup: BeautifulSoup) -> Optional[float]:
        """価格を抽出"""
        try:
            # メインコンテンツエリアを探す（商品詳細ページの本体）
            main_content = soup.select_one('.product-details, .product-page, main, #main-content')
            search_area = main_content if main_content else soup

            # 1. 割引価格を最優先で探す（.price.discounted は正しい価格を持っていることが多い）
            discounted_elems = search_area.select('.price.discounted')
            for elem in discounted_elems:
                text = elem.get_text(strip=True)
                price = self._parse_usd_price(text)
                logger.info(f"[DEBUG] .price.discounted: {text} -> {price}")
                if price and price > 0:
                    return price

            # 2. 数量別価格テーブルを探す
            price_tables = search_area.select('table')
            for table in price_tables:
                rows = table.select('tr')
                for row in rows:
                    cells = row.select('td')
                    if len(cells) >= 2:
                        # 価格が含まれているセルを探す
                        for cell in cells:
                            text = cell.get_text(strip=True)
                            if '$' in text:
                                price = self._parse_usd_price(text)
                                logger.info(f"[DEBUG] テーブルセル: {text} -> {price}")
                                if price and price > 0:
                                    return price

            # 3. 商品価格の一般的なセレクタを試す
            price_selectors = [
                '.product-price-value',
                '.product-buy-price',
                '.add-to-cart-price',
                '[data-price]',
            ]
            for selector in price_selectors:
                elem = search_area.select_one(selector)
                if elem:
                    # data-price属性をチェック
                    data_price = elem.get('data-price')
                    if data_price:
                        try:
                            price = float(data_price)
                            if price > 0:
                                logger.info(f"[DEBUG] data-price: {price}")
                                return price
                        except ValueError:
                            pass
                    text = elem.get_text(strip=True)
                    price = self._parse_usd_price(text)
                    if price and price > 0:
                        logger.info(f"[DEBUG] {selector}: {text} -> {price}")
                        return price

            # 4. ページ内の全価格から妥当な価格を探す（フォールバック）
            page_text = search_area.get_text()
            all_prices = re.findall(r'\$([\d,]+\.?\d*)', page_text)
            logger.info(f"[DEBUG] ページ内の全価格（先頭10件）: {all_prices[:10]}")

            # 有効な価格を探す（$1以上）
            valid_prices = []
            for price_str in all_prices:
                try:
                    price = float(price_str.replace(',', ''))
                    if price > 0:
                        valid_prices.append(price)
                except ValueError:
                    continue

            if valid_prices:
                # 最初に見つかった価格を返す（ページ上部の価格が正しいことが多い）
                first_price = valid_prices[0]
                logger.info(f"[DEBUG] ページ内の価格: {valid_prices[:5]}, 選択: {first_price}")
                return first_price

            return None

        except Exception as e:
            logger.error(f"価格抽出エラー: {e}")
            return None

    def _parse_usd_price(self, text: str) -> Optional[float]:
        """USD価格テキストをパース"""
        try:
            match = re.search(r'\$([\d,]+\.?\d*)', text)
            if match:
                price_str = match.group(1).replace(',', '')
                return float(price_str)
            return None
        except (ValueError, IndexError):
            return None

    def _check_stock(self, soup: BeautifulSoup) -> bool:
        """在庫状態を確認"""
        try:
            page_text = soup.get_text().lower()
            out_of_stock_patterns = [
                "out of stock",
                "sold out",
                "unavailable",
                "no longer available",
            ]
            for pattern in out_of_stock_patterns:
                if pattern in page_text:
                    return False
            return True
        except Exception:
            return True


async def scrape_apmex_urls(
    urls: list[str],
    ws_endpoint: str,
    wait_between: float = 2.0
) -> list[ScrapedData]:
    """
    複数のAPMEX URLをスクレイピングする

    Bright Data Browser APIのページナビゲーション制限を回避するため、
    各URLごとに新しいブラウザ接続を作成する。

    Args:
        urls: スクレイピング対象のURLリスト
        ws_endpoint: Bright Data Browser APIのWebSocketエンドポイント
        wait_between: リクエスト間の待機時間（秒）

    Returns:
        list[ScrapedData]: スクレイピング結果のリスト
    """
    config = BrightDataConfig(ws_endpoint=ws_endpoint)
    results = []

    for i, url in enumerate(urls):
        logger.info(f"APMEX スクレイピング ({i+1}/{len(urls)}): {url}")

        # 各URLごとに新しいスクレイパーインスタンスを作成
        scraper = ApmexBrightDataScraper(config)
        try:
            await scraper.connect()
            result = await scraper.scrape(url)
            results.append(result)
        except Exception as e:
            logger.error(f"スクレイピングエラー: {url} - {e}")
            results.append(ScrapedData(
                product_name="",
                price=0.0,
                currency="USD",
                url=url,
                in_stock=False,
                error=str(e)
            ))
        finally:
            await scraper.close()

        # リクエスト間の待機
        if i < len(urls) - 1:
            await asyncio.sleep(wait_between)

    return results
