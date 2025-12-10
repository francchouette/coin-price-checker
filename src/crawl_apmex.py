"""
APMEX 商品一覧クロール実行スクリプト

商品一覧を取得してスプレッドシートに保存する。
ページごとに保存することで、途中で中断しても取得済みデータは保持される。
進捗を記録し、中断した場所から再開可能。

playwright-stealthを使用してボット検出を回避する。
"""

import sys
import logging
import time
import random
import re
from datetime import datetime, timezone, timedelta

import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

# playwright-stealthのインポート
try:
    from playwright_stealth import Stealth
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

from .config import Config
from .crawlers.apmex import ApmexProduct

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# シートのヘッダー
SHEET_HEADERS = [
    "URL",
    "商品名",
    "価格(USD)",
    "在庫",
    "カテゴリ",
    "サブカテゴリ",
    "商品ID",
    "説明",
    "仕様",
    "画像URL1",
    "画像URL2",
    "画像URL3",
    "画像URL4",
    "画像URL5",
    "取得日時",
]

# 進捗管理シート名
PROGRESS_SHEET_NAME = "クロール進捗_APMEX"


class ProgressTracker:
    """クロール進捗管理クラス"""

    def __init__(self, spreadsheet):
        self._spreadsheet = spreadsheet
        self._sheet = None
        self._progress = {}  # {category: last_page}

    def connect(self) -> bool:
        """進捗シートに接続"""
        try:
            try:
                self._sheet = self._spreadsheet.worksheet(PROGRESS_SHEET_NAME)
                logger.info(f"進捗シート '{PROGRESS_SHEET_NAME}' を使用")
            except gspread.WorksheetNotFound:
                self._sheet = self._spreadsheet.add_worksheet(
                    title=PROGRESS_SHEET_NAME, rows=100, cols=5
                )
                self._sheet.append_row(
                    ["カテゴリ", "最終ページ", "状態", "更新日時"],
                    value_input_option='RAW'
                )
                logger.info(f"進捗シート '{PROGRESS_SHEET_NAME}' を作成")

            # 既存の進捗を読み込み
            data = self._sheet.get_all_values()
            if len(data) > 1:
                for row in data[1:]:
                    if len(row) >= 3 and row[0]:
                        category = row[0]
                        last_page = int(row[1]) if row[1].isdigit() else 0
                        status = row[2] if len(row) > 2 else ""
                        # 完了していないカテゴリのみ進捗を記録
                        if status != "完了":
                            self._progress[category] = last_page

            return True
        except Exception as e:
            logger.error(f"進捗シート接続エラー: {e}")
            return False

    def get_start_page(self, category: str) -> int:
        """カテゴリの開始ページを取得"""
        # 前回の最終ページの次から開始
        last_page = self._progress.get(category, 0)
        return last_page + 1 if last_page > 0 else 1

    def update_progress(self, category: str, page: int, status: str = "進行中"):
        """進捗を更新"""
        try:
            timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

            # 既存の行を探す
            data = self._sheet.get_all_values()
            row_num = None
            for i, row in enumerate(data[1:], start=2):
                if row and row[0] == category:
                    row_num = i
                    break

            row_data = [category, str(page), status, timestamp]

            if row_num:
                # 更新
                self._sheet.update(f'A{row_num}:D{row_num}', [row_data], value_input_option='RAW')
            else:
                # 新規追加
                self._sheet.append_row(row_data, value_input_option='RAW')

            self._progress[category] = page

        except Exception as e:
            logger.warning(f"進捗更新エラー: {e}")

    def mark_complete(self, category: str, total_pages: int):
        """カテゴリ完了をマーク"""
        self.update_progress(category, total_pages, "完了")
        # 完了したカテゴリは進捗から削除（次回は最初から）
        if category in self._progress:
            del self._progress[category]

    def reset_progress(self, category: str):
        """進捗をリセット"""
        self.update_progress(category, 0, "リセット")
        if category in self._progress:
            del self._progress[category]


