"""
APMEX 商品一覧クロール実行スクリプト

商品一覧を取得してスプレッドシートに保存する。
Bright Data Web Unlocker APIを使用してCloudflareをバイパスする。
"""

import sys
import logging
import time
import random
import re
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

import gspread
from google.auth import default
from google.oauth2.service_account import Credentials

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

# Bright Data Web Unlocker API エンドポイント
BRIGHTDATA_API_URL = "https://api.brightdata.com/request"


class BrightDataClient:
    """Bright Data Web Unlocker APIクライアント"""

    def __init__(self, api_key: str, zone: str = "web_unlocker1"):
        self.api_key = api_key
        self.zone = zone
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        })

    def fetch(self, url: str, timeout: int = 120) -> str:
        """
        URLのHTMLを取得する

        Args:
            url: 取得するURL
            timeout: タイムアウト秒数

        Returns:
            str: HTML文字列（失敗時は空文字列）
        """
        try:
            payload = {
                "zone": self.zone,
                "url": url,
                "format": "raw"
            }

            response = self.session.post(
                BRIGHTDATA_API_URL,
                json=payload,
                timeout=timeout
            )

            if response.status_code == 200:
                return response.text
            else:
                logger.error(f"Bright Data API エラー: {response.status_code} - {response.text[:200]}")
                return ""

        except requests.Timeout:
            logger.error(f"Bright Data API タイムアウト: {url}")
            return ""
        except Exception as e:
            logger.error(f"Bright Data API 例外: {e}")
            return ""


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
            if not existing_data:
                self._sheet.append_row(SHEET_HEADERS, value_input_option='RAW')
                logger.info("ヘッダー行を追加")
                existing_data = [SHEET_HEADERS]

            if len(existing_data) > 1:
                for i, row in enumerate(existing_data[1:], start=2):
                    if row:
                        self._existing_urls.add(row[0])
                        self._url_to_row[row[0]] = i

            logger.info(f"既存商品数: {len(self._existing_urls)}件")

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


def run_incremental(category: str = None, reset: bool = False, max_pages: int = None):
    """インクリメンタル方式でクロール実行"""
    logger.info("=" * 60)
    logger.info("APMEX 商品一覧クロール（Web Unlocker API方式）")
    logger.info("=" * 60)

    start_time = datetime.now()

    # Web Unlocker API確認
    if not Config.is_brightdata_api_enabled():
        logger.error("BRIGHTDATA_API_KEY が設定されていません")
        sys.exit(1)

    logger.info("Bright Data Web Unlocker API: 有効")

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

    # Bright Data クライアント
    client = BrightDataClient(Config.BRIGHTDATA_API_KEY, Config.BRIGHTDATA_ZONE)

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

    for cat_info in MAIN_CATEGORIES:
        cat_slug = cat_info["slug"]
        cat_name = cat_info["name"]
        logger.info(f"\n=== カテゴリ: {cat_name} ===")

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

                # Web Unlocker APIでHTML取得
                html = client.fetch(url)

                if not html:
                    logger.error(f"  HTMLの取得に失敗しました")
                    break

                # デバッグ: HTMLの長さと内容の一部を出力
                logger.info(f"  HTML取得成功: {len(html)} bytes")
                logger.debug(f"  HTML先頭500文字: {html[:500]}")

                # 商品セレクタの存在確認
                if "mod-product-card" in html:
                    logger.info("  ✓ mod-product-card クラスを検出")
                elif "product-card" in html:
                    logger.info("  ✓ product-card クラスを検出")
                else:
                    logger.warning("  ✗ 商品カードクラスが見つかりません")
                    # HTMLの構造を確認するためタイトルを出力
                    from bs4 import BeautifulSoup as BS
                    soup = BS(html, 'html.parser')
                    title = soup.find('title')
                    logger.info(f"  ページタイトル: {title.text if title else 'なし'}")
                    # 主要なクラス名を出力
                    classes = set()
                    for elem in soup.find_all(class_=True)[:100]:
                        classes.update(elem.get('class', []))
                    logger.info(f"  検出されたクラス(最初の100要素): {sorted(classes)}")
                    # 商品リンクの存在確認
                    product_links = soup.select('a[href*="/product/"]')
                    logger.info(f"  /product/ リンク数: {len(product_links)}")
                    if product_links:
                        logger.info(f"  サンプルリンク: {product_links[0].get('href')}")
                    # HTMLの一部を出力（先頭1000文字）
                    logger.info(f"  HTML先頭1000文字: {html[:1000]}")

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

                # スプレッドシートに保存
                new_count, update_count = saver.save_products(products)
                total_new += new_count
                total_update += update_count
                logger.info(f"    → 保存完了: 新規 {new_count}件, 更新 {update_count}件")

                # 進捗を更新
                saver.update_progress(cat_name, page_num)
                last_page = page_num

                # 次ページがあるかチェック
                if not has_next_page(html, page_num):
                    logger.info(f"  最終ページ: {page_num}")
                    saver.mark_category_complete(cat_name, page_num)
                    break

                page_num += 1

                # レート制限対策
                time.sleep(random.uniform(2.0, 4.0))

            except Exception as e:
                logger.error(f"ページ取得エラー: {url} - {e}")
                saver.update_progress(cat_name, last_page)
                break

        # カテゴリ間で待機
        time.sleep(2)

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("\n" + "=" * 60)
    logger.info(f"クロール完了")
    logger.info(f"  新規追加: {total_new}件")
    logger.info(f"  更新: {total_update}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒（{elapsed/60:.1f}分）")
    logger.info("=" * 60)


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
