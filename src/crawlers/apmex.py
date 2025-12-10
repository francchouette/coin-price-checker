"""
APMEX 商品一覧クローラー

https://www.apmex.com/ から商品一覧を取得する。
playwright-stealthを使用してボット検出を回避する。
"""

import re
import logging
import time
import random
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

# playwright-stealthのインポート
try:
    from playwright_stealth import Stealth
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


@dataclass
class ApmexProduct:
    """APMEXの商品データ"""
    # 基本情報
    url: str
    name: str
    price: float
    currency: str = "USD"
    in_stock: bool = True

    # 詳細情報
    description: str = ""
    specification: str = ""
    images: list[str] = field(default_factory=list)

    # カテゴリ情報
    category: str = ""
    subcategory: str = ""

    # 商品ID（APMEXのSKU）
    product_id: str = ""

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
            "product_id": self.product_id,
            "scraped_at": self.scraped_at,
        }


class ApmexCrawler:
    """APMEX商品クローラー"""

    BASE_URL = "https://www.apmex.com"

    # メインカテゴリ（これらの配下を全てクロール）
    # 仕入れ対象として金貨・銀貨に絞る
    MAIN_CATEGORIES = [
        {"slug": "25000/gold-coins", "name": "gold-coins"},
        {"slug": "26000/silver-coins", "name": "silver-coins"},
    ]

    # 待機時間設定（ボット対策のため長めに設定）
    MIN_WAIT = 3.0
    MAX_WAIT = 6.0
    PAGE_LOAD_WAIT = 5000  # ミリ秒

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
        logger.info("APMEX Crawler: ブラウザを起動中...")

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
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            # クッキーとストレージを有効化
            java_script_enabled=True,
        )

        # JavaScript経由でのwebdriver検出を回避
        self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        self._page = self._context.new_page()

        # playwright-stealthを適用
        if STEALTH_AVAILABLE:
            stealth = Stealth()
            stealth.apply_stealth_sync(self._page)
            logger.info("APMEX Crawler: playwright-stealth適用済み")
        else:
            logger.warning("APMEX Crawler: playwright-stealthが利用できません")

        logger.info("APMEX Crawler: ブラウザ起動完了")

    def stop(self):
        """ブラウザを終了"""
        logger.info("APMEX Crawler: ブラウザを終了中...")

        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

        logger.info("APMEX Crawler: ブラウザ終了完了")

    def _wait(self):
        """ランダムな待機"""
        wait_time = random.uniform(self.MIN_WAIT, self.MAX_WAIT)
        time.sleep(wait_time)

    def _human_like_scroll(self):
        """人間のようなスクロール動作"""
        try:
            # ランダムな位置までスクロール
            scroll_height = self._page.evaluate("document.body.scrollHeight")
            for _ in range(random.randint(2, 5)):
                scroll_to = random.randint(100, min(scroll_height, 2000))
                self._page.evaluate(f"window.scrollTo(0, {scroll_to})")
                time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass

    def crawl_all(self, max_pages_per_category: int = None) -> list[ApmexProduct]:
        """
        全カテゴリの商品一覧を取得

        Args:
            max_pages_per_category: カテゴリあたりの最大ページ数（Noneで無制限）

        Returns:
            list[ApmexProduct]: 商品リスト
        """
        all_products = []
        seen_urls = set()

        # メインカテゴリごとにクロール
        for cat_info in self.MAIN_CATEGORIES:
            category_slug = cat_info["slug"]
            category_name = cat_info["name"]
            logger.info(f"=== メインカテゴリ: {category_name} ===")

            # カテゴリページから全商品を取得（ページネーション対応）
            base_url = f"{self.BASE_URL}/category/{category_slug}"
            products = self._crawl_category_with_pagination(
                base_url, category_name, max_pages=max_pages_per_category
            )

            # 重複を除外
            for product in products:
                if product.url not in seen_urls:
                    seen_urls.add(product.url)
                    all_products.append(product)

            logger.info(f"  → {category_name}: {len(products)}件取得（累計: {len(all_products)}件）")
            self._wait()

        logger.info(f"商品一覧取得完了: {len(all_products)}件")
        return all_products

    def _crawl_category_with_pagination(
        self, base_url: str, category: str, max_pages: int = None
    ) -> list[ApmexProduct]:
        """
        カテゴリページから全商品を取得（ページネーション対応）

        Args:
            base_url: カテゴリページURL
            category: カテゴリ名
            max_pages: 最大ページ数

        Returns:
            list[ApmexProduct]: 商品リスト
        """
        products = []
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        page_num = 1

        while True:
            if max_pages and page_num > max_pages:
                logger.info(f"  最大ページ数({max_pages})に達しました")
                break

            # APMEXのページネーションURLパターン
            url = base_url if page_num == 1 else f"{base_url}?page={page_num}"

            try:
                logger.info(f"  ページ{page_num}を取得中: {url}")

                # ページにアクセス
                response = self._page.goto(url, wait_until="domcontentloaded", timeout=60000)

                if response and response.status == 403:
                    logger.error(f"  403 Forbidden - ボット検出されました")
                    break

                # ページロード待機
                self._page.wait_for_timeout(self.PAGE_LOAD_WAIT)

                # 人間のようなスクロール
                self._human_like_scroll()

                # 商品一覧を取得（複数のセレクタを試す）
                items = self._find_product_items()

                if not items:
                    logger.info(f"  ページ{page_num}: 商品なし - 終了")
                    break

                logger.info(f"  ページ{page_num}: {len(items)}件")

                for item in items:
                    try:
                        product = self._parse_list_item(item, category, timestamp)
                        if product:
                            products.append(product)
                    except Exception as e:
                        logger.warning(f"商品パースエラー: {e}")
                        continue

                # 次ページがあるかチェック
                if not self._has_next_page(page_num):
                    logger.info(f"  最終ページ: {page_num}")
                    break

                page_num += 1
                self._wait()

            except Exception as e:
                logger.error(f"カテゴリ取得エラー: {url} - {e}")
                break

        return products

    def _find_product_items(self) -> list:
        """商品アイテム要素を検索"""
        # APMEXの商品カードセレクタ（優先度順）
        selectors = [
            ".mod-product-card",  # メイン商品カード
            ".grid-view > *",     # グリッドビューの子要素
            "[class*='product-card']",
            ".product-item",
        ]

        for selector in selectors:
            try:
                items = self._page.query_selector_all(selector)
                if items and len(items) > 0:
                    logger.debug(f"  セレクタ '{selector}' で {len(items)}件見つかりました")
                    return items
            except Exception:
                continue

        return []

    def _has_next_page(self, current_page: int) -> bool:
        """次ページがあるかどうかを確認"""
        try:
            # ページネーションの次へボタンを探す
            next_selectors = [
                f"a[href*='page={current_page + 1}']",
                ".pagination a.next",
                "[aria-label='Next page']",
                "button:has-text('Next')",
                ".pagination-next",
            ]

            for selector in next_selectors:
                try:
                    next_btn = self._page.query_selector(selector)
                    if next_btn and next_btn.is_visible():
                        return True
                except Exception:
                    continue

            return False

        except Exception:
            return False

    def _parse_list_item(
        self,
        item,
        category: str,
        timestamp: str
    ) -> Optional[ApmexProduct]:
        """
        商品一覧の1アイテムをパース

        Args:
            item: 商品要素
            category: カテゴリ名
            timestamp: 取得日時

        Returns:
            ApmexProduct: 商品データ（パース失敗時はNone）
        """
        try:
            # URL取得
            url = self._extract_product_url(item)
            if not url:
                return None

            # 商品名取得
            name = self._extract_product_name(item)
            if not name:
                return None

            # 価格取得
            price = self._extract_product_price(item)

            # 商品ID取得
            product_id = self._extract_product_id(item, url)

            # 画像URL取得
            images = self._extract_product_images(item)

            # 在庫状態
            in_stock = self._check_product_stock(item)

            return ApmexProduct(
                url=url,
                name=name,
                price=price,
                currency="USD",
                in_stock=in_stock,
                images=images,
                category=category,
                subcategory="",
                product_id=product_id,
                scraped_at=timestamp,
            )

        except Exception as e:
            logger.warning(f"商品パースエラー: {e}")
            return None

    def _extract_product_url(self, item) -> Optional[str]:
        """商品URLを抽出"""
        try:
            # item-linkクラスのリンクを優先
            link = item.query_selector("a.item-link[href]")
            if link:
                href = link.get_attribute("href")
                if href and "/product/" in href:
                    return href if href.startswith("http") else f"{self.BASE_URL}{href}"

            # 他のリンクを探す
            link = item.query_selector("a[href*='/product/']")
            if link:
                href = link.get_attribute("href")
                if href:
                    return href if href.startswith("http") else f"{self.BASE_URL}{href}"

            return None
        except Exception:
            return None

    def _extract_product_name(self, item) -> Optional[str]:
        """商品名を抽出"""
        try:
            # APMEXの商品名セレクタ
            name_selectors = [
                ".mod-product-title",  # APMEXメイン
                ".mod-product-title span",
                "a.item-link[data-product-name]",  # data属性から取得
                "[class*='title']",
                "[class*='name']",
            ]

            for selector in name_selectors:
                try:
                    elem = item.query_selector(selector)
                    if elem:
                        # data-product-name属性を優先
                        name = elem.get_attribute("data-product-name")
                        if name and len(name) > 3:
                            return name.strip()
                        # inner_textで取得
                        name = elem.inner_text().strip()
                        if name and len(name) > 3:
                            return name
                except Exception:
                    continue

            return None
        except Exception:
            return None

    def _extract_product_price(self, item) -> float:
        """商品価格を抽出"""
        try:
            text = item.inner_text().strip()

            # USD価格パターンを探す
            price_patterns = [
                r'\$([0-9,]+(?:\.[0-9]{2})?)',
                r'USD\s*([0-9,]+(?:\.[0-9]{2})?)',
                r'([0-9,]+(?:\.[0-9]{2})?)\s*USD',
            ]

            for pattern in price_patterns:
                match = re.search(pattern, text)
                if match:
                    price_str = match.group(1).replace(',', '')
                    return float(price_str)

            return 0.0
        except Exception:
            return 0.0

    def _extract_product_id(self, item, url: str) -> str:
        """商品IDを抽出"""
        try:
            # item-linkのdata-product-id属性から取得
            link = item.query_selector("a.item-link[data-product-id]")
            if link:
                product_id = link.get_attribute("data-product-id")
                if product_id:
                    return product_id

            # 他の要素のdata-product-id属性を探す
            elem = item.query_selector("[data-product-id]")
            if elem:
                product_id = elem.get_attribute("data-product-id")
                if product_id:
                    return product_id

            # URLから抽出
            match = re.search(r'/product/(\d+)', url)
            if match:
                return match.group(1)

            return ""
        except Exception:
            return ""

    def _extract_product_images(self, item) -> list[str]:
        """商品画像URLを抽出"""
        images = []
        try:
            img_selectors = ["img[src]", "img[data-src]"]

            for selector in img_selectors:
                for img in item.query_selector_all(selector):
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if src and not src.startswith("data:"):
                        full_src = src if src.startswith("http") else f"{self.BASE_URL}{src}"
                        if full_src not in images:
                            images.append(full_src)
        except Exception:
            pass

        return images[:5]  # 最大5枚

    def _check_product_stock(self, item) -> bool:
        """在庫状態を確認"""
        try:
            text = item.inner_text().lower()
            out_of_stock_patterns = [
                "out of stock",
                "sold out",
                "unavailable",
                "no longer available",
            ]
            for pattern in out_of_stock_patterns:
                if pattern in text:
                    return False
            return True
        except Exception:
            return True


def crawl_apmex(max_pages_per_category: int = None) -> list[ApmexProduct]:
    """
    APMEXの商品一覧を取得するヘルパー関数

    Args:
        max_pages_per_category: カテゴリあたりの最大ページ数

    Returns:
        list[ApmexProduct]: 商品リスト
    """
    with ApmexCrawler() as crawler:
        return crawler.crawl_all(max_pages_per_category=max_pages_per_category)
