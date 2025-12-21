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


def preserve_or_set(existing_row: list, index: int, new_value: str, old_row_num: int, new_row_num: int, preserve_existing: bool = True) -> str:
    """
    既存セルが数式の場合は行番号を調整して保持する。
    preserve_existing=Trueの場合、既存の値（数式以外）も保持する。
    preserve_existing=Falseの場合、数式のみ保持し、それ以外はnew_valueを使用する。

    Args:
        existing_row: 既存の行データ
        index: 列インデックス
        new_value: 新しい値（既存データがない場合、またはpreserve_existing=Falseで数式でない場合に使用）
        old_row_num: 元の行番号（1-indexed）
        new_row_num: 新しい行番号（1-indexed）
        preserve_existing: 既存の値（数式以外）も保持するかどうか（デフォルト: True）

    Returns:
        str: セルに設定する値
    """
    if len(existing_row) > index:
        cell_value = existing_row[index]
        # 文字列に変換して処理
        cell_str = str(cell_value) if cell_value is not None else ""
        if cell_str.startswith("="):
            # 数式の場合は行番号を調整
            return adjust_formula_row(cell_str, old_row_num, new_row_num)
        elif preserve_existing and cell_value:
            # 既存の値を保持（空でない場合）- 文字列として返す
            return cell_str
    return new_value


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

        # 既存データを取得（仕入れ先情報と数式を保持するため）
        # 値として取得（商品IDのマッピング用）
        existing = sheet.get_all_values()
        # 数式として取得（数式を保持するため）
        existing_formulas = sheet.get(f'A1:CB{len(existing) + 1}', value_render_option='FORMULA')

        existing_data = {}  # 商品ID -> 既存行データのマッピング（数式含む）
        existing_row_map = {}  # 商品ID -> 行番号のマッピング（1-indexed、ヘッダー除く）
        max_existing_row = 1  # 既存データの最大行番号
        if len(existing) > 1:
            logger.info(f"既存データ行数: {len(existing)}行, 数式データ行数: {len(existing_formulas) if existing_formulas else 0}行")
            for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
                if len(row) > 2 and row[2]:  # C列: カラーミー商品ID
                    try:
                        pid = int(row[2])
                        # 数式データと値データをマージ
                        # 数式データがあればそれを優先、なければ値データを使用
                        if existing_formulas and row_idx < len(existing_formulas):
                            formula_row = list(existing_formulas[row_idx])
                            # 数式データが短い場合は値データで補完（80列まで）
                            while len(formula_row) < 80:
                                if len(row) > len(formula_row):
                                    formula_row.append(row[len(formula_row)])
                                else:
                                    formula_row.append("")
                            existing_data[pid] = formula_row
                        else:
                            # 値データを使用（80列まで拡張）
                            value_row = list(row)
                            while len(value_row) < 80:
                                value_row.append("")
                            existing_data[pid] = value_row
                        existing_row_map[pid] = row_idx
                        max_existing_row = max(max_existing_row, row_idx)

                        # デバッグ: 最初の3件のみF,O,P,S,U-X列の内容を確認
                        if len(existing_data) <= 3:
                            r = existing_data[pid]
                            logger.info(f"  既存データ 商品ID {pid}: F={r[5][:20] if r[5] else ''}, O={r[14]}, P={r[15]}, S={r[18]}, U-X={r[20:24]}")
                    except ValueError:
                        pass
            logger.info(f"既存データを取得: {len(existing_data)}件（数式を保持）")
            # 注意: 既存データをクリアしない - 各行を個別に更新する

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
        # 既存行の更新と新規行の追加を分けて処理
        update_rows = {}  # 行番号 -> 行データのマッピング（既存商品の更新用）
        new_rows = []  # 新規商品用
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        next_new_row = max_existing_row + 1  # 新規商品を追加する開始行番号

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
            is_existing = product_id in existing_row_map

            # 行番号の計算（数式調整用）
            if is_existing:
                # 既存商品：同じ行番号を維持
                old_row_num = existing_row_map[product_id] + 1  # 1-indexed（ヘッダー含む）
                new_row_num = old_row_num  # 同じ行に上書き
            else:
                # 新規商品：末尾に追加
                old_row_num = 0
                new_row_num = next_new_row + len(new_rows) + 1  # ヘッダー含む

            # A-B: 操作項目（既存値を保持、なければデフォルト値）
            row[0] = preserve_or_set(existing_row, 0, "変更なし", old_row_num, new_row_num)  # A: 同期モード
            row[1] = display_state  # B: 掲載設定（日本語）- APIから取得

            # C-E: 識別情報
            row[2] = str(product_id)  # C: カラーミー商品ID
            row[3] = product.get("name", "")  # D: 商品名
            row[4] = f"https://ybx.jp/?pid={product_id}"  # E: カラーミー商品URL

            # F-O: 仕入れ先情報（既存データを保持、数式があれば保持）
            row[5] = preserve_or_set(existing_row, 5, "", old_row_num, new_row_num)   # F: 仕入れ先商品URL
            row[6] = preserve_or_set(existing_row, 6, "", old_row_num, new_row_num)   # G: 仕入れ先商品名
            row[7] = preserve_or_set(existing_row, 7, "", old_row_num, new_row_num)   # H: 仕入れ先サイト
            row[8] = preserve_or_set(existing_row, 8, "", old_row_num, new_row_num)   # I: 最上位カテゴリ
            row[9] = preserve_or_set(existing_row, 9, "", old_row_num, new_row_num)   # J: 親カテゴリ
            row[10] = preserve_or_set(existing_row, 10, "", old_row_num, new_row_num) # K: 子カテゴリ

            # L-O列: 価格情報（数式があれば保持、スクレイピング結果があれば更新）
            # まず既存値または数式を保持
            row[11] = preserve_or_set(existing_row, 11, "", old_row_num, new_row_num)  # L: 仕入れ先価格
            row[12] = preserve_or_set(existing_row, 12, "", old_row_num, new_row_num)  # M: 前回仕入れ価格
            row[13] = preserve_or_set(existing_row, 13, "", old_row_num, new_row_num)  # N: 価格変動率
            row[14] = preserve_or_set(existing_row, 14, "", old_row_num, new_row_num)  # O: 取引通貨

            # スクレイピング結果があり、かつ数式でない場合のみ更新
            supplier_url_raw = row[5]
            supplier_url = str(supplier_url_raw).strip() if supplier_url_raw and not str(supplier_url_raw).startswith("=") else ""
            if supplier_url and supplier_url in price_data:
                scraped = price_data[supplier_url]
                if not scraped.error:
                    # L列が数式でない場合のみ更新
                    if not (row[11] and isinstance(row[11], str) and row[11].startswith("=")):
                        # 前回価格をM列に保存（M列が数式でない場合）
                        if not (row[12] and isinstance(row[12], str) and row[12].startswith("=")):
                            prev_val = existing_row[11] if len(existing_row) > 11 else ""
                            row[12] = str(prev_val) if prev_val else ""
                        # 新価格をL列に設定
                        row[11] = str(scraped.price)

                    # O列が数式でない場合のみ更新
                    if not (row[14] and isinstance(row[14], str) and row[14].startswith("=")):
                        row[14] = scraped.currency

                    # N列が数式でない場合のみ価格変動率を計算
                    if not (row[13] and isinstance(row[13], str) and row[13].startswith("=")):
                        prev_price_raw = existing_row[11] if len(existing_row) > 11 else ""
                        prev_price_str = str(prev_price_raw) if prev_price_raw else ""
                        if prev_price_str and not prev_price_str.startswith("="):
                            try:
                                prev_price = float(prev_price_str)
                                if prev_price > 0:
                                    change_rate = ((scraped.price - prev_price) / prev_price) * 100
                                    row[13] = f"{change_rate:+.2f}%"
                            except ValueError:
                                pass

            # P-AB: 価格計算（既存データを保持、数式は行番号を調整）
            # P(15), Q(16), R(17), S(18), T(19), U(20), V(21), W(22), X(23), Y(24), Z(25), AA(26), AB(27)
            for i in range(15, 28):
                row[i] = preserve_or_set(existing_row, i, "", old_row_num, new_row_num)

            # Q列（為替レート）を自動更新（数式でない場合のみ）
            if not (row[16] and isinstance(row[16], str) and row[16].startswith("=")):
                currency = row[14].strip().upper() if row[14] else ""
                exchange_type = row[15].strip() if row[15] else "クレカ"
                if currency == "JPY":
                    row[16] = "1"  # Q: 為替レート（JPYは1）
                elif currency:
                    rate_key = f"{currency}_{exchange_type}"
                    if rate_key in exchange_rates:
                        row[16] = str(round(exchange_rates[rate_key], 4))  # Q: 為替レート

            # AC-AH: カラーミー価格情報（数式があれば保持、値はAPIから取得）
            row[28] = preserve_or_set(existing_row, 28, str(product.get("sales_price") or product.get("price") or 0), old_row_num, new_row_num, preserve_existing=False)  # AC: 販売価格
            row[29] = preserve_or_set(existing_row, 29, str(product.get("price") or 0), old_row_num, new_row_num, preserve_existing=False)  # AD: 定価
            row[30] = preserve_or_set(existing_row, 30, str(product.get("members_price") or 0), old_row_num, new_row_num, preserve_existing=False)  # AE: 会員価格
            row[31] = preserve_or_set(existing_row, 31, str(product.get("cost") or 0), old_row_num, new_row_num, preserve_existing=False)  # AF: 原価
            row[32] = preserve_or_set(existing_row, 32, "", old_row_num, new_row_num)  # AG: 消費税込販売価格
            row[33] = preserve_or_set(existing_row, 33, "", old_row_num, new_row_num)  # AH: 消費税額

            # AI-AL: カテゴリー・グループ（数式があれば保持）
            row[34] = preserve_or_set(existing_row, 34, str(category_id_big) if category_id_big else "", old_row_num, new_row_num)  # AI: 大カテゴリーID
            row[35] = preserve_or_set(existing_row, 35, str(category_id_small) if category_id_small else "", old_row_num, new_row_num)  # AJ: 小カテゴリーID
            row[36] = preserve_or_set(existing_row, 36, group_ids_str, old_row_num, new_row_num)  # AK: グループID
            row[37] = preserve_or_set(existing_row, 37, product.get("model_number", "") or "", old_row_num, new_row_num)  # AL: 型番

            # AM-AS: 在庫管理（数式があれば保持、APIから取得した値で更新）
            row[38] = preserve_or_set(existing_row, 38, str(product.get("stocks") or 0), old_row_num, new_row_num, preserve_existing=False)  # AM: 在庫数
            row[39] = preserve_or_set(existing_row, 39, "する" if product.get("stock_managed", True) else "しない", old_row_num, new_row_num, preserve_existing=False)  # AN: 在庫管理
            row[40] = preserve_or_set(existing_row, 40, str(product.get("few_num") or 0), old_row_num, new_row_num, preserve_existing=False)  # AO: 残りわずか数
            soldout_display = product.get("soldout_display", True)
            row[41] = preserve_or_set(existing_row, 41, "表示" if soldout_display else "非表示", old_row_num, new_row_num, preserve_existing=False)  # AP: 売切れ表示
            row[42] = preserve_or_set(existing_row, 42, str(product.get("min_num") or 1), old_row_num, new_row_num, preserve_existing=False)  # AQ: 最小購入数
            row[43] = preserve_or_set(existing_row, 43, str(product.get("max_num") or 0), old_row_num, new_row_num, preserve_existing=False)  # AR: 最大購入数
            row[44] = preserve_or_set(existing_row, 44, product.get("unit", "") or "", old_row_num, new_row_num, preserve_existing=False)  # AS: 単位

            # AT-AW: 送料・配送（数式があれば保持）
            row[45] = preserve_or_set(existing_row, 45, str(product.get("delivery_charge") or 0), old_row_num, new_row_num)  # AT: 個別送料
            row[46] = preserve_or_set(existing_row, 46, "", old_row_num, new_row_num)  # AU: クール便料金
            row[47] = preserve_or_set(existing_row, 47, "", old_row_num, new_row_num)  # AV: 重量(g)
            row[48] = preserve_or_set(existing_row, 48, "", old_row_num, new_row_num)  # AW: 配送不要

            # AX-BA: 商品説明（数式があれば保持）
            row[49] = preserve_or_set(existing_row, 49, product.get("expl", "") or "", old_row_num, new_row_num)  # AX: 商品説明
            row[50] = preserve_or_set(existing_row, 50, product.get("simple_expl", "") or "", old_row_num, new_row_num)  # AY: 簡易説明
            row[51] = preserve_or_set(existing_row, 51, "", old_row_num, new_row_num)  # AZ: スマホ説明
            row[52] = preserve_or_set(existing_row, 52, "", old_row_num, new_row_num)  # BA: 備考

            # BB-BK: 画像（数式があれば保持）
            row[53] = preserve_or_set(existing_row, 53, image_url, old_row_num, new_row_num)  # BB: メイン画像URL
            row[54] = preserve_or_set(existing_row, 54, product.get("thumbnail_image_url", "") or "", old_row_num, new_row_num)  # BC: サムネイルURL
            for i, url in enumerate(image_urls[:8]):
                row[55 + i] = preserve_or_set(existing_row, 55 + i, url, old_row_num, new_row_num)  # BD-BK: 画像URL1-8

            # BL-BU: SEO、フラグ、掲載期間（数式があれば保持）
            for i in range(63, 73):
                default_val = str(existing_row[i]) if len(existing_row) > i and existing_row[i] else ""
                row[i] = preserve_or_set(existing_row, i, default_val, old_row_num, new_row_num)

            # BV-BZ: 更新制御（既存の設定を保持、数式があれば保持）
            bv_default = str(existing_row[73]) if len(existing_row) > 73 and existing_row[73] else "OFF"
            row[73] = preserve_or_set(existing_row, 73, bv_default, old_row_num, new_row_num)  # BV: 価格更新ON/OFF
            bw_default = str(existing_row[74]) if len(existing_row) > 74 and existing_row[74] else "OFF"
            row[74] = preserve_or_set(existing_row, 74, bw_default, old_row_num, new_row_num)  # BW: 在庫連動ON/OFF
            bx_default = str(existing_row[75]) if len(existing_row) > 75 and existing_row[75] else "変更しない"
            row[75] = preserve_or_set(existing_row, 75, bx_default, old_row_num, new_row_num)  # BX: 表示連動
            row[76] = preserve_or_set(existing_row, 76, "ダウンロード済", old_row_num, new_row_num, preserve_existing=False)  # BY: 同期ステータス
            row[77] = preserve_or_set(existing_row, 77, now, old_row_num, new_row_num, preserve_existing=False)  # BZ: 同期日時

            # CA-CB: システム情報（数式があれば保持、APIから取得）
            make_date = product.get("make_date", "")
            update_date = product.get("update_date", "")
            row[78] = preserve_or_set(existing_row, 78, make_date if make_date else "", old_row_num, new_row_num, preserve_existing=False)  # CA: 商品作成日時
            row[79] = preserve_or_set(existing_row, 79, update_date if update_date else "", old_row_num, new_row_num, preserve_existing=False)  # CB: 商品更新日時

            # 既存商品か新規商品かで振り分け
            if is_existing:
                update_rows[existing_row_map[product_id]] = row
            else:
                new_rows.append(row)

        # バッチ書き込み（API呼び出し回数を最小化）
        updated_count = 0
        added_count = 0

        # 既存行の更新（batch_updateで一括処理）
        if update_rows:
            logger.info(f"既存商品を更新中: {len(update_rows)}件")
            batch_data = []
            for row_num, row_data in update_rows.items():
                sheet_row = row_num + 1  # 1-indexed（ヘッダー含む）
                batch_data.append({
                    'range': f'A{sheet_row}:CB{sheet_row}',
                    'values': [row_data]
                })
            # batch_updateで一括更新（1回のAPI呼び出し）
            sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
            updated_count = len(update_rows)
            logger.info(f"既存商品の更新完了: {updated_count}件")

        # 新規行の追加（末尾に一括追加）
        if new_rows:
            start_row = max_existing_row + 2  # ヘッダー含む
            end_row = start_row + len(new_rows) - 1
            logger.info(f"新規商品を追加中: {len(new_rows)}件 (行{start_row}〜{end_row})")
            sheet.update(values=new_rows, range_name=f'A{start_row}:CB{end_row}', value_input_option='USER_ENTERED')
            added_count = len(new_rows)
            logger.info(f"新規商品の追加完了: {added_count}件")

        logger.info(f"シート更新完了: 更新{updated_count}件, 追加{added_count}件")

    except Exception as e:
        logger.error(f"シート書き込みエラー: {e}")
        sys.exit(1)

    logger.info("=== カラーミー商品ダウンロード完了 ===")


if __name__ == "__main__":
    main()
