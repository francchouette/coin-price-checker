"""
Bullionstar 商品ページ一覧取得スクリプト

Bullionstarの全商品ページURLとカテゴリー情報を取得し、
スプレッドシートの「ブリオンスター商品ページ一覧」シートに保存する。

取得情報（83列: A-CE）:
- A-C列: 管理列（採用フラグ、カラーミー登録状況、仕入れ先商品ID）
- D-P列: 仕入れ先商品情報（13列）
  - D: カラーミー商品URL（登録後自動設定）
  - E: 仕入れ先商品URL（ユニークキー）
  - F-G: 仕入れ先商品名 / 仕入れ先サイト
  - H-K: 最上位カテゴリ / 親カテゴリ / 子カテゴリ / 製造国
  - L-O: 商品説明（英語）/ 仕様・スペック / 発行年 / 発行数・限定数
  - P: 仕入れ先在庫状況
- Q-AG列: 価格情報（17列）
  - Q-S: 仕入れ先価格（現地通貨）/ 前回仕入れ価格 / 価格変動率
  - T-W: 取引通貨 / 為替種類 / 為替レート / 仕入れ額(日本円)
  - X-AG: 枚数 / 仕入れ合計 / マージン / 送料 / 諸経費 / 合計原価 / 適正価格 / 粗利
- AH-AM列: カラーミー価格情報（6列）
- AN-AS列: カテゴリー・グループ（6列）
  - AN: 大カテゴリーID / AO: 大カテゴリー名称
  - AP: 小カテゴリーID / AQ: 小カテゴリー名称
  - AR: グループID / AS: グループ名
- AT-AZ列: 在庫管理（7列）
- BA-BD列: 送料・配送（4列）
- BE-BH列: 商品説明（4列）
- BI-BR列: 画像URL（10列）画像URL1-10
- BS-BU列: SEO項目（3列）
- BV-BZ列: フラグ・設定（5列）
- CA-CB列: 掲載期間（2列）
- CC-CE列: システム情報（3列）

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
from src.exchange_rate import ExchangeRateClient, WiseRateClient
from src.add_product import JapaneseProductNameGenerator, CategoryDetector, DescriptionGenerator, SEOGenerator, ModelNumberGenerator
from src.colorme import ColorMeClient

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


@dataclass
class BullionstarProduct:
    """Bullionstar商品データ（83列対応: A-CE）

    列構造:
    - A-C列: 管理列（採用フラグ、登録状況、仕入れ先商品ID）
    - D-CE列: 商品情報・カラーミー登録用項目
    - AN-AS列: カテゴリー・グループ（6列）ID・名称
    - AT列: 型番
    """
    # 必須フィールド
    name: str                          # F列: 仕入れ先商品名
    url: str                           # E列: 仕入れ先商品URL（ユニークキー）
    top_category: str                  # H列: 最上位カテゴリ
    parent_category: str               # I列: 親カテゴリ
    child_category: str                # J列: 子カテゴリ
    location: str                      # K列: 製造国

    # 管理列（A-C列）
    adopted_flag: str = "検討中"        # A列: 採用フラグ（デフォルト「検討中」）
    colorme_registration: str = "未登録"  # B列: カラーミー登録状況（デフォルト「未登録」）
    supplier_id: str = ""              # C列: 仕入れ先商品ID (BS-XXXXXX)

    # 仕入れ先商品情報（D-P列）
    colorme_url: str = ""              # D列: カラーミー商品URL
    site: str = "Bullionstar"          # G列: 仕入れ先サイト
    description_en: str = ""           # L列: 商品説明（英語）
    specs: str = ""                    # M列: 仕様・スペック
    mint_year: str = ""                # N列: 発行年
    mintage: str = ""                  # O列: 発行数・限定数
    in_stock: Optional[bool] = None    # P列: 仕入れ先在庫状況

    # 価格情報（Q-W列）
    price: Optional[float] = None      # Q列: 仕入れ先価格（現地通貨）
    prev_price: float = 0.0            # R列: 前回仕入れ価格
    currency: str = ""                 # T列: 取引通貨
    exchange_type: str = "クレカ"       # U列: 為替種類
    exchange_rate: float = 0.0         # V列: 為替レート
    price_jpy: float = 0.0             # W列: 仕入れ額(日本円)

    # 画像URL（BJ-BS列: 10列）
    image_url1: str = ""               # BJ列: 画像URL1
    image_url2: str = ""               # BK列: 画像URL2
    image_url3: str = ""               # BL列: 画像URL3
    image_url4: str = ""               # BM列: 画像URL4
    image_url5: str = ""               # BN列: 画像URL5
    image_url6: str = ""               # BO列: 画像URL6
    image_url7: str = ""               # BP列: 画像URL7
    image_url8: str = ""               # BQ列: 画像URL8
    image_url9: str = ""               # BR列: 画像URL9
    image_url10: str = ""              # BS列: 画像URL10

    # AT列: 型番（AI生成）
    model_number: str = ""             # AT列: 型番

    # 内部処理用フィールド（スプレッドシートには直接保存されない）
    fetched_at: str = ""               # 取得日時（内部処理用）
    last_price_updated: str = ""       # 最終価格更新日時（内部処理用）
    description_ja: str = ""           # 商品説明（日本語）
    colorme_id: str = ""               # カラーミー商品ID
    memo: str = ""                     # 備考
    price_change_rate: str = ""        # 価格変動率（文字列）
    # 互換性フィールド（旧構造）
    main_image_url: str = ""           # 互換性用: メイン画像URL
    thumbnail_url: str = ""            # 互換性用: サムネイルURL


# 販売拠点定義（APIエンドポイント用）
# 注: これは商品取得用であり、H列（製造国）には使用しない
SALES_LOCATIONS = {
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

    def get_all_products(self, limit: Optional[int] = None) -> list[BullionstarProduct]:
        """
        全ロケーションの商品ページ一覧をAPIから取得

        Args:
            limit: 取得件数制限（Noneで全件）

        Returns:
            list[BullionstarProduct]: 商品リスト
        """
        all_products = []
        seen_keys = set()
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

        for location_id, (sales_region, base_url) in SALES_LOCATIONS.items():
            # limitに達したら終了
            if limit and len(all_products) >= limit:
                break

            logger.info(f"\n{'='*60}")
            logger.info(f"販売拠点: {sales_region}")
            logger.info(f"{'='*60}")

            # 残り必要件数を計算
            remaining = limit - len(all_products) if limit else None

            products = self._fetch_location_products(
                location_id=location_id,
                base_url=base_url,
                timestamp=timestamp,
                limit=remaining
            )

            # 重複を除外して追加（URLのみでチェック）
            for product in products:
                if product.url not in seen_keys:
                    seen_keys.add(product.url)
                    all_products.append(product)
                    # limitに達したら終了
                    if limit and len(all_products) >= limit:
                        break

            logger.info(f"  → {sales_region}: {len(products)}件（累計: {len(all_products)}件）")

            # limitに達したら終了
            if limit and len(all_products) >= limit:
                break

        logger.info(f"\n商品ページ取得完了: {len(all_products)}件")
        return all_products

    def _fetch_location_products(
        self,
        location_id: int,
        base_url: str,
        timestamp: str,
        limit: Optional[int] = None
    ) -> list[BullionstarProduct]:
        """
        特定ロケーションの全商品をAPIから取得（ページネーション対応）

        Args:
            limit: 取得件数制限（Noneで全件）
        """
        products = []
        page = 1
        total_count = None

        while True:
            # limitに達したら終了
            if limit and len(products) >= limit:
                break

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
                    if limit:
                        logger.info(f"  商品総数: {total_count}件（{limit}件まで取得）")
                    else:
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
                        # limitに達したら終了
                        if limit and len(products) >= limit:
                            break

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
                            location="",  # H列: 製造国はスクレイピング時に取得
                            fetched_at=timestamp
                        ))
                        page_count += 1

                    # limitに達したら終了
                    if limit and len(products) >= limit:
                        break

                logger.info(f"  ページ {page}: {page_count}件取得（累計: {len(products)}件）")

                # limitに達したら終了
                if limit and len(products) >= limit:
                    break

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


def fetch_exchange_rates(currencies: list[str], exchange_type: str = "クレカ") -> dict[str, float]:
    """
    通貨リストから為替レートを取得する

    Args:
        currencies: 通貨コードのリスト（例: ["USD", "SGD"]）
        exchange_type: 為替種類（"クレカ" または "Wise"）

    Returns:
        dict: 通貨 -> レートのマッピング
    """
    if not currencies:
        return {}

    logger.info(f"為替レートを取得中... ({len(currencies)}通貨)")

    rates = {}
    exchange_client = ExchangeRateClient()
    wise_client = WiseRateClient()

    # 事前にExchangeRateClientのレートを取得
    exchange_client.fetch_rates()

    for currency in currencies:
        currency = currency.upper().strip()
        if not currency or currency == "JPY":
            rates[currency] = 1.0
            continue

        if exchange_type == "Wise":
            rate = wise_client.get_rate(currency, "JPY")
            if rate:
                rates[currency] = rate
                logger.info(f"  Wise: 1 {currency} = {rate:.4f} JPY")
            else:
                # Wiseが取得できない場合は一般レートで代用
                general_rate = exchange_client.get_rate(currency, "JPY")
                if general_rate:
                    rates[currency] = general_rate
                    logger.info(f"  Wise（代替）: 1 {currency} = {general_rate:.4f} JPY")
        else:
            # クレカレート（手数料込み）
            rate = exchange_client.get_credit_card_rate(currency, "JPY")
            if rate:
                rates[currency] = rate
                logger.info(f"  クレカ: 1 {currency} = {rate:.4f} JPY")

    return rates


def generate_supplier_id(existing_ids: set[str], prefix: str = "BS") -> str:
    """
    新しい仕入れ先商品IDを生成（{prefix}-XXXXXX形式）

    Args:
        existing_ids: 既存のIDセット
        prefix: IDのプレフィックス（デフォルト: "BS"、統合マスタ用: "SP"）

    Returns:
        str: 新しい仕入れ先商品ID
    """
    # 既存IDから最大番号を取得（指定されたプレフィックスのみ）
    max_num = 0
    prefix_with_dash = f"{prefix}-"
    for sid in existing_ids:
        if sid.startswith(prefix_with_dash):
            try:
                num = int(sid[len(prefix_with_dash):])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"{prefix}-{max_num + 1:06d}"


def save_products_to_spreadsheet(products: list[BullionstarProduct]) -> bool:
    """
    商品をスプレッドシートに保存（差分追加・価格更新モード）

    - 新規商品: 83列のデータを追加（C列にBS-XXXXXXを自動採番）
    - 既存商品: 価格関連列（P-W列）を更新
    - E列（URL）をユニークキーとして使用

    列構造（83列: A-CE）:
    - A-C列: 管理列（採用フラグ、登録状況、仕入れ先商品ID）
    - D-P列: 仕入れ先商品情報（13列）
    - Q-AG列: 価格情報（17列）
    - AH-AM列: CM商品名、画像URL等
    - AN-AS列: カテゴリー・グループ（ID・名称: 6列）
    - AT列: 型番
    - AU-CE列: カラーミー登録用項目
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    # AI生成器を初期化
    name_generator = JapaneseProductNameGenerator()
    description_generator = DescriptionGenerator()
    seo_generator = SEOGenerator()
    model_number_generator = ModelNumberGenerator()

    # カテゴリー判定器を初期化（カラーミーAPIが必要）
    category_detector = None
    categories = []
    groups = []
    # カテゴリー・グループ名称引き用辞書
    category_name_map = {}  # {category_id: category_name}
    group_name_map = {}     # {group_id: group_name}
    try:
        colorme_client = ColorMeClient()
        categories = colorme_client.get_categories()
        groups = colorme_client.get_groups()
        category_detector = CategoryDetector(categories, groups, colorme_client)

        # CategoryDetectorの固定カテゴリーID → 名称マップを構築
        # （カラーミーAPIから取得したカテゴリーとは別に管理）
        category_name_map = {
            2961572: "金貨・金地金",      # gold
            2961573: "銀貨・銀地金",      # silver
        }

        # グループ名称マップを構築（CategoryDetector.GROUP_MASTERから）
        for _, info in CategoryDetector.GROUP_MASTER.items():
            grp_id = info.get("id", 0)
            grp_name = info.get("name", "")
            if grp_id and grp_name:
                group_name_map[grp_id] = grp_name

        # カラーミーAPIから取得したグループ名称も追加（上書きしない）
        for grp in groups:
            grp_id = grp.get("id", 0)
            grp_name = grp.get("name", "")
            if grp_id and grp_name and grp_id not in group_name_map:
                group_name_map[grp_id] = grp_name

        logger.info(f"カテゴリー判定器: {len(categories)}カテゴリー, {len(groups)}グループ")
        logger.info(f"カテゴリー名称マップ: {len(category_name_map)}件, グループ名称マップ: {len(group_name_map)}件")
    except Exception as e:
        logger.warning(f"カテゴリー判定器の初期化に失敗: {e}")
        logger.warning("カテゴリー・グループ自動判定は無効化されます")

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
                cols=85  # 81列 + 余裕
            )
            sheet.update('A1:CE1', [headers])
            logger.info(f"シート '{sheet_name}' を作成しました")

        # 既存データを取得
        existing_data = sheet.get_all_values()
        if not existing_data:
            sheet.update('A1:CE1', [headers])
            logger.info("ヘッダー行を追加")
            existing_data = [headers]

        # 既存データをURLでインデックス化（E列=index 4がURL）
        existing_by_url: dict[str, tuple[int, list[str]]] = {}  # URL -> (行番号, 行データ)
        existing_ids: set[str] = set()  # 既存の仕入れ先商品ID
        for row_idx, row in enumerate(existing_data[1:], start=2):  # ヘッダー行をスキップ、行番号は2から
            if len(row) > 4 and row[4]:  # E列: URL
                existing_by_url[row[4]] = (row_idx, row)
            if len(row) > 2 and row[2]:  # C列: 仕入れ先商品ID
                existing_ids.add(row[2])

        logger.info(f"既存商品数: {len(existing_by_url)}件")

        # 新規追加行と更新行を分類
        new_rows = []
        update_cells = []  # (row, col, value) のリスト
        skipped_count = 0
        updated_count = 0
        processed_count = 0
        BATCH_SAVE_INTERVAL = 50  # 50件ごとに中間保存

        def save_batch(sheet, new_rows_batch, update_cells_batch, existing_data_len, start_row_offset):
            """バッチ保存を実行"""
            saved_new = 0
            saved_update = 0

            # 新規行を追加
            if new_rows_batch:
                start_row = existing_data_len + start_row_offset + 1
                for i, row in enumerate(new_rows_batch):
                    row_num = start_row + i
                    row[18] = f'=IF(R{row_num}="","",IF(R{row_num}=0,"",(Q{row_num}-R{row_num})/R{row_num}*100))'
                    row[24] = f'=W{row_num}*X{row_num}'
                    row[29] = f'=Y{row_num}+AB{row_num}+AC{row_num}'
                    row[30] = f'=ROUNDUP(AD{row_num}/(2-Z{row_num})+AB{row_num}+AC{row_num},-2)'
                    row[31] = f'=AE{row_num}-AD{row_num}'
                    row[32] = f'=IF(AE{row_num}=0,"",AF{row_num}/AE{row_num}*100)'
                    row[33] = f'=AE{row_num}'
                    row[34] = f'=AE{row_num}'
                    row[35] = f'=AE{row_num}'
                    row[36] = f'=AD{row_num}'
                    row[37] = f'=AH{row_num}*1.1'
                    row[38] = f'=AH{row_num}*0.1'
                sheet.append_rows(new_rows_batch, value_input_option='USER_ENTERED')
                saved_new = len(new_rows_batch)

            # 既存行を更新
            if update_cells_batch:
                batch_data = []
                for row_idx, col_idx, value in update_cells_batch:
                    col_letter = chr(ord('A') + col_idx - 1) if col_idx <= 26 else \
                                chr(ord('A') + (col_idx - 1) // 26 - 1) + chr(ord('A') + (col_idx - 1) % 26)
                    cell_ref = f"{col_letter}{row_idx}"
                    batch_data.append({'range': cell_ref, 'values': [[value]]})

                batch_size = 100
                for i in range(0, len(batch_data), batch_size):
                    batch_chunk = batch_data[i:i + batch_size]
                    sheet.batch_update(batch_chunk, value_input_option='USER_ENTERED')
                    if i + batch_size < len(batch_data):
                        time.sleep(1)
                saved_update = len(update_cells_batch)

            return saved_new, saved_update

        total_new_saved = 0
        total_update_saved = 0

        for product in products:
            if product.url in existing_by_url:
                # 既存商品: 価格情報を更新（P-W列）
                # 81列構造: P=在庫, Q=価格, R=前回価格, S=変動率, T=通貨, U=為替種類, V=為替レート, W=日本円
                row_idx, existing_row = existing_by_url[product.url]

                # 価格情報がある場合のみ更新
                if product.price is not None:
                    # R列(17): 前回仕入れ価格（現在のQ列(16)の値を保存）
                    if len(existing_row) > 16 and existing_row[16]:
                        try:
                            prev_price = float(existing_row[16].replace(",", ""))
                            update_cells.append((row_idx, 18, str(prev_price)))  # R列 = index 17 + 1 = 18
                        except ValueError:
                            pass

                    # P列(16): 仕入れ先在庫状況
                    if product.in_stock is not None:
                        stock_status = "In Stock" if product.in_stock else "Out of Stock"
                        update_cells.append((row_idx, 16, stock_status))

                    # Q列(17): 仕入れ先価格（現地通貨）
                    update_cells.append((row_idx, 17, str(product.price)))

                    # T列(20): 取引通貨
                    if product.currency:
                        update_cells.append((row_idx, 20, product.currency))

                    # U列(21): 為替種類
                    if product.exchange_type:
                        update_cells.append((row_idx, 21, product.exchange_type))

                    # V列(22): 為替レート - JPYなら1、それ以外は取得した為替レート
                    if product.currency == "JPY":
                        update_cells.append((row_idx, 22, "1"))
                    elif product.exchange_rate > 0:
                        update_cells.append((row_idx, 22, str(round(product.exchange_rate, 4))))

                    # W列(23): 仕入れ額(日本円)
                    if product.price_jpy > 0:
                        update_cells.append((row_idx, 23, str(int(product.price_jpy))))

                    updated_count += 1
                else:
                    skipped_count += 1

                # AN-AT列: カテゴリー・グループ・型番が空の場合のみAI生成（価格有無に関わらず実行）
                # AN列(40): 大カテゴリーID, AO列(41): 大カテゴリー名称
                # AP列(42): 小カテゴリーID, AQ列(43): 小カテゴリー名称
                # AR列(44): グループID, AS列(45): グループ名
                # AT列(46): 型番
                existing_cat_big = existing_row[39] if len(existing_row) > 39 else ""
                existing_cat_big_name = existing_row[40] if len(existing_row) > 40 else ""
                existing_cat_small = existing_row[41] if len(existing_row) > 41 else ""
                existing_cat_small_name = existing_row[42] if len(existing_row) > 42 else ""
                existing_group_ids = existing_row[43] if len(existing_row) > 43 else ""
                existing_group_names = existing_row[44] if len(existing_row) > 44 else ""
                existing_model_number = existing_row[45] if len(existing_row) > 45 else ""

                # カテゴリー・グループ自動判定（AN-AS列: 6列）
                # IDまたは名称が空の場合に更新を試みる
                needs_category_update = (
                    not existing_cat_big or not existing_cat_big_name or
                    not existing_group_ids or not existing_group_names
                )
                if category_detector and needs_category_update:
                    try:
                        cat_big, cat_small, group_ids = category_detector.detect(product.name, product.url)
                        # 大カテゴリーID/名称の更新
                        if cat_big:
                            if not existing_cat_big:
                                update_cells.append((row_idx, 40, str(cat_big)))  # AN列: 大カテゴリーID
                            # AO列: 大カテゴリー名称を取得（IDがあれば名称も設定）
                            if not existing_cat_big_name:
                                # 既存のカテゴリーIDまたは新規判定のIDを使用
                                lookup_id = int(existing_cat_big) if existing_cat_big else cat_big
                                cat_big_name = category_name_map.get(lookup_id, "")
                                if cat_big_name:
                                    update_cells.append((row_idx, 41, cat_big_name))  # AO列
                        # 小カテゴリーID/名称の更新
                        if cat_small:
                            if not existing_cat_small:
                                update_cells.append((row_idx, 42, str(cat_small)))  # AP列: 小カテゴリーID
                            # AQ列: 小カテゴリー名称を取得
                            if not existing_cat_small_name:
                                lookup_id = int(existing_cat_small) if existing_cat_small else cat_small
                                cat_small_name = category_name_map.get(lookup_id, "")
                                if cat_small_name:
                                    update_cells.append((row_idx, 43, cat_small_name))  # AQ列
                        # グループID/名称の更新
                        if group_ids:
                            if not existing_group_ids:
                                update_cells.append((row_idx, 44, ",".join(str(g) for g in group_ids)))  # AR列: グループID
                            # AS列: グループ名称を取得（IDがあれば名称も設定）
                            if not existing_group_names:
                                # 既存のグループIDまたは新規判定のIDを使用
                                if existing_group_ids:
                                    lookup_ids = [int(g.strip()) for g in existing_group_ids.split(",") if g.strip()]
                                else:
                                    lookup_ids = group_ids
                                group_names = [group_name_map.get(g, "") for g in lookup_ids]
                                group_names = [n for n in group_names if n]  # 空文字を除外
                                if group_names:
                                    update_cells.append((row_idx, 45, ",".join(group_names)))  # AS列
                        logger.debug(f"  既存商品カテゴリー更新: 大={cat_big}, 小={cat_small}, グループ={group_ids}")
                    except Exception as e:
                        logger.debug(f"  既存商品カテゴリー判定エラー: {e}")

                # 型番自動生成（AT列）
                if model_number_generator.client and not existing_model_number:
                    try:
                        model_info = {
                            "name": product.name,
                            "specs": product.specs or "",
                            "description": product.description_en or "",
                        }
                        model_number = model_number_generator.generate(model_info, quantity=1)
                        if model_number:
                            update_cells.append((row_idx, 46, model_number))  # AT列
                            logger.debug(f"  既存商品型番更新: {model_number}")
                    except Exception as e:
                        logger.debug(f"  既存商品型番生成エラー: {e}")

                # 計算式列が空の場合に数式を追加（S, Y, AD-AM列）
                # 既存の値をチェック（数式か値かに関わらず、空の場合のみ設定）
                existing_s = existing_row[18] if len(existing_row) > 18 else ""
                existing_y = existing_row[24] if len(existing_row) > 24 else ""
                existing_ad = existing_row[29] if len(existing_row) > 29 else ""
                existing_ae = existing_row[30] if len(existing_row) > 30 else ""
                existing_af = existing_row[31] if len(existing_row) > 31 else ""
                existing_ag = existing_row[32] if len(existing_row) > 32 else ""
                existing_ah = existing_row[33] if len(existing_row) > 33 else ""
                existing_ak = existing_row[36] if len(existing_row) > 36 else ""
                existing_al = existing_row[37] if len(existing_row) > 37 else ""
                existing_am = existing_row[38] if len(existing_row) > 38 else ""

                # デフォルト値を追加（空の列のみ）- A列、B列、AB列、AC列
                existing_a = existing_row[0] if len(existing_row) > 0 else ""
                existing_b = existing_row[1] if len(existing_row) > 1 else ""
                existing_ab = existing_row[27] if len(existing_row) > 27 else ""
                existing_ac = existing_row[28] if len(existing_row) > 28 else ""
                if not existing_a:
                    update_cells.append((row_idx, 1, "検討中"))  # A列: 採用フラグ
                if not existing_b:
                    update_cells.append((row_idx, 2, "未登録"))  # B列: カラーミー登録状況
                if not existing_ab:
                    update_cells.append((row_idx, 28, "100"))  # AB列: 送料
                if not existing_ac:
                    update_cells.append((row_idx, 29, "50"))   # AC列: 諸経費

                # 計算式を追加（空の列のみ）- update_cellsに追加（後でbatch_updateで処理）
                if not existing_s:
                    update_cells.append((row_idx, 19, f'=IF(R{row_idx}="","",IF(R{row_idx}=0,"",(Q{row_idx}-R{row_idx})/R{row_idx}*100))'))
                if not existing_y:
                    update_cells.append((row_idx, 25, f'=W{row_idx}*X{row_idx}'))
                if not existing_ad:
                    update_cells.append((row_idx, 30, f'=Y{row_idx}+AB{row_idx}+AC{row_idx}'))
                if not existing_ae:
                    update_cells.append((row_idx, 31, f'=ROUNDUP(AD{row_idx}/(2-Z{row_idx})+AB{row_idx}+AC{row_idx},-2)'))
                if not existing_af:
                    update_cells.append((row_idx, 32, f'=AE{row_idx}-AD{row_idx}'))
                if not existing_ag:
                    update_cells.append((row_idx, 33, f'=IF(AE{row_idx}=0,"",AF{row_idx}/AE{row_idx}*100)'))
                if not existing_ah:
                    update_cells.append((row_idx, 34, f'=AE{row_idx}'))
                # AI列(35): 定価 = AE
                existing_ai = existing_row[34] if len(existing_row) > 34 else ""
                if not existing_ai:
                    update_cells.append((row_idx, 35, f'=AE{row_idx}'))
                # AJ列(36): 会員価格 = AE
                existing_aj = existing_row[35] if len(existing_row) > 35 else ""
                if not existing_aj:
                    update_cells.append((row_idx, 36, f'=AE{row_idx}'))
                if not existing_ak:
                    update_cells.append((row_idx, 37, f'=AD{row_idx}'))
                if not existing_al:
                    update_cells.append((row_idx, 38, f'=AH{row_idx}*1.1'))
                if not existing_am:
                    update_cells.append((row_idx, 39, f'=AH{row_idx}*0.1'))
            else:
                # 新規商品: 83列のデータを作成
                supplier_id = generate_supplier_id(existing_ids)
                existing_ids.add(supplier_id)

                # 在庫状況の文字列変換
                stock_status = ""
                if product.in_stock is not None:
                    stock_status = "In Stock" if product.in_stock else "Out of Stock"

                # CM商品名を自動生成（AH列）
                product_info = {
                    "name": product.name,
                    "specs": product.specs,
                    "description": product.description_en,
                }
                cm_product_name = name_generator.generate(product_info, quantity=1)
                if not cm_product_name:
                    cm_product_name = ""  # 生成失敗時は空欄

                # カテゴリー・グループ自動判定（AN-AS列: 6列）
                # 注: CategoryDetectorは大カテゴリーIDのみを返す（小カテゴリーは常に0）
                category_big = ""
                category_big_name = ""
                category_small = ""
                category_small_name = ""
                group_ids_str = ""
                group_names_str = ""
                if category_detector:
                    try:
                        cat_big, cat_small, group_ids = category_detector.detect(product.name, product.url)
                        if cat_big:
                            category_big = str(cat_big)
                            # カテゴリー名称を取得（単一IDでルックアップ）
                            category_big_name = category_name_map.get(cat_big, "")
                            if cat_small:
                                category_small = str(cat_small)
                                category_small_name = category_name_map.get(cat_small, "")
                        if group_ids:
                            group_ids_str = ",".join(str(g) for g in group_ids)
                            # グループ名称を取得
                            group_names = [group_name_map.get(g, "") for g in group_ids]
                            group_names_str = ",".join(n for n in group_names if n)
                        logger.debug(f"  カテゴリー自動判定: 大={category_big}({category_big_name}), 小={category_small}({category_small_name}), グループ={group_ids_str}({group_names_str})")
                    except Exception as e:
                        logger.debug(f"  カテゴリー判定エラー: {e}")

                # 商品説明を自動生成（BP-BR列）
                cm_description = ""
                cm_simple_description = ""
                if description_generator.client:
                    try:
                        price_jpy = int(product.price_jpy) if product.price_jpy else 0
                        desc_info = {
                            "name": product.name,
                            "price": price_jpy,
                            "currency": "JPY",
                            "description": product.description_en or "",
                            "specs": product.specs or ""
                        }
                        cm_description, cm_simple_description = description_generator.generate(desc_info)
                        if cm_description:
                            logger.debug(f"  商品説明: 自動生成 ({len(cm_description)}文字)")
                    except Exception as e:
                        logger.debug(f"  商品説明生成エラー: {e}")

                # SEO項目を自動生成（BQ-BS列）
                page_title = ""
                meta_description = ""
                meta_keywords = ""
                if seo_generator.client:
                    try:
                        price_jpy = int(product.price_jpy) if product.price_jpy else 0
                        seo_info = {
                            "name": product.name,
                            "price": price_jpy,
                            "description": product.description_en or "",
                            "specs": product.specs or ""
                        }
                        page_title, meta_description, meta_keywords = seo_generator.generate(seo_info)
                        if page_title:
                            logger.debug(f"  SEO項目: 自動生成")
                    except Exception as e:
                        logger.debug(f"  SEO生成エラー: {e}")

                # 型番を自動生成（AQ列）
                model_number = ""
                if model_number_generator.client:
                    try:
                        model_info = {
                            "name": product.name,
                            "specs": product.specs or "",
                            "description": product.description_en or "",
                        }
                        model_number = model_number_generator.generate(model_info, quantity=1)
                        if model_number:
                            logger.debug(f"  型番: AI生成 → {model_number}")
                    except Exception as e:
                        logger.debug(f"  型番生成エラー: {e}")

                # フォールバック: AI生成失敗時は仕入れ先商品IDを使用
                if not model_number:
                    model_number = supplier_id

                # 83列構造（A-CE）: カテゴリー名称追加による新列順
                new_row = [
                    # === 管理列（A-C列: 3列）===
                    product.adopted_flag,                           # A: 採用フラグ
                    product.colorme_registration,                   # B: カラーミー登録状況
                    supplier_id,                                    # C: 仕入れ先商品ID

                    # === 仕入れ先商品情報（D-P列: 13列）===
                    "",                                             # D: カラーミー商品URL（登録後自動）
                    product.url,                                    # E: 仕入れ先商品URL（ユニークキー）
                    product.name,                                   # F: 仕入れ先商品名
                    product.site,                                   # G: 仕入れ先サイト（Bullionstar）
                    product.top_category,                           # H: 最上位カテゴリ
                    product.parent_category,                        # I: 親カテゴリ
                    product.child_category,                         # J: 子カテゴリ
                    product.location,                               # K: 製造国
                    product.description_en,                         # L: 商品説明（英語）
                    product.specs,                                  # M: 仕様・スペック
                    product.mint_year,                              # N: 発行年
                    product.mintage,                                # O: 発行数・限定数
                    stock_status,                                   # P: 仕入れ先在庫状況

                    # === 価格情報（Q-AG列: 17列）===
                    str(product.price) if product.price else "",    # Q: 仕入れ先価格（現地通貨）
                    "",                                             # R: 前回仕入れ価格
                    "",                                             # S: 価格変動率（計算式）
                    product.currency,                               # T: 取引通貨
                    product.exchange_type,                          # U: 為替種類
                    # V列: 為替レート - JPYなら1、それ以外は取得した為替レート
                    "1" if product.currency == "JPY" else (str(product.exchange_rate) if product.exchange_rate else ""),
                    str(int(product.price_jpy)) if product.price_jpy else "",  # W: 仕入れ額(日本円)
                    "1",                                            # X: 枚数（デフォルト1）
                    "",                                             # Y: 仕入れ合計（計算式）
                    "1.1",                                          # Z: 設定マージン率（デフォルト1.1）
                    "",                                             # AA: 設定マージン額（手入力）
                    "100",                                          # AB: 送料（デフォルト100）
                    "50",                                           # AC: 諸経費（デフォルト50）
                    "",                                             # AD: 合計原価（計算式）
                    "",                                             # AE: 適正価格（計算式）
                    "",                                             # AF: 粗利額（計算式）
                    "",                                             # AG: 粗利率（計算式）

                    # === カラーミー価格情報（AH-AM列: 6列）===
                    "",                                             # AH: 販売価格
                    "",                                             # AI: 定価
                    "",                                             # AJ: 会員価格
                    "",                                             # AK: 原価
                    "",                                             # AL: 消費税込販売価格
                    "",                                             # AM: 消費税額

                    # === カテゴリー・グループ（AN-AS列: 6列）===
                    category_big,                                   # AN: 大カテゴリーID（自動判定）
                    category_big_name,                              # AO: 大カテゴリー名称
                    category_small,                                 # AP: 小カテゴリーID（自動判定）
                    category_small_name,                            # AQ: 小カテゴリー名称
                    group_ids_str,                                  # AR: グループID（自動判定）
                    group_names_str,                                # AS: グループ名

                    # === 型番（AT列: 1列）===
                    model_number,                                   # AT: 型番（AI生成、失敗時は仕入れ先商品ID）

                    # === 在庫管理（AU-BA列: 7列）===
                    "10",                                           # AU: 在庫数（デフォルト10）
                    "する",                                         # AV: 在庫管理（デフォルト「する」）
                    "3",                                            # AW: 残りわずか数（デフォルト3）
                    "表示",                                         # AX: 売切れ表示（デフォルト「表示」）
                    "1",                                            # AY: 最小購入数（デフォルト1）
                    "10",                                           # AZ: 最大購入数（デフォルト10）
                    "",                                             # BA: 単位（手入力、空欄可）

                    # === 送料・配送（BB-BE列: 4列）===
                    "1000",                                         # BB: 個別送料（デフォルト1000）
                    "",                                             # BC: クール便料金（手入力）
                    "",                                             # BD: 重量(g)（手入力）
                    "",                                             # BE: 配送不要（手入力）

                    # === 商品説明（BF-BI列: 4列）===
                    cm_description,                                 # BF: 商品説明（自動生成）
                    cm_simple_description,                          # BG: 簡易説明（自動生成）
                    "",                                             # BH: スマホ説明
                    "",                                             # BI: 備考

                    # === 画像URL（BJ-BS列: 10列）===
                    product.image_url1,                             # BJ: 画像URL1
                    product.image_url2,                             # BK: 画像URL2
                    product.image_url3,                             # BL: 画像URL3
                    product.image_url4,                             # BM: 画像URL4
                    product.image_url5,                             # BN: 画像URL5
                    product.image_url6,                             # BO: 画像URL6
                    product.image_url7,                             # BP: 画像URL7
                    product.image_url8,                             # BQ: 画像URL8
                    product.image_url9,                             # BR: 画像URL9
                    product.image_url10,                            # BS: 画像URL10

                    # === SEO項目（BT-BV列: 3列）===
                    page_title,                                     # BT: ページタイトル（自動生成）
                    meta_description,                               # BU: メタディスクリプション（自動生成）
                    meta_keywords,                                  # BV: メタキーワード（自動生成）

                    # === フラグ・設定（BW-CA列: 5列）===
                    "対象外",                                       # BW: 軽減税率対象（デフォルト「対象外」）
                    "対象外",                                       # BX: デジタルコンテンツ（デフォルト「対象外」）
                    "対象外",                                       # BY: 定期購入（デフォルト「対象外」）
                    "0",                                            # BZ: 表示順（デフォルト0）
                    "",                                             # CA: 利用不可決済（手入力）

                    # === 掲載期間（CB-CC列: 2列）===
                    "",                                             # CB: 掲載開始日時
                    "",                                             # CC: 掲載終了日時

                    # === システム情報（CD-CE列: 2列）※元は3列だが調整===
                    "",                                             # CD: 同期日時
                    "",                                             # CE: 商品更新日時
                ]
                new_rows.append(new_row)

            # 50件ごとに中間保存
            processed_count += 1
            if processed_count % BATCH_SAVE_INTERVAL == 0:
                logger.info(f"中間保存中... ({processed_count}/{len(products)}件処理済み)")
                saved_new, saved_update = save_batch(
                    sheet, new_rows, update_cells,
                    len(existing_data), total_new_saved
                )
                total_new_saved += saved_new
                total_update_saved += saved_update
                # リストをクリアして次のバッチへ
                new_rows = []
                update_cells = []
                logger.info(f"  中間保存完了: 新規{saved_new}件, 更新{saved_update}件")

        # 残りを保存
        if new_rows or update_cells:
            logger.info(f"最終保存中... (残り{len(new_rows)}新規, {len(update_cells)}更新)")
            saved_new, saved_update = save_batch(
                sheet, new_rows, update_cells,
                len(existing_data), total_new_saved
            )
            total_new_saved += saved_new
            total_update_saved += saved_update

        # 結果サマリー
        if total_new_saved > 0:
            logger.info(f"新規追加: {total_new_saved}件（計算式含む）")
        if total_update_saved > 0:
            logger.info(f"価格更新: {updated_count}件（計算式含む）")

        if skipped_count > 0:
            logger.info(f"スキップ（価格情報なし）: {skipped_count}件")

        if total_new_saved == 0 and total_update_saved == 0:
            logger.info("追加・更新する商品はありませんでした")

        # 注: 商品仕入れ先一覧への同期は初期登録時に行う
        # （ブリオンスター商品ページ一覧のB列=「登録済」になったタイミング）

        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


def sync_to_supplier_list(products: list[BullionstarProduct], client: SpreadsheetClient) -> bool:
    """
    商品仕入れ先一覧シートに同期する

    ブリオンスター商品ページ一覧シートの商品データを
    商品仕入れ先一覧シートにも反映する。
    これにより、新カラーミー商品管理シートからVLOOKUPで参照可能になる。

    列構造（35列: A-AI）:
    - A-R列: 商品基本情報・価格情報
    - S-T列: カラーミー商品ID / 備考
    - U-AI列: 画像・詳細情報
    ※採用フラグ・カラーミー登録状況列はなし

    Args:
        products: 商品リスト
        client: SpreadsheetClient インスタンス

    Returns:
        bool: 成功時True
    """
    sheet_name = Config.SHEET_SUPPLIERS
    headers = Config.SUPPLIER_HEADERS

    try:
        # シート取得または作成
        try:
            sheet = client._spreadsheet.worksheet(sheet_name)
            logger.info(f"商品仕入れ先一覧シート '{sheet_name}' に同期中...")
        except Exception:
            sheet = client._spreadsheet.add_worksheet(
                title=sheet_name,
                rows=10000,
                cols=40
            )
            sheet.update('A1:AI1', [headers])
            logger.info(f"商品仕入れ先一覧シート '{sheet_name}' を作成しました")

        # 既存データを取得
        existing_data = sheet.get_all_values()
        if not existing_data:
            sheet.update('A1:AI1', [headers])
            existing_data = [headers]

        # 既存データをURLでインデックス化（C列=index 2がURL）
        existing_by_url: dict[str, tuple[int, list[str]]] = {}
        existing_ids: set[str] = set()
        for row_idx, row in enumerate(existing_data[1:], start=2):
            if len(row) > 2 and row[2]:  # C列: URL
                existing_by_url[row[2]] = (row_idx, row)
            if len(row) > 0 and row[0]:  # A列: 仕入れ先商品ID
                existing_ids.add(row[0])

        # 新規追加行と更新行を分類
        new_rows = []
        update_cells = []
        new_count = 0
        updated_count = 0

        for product in products:
            if product.url in existing_by_url:
                # 既存商品: 価格関連列を更新（J-R列）
                # 新列構造: J=在庫, K=価格, L=通貨, M=為替種類, N=為替レート, O=日本円, P=更新日時, Q=前回価格, R=変動率
                row_idx, existing_row = existing_by_url[product.url]

                if product.price is not None:
                    # Q列(17): 前回価格（現在のK列(10)の値を保存）
                    if len(existing_row) > 10 and existing_row[10]:
                        try:
                            prev_price = float(existing_row[10])
                            update_cells.append((row_idx, 17, str(prev_price)))
                        except ValueError:
                            pass

                    # J列(10): 在庫状況
                    if product.in_stock is not None:
                        stock_status = "In Stock" if product.in_stock else "Out of Stock"
                        update_cells.append((row_idx, 10, stock_status))

                    # K列(11): 現在価格
                    update_cells.append((row_idx, 11, str(product.price)))

                    # L列(12): 取引通貨
                    if product.currency:
                        update_cells.append((row_idx, 12, product.currency))

                    # P列(16): 最終価格更新日時
                    update_cells.append((row_idx, 16, product.last_price_updated or product.fetched_at))

                    updated_count += 1
            else:
                # 新規商品: 35列のデータを作成（採用フラグ・登録状況なし）
                supplier_id = generate_supplier_id(existing_ids, prefix="SP")  # 統合マスタ用プレフィックス
                existing_ids.add(supplier_id)

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
                    product.location,                               # H: 製造国
                    product.fetched_at,                             # I: 初回取得日
                    stock_status,                                   # J: 在庫状況
                    str(product.price) if product.price else "",    # K: 現在価格
                    product.currency,                               # L: 取引通貨
                    product.exchange_type,                          # M: 為替種類
                    str(product.exchange_rate) if product.exchange_rate else "",  # N: 為替レート
                    str(int(product.price_jpy)) if product.price_jpy else "",  # O: 日本円換算価格
                    product.last_price_updated,                     # P: 最終価格更新日時
                    str(product.prev_price) if product.prev_price else "",  # Q: 前回価格
                    product.price_change_rate,                      # R: 価格変動率
                    product.colorme_id,                             # S: カラーミー商品ID
                    product.memo,                                   # T: 備考
                    # 画像URL（U-AD列: 10列）
                    product.image_url1,                             # U: 画像URL1
                    product.image_url2,                             # V: 画像URL2
                    product.image_url3,                             # W: 画像URL3
                    product.image_url4,                             # X: 画像URL4
                    product.image_url5,                             # Y: 画像URL5
                    product.image_url6,                             # Z: 画像URL6
                    product.image_url7,                             # AA: 画像URL7
                    product.image_url8,                             # AB: 画像URL8
                    product.image_url9,                             # AC: 画像URL9
                    product.image_url10,                            # AD: 画像URL10
                    # 商品情報（AE-AI列: 5列）
                    product.specs,                                  # AE: 仕様・スペック
                    product.description_en,                         # AF: 商品説明（英語）
                    product.description_ja,                         # AG: 商品説明（日本語）
                    product.mint_year,                              # AH: 発行年
                    product.mintage,                                # AI: 発行数・限定数
                ]
                new_rows.append(new_row)
                new_count += 1

        # 新規行を追加
        if new_rows:
            sheet.append_rows(new_rows, value_input_option='RAW')
            logger.info(f"  商品仕入れ先一覧: 新規追加 {new_count}件")

        # 既存行を更新
        if update_cells:
            batch_data = []
            for row_idx, col_idx, value in update_cells:
                col_letter = chr(ord('A') + col_idx - 1) if col_idx <= 26 else \
                            chr(ord('A') + (col_idx - 1) // 26 - 1) + chr(ord('A') + (col_idx - 1) % 26)
                cell_ref = f"{col_letter}{row_idx}"
                batch_data.append({
                    'range': cell_ref,
                    'values': [[value]]
                })

            batch_size = 100
            for i in range(0, len(batch_data), batch_size):
                batch_chunk = batch_data[i:i + batch_size]
                sheet.batch_update(batch_chunk, value_input_option='RAW')
                if i + batch_size < len(batch_data):
                    time.sleep(1)

            logger.info(f"  商品仕入れ先一覧: 価格更新 {updated_count}件")

        if not new_rows and not update_cells:
            logger.info("  商品仕入れ先一覧: 更新なし")

        return True

    except Exception as e:
        logger.warning(f"商品仕入れ先一覧への同期エラー: {e}")
        return False


def fetch_bullionstar_products(limit: Optional[int] = None) -> list[BullionstarProduct]:
    """Bullionstarから商品ページ一覧を取得

    Args:
        limit: 取得件数制限（Noneで全件）
    """
    fetcher = BullionstarProductFetcher()
    return fetcher.get_all_products(limit=limit)


def get_existing_urls_from_spreadsheet() -> set[str]:
    """
    スプレッドシートから既存商品のURLを取得

    Returns:
        set[str]: 既存商品URLのセット
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.warning("スプレッドシートへの接続に失敗。既存URLチェックをスキップ")
        return set()

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        # E列（URL）のみ取得（高速化のため）
        url_column = sheet.col_values(5)  # E列 = 5
        existing_urls = set(url_column[1:])  # ヘッダー行をスキップ
        logger.info(f"既存商品URL: {len(existing_urls)}件")
        return existing_urls
    except Exception as e:
        logger.warning(f"既存URL取得エラー: {e}")
        return set()


def fetch_prices_for_products(
    products: list[BullionstarProduct],
    limit: Optional[int] = None,
    exchange_type: str = "クレカ",
    save_callback=None,
    batch_size: int = 50
) -> list[BullionstarProduct]:
    """
    商品リストの価格・在庫・画像・仕様情報をスクレイピングで取得し、
    日本円換算価格も計算する。
    batch_size件ごとにsave_callbackを呼び出して中間保存する。

    Args:
        products: 商品リスト
        limit: 取得件数制限（Noneで全件）
        exchange_type: 為替種類（"クレカ" または "Wise"）
        save_callback: 中間保存用コールバック関数（商品リストを受け取る）
        batch_size: 中間保存の間隔（デフォルト50件）

    Returns:
        価格・画像・仕様情報・日本円換算価格が付加された商品リスト
    """
    try:
        from playwright.sync_api import sync_playwright
        from src.shops.bullionstar import BullionstarScraper
    except ImportError:
        logger.error("Playwrightがインストールされていません。pip install playwright && playwright install chromium")
        return products

    target_products = products[:limit] if limit else products
    logger.info(f"価格・画像取得開始: {len(target_products)}件")
    if save_callback:
        logger.info(f"  → {batch_size}件ごとに中間保存します")

    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    success_count = 0
    error_count = 0
    currencies_found: set[str] = set()  # 見つかった通貨を収集
    last_saved_index = 0  # 最後に保存したインデックス（二重保存防止用）

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
                    scraper.reset_extra_fields()
                    continue

                # 価格情報を更新
                product.price = result.price
                product.currency = result.currency  # M列: 実際の取引通貨
                product.in_stock = result.in_stock
                product.last_price_updated = timestamp

                # JPY以外の通貨が検出された場合は記録（後で為替変換）
                if result.currency and result.currency != "JPY":
                    currencies_found.add(result.currency)
                    # 一旦為替レート未設定（後でまとめて設定）
                    product.exchange_rate = 0.0
                    product.price_jpy = 0.0
                else:
                    # JPYの場合は為替レート1、日本円価格はそのまま
                    product.exchange_type = "なし"  # JPYなので為替不要
                    product.exchange_rate = 1.0
                    product.price_jpy = result.price if result.price else 0.0

                # 製造国を取得
                scraped_location = scraper.get_location()
                if scraped_location:
                    product.location = scraped_location

                # 画像情報を取得
                # メイン画像URLを画像URL1として設定
                main_image = scraper.get_main_image_url()
                if main_image:
                    product.image_url1 = main_image

                # 追加画像URLを画像URL2-10として設定
                image_urls = scraper.get_image_urls()
                if len(image_urls) > 0:
                    product.image_url2 = image_urls[0]
                if len(image_urls) > 1:
                    product.image_url3 = image_urls[1]
                if len(image_urls) > 2:
                    product.image_url4 = image_urls[2]
                if len(image_urls) > 3:
                    product.image_url5 = image_urls[3]
                if len(image_urls) > 4:
                    product.image_url6 = image_urls[4]
                if len(image_urls) > 5:
                    product.image_url7 = image_urls[5]
                if len(image_urls) > 6:
                    product.image_url8 = image_urls[6]
                if len(image_urls) > 7:
                    product.image_url9 = image_urls[7]
                if len(image_urls) > 8:
                    product.image_url10 = image_urls[8]

                # 仕様・詳細情報を取得
                product.specs = scraper.get_specs()
                product.description_en = scraper.get_description_en()
                product.mint_year = scraper.get_mint_year()
                product.mintage = scraper.get_mintage()

                # ログ出力
                img_count = 1 + len(image_urls) if main_image else len(image_urls)
                logger.info(
                    f"  価格: {result.currency} {result.price:.2f}, "
                    f"在庫: {'あり' if result.in_stock else 'なし'}, "
                    f"画像: {img_count}枚"
                )
                if scraped_location:
                    logger.info(f"  製造国: {scraped_location}")
                if product.specs:
                    logger.info(f"  仕様: {product.specs[:50]}...")
                if product.mint_year:
                    logger.info(f"  発行年: {product.mint_year}")

                # 次の商品用にスクレイパーの状態をリセット
                scraper.reset_extra_fields()

                success_count += 1

                # 中間保存（batch_size件ごと）
                if save_callback and success_count % batch_size == 0:
                    logger.info(f"\n{'='*40}")
                    logger.info(f"中間保存: {success_count}/{len(target_products)}件完了")
                    logger.info(f"{'='*40}")
                    # 前回保存分以降の新規バッチのみ保存（二重処理防止）
                    batch_products = target_products[last_saved_index:i+1]
                    save_callback(batch_products)
                    last_saved_index = i + 1
                    logger.info(f"中間保存完了: {len(batch_products)}件")

                # レート制限対策
                time.sleep(random.uniform(1.0, 2.0))

            except Exception as e:
                logger.error(f"  スクレイピングエラー: {e}")
                error_count += 1
                scraper.reset_extra_fields()
                continue

        browser.close()

    logger.info(f"価格・画像取得完了: 成功={success_count}件, 失敗={error_count}件")

    # 最終保存（残りの商品）
    if save_callback and last_saved_index < len(target_products):
        remaining_products = target_products[last_saved_index:]
        logger.info(f"\n{'='*40}")
        logger.info(f"最終保存: {len(remaining_products)}件")
        logger.info(f"{'='*40}")
        save_callback(remaining_products)
        logger.info(f"最終保存完了")

    # BullionstarはJPY価格を取得するため、為替レート取得は通常不要
    # （スクレイピング時にJPY/為替レート1/日本円価格を設定済み）
    # 万が一JPY以外の通貨が検出された場合のみ為替レートを取得
    if currencies_found:
        logger.info("\n" + "=" * 60)
        logger.info(f"JPY以外の通貨が検出されました: {currencies_found}")
        logger.info("為替レートを取得して日本円換算価格を計算")
        logger.info("=" * 60)

        exchange_rates = fetch_exchange_rates(list(currencies_found), exchange_type)

        # 各商品の日本円換算価格を計算（JPY以外のみ）
        jpy_calculated_count = 0
        for product in target_products:
            if product.price is not None and product.currency:
                currency = product.currency.upper()
                if currency != "JPY":
                    rate = exchange_rates.get(currency)
                    if rate:
                        product.exchange_type = exchange_type
                        product.exchange_rate = rate
                        product.price_jpy = round(product.price * rate, 0)
                        jpy_calculated_count += 1

        logger.info(f"日本円換算完了: {jpy_calculated_count}件")

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
    parser.add_argument("--exchange-type", type=str, default="クレカ",
                        choices=["クレカ", "Wise"], help="為替種類 (デフォルト: クレカ)")

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

    # limitはfetch_prices有効時のみAPI取得にも適用
    api_limit = args.limit if args.fetch_prices else None
    products = fetch_bullionstar_products(limit=api_limit)

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
        if product.location:
            logger.info(f"     {product.top_category} > {product.parent_category} [製造国: {product.location}]")
        else:
            logger.info(f"     {product.top_category} > {product.parent_category}")

    if len(products) > 10:
        logger.info(f"  ... 他 {len(products) - 10} 件")

    # 価格取得
    if args.fetch_prices:
        logger.info("\n" + "=" * 60)
        logger.info(f"価格・在庫情報を取得（為替種類: {args.exchange_type}）")
        logger.info("=" * 60)

        # 既存商品URLを取得してスクレイピング対象から除外
        if not args.dry_run:
            existing_urls = get_existing_urls_from_spreadsheet()
            new_products = [p for p in products if p.url not in existing_urls]
            skipped_count = len(products) - len(new_products)
            if skipped_count > 0:
                logger.info(f"既存商品をスキップ: {skipped_count}件")
                logger.info(f"スクレイピング対象: {len(new_products)}件（新規のみ）")
            products = new_products

        # 中間保存用コールバック（ドライランでない場合のみ）
        save_callback = None
        if not args.dry_run:
            def save_callback(processed_products):
                """スクレイピング中間保存用コールバック"""
                save_products_to_spreadsheet(processed_products)

        products = fetch_prices_for_products(
            products,
            limit=args.limit,
            exchange_type=args.exchange_type,
            save_callback=save_callback,
            batch_size=50
        )

        # limitが指定されている場合、保存する商品もlimit件数に制限
        if args.limit and args.limit < len(products):
            products = products[:args.limit]
            logger.info(f"保存対象を{args.limit}件に制限")

        # fetch_pricesモードでは中間保存で既に保存済みなので、追加の保存は不要
        if not args.dry_run:
            logger.info("スプレッドシートへの保存完了（中間保存済み）")

    elif args.dry_run:
        logger.info("\n[ドライラン] スプレッドシートへの保存をスキップ")
    else:
        # fetch_pricesなしの場合は従来通り最後に保存
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
