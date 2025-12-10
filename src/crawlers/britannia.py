"""
Britannia Coin Company 商品一覧クローラー

https://britanniacoincompany.com/ から商品一覧を取得する。
"""

import re
import logging
import time
import random
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


@dataclass
class BritanniaProduct:
    """Britannia Coin Companyの商品データ"""
    # 基本情報
    url: str
    name: str
    price: float
    currency: str = "GBP"
    in_stock: bool = True

    # 詳細情報（商品詳細ページから取得）
    description: str = ""
    specification: str = ""
    images: list[str] = field(default_factory=list)

    # カテゴリ情報
    category: str = ""
    subcategory: str = ""

    # メタデータ
    scraped_at: str = ""

    def to_dict(self) -> dict:
        """辞書形式に変換"""
        return {
            "url": self.url,
            "name": self.name,
            "price": self.price,
            "currency": self.currency,
            "in_stock": self.in_stock,
            "description": self.description,
            "specification": self.specification,
            "images": ",".join(self.images) if self.images else "",
            "category": self.category,
            "subcategory": self.subcategory,
            "scraped_at": self.scraped_at,
        }


class BritanniaCrawler:
    """Britannia Coin Company商品クローラー"""

    BASE_URL = "https://britanniacoincompany.com"

    # クロール対象のカテゴリURL
    CATEGORY_URLS = [
        # Gold Coins
        ("/buy-coins/gold-coins/bullion/", "Gold Coins", "Bullion"),
        ("/buy-coins/gold-coins/sovereign/", "Gold Coins", "Sovereign"),
        ("/buy-coins/gold-coins/half-sovereign/", "Gold Coins", "Half Sovereign"),
        # Silver Coins
        ("/buy-coins/silver-coins/", "Silver Coins", "All"),
        # Proof Sets
        ("/buy-coins/proof-sets/", "Proof Sets", "All"),
    ]

    # 待機時間設定
    MIN_WAIT = 2.0
    MAX_WAIT = 4.0
    PAGE_LOAD_WAIT = 3000  # ミリ秒

    def __init__(self):
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._playwright = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """ブラウザを起動"""
        logger.info("Britannia Crawler: ブラウザを起動中...")

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
        )

        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        self._page = self._context.new_page()
        logger.info("Britannia Crawler: ブラウザ起動完了")

    def stop(self):
        """ブラウザを終了"""
        logger.info("Britannia Crawler: ブラウザを終了中...")

        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

        logger.info("Britannia Crawler: ブラウザ終了完了")

    def _wait(self):
        """ランダムな待機"""
        wait_time = random.uniform(self.MIN_WAIT, self.MAX_WAIT)
        time.sleep(wait_time)

    def crawl_all(self, include_details: bool = False) -> list[BritanniaProduct]:
        """
        全カテゴリの商品一覧を取得

        Args:
            include_details: 商品詳細ページも取得するか

        Returns:
            list[BritanniaProduct]: 商品リスト
        """
        all_products = []
        seen_urls = set()

        for path, category, subcategory in self.CATEGORY_URLS:
            url = f"{self.BASE_URL}{path}"
            logger.info(f"カテゴリ取得中: {category} > {subcategory}")

            products = self._crawl_category(url, category, subcategory)

            # 重複を除外
            for product in products:
                if product.url not in seen_urls:
                    seen_urls.add(product.url)
                    all_products.append(product)

            self._wait()

        logger.info(f"商品一覧取得完了: {len(all_products)}件")

        # 詳細情報を取得
        if include_details:
            logger.info("商品詳細を取得中...")
            for i, product in enumerate(all_products, 1):
                logger.info(f"詳細取得中 ({i}/{len(all_products)}): {product.name[:30]}...")
                self._fetch_product_details(product)
                self._wait()

        return all_products

    def _crawl_category(self, url: str, category: str, subcategory: str) -> list[BritanniaProduct]:
        """
        カテゴリページから商品一覧を取得

        Args:
            url: カテゴリページURL
            category: カテゴリ名
            subcategory: サブカテゴリ名

        Returns:
            list[BritanniaProduct]: 商品リスト
        """
        products = []
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

        try:
            self._page.goto(url, wait_until="networkidle", timeout=60000)
            self._page.wait_for_timeout(self.PAGE_LOAD_WAIT)

            # 商品一覧を取得
            items = self._page.query_selector_all(".product-listings__item")
            logger.info(f"  {len(items)}件の商品を発見")

            for item in items:
                try:
                    product = self._parse_list_item(item, category, subcategory, timestamp)
                    if product:
                        products.append(product)
                except Exception as e:
                    logger.warning(f"商品パースエラー: {e}")
                    continue

            # ページネーションがあれば次ページも取得
            # TODO: 必要に応じて実装

        except Exception as e:
            logger.error(f"カテゴリ取得エラー: {url} - {e}")

        return products

    def _parse_list_item(
        self,
        item,
        category: str,
        subcategory: str,
        timestamp: str
    ) -> Optional[BritanniaProduct]:
        """
        商品一覧の1アイテムをパース

        Args:
            item: 商品要素
            category: カテゴリ名
            subcategory: サブカテゴリ名
            timestamp: 取得日時

        Returns:
            BritanniaProduct: 商品データ（パース失敗時はNone）
        """
        # リンク・URL
        link = item.query_selector("a[href]")
        if not link:
            return None

        href = link.get_attribute("href")
        if not href:
            return None

        url = href if href.startswith("http") else f"{self.BASE_URL}{href}"

        # 商品名
        name_elem = item.query_selector("h2, h3, .product-title, [class*='title']")
        name = name_elem.inner_text().strip() if name_elem else ""

        if not name:
            return None

        # 価格
        price = 0.0
        in_stock = False

        # BUYボタンから価格を取得
        buy_btn = item.query_selector("a.product-listings__item__button")
        if buy_btn:
            btn_text = buy_btn.inner_text().strip()
            if "buy" in btn_text.lower():
                in_stock = True
                match = re.search(r'£([\d,]+\.?\d*)', btn_text)
                if match:
                    price = float(match.group(1).replace(',', ''))

        # 価格が取れなかった場合、他の要素から探す
        if price == 0:
            price_elem = item.query_selector("[class*='price'], .amount")
            if price_elem:
                text = price_elem.inner_text().strip()
                match = re.search(r'£?([\d,]+\.?\d*)', text)
                if match:
                    price = float(match.group(1).replace(',', ''))

        # 画像
        images = []
        img = item.query_selector("img")
        if img:
            src = img.get_attribute("src") or img.get_attribute("data-src")
            if src:
                full_src = src if src.startswith("http") else f"{self.BASE_URL}{src}"
                images.append(full_src)

        return BritanniaProduct(
            url=url,
            name=name,
            price=price,
            currency="GBP",
            in_stock=in_stock,
            images=images,
            category=category,
            subcategory=subcategory,
            scraped_at=timestamp,
        )

    def _fetch_product_details(self, product: BritanniaProduct):
        """
        商品詳細ページから追加情報を取得

        Args:
            product: 商品データ（詳細情報が追加される）
        """
        try:
            self._page.goto(product.url, wait_until="networkidle", timeout=60000)
            self._page.wait_for_timeout(self.PAGE_LOAD_WAIT)

            # 説明文
            desc_elem = self._page.query_selector(".product-description, .product-content, [class*='description']")
            if desc_elem:
                product.description = desc_elem.inner_text().strip()[:2000]

            # 仕様
            spec_patterns = ["Specification", "Weight:", "Diameter:", "Fineness:"]
            body_text = self._page.inner_text("body")

            specs = []
            for pattern in spec_patterns:
                if pattern in body_text:
                    # パターン以降の文を抽出
                    idx = body_text.find(pattern)
                    if idx >= 0:
                        snippet = body_text[idx:idx+200].split('\n')[0]
                        specs.append(snippet)

            if specs:
                product.specification = " | ".join(specs)

            # 追加画像
            gallery_images = self._page.query_selector_all(
                ".product-image img, [class*='gallery'] img, img[src*='product']"
            )
            for img in gallery_images[:5]:
                src = img.get_attribute("src") or img.get_attribute("data-src")
                if src:
                    full_src = src if src.startswith("http") else f"{self.BASE_URL}{src}"
                    if full_src not in product.images:
                        product.images.append(full_src)

            # 在庫状態を再確認
            buy_btn = self._page.query_selector("a.product-listings__item__button")
            if buy_btn:
                btn_text = buy_btn.inner_text().strip()
                product.in_stock = "buy" in btn_text.lower()

                # 価格を更新
                match = re.search(r'£([\d,]+\.?\d*)', btn_text)
                if match:
                    product.price = float(match.group(1).replace(',', ''))
            else:
                product.in_stock = False

        except Exception as e:
            logger.warning(f"詳細取得エラー: {product.url} - {e}")


def crawl_britannia(include_details: bool = False) -> list[BritanniaProduct]:
    """
    Britannia Coin Companyの商品一覧を取得するヘルパー関数

    Args:
        include_details: 商品詳細ページも取得するか

    Returns:
        list[BritanniaProduct]: 商品リスト
    """
    with BritanniaCrawler() as crawler:
        return crawler.crawl_all(include_details=include_details)
