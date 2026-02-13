"""
仕入れ先価格取得 + カラーミー即時同期スクリプト

新カラーミー商品管理シートのJ列（仕入れ先URL）から
価格と在庫状況をスクレイピングし、1行ずつ以下を実行する:
  1. シートのM-Q列・S列を更新（仕入れ先価格データ）
  2. 数式で再計算されたAE列（販売価格）を読み戻す
  3. カラーミーAPIに価格・在庫・表示状態を同期

--sync オプションなしの場合は、シート更新のみ（従来動作）。

使用方法:
  python -m src.fetch_supplier_prices --sync --verbose  # スクレイピング+即時同期
  python -m src.fetch_supplier_prices --sync --limit 5  # 5件のみ（テスト用）
  python -m src.fetch_supplier_prices --verbose          # シート更新のみ（同期なし）
"""

import argparse
import copy
import logging
import sys
import time
from datetime import datetime

from .spreadsheet import SpreadsheetClient
from .colorme import ColorMeClient
from .config import Config
from .scraper import ScraperManager, detect_shop_from_url
from .shops import ScrapedData
from .download_colorme_products import fetch_exchange_rates, is_formula, scrape_url
from .cm_sheet_columns import Col, get_cell, get_cell_int
from .sync_colorme_products import row_to_update_data

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# バッチ設定
BATCH_SIZE = 10  # N件ごとにシート書き込み（--syncなしの場合）
MAX_RETRIES = 3


