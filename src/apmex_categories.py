"""
APMEXカテゴリー取得 & カラーミーグループ登録スクリプト

Bright Data Browser API経由でAPMEXのカテゴリー一覧を取得し、
カラーミーのグループとして登録する。
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, Page

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.colorme import ColorMeClient

logger = logging.getLogger(__name__)


@dataclass
class ApmexCategory:
    """APMEXカテゴリー"""
    name: str
    url: str
    parent_name: Optional[str] = None
    children: list["ApmexCategory"] = None

    def __post_init__(self):
        if self.children is None:
            self.children = []


class ApmexCategoryScraper:
    """APMEXカテゴリースクレイパー（Bright Data版）"""

    APMEX_URL = "https://www.apmex.com"

    def __init__(self, ws_endpoint: str, timeout: int = 120000):
        """
        Args:
            ws_endpoint: Bright Data Browser API WebSocketエンドポイント
            timeout: タイムアウト（ミリ秒）
        """
        self.ws_endpoint = ws_endpoint
        self.timeout = timeout
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def connect(self):
        """Bright Data Browser APIに接続"""
        self._playwright = await async_playwright().start()
        logger.info("Bright Data Browser APIに接続中...")

        self._browser = await self._playwright.chromium.connect_over_cdp(
            self.ws_endpoint,
            timeout=self.timeout
        )
        logger.info("Bright Data Browser APIに接続しました")

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

        logger.info("ページ準備完了")

    async def close(self):
        """接続を閉じる"""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("接続を閉じました")

    async def get_categories(self) -> list[ApmexCategory]:
        """
        APMEXのカテゴリー一覧を取得

        Returns:
            list[ApmexCategory]: カテゴリーリスト（階層構造）
        """
        if not self._page:
            await self.connect()

        categories = []

        try:
            # APMEXのメインページにアクセス
            logger.info(f"APMEXにアクセス中: {self.APMEX_URL}")
            await self._page.goto(self.APMEX_URL, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(5)

            # ナビゲーションメニューを取得
            html = await self._page.content()
            soup = BeautifulSoup(html, 'html.parser')

            # メインナビゲーションからカテゴリーを抽出
            # APMEXのナビゲーション構造に基づいて解析
            categories = self._parse_navigation(soup)

            if not categories:
                # 代替: カテゴリーページから取得
                logger.info("ナビゲーションからカテゴリー取得できず、カテゴリーページを試行...")
                categories = await self._get_categories_from_browse_page()

            logger.info(f"取得したカテゴリー数: {len(categories)}")
            return categories

        except Exception as e:
            logger.error(f"カテゴリー取得エラー: {e}")
            return []

    def _parse_navigation(self, soup: BeautifulSoup) -> list[ApmexCategory]:
        """
        ナビゲーションメニューからカテゴリーを解析

        Args:
            soup: BeautifulSoupオブジェクト

        Returns:
            list[ApmexCategory]: カテゴリーリスト
        """
        categories = []

        # APMEXのナビゲーション構造を探索
        # 複数のセレクタを試行
        nav_selectors = [
            'nav[class*="main"] ul li a',
            '.navigation ul li a',
            '.nav-menu ul li a',
            'header nav ul li a',
            '[class*="mega-menu"] a',
            '.category-nav a',
        ]

        for selector in nav_selectors:
            nav_links = soup.select(selector)
            if nav_links:
                logger.info(f"セレクタ '{selector}' で {len(nav_links)} 件のリンクを検出")
                for link in nav_links:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)

                    # カテゴリーページへのリンクをフィルタ
                    if href and text and '/category/' in href:
                        categories.append(ApmexCategory(
                            name=text,
                            url=href if href.startswith('http') else f"{self.APMEX_URL}{href}"
                        ))

                if categories:
                    break

        # 重複を除去
        seen = set()
        unique_categories = []
        for cat in categories:
            if cat.name not in seen:
                seen.add(cat.name)
                unique_categories.append(cat)

        return unique_categories

    async def _get_categories_from_browse_page(self) -> list[ApmexCategory]:
        """
        ブラウズページからカテゴリーを取得

        Returns:
            list[ApmexCategory]: カテゴリーリスト
        """
        categories = []

        try:
            # カテゴリー一覧ページにアクセス
            browse_url = f"{self.APMEX_URL}/category/all-products"
            logger.info(f"ブラウズページにアクセス: {browse_url}")
            await self._page.goto(browse_url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(5)

            html = await self._page.content()
            soup = BeautifulSoup(html, 'html.parser')

            # カテゴリーリンクを探索
            category_selectors = [
                '.category-list a',
                '.categories a',
                '[class*="category"] a',
                '.sidebar a[href*="/category/"]',
                'a[href*="/category/"]',
            ]

            for selector in category_selectors:
                links = soup.select(selector)
                if links:
                    logger.info(f"セレクタ '{selector}' で {len(links)} 件のリンクを検出")
                    for link in links:
                        href = link.get('href', '')
                        text = link.get_text(strip=True)

                        if href and text and '/category/' in href:
                            # all-productsなど一般的なカテゴリーを除外
                            if 'all-products' not in href.lower():
                                categories.append(ApmexCategory(
                                    name=text,
                                    url=href if href.startswith('http') else f"{self.APMEX_URL}{href}"
                                ))

                    if categories:
                        break

            # 重複を除去
            seen = set()
            unique_categories = []
            for cat in categories:
                if cat.name not in seen:
                    seen.add(cat.name)
                    unique_categories.append(cat)

            return unique_categories

        except Exception as e:
            logger.error(f"ブラウズページからのカテゴリー取得エラー: {e}")
            return []


async def fetch_apmex_categories() -> list[ApmexCategory]:
    """
    APMEXからカテゴリー一覧を取得

    Returns:
        list[ApmexCategory]: カテゴリーリスト
    """
    if not Config.is_brightdata_browser_enabled():
        logger.error("Bright Data Browser APIが有効ではありません")
        logger.error("BRIGHTDATA_BROWSER_WS環境変数を設定してください")
        return []

    scraper = ApmexCategoryScraper(
        ws_endpoint=Config.BRIGHTDATA_BROWSER_WS,
        timeout=120000
    )

    try:
        await scraper.connect()
        categories = await scraper.get_categories()
        return categories
    finally:
        await scraper.close()


def register_categories_to_colorme(
    categories: list[ApmexCategory],
    parent_group_id: int = 0,
    dry_run: bool = False
) -> dict[str, int]:
    """
    カテゴリーをカラーミーグループとして登録

    Args:
        categories: APMEXカテゴリーリスト
        parent_group_id: 親グループID（APMEX用の親グループ）
        dry_run: Trueの場合は登録せずにプレビューのみ

    Returns:
        dict[str, int]: カテゴリー名 → グループIDのマッピング
    """
    client = ColorMeClient()
    registered = {}

    logger.info(f"カラーミーにグループを登録中... (親グループID: {parent_group_id})")

    for cat in categories:
        if dry_run:
            logger.info(f"[ドライラン] グループ作成: {cat.name}")
            registered[cat.name] = 0
        else:
            group_id, error = client.create_group(cat.name, parent_group_id)
            if group_id > 0:
                logger.info(f"グループ作成成功: {cat.name} → ID: {group_id}")
                registered[cat.name] = group_id
            else:
                logger.error(f"グループ作成失敗: {cat.name} - {error}")

    return registered


async def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(
        description="APMEXカテゴリーを取得してカラーミーグループとして登録"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ドライラン（実際には登録しない）"
    )
    parser.add_argument(
        "--parent-group-id",
        type=int,
        default=0,
        help="親グループID（APMEX用の親グループ）"
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="カテゴリー取得のみ（カラーミー登録しない）"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="詳細ログ出力"
    )

    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # カテゴリー取得
    logger.info("=" * 50)
    logger.info("APMEXカテゴリー取得開始")
    logger.info("=" * 50)

    categories = await fetch_apmex_categories()

    if not categories:
        logger.error("カテゴリーを取得できませんでした")
        return 1

    logger.info(f"\n取得したカテゴリー ({len(categories)}件):")
    for i, cat in enumerate(categories, 1):
        logger.info(f"  {i}. {cat.name}")
        logger.info(f"     URL: {cat.url}")

    if args.fetch_only:
        logger.info("\n--fetch-only オプションのため、カラーミー登録はスキップ")
        return 0

    # カラーミーグループ登録
    logger.info("\n" + "=" * 50)
    logger.info("カラーミーグループ登録")
    logger.info("=" * 50)

    registered = register_categories_to_colorme(
        categories,
        parent_group_id=args.parent_group_id,
        dry_run=args.dry_run
    )

    logger.info(f"\n登録完了: {len(registered)}件")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