class SpreadsheetSaver:
    """スプレッドシート保存クラス（インクリメンタル保存対応）"""

    def __init__(self):
        self._client = None
        self._spreadsheet = None
        self._sheet = None
        self._existing_urls = set()
        self._url_to_row = {}
        self._progress_tracker = None

    def connect(self) -> bool:
        """スプレッドシートに接続"""
        try:
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive',
            ]

            creds_json = Config.get_google_credentials()
            if creds_json:
                creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
                logger.info("サービスアカウント認証を使用")
            else:
                creds, _ = default(scopes=scopes)
                logger.info("ADC認証を使用")

            self._client = gspread.authorize(creds)
            self._spreadsheet = self._client.open_by_key(Config.SPREADSHEET_ID)

            # 商品シートを取得または作成
            sheet_name = Config.SHEET_MASTER_APMEX
            try:
                self._sheet = self._spreadsheet.worksheet(sheet_name)
                logger.info(f"既存シート '{sheet_name}' を使用")
            except gspread.WorksheetNotFound:
                self._sheet = self._spreadsheet.add_worksheet(title=sheet_name, rows=5000, cols=20)
                logger.info(f"新規シート '{sheet_name}' を作成")

            # 既存データを読み込み
            existing_data = self._sheet.get_all_values()
            if not existing_data:
                self._sheet.append_row(SHEET_HEADERS, value_input_option='RAW')
                logger.info("ヘッダー行を追加")
                existing_data = [SHEET_HEADERS]

            # 既存URLをマッピング
            if len(existing_data) > 1:
                for i, row in enumerate(existing_data[1:], start=2):
                    if row:
                        self._existing_urls.add(row[0])
                        self._url_to_row[row[0]] = i

            logger.info(f"既存商品数: {len(self._existing_urls)}件")

            # 進捗トラッカーを初期化
            self._progress_tracker = ProgressTracker(self._spreadsheet)
            if not self._progress_tracker.connect():
                logger.warning("進捗トラッカーの初期化に失敗（続行します）")

            return True

        except Exception as e:
            logger.error(f"スプレッドシート接続エラー: {e}")
            return False

    def get_start_page(self, category: str) -> int:
        """カテゴリの開始ページを取得"""
        if self._progress_tracker:
            return self._progress_tracker.get_start_page(category)
        return 1

    def update_progress(self, category: str, page: int):
        """進捗を更新"""
        if self._progress_tracker:
            self._progress_tracker.update_progress(category, page)

    def mark_category_complete(self, category: str, total_pages: int):
        """カテゴリ完了をマーク"""
        if self._progress_tracker:
            self._progress_tracker.mark_complete(category, total_pages)

    def save_products(self, products: list[ApmexProduct]) -> tuple[int, int]:
        """
        商品をスプレッドシートに保存

        Args:
            products: 商品リスト

        Returns:
            tuple[int, int]: (新規追加件数, 更新件数)
        """
        if not products or not self._sheet:
            return 0, 0

        new_rows = []
        update_count = 0

        for product in products:
            # 画像URLを最大5つまで展開
            images = product.images[:5] if product.images else []
            images.extend([""] * (5 - len(images)))

            row_data = [
                product.url,
                product.name,
                str(product.price),
                "○" if product.in_stock else "×",
                product.category,
                product.subcategory,
                product.product_id,
                product.description[:500] if product.description else "",
                product.specification[:200] if product.specification else "",
                images[0],
                images[1],
                images[2],
                images[3],
                images[4],
                product.scraped_at,
            ]

            if product.url in self._existing_urls:
                # 更新
                row_num = self._url_to_row.get(product.url)
                if row_num:
                    try:
                        self._sheet.update(f'A{row_num}:O{row_num}', [row_data], value_input_option='RAW')
                        update_count += 1
                    except Exception as e:
                        logger.warning(f"更新エラー: {e}")
            else:
                # 新規追加
                new_rows.append(row_data)
                self._existing_urls.add(product.url)

        # 新規行を一括追加
        if new_rows:
            try:
                self._sheet.append_rows(new_rows, value_input_option='RAW')
            except Exception as e:
                logger.error(f"追加エラー: {e}")
                return 0, update_count

        return len(new_rows), update_count


