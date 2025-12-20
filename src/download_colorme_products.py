"""
カラーミー商品ダウンロードスクリプト

カラーミーAPIから全商品を取得し、新カラーミー商品管理シートに書き込む。
F列（仕入れ先商品URL）がある場合は、価格を自動取得してL列に反映する。
"""

import argparse
import logging
import sys
from datetime import datetime

from .spreadsheet import SpreadsheetClient
from .colorme import ColorMeClient
from .config import Config
from .scraper import ScraperManager, ScrapeTarget, detect_shop_from_url
from .shops import ScrapedData
from .exchange_rate import ExchangeRateClient, WiseRateClient

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def adjust_formula_row(formula: str, old_row: int, new_row: int) -> str:
    """
    数式内の行番号を調整する

    Args:
        formula: 元の数式（例: "=L2*Q2"）
        old_row: 元の行番号
        new_row: 新しい行番号

    Returns:
        str: 行番号を調整した数式
    """
    if not formula or not formula.startswith("="):
        return formula

    import re
    # セル参照のパターン（例: A2, $A2, A$2, $A$2）
    # 行番号が固定されていない参照のみ置換
    def replace_row(match):
        col = match.group(1)  # 列部分（$A, A, $AB, ABなど）
        row = match.group(2)  # 行番号部分
        if row.startswith("$"):
            # 行番号が固定されている場合は変更しない
            return match.group(0)
        if int(row) == old_row:
            return f"{col}{new_row}"
        return match.group(0)

    # パターン: 列名（$付きまたは無し） + 行番号（$付きまたは無し）
    pattern = r'(\$?[A-Z]+)(\$?\d+)'
    return re.sub(pattern, replace_row, formula)


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
            # クレカレート（手数料込み）
            rate = exchange_client.get_credit_card_rate(currency, "JPY")
            if rate:
                rates[f"{currency}_クレカ"] = rate
                logger.info(f"  クレカ: 1 {currency} = {rate:.4f} JPY")

    return rates


