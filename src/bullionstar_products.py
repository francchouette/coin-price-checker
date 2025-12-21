"""
Bullionstar 商品ページ一覧取得スクリプト

Bullionstarの全商品ページURLとカテゴリー情報を取得し、
スプレッドシートの「ブリオンスター商品ページ一覧」シートに保存する。

取得情報（23列: A-W）:
- 仕入れ先商品ID (BS-XXXXXX)
- 仕入れ先商品名
- 仕入れ先商品URL
- 仕入れ先サイト (固定: Bullionstar)
- 最上位カテゴリ / 親カテゴリ / 子カテゴリ
- ロケーション
- 初回取得日
- 商品グループID（将来用）
- 現在価格（現地通貨）/ 取引通貨 / 在庫状況
- 為替種類 / 為替レート / 日本円換算価格
- 最終価格更新日時 / 前回価格 / 価格変動率
- 採用フラグ / 採用理由（将来用）
- カラーミー商品ID / 備考

次回実行時は上書きせず、差分のみ追加。既存商品は価格情報を更新。

API方式で在庫切れ商品も含めて全商品を取得。
--fetch-pricesオプションで価格・在庫もスクレイピング。
"""

import logging
import sys
import time
import random
import requests
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


@dataclass
class BullionstarProduct:
    """Bullionstar商品データ（23列対応）"""
    # 必須フィールド
    name: str                          # B列: 仕入れ先商品名
    url: str                           # C列: 仕入れ先商品URL（ユニークキー）
    top_category: str                  # E列: 最上位カテゴリ
    parent_category: str               # F列: 親カテゴリ
    child_category: str                # G列: 子カテゴリ
    location: str                      # H列: ロケーション
    fetched_at: str                    # I列: 初回取得日

    # オプションフィールド（価格情報）
    supplier_id: str = ""              # A列: 仕入れ先商品ID (BS-XXXXXX)
    site: str = "Bullionstar"          # D列: 仕入れ先サイト
    group_id: str = ""                 # J列: 商品グループID（将来用）
    price: Optional[float] = None      # K列: 現在価格（現地通貨）
    currency: str = ""                 # L列: 取引通貨
    in_stock: Optional[bool] = None    # M列: 在庫状況
    exchange_type: str = "クレカ"       # N列: 為替種類
    exchange_rate: float = 0.0         # O列: 為替レート
    price_jpy: float = 0.0             # P列: 日本円換算価格
    last_price_updated: str = ""       # Q列: 最終価格更新日時
    prev_price: Optional[float] = None # R列: 前回価格（現地通貨）
    price_change_rate: str = ""        # S列: 価格変動率
    adopted_flag: str = ""             # T列: 採用フラグ（将来用）
    adopted_reason: str = ""           # U列: 採用理由（将来用）
    colorme_id: str = ""               # V列: カラーミー商品ID
    memo: str = ""                     # W列: 備考


# ロケーション定義
LOCATIONS = {
    1: ("Singapore", "https://www.bullionstar.com"),
    2: ("USA", "https://www.bullionstar.us"),
    3: ("New Zealand", "https://www.bullionstar.co.nz"),
}


