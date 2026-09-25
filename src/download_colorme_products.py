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
import os
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


# スクレイピング対象外にするドメイン（環境変数で指定・試験運用/ロールバック用）
#   例: CM_SYNC_SKIP_SCRAPE_DOMAINS="apmex.com"
# 未設定なら従来どおり全URLをスクレイピングする。
#
# apmex.com を除外する背景（2026-09-11 調査）:
#   cm-sync は Playwright で商品ページを開いて価格を読むが、APMEX の Bot 検知に
#   100%ブロックされている（毎回141件試行/0件成功、1件8.1秒待ち＝1回19分の無駄）。
#   APMEX の価格は専用ジョブ ap-scrape がカテゴリ一覧のXHR経由で取得しており
#   （成功率100%）、CMシートのN/M列はそこからのVLOOKUPなので鮮度に影響しない。
#   スクレイプ失敗時は書き込みブロックごとスキップされるため、除外しても
#   シートへの出力は従来と同一になる。
SKIP_SCRAPE_DOMAINS = tuple(
    d.strip().lower()
    for d in os.environ.get("CM_SYNC_SKIP_SCRAPE_DOMAINS", "").split(",")
    if d.strip()
)


# 同期項目（fields）→ 書き込み対象列インデックスのマップ
# API→シート時、sync_fields に含まれない項目の列は preserve_existing=True を強制
SYNC_FIELD_COLUMNS = {
    "price": {Col.SALES_PRICE.index, Col.REGULAR_PRICE.index, Col.MEMBERS_PRICE.index, Col.COST.index},
    "name": {Col.NAME.index},
    "description": {Col.EXPL.index, Col.SIMPLE_EXPL.index, Col.MOBILE_EXPL.index, Col.MEMO.index},
    "category": {Col.CATEGORY_ID_BIG.index, Col.GROUP_IDS.index},
    "model": {Col.MODEL_NUMBER.index},
    "stock": {Col.STOCKS.index},
    "display": {Col.DISPLAY_SETTING.index},
    "stock_settings": {Col.STOCK_MANAGED.index, Col.FEW_NUM.index, Col.SOLDOUT_DISPLAY.index,
                       Col.MIN_NUM.index, Col.MAX_NUM.index, Col.UNIT.index},
    "shipping": {Col.DELIVERY_CHARGE.index, Col.COOL_CHARGE.index, Col.WEIGHT.index, Col.NO_DELIVERY.index},
    "seo": {Col.PAGE_TITLE.index, Col.META_DESC.index, Col.META_KEYWORDS.index},
    "options": {Col.REDUCED_TAX.index, Col.DIGITAL_CONTENT.index, Col.SUBSCRIPTION.index},
    "images": {Col.MAIN_IMAGE.index, Col.THUMBNAIL.index, Col.IMAGE_URL_1.index, Col.IMAGE_URL_2.index,
               Col.IMAGE_URL_3.index, Col.IMAGE_URL_4.index, Col.IMAGE_URL_5.index,
               Col.IMAGE_URL_6.index, Col.IMAGE_URL_7.index, Col.IMAGE_URL_8.index},
}

# 同期項目フィルタを無視して常に更新する列（同期実行のブックキーピング）
ALWAYS_UPDATE_COLUMNS = {Col.SYNC_STATUS.index, Col.SYNC_DATETIME.index}


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


# USER_ENTERED で書くと Google Sheets に数値解釈されて壊れる列
# 例: AM列 "3190096,3190107" → 31900963190107（カンマを桁区切りと解釈）
#     → 再読込時に "31,900,963,190,107" となり、カンマ分割で無効なグループIDが
#       カラーミーへ送られ PUT が 404 になる
# これらの列は USER_ENTERED のバッチから除外し、RAW で別途書き込む
RAW_ONLY_COLS = {Col.GROUP_IDS.index}


def build_raw_segments(row_data: list, sheet_row: int) -> list:
    """RAW_ONLY_COLS のセルを RAW 書き込み用データに変換する"""
    out = []
    for i in sorted(RAW_ONLY_COLS):
        if i >= len(row_data):
            continue
        # USER_ENTERED 時代の名残でテキスト強制用の先頭 ' が付いている場合がある。
        # RAW ではリテラル文字として保存されてしまうため取り除く
        val = str(row_data[i]).lstrip("'").strip() if row_data[i] is not None else ""
        if val == "":
            continue
        out.append({
            'range': f'{_idx_to_letter(i)}{sheet_row}',
            'values': [[val]]
        })
    return out