def fetch_prices_from_urls(urls: list[str]) -> dict[str, ScrapedData]:
    """
    URLリストから価格情報をスクレイピングする

    Args:
        urls: スクレイピング対象のURLリスト

    Returns:
        dict: URL -> ScrapedData のマッピング
    """
    if not urls:
        return {}

    logger.info(f"仕入れ先URLから価格を取得中... ({len(urls)}件)")

    scrape_targets = []
    for url in urls:
        shop_name = detect_shop_from_url(url)
        scrape_targets.append(ScrapeTarget(
            shop_name=shop_name,
            url=url,
            product_name_hint=""
        ))

    results = {}
    with ScraperManager() as manager:
        scraped_results = manager.scrape_all(scrape_targets)

        for url, result in zip(urls, scraped_results):
            if result.error:
                logger.warning(f"  スクレイピング失敗: {url[:50]}... - {result.error}")
            else:
                logger.info(f"  取得成功: {result.product_name[:30]} - {result.currency} {result.price:,.2f}")
            results[url] = result

    return results


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="カラーミー商品ダウンロード")
    parser.add_argument("--fetch-prices", action="store_true",
                        help="F列のURLから価格を自動取得してL列に反映")
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

    # 全商品を取得
    logger.info("カラーミーから全商品を取得中...")
    products = colorme.get_all_products(limit=1000)
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

        # 既存データを取得（仕入れ先情報と数式を保持するため）
        # 値として取得（商品IDのマッピング用）
        existing = sheet.get_all_values()
        # 数式として取得（数式を保持するため）
        existing_formulas = sheet.get(f'A1:CB{len(existing) + 1}', value_render_option='FORMULA')

        existing_data = {}  # 商品ID -> 既存行データのマッピング（数式含む）
        existing_row_map = {}  # 商品ID -> 行番号のマッピング
        if len(existing) > 1:
            for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
                if len(row) > 2 and row[2]:  # C列: カラーミー商品ID
                    try:
                        pid = int(row[2])
                        # 数式データを使用（存在すれば）
                        if existing_formulas and row_idx < len(existing_formulas):
                            existing_data[pid] = existing_formulas[row_idx]
                        else:
                            existing_data[pid] = row
                        existing_row_map[pid] = row_idx
                    except ValueError:
                        pass
            logger.info(f"既存データを取得: {len(existing_data)}件（数式を保持）")

            # 既存データをクリア（2行目以降）
            sheet.batch_clear([f'A2:CB{len(existing) + 1}'])

        # 既存データからO列（取引通貨）とP列（為替種類）を収集
        # ※数式の場合は値を取得するためexistingから取得
        currency_exchange_types = {}  # 通貨 -> 為替種類
        for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
            if len(row) > 15:
                currency = row[14].strip().upper() if len(row) > 14 and row[14] else ""
                exchange_type = row[15].strip() if len(row) > 15 and row[15] else "クレカ"
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

        # 価格自動取得オプション: 既存データのF列URLから価格を取得
        price_data = {}
        if args.fetch_prices and existing_data:
            # F列（仕入れ先商品URL）を収集
            urls_to_fetch = []
            for pid, row in existing_data.items():
                if len(row) > 5 and row[5]:  # F列: 仕入れ先商品URL
                    url = row[5].strip()
                    if url.startswith("http"):
                        urls_to_fetch.append(url)

            if urls_to_fetch:
                # 重複を除いて価格取得
                unique_urls = list(set(urls_to_fetch))
                price_data = fetch_prices_from_urls(unique_urls)
                logger.info(f"価格取得完了: {len([r for r in price_data.values() if not r.error])}件成功")

        # 商品データを行に変換
        rows = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for product in products:
            product_id = product.get("id", 0)

            # カテゴリー情報
            category = product.get("category") or {}
            category_id_big = category.get("id_big", 0) if isinstance(category, dict) else 0
            category_id_small = category.get("id_small", 0) if isinstance(category, dict) else 0

            # グループID
            group_ids = product.get("group_ids") or []
            if isinstance(group_ids, list):
                group_ids_str = ",".join(str(g) for g in group_ids)
            else:
                group_ids_str = str(group_ids)

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

            # 80列分のデータを作成（A-B列: 操作項目を先頭に配置）
            row = [""] * 80

            # 既存データがあれば仕入れ先情報を保持
            existing_row = existing_data.get(product_id, [])

            # 行番号の計算（数式調整用）
            old_row_num = existing_row_map.get(product_id, 0) + 1  # 1-indexed（ヘッダー含む）
            new_row_num = len(rows) + 2  # 現在書き込む行番号（ヘッダー+1）

            # A-B: 操作項目
            row[0] = "変更なし"  # A: 同期モード
            row[1] = display_state  # B: 掲載設定（日本語）

            # C-E: 識別情報
            row[2] = str(product_id)  # C: カラーミー商品ID
            row[3] = product.get("name", "")  # D: 商品名
            row[4] = f"https://ybx.jp/?pid={product_id}"  # E: カラーミー商品URL

            # F-O: 仕入れ先情報（既存データを保持）
            if len(existing_row) > 14:
                row[5] = existing_row[5] if len(existing_row) > 5 else ""   # F: 仕入れ先商品URL
                row[6] = existing_row[6] if len(existing_row) > 6 else ""   # G: 仕入れ先商品名
                row[7] = existing_row[7] if len(existing_row) > 7 else ""   # H: 仕入れ先サイト
                row[8] = existing_row[8] if len(existing_row) > 8 else ""   # I: 最上位カテゴリ
                row[9] = existing_row[9] if len(existing_row) > 9 else ""   # J: 親カテゴリ
                row[10] = existing_row[10] if len(existing_row) > 10 else "" # K: 子カテゴリ

                # L列: 仕入れ先価格（現地通貨）- 自動取得または既存値
                supplier_url = row[5].strip() if row[5] else ""
                if supplier_url and supplier_url in price_data:
                    scraped = price_data[supplier_url]
                    if not scraped.error:
                        # 前回価格をM列に保存
                        prev_price_str = existing_row[11] if len(existing_row) > 11 else ""
                        row[12] = prev_price_str  # M: 前回仕入れ価格

                        # 新価格をL列に設定
                        row[11] = str(scraped.price)  # L: 仕入れ先価格（現地通貨）
                        row[14] = scraped.currency  # O: 取引通貨

                        # 価格変動率を計算してN列に設定
                        if prev_price_str:
                            try:
                                prev_price = float(prev_price_str)
                                if prev_price > 0:
                                    change_rate = ((scraped.price - prev_price) / prev_price) * 100
                                    row[13] = f"{change_rate:+.2f}%"  # N: 価格変動率
                            except ValueError:
                                pass
                    else:
                        # スクレイピング失敗時は既存値を保持
                        row[11] = existing_row[11] if len(existing_row) > 11 else ""
                        row[12] = existing_row[12] if len(existing_row) > 12 else ""
                        row[13] = existing_row[13] if len(existing_row) > 13 else ""
                        row[14] = existing_row[14] if len(existing_row) > 14 else ""
                else:
                    # 価格取得対象外は既存値を保持
                    row[11] = existing_row[11] if len(existing_row) > 11 else ""
                    row[12] = existing_row[12] if len(existing_row) > 12 else ""
                    row[13] = existing_row[13] if len(existing_row) > 13 else ""
                    row[14] = existing_row[14] if len(existing_row) > 14 else ""

            # P-AB: 価格計算（既存データを保持、数式は行番号を調整）
            for i in range(15, 28):
                if len(existing_row) > i:
                    cell_value = existing_row[i]
                    # 数式の場合は行番号を調整
                    if isinstance(cell_value, str) and cell_value.startswith("="):
                        row[i] = adjust_formula_row(cell_value, old_row_num, new_row_num)
                    else:
                        row[i] = cell_value

            # Q列（為替レート）を自動更新（数式でない場合のみ）
            if not (row[16] and isinstance(row[16], str) and row[16].startswith("=")):
                currency = row[14].strip().upper() if row[14] else ""
                exchange_type = row[15].strip() if row[15] else "クレカ"
                if currency and currency != "JPY":
                    rate_key = f"{currency}_{exchange_type}"
                    if rate_key in exchange_rates:
                        row[16] = str(round(exchange_rates[rate_key], 4))  # Q: 為替レート

            # AC-AH: カラーミー価格情報
            row[28] = str(product.get("sales_price") or product.get("price") or 0)  # AC: 販売価格
            row[29] = str(product.get("price") or 0)  # AD: 定価
            row[30] = str(product.get("members_price") or 0)  # AE: 会員価格
            row[31] = str(product.get("cost") or 0)  # AF: 原価

            # AG-AH: 消費税計算（数式を保持）
            for i in range(32, 34):
                if len(existing_row) > i:
                    cell_value = existing_row[i]
                    if isinstance(cell_value, str) and cell_value.startswith("="):
                        row[i] = adjust_formula_row(cell_value, old_row_num, new_row_num)
                    else:
                        row[i] = cell_value

            # AI-AL: カテゴリー・グループ
            row[34] = str(category_id_big) if category_id_big else ""  # AI: 大カテゴリーID
            row[35] = str(category_id_small) if category_id_small else ""  # AJ: 小カテゴリーID
            row[36] = group_ids_str  # AK: グループID
            row[37] = product.get("model_number", "") or ""  # AL: 型番

            # AM-AS: 在庫管理
            row[38] = str(product.get("stocks") or 0)  # AM: 在庫数
            row[39] = "する" if product.get("stock_managed", True) else "しない"  # AN: 在庫管理
            row[40] = str(product.get("few_num") or 0)  # AO: 残りわずか数
            soldout_display = product.get("soldout_display", True)
            row[41] = "表示" if soldout_display else "非表示"  # AP: 売切れ表示
            row[42] = str(product.get("min_num") or 1)  # AQ: 最小購入数
            row[43] = str(product.get("max_num") or 0)  # AR: 最大購入数
            row[44] = product.get("unit", "") or ""  # AS: 単位

            # AT-AW: 送料・配送
            row[45] = str(product.get("delivery_charge") or 0)  # AT: 個別送料

            # AX-BA: 商品説明
            row[49] = product.get("expl", "") or ""  # AX: 商品説明
            row[50] = product.get("simple_expl", "") or ""  # AY: 簡易説明

            # BB-BK: 画像
            row[53] = image_url  # BB: メイン画像URL
            row[54] = product.get("thumbnail_image_url", "") or ""  # BC: サムネイルURL
            for i, url in enumerate(image_urls[:8]):
                row[55 + i] = url  # BD-BK: 画像URL1-8

            # BV-BZ: 更新制御（既存の設定を保持）
            if len(existing_row) > 75:
                row[73] = existing_row[73] if len(existing_row) > 73 else "OFF"  # BV: 価格更新ON/OFF
                row[74] = existing_row[74] if len(existing_row) > 74 else "OFF"  # BW: 在庫連動ON/OFF
                row[75] = existing_row[75] if len(existing_row) > 75 else "変更しない"  # BX: 表示連動
            else:
                row[73] = "OFF"
                row[74] = "OFF"
                row[75] = "変更しない"
            row[76] = "ダウンロード済"  # BY: 同期ステータス
            row[77] = now  # BZ: 同期日時

            # CA-CB: システム情報
            make_date = product.get("make_date", "")
            update_date = product.get("update_date", "")
            row[78] = make_date if make_date else ""  # CA: 商品作成日時
            row[79] = update_date if update_date else ""  # CB: 商品更新日時

            rows.append(row)

        # バッチ書き込み
        if rows:
            sheet.update(f'A2:CB{len(rows) + 1}', rows, value_input_option='USER_ENTERED')
            logger.info(f"シートに{len(rows)}件の商品を書き込みました")

    except Exception as e:
        logger.error(f"シート書き込みエラー: {e}")
        sys.exit(1)

    logger.info("=== カラーミー商品ダウンロード完了 ===")


if __name__ == "__main__":
    main()
