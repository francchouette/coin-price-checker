"""
APMEXカテゴリー取得 & カラーミーグループ登録スクリプト

Bright Data Browser API経由でAPMEXのカテゴリー一覧を取得し、
スプレッドシートに保存。ユーザーが確認後、カラーミーのグループとして登録する。

フロー:
1. fetch: APMEXからカテゴリー取得 → スプレッドシート保存
2. ユーザーがスプレッドシートで確認・編集（登録列をTRUEに）
3. register: スプレッドシートから読み込み → カラーミーグループ登録
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, Page

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.colorme import ColorMeClient
from src.spreadsheet import SpreadsheetClient

logger = logging.getLogger(__name__)


@dataclass
class ApmexCategory:
    """APMEXカテゴリー"""
    name: str
    url: str
    parent_name: Optional[str] = None
    children: list["ApmexCategory"] = None
    # スプレッドシート用
    register: bool = False
    group_id: int = 0
    status: str = ""

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
            # APMEXのメインページにアクセス（リトライ付き）
            logger.info(f"APMEXにアクセス中: {self.APMEX_URL}")

            for attempt in range(3):
                try:
                    # domcontentloadedで待機（networkidleより早い）
                    await self._page.goto(
                        self.APMEX_URL,
                        wait_until="domcontentloaded",
                        timeout=90000
                    )
                    # ページ読み込み後に追加で待機
                    await asyncio.sleep(10)
                    break
                except Exception as e:
                    logger.warning(f"ページアクセス試行 {attempt + 1}/3 失敗: {e}")
                    if attempt < 2:
                        await asyncio.sleep(5)
                    else:
                        raise

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
        ナビゲーションメニューからカテゴリーを解析（親子関係を保持）

        Args:
            soup: BeautifulSoupオブジェクト

        Returns:
            list[ApmexCategory]: カテゴリーリスト（親子関係付き）
        """
        categories = []

        # APMEXのメガメニュー構造を探索
        # 親カテゴリー → 子カテゴリーの構造を解析

        # メガメニューのセクションを探す
        mega_menu_selectors = [
            '.mega-menu',
            '[class*="mega-menu"]',
            '.nav-dropdown',
            '[class*="dropdown-menu"]',
            '.submenu',
        ]

        for menu_selector in mega_menu_selectors:
            menu_sections = soup.select(menu_selector)
            if menu_sections:
                logger.info(f"メニューセクション '{menu_selector}' で {len(menu_sections)} 件検出")

                for section in menu_sections:
                    # セクション内のヘッダー（親カテゴリー）を探す
                    header_selectors = ['h2', 'h3', 'h4', '.menu-header', '.category-header', 'strong']
                    parent_name = None

                    for header_sel in header_selectors:
                        header = section.select_one(header_sel)
                        if header:
                            parent_text = header.get_text(strip=True)
                            if parent_text and len(parent_text) < 50:  # 長すぎるテキストを除外
                                parent_name = parent_text
                                break

                    # セクション内のリンクを取得
                    links = section.select('a[href*="/category/"]')
                    for link in links:
                        href = link.get('href', '')
                        text = link.get_text(strip=True)

                        if href and text and '/category/' in href:
                            # 親カテゴリー自体のリンクかどうかを判定
                            is_parent = parent_name and text == parent_name

                            categories.append(ApmexCategory(
                                name=text,
                                url=href if href.startswith('http') else f"{self.APMEX_URL}{href}",
                                parent_name=None if is_parent else parent_name
                            ))

                if categories:
                    break

        # メガメニューで見つからない場合、フラットなナビゲーションを試行
        if not categories:
            nav_selectors = [
                'nav[class*="main"] ul li a',
                '.navigation ul li a',
                'header nav ul li a',
            ]

            for selector in nav_selectors:
                nav_links = soup.select(selector)
                if nav_links:
                    logger.info(f"セレクタ '{selector}' で {len(nav_links)} 件のリンクを検出")
                    for link in nav_links:
                        href = link.get('href', '')
                        text = link.get_text(strip=True)

                        if href and text and '/category/' in href:
                            categories.append(ApmexCategory(
                                name=text,
                                url=href if href.startswith('http') else f"{self.APMEX_URL}{href}"
                            ))

                    if categories:
                        break

        # 重複を除去（名前とURLの組み合わせで）
        seen = set()
        unique_categories = []
        for cat in categories:
            key = (cat.name, cat.url)
            if key not in seen:
                seen.add(key)
                unique_categories.append(cat)

        # 親カテゴリーの情報をログ出力
        parents = set(cat.parent_name for cat in unique_categories if cat.parent_name)
        if parents:
            logger.info(f"検出した親カテゴリー: {', '.join(parents)}")

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

            for attempt in range(3):
                try:
                    await self._page.goto(
                        browse_url,
                        wait_until="domcontentloaded",
                        timeout=90000
                    )
                    await asyncio.sleep(10)
                    break
                except Exception as e:
                    logger.warning(f"ブラウズページ試行 {attempt + 1}/3 失敗: {e}")
                    if attempt < 2:
                        await asyncio.sleep(5)
                    else:
                        raise

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


