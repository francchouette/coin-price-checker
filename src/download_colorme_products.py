"""
カラーミー商品ダウンロードスクリプト

カラーミーAPIから全商品を取得し、新カラーミー商品管理シートに書き込む。
J列（仕入れ先商品URL）がある場合は、価格を自動取得してV列に反映する。
"""

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime

from .spreadsheet import SpreadsheetClient
from .colorme import ColorMeClient
from .config import Config
from .scraper import ScraperManager, detect_shop_from_url
from .shops import ScrapedData
from .exchange_rate import ExchangeRateClient, WiseRateClient
from .cm_sheet_columns import Col, get_cell, preserve_or_set, Formula

# 削除された列への後方互換性のためのダミー定義（スクレイピング結果を無視）
# ※以下の列は新カラーミー商品管理シートから削除されました
# - TOP_CATEGORY, PARENT_CATEGORY, CHILD_CATEGORY
# - COUNTRY, DESCRIPTION_EN, SPECS, MINT_YEAR, MINTAGE
# - CATEGORY_ID_SMALL, CATEGORY_NAME_SMALL
# - CREATED_DATE, UPDATED_DATE

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def fetch_exchange_rates(currencies: list[str], exchange_types: dict[str, str]) -> dict[str, float]:
    """
    通貨リストから為替レートを取得する

    Args:
        currencies: 通貨コードのリスト（例: ["USD", "SGD"]）
        exchange_types: 通貨 -> 為替種類（"クレカ" または "Wise"）のマッピング

    Returns:
        dict: 通貨 -> レートのマッピング（"通貨_種類"形式のキー）
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
            continue

        exchange_type = exchange_types.get(currency, "クレカ")

        if exchange_type == "Wise":
            rate = wise_client.get_rate(currency, "JPY")
            if rate:
                rates[f"{currency}_Wise"] = rate
                logger.info(f"  Wise: 1 {currency} = {rate:.4f} JPY")
            else:
                # Wiseが取得できない場合は一般レートで代用
                general_rate = exchange_client.get_rate(currency, "JPY")
                if general_rate:
                    rates[f"{currency}_Wise"] = general_rate
                    logger.info(f"  Wise（代替）: 1 {currency} = {general_rate:.4f} JPY")
        else:
            # クレカレート（手数料込み）
            rate = exchange_client.get_credit_card_rate(currency, "JPY")
            if rate:
                rates[f"{currency}_クレカ"] = rate
                logger.info(f"  クレカ: 1 {currency} = {rate:.4f} JPY")

    return rates


@dataclass
class ScrapedDataWithExtras:
    """スクレイピング結果の拡張データクラス（追加情報含む）"""
    scraped_data: ScrapedData
    location: str = ""          # 製造国
    description_en: str = ""    # 商品説明（英語）
    specs: str = ""             # 仕様・スペック
    mint_year: str = ""         # 発行年
    mintage: str = ""           # 発行数・限定数


def fetch_prices_from_urls(urls: list[str]) -> dict[str, ScrapedDataWithExtras]:
    """
    URLリストから価格情報と追加情報をスクレイピングする

    Args:
        urls: スクレイピング対象のURLリスト

    Returns:
        dict: URL -> ScrapedDataWithExtras のマッピング
    """
    if not urls:
        return {}

    logger.info(f"仕入れ先URLから価格を取得中... ({len(urls)}件)")

    results = {}
    with ScraperManager() as manager:
        for url in urls:
            shop_name = detect_shop_from_url(url)
            scraper = manager.get_scraper(shop_name)

            if not scraper:
                results[url] = ScrapedDataWithExtras(
                    scraped_data=ScrapedData(
                        product_name="",
                        price=0.0,
                        currency="",
                        url=url,
                        in_stock=False,
                        error=f"未対応のショップ: {shop_name}"
                    )
                )
                continue

            # スクレイピング実行
            scraped = scraper.scrape(url)

            # 追加情報を取得（BullionstarScraperの場合のみ）
            location = ""
            description_en = ""
            specs = ""
            mint_year = ""
            mintage = ""

            if hasattr(scraper, 'get_location'):
                location = scraper.get_location()
            if hasattr(scraper, 'get_description_en'):
                description_en = scraper.get_description_en()
            if hasattr(scraper, 'get_specs'):
                specs = scraper.get_specs()
            if hasattr(scraper, 'get_mint_year'):
                mint_year = scraper.get_mint_year()
            if hasattr(scraper, 'get_mintage'):
                mintage = scraper.get_mintage()

            # 次の商品用にリセット
            if hasattr(scraper, 'reset_extra_fields'):
                scraper.reset_extra_fields()

            if scraped.error:
                logger.warning(f"  スクレイピング失敗: {url[:50]}... - {scraped.error}")
            else:
                logger.info(f"  取得成功: {scraped.product_name[:30]} - {scraped.currency} {scraped.price:,.2f}")
                if location:
                    logger.info(f"    製造国: {location}")
                if mint_year:
                    logger.info(f"    発行年: {mint_year}")

            results[url] = ScrapedDataWithExtras(
                scraped_data=scraped,
                location=location,
                description_en=description_en,
                specs=specs,
                mint_year=mint_year,
                mintage=mintage
            )

    return results


def is_formula(value) -> bool:
    """値が数式かどうかを判定"""
    return value and isinstance(value, str) and value.startswith("=")


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="カラーミー商品ダウンロード")
    parser.add_argument("--fetch-prices", action="store_true",
                        help="J列のURLから価格を自動取得してV列に反映")
    args = parser.parse_args()

    logger.info("=== カラーミー商品ダウンロード開始 ===")

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # カラーミーAPI確認
    if not Config.is_colorme_enabled():
        logger.error("カラーミーアクセストークンが設定されていません")
        sys.exit(1)

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    # カラーミークライアント初期化
    colorme = ColorMeClient()

    # 全商品を取得（1万件以上に対応）
    logger.info("カラーミーから全商品を取得中...")
    products = colorme.get_all_products()  # デフォルト: 20000件
    logger.info(f"取得した商品数: {len(products)}件")

    if not products:
        logger.info("商品がありません")
        return

    # 商品IDの昇順でソート
    products.sort(key=lambda p: p.get("id", 0))
    logger.info("商品をID昇順でソートしました")

    # シートに書き込み
    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
        last_col = Col.last_column_letter()

        # 既存データを取得（仕入れ先情報と数式を保持するため）
        # 値として取得（商品IDのマッピング用）
        existing = sheet.get_all_values()
        # 数式として取得（数式を保持するため）
        existing_formulas = sheet.get(f'A1:{last_col}{len(existing) + 1}', value_render_option='FORMULA')

        existing_data = {}  # 商品ID -> 既存行データのマッピング（数式含む）
        existing_row_map = {}  # 商品ID -> 行番号のマッピング（1-indexed、ヘッダー除く）
        max_existing_row = 1  # 既存データの最大行番号

        if len(existing) > 1:
            logger.info(f"既存データ行数: {len(existing)}行, 数式データ行数: {len(existing_formulas) if existing_formulas else 0}行")
            for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
                pid_val = get_cell(row, Col.PRODUCT_ID)
                if pid_val:
                    try:
                        pid = int(pid_val)
                        # 数式データと値データをマージ
                        if existing_formulas and row_idx < len(existing_formulas):
                            formula_row = list(existing_formulas[row_idx])
                            # 数式データが短い場合は値データで補完
                            while len(formula_row) < Col.TOTAL_COLUMNS:
                                if len(row) > len(formula_row):
                                    formula_row.append(row[len(formula_row)])
                                else:
                                    formula_row.append("")
                            existing_data[pid] = formula_row
                        else:
                            # 値データを使用
                            value_row = list(row)
                            while len(value_row) < Col.TOTAL_COLUMNS:
                                value_row.append("")
                            existing_data[pid] = value_row
                        existing_row_map[pid] = row_idx
                        max_existing_row = max(max_existing_row, row_idx)

                        # デバッグ: 最初の3件のみ確認
                        if len(existing_data) <= 3:
                            r = existing_data[pid]
                            logger.info(f"  既存データ 商品ID {pid}: J={get_cell(r, Col.SUPPLIER_URL)[:20] if get_cell(r, Col.SUPPLIER_URL) else ''}")
                    except ValueError:
                        pass
            logger.info(f"既存データを取得: {len(existing_data)}件（数式を保持）")

        # 既存データからY列（取引通貨）とZ列（為替種類）を収集
        currency_exchange_types = {}  # 通貨 -> 為替種類
        for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
            currency = get_cell(row, Col.CURRENCY).upper()
            exchange_type = get_cell(row, Col.EXCHANGE_TYPE) or "クレカ"
            if currency and currency != "JPY":
                currency_exchange_types[currency] = exchange_type

        # 為替レートを取得
        exchange_rates = {}
        if currency_exchange_types:
            exchange_rates = fetch_exchange_rates(
                list(currency_exchange_types.keys()),
                currency_exchange_types
            )
            logger.info(f"為替レート取得完了: {len(exchange_rates)}件")

        # 価格自動取得オプション: 既存データのJ列URLから価格を取得
        price_data = {}
        url_to_pid = {}  # URL -> 商品IDのマッピング
        if args.fetch_prices and existing_data:
            # J列（仕入れ先商品URL）を収集
            urls_to_fetch = []
            empty_url_count = 0
            formula_url_count = 0
            for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
                url = get_cell(row, Col.SUPPLIER_URL)
                if url:
                    if url.startswith("http"):
                        urls_to_fetch.append(url)
                        pid_val = get_cell(row, Col.PRODUCT_ID)
                        if pid_val:
                            try:
                                url_to_pid[url] = int(pid_val)
                            except ValueError:
                                pass
                    elif url.startswith("="):
                        formula_url_count += 1
                        logger.warning(f"  J列が数式のまま: 行{row_idx+1}, 値={url[:50]}")
                    else:
                        empty_url_count += 1
                else:
                    empty_url_count += 1
            logger.info(f"J列URL集計: URLあり={len(urls_to_fetch)}件, URLなし={empty_url_count}件, 数式のまま={formula_url_count}件")

            if urls_to_fetch:
                # 重複を除いて価格取得
                unique_urls = list(set(urls_to_fetch))
                logger.info(f"  URL収集詳細: 全{len(urls_to_fetch)}件中、ユニーク{len(unique_urls)}件")
                price_data = fetch_prices_from_urls(unique_urls)
                success_count = len([r for r in price_data.values() if not r.scraped_data.error])
                fail_count = len([r for r in price_data.values() if r.scraped_data.error])
                logger.info(f"価格取得完了: {success_count}件成功, {fail_count}件失敗")

                # スクレイピング結果から新しい通貨を収集
                new_currencies = set()
                for scraped_with_extras in price_data.values():
                    scraped = scraped_with_extras.scraped_data
                    if not scraped.error and scraped.currency:
                        currency = scraped.currency.upper()
                        if currency != "JPY" and currency not in currency_exchange_types:
                            new_currencies.add(currency)

                if new_currencies:
                    logger.info(f"スクレイピング結果から新規通貨を検出: {new_currencies}")
                    new_currency_types = {c: "クレカ" for c in new_currencies}
                    new_rates = fetch_exchange_rates(list(new_currencies), new_currency_types)
                    exchange_rates.update(new_rates)
                    logger.info(f"新規通貨の為替レート取得完了: {len(new_rates)}件")

        # 商品データを行に変換
        update_rows = {}  # 行番号 -> 行データのマッピング（既存商品の更新用）
        new_rows = []  # 新規商品用
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        next_new_row = max_existing_row + 1

        for product in products:
            product_id = product.get("id", 0)

            # カテゴリー情報（大カテゴリーのみ使用、小カテゴリーは削除されました）
            category = product.get("category") or {}
            category_id_big = category.get("id_big", 0) if isinstance(category, dict) else 0

            # グループID（テキストとして扱うため先頭にシングルクォートを付ける）
            group_ids = product.get("group_ids") or []
            if isinstance(group_ids, list) and group_ids:
                group_ids_str = "'" + ",".join(str(g) for g in group_ids)
            elif group_ids:
                group_ids_str = "'" + str(group_ids)
            else:
                group_ids_str = ""

            # 画像URL
            image_url = product.get("image_url", "") or ""
            images = product.get("images") or []
            image_urls = [image_url] if image_url else []
            for img in images:
                if isinstance(img, dict):
                    url = img.get("src") or img.get("url") or ""
                    if url and url not in image_urls:
                        image_urls.append(url)
                elif isinstance(img, str) and img and img not in image_urls:
                    image_urls.append(img)

            # 表示状態（APIの値を日本語に変換）
            display_state_api = product.get("display_state", "showing")
            display_state_map = {
                "showing": "掲載する",
                "hidden": "掲載しない",
                "showing_for_members": "会員のみ表示",
                "sale_for_members": "会員のみ購入可",
            }
            display_state = display_state_map.get(display_state_api, display_state_api)

            # 77列分のデータを作成（A-BY列）
            row = [""] * Col.TOTAL_COLUMNS

            # 既存データがあれば仕入れ先情報を保持
            existing_row = existing_data.get(product_id, [])
            is_existing = product_id in existing_row_map

            # 行番号の計算（数式調整用）
            if is_existing:
                old_row_num = existing_row_map[product_id] + 1  # 1-indexed（ヘッダー含む）
                new_row_num = old_row_num
            else:
                old_row_num = 0
                new_row_num = next_new_row + len(new_rows) + 1

            # === A-F列: 操作項目 ===
            row[Col.SYNC_MODE.index] = preserve_or_set(existing_row, Col.SYNC_MODE, "変更なし", old_row_num, new_row_num)
            row[Col.DISPLAY_SETTING.index] = preserve_or_set(existing_row, Col.DISPLAY_SETTING, display_state, old_row_num, new_row_num)
            row[Col.PRICE_UPDATE.index] = preserve_or_set(existing_row, Col.PRICE_UPDATE, "OFF", old_row_num, new_row_num)
            row[Col.STOCK_SYNC.index] = preserve_or_set(existing_row, Col.STOCK_SYNC, "OFF", old_row_num, new_row_num)
            row[Col.DISPLAY_SYNC.index] = preserve_or_set(existing_row, Col.DISPLAY_SYNC, "OFF", old_row_num, new_row_num)
            row[Col.SYNC_STATUS.index] = preserve_or_set(existing_row, Col.SYNC_STATUS, "ダウンロード済", old_row_num, new_row_num, preserve_existing=False)

            # === G-I列: 識別情報 ===
            row[Col.PRODUCT_ID.index] = str(product_id)
            row[Col.NAME.index] = product.get("name", "")
            row[Col.COLORME_URL.index] = f"https://ybx.jp/?pid={product_id}"

            # === J-L列: 仕入れ先基本情報 ===
            row[Col.SUPPLIER_URL.index] = preserve_or_set(existing_row, Col.SUPPLIER_URL, "", old_row_num, new_row_num)
            row[Col.SUPPLIER_NAME.index] = preserve_or_set(existing_row, Col.SUPPLIER_NAME, Formula.supplier_name(new_row_num), old_row_num, new_row_num)
            row[Col.SUPPLIER_SITE.index] = preserve_or_set(existing_row, Col.SUPPLIER_SITE, Formula.supplier_site(new_row_num), old_row_num, new_row_num)

            # === M-Q列: 仕入れ先価格情報 ===
            row[Col.SUPPLIER_STOCK.index] = preserve_or_set(existing_row, Col.SUPPLIER_STOCK, "", old_row_num, new_row_num)
            row[Col.SUPPLIER_PRICE.index] = preserve_or_set(existing_row, Col.SUPPLIER_PRICE, "", old_row_num, new_row_num)
            row[Col.PREV_PRICE.index] = preserve_or_set(existing_row, Col.PREV_PRICE, "", old_row_num, new_row_num)
            row[Col.PRICE_CHANGE_RATE.index] = preserve_or_set(existing_row, Col.PRICE_CHANGE_RATE, "", old_row_num, new_row_num)
            row[Col.CURRENCY.index] = preserve_or_set(existing_row, Col.CURRENCY, "", old_row_num, new_row_num)

            # スクレイピング結果があり、かつ数式でない場合のみ更新
            supplier_url = ""
            if is_existing:
                row_idx = existing_row_map[product_id]
                if row_idx < len(existing):
                    supplier_url = get_cell(existing[row_idx], Col.SUPPLIER_URL)
            else:
                supplier_url_raw = row[Col.SUPPLIER_URL.index]
                supplier_url = str(supplier_url_raw).strip() if supplier_url_raw and not is_formula(supplier_url_raw) else ""

            if supplier_url and supplier_url.startswith("http") and supplier_url in price_data:
                scraped_with_extras = price_data[supplier_url]
                scraped = scraped_with_extras.scraped_data
                if not scraped.error:
                    # ※P-T列（製造国〜発行数）は削除されたため、スクレイピング結果は使用しない
                    # 仕入れ先詳細情報は「商品仕入れ先一覧」シートで管理

                    # M列: 仕入れ先在庫状況
                    if not is_formula(row[Col.SUPPLIER_STOCK.index]):
                        row[Col.SUPPLIER_STOCK.index] = "In Stock" if scraped.in_stock else "Out of Stock"

                    # N列: 仕入れ先価格
                    if not is_formula(row[Col.SUPPLIER_PRICE.index]):
                        # 前回価格をO列に保存
                        if not is_formula(row[Col.PREV_PRICE.index]):
                            prev_val = get_cell(existing_row, Col.SUPPLIER_PRICE)
                            row[Col.PREV_PRICE.index] = prev_val
                        row[Col.SUPPLIER_PRICE.index] = str(scraped.price)

                    # Q列: 通貨
                    old_currency = row[Col.CURRENCY.index]
                    row[Col.CURRENCY.index] = scraped.currency
                    if old_currency != scraped.currency:
                        if is_formula(old_currency):
                            logger.info(f"  商品ID {product_id}: 通貨更新（数式を値に置換） -> {scraped.currency}")
                        else:
                            logger.info(f"  商品ID {product_id}: 通貨更新 {old_currency} -> {scraped.currency}")

                    # P列: 価格変動率
                    if not is_formula(row[Col.PRICE_CHANGE_RATE.index]):
                        prev_price_str = get_cell(existing_row, Col.SUPPLIER_PRICE)
                        if prev_price_str and not is_formula(prev_price_str):
                            try:
                                prev_price = float(prev_price_str)
                                if prev_price > 0:
                                    change_rate = ((scraped.price - prev_price) / prev_price) * 100
                                    row[Col.PRICE_CHANGE_RATE.index] = f"{change_rate:+.2f}%"
                            except ValueError:
                                pass

            # === R-AD列: 価格計算 ===
            for col in [Col.EXCHANGE_TYPE, Col.EXCHANGE_RATE, Col.PURCHASE_PRICE_JPY,
                        Col.QUANTITY, Col.PURCHASE_TOTAL, Col.MARGIN_RATE, Col.MARGIN_AMOUNT,
                        Col.SHIPPING, Col.FEE, Col.TOTAL_COST, Col.PROPER_PRICE,
                        Col.GROSS_PROFIT, Col.GROSS_PROFIT_RATE]:
                row[col.index] = preserve_or_set(existing_row, col, "", old_row_num, new_row_num)

            # S列（為替レート）を自動更新
            if not is_formula(row[Col.EXCHANGE_RATE.index]):
                currency_val = row[Col.CURRENCY.index]
                if is_formula(currency_val):
                    row_idx = existing_row_map.get(product_id)
                    if row_idx and row_idx < len(existing):
                        currency_val = get_cell(existing[row_idx], Col.CURRENCY)
                currency = currency_val.strip().upper() if currency_val else ""

                exchange_type_val = row[Col.EXCHANGE_TYPE.index]
                if is_formula(exchange_type_val):
                    row_idx = existing_row_map.get(product_id)
                    if row_idx and row_idx < len(existing):
                        exchange_type_val = get_cell(existing[row_idx], Col.EXCHANGE_TYPE)
                exchange_type = exchange_type_val.strip() if exchange_type_val else "クレカ"

                if currency == "JPY":
                    row[Col.EXCHANGE_RATE.index] = "1"
                elif currency:
                    rate_key = f"{currency}_{exchange_type}"
                    if rate_key in exchange_rates:
                        row[Col.EXCHANGE_RATE.index] = str(round(exchange_rates[rate_key], 4))
                        logger.info(f"  商品ID {product_id}: 為替レート更新 {rate_key} = {row[Col.EXCHANGE_RATE.index]}")

            # === AE-AJ列: カラーミー価格情報 ===
            row[Col.SALES_PRICE.index] = preserve_or_set(existing_row, Col.SALES_PRICE, str(product.get("sales_price") or product.get("price") or 0), old_row_num, new_row_num, preserve_existing=False)
            row[Col.REGULAR_PRICE.index] = preserve_or_set(existing_row, Col.REGULAR_PRICE, str(product.get("price") or 0), old_row_num, new_row_num, preserve_existing=False)
            row[Col.MEMBERS_PRICE.index] = preserve_or_set(existing_row, Col.MEMBERS_PRICE, str(product.get("members_price") or 0), old_row_num, new_row_num, preserve_existing=False)
            row[Col.COST.index] = preserve_or_set(existing_row, Col.COST, str(product.get("cost") or 0), old_row_num, new_row_num, preserve_existing=False)
            row[Col.TAX_INCLUDED_PRICE.index] = preserve_or_set(existing_row, Col.TAX_INCLUDED_PRICE, "", old_row_num, new_row_num)
            row[Col.TAX_AMOUNT.index] = preserve_or_set(existing_row, Col.TAX_AMOUNT, "", old_row_num, new_row_num)

            # === AK-AN列: カテゴリー・グループ ===
            # ※小カテゴリーID・小カテゴリー名は削除されました
            row[Col.CATEGORY_ID_BIG.index] = preserve_or_set(existing_row, Col.CATEGORY_ID_BIG, str(category_id_big) if category_id_big else "", old_row_num, new_row_num)
            row[Col.CATEGORY_NAME_BIG.index] = preserve_or_set(existing_row, Col.CATEGORY_NAME_BIG, "", old_row_num, new_row_num)
            row[Col.GROUP_IDS.index] = preserve_or_set(existing_row, Col.GROUP_IDS, group_ids_str, old_row_num, new_row_num)
            row[Col.GROUP_NAMES.index] = preserve_or_set(existing_row, Col.GROUP_NAMES, "", old_row_num, new_row_num)

            # === AO列: 型番 ===
            row[Col.MODEL_NUMBER.index] = preserve_or_set(existing_row, Col.MODEL_NUMBER, product.get("model_number", "") or "", old_row_num, new_row_num)

            # === AP-AV列: 在庫管理 ===
            row[Col.STOCKS.index] = preserve_or_set(existing_row, Col.STOCKS, str(product.get("stocks") or 0), old_row_num, new_row_num, preserve_existing=False)
            row[Col.STOCK_MANAGED.index] = preserve_or_set(existing_row, Col.STOCK_MANAGED, "する" if product.get("stock_managed", True) else "しない", old_row_num, new_row_num, preserve_existing=False)
            row[Col.FEW_NUM.index] = preserve_or_set(existing_row, Col.FEW_NUM, str(product.get("few_num") or 0), old_row_num, new_row_num, preserve_existing=False)
            soldout_display = product.get("soldout_display", True)
            row[Col.SOLDOUT_DISPLAY.index] = preserve_or_set(existing_row, Col.SOLDOUT_DISPLAY, "表示" if soldout_display else "非表示", old_row_num, new_row_num, preserve_existing=False)
            row[Col.MIN_NUM.index] = preserve_or_set(existing_row, Col.MIN_NUM, str(product.get("min_num") or 1), old_row_num, new_row_num, preserve_existing=False)
            row[Col.MAX_NUM.index] = preserve_or_set(existing_row, Col.MAX_NUM, str(product.get("max_num") or 0), old_row_num, new_row_num, preserve_existing=False)
            row[Col.UNIT.index] = preserve_or_set(existing_row, Col.UNIT, product.get("unit", "") or "", old_row_num, new_row_num, preserve_existing=False)

            # === AW-AZ列: 送料・配送 ===
            row[Col.DELIVERY_CHARGE.index] = preserve_or_set(existing_row, Col.DELIVERY_CHARGE, str(product.get("delivery_charge") or 0), old_row_num, new_row_num)
            row[Col.COOL_CHARGE.index] = preserve_or_set(existing_row, Col.COOL_CHARGE, "", old_row_num, new_row_num)
            row[Col.WEIGHT.index] = preserve_or_set(existing_row, Col.WEIGHT, "", old_row_num, new_row_num)
            row[Col.NO_DELIVERY.index] = preserve_or_set(existing_row, Col.NO_DELIVERY, "", old_row_num, new_row_num)

            # === BA-BD列: 商品説明 ===
            row[Col.EXPL.index] = preserve_or_set(existing_row, Col.EXPL, product.get("expl", "") or "", old_row_num, new_row_num)
            row[Col.SIMPLE_EXPL.index] = preserve_or_set(existing_row, Col.SIMPLE_EXPL, product.get("simple_expl", "") or "", old_row_num, new_row_num)
            row[Col.MOBILE_EXPL.index] = preserve_or_set(existing_row, Col.MOBILE_EXPL, "", old_row_num, new_row_num)
            row[Col.MEMO.index] = preserve_or_set(existing_row, Col.MEMO, "", old_row_num, new_row_num)

            # === BE-BN列: 画像 ===
            image_cols = [Col.MAIN_IMAGE, Col.THUMBNAIL, Col.IMAGE_URL_1, Col.IMAGE_URL_2,
                          Col.IMAGE_URL_3, Col.IMAGE_URL_4, Col.IMAGE_URL_5, Col.IMAGE_URL_6,
                          Col.IMAGE_URL_7, Col.IMAGE_URL_8]
            for i, col in enumerate(image_cols):
                img_url = image_urls[i] if i < len(image_urls) else ""
                row[col.index] = preserve_or_set(existing_row, col, img_url, old_row_num, new_row_num)

            # === BO-BQ列: SEO ===
            row[Col.PAGE_TITLE.index] = preserve_or_set(existing_row, Col.PAGE_TITLE, "", old_row_num, new_row_num)
            row[Col.META_DESC.index] = preserve_or_set(existing_row, Col.META_DESC, "", old_row_num, new_row_num)
            row[Col.META_KEYWORDS.index] = preserve_or_set(existing_row, Col.META_KEYWORDS, "", old_row_num, new_row_num)

            # === BR-BV列: フラグ ===
            row[Col.REDUCED_TAX.index] = preserve_or_set(existing_row, Col.REDUCED_TAX, "", old_row_num, new_row_num)
            row[Col.DIGITAL_CONTENT.index] = preserve_or_set(existing_row, Col.DIGITAL_CONTENT, "", old_row_num, new_row_num)
            row[Col.SUBSCRIPTION.index] = preserve_or_set(existing_row, Col.SUBSCRIPTION, "", old_row_num, new_row_num)
            row[Col.DISPLAY_ORDER.index] = preserve_or_set(existing_row, Col.DISPLAY_ORDER, "", old_row_num, new_row_num)
            row[Col.DISABLED_PAYMENTS.index] = preserve_or_set(existing_row, Col.DISABLED_PAYMENTS, "", old_row_num, new_row_num)

            # === BW-BX列: 掲載期間 ===
            row[Col.START_DATE.index] = preserve_or_set(existing_row, Col.START_DATE, "", old_row_num, new_row_num)
            row[Col.END_DATE.index] = preserve_or_set(existing_row, Col.END_DATE, "", old_row_num, new_row_num)

            # === BY列: システム情報 ===
            # ※商品作成日時・商品更新日時は削除されました
            row[Col.SYNC_DATETIME.index] = preserve_or_set(existing_row, Col.SYNC_DATETIME, now, old_row_num, new_row_num, preserve_existing=False)

            # 既存商品か新規商品かで振り分け
            if is_existing:
                update_rows[existing_row_map[product_id]] = row
            else:
                new_rows.append(row)

        # バッチ書き込み
        updated_count = 0
        added_count = 0

        # 既存行の更新
        if update_rows:
            logger.info(f"既存商品を更新中: {len(update_rows)}件")
            batch_data = []
            for row_num, row_data in update_rows.items():
                sheet_row = row_num + 1  # 1-indexed（ヘッダー含む）
                batch_data.append({
                    'range': f'A{sheet_row}:{last_col}{sheet_row}',
                    'values': [row_data]
                })
            sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
            updated_count = len(update_rows)
            logger.info(f"既存商品の更新完了: {updated_count}件")

        # 新規行の追加
        if new_rows:
            start_row = max_existing_row + 2  # ヘッダー含む
            end_row = start_row + len(new_rows) - 1
            logger.info(f"新規商品を追加中: {len(new_rows)}件 (行{start_row}〜{end_row})")
            sheet.update(values=new_rows, range_name=f'A{start_row}:{last_col}{end_row}', value_input_option='USER_ENTERED')
            added_count = len(new_rows)
            logger.info(f"新規商品の追加完了: {added_count}件")

        logger.info(f"シート更新完了: 更新{updated_count}件, 追加{added_count}件")

    except Exception as e:
        logger.error(f"シート書き込みエラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    logger.info("=== カラーミー商品ダウンロード完了 ===")


if __name__ == "__main__":
    main()
