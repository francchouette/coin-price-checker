"""
APMEX 商品一覧クロール実行スクリプト

商品一覧を取得してスプレッドシートに保存する。
Bright Data Browser APIを使用してJavaScriptレンダリングを行う。
"""

import sys
import logging
import time
import random
import re
import asyncio
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright

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


class BrightDataBrowserClient:
    """Bright Data Browser APIクライアント（Playwright使用）"""

    def __init__(self, ws_endpoint: str):
        self.ws_endpoint = ws_endpoint
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def connect(self, timeout: int = 120000, max_retries: int = 3):
        """ブラウザに接続（リトライ機能付き）"""
        self._playwright = await async_playwright().start()
        logger.info(f"Bright Data Browser APIに接続中... (タイムアウト: {timeout/1000}秒)")

        last_error = None
        for attempt in range(max_retries):
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    self.ws_endpoint,
                    timeout=timeout
                )
                logger.info("Bright Data Browser APIに接続しました")
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()
                return
            except Exception as e:
                last_error = e
                error_msg = str(e)
                if "503" in error_msg or "No free browsers" in error_msg:
                    wait_time = 60 * (attempt + 1)  # 60秒, 120秒, 180秒
                    logger.warning(f"ブラウザプールが空いていません。{wait_time}秒後にリトライします... (試行 {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Browser API接続エラー: {e}")
                    raise

        logger.error(f"Browser API接続エラー（リトライ上限）: {last_error}")
        raise last_error

    async def close(self):
        """ブラウザを閉じる"""
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def navigate(self, url: str, wait_selector: str = None, timeout: int = 60000) -> str:
        """
        URLに遷移してHTMLを取得する（初回アクセス用）

        Args:
            url: 取得するURL
            wait_selector: 待機するセレクタ
            timeout: タイムアウト（ミリ秒）

        Returns:
            str: HTML文字列（失敗時は空文字列）
        """
        try:
            # ページ遷移
            await self._page.goto(url, timeout=timeout, wait_until="domcontentloaded")

            # 商品カードが読み込まれるまで待機
            if wait_selector:
                try:
                    await self._page.wait_for_selector(wait_selector, timeout=45000)
                    logger.info(f"セレクタ '{wait_selector}' を検出")
                except Exception:
                    logger.warning(f"セレクタ '{wait_selector}' が見つかりません、追加待機します")
                    await self._page.wait_for_timeout(5000)

            # 追加の待機（動的コンテンツ用）
            await self._page.wait_for_timeout(3000)

            # HTMLを取得
            html = await self._page.content()
            return html

        except Exception as e:
            logger.error(f"Browser API エラー: {e}")
            return ""

    async def click_next_page(self, wait_selector: str = None) -> tuple[bool, str]:
        """
        次ページボタンをクリックして遷移する

        Args:
            wait_selector: 待機するセレクタ

        Returns:
            tuple[bool, str]: (成功したか, HTML文字列)
        """
        try:
            # 次ページボタンを探す（複数のセレクタを試す）
            next_selectors = [
                'a[aria-label="Next page"]',
                'a[rel="next"]',
                '.pagination a.next',
                '.pagination li:last-child a',
                'a[href*="page="]:has-text("Next")',
                'a[href*="page="]:has-text(">")',
                '.mod-pagination a:last-of-type',
            ]

            next_btn = None
            for selector in next_selectors:
                try:
                    next_btn = await self._page.query_selector(selector)
                    if next_btn:
                        # ボタンが無効化されていないか確認
                        is_disabled = await next_btn.get_attribute('disabled')
                        classes = await next_btn.get_attribute('class') or ''
                        if is_disabled or 'disabled' in classes:
                            next_btn = None
                            continue
                        logger.info(f"次ページボタンを検出: {selector}")
                        break
                except Exception:
                    continue

            if not next_btn:
                logger.info("次ページボタンが見つかりません（最終ページ）")
                return False, ""

            # クリック前のURL
            old_url = self._page.url

            # クリックして遷移を待つ
            await next_btn.click()
            await self._page.wait_for_timeout(2000)

            # URLが変わったか、商品が再読み込みされるまで待機
            if wait_selector:
                try:
                    await self._page.wait_for_selector(wait_selector, timeout=30000)
                except Exception:
                    pass

            # 追加の待機
            await self._page.wait_for_timeout(3000)

            new_url = self._page.url
            logger.info(f"ページ遷移: {old_url} → {new_url}")

            # HTMLを取得
            html = await self._page.content()
            return True, html

        except Exception as e:
            logger.error(f"次ページ遷移エラー: {e}")
            return False, ""

    async def get_current_html(self) -> str:
        """現在のページのHTMLを取得"""
        try:
            return await self._page.content()
        except Exception as e:
            logger.error(f"HTML取得エラー: {e}")
            return ""

    async def fetch_product_detail(self, url: str, timeout: int = 30000) -> str:
        """
        商品詳細ページのHTMLを取得する（新しいページで開く）

        Args:
            url: 商品詳細ページのURL
            timeout: タイムアウト（ミリ秒）

        Returns:
            str: HTML文字列（失敗時は空文字列）
        """
        detail_page = None
        try:
            detail_page = await self._context.new_page()
            await detail_page.goto(url, timeout=timeout, wait_until="domcontentloaded")

            # 商品情報が読み込まれるまで待機
            try:
                await detail_page.wait_for_selector('.product-description, .mod-product-description, #product-description', timeout=10000)
            except Exception:
                pass

            await detail_page.wait_for_timeout(2000)

            html = await detail_page.content()
            return html

        except Exception as e:
            logger.warning(f"詳細ページ取得エラー ({url}): {e}")
            return ""
        finally:
            if detail_page:
                await detail_page.close()


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
        last_page = self._progress.get(category, 0)
        return last_page + 1 if last_page > 0 else 1

    def update_progress(self, category: str, page: int, status: str = "進行中"):
        """進捗を更新"""
        try:
            timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

            data = self._sheet.get_all_values()
            row_num = None
            for i, row in enumerate(data[1:], start=2):
                if row and row[0] == category:
                    row_num = i
                    break

            row_data = [category, str(page), status, timestamp]

            if row_num:
                self._sheet.update(f'A{row_num}:D{row_num}', [row_data], value_input_option='RAW')
            else:
                self._sheet.append_row(row_data, value_input_option='RAW')

            self._progress[category] = page

        except Exception as e:
            logger.warning(f"進捗更新エラー: {e}")

    def mark_complete(self, category: str, total_pages: int):
        """カテゴリ完了をマーク"""
        self.update_progress(category, total_pages, "完了")
        if category in self._progress:
            del self._progress[category]