def save_categories_to_spreadsheet(categories: list[ApmexCategory]) -> bool:
    """
    カテゴリーをスプレッドシートに保存（親子関係を保持）

    Args:
        categories: APMEXカテゴリーリスト

    Returns:
        bool: 成功時True
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    try:
        # シートを取得または作成
        try:
            sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_CATEGORIES)
            logger.info(f"既存シート '{Config.SHEET_APMEX_CATEGORIES}' を使用")
        except Exception:
            # シートがなければ作成
            sheet = client._spreadsheet.add_worksheet(
                title=Config.SHEET_APMEX_CATEGORIES,
                rows=200,
                cols=10
            )
            # ヘッダーを追加
            sheet.update('A1:H1', [Config.APMEX_CATEGORY_HEADERS])
            logger.info(f"シート '{Config.SHEET_APMEX_CATEGORIES}' を作成しました")

        # 既存データを取得（カテゴリー名+親カテゴリー名でチェック）
        existing_data = sheet.get_all_values()
        existing_keys = set()
        for row in existing_data[1:]:  # ヘッダーをスキップ
            if len(row) >= 2 and row[0].strip():
                # 名前と親カテゴリーの組み合わせで一意性を判断
                key = (row[0].strip(), row[1].strip() if len(row) > 1 else "")
                existing_keys.add(key)

        # カテゴリーを親子順にソート（親カテゴリーを先に登録するため）
        # 親カテゴリーがないもの（トップレベル）を先に
        sorted_categories = sorted(categories, key=lambda c: (c.parent_name or "", c.name))

        # 新規カテゴリーのみ追加
        new_rows = []
        for cat in sorted_categories:
            key = (cat.name, cat.parent_name or "")
            if key not in existing_keys:
                new_rows.append([
                    cat.name,               # A: カテゴリー名
                    cat.parent_name or "",  # B: 親カテゴリー
                    cat.url,                # C: APMEX URL
                    "FALSE",                # D: 登録（デフォルトFALSE）
                    "",                     # E: カラーミーグループID
                    "",                     # F: 親グループID
                    "",                     # G: 登録日時
                    "",                     # H: ステータス
                ])
                parent_info = f" (親: {cat.parent_name})" if cat.parent_name else " (トップレベル)"
                logger.info(f"新規追加: {cat.name}{parent_info}")
            else:
                logger.info(f"既存のためスキップ: {cat.name}")

        if new_rows:
            sheet.append_rows(new_rows, value_input_option='USER_ENTERED')
            logger.info(f"スプレッドシートに {len(new_rows)} 件追加しました")
        else:
            logger.info("追加するカテゴリーはありませんでした")

        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        return False


def get_categories_to_register() -> tuple[list[ApmexCategory], dict[str, int]]:
    """
    スプレッドシートから登録対象カテゴリーを取得

    D列（登録）がTRUEで、E列（グループID）が空のものを取得
    親カテゴリーのグループIDも取得

    Returns:
        tuple[list[ApmexCategory], dict[str, int]]:
            - 登録対象カテゴリーリスト
            - 既存カテゴリー名 → グループIDのマッピング
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return [], {}

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_CATEGORIES)
        all_data = sheet.get_all_values()

        categories = []
        existing_group_ids = {}  # カテゴリー名 → グループID

        # 1パス目: 既存のグループIDを収集
        for row in all_data[1:]:  # ヘッダーをスキップ
            if len(row) >= 5:
                name = row[0].strip()
                group_id_str = row[4].strip() if len(row) > 4 else ""
                if name and group_id_str:
                    try:
                        existing_group_ids[name] = int(group_id_str)
                    except ValueError:
                        pass

        # 2パス目: 登録対象を収集
        for i, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
            if len(row) >= 5:
                name = row[0].strip()
                parent_name = row[1].strip() if len(row) > 1 else ""
                url = row[2].strip() if len(row) > 2 else ""
                register = row[3].strip().upper() == "TRUE" if len(row) > 3 else False
                group_id = row[4].strip() if len(row) > 4 else ""

                # 登録フラグがTRUEで、グループIDが空のもの
                if name and register and not group_id:
                    categories.append(ApmexCategory(
                        name=name,
                        url=url,
                        parent_name=parent_name if parent_name else None,
                        register=True
                    ))

        # 親カテゴリーを先に登録するようにソート
        # 親がないもの → 親があるもの の順
        sorted_categories = sorted(categories, key=lambda c: (1 if c.parent_name else 0, c.name))

        logger.info(f"登録対象カテゴリー: {len(sorted_categories)} 件")
        if existing_group_ids:
            logger.info(f"既存グループID: {len(existing_group_ids)} 件")

        return sorted_categories, existing_group_ids

    except Exception as e:
        logger.error(f"スプレッドシート読み込みエラー: {e}")
        return [], {}