def run_incremental(category: str = None, reset: bool = False, max_pages: int = None):
    """
    インクリメンタル方式でクロール実行

    ページごとにスプレッドシートに保存することで、
    途中で中断しても取得済みデータは保持される。
    進捗を記録し、中断した場所から再開可能。

    Args:
        category: 特定カテゴリのみ取得する場合に指定
        reset: 進捗をリセットして最初から取得する場合True
        max_pages: カテゴリあたりの最大ページ数
    """
    logger.info("=" * 60)
    logger.info("APMEX 商品一覧クロール（インクリメンタル方式）")
    logger.info("=" * 60)

    if STEALTH_AVAILABLE:
        logger.info("playwright-stealth: 有効")
    else:
        logger.warning("playwright-stealth: 無効（インストールされていません）")

    start_time = datetime.now()

    # 設定検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # スプレッドシートに接続
    saver = SpreadsheetSaver()
    if not saver.connect():
        logger.error("スプレッドシート接続に失敗")
        sys.exit(1)

    # カテゴリ設定
    MAIN_CATEGORIES = [
        {"slug": "25000/gold-coins", "name": "gold-coins"},
        {"slug": "26000/silver-coins", "name": "silver-coins"},
    ]

    if category:
        MAIN_CATEGORIES = [c for c in MAIN_CATEGORIES if c["name"] == category]

    total_new = 0
    total_update = 0

    BASE_URL = "https://www.apmex.com"

    # Bright Data Proxy設定
    proxy_config = None
    if Config.is_brightdata_enabled():
        proxy_url = Config.get_brightdata_proxy_url()
        proxy_config = {"server": proxy_url}
        logger.info("Bright Data Proxy: 有効")
    else:
        logger.warning("Bright Data Proxy: 無効（環境変数が設定されていません）")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )

        # コンテキスト作成（Proxy設定を含む）
        context_options = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if proxy_config:
            context_options["proxy"] = proxy_config

        context = browser.new_context(**context_options)

        # ボット検出回避
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = context.new_page()

        # playwright-stealth適用
        if STEALTH_AVAILABLE:
            stealth = Stealth()
            stealth.apply_stealth_sync(page)
            logger.info("playwright-stealth適用済み")

        for cat_info in MAIN_CATEGORIES:
            cat_slug = cat_info["slug"]
            cat_name = cat_info["name"]
            logger.info(f"\n=== カテゴリ: {cat_name} ===")

            # 開始ページを取得（リセットの場合は1から）
            if reset:
                start_page = 1
                logger.info("  進捗リセット: ページ1から開始")
            else:
                start_page = saver.get_start_page(cat_name)
                if start_page > 1:
                    logger.info(f"  前回の続きから再開: ページ{start_page}から")
                else:
                    logger.info("  新規開始: ページ1から")

            page_num = start_page
            last_page = page_num

            while True:
                if max_pages and page_num > max_pages:
                    logger.info(f"  最大ページ数({max_pages})に達しました")
                    break

                url = f"{BASE_URL}/category/{cat_slug}" if page_num == 1 else f"{BASE_URL}/category/{cat_slug}?page={page_num}"

                try:
                    logger.info(f"  ページ{page_num}を取得中: {url}")

                    # Proxy経由は遅いのでタイムアウトを長めに設定
                    response = page.goto(url, wait_until="domcontentloaded", timeout=120000)

                    if response and response.status == 403:
                        logger.error(f"  403 Forbidden - ボット検出されました")
                        break

                    # Cloudflareチャレンジを待つ（最大30秒）
                    page.wait_for_timeout(10000)

                    # Cloudflareチャレンジページかどうか確認
                    page_content = page.content()
                    if "Just a moment" in page_content or "challenge" in page_content.lower():
                        logger.info("  Cloudflareチャレンジを検出、待機中...")
                        page.wait_for_timeout(15000)

                    page.wait_for_timeout(5000)

                    # 人間のようなスクロール
                    for _ in range(random.randint(2, 4)):
                        scroll_to = random.randint(100, 1500)
                        page.evaluate(f"window.scrollTo(0, {scroll_to})")
                        time.sleep(random.uniform(0.3, 0.7))

                    # 商品一覧を取得
                    items = _find_product_items(page)

                    if not items:
                        logger.info(f"  ページ{page_num}: 商品なし - 終了")
                        break

                    logger.info(f"  ページ{page_num}: {len(items)}件を取得中...")

                    # 商品をパース
                    products = []
                    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

                    for item in items:
                        try:
                            product = _parse_list_item(item, cat_name, timestamp, BASE_URL)
                            if product:
                                products.append(product)
                        except Exception as e:
                            logger.warning(f"商品パースエラー: {e}")
                            continue

                    # ページごとにスプレッドシートに保存
                    if products:
                        new_count, update_count = saver.save_products(products)
                        total_new += new_count
                        total_update += update_count
                        logger.info(f"    → 保存完了: 新規 {new_count}件, 更新 {update_count}件")

                    # 進捗を更新
                    saver.update_progress(cat_name, page_num)
                    last_page = page_num

                    # 次ページがあるかチェック
                    if not _has_next_page(page, page_num):
                        logger.info(f"  最終ページ: {page_num}")
                        # カテゴリ完了をマーク
                        saver.mark_category_complete(cat_name, page_num)
                        break

                    page_num += 1
                    time.sleep(random.uniform(3.0, 6.0))

                except Exception as e:
                    logger.error(f"ページ取得エラー: {url} - {e}")
                    # エラー時も進捗を保存（次回はここから再開）
                    saver.update_progress(cat_name, last_page)
                    break

            # カテゴリ間で少し待機
            time.sleep(3)

        browser.close()

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("\n" + "=" * 60)
    logger.info(f"クロール完了")
    logger.info(f"  新規追加: {total_new}件")
    logger.info(f"  更新: {total_update}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒（{elapsed/60:.1f}分）")
    logger.info("=" * 60)