class BullionstarProductFetcher:
    """Bullionstar商品取得クラス（API版）"""

    # API設定
    API_PATH = "/product/filter/desktop"
    PAGE_SIZE = 500  # 1回のリクエストで取得する件数
    MIN_WAIT = 0.5
    MAX_WAIT = 1.5

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })

    def _wait(self):
        """ランダムな待機"""
        wait_time = random.uniform(self.MIN_WAIT, self.MAX_WAIT)
        time.sleep(wait_time)

    def get_all_products(self) -> list[BullionstarProduct]:
        """
        全ロケーションの商品ページ一覧をAPIから取得

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

            products = self._fetch_location_products(
                location_id=location_id,
                location_name=location_name,
                base_url=base_url,
                timestamp=timestamp
            )

            # 重複を除外して追加
            for product in products:
                key = (product.url, product.location)
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_products.append(product)

            logger.info(f"  → {location_name}: {len(products)}件（累計: {len(all_products)}件）")

        logger.info(f"\n商品ページ取得完了: {len(all_products)}件")
        return all_products

    def _fetch_location_products(
        self,
        location_id: int,
        location_name: str,
        base_url: str,
        timestamp: str
    ) -> list[BullionstarProduct]:
        """
        特定ロケーションの全商品をAPIから取得（ページネーション対応）
        """
        products = []
        page = 1
        total_count = None

        while True:
            api_url = f"{base_url}{self.API_PATH}"
            params = {
                "locationId": location_id,
                "page": page,
            }

            try:
                response = self._session.get(api_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                result = data.get("result", {})
                pagination = data.get("pagination", {})

                # 初回のみ総数を取得
                if total_count is None:
                    total_count = pagination.get("totalCount", 0)
                    logger.info(f"  商品総数: {total_count}件")

                # 商品グループを処理
                product_groups = result.get("groups", [])
                if not product_groups:
                    break

                page_count = 0
                for group in product_groups:
                    group_name = group.get("title", "")
                    group_products = group.get("products", [])

                    for prod in group_products:
                        prod_url = prod.get("url", "")
                        prod_name = prod.get("title", "")

                        if not prod_url:
                            continue

                        # URLがすでに完全なURLの場合はそのまま使用
                        if prod_url.startswith("http"):
                            full_url = prod_url
                        elif prod_url.startswith("/"):
                            full_url = f"{base_url}{prod_url}"
                        else:
                            full_url = f"{base_url}/buy/product/{prod_url}"

                        # カテゴリーを判定
                        top_category, parent_category = self._determine_category(
                            prod_name, group_name, prod_url
                        )

                        products.append(BullionstarProduct(
                            name=prod_name[:200] if prod_name else "Unknown",
                            url=full_url,
                            top_category=top_category,
                            parent_category=parent_category,
                            child_category=group_name[:100] if group_name else "",
                            location=location_name,
                            fetched_at=timestamp
                        ))
                        page_count += 1

                logger.info(f"  ページ {page}: {page_count}件取得（累計: {len(products)}件）")

                # 次のページがあるか確認
                next_page = pagination.get("nextPage")
                if not next_page or len(products) >= total_count:
                    break

                page += 1
                self._wait()

            except requests.RequestException as e:
                logger.error(f"  API取得エラー: {e}")
                break
            except Exception as e:
                logger.error(f"  処理エラー: {e}")
                break

        return products

    def _determine_category(self, name: str, group_name: str, url: str) -> tuple[str, str]:
        """商品名やURLからカテゴリーを判定"""
        name_lower = (name + " " + group_name + " " + url).lower()

        # 最上位カテゴリの判定
        if "gold" in name_lower:
            top_category = "Gold"
        elif "silver" in name_lower:
            top_category = "Silver"
        elif "platinum" in name_lower:
            top_category = "Platinum"
        elif "palladium" in name_lower:
            top_category = "Palladium"
        elif "copper" in name_lower:
            top_category = "Copper"
        elif "jewel" in name_lower:
            top_category = "Jewellery"
        else:
            top_category = "Other"

        # 親カテゴリの判定
        if "bar" in name_lower:
            parent_category = f"{top_category} Bars"
        elif "coin" in name_lower or "round" in name_lower:
            parent_category = f"{top_category} Coins"
        elif "jewel" in name_lower or "necklace" in name_lower or "bracelet" in name_lower:
            parent_category = "Jewellery"
        elif "bsp" in name_lower or "savings program" in name_lower:
            parent_category = "Bullion Savings Program"
        else:
            parent_category = top_category

        return top_category, parent_category


def generate_supplier_id(existing_ids: set[str]) -> str:
    """
    新しい仕入れ先商品IDを生成（BS-XXXXXX形式）
    """
    # 既存IDから最大番号を取得
    max_num = 0
    for sid in existing_ids:
        if sid.startswith("BS-"):
            try:
                num = int(sid[3:])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"BS-{max_num + 1:06d}"


def save_products_to_spreadsheet(products: list[BullionstarProduct]) -> bool:
    """
    商品をスプレッドシートに保存（差分追加・価格更新モード）

    - 新規商品: 23列のデータを追加（A列にBS-XXXXXXを自動採番）
    - 既存商品: 価格関連列（K-S列）を更新
    - C列（URL）をユニークキーとして使用
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    sheet_name = Config.SHEET_BULLIONSTAR_PRODUCTS
    headers = Config.BULLIONSTAR_PRODUCT_HEADERS

    try:
        # シート取得または作成
        try:
            sheet = client._spreadsheet.worksheet(sheet_name)
            logger.info(f"既存シート '{sheet_name}' を使用")
        except Exception:
            sheet = client._spreadsheet.add_worksheet(
                title=sheet_name,
                rows=10000,
                cols=30
            )
            sheet.update('A1:W1', [headers])
            logger.info(f"シート '{sheet_name}' を作成しました")

        # 既存データを取得
        existing_data = sheet.get_all_values()
        if not existing_data:
            sheet.update('A1:W1', [headers])
            logger.info("ヘッダー行を追加")
            existing_data = [headers]

        # 既存データをURLでインデックス化（C列=index 2がURL）
        existing_by_url: dict[str, tuple[int, list[str]]] = {}  # URL -> (行番号, 行データ)
        existing_ids: set[str] = set()  # 既存の仕入れ先商品ID
        for row_idx, row in enumerate(existing_data[1:], start=2):  # ヘッダー行をスキップ、行番号は2から
            if len(row) > 2 and row[2]:  # C列: URL
                existing_by_url[row[2]] = (row_idx, row)
            if len(row) > 0 and row[0]:  # A列: 仕入れ先商品ID
                existing_ids.add(row[0])

        logger.info(f"既存商品数: {len(existing_by_url)}件")

        # 新規追加行と更新行を分類
        new_rows = []
        update_cells = []  # (row, col, value) のリスト
        skipped_count = 0
        updated_count = 0

        for product in products:
            if product.url in existing_by_url:
                # 既存商品: 価格情報を更新（K-S列）
                row_idx, existing_row = existing_by_url[product.url]

                # 価格情報がある場合のみ更新
                if product.price is not None:
                    # R列: 前回価格（現在のK列の値を保存）
                    if len(existing_row) > 10 and existing_row[10]:
                        try:
                            prev_price = float(existing_row[10])
                            update_cells.append((row_idx, 18, str(prev_price)))  # R列
                        except ValueError:
                            pass

                    # K列: 現在価格
                    update_cells.append((row_idx, 11, str(product.price)))

                    # L列: 取引通貨
                    if product.currency:
                        update_cells.append((row_idx, 12, product.currency))

                    # M列: 在庫状況
                    if product.in_stock is not None:
                        stock_status = "In Stock" if product.in_stock else "Out of Stock"
                        update_cells.append((row_idx, 13, stock_status))

                    # Q列: 最終価格更新日時
                    update_cells.append((row_idx, 17, product.last_price_updated or product.fetched_at))

                    # S列: 価格変動率（計算）
                    if len(existing_row) > 10 and existing_row[10]:
                        try:
                            prev_price = float(existing_row[10])
                            if prev_price > 0:
                                change_rate = ((product.price - prev_price) / prev_price) * 100
                                update_cells.append((row_idx, 19, f"{change_rate:+.2f}%"))
                        except ValueError:
                            pass

                    updated_count += 1
                else:
                    skipped_count += 1
            else:
                # 新規商品: 23列のデータを作成
                supplier_id = generate_supplier_id(existing_ids)
                existing_ids.add(supplier_id)

                # 在庫状況の文字列変換
                stock_status = ""
                if product.in_stock is not None:
                    stock_status = "In Stock" if product.in_stock else "Out of Stock"

                new_row = [
                    supplier_id,                                    # A: 仕入れ先商品ID
                    product.name,                                   # B: 仕入れ先商品名
                    product.url,                                    # C: 仕入れ先商品URL
                    product.site,                                   # D: 仕入れ先サイト
                    product.top_category,                           # E: 最上位カテゴリ
                    product.parent_category,                        # F: 親カテゴリ
                    product.child_category,                         # G: 子カテゴリ
                    product.location,                               # H: ロケーション
                    product.fetched_at,                             # I: 初回取得日
                    product.group_id,                               # J: 商品グループID
                    str(product.price) if product.price else "",    # K: 現在価格
                    product.currency,                               # L: 取引通貨
                    stock_status,                                   # M: 在庫状況
                    product.exchange_type,                          # N: 為替種類
                    str(product.exchange_rate) if product.exchange_rate else "",  # O: 為替レート
                    str(product.price_jpy) if product.price_jpy else "",  # P: 日本円換算価格
                    product.last_price_updated,                     # Q: 最終価格更新日時
                    str(product.prev_price) if product.prev_price else "",  # R: 前回価格
                    product.price_change_rate,                      # S: 価格変動率
                    product.adopted_flag,                           # T: 採用フラグ
                    product.adopted_reason,                         # U: 採用理由
                    product.colorme_id,                             # V: カラーミー商品ID
                    product.memo,                                   # W: 備考
                ]
                new_rows.append(new_row)

        # 新規行を追加
        if new_rows:
            sheet.append_rows(new_rows, value_input_option='RAW')
            logger.info(f"新規追加: {len(new_rows)}件")

        # 既存行を更新
        if update_cells:
            # バッチ更新のためにセルをグループ化
            for row_idx, col_idx, value in update_cells:
                sheet.update_cell(row_idx, col_idx, value)
            logger.info(f"価格更新: {updated_count}件")

        if skipped_count > 0:
            logger.info(f"スキップ（価格情報なし）: {skipped_count}件")

        if not new_rows and not update_cells:
            logger.info("追加・更新する商品はありませんでした")

        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