def register_categories_to_colorme(
    categories: list[ApmexCategory],
    existing_group_ids: dict[str, int],
    root_parent_group_id: int = 0,
    dry_run: bool = False
) -> dict[str, tuple[int, int]]:
    """
    カテゴリーをカラーミーグループとして登録（親子関係を維持）

    Args:
        categories: APMEXカテゴリーリスト
        existing_group_ids: 既存カテゴリー名 → グループIDのマッピング
        root_parent_group_id: ルート親グループID（APMEX用の親グループ）
        dry_run: Trueの場合は登録せずにプレビューのみ

    Returns:
        dict[str, tuple[int, int]]: カテゴリー名 → (グループID, 親グループID)のマッピング
    """
    client = ColorMeClient()
    registered = {}  # カテゴリー名 → (グループID, 親グループID)

    # 登録中に作成したグループIDも追跡
    created_group_ids = dict(existing_group_ids)

    logger.info(f"カラーミーにグループを登録中... (ルート親グループID: {root_parent_group_id})")

    for cat in categories:
        # 親グループIDを決定
        if cat.parent_name and cat.parent_name in created_group_ids:
            # 親カテゴリーが既に登録されている場合
            parent_id = created_group_ids[cat.parent_name]
            logger.info(f"  親カテゴリー '{cat.parent_name}' のグループID: {parent_id}")
        else:
            # トップレベルカテゴリー or 親がまだ登録されていない
            parent_id = root_parent_group_id

        if dry_run:
            parent_info = f" (親ID: {parent_id})" if parent_id > 0 else ""
            logger.info(f"[ドライラン] グループ作成: {cat.name}{parent_info}")
            registered[cat.name] = (0, parent_id)
        else:
            group_id, error = client.create_group(cat.name, parent_id)
            if group_id > 0:
                parent_info = f" (親ID: {parent_id})" if parent_id > 0 else ""
                logger.info(f"グループ作成成功: {cat.name} → ID: {group_id}{parent_info}")
                registered[cat.name] = (group_id, parent_id)
                # 作成したグループIDを追跡（後続の子カテゴリー用）
                created_group_ids[cat.name] = group_id
            else:
                logger.error(f"グループ作成失敗: {cat.name} - {error}")
                registered[cat.name] = (-1, parent_id)  # エラーを示す

    return registered


