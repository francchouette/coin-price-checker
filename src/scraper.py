"""
スクレイピング管理モジュール

Playwrightを使用してショップ別のスクレイピングを実行する。
"""

import random
import time
import logging
from typing import Optional
from dataclasses import dataclass

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from .config import Config
from .shops import BaseScraper, ScrapedData, BullionstarScraper, ApmexScraper

logger = logging.getLogger(__name__)


@dataclass
class ScrapeTarget:
    """スクレイピング対象"""
    shop_name: str
    url: str
    product_name_hint: str = ""  # スプレッドシートの商品名（参考）


class ScraperManager:
    """スクレイパー管理クラス"""

    # ショップ名とスクレイパークラスのマッピング
    SCRAPER_MAP = {
        "bullionstar": BullionstarScraper,
        "apmex": ApmexScraper,
    }

    def __init__(self):
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._playwright = None

    def __enter__(self):
        """コンテキストマネージャー: 開始"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """コンテキストマネージャー: 終了"""
        self.stop()

    def start(self):
        """ブラウザを起動する"""
        logger.info("ブラウザを起動します...")

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )

        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            java_script_enabled=True,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )

        # WebDriverを隠す
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        self._page = self._context.new_page()
        logger.info("ブラウザを起動しました")

    def stop(self):
        """ブラウザを終了する"""
        logger.info("ブラウザを終了します...")

        if self._page:
            self._page.close()
            self._page = None

        if self._context:
            self._context.close()
            self._context = None

        if self._browser:
            self._browser.close()
            self._browser = None

        if self._playwright:
            self._playwright.stop()
            self._playwright = None

        logger.info("ブラウザを終了しました")

    def get_scraper(self, shop_name: str) -> Optional[BaseScraper]:
        """
        ショップ名に対応するスクレイパーを取得する

        Args:
            shop_name: ショップ名

        Returns:
            BaseScraper: スクレイパーインスタンス（見つからない場合はNone）
        """
        if not self._page:
            logger.error("ブラウザが起動されていません")
            return None

        shop_key = shop_name.lower().strip()
        scraper_class = self.SCRAPER_MAP.get(shop_key)

        if scraper_class:
            return scraper_class(self._page)

        logger.warning(f"未対応のショップ: {shop_name}")
        return None

    def scrape(self, target: ScrapeTarget) -> ScrapedData:
        """
        単一のターゲットをスクレイピングする

        Args:
            target: スクレイピング対象

        Returns:
            ScrapedData: スクレイピング結果
        """
        scraper = self.get_scraper(target.shop_name)
        if not scraper:
            return ScrapedData(
                product_name=target.product_name_hint,
                price=0.0,
                currency="",
                url=target.url,
                in_stock=False,
                error=f"未対応のショップ: {target.shop_name}"
            )

        return scraper.scrape(target.url)

    def scrape_all(self, targets: list[ScrapeTarget]) -> list[ScrapedData]:
        """
        複数のターゲットをスクレイピングする

        Args:
            targets: スクレイピング対象のリスト

        Returns:
            list[ScrapedData]: スクレイピング結果のリスト
        """
        results = []
        total = len(targets)

        for i, target in enumerate(targets, 1):
            logger.info(f"スクレイピング中 ({i}/{total}): {target.url}")

            result = self.scrape(target)
            results.append(result)

            # 次のリクエストまでランダムに待機（最後のリクエスト以外）
            if i < total:
                wait_time = random.uniform(
                    Config.SCRAPE_MIN_WAIT,
                    Config.SCRAPE_MAX_WAIT
                )
                logger.debug(f"待機中: {wait_time:.1f}秒")
                time.sleep(wait_time)

        return results


def detect_shop_from_url(url: str) -> str:
    """
    URLからショップ名を推定する

    Args:
        url: 商品URL

    Returns:
        str: ショップ名（推定できない場合は空文字）
    """
    url_lower = url.lower()

    if "bullionstar.com" in url_lower:
        return "Bullionstar"
    elif "apmex.com" in url_lower:
        return "APMEX"

    return ""