class SpreadsheetSaver:
    """スプレッドシート保存クラス"""

    def __init__(self):
        self._client = None
        self._spreadsheet = None
        self._sheet = None
        self._existing_urls = set()
        self._url_to_row = {}
        self._urls_need_detail = set()  # 説明・仕様が空のURL
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

            sheet_name = Config.SHEET_MASTER_APMEX
            try:
                self._sheet = self._spreadsheet.worksheet(sheet_name)
                logger.info(f"既存シート '{sheet_name}' を使用")
            except gspread.WorksheetNotFound:
                self._sheet = self._spreadsheet.add_worksheet(title=sheet_name, rows=5000, cols=20)
                logger.info(f"新規シート '{sheet_name}' を作成")

            existing_data = self._sheet.get_all_values()

            # ヘッダーがない場合は追加
            if not existing_data:
                # データが空の場合はヘッダーを追加
                self._sheet.append_row(SHEET_HEADERS, value_input_option='RAW')
                logger.info("ヘッダー行を追加（空シート）")
                existing_data = [SHEET_HEADERS]
            elif existing_data[0] != SHEET_HEADERS and existing_data[0][0] != "URL":
                # 1行目がヘッダーではない場合、ヘッダーを先頭に挿入
                self._sheet.insert_row(SHEET_HEADERS, 1, value_input_option='RAW')
                logger.info("ヘッダー行を先頭に挿入")
                # 行番号がずれるので再取得
                existing_data = self._sheet.get_all_values()
            else:
                logger.info("ヘッダー行を確認")

            if len(existing_data) > 1:
                # ヘッダーのインデックスを確認（説明=7, 仕様=8）
                desc_idx = 7  # 説明列
                spec_idx = 8  # 仕様列
                for i, row in enumerate(existing_data[1:], start=2):
                    if row and row[0]:
                        url = row[0]
                        self._existing_urls.add(url)
                        self._url_to_row[url] = i
                        # 説明・仕様が空の場合は詳細取得対象
                        desc = row[desc_idx] if len(row) > desc_idx else ""
                        spec = row[spec_idx] if len(row) > spec_idx else ""
                        if not desc.strip() and not spec.strip():
                            self._urls_need_detail.add(url)

            logger.info(f"既存商品数: {len(self._existing_urls)}件")
            logger.info(f"詳細未取得: {len(self._urls_need_detail)}件")

            self._progress_tracker = ProgressTracker(self._spreadsheet)
            if not self._progress_tracker.connect():
                logger.warning("進捗トラッカーの初期化に失敗（続行します）")

            return True

        except Exception as e:
            logger.error(f"スプレッドシート接続エラー: {e}")
            return False

    def get_start_page(self, category: str) -> int:
        if self._progress_tracker:
            return self._progress_tracker.get_start_page(category)
        return 1

    def update_progress(self, category: str, page: int):
        if self._progress_tracker:
            self._progress_tracker.update_progress(category, page)

    def mark_category_complete(self, category: str, total_pages: int):
        if self._progress_tracker:
            self._progress_tracker.mark_complete(category, total_pages)

    def needs_detail_update(self, url: str) -> bool:
        """指定URLの商品が詳細取得を必要とするかチェック"""
        return url in self._urls_need_detail

    def mark_detail_updated(self, url: str):
        """詳細取得完了をマーク"""
        self._urls_need_detail.discard(url)

    def save_products(self, products: list[ApmexProduct]) -> tuple[int, int]:
        """商品をスプレッドシートに保存"""
        if not products or not self._sheet:
            return 0, 0

        new_rows = []
        update_count = 0

        for product in products:
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
                row_num = self._url_to_row.get(product.url)
                if row_num:
                    try:
                        self._sheet.update(f'A{row_num}:O{row_num}', [row_data], value_input_option='RAW')
                        update_count += 1
                    except Exception as e:
                        logger.warning(f"更新エラー: {e}")
            else:
                new_rows.append(row_data)
                self._existing_urls.add(product.url)

        if new_rows:
            try:
                self._sheet.append_rows(new_rows, value_input_option='RAW')
            except Exception as e:
                logger.error(f"追加エラー: {e}")
                return 0, update_count

        return len(new_rows), update_count