def _find_product_items(page) -> list:
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
            items = page.query_selector_all(selector)
            if items and len(items) > 0:
                logger.debug(f"  セレクタ '{selector}' で {len(items)}件見つかりました")
                return items
        except Exception:
            continue

    return []


def _has_next_page(page, current_page: int) -> bool:
    """次ページがあるかどうかを確認"""
    try:
        next_selectors = [
            f"a[href*='page={current_page + 1}']",
            ".pagination a.next",
            "[aria-label='Next page']",
            "button:has-text('Next')",
            ".pagination-next",
        ]

        for selector in next_selectors:
            try:
                next_btn = page.query_selector(selector)
                if next_btn and next_btn.is_visible():
                    return True
            except Exception:
                continue

        return False

    except Exception:
        return False


def _parse_list_item(item, category: str, timestamp: str, base_url: str) -> ApmexProduct:
    """商品一覧の1アイテムをパース"""
    # URL取得（item-linkを優先）
    url = None
    try:
        link = item.query_selector("a.item-link[href]")
        if link:
            href = link.get_attribute("href")
            if href and "/product/" in href:
                url = href if href.startswith("http") else f"{base_url}{href}"
        if not url:
            link = item.query_selector("a[href*='/product/']")
            if link:
                href = link.get_attribute("href")
                if href:
                    url = href if href.startswith("http") else f"{base_url}{href}"
    except Exception:
        pass

    if not url:
        return None

    # 商品名取得（data-product-name属性を優先）
    name = None
    name_selectors = [
        ".mod-product-title",
        ".mod-product-title span",
        "a.item-link[data-product-name]",
        "[class*='title']",
    ]
    for selector in name_selectors:
        try:
            elem = item.query_selector(selector)
            if elem:
                # data-product-name属性を優先
                name = elem.get_attribute("data-product-name")
                if name and len(name) > 3:
                    break
                # inner_textで取得
                name = elem.inner_text().strip()
                if name and len(name) > 3:
                    break
        except Exception:
            continue

    if not name:
        return None

    # 価格取得
    price = 0.0
    try:
        text = item.inner_text().strip()
        match = re.search(r'\$([0-9,]+(?:\.[0-9]{2})?)', text)
        if match:
            price = float(match.group(1).replace(',', ''))
    except Exception:
        pass

    # 商品ID取得（data-product-id属性から）
    product_id = ""
    try:
        link = item.query_selector("a.item-link[data-product-id]")
        if link:
            product_id = link.get_attribute("data-product-id") or ""
        if not product_id:
            elem = item.query_selector("[data-product-id]")
            if elem:
                product_id = elem.get_attribute("data-product-id") or ""
        if not product_id:
            match = re.search(r'/product/(\d+)', url)
            if match:
                product_id = match.group(1)
    except Exception:
        pass

    # 画像URL取得
    images = []
    try:
        for img in item.query_selector_all("img[src]"):
            src = img.get_attribute("src") or img.get_attribute("data-src")
            if src and not src.startswith("data:"):
                full_src = src if src.startswith("http") else f"{base_url}{src}"
                if full_src not in images:
                    images.append(full_src)
    except Exception:
        pass

    # 在庫状態
    in_stock = True
    try:
        text = item.inner_text().lower()
        for pattern in ["out of stock", "sold out", "unavailable"]:
            if pattern in text:
                in_stock = False
                break
    except Exception:
        pass

    return ApmexProduct(
        url=url,
        name=name,
        price=price,
        currency="USD",
        in_stock=in_stock,
        images=images[:5],
        category=category,
        subcategory="",
        product_id=product_id,
        scraped_at=timestamp,
    )


if __name__ == "__main__":
    # --category=xxx で特定カテゴリのみ
    # --reset で進捗をリセットして最初から
    # --max-pages=N で最大ページ数を指定
    category = None
    reset = False
    max_pages = None

    for arg in sys.argv:
        if arg.startswith("--category="):
            category = arg.split("=")[1]
        elif arg == "--reset":
            reset = True
        elif arg.startswith("--max-pages="):
            max_pages = int(arg.split("=")[1])

    run_incremental(category=category, reset=reset, max_pages=max_pages)
