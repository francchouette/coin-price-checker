"""
カラーミー商品ダウンロードスクリプト

カラーミーAPIから全商品を取得し、新カラーミー商品管理シートに書き込む。
J列（仕入れ先商品URL）がある場合は、価格を自動取得してM-Q列に反映する。

処理フロー:
  1. カラーミーAPIから全商品ダウンロード
  2. 既存シートデータ（数式含む）を読み込み
  3. 為替レートを取得
  4. 商品ループ:
     a. --fetch-prices時: 仕入れ先URLからスクレイピング
     b. 全77列の行データを構築（API + スクレイピング + 既存データ）
     c. 10件ごとにシートに書き込み
"""

import argparse
import copy
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from .spreadsheet import SpreadsheetClient
from .colorme import ColorMeClient
from .config import Config
from .scraper import ScraperManager, detect_shop_from_url
from .shops import ScrapedData
from .exchange_rate import ExchangeRateClient, WiseRateClient
from .cm_sheet_columns import Col, get_cell, get_cell_int, preserve_or_set, Formula
from .sync_colorme_products import row_to_update_data
from .restore_formulas import SAFE_FORMULA_COLS, adjust_formula_row as restore_adjust_formula

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


def is_formula(value) -> bool:
    """値が数式かどうかを判定"""
    return value and isinstance(value, str) and value.startswith("=")


def _idx_to_letter(index: int) -> str:
    """0-based index を列文字に変換 (0->A, 25->Z, 26->AA)"""
    result = ""
    index += 1
    while index > 0:
        index -= 1
        result = chr(ord('A') + index % 26) + result
        index //= 26
    return result


def build_row_segments(row_data: list, raw_formula_row: list, sheet_row: int) -> list:
    """
    既存行の書き込み用: 数式セルをスキップしてセグメント単位のbatch_update用データを構築する。

    raw_formula_row に数式（=で始まる）があり、row_data が数式を保持できていない場合、
    そのセルをスキップすることでシート上の数式を保護する。
    """
    if not raw_formula_row:
        # 数式データなし → 全列書き込み
        last_letter = _idx_to_letter(len(row_data) - 1)
        return [{
            'range': f'A{sheet_row}:{last_letter}{sheet_row}',
            'values': [list(row_data)]
        }]

    segments = []
    seg_start = None
    seg_values = []

    for i in range(len(row_data)):
        # 既存セルに数式があるか
        existing_has_formula = (
            i < len(raw_formula_row) and
            isinstance(raw_formula_row[i], str) and
            raw_formula_row[i].startswith("=")
        )
        # 新しいデータが数式を保持しているか
        new_has_formula = isinstance(row_data[i], str) and row_data[i].startswith("=")

        # 既存に数式があるのに新データが数式でない → スキップ（数式を保護）
        skip = existing_has_formula and not new_has_formula

        if skip:
            # 現在のセグメントを閉じる
            if seg_values:
                start_letter = _idx_to_letter(seg_start)
                end_letter = _idx_to_letter(seg_start + len(seg_values) - 1)
                segments.append({
                    'range': f'{start_letter}{sheet_row}:{end_letter}{sheet_row}',
                    'values': [seg_values]
                })
                seg_start = None
                seg_values = []
        else:
            if seg_start is None:
                seg_start = i
            seg_values.append(row_data[i])

    # 最後のセグメント
    if seg_values:
        start_letter = _idx_to_letter(seg_start)
        end_letter = _idx_to_letter(seg_start + len(seg_values) - 1)
        segments.append({
            'range': f'{start_letter}{sheet_row}:{end_letter}{sheet_row}',
            'values': [seg_values]
        })

    return segments