def parse_html_products(html: str, category: str, timestamp: str, base_url: str) -> list[ApmexProduct]:
    """HTMLから商品リストをパース"""
    products = []

    try:
        soup = BeautifulSoup(html, 'html.parser')

        # 商品カードを検索
        items = soup.select('.mod-product-card')

        if not items:
            # フォールバック: 他のセレクタを試す
            items = soup.select('[class*="product-card"]')

        for item in items:
            try:
                product = parse_product_card(item, category, timestamp, base_url)
                if product:
                    products.append(product)
            except Exception as e:
                logger.warning(f"商品パースエラー: {e}")
                continue

    except Exception as e:
        logger.error(f"HTMLパースエラー: {e}")

    return products


def parse_product_card(item, category: str, timestamp: str, base_url: str) -> ApmexProduct:
    """商品カードをパース"""
    # URL取得
    link = item.select_one('a.item-link[href]')
    if not link:
        link = item.select_one('a[href*="/product/"]')

    if not link:
        return None

    href = link.get('href', '')
    if not href or '/product/' not in href:
        return None

    url = href if href.startswith('http') else f"{base_url}{href}"

    # 商品名取得
    name = link.get('data-product-name', '')
    if not name:
        title_elem = item.select_one('.mod-product-title')
        if title_elem:
            name = title_elem.get_text(strip=True)

    if not name or len(name) < 3:
        return None

    # 価格取得
    price = 0.0
    text = item.get_text()
    match = re.search(r'\$([0-9,]+(?:\.[0-9]{2})?)', text)
    if match:
        price = float(match.group(1).replace(',', ''))

    # 商品ID取得
    product_id = link.get('data-product-id', '')
    if not product_id:
        match = re.search(r'/product/(\d+)', url)
        if match:
            product_id = match.group(1)

    # 画像URL取得
    images = []
    for img in item.select('img[src]'):
        src = img.get('src') or img.get('data-src')
        if src and not src.startswith('data:'):
            full_src = src if src.startswith('http') else f"{base_url}{src}"
            if full_src not in images:
                images.append(full_src)

    # 在庫状態
    in_stock = True
    text_lower = text.lower()
    for pattern in ["out of stock", "sold out", "unavailable"]:
        if pattern in text_lower:
            in_stock = False
            break

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


