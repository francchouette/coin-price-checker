"""
Bullionstar 商品ページ一覧取得スクリプト

Bullionstarの全商品ページURLとカテゴリー情報を取得し、
スプレッドシートの「ブリオンスター商品ページ一覧」シートに保存する。

取得情報:
- 商品名
- URL
- 最上位カテゴリ
- 親カテゴリ
- 子カテゴリ
- ロケーション
- 取得日

次回実行時は上書きせず、新しいデータを下に追加する。

Playwright + APIのハイブリッド方式で在庫切れ商品も含めて取得。
"""

import asyncio
import logging
import re
import sys
import time
import random
import requests
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


@dataclass
class BullionstarProduct:
    """Bullionstar商品データ"""
    name: str
    url: str
    top_category: str      # 最上位カテゴリ (Gold, Silver, Platinum等)
    parent_category: str   # 親カテゴリ (Gold Bars, Gold Coins等)
    child_category: str    # 子カテゴリ (詳細分類)
    location: str          # ロケーション (Singapore, USA, New Zealand)
    fetched_at: str        # 取得日時


# ロケーション定義
LOCATIONS = {
    1: ("Singapore", "https://www.bullionstar.com"),
    2: ("USA", "https://www.bullionstar.us"),
    3: ("New Zealand", "https://www.bullionstar.co.nz"),
}

# カテゴリー構造定義
# 最上位カテゴリー → 親カテゴリー → URLパス
CATEGORY_STRUCTURE = {
    "Gold": {
        "Gold Bars": "/buy/gold-bars",
        "Gold Coins": "/buy/gold-coins",
        "Numismatics Gold": "/buy/numismatics-collectibles-gold-coins",
        "Gold Jewellery": "/buy/gold-jewellery",
    },
    "Silver": {
        "Silver Bars": "/buy/silver-bars",
        "Silver Coins": "/buy/silver-coins-rounds-wafers",
        "Numismatics Silver": "/buy/numismatics-collectibles-silver-coins",
    },
    "Platinum": {
        "Platinum Bars": "/buy/platinum-bars",
        "Platinum Coins": "/buy/platinum-coins",
    },
    "Copper": {
        "Copper Products": "/buy/copper",
    },
    "Jewellery": {
        "Jewellery": "/buy/jewellery",
    },
    "Supplies": {
        "Coin Supplies": "/buy/supplies",
    },
}


