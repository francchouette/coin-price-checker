"""
Britannia Coin Company 商品一覧クロール実行スクリプト

商品一覧を取得してスプレッドシートに保存する。
ページごとに保存することで、途中で中断しても取得済みデータは保持される。
進捗を記録し、中断した場所から再開可能。
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

from .config import Config
from .crawlers.britannia import BritanniaProduct

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
    "価格(GBP)",
    "在庫",
    "カテゴリ",
    "サブカテゴリ",
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
PROGRESS_SHEET_NAME = "クロール進捗_Britannia"


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
            sheet_name = Config.SHEET_MASTER_BRITANNIA
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

    def save_products(self, products: list[BritanniaProduct]) -> tuple[int, int]:
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
                        self._sheet.update(f'A{row_num}:N{row_num}', [row_data], value_input_option='RAW')
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


def run_incremental(category: str = None, reset: bool = False):
    """
    インクリメンタル方式でクロール実行

    ページごとにスプレッドシートに保存することで、
    途中で中断しても取得済みデータは保持される。
    進捗を記録し、中断した場所から再開可能。

    Args:
        category: 特定カテゴリのみ取得する場合に指定
        reset: 進捗をリセットして最初から取得する場合True
    """
    logger.info("=" * 60)
    logger.info("Britannia Coin Company 商品一覧クロール（インクリメンタル方式）")
    logger.info("=" * 60)

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
    categories = [category] if category else ["gold-coins", "silver-coins"]

    total_new = 0
    total_update = 0

    BASE_URL = "https://britanniacoincompany.com"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-GB",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        page = context.new_page()

        for cat in categories:
            logger.info(f"\n=== カテゴリ: {cat} ===")

            # 開始ページを取得（リセットの場合は1から）
            if reset:
                start_page = 1
                logger.info("  進捗リセット: ページ1から開始")
            else:
                start_page = saver.get_start_page(cat)
                if start_page > 1:
                    logger.info(f"  前回の続きから再開: ページ{start_page}から")
                else:
                    logger.info("  新規開始: ページ1から")

            page_num = start_page
            last_page = page_num

            while True:
                url = f"{BASE_URL}/buy-coins/{cat}/" if page_num == 1 else f"{BASE_URL}/buy-coins/{cat}/?pg={page_num}"

                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(2000)

                    items = page.query_selector_all(".product-listings__item")

                    if not items:
                        logger.info(f"  ページ{page_num}: 商品なし - 終了")
                        break

                    logger.info(f"  ページ{page_num}: {len(items)}件を取得中...")

                    # 商品をパース
                    products = []
                    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

                    for item in items:
                        try:
                            link = item.query_selector("a[href]")
                            if not link:
                                continue

                            href = link.get_attribute("href")
                            if not href:
                                continue

                            product_url = href if href.startswith("http") else f"{BASE_URL}{href}"

                            name_elem = item.query_selector("h2, h3, .product-title, [class*='title']")
                            name = name_elem.inner_text().strip() if name_elem else ""
                            if not name:
                                continue

                            price = 0.0
                            in_stock = False

                            buy_btn = item.query_selector("a.product-listings__item__button")
                            if buy_btn:
                                btn_text = buy_btn.inner_text().strip()
                                if "buy" in btn_text.lower():
                                    in_stock = True
                                    match = re.search(r'£([\d,]+\.?\d*)', btn_text)
                                    if match:
                                        price = float(match.group(1).replace(',', ''))

                            images = []
                            img = item.query_selector("img")
                            if img:
                                src = img.get_attribute("src") or img.get_attribute("data-src")
                                if src:
                                    full_src = src if src.startswith("http") else f"{BASE_URL}{src}"
                                    images.append(full_src)

                            products.append(BritanniaProduct(
                                url=product_url,
                                name=name,
                                price=price,
                                currency="GBP",
                                in_stock=in_stock,
                                images=images,
                                category=cat,
                                subcategory="",
                                scraped_at=timestamp,
                            ))

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
                    saver.update_progress(cat, page_num)
                    last_page = page_num

                    # 次ページがあるかチェック
                    next_link = page.query_selector(f"a[href*='pg={page_num + 1}']")
                    if not next_link:
                        logger.info(f"  最終ページ: {page_num}")
                        # カテゴリ完了をマーク
                        saver.mark_category_complete(cat, page_num)
                        break

                    page_num += 1
                    time.sleep(random.uniform(1.5, 3.0))

                except Exception as e:
                    logger.error(f"ページ取得エラー: {url} - {e}")
                    # エラー時も進捗を保存（次回はここから再開）
                    saver.update_progress(cat, last_page)
                    break

            # カテゴリ間で少し待機
            time.sleep(2)

        browser.close()

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("\n" + "=" * 60)
    logger.info(f"クロール完了")
    logger.info(f"  新規追加: {total_new}件")
    logger.info(f"  更新: {total_update}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒（{elapsed/60:.1f}分）")
    logger.info("=" * 60)


if __name__ == "__main__":
    # --category=xxx で特定カテゴリのみ
    # --reset で進捗をリセットして最初から
    category = None
    reset = False

    for arg in sys.argv:
        if arg.startswith("--category="):
            category = arg.split("=")[1]
        elif arg == "--reset":
            reset = True

    run_incremental(category=category, reset=reset)