def has_next_page(html: str, current_page: int) -> bool:
    """次ページがあるかどうかを確認"""
    try:
        soup = BeautifulSoup(html, 'html.parser')

        # ページネーションリンクを探す
        next_page = current_page + 1
        next_link = soup.select_one(f'a[href*="page={next_page}"]')
        if next_link:
            return True

        # 他のパターン
        next_btn = soup.select_one('.pagination a.next, [aria-label="Next page"]')
        if next_btn:
            return True

        return False
    except Exception:
        return False


def parse_product_detail(html: str) -> dict:
    """商品詳細ページから説明・仕様を取得"""
    result = {
        "description": "",
        "specification": "",
        "images": [],
    }

    try:
        soup = BeautifulSoup(html, 'html.parser')

        # 商品説明を取得（複数のセレクタを試す）
        desc_selectors = [
            '.product-description',
            '[data-testid="product-description"]',
            '.mod-product-description',
            '#product-description',
            '.description-content',
            '.product-details-description',
        ]
        for selector in desc_selectors:
            desc_elem = soup.select_one(selector)
            if desc_elem:
                result["description"] = desc_elem.get_text(separator=' ', strip=True)[:1000]
                break

        # 仕様を取得（スペック表やリストから）
        spec_selectors = [
            '.product-specifications',
            '.mod-product-specs',
            '.specifications-table',
            '#product-specs',
            '.product-attributes',
            'table.specs',
        ]
        for selector in spec_selectors:
            spec_elem = soup.select_one(selector)
            if spec_elem:
                result["specification"] = spec_elem.get_text(separator=' | ', strip=True)[:500]
                break

        # 仕様が見つからない場合、リスト形式を試す
        if not result["specification"]:
            spec_list = soup.select('.product-spec-item, .spec-row, .attribute-row')
            if spec_list:
                specs = []
                for item in spec_list[:10]:
                    specs.append(item.get_text(separator=': ', strip=True))
                result["specification"] = ' | '.join(specs)[:500]

        # 追加の画像を取得
        img_selectors = [
            '.product-gallery img',
            '.product-images img',
            '.mod-product-image img',
            '[data-testid="product-image"] img',
        ]
        for selector in img_selectors:
            imgs = soup.select(selector)
            if imgs:
                for img in imgs[:5]:
                    src = img.get('src') or img.get('data-src')
                    if src and not src.startswith('data:') and src not in result["images"]:
                        result["images"].append(src)
                break

    except Exception as e:
        logger.warning(f"詳細ページパースエラー: {e}")

    return result


