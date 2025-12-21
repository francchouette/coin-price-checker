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

API方式で在庫切れ商品も含めて全商品を取得。
"""

import logging
import sys
import time
import random
import requests
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

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


def save_products_to_spreadsheet(products: list[BullionstarProduct]) -> bool:
    """
    商品をスプレッドシートに保存（差分追加モード）

    既存のURLと比較して、新規商品のみを追加する。
    URLをユニークキーとして重複を防止。
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
            existing_urls = set()
        else:
            # B列（URL）を既存URLセットとして取得（ヘッダー行を除く）
            existing_urls = set(row[1] for row in existing_data[1:] if len(row) > 1 and row[1])
            logger.info(f"既存商品数: {len(existing_urls)}件")

        # 差分のみ抽出（URLが既存にないもの）
        new_rows = []
        skipped_count = 0
        for product in products:
            if product.url in existing_urls:
                skipped_count += 1
                continue
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
            logger.info(f"スプレッドシートに {len(new_rows)} 件追加しました（スキップ: {skipped_count}件）")
        else:
            logger.info(f"追加する新規商品はありませんでした（全{skipped_count}件が既存）")

        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        return False


def fetch_bullionstar_products() -> list[BullionstarProduct]:
    """Bullionstarから商品ページ一覧を取得"""
    fetcher = BullionstarProductFetcher()
    return fetcher.get_all_products()


def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Bullionstar商品ページ一覧を取得してスプレッドシートに保存"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")
    parser.add_argument("--dry-run", action="store_true", help="ドライラン")
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
    logger.info("Bullionstar 商品ページ一覧取得開始（API版・在庫切れ含む）")
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


if __name__ == "__main__":
    sys.exit(main())