class BullionstarProductScraper:
    """Bullionstar商品ページスクレイパー（Playwright版）"""

    # 待機時間設定
    MIN_WAIT = 1.5
    MAX_WAIT = 3.0
    PAGE_LOAD_WAIT = 2000  # ミリ秒

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def start(self):
        """ブラウザを起動"""
        logger.info("Bullionstar Scraper: ブラウザを起動中...")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        self._page = await context.new_page()
        logger.info("Bullionstar Scraper: ブラウザ起動完了")

    async def stop(self):
        """ブラウザを終了"""
        logger.info("Bullionstar Scraper: ブラウザを終了中...")

        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        logger.info("Bullionstar Scraper: ブラウザ終了完了")

    async def _wait(self):
        """ランダムな待機"""
        wait_time = random.uniform(self.MIN_WAIT, self.MAX_WAIT)
        await asyncio.sleep(wait_time)

    async def get_all_products(self) -> list[BullionstarProduct]:
        """
        全ロケーション・全カテゴリの商品ページ一覧を取得

        Returns:
            list[BullionstarProduct]: 商品リスト
        """
        all_products = []
        seen_keys = set()
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

        for location_id, (location_name, base_url) in LOCATIONS.items():
            logger.info(f"\n{'='*60}")
            logger.info(f"ロケーション: {location_name}")
            logger.info(f"{'='*60}")

            for top_category, subcategories in CATEGORY_STRUCTURE.items():
                logger.info(f"\n=== 最上位カテゴリ: {top_category} ===")

                for parent_category, url_path in subcategories.items():
                    logger.info(f"  親カテゴリ: {parent_category}")

                    products = await self._scrape_category_page(
                        base_url=base_url,
                        url_path=url_path,
                        top_category=top_category,
                        parent_category=parent_category,
                        location_name=location_name,
                        timestamp=timestamp
                    )

                    # 重複を除外して追加
                    for product in products:
                        key = (product.url, product.location)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_products.append(product)

                    logger.info(f"    → {len(products)}件取得（累計: {len(all_products)}件）")
                    await self._wait()

        logger.info(f"\n商品ページ取得完了: {len(all_products)}件")
        return all_products

    async def _scrape_category_page(
        self,
        base_url: str,
        url_path: str,
        top_category: str,
        parent_category: str,
        location_name: str,
        timestamp: str
    ) -> list[BullionstarProduct]:
        """
        カテゴリーページから全商品を取得（スクロールで全件読み込み）
        """
        products = []
        url = f"{base_url}{url_path}"

        try:
            logger.info(f"    ページを取得中: {url}")

            await self._page.goto(url, wait_until="networkidle", timeout=60000)
            await self._page.wait_for_timeout(self.PAGE_LOAD_WAIT)

            # ページ内の商品総数を取得
            total_count = await self._page.evaluate("() => window.productTotal || 0")
            logger.info(f"    商品総数: {total_count}件")

            # 仮想スクロール対応: スクロールしながらリアルタイムで商品URLを収集
            seen_urls = set()
            no_new_count = 0
            max_no_new = 10  # 新規商品が見つからない回数の上限
            max_scroll_attempts = 200  # 最大スクロール回数
            scroll_step = 800  # スクロールのステップ（ピクセル）

            for scroll_num in range(max_scroll_attempts):
                # 現在表示されている商品リンクを取得して収集
                current_links = await self._page.evaluate('''() => {
                    const links = document.querySelectorAll('a[href*="/buy/product/"]');
                    return Array.from(links).map(a => a.href);
                }''')

                new_found = 0
                for href in current_links:
                    if href and href not in seen_urls:
                        seen_urls.add(href)
                        new_found += 1

                # 目標数に達したら終了
                if total_count > 0 and len(seen_urls) >= total_count:
                    logger.info(f"    全商品収集完了: {len(seen_urls)}/{total_count}")
                    break

                if new_found == 0:
                    no_new_count += 1
                    if no_new_count >= max_no_new:
                        logger.info(f"    収集終了: {len(seen_urls)}件 (新規なし{max_no_new}回)")
                        break
                else:
                    no_new_count = 0

                # 段階的にスクロール
                await self._page.evaluate(f"window.scrollBy(0, {scroll_step})")
                await self._page.wait_for_timeout(300)  # 短い待機

                # 10回ごとにネットワーク待機
                if scroll_num % 10 == 9:
                    try:
                        await self._page.wait_for_load_state("networkidle", timeout=3000)
                    except:
                        pass

            logger.info(f"    収集したURL: {len(seen_urls)}件")

            # 収集したURLから商品データを作成
            for product_url in seen_urls:
                try:
                    # URLから商品名を抽出 (例: /buy/product/gold-bar-1oz -> Gold Bar 1oz)
                    url_parts = product_url.split('/buy/product/')
                    if len(url_parts) > 1:
                        name = url_parts[1].split('?')[0].replace('-', ' ').title()
                    else:
                        name = "Unknown Product"

                    products.append(BullionstarProduct(
                        name=name[:200],
                        url=product_url,
                        top_category=top_category,
                        parent_category=parent_category,
                        child_category="",
                        location=location_name,
                        fetched_at=timestamp
                    ))

                except Exception as e:
                    logger.debug(f"商品データ作成エラー: {e}")
                    continue

            logger.info(f"    取得した商品: {len(products)}件")

        except Exception as e:
            logger.error(f"    カテゴリ取得エラー: {url} - {e}")

        return products