async def run_incremental_async(category: str = None, reset: bool = False, max_pages: int = None):
    """インクリメンタル方式でクロール実行（非同期）"""
    logger.info("=" * 60)
    logger.info("APMEX 商品一覧クロール（Browser API方式）")
    logger.info("=" * 60)

    start_time = datetime.now()

    # Browser API確認
    if not Config.is_brightdata_browser_enabled():
        logger.error("BRIGHTDATA_BROWSER_WS が設定されていません")
        sys.exit(1)

    logger.info("Bright Data Browser API: 有効")

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

    # Bright Data Browser クライアント
    client = BrightDataBrowserClient(Config.BRIGHTDATA_BROWSER_WS)
    await client.connect()

    try:
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
        WAIT_SELECTOR = ".mod-product-card, [class*='product-item'], .product-list"

        for cat_info in MAIN_CATEGORIES:
            cat_slug = cat_info["slug"]
            cat_name = cat_info["name"]
            logger.info(f"\n=== カテゴリ: {cat_name} ===")

            # 初回アクセス
            url = f"{BASE_URL}/category/{cat_slug}"
            logger.info(f"  初回アクセス: {url}")

            html = await client.navigate(url, wait_selector=WAIT_SELECTOR)

            if not html:
                logger.error(f"  HTMLの取得に失敗しました")
                continue

            page_num = 1
            last_page = page_num

            while True:
                if max_pages and page_num > max_pages:
                    logger.info(f"  最大ページ数({max_pages})に達しました")
                    break

                try:
                    # デバッグ: HTMLの長さを出力
                    logger.info(f"  ページ{page_num}: HTML取得成功: {len(html)} bytes")

                    # 商品セレクタの存在確認
                    if "mod-product-card" in html:
                        logger.info("  ✓ mod-product-card クラスを検出")
                    elif "product-card" in html:
                        logger.info("  ✓ product-card クラスを検出")
                    else:
                        logger.warning("  ✗ 商品カードクラスが見つかりません")
                        soup = BeautifulSoup(html, 'html.parser')
                        title = soup.find('title')
                        logger.info(f"  ページタイトル: {title.text if title else 'なし'}")

                    # Cloudflareチャレンジページかどうか確認
                    if "Just a moment" in html or "challenge-platform" in html:
                        logger.error(f"  Cloudflareチャレンジが解決されませんでした")
                        break

                    # 商品をパース
                    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                    products = parse_html_products(html, cat_name, timestamp, BASE_URL)

                    if not products:
                        logger.info(f"  ページ{page_num}: 商品なし - 終了")
                        break

                    logger.info(f"  ページ{page_num}: {len(products)}件を取得")

                    # デバッグ: 最初の3件の商品URLを表示
                    for i, p in enumerate(products[:3]):
                        logger.info(f"    [{i+1}] {p.url}")

                    # 詳細が未取得の商品を抽出（新規 or 説明・仕様が空）
                    products_need_detail = [
                        p for p in products
                        if p.url not in saver._existing_urls or saver.needs_detail_update(p.url)
                    ]
                    if products_need_detail:
                        logger.info(f"    詳細取得対象: {len(products_need_detail)}件")
                        for i, product in enumerate(products_need_detail):
                            try:
                                detail_html = await client.fetch_product_detail(product.url)
                                if detail_html:
                                    detail = parse_product_detail(detail_html)
                                    if detail["description"]:
                                        product.description = detail["description"]
                                    if detail["specification"]:
                                        product.specification = detail["specification"]
                                    if detail["images"]:
                                        # 既存の画像に追加
                                        for img in detail["images"]:
                                            if img not in product.images and len(product.images) < 5:
                                                product.images.append(img)
                                    # 詳細取得完了をマーク
                                    saver.mark_detail_updated(product.url)
                                    logger.info(f"      [{i+1}/{len(products_need_detail)}] 詳細取得完了: {product.name[:30]}...")
                                # レート制限対策
                                await asyncio.sleep(random.uniform(1.0, 2.0))
                            except Exception as e:
                                logger.warning(f"      詳細取得エラー: {e}")

                    # スプレッドシートに保存
                    new_count, update_count = saver.save_products(products)
                    total_new += new_count
                    total_update += update_count
                    logger.info(f"    → 保存完了: 新規 {new_count}件, 更新 {update_count}件")

                    # 進捗を更新
                    saver.update_progress(cat_name, page_num)
                    last_page = page_num

                    # 次ページへクリックで遷移
                    has_next, next_html = await client.click_next_page(wait_selector=WAIT_SELECTOR)

                    if not has_next:
                        logger.info(f"  最終ページ: {page_num}")
                        saver.mark_category_complete(cat_name, page_num)
                        break

                    html = next_html
                    page_num += 1

                    # レート制限対策
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                except Exception as e:
                    logger.error(f"ページ取得エラー: ページ{page_num} - {e}")
                    import traceback
                    traceback.print_exc()
                    saver.update_progress(cat_name, last_page)
                    break

            # カテゴリ間で待機
            await asyncio.sleep(2)

    finally:
        await client.close()

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("\n" + "=" * 60)
    logger.info(f"クロール完了")
    logger.info(f"  新規追加: {total_new}件")
    logger.info(f"  更新: {total_update}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒（{elapsed/60:.1f}分）")
    logger.info("=" * 60)


def run_incremental(category: str = None, reset: bool = False, max_pages: int = None):
    """インクリメンタル方式でクロール実行（同期ラッパー）"""
    asyncio.run(run_incremental_async(category=category, reset=reset, max_pages=max_pages))


if __name__ == "__main__":
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