def update_spreadsheet_after_register(results: dict[str, tuple[int, int]]) -> bool:
    """
    カラーミー登録後にスプレッドシートを更新

    Args:
        results: カテゴリー名 → (グループID, 親グループID)のマッピング

    Returns:
        bool: 成功時True
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_CATEGORIES)
        all_data = sheet.get_all_values()

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updates = []

        for i, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
            if len(row) >= 1:
                name = row[0].strip()
                if name in results:
                    group_id, parent_group_id = results[name]
                    if group_id > 0:
                        # 成功: E列(グループID), F列(親グループID), G列(登録日時), H列(ステータス)
                        parent_id_str = str(parent_group_id) if parent_group_id > 0 else ""
                        updates.append({
                            'range': f'E{i}:H{i}',
                            'values': [[str(group_id), parent_id_str, timestamp, "成功"]]
                        })
                    elif group_id == -1:
                        # エラー: G列(登録日時), H列(ステータス)
                        updates.append({
                            'range': f'G{i}:H{i}',
                            'values': [[timestamp, "エラー"]]
                        })

        if updates:
            sheet.batch_update(updates, value_input_option='RAW')
            logger.info(f"スプレッドシートを更新しました: {len(updates)} 件")

        return True

    except Exception as e:
        logger.error(f"スプレッドシート更新エラー: {e}")
        return False


async def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(
        description="APMEXカテゴリーを取得してカラーミーグループとして登録"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["fetch", "register"],
        default="fetch",
        help="実行コマンド: fetch=カテゴリー取得→シート保存, register=シート→カラーミー登録"
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

    if args.command == "fetch":
        # カテゴリー取得 → スプレッドシート保存
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

        # スプレッドシートに保存
        logger.info("\n" + "=" * 50)
        logger.info("スプレッドシートに保存")
        logger.info("=" * 50)

        if save_categories_to_spreadsheet(categories):
            logger.info("✅ スプレッドシートへの保存完了")
            logger.info("スプレッドシートで「登録」列をTRUEにして、registerコマンドを実行してください")
        else:
            logger.error("❌ スプレッドシートへの保存失敗")
            return 1

    elif args.command == "register":
        # スプレッドシートから読み込み → カラーミー登録
        logger.info("=" * 50)
        logger.info("カラーミーグループ登録")
        logger.info("=" * 50)

        categories, existing_group_ids = get_categories_to_register()

        if not categories:
            logger.info("登録対象のカテゴリーがありません")
            logger.info("スプレッドシートで「登録」列をTRUEにしてください")
            return 0

        logger.info(f"\n登録対象カテゴリー ({len(categories)}件):")
        for i, cat in enumerate(categories, 1):
            parent_info = f" → 親: {cat.parent_name}" if cat.parent_name else " (トップレベル)"
            logger.info(f"  {i}. {cat.name}{parent_info}")

        if args.dry_run:
            logger.info("\n[ドライラン] 実際には登録しません")

        # カラーミーに登録
        results = register_categories_to_colorme(
            categories,
            existing_group_ids=existing_group_ids,
            root_parent_group_id=args.parent_group_id,
            dry_run=args.dry_run
        )

        # スプレッドシート更新
        if not args.dry_run and results:
            update_spreadsheet_after_register(results)

        success_count = sum(1 for v in results.values() if v[0] > 0)
        logger.info(f"\n登録完了: {success_count}/{len(categories)}件")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