def build_row_segments(row_data: list, raw_formula_row: list, sheet_row: int) -> list:
    """
    既存行の書き込み用: 数式セルをスキップしてセグメント単位のbatch_update用データを構築する。

    raw_formula_row に数式（=で始まる）があり、row_data が数式を保持できていない場合、
    そのセルをスキップすることでシート上の数式を保護する。
    """
    # 数式データが無くても RAW_ONLY_COLS は除外する必要があるため、
    # 全列一括書き込みのショートカットは使わずに必ずループを通す
    if not raw_formula_row:
        raw_formula_row = []

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
        # RAW_ONLY_COLS は USER_ENTERED で書くと壊れるため常にスキップ（後で RAW 書き込み）
        skip = (existing_has_formula and not new_has_formula) or i in RAW_ONLY_COLS

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
    parser.add_argument("--sync-fields", type=str, default="",
                        help="同期項目をカンマ区切りで指定（空=全項目）: price,name,description,category,model,stock,display,stock_settings,shipping,seo,options,images")
    parser.add_argument("--overwrite", action="store_true",
                        help="既存のシート値をAPIの値で上書きする（デフォルト: 空欄のみ書き込み）")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 同期項目フィルター
    sync_fields = None
    if args.sync_fields:
        sync_fields = set(f.strip() for f in args.sync_fields.split(",") if f.strip())
        logger.info(f"同期項目フィルター: {', '.join(sorted(sync_fields))}")

    overwrite_mode = args.overwrite

    mode_label = "カラーミー商品ダウンロード+同期" if args.sync else "カラーミー商品ダウンロード"
    logger.info(f"=== {mode_label}開始 ===")
    if overwrite_mode:
        logger.info("上書きモード: APIの値で既存シート値を上書きします")

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

        # BS/APMEX マスタシートから CM_ID → SEO のマップを構築（Colorme APIはSEOを返さないため）
        # register_adopted_products.py が BS/APMEX シートのBU/BV/BW列にSEOを保存している
        cm_id_to_seo = {}  # {cm_id: (page_title, meta_desc, meta_keywords)}
        try:
            from src.bs_sheet_columns import Col as BsCol
            import re as _re
            for _sheet_name in [Config.SHEET_BULLIONSTAR_PRODUCTS, Config.SHEET_APMEX_PRODUCTS]:
                try:
                    _ws = client._spreadsheet.worksheet(_sheet_name)
                    _rows = _ws.get_all_values()
                    for _r in _rows[1:]:
                        # E列: カラーミー商品URL（https://ybx.jp/?pid=<CM_ID>）
                        if len(_r) <= BsCol.COLORME_URL.index:
                            continue
                        _url = _r[BsCol.COLORME_URL.index]
                        _m = _re.search(r'pid=(\d+)', _url or '')
                        if not _m:
                            continue
                        _cm_id = int(_m.group(1))
                        _pt = _r[BsCol.CM_PAGE_TITLE.index] if len(_r) > BsCol.CM_PAGE_TITLE.index else ''
                        _md = _r[BsCol.CM_META_DESC.index] if len(_r) > BsCol.CM_META_DESC.index else ''
                        _mk = _r[BsCol.CM_META_KEYWORDS.index] if len(_r) > BsCol.CM_META_KEYWORDS.index else ''
                        if _pt or _md or _mk:
                            cm_id_to_seo[_cm_id] = (_pt, _md, _mk)
                except Exception:
                    pass
            logger.info(f"BS/APMEXマスタから SEO データ取得: {len(cm_id_to_seo)}件")
        except Exception as _e:
            logger.warning(f"BS/APMEXマスタからのSEO取得失敗（続行）: {_e}")

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

        def raw_update_with_retry(batch_data: list, description: str):
            """RAW でのバッチ書き込み（AM列などが数値解釈されるのを防ぐ）"""
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    data_copy = copy.deepcopy(batch_data)
                    sheet.batch_update(data_copy, value_input_option='RAW')
                    return True
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        wait = 10 * attempt
                        logger.warning(f"  {description}(RAW): エラー (試行{attempt}/{MAX_RETRIES}), {wait}秒後にリトライ: {e}")
                        time.sleep(wait)
                    else:
                        logger.error(f"  {description}(RAW): {MAX_RETRIES}回失敗: {e}")
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
                raw_data = []
                for row_num, row_data in update_items:
                    sheet_row = row_num + 1  # 1-indexed（ヘッダー含む）
                    # 既存行の生データ（数式含む）を取得して数式セルをスキップ
                    raw_formula = None
                    if existing_formulas and row_num < len(existing_formulas):
                        raw_formula = existing_formulas[row_num]
                    segments = build_row_segments(row_data, raw_formula, sheet_row)
                    batch_data.extend(segments)
                    raw_data.extend(build_raw_segments(row_data, sheet_row))
                desc = f"既存更新 {updated_count+1}〜{updated_count+len(update_items)}"
                batch_update_with_retry(batch_data, desc)
                if raw_data:
                    raw_update_with_retry(raw_data, desc)
                updated_count += len(update_items)
                logger.info(f"  [シート書き込み] 既存更新: {updated_count}件完了 ({len(batch_data)}セグメント)")
                time.sleep(1)

            if new_items:
                start_row = next_new_row + 1  # ヘッダー含む（1-indexed）
                end_row = start_row + len(new_items) - 1
                # シートの行数が足りない場合、append_rowsで拡張してから範囲書き込み
                if end_row > sheet.row_count:
                    needed = end_row - sheet.row_count
                    logger.info(f"  シート行数拡張: {sheet.row_count} → {end_row} (+{needed}行)")
                    sheet.add_rows(needed)
                desc = f"新規追加 {added_count+1}〜{added_count+len(new_items)}"
                # RAW_ONLY_COLS は一括書き込みから外し（空欄で送る）、後段で RAW 書き込みする
                raw_data = []
                safe_items = []
                for offset, row_data in enumerate(new_items):
                    raw_data.extend(build_raw_segments(row_data, start_row + offset))
                    item = list(row_data)
                    for ci in RAW_ONLY_COLS:
                        if ci < len(item):
                            item[ci] = ""
                    safe_items.append(item)
                sheet_update_with_retry(safe_items, f'A{start_row}:{last_col}{end_row}', desc)
                if raw_data:
                    raw_update_with_retry(raw_data, desc)
                added_count += len(new_items)
                next_new_row += len(new_items)
                logger.info(f"  [シート書き込み] 新規追加: {added_count}件完了")
                time.sleep(1)

            return updated_count, added_count, next_new_row

        # ========================================
        # 商品ループ: スクレイピング → 全77列構築 → 10件ごとにシート書き込み
        # ========================================

        # 同期項目フィルタ用に許可列インデックスを集計
        allowed_field_indices = None
        if sync_fields:
            allowed_field_indices = set()
            for f in sync_fields:
                allowed_field_indices |= SYNC_FIELD_COLUMNS.get(f, set())
            logger.info(f"書き込み対象列インデックス: {sorted(allowed_field_indices)} (+ {sorted(ALWAYS_UPDATE_COLUMNS)})")

        # 上書きモード + sync_fields フィルタ: preserve_or_set のラッパー
        # overwrite_mode=True の場合、preserve_existing を False に強制する
        # sync_fields 指定時、非対象列は preserve_existing=True を強制（既存値を守る）
        _orig_preserve_or_set = preserve_or_set
        def _pos(existing_row, col, new_value, old_row_num, new_row_num, preserve_existing=True):
            # sync_fields フィルタ: 既存行 かつ 非対象列 かつ 常時更新列でない → 強制保持
            if (allowed_field_indices is not None
                    and existing_row
                    and col.index not in allowed_field_indices
                    and col.index not in ALWAYS_UPDATE_COLUMNS):
                return _orig_preserve_or_set(existing_row, col, new_value, old_row_num, new_row_num, preserve_existing=True)
            if overwrite_mode and preserve_existing:
                preserve_existing = False
            return _orig_preserve_or_set(existing_row, col, new_value, old_row_num, new_row_num, preserve_existing=preserve_existing)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        next_new_row = max_existing_row + 1
        updated_count = 0
        added_count = 0
        scrape_success = 0
        scrape_fail = 0
        scrape_skipped = 0
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

                    skip_domain = next(
                        (d for d in SKIP_SCRAPE_DOMAINS if d in (supplier_url_for_scrape or "").lower()),
                        None,
                    )
                    if skip_domain:
                        scrape_skipped += 1
                        logger.debug(f"  スクレイピング除外({skip_domain}): {supplier_url_for_scrape[:60]}")
                    elif supplier_url_for_scrape and supplier_url_for_scrape.startswith("http"):
                        # Bullionstar: .co.nz/.us → .com に変換（.co.nz/.usはタイムアウトするため）
                        scrape_url_target = supplier_url_for_scrape
                        if "bullionstar.co.nz" in scrape_url_target:
                            scrape_url_target = scrape_url_target.replace("bullionstar.co.nz", "bullionstar.com")
                        elif "bullionstar.us" in scrape_url_target:
                            scrape_url_target = scrape_url_target.replace("bullionstar.us", "bullionstar.com")

                        # キャッシュチェック（同一URL重複防止）
                        if scrape_url_target in price_cache:
                            scraped_result = price_cache[scrape_url_target]
                        else:
                            scraped_result = scrape_url(scraper_manager, scrape_url_target)
                            price_cache[scrape_url_target] = scraped_result

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
                row[Col.SYNC_MODE.index] = _pos(existing_row, Col.SYNC_MODE, "変更なし", old_row_num, new_row_num)
                row[Col.DISPLAY_SETTING.index] = _pos(existing_row, Col.DISPLAY_SETTING, display_state, old_row_num, new_row_num, preserve_existing=True)
                row[Col.PRICE_UPDATE.index] = _pos(existing_row, Col.PRICE_UPDATE, "ON", old_row_num, new_row_num)
                row[Col.STOCK_SYNC.index] = _pos(existing_row, Col.STOCK_SYNC, "OFF", old_row_num, new_row_num)
                row[Col.DISPLAY_SYNC.index] = _pos(existing_row, Col.DISPLAY_SYNC, "OFF", old_row_num, new_row_num)
                row[Col.SYNC_STATUS.index] = _pos(existing_row, Col.SYNC_STATUS, "ダウンロード済", old_row_num, new_row_num, preserve_existing=False)

                # === G-I列: 識別情報 ===
                row[Col.PRODUCT_ID.index] = str(product_id)
                row[Col.NAME.index] = _pos(existing_row, Col.NAME, product.get("name", ""), old_row_num, new_row_num, preserve_existing=True)
                row[Col.COLORME_URL.index] = f"https://ybx.jp/?pid={product_id}"

                # === J-L列: 仕入れ先基本情報 ===
                row[Col.SUPPLIER_URL.index] = _pos(existing_row, Col.SUPPLIER_URL, "", old_row_num, new_row_num)
                row[Col.SUPPLIER_NAME.index] = _pos(existing_row, Col.SUPPLIER_NAME, Formula.supplier_name(new_row_num), old_row_num, new_row_num)
                row[Col.SUPPLIER_SITE.index] = _pos(existing_row, Col.SUPPLIER_SITE, Formula.supplier_site(new_row_num), old_row_num, new_row_num)

                # === M-Q列: 仕入れ先価格情報（既存値をベースに設定）===
                row[Col.SUPPLIER_STOCK.index] = _pos(existing_row, Col.SUPPLIER_STOCK, "", old_row_num, new_row_num)
                row[Col.SUPPLIER_PRICE.index] = _pos(existing_row, Col.SUPPLIER_PRICE, "", old_row_num, new_row_num)
                row[Col.PREV_PRICE.index] = _pos(existing_row, Col.PREV_PRICE, "", old_row_num, new_row_num)
                row[Col.PRICE_CHANGE_RATE.index] = _pos(existing_row, Col.PRICE_CHANGE_RATE, "", old_row_num, new_row_num)
                row[Col.CURRENCY.index] = _pos(existing_row, Col.CURRENCY, "", old_row_num, new_row_num)

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

                    # Q列: 通貨（数式なら上書きしない = 商品仕入れ先一覧からVLOOKUP参照を保持）
                    if not is_formula(row[Col.CURRENCY.index]):
                        old_currency = row[Col.CURRENCY.index]
                        row[Col.CURRENCY.index] = scraped.currency
                        if old_currency != scraped.currency:
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
                    row[col.index] = _pos(existing_row, col, "", old_row_num, new_row_num)

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
                # AE列(販売価格), AF列(定価): 常に数式（既存行の静的値化を防ぐ）
                # セール判定はセールON/OFF・セール率列で行い、シート上の計算式が販売/定価を導出する。
                # 過去に静的値化された場合の再発防止（Colorme APIの古い価格が
                # マージン率変更後のAE(=roundup(AB,-2))と乖離してセール表示になるバグ回避）。
                # 列文字は必ず Col から引く（ハードコードすると列の増減で参照がずれる）。
                _cur_r = new_row_num  # シート上の行番号（既存行なら維持、新規行なら新番号）
                # 原価下限(MAX(...,AA))は設けない。AAは「現在の」仕入れ先価格から計算されるため、
                # 安く仕入れた在庫では実態と合わず、指定した割引率が効かなくなるため。
                # セールONは商品ごとの手動操作なので、原価割れの可否は運用側で判断する。
                _on = Col.SALE_ENABLED.letter
                _rate = Col.SALE_RATE.letter
                _ab = Col.PROPER_PRICE.letter
                _sale_formula = (
                    f'=IF({_on}{_cur_r}="ON",'
                    f'ROUND({_ab}{_cur_r}*(1-IF({_rate}{_cur_r}="",0.05,{_rate}{_cur_r})),-2),'
                    f'roundup({_ab}{_cur_r},-2))'
                )
                _regular_formula = f'=roundup({_ab}{_cur_r},-2)'
                row[Col.SALES_PRICE.index] = _sale_formula
                row[Col.REGULAR_PRICE.index] = _regular_formula
                row[Col.MEMBERS_PRICE.index] = _pos(existing_row, Col.MEMBERS_PRICE, str(product.get("members_price") or 0), old_row_num, new_row_num, preserve_existing=False)
                row[Col.COST.index] = _pos(existing_row, Col.COST, str(product.get("cost") or 0), old_row_num, new_row_num, preserve_existing=False)
                row[Col.TAX_INCLUDED_PRICE.index] = _pos(existing_row, Col.TAX_INCLUDED_PRICE, "", old_row_num, new_row_num)
                row[Col.TAX_AMOUNT.index] = _pos(existing_row, Col.TAX_AMOUNT, "", old_row_num, new_row_num)

                # === AK-AN列: カテゴリー・グループ ===
                row[Col.CATEGORY_ID_BIG.index] = _pos(existing_row, Col.CATEGORY_ID_BIG, str(category_id_big) if category_id_big else "", old_row_num, new_row_num)
                row[Col.CATEGORY_NAME_BIG.index] = _pos(existing_row, Col.CATEGORY_NAME_BIG, "", old_row_num, new_row_num)
                row[Col.GROUP_IDS.index] = _pos(existing_row, Col.GROUP_IDS, group_ids_str, old_row_num, new_row_num, preserve_existing=True)
                row[Col.GROUP_NAMES.index] = _pos(existing_row, Col.GROUP_NAMES, "", old_row_num, new_row_num)

                # === AO列: 型番 ===
                row[Col.MODEL_NUMBER.index] = _pos(existing_row, Col.MODEL_NUMBER, product.get("model_number", "") or "", old_row_num, new_row_num)

                # === AP-AV列: 在庫管理 ===
                # 在庫数: ユーザーがスプレッドシート上で変更した値を保持する（カラーミーの値で上書きしない）
                row[Col.STOCKS.index] = _pos(existing_row, Col.STOCKS, str(product.get("stocks") or 0), old_row_num, new_row_num, preserve_existing=True)
                row[Col.STOCK_MANAGED.index] = _pos(existing_row, Col.STOCK_MANAGED, "する" if product.get("stock_managed", True) else "しない", old_row_num, new_row_num, preserve_existing=True)
                row[Col.FEW_NUM.index] = _pos(existing_row, Col.FEW_NUM, str(product.get("few_num") or 0), old_row_num, new_row_num, preserve_existing=True)
                soldout_display = product.get("soldout_display", True)
                row[Col.SOLDOUT_DISPLAY.index] = _pos(existing_row, Col.SOLDOUT_DISPLAY, "表示" if soldout_display else "非表示", old_row_num, new_row_num, preserve_existing=True)
                row[Col.MIN_NUM.index] = _pos(existing_row, Col.MIN_NUM, str(product.get("min_num") or 1), old_row_num, new_row_num, preserve_existing=True)
                row[Col.MAX_NUM.index] = _pos(existing_row, Col.MAX_NUM, str(product.get("max_num") or 0), old_row_num, new_row_num, preserve_existing=True)
                row[Col.UNIT.index] = _pos(existing_row, Col.UNIT, product.get("unit", "") or "", old_row_num, new_row_num, preserve_existing=True)

                # === AW-AZ列: 送料・配送 ===
                # 個別送料: カラーミー側で「0」は送料無料扱いになり送料無料ラインが無効化されるため、
                #          AW列は「空欄」が正解（= デフォルト送料を適用）。
                #          API側が null/0 を返す場合も空欄で書き込む。
                _dc_raw = product.get("delivery_charge")
                try:
                    _dc_val = int(_dc_raw) if _dc_raw not in (None, "") else 0
                except (TypeError, ValueError):
                    _dc_val = 0
                _dc_str = str(_dc_val) if _dc_val > 0 else ""
                row[Col.DELIVERY_CHARGE.index] = _pos(existing_row, Col.DELIVERY_CHARGE, _dc_str, old_row_num, new_row_num)
                row[Col.COOL_CHARGE.index] = _pos(existing_row, Col.COOL_CHARGE, "", old_row_num, new_row_num)
                row[Col.WEIGHT.index] = _pos(existing_row, Col.WEIGHT, "", old_row_num, new_row_num)
                row[Col.NO_DELIVERY.index] = _pos(existing_row, Col.NO_DELIVERY, "", old_row_num, new_row_num)

                # === BA-BD列: 商品説明 ===
                row[Col.EXPL.index] = _pos(existing_row, Col.EXPL, product.get("expl", "") or "", old_row_num, new_row_num)
                row[Col.SIMPLE_EXPL.index] = _pos(existing_row, Col.SIMPLE_EXPL, product.get("simple_expl", "") or "", old_row_num, new_row_num, preserve_existing=True)
                row[Col.MOBILE_EXPL.index] = _pos(existing_row, Col.MOBILE_EXPL, "", old_row_num, new_row_num)
                row[Col.MEMO.index] = _pos(existing_row, Col.MEMO, "", old_row_num, new_row_num)

                # === BE-BN列: 画像 ===
                image_cols = [Col.MAIN_IMAGE, Col.THUMBNAIL, Col.IMAGE_URL_1, Col.IMAGE_URL_2,
                              Col.IMAGE_URL_3, Col.IMAGE_URL_4, Col.IMAGE_URL_5, Col.IMAGE_URL_6,
                              Col.IMAGE_URL_7, Col.IMAGE_URL_8]
                for i, col in enumerate(image_cols):
                    img_url = image_urls[i] if i < len(image_urls) else ""
                    row[col.index] = _pos(existing_row, col, img_url, old_row_num, new_row_num)

                # === BO-BQ列: SEO ===
                # カラーミーAPIはSEOを返さないため、BS/APMEXマスタシートから CM_ID で引く
                # 既存値があれば保持、空ならBS/APMEXマスタの値を反映
                _seo_tuple = cm_id_to_seo.get(product_id, ("", "", ""))
                row[Col.PAGE_TITLE.index] = _pos(existing_row, Col.PAGE_TITLE, _seo_tuple[0] or "", old_row_num, new_row_num)
                row[Col.META_DESC.index] = _pos(existing_row, Col.META_DESC, _seo_tuple[1] or "", old_row_num, new_row_num)
                row[Col.META_KEYWORDS.index] = _pos(existing_row, Col.META_KEYWORDS, _seo_tuple[2] or "", old_row_num, new_row_num)

                # === BR-BV列: フラグ ===
                row[Col.REDUCED_TAX.index] = _pos(existing_row, Col.REDUCED_TAX, "", old_row_num, new_row_num)
                row[Col.DIGITAL_CONTENT.index] = _pos(existing_row, Col.DIGITAL_CONTENT, "", old_row_num, new_row_num)
                row[Col.SUBSCRIPTION.index] = _pos(existing_row, Col.SUBSCRIPTION, "", old_row_num, new_row_num)
                row[Col.DISPLAY_ORDER.index] = _pos(existing_row, Col.DISPLAY_ORDER, "", old_row_num, new_row_num)
                row[Col.DISABLED_PAYMENTS.index] = _pos(existing_row, Col.DISABLED_PAYMENTS, "", old_row_num, new_row_num)

                # === BW-BX列: 掲載期間 ===
                row[Col.START_DATE.index] = _pos(existing_row, Col.START_DATE, "", old_row_num, new_row_num)
                row[Col.END_DATE.index] = _pos(existing_row, Col.END_DATE, "", old_row_num, new_row_num)

                # === BY列: システム情報 ===
                row[Col.SYNC_DATETIME.index] = _pos(existing_row, Col.SYNC_DATETIME, now, old_row_num, new_row_num, preserve_existing=False)

                # === BZ-CC列: 競合情報（ユーザー手動入力、既存値を保持）===
                row[Col.COMPETITOR_URL.index] = _pos(existing_row, Col.COMPETITOR_URL, "", old_row_num, new_row_num, preserve_existing=True)
                row[Col.COMPETITOR_NAME.index] = _pos(existing_row, Col.COMPETITOR_NAME, "", old_row_num, new_row_num, preserve_existing=True)
                row[Col.COMPETITOR_PRICE.index] = _pos(existing_row, Col.COMPETITOR_PRICE, "", old_row_num, new_row_num, preserve_existing=True)
                row[Col.COMPETITOR_STOCK.index] = _pos(existing_row, Col.COMPETITOR_STOCK, "", old_row_num, new_row_num, preserve_existing=True)

                # === CD列: サブショップ採用（ユーザー手動入力、既存値を保持） ===
                row[Col.SUB_SHOP_ADOPTION.index] = _pos(existing_row, Col.SUB_SHOP_ADOPTION, "", old_row_num, new_row_num, preserve_existing=True)
                # === CE列: 価格警告（数式、既存値を保持） ===
                row[Col.PRICE_WARNING.index] = _pos(existing_row, Col.PRICE_WARNING, "", old_row_num, new_row_num, preserve_existing=True)
                # === CF-CG列: セール制御（ユーザー手動入力、既存値を保持） ===
                row[Col.SALE_ENABLED.index] = _pos(existing_row, Col.SALE_ENABLED, "", old_row_num, new_row_num, preserve_existing=True)
                row[Col.SALE_RATE.index] = _pos(existing_row, Col.SALE_RATE, "", old_row_num, new_row_num, preserve_existing=True)
                # === CH列: サブショップID（ユーザー手動入力、既存値を保持） ===
                row[Col.SUB_SHOP_ID.index] = _pos(existing_row, Col.SUB_SHOP_ID, "", old_row_num, new_row_num, preserve_existing=True)
                # === CJ列: 取り扱い区分（ユーザー手動選択、既存値を保持） ===
                row[Col.HANDLING_CATEGORY.index] = _pos(existing_row, Col.HANDLING_CATEGORY, "", old_row_num, new_row_num, preserve_existing=True)

                # === 新規行の場合のみ、M-S/T-AD/AI-AJ列に数式を自動挿入 ===
                # 既存行は preserve_existing で守られるため上書きしない
                if not is_existing:
                    r = new_row_num  # シート上の行番号（1-indexed、ヘッダー含む）
                    # M-S列: 商品仕入れ先一覧からVLOOKUP
                    # M(在庫状況)←J列, N(現価)←K列, O(前回価格)←Q列, P(変動率)←R列,
                    # Q(通貨)←L列, R(為替種類)←M列, S(為替レート)←N列
                    vlookup_map = [
                        (Col.SUPPLIER_STOCK, 'J'),
                        (Col.SUPPLIER_PRICE, 'K'),
                        (Col.PREV_PRICE, 'Q'),
                        (Col.PRICE_CHANGE_RATE, 'R'),
                        (Col.CURRENCY, 'L'),
                        (Col.EXCHANGE_TYPE, 'M'),
                        (Col.EXCHANGE_RATE, 'N'),
                    ]
                    for cm_col, sp_col in vlookup_map:
                        row[cm_col.index] = (
                            f"=IFERROR(INDEX('商品仕入れ先一覧'!${sp_col}:${sp_col},"
                            f"MATCH($J{r},'商品仕入れ先一覧'!$C:$C,0)),\"\")"
                        )
                    # T-AD列: 計算式
                    row[Col.PURCHASE_PRICE_JPY.index] = f"=N{r}*S{r}"           # T: 仕入れ額
                    row[Col.QUANTITY.index] = 1                                  # U: 数量
                    row[Col.PURCHASE_TOTAL.index] = f"=T{r}*U{r}"                # V: 仕入れ合計
                    row[Col.MARGIN_RATE.index] = 1.12                            # W: マージン率
                    row[Col.MARGIN_AMOUNT.index] = 0                             # X: マージン額
                    row[Col.SHIPPING.index] = 150                                # Y: 送料
                    row[Col.FEE.index] = 100                                     # Z: 諸経費
                    row[Col.TOTAL_COST.index] = f"=V{r}+Y{r}+Z{r}"              # AA: 合計原価
                    row[Col.PROPER_PRICE.index] = f"=AA{r}/(2-W{r})+Y{r}+Z{r}"  # AB: 適正価格
                    row[Col.GROSS_PROFIT.index] = f"=AB{r}-AA{r}"               # AC: 粗利額
                    row[Col.GROSS_PROFIT_RATE.index] = f"=(AB{r}-AA{r})/AB{r}"  # AD: 粗利率
                    # AE(販売価格) / AF(定価) は上のセクションで既存/新規共通で数式設定済み
                    # AH列: 原価 = AE（販売価格と一致、市場変動に追随）
                    row[Col.COST.index] = f"=AE{r}"                              # AH: 原価
                    # AI-AJ列: 税込販売価格・消費税額
                    row[Col.TAX_INCLUDED_PRICE.index] = f"=AE{r}*1.1"           # AI
                    row[Col.TAX_AMOUNT.index] = f"=AI{r}-AE{r}"                 # AJ
                    # CF列: 価格警告（自ショップ税込価格 AI が 競合価格 CB の 20%超で警告表示）
                    row[Col.PRICE_WARNING.index] = (
                        f'=IF(AND(CB{r}>0,AI{r}>0,AI{r}>CB{r}*1.2),'
                        f'"⚠ +"&ROUND((AI{r}/CB{r}-1)*100,1)&"%","")'
                    )

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
                            # 【根治策】カラーミー送信直前に、シートの全列を計算値(FORMATTED_VALUE)で1回だけ読み込む
                            # 数式列 (M-Q仕入れ先, R-S為替, T/V/AA-AJ 価格計算, BO-BQ SEO, BR-BT オプション 等)
                            # がすべて計算後の値に置換される。数式テキストがカラーミーに漏れることを完全防止。
                            #
                            # 従来: row (数式含む) を渡し、AB-AE / M-Q / BO-BQ を個別に再読み込みしていた
                            # (3回のAPI call + 数式列を手動列挙する必要あり = 見落としバグの温床)
                            full_row_cells = sheet.get(f'A{sheet_row}:{last_col}{sheet_row}')
                            if full_row_cells and full_row_cells[0]:
                                updated_row = [str(v) if v is not None else "" for v in full_row_cells[0]]
                                while len(updated_row) < Col.TOTAL_COLUMNS:
                                    updated_row.append("")
                            else:
                                # フォールバック: シート読み込み失敗時は元の row を使用
                                logger.warning(f"  シート行読み込み失敗、rowをフォールバック使用")
                                updated_row = list(row)
                                while len(updated_row) < Col.TOTAL_COLUMNS:
                                    updated_row.append("")

                            # 更新データを構築（フルスペック）
                            data = row_to_update_data(updated_row, price_only=False, sync_fields=sync_fields)

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
            skip_note = f", 除外{scrape_skipped}件({','.join(SKIP_SCRAPE_DOMAINS)})" if scrape_skipped else ""
            logger.info(f"スクレイピング結果: 成功{scrape_success}件, 失敗{scrape_fail}件{skip_note}")
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

    # AM列(グループID)をRAWで再書き込み（USER_ENTEREDだと巨大数値として解釈されデータ破損するため）
    # シート書き込みフェーズで AM=`'3190091,3190093` のように書いてもアポストロフィが効かず
    # 数値解釈されてしまうので、最後に RAW で上書きしてテキスト保存を強制する
    logger.info("")
    logger.info("=== AM列(グループID)RAW再書き込み 開始 ===")
    try:
        sheet_for_am = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
        # 既存行マップを再構築（書き込み後の最新状態）
        latest_rows = sheet_for_am.get_all_values()
        pid_to_row = {}
        for r_idx, r in enumerate(latest_rows[1:], start=2):
            pid_str = r[Col.PRODUCT_ID.index] if Col.PRODUCT_ID.index < len(r) else ""
            if pid_str and pid_str.isdigit():
                pid_to_row[int(pid_str)] = r_idx

        am_updates = []
        for product in products:
            pid = product.get("id")
            if pid not in pid_to_row:
                continue
            gids = product.get("group_ids") or []
            if not gids:
                continue
            row_n = pid_to_row[pid]
            value = ",".join(str(g) for g in gids)
            am_updates.append({
                'range': f'{Config.SHEET_COLORME_V2}!{Col.GROUP_IDS.letter}{row_n}',
                'values': [[value]]
            })

        if am_updates:
            # チャンク分割（大量更新時のAPI制限対策）
            CHUNK = 500
            for i in range(0, len(am_updates), CHUNK):
                chunk = am_updates[i:i+CHUNK]
                sheet_for_am.spreadsheet.values_batch_update({
                    'data': chunk,
                    'valueInputOption': 'RAW',  # 数値解釈させずに文字列として保存
                })
                time.sleep(0.5)
            logger.info(f"AM列RAW再書き込み: {len(am_updates)}件完了")
        else:
            logger.info("AM列RAW再書き込み: 対象なし")
    except Exception as e:
        logger.warning(f"AM列RAW再書き込みエラー（スキップ）: {e}")

    # シート書き込み完了後にJ列(仕入れ先URL)を自動補完
    # （新規行や空欄のJ列を埋めることで K-AE列のVLOOKUP/数式が連鎖計算される）
    # スナップショット書き戻しによるレースコンディションを防ぐため、必ず最後に実行する
    logger.info("")
    logger.info("=== J列(仕入れ先URL)自動補完 開始 ===")
    try:
        from .fill_supplier_url import fill_supplier_urls
        filled, no_match = fill_supplier_urls(client=client, dry_run=False, overwrite=False)
        logger.info(f"J列補完: {filled}件更新 / マップにない商品 {no_match}件")
    except Exception as e:
        logger.warning(f"J列自動補完でエラー（スキップ、後で手動で fill_supplier_url 実行可）: {e}")

    logger.info(f"=== {mode_label}完了 ===")


if __name__ == "__main__":
    main()