def save_products_to_spreadsheet(products: list[BullionstarProduct]) -> bool:
    """
    商品をスプレッドシートに保存（追加モード）
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    sheet_name = Config.SHEET_BULLIONSTAR_PRODUCTS

    try:
        try:
            sheet = client._spreadsheet.worksheet(sheet_name)
            logger.info(f"既存シート '{sheet_name}' を使用")
        except Exception:
            sheet = client._spreadsheet.add_worksheet(
                title=sheet_name,
                rows=10000,
                cols=10
            )
            headers = Config.BULLIONSTAR_PRODUCT_HEADERS
            sheet.update('A1:G1', [headers])
            logger.info(f"シート '{sheet_name}' を作成しました")

        existing_data = sheet.get_all_values()
        if not existing_data:
            headers = Config.BULLIONSTAR_PRODUCT_HEADERS
            sheet.update('A1:G1', [headers])
            logger.info("ヘッダー行を追加")

        new_rows = []
        for product in products:
            new_rows.append([
                product.name,
                product.url,
                product.top_category,
                product.parent_category,
                product.child_category,
                product.location,
                product.fetched_at,
            ])

        if new_rows:
            sheet.append_rows(new_rows, value_input_option='RAW')
            logger.info(f"スプレッドシートに {len(new_rows)} 件追加しました")
        else:
            logger.info("追加する商品はありませんでした")

        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        return False


async def fetch_bullionstar_products() -> list[BullionstarProduct]:
    """Bullionstarから商品ページ一覧を取得"""
    scraper = BullionstarProductScraper()
    try:
        await scraper.start()
        return await scraper.get_all_products()
    finally:
        await scraper.stop()


async def main_async():
    """非同期メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bullionstar商品ページ一覧を取得してスプレッドシートに保存"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")
    parser.add_argument("--dry-run", action="store_true", help="ドライラン")
    parser.add_argument("--category", type=str, help="特定カテゴリのみ")
    parser.add_argument("--location", type=str, help="特定ロケーションのみ (Singapore, USA, New Zealand)")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logger.info("=" * 60)
    logger.info("Bullionstar 商品ページ一覧取得開始（在庫切れ含む）")
    logger.info("=" * 60)

    start_time = datetime.now()

    errors = Config.validate()
    if errors and not args.dry_run:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # フィルタリング
    global CATEGORY_STRUCTURE, LOCATIONS
    if args.category:
        if args.category in CATEGORY_STRUCTURE:
            CATEGORY_STRUCTURE = {args.category: CATEGORY_STRUCTURE[args.category]}
            logger.info(f"カテゴリを {args.category} に限定")
        else:
            logger.error(f"無効なカテゴリ: {args.category}")
            sys.exit(1)

    if args.location:
        for loc_id, (loc_name, _) in list(LOCATIONS.items()):
            if loc_name == args.location:
                LOCATIONS = {loc_id: LOCATIONS[loc_id]}
                logger.info(f"ロケーションを {args.location} に限定")
                break
        else:
            logger.error(f"無効なロケーション: {args.location}")
            sys.exit(1)

    products = await fetch_bullionstar_products()

    if not products:
        logger.warning("商品を取得できませんでした")
        return 1

    logger.info(f"\n取得した商品数: {len(products)}件")

    logger.info("\n取得した商品（最初の10件）:")
    for i, product in enumerate(products[:10], 1):
        logger.info(f"  {i}. {product.name[:50]}...")
        logger.info(f"     URL: {product.url}")
        logger.info(f"     {product.top_category} > {product.parent_category} [{product.location}]")

    if len(products) > 10:
        logger.info(f"  ... 他 {len(products) - 10} 件")

    if args.dry_run:
        logger.info("\n[ドライラン] スプレッドシートへの保存をスキップ")
    else:
        logger.info("\n" + "=" * 60)
        logger.info("スプレッドシートに保存")
        logger.info("=" * 60)

        if save_products_to_spreadsheet(products):
            logger.info("スプレッドシートへの保存完了")
        else:
            logger.error("スプレッドシートへの保存失敗")
            return 1

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("\n" + "=" * 60)
    logger.info(f"処理完了")
    logger.info(f"  取得件数: {len(products)}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒（{elapsed/60:.1f}分）")
    logger.info("=" * 60)

    return 0


def main():
    """メイン処理"""
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