def fetch_bullionstar_products() -> list[BullionstarProduct]:
    """Bullionstarから商品ページ一覧を取得"""
    fetcher = BullionstarProductFetcher()
    return fetcher.get_all_products()


def fetch_prices_for_products(
    products: list[BullionstarProduct],
    limit: Optional[int] = None
) -> list[BullionstarProduct]:
    """
    商品リストの価格・在庫情報をスクレイピングで取得

    Args:
        products: 商品リスト
        limit: 取得件数制限（Noneで全件）

    Returns:
        価格情報が付加された商品リスト
    """
    try:
        from playwright.sync_api import sync_playwright
        from src.shops.bullionstar import BullionstarScraper
    except ImportError:
        logger.error("Playwrightがインストールされていません。pip install playwright && playwright install chromium")
        return products

    target_products = products[:limit] if limit else products
    logger.info(f"価格取得開始: {len(target_products)}件")

    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    success_count = 0
    error_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        scraper = BullionstarScraper(page)

        for i, product in enumerate(target_products):
            try:
                logger.info(f"[{i+1}/{len(target_products)}] {product.name[:40]}...")

                result = scraper.scrape(product.url)

                if result.error:
                    logger.warning(f"  エラー: {result.error}")
                    error_count += 1
                    continue

                # 価格情報を更新
                product.price = result.price
                product.currency = result.currency
                product.in_stock = result.in_stock
                product.last_price_updated = timestamp

                # ロケーション（製造国/発行国）を取得
                scraped_location = scraper.get_location()
                if scraped_location:
                    product.location = scraped_location
                    logger.info(f"  価格: {result.currency} {result.price:.2f}, 在庫: {'あり' if result.in_stock else 'なし'}, 製造国: {scraped_location}")
                else:
                    logger.info(f"  価格: {result.currency} {result.price:.2f}, 在庫: {'あり' if result.in_stock else 'なし'}")

                # 次の商品用にスクレイパーの状態をリセット
                scraper._detected_location = None

                success_count += 1

                # レート制限対策
                time.sleep(random.uniform(1.0, 2.0))

            except Exception as e:
                logger.error(f"  スクレイピングエラー: {e}")
                error_count += 1
                continue

        browser.close()

    logger.info(f"価格取得完了: 成功={success_count}件, 失敗={error_count}件")
    return products