def scrape_url(scraper_manager, url: str) -> ScrapedDataWithExtras:
    """1つのURLをスクレイピングする"""
    shop_name = detect_shop_from_url(url)
    scraper = scraper_manager.get_scraper(shop_name)

    if not scraper:
        return ScrapedDataWithExtras(
            scraped_data=ScrapedData(
                product_name="",
                price=0.0,
                currency="",
                url=url,
                in_stock=False,
                error=f"未対応のショップ: {shop_name}"
            )
        )

    scraped = scraper.scrape(url)

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
    if hasattr(scraper, 'reset_extra_fields'):
        scraper.reset_extra_fields()

    return ScrapedDataWithExtras(
        scraped_data=scraped,
        location=location,
        description_en=description_en,
        specs=specs,
        mint_year=mint_year,
        mintage=mintage
    )


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="カラーミー商品ダウンロード")
    parser.add_argument("--fetch-prices", action="store_true",
                        help="J列のURLから価格を自動取得してM-Q列に反映")
    parser.add_argument("--sync", action="store_true",
                        help="ダウンロード後にカラーミーAPIへ即時同期（1行ずつ）")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    parser.add_argument("--limit", type=int, default=0, help="処理件数制限（0=無制限）")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode_label = "カラーミー商品ダウンロード+同期" if args.sync else "カラーミー商品ダウンロード"
    logger.info(f"=== {mode_label}開始 ===")

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

    if args.limit > 0:
        products = products[:args.limit]
        logger.info(f"処理件数を{args.limit}件に制限")

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

        # 既存データからQ列（通貨）とR列（為替種類）を収集
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

        # --sync: 数式テンプレートを収集（数式復元用）
        formula_templates = {}
        if args.sync:
            if existing_formulas:
                for col_idx in SAFE_FORMULA_COLS:
                    for row_idx in range(1, len(existing_formulas)):
                        frow = existing_formulas[row_idx]
                        if col_idx < len(frow):
                            val = str(frow[col_idx]) if frow[col_idx] is not None else ""
                            if val.startswith("="):
                                formula_templates[col_idx] = (val, row_idx + 1)
                                break
            logger.info(f"数式テンプレート: {len(formula_templates)}列分を収集")

        # ========================================
        # バッチ書き込みヘルパー
        # ========================================
        BATCH_SIZE = 10
        MAX_RETRIES = 3

        def batch_update_with_retry(batch_data: list, description: str):
            """リトライ付きバッチ書き込み（gspreadがrangeを書き換えるためコピーして実行）"""
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    data_copy = copy.deepcopy(batch_data)
                    sheet.batch_update(data_copy, value_input_option='USER_ENTERED')
                    return True
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        wait = 10 * attempt
                        logger.warning(f"  {description}: エラー (試行{attempt}/{MAX_RETRIES}), {wait}秒後にリトライ: {e}")
                        time.sleep(wait)
                    else:
                        logger.error(f"  {description}: {MAX_RETRIES}回失敗: {e}")
                        raise

        def sheet_update_with_retry(values: list, range_name: str, description: str):
            """リトライ付きシート書き込み"""
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    sheet.update(values=values, range_name=range_name, value_input_option='USER_ENTERED')
                    return True
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        wait = 10 * attempt
                        logger.warning(f"  {description}: エラー (試行{attempt}/{MAX_RETRIES}), {wait}秒後にリトライ: {e}")
                        time.sleep(wait)
                    else:
                        logger.error(f"  {description}: {MAX_RETRIES}回失敗: {e}")
                        raise

        def flush_batch(update_items, new_items, updated_count, added_count, next_new_row):
            """溜まったバッチをシートに書き込む（既存行は数式セルをスキップ）"""
            if update_items:
                batch_data = []
                for row_num, row_data in update_items:
                    sheet_row = row_num + 1  # 1-indexed（ヘッダー含む）
                    # 既存行の生データ（数式含む）を取得して数式セルをスキップ
                    raw_formula = None
                    if existing_formulas and row_num < len(existing_formulas):
                        raw_formula = existing_formulas[row_num]
                    segments = build_row_segments(row_data, raw_formula, sheet_row)
                    batch_data.extend(segments)
                desc = f"既存更新 {updated_count+1}〜{updated_count+len(update_items)}"
                batch_update_with_retry(batch_data, desc)
                updated_count += len(update_items)
                logger.info(f"  [シート書き込み] 既存更新: {updated_count}件完了 ({len(batch_data)}セグメント)")
                time.sleep(1)

            if new_items:
                start_row = next_new_row + 1  # ヘッダー含む（1-indexed）
                end_row = start_row + len(new_items) - 1
                desc = f"新規追加 {added_count+1}〜{added_count+len(new_items)}"
                sheet_update_with_retry(new_items, f'A{start_row}:{last_col}{end_row}', desc)
                added_count += len(new_items)
                next_new_row += len(new_items)
                logger.info(f"  [シート書き込み] 新規追加: {added_count}件完了")
                time.sleep(1)

            return updated_count, added_count, next_new_row

        # ========================================
        # 商品ループ: スクレイピング → 全77列構築 → 10件ごとにシート書き込み
        # ========================================
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        next_new_row = max_existing_row + 1
        updated_count = 0
        added_count = 0
        scrape_success = 0
        scrape_fail = 0
        sync_success = 0
        sync_fail = 0
        sync_skip = 0
        update_batch = []  # [(row_num, row_data), ...]
        new_batch = []  # [row_data, ...]
        price_cache = {}  # URL -> ScrapedDataWithExtras（重複スクレイピング防止）
        scraper_manager = None

        try:
            # スクレイピング準備（--fetch-prices時のみ）
            if args.fetch_prices:
                urls_count = sum(
                    1 for row in existing[1:]
                    if get_cell(row, Col.SUPPLIER_URL).startswith("http")
                )
                logger.info(f"スクレイピング対象URL: {urls_count}件（商品ループ中に順次取得）")
                scraper_manager = ScraperManager()
                scraper_manager.__enter__()

            for idx, product in enumerate(products):
                product_id = product.get("id", 0)
                existing_row = existing_data.get(product_id, [])
                is_existing = product_id in existing_row_map

                if args.sync:
                    product_name_log = product.get("name", "")[:30] or f"ID:{product_id}"
                    logger.info(f"[{idx+1}/{len(products)}] {product_name_log} (ID: {product_id})")

                # --- スクレイピング（仕入れ先URLがある既存商品のみ）---
                scraped_result = None
                if scraper_manager and is_existing:
                    row_idx = existing_row_map[product_id]
                    supplier_url_for_scrape = ""
                    if row_idx < len(existing):
                        supplier_url_for_scrape = get_cell(existing[row_idx], Col.SUPPLIER_URL)

                    if supplier_url_for_scrape and supplier_url_for_scrape.startswith("http"):
                        # キャッシュチェック（同一URL重複防止）
                        if supplier_url_for_scrape in price_cache:
                            scraped_result = price_cache[supplier_url_for_scrape]
                        else:
                            scraped_result = scrape_url(scraper_manager, supplier_url_for_scrape)
                            price_cache[supplier_url_for_scrape] = scraped_result

                            if scraped_result.scraped_data.error:
                                scrape_fail += 1
                                logger.warning(f"  スクレイピング失敗: {supplier_url_for_scrape[:50]}... - {scraped_result.scraped_data.error}")
                            else:
                                scrape_success += 1
                                s = scraped_result.scraped_data
                                logger.info(f"  取得成功: {s.product_name[:30]} - {s.currency} {s.price:,.2f}")

                                # 新規通貨の為替レート取得
                                if s.currency:
                                    cur = s.currency.upper()
                                    if cur != "JPY" and cur not in currency_exchange_types:
                                        currency_exchange_types[cur] = "クレカ"
                                        new_rates = fetch_exchange_rates([cur], {cur: "クレカ"})
                                        exchange_rates.update(new_rates)

                # --- 全77列の行データを構築 ---
                # カテゴリー情報（大カテゴリーのみ）
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

                # 行番号の計算（数式調整用）
                if is_existing:
                    old_row_num = existing_row_map[product_id] + 1  # 1-indexed（ヘッダー含む）
                    new_row_num = old_row_num
                else:
                    old_row_num = 0
                    new_row_num = next_new_row + len(new_batch) + 1

                # === A-F列: 操作項目 ===
                row[Col.SYNC_MODE.index] = preserve_or_set(existing_row, Col.SYNC_MODE, "変更なし", old_row_num, new_row_num)
                row[Col.DISPLAY_SETTING.index] = preserve_or_set(existing_row, Col.DISPLAY_SETTING, display_state, old_row_num, new_row_num)
                row[Col.PRICE_UPDATE.index] = preserve_or_set(existing_row, Col.PRICE_UPDATE, "ON", old_row_num, new_row_num)
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

                # === M-Q列: 仕入れ先価格情報（既存値をベースに設定）===
                row[Col.SUPPLIER_STOCK.index] = preserve_or_set(existing_row, Col.SUPPLIER_STOCK, "", old_row_num, new_row_num)
                row[Col.SUPPLIER_PRICE.index] = preserve_or_set(existing_row, Col.SUPPLIER_PRICE, "", old_row_num, new_row_num)
                row[Col.PREV_PRICE.index] = preserve_or_set(existing_row, Col.PREV_PRICE, "", old_row_num, new_row_num)
                row[Col.PRICE_CHANGE_RATE.index] = preserve_or_set(existing_row, Col.PRICE_CHANGE_RATE, "", old_row_num, new_row_num)
                row[Col.CURRENCY.index] = preserve_or_set(existing_row, Col.CURRENCY, "", old_row_num, new_row_num)

                # スクレイピング結果があれば適用（M-Q列を上書き）
                if scraped_result and not scraped_result.scraped_data.error:
                    scraped = scraped_result.scraped_data

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
                        r_idx = existing_row_map.get(product_id)
                        if r_idx and r_idx < len(existing):
                            currency_val = get_cell(existing[r_idx], Col.CURRENCY)
                    currency = currency_val.strip().upper() if currency_val else ""

                    exchange_type_val = row[Col.EXCHANGE_TYPE.index]
                    if is_formula(exchange_type_val):
                        r_idx = existing_row_map.get(product_id)
                        if r_idx and r_idx < len(existing):
                            exchange_type_val = get_cell(existing[r_idx], Col.EXCHANGE_TYPE)
                    exchange_type = exchange_type_val.strip() if exchange_type_val else "クレカ"

                    if currency == "JPY":
                        row[Col.EXCHANGE_RATE.index] = "1"
                    elif currency:
                        rate_key = f"{currency}_{exchange_type}"
                        if rate_key in exchange_rates:
                            row[Col.EXCHANGE_RATE.index] = str(round(exchange_rates[rate_key], 4))
                            logger.info(f"  商品ID {product_id}: 為替レート更新 {rate_key} = {row[Col.EXCHANGE_RATE.index]}")

                # === AE-AJ列: カラーミー価格情報 ===
                # AE列: 数式がある場合は保持（数式で計算した販売価格をカラーミーに同期するため）
                row[Col.SALES_PRICE.index] = preserve_or_set(existing_row, Col.SALES_PRICE, str(product.get("sales_price") or product.get("price") or 0), old_row_num, new_row_num, preserve_existing=True)
                row[Col.REGULAR_PRICE.index] = preserve_or_set(existing_row, Col.REGULAR_PRICE, str(product.get("price") or 0), old_row_num, new_row_num, preserve_existing=False)
                row[Col.MEMBERS_PRICE.index] = preserve_or_set(existing_row, Col.MEMBERS_PRICE, str(product.get("members_price") or 0), old_row_num, new_row_num, preserve_existing=False)
                row[Col.COST.index] = preserve_or_set(existing_row, Col.COST, str(product.get("cost") or 0), old_row_num, new_row_num, preserve_existing=False)
                row[Col.TAX_INCLUDED_PRICE.index] = preserve_or_set(existing_row, Col.TAX_INCLUDED_PRICE, "", old_row_num, new_row_num)
                row[Col.TAX_AMOUNT.index] = preserve_or_set(existing_row, Col.TAX_AMOUNT, "", old_row_num, new_row_num)

                # === AK-AN列: カテゴリー・グループ ===
                row[Col.CATEGORY_ID_BIG.index] = preserve_or_set(existing_row, Col.CATEGORY_ID_BIG, str(category_id_big) if category_id_big else "", old_row_num, new_row_num)
                row[Col.CATEGORY_NAME_BIG.index] = preserve_or_set(existing_row, Col.CATEGORY_NAME_BIG, "", old_row_num, new_row_num)
                # グループIDは常にAPIの値で上書き（preserve_existing=Falseで壊れた数値を修復）
                row[Col.GROUP_IDS.index] = preserve_or_set(existing_row, Col.GROUP_IDS, group_ids_str, old_row_num, new_row_num, preserve_existing=False)
                row[Col.GROUP_NAMES.index] = preserve_or_set(existing_row, Col.GROUP_NAMES, "", old_row_num, new_row_num)

                # === AO列: 型番 ===
                row[Col.MODEL_NUMBER.index] = preserve_or_set(existing_row, Col.MODEL_NUMBER, product.get("model_number", "") or "", old_row_num, new_row_num)

                # === AP-AV列: 在庫管理 ===
                # 在庫数: ユーザーがスプレッドシート上で変更した値を保持する（カラーミーの値で上書きしない）
                row[Col.STOCKS.index] = preserve_or_set(existing_row, Col.STOCKS, str(product.get("stocks") or 0), old_row_num, new_row_num, preserve_existing=True)
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
                row[Col.SYNC_DATETIME.index] = preserve_or_set(existing_row, Col.SYNC_DATETIME, now, old_row_num, new_row_num, preserve_existing=False)

                # バッチに追加
                if is_existing:
                    update_batch.append((existing_row_map[product_id], row))
                else:
                    new_batch.append(row)

                # ========================================
                # --sync: 1行ずつ即時書き込み → 数式復元 → カラーミー同期
                # ========================================
                if args.sync:
                    # シート行番号を特定
                    if is_existing:
                        sheet_row = existing_row_map[product_id] + 1  # 1-indexed（ヘッダー含む）
                    else:
                        sheet_row = next_new_row + 1  # flush前のnext_new_rowから算出

                    # 即座にシート書き込み
                    updated_count, added_count, next_new_row = flush_batch(
                        update_batch, new_batch, updated_count, added_count, next_new_row
                    )
                    update_batch = []
                    new_batch = []

                    # 数式復元（必要な列のみ）
                    restore_updates = []
                    for col_idx, (template, template_row) in formula_templates.items():
                        needs_restore = True
                        if is_existing:
                            old_row_idx = existing_row_map[product_id]
                            if existing_formulas and old_row_idx < len(existing_formulas):
                                efrow = existing_formulas[old_row_idx]
                                if col_idx < len(efrow):
                                    val = str(efrow[col_idx]) if efrow[col_idx] is not None else ""
                                    if val.startswith("="):
                                        needs_restore = False  # 既存数式はbuild_row_segmentsで保護済み
                        if needs_restore:
                            restored = restore_adjust_formula(template, template_row, sheet_row)
                            restore_updates.append({
                                'range': f'{_idx_to_letter(col_idx)}{sheet_row}',
                                'values': [[restored]]
                            })
                    if restore_updates:
                        batch_update_with_retry(restore_updates, f"数式復元(行{sheet_row})")
                        logger.debug(f"  数式復元: {len(restore_updates)}列")

                    # カラーミー同期対象かチェック
                    sync_mode = row[Col.SYNC_MODE.index]
                    if is_formula(sync_mode):
                        cell = sheet.get(f'{Col.SYNC_MODE.letter}{sheet_row}')
                        sync_mode = cell[0][0] if cell and cell[0] else ""

                    if sync_mode != "更新" or product_id <= 0:
                        sync_skip += 1
                        if sync_mode != "更新":
                            logger.debug(f"  CM同期スキップ: A列='{sync_mode}'")
                        else:
                            logger.debug(f"  CM同期スキップ: 商品IDなし")
                    else:
                        try:
                            # 数式で再計算されたAB〜AE列を読み戻す
                            price_range = f'{Col.PROPER_PRICE.letter}{sheet_row}:{Col.SALES_PRICE.letter}{sheet_row}'
                            price_cells = sheet.get(price_range)

                            updated_row = list(row)
                            while len(updated_row) <= Col.SALES_PRICE.index:
                                updated_row.append("")

                            if price_cells and price_cells[0]:
                                read_cells = price_cells[0]
                                start_idx = Col.PROPER_PRICE.index
                                for j, val in enumerate(read_cells):
                                    cell_idx = start_idx + j
                                    if cell_idx < len(updated_row):
                                        updated_row[cell_idx] = str(val) if val else ""

                            # 更新データを構築（フルスペック）
                            data = row_to_update_data(updated_row, price_only=False)

                            if not data or not data.get("updates"):
                                sync_skip += 1
                                logger.debug(f"  CM同期スキップ: 更新項目なし")
                            else:
                                log_parts = data.get("log_parts", [])
                                if log_parts:
                                    logger.info(f"  CM: {', '.join(log_parts)}")

                                update_keys = list(data["updates"].keys())
                                logger.info(f"  更新項目: {', '.join(update_keys)}")

                                if colorme.update_product(product_id, data["updates"]):
                                    sync_success += 1
                                    logger.info(f"  → カラーミー同期成功")

                                    now_sync = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    sync_status_updates = [
                                        {'range': f'{Col.SYNC_STATUS.letter}{sheet_row}', 'values': [["同期済み"]]},
                                        {'range': f'{Col.SYNC_DATETIME.letter}{sheet_row}', 'values': [[now_sync]]},
                                    ]
                                    batch_update_with_retry(sync_status_updates, f"同期ステータス(行{sheet_row})")
                                else:
                                    sync_fail += 1
                                    logger.error(f"  → カラーミー同期失敗")

                        except Exception as e:
                            sync_fail += 1
                            logger.error(f"  CM同期エラー: {e}")

                    # API制限対策
                    time.sleep(0.3)

                else:
                    # --syncなし: 従来のバッチ書き込み
                    if len(update_batch) + len(new_batch) >= BATCH_SIZE:
                        updated_count, added_count, next_new_row = flush_batch(
                            update_batch, new_batch, updated_count, added_count, next_new_row
                        )
                        update_batch = []
                        new_batch = []

            # 残りを書き込み
            if update_batch or new_batch:
                updated_count, added_count, next_new_row = flush_batch(
                    update_batch, new_batch, updated_count, added_count, next_new_row
                )

        finally:
            if scraper_manager:
                scraper_manager.__exit__(None, None, None)

        if args.fetch_prices:
            logger.info(f"スクレイピング結果: 成功{scrape_success}件, 失敗{scrape_fail}件")
        logger.info(f"シート更新完了: 更新{updated_count}件, 追加{added_count}件")
        if args.sync:
            logger.info(f"カラーミー同期成功: {sync_success}件")
            logger.info(f"カラーミー同期失敗: {sync_fail}件")
            logger.info(f"カラーミー同期スキップ: {sync_skip}件")

    except Exception as e:
        logger.error(f"シート書き込みエラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    logger.info(f"=== {mode_label}完了 ===")


if __name__ == "__main__":
    main()