def batch_update_with_retry(sheet, batch_data: list) -> bool:
    """リトライ付きバッチ更新"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            data = copy.deepcopy(batch_data)
            sheet.batch_update(data, value_input_option='USER_ENTERED')
            return True
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 10 * attempt
                logger.warning(f"  シート更新エラー（リトライ {attempt}/{MAX_RETRIES}、{wait}秒後）: {e}")
                time.sleep(wait)
            else:
                logger.error(f"  シート更新エラー（全リトライ失敗）: {e}")
                return False


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="仕入れ先価格取得 + カラーミー同期")
    parser.add_argument("--limit", type=int, default=0, help="処理件数制限（0=無制限）")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    parser.add_argument("--sync", action="store_true",
                        help="スクレイピング後にカラーミーAPIへ即時同期（1行ずつ）")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode_label = "価格取得+カラーミー同期" if args.sync else "仕入れ先価格取得（軽量版）"
    logger.info(f"=== {mode_label}開始 ===")

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # --sync時はカラーミーAPI必須
    colorme = None
    if args.sync:
        if not Config.is_colorme_enabled():
            logger.error("--sync使用時はCOLORME_ACCESS_TOKENが必要です")
            sys.exit(1)
        colorme = ColorMeClient()

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

    # ========================================
    # 1. 既存シートデータ読み込み
    # ========================================
    logger.info("シートデータを読み込み中...")
    last_col = Col.last_column_letter()

    existing = sheet.get_all_values()
    existing_formulas = sheet.get(f'A1:{last_col}', value_render_option='FORMULA')

    if len(existing) <= 1:
        logger.info("データがありません")
        return

    logger.info(f"シート行数: {len(existing) - 1}件（ヘッダー除く）")

    # ========================================
    # 2. スクレイピング対象URLと通貨情報を収集
    # ========================================
    targets = []  # [(row_num, url, value_row, formula_row), ...]
    currency_exchange_types = {}  # 通貨 -> 為替種類

    for row_idx in range(1, len(existing)):  # ヘッダーをスキップ
        row = existing[row_idx]
        formula_row = existing_formulas[row_idx] if row_idx < len(existing_formulas) else []

        url = get_cell(row, Col.SUPPLIER_URL)
        if not url or not url.startswith("http"):
            continue

        row_num = row_idx + 1  # 1-indexed（シート上の行番号）
        targets.append((row_num, url, row, formula_row))

        # 通貨・為替種類を収集
        currency = get_cell(row, Col.CURRENCY).strip().upper()
        if currency and currency != "JPY":
            exchange_type = get_cell(row, Col.EXCHANGE_TYPE).strip()
            if not exchange_type:
                exchange_type = "クレカ"
            currency_exchange_types[currency] = exchange_type

    if not targets:
        logger.info("スクレイピング対象のURLがありません")
        return

    if args.limit > 0:
        targets = targets[:args.limit]

    logger.info(f"スクレイピング対象: {len(targets)}件")

    # ========================================
    # 3. 為替レート取得
    # ========================================
    currencies = list(currency_exchange_types.keys())
    exchange_rates = fetch_exchange_rates(currencies, currency_exchange_types)

    # ========================================
    # 4. スクレイピング + シート更新 (+カラーミー同期)
    # ========================================
    scrape_success = 0
    scrape_fail = 0
    sync_success = 0
    sync_fail = 0
    sync_skip = 0
    price_cache = {}  # URL -> ScrapedData（重複防止）
    pending_updates = []

    def flush_updates():
        """溜まった更新をバッチ書き込み"""
        nonlocal pending_updates
        if not pending_updates:
            return
        if batch_update_with_retry(sheet, pending_updates):
            logger.debug(f"  [シート保存] {len(pending_updates)}セルを更新")
        pending_updates = []

    with ScraperManager() as scraper_manager:
        for i, (row_num, url, value_row, formula_row) in enumerate(targets):
            product_name = get_cell(value_row, Col.NAME)[:30] or url[:40]
            logger.info(f"[{i+1}/{len(targets)}] {product_name} (行: {row_num})")

            # キャッシュチェック
            if url in price_cache:
                scraped_result = price_cache[url]
                logger.info(f"  キャッシュ使用")
            else:
                scraped_result = scrape_url(scraper_manager, url)
                price_cache[url] = scraped_result

            scraped = scraped_result.scraped_data

            if scraped.error:
                scrape_fail += 1
                logger.warning(f"  スクレイピング失敗: {scraped.error}")
                continue

            scrape_success += 1
            logger.info(f"  取得成功: {scraped.currency} {scraped.price:,.2f} / {'在庫あり' if scraped.in_stock else '在庫なし'}")

            # --- M列: 仕入れ先在庫状況 ---
            if not _has_formula(formula_row, Col.SUPPLIER_STOCK):
                stock_str = "In Stock" if scraped.in_stock else "Out of Stock"
                pending_updates.append({
                    'range': f'{Col.SUPPLIER_STOCK.letter}{row_num}',
                    'values': [[stock_str]]
                })

            # --- O列: 前回仕入れ価格（現在のN列の値を保存）---
            if not _has_formula(formula_row, Col.PREV_PRICE):
                current_price = get_cell(value_row, Col.SUPPLIER_PRICE)
                if current_price:
                    pending_updates.append({
                        'range': f'{Col.PREV_PRICE.letter}{row_num}',
                        'values': [[current_price]]
                    })

            # --- N列: 仕入れ先価格 ---
            if not _has_formula(formula_row, Col.SUPPLIER_PRICE):
                pending_updates.append({
                    'range': f'{Col.SUPPLIER_PRICE.letter}{row_num}',
                    'values': [[str(scraped.price)]]
                })

            # --- P列: 価格変動率 ---
            if not _has_formula(formula_row, Col.PRICE_CHANGE_RATE):
                prev_price_str = get_cell(value_row, Col.SUPPLIER_PRICE)
                if prev_price_str:
                    try:
                        prev_price = float(prev_price_str.replace(",", ""))
                        if prev_price > 0:
                            change_rate = ((scraped.price - prev_price) / prev_price) * 100
                            pending_updates.append({
                                'range': f'{Col.PRICE_CHANGE_RATE.letter}{row_num}',
                                'values': [[f"{change_rate:+.2f}%"]]
                            })
                    except ValueError:
                        pass

            # --- Q列: 通貨 ---
            pending_updates.append({
                'range': f'{Col.CURRENCY.letter}{row_num}',
                'values': [[scraped.currency]]
            })

            # --- S列: 為替レート ---
            if not _has_formula(formula_row, Col.EXCHANGE_RATE):
                cur = scraped.currency.upper() if scraped.currency else ""
                if cur == "JPY":
                    pending_updates.append({
                        'range': f'{Col.EXCHANGE_RATE.letter}{row_num}',
                        'values': [["1"]]
                    })
                elif cur:
                    # 新規通貨の為替レート取得
                    if cur not in currency_exchange_types:
                        currency_exchange_types[cur] = "クレカ"
                        new_rates = fetch_exchange_rates([cur], {cur: "クレカ"})
                        exchange_rates.update(new_rates)

                    exchange_type = get_cell(value_row, Col.EXCHANGE_TYPE).strip() or "クレカ"
                    rate_key = f"{cur}_{exchange_type}"
                    if rate_key in exchange_rates:
                        rate = round(exchange_rates[rate_key], 4)
                        pending_updates.append({
                            'range': f'{Col.EXCHANGE_RATE.letter}{row_num}',
                            'values': [[str(rate)]]
                        })
                        logger.debug(f"  為替レート更新: {rate_key} = {rate}")

            # ========================================
            # --sync: 1行ずつシート書き込み → 読み戻し → カラーミー同期
            # ========================================
            if args.sync:
                # シートに即座に書き込み（数式が再計算される）
                flush_updates()

                # カラーミー同期対象かチェック
                sync_mode = get_cell(value_row, Col.SYNC_MODE)
                product_id = get_cell_int(value_row, Col.PRODUCT_ID)

                if sync_mode != "更新" or product_id <= 0:
                    sync_skip += 1
                    if sync_mode != "更新":
                        logger.debug(f"  CM同期スキップ: A列='{sync_mode}'")
                    else:
                        logger.debug(f"  CM同期スキップ: 商品IDなし")
                    continue

                try:
                    # 数式で再計算されたAB列・AE列を読み戻す
                    price_range = f'{Col.PROPER_PRICE.letter}{row_num}:{Col.SALES_PRICE.letter}{row_num}'
                    price_cells = sheet.get(price_range)

                    # ローカル行データを更新して row_to_update_data に渡す
                    updated_row = list(value_row)

                    # 行を必要な長さまでパディング
                    while len(updated_row) <= Col.SALES_PRICE.index:
                        updated_row.append("")

                    # M列: 今回書き込んだ在庫状況を反映
                    updated_row[Col.SUPPLIER_STOCK.index] = "In Stock" if scraped.in_stock else "Out of Stock"

                    # AB-AE列: 読み戻した数式計算結果を反映
                    if price_cells and price_cells[0]:
                        read_cells = price_cells[0]
                        start_idx = Col.PROPER_PRICE.index
                        for j, val in enumerate(read_cells):
                            idx = start_idx + j
                            if idx < len(updated_row):
                                updated_row[idx] = str(val) if val else ""

                    # 更新データを構築
                    data = row_to_update_data(updated_row, price_only=True)

                    if not data or not data.get("updates"):
                        sync_skip += 1
                        logger.debug(f"  CM同期スキップ: 更新項目なし")
                        continue

                    log_parts = data.get("log_parts", [])
                    if log_parts:
                        logger.info(f"  CM: {', '.join(log_parts)}")

                    # カラーミーAPIに送信
                    if colorme.update_product(product_id, data["updates"]):
                        sync_success += 1
                        logger.info(f"  → カラーミー同期成功")

                        # F列・BY列を更新
                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        pending_updates.extend([
                            {'range': f'{Col.SYNC_STATUS.letter}{row_num}', 'values': [["同期済み"]]},
                            {'range': f'{Col.SYNC_DATETIME.letter}{row_num}', 'values': [[now]]},
                        ])
                        flush_updates()
                    else:
                        sync_fail += 1
                        logger.error(f"  → カラーミー同期失敗")

                except Exception as e:
                    sync_fail += 1
                    logger.error(f"  CM同期エラー: {e}")

                # API制限対策（スクレイピング間隔もあるので短めで十分）
                time.sleep(0.3)
            else:
                # --syncなし: 従来のバッチ書き込み
                if (i + 1) % BATCH_SIZE == 0:
                    flush_updates()
                    time.sleep(1)

    # 残りを書き込み
    flush_updates()

    # ========================================
    # 5. サマリー
    # ========================================
    logger.info("=== 結果 ===")
    logger.info(f"スクレイピング成功: {scrape_success}件")
    logger.info(f"スクレイピング失敗: {scrape_fail}件")
    if args.sync:
        logger.info(f"カラーミー同期成功: {sync_success}件")
        logger.info(f"カラーミー同期失敗: {sync_fail}件")
        logger.info(f"カラーミー同期スキップ: {sync_skip}件")
    logger.info(f"=== {mode_label}完了 ===")


def _has_formula(formula_row: list, col) -> bool:
    """数式行の指定列が数式かどうかを判定"""
    if len(formula_row) > col.index:
        val = formula_row[col.index]
        return isinstance(val, str) and val.startswith("=")
    return False


if __name__ == "__main__":
    main()