def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bullionstar商品ページ一覧を取得してスプレッドシートに保存"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")
    parser.add_argument("--dry-run", action="store_true", help="ドライラン")
    parser.add_argument("--location", type=str, help="特定ロケーションのみ (Singapore, USA, New Zealand)")
    parser.add_argument("--fetch-prices", action="store_true", help="価格・在庫もスクレイピングで取得")
    parser.add_argument("--limit", type=int, help="価格取得の件数制限（テスト用）")
    parser.add_argument("--category", type=str, help="特定カテゴリのみ (Gold, Silver, Platinum等)")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logger.info("=" * 60)
    logger.info("Bullionstar 商品ページ一覧取得開始（API版・在庫切れ含む）")
    if args.fetch_prices:
        logger.info("※価格・在庫取得モード有効")
    logger.info("=" * 60)

    start_time = datetime.now()

    errors = Config.validate()
    if errors and not args.dry_run:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # ロケーションフィルタリング
    global LOCATIONS
    if args.location:
        for loc_id, (loc_name, _) in list(LOCATIONS.items()):
            if loc_name == args.location:
                LOCATIONS = {loc_id: LOCATIONS[loc_id]}
                logger.info(f"ロケーションを {args.location} に限定")
                break
        else:
            logger.error(f"無効なロケーション: {args.location}")
            sys.exit(1)

    products = fetch_bullionstar_products()

    if not products:
        logger.warning("商品を取得できませんでした")
        return 1

    # カテゴリフィルタリング
    if args.category:
        products = [p for p in products if p.top_category == args.category]
        logger.info(f"カテゴリ {args.category} にフィルタリング: {len(products)}件")

    logger.info(f"\n取得した商品数: {len(products)}件")

    logger.info("\n取得した商品（最初の10件）:")
    for i, product in enumerate(products[:10], 1):
        logger.info(f"  {i}. {product.name[:50]}...")
        logger.info(f"     URL: {product.url}")
        logger.info(f"     {product.top_category} > {product.parent_category} [{product.location}]")

    if len(products) > 10:
        logger.info(f"  ... 他 {len(products) - 10} 件")

    # 価格取得
    if args.fetch_prices:
        logger.info("\n" + "=" * 60)
        logger.info("価格・在庫情報を取得")
        logger.info("=" * 60)
        products = fetch_prices_for_products(products, limit=args.limit)

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


if __name__ == "__main__":
    sys.exit(main())
