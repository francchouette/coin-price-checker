"""
カラーミー商品ダウンロードスクリプト

カラーミーAPIから全商品を取得し、新カラーミー商品管理シートに書き込む。
F列（仕入れ先商品URL）がある場合は、価格を自動取得してL列に反映する。
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
                # Wiseが取得できない場合は一般レートで代用（main.pyと同じロジック）
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
        # ※2025-12: 5列追加（P-T: 製造国〜発行数）により、CC→CH列に変更
        # ※2025-12: カテゴリー・グループ名称2列追加により、CH→CJ列に変更（86列→88列）
        existing_formulas = sheet.get(f'A1:CJ{len(existing) + 1}', value_render_option='FORMULA')

        existing_data = {}  # 商品ID -> 既存行データのマッピング（数式含む）
        existing_row_map = {}  # 商品ID -> 行番号のマッピング（1-indexed、ヘッダー除く）
        max_existing_row = 1  # 既存データの最大行番号
        if len(existing) > 1:
            logger.info(f"既存データ行数: {len(existing)}行, 数式データ行数: {len(existing_formulas) if existing_formulas else 0}行")
            for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
                # ※2025-12: 操作項目列（C-F）追加により+4シフト: C→G(6)
                if len(row) > 6 and row[6]:  # G列: カラーミー商品ID
                    try:
                        pid = int(row[6])
                        # 数式データと値データをマージ
                        # 数式データがあればそれを優先、なければ値データを使用
                        # ※2025-12: 5列追加により 81列→86列
                        if existing_formulas and row_idx < len(existing_formulas):
                            formula_row = list(existing_formulas[row_idx])
                            # 数式データが短い場合は値データで補完（88列まで）
                            while len(formula_row) < 88:
                                if len(row) > len(formula_row):
                                    formula_row.append(row[len(formula_row)])
                                else:
                                    formula_row.append("")
                            existing_data[pid] = formula_row
                        else:
                            # 値データを使用（88列まで拡張）
                            value_row = list(row)
                            while len(value_row) < 88:
                                value_row.append("")
                            existing_data[pid] = value_row
                        existing_row_map[pid] = row_idx
                        max_existing_row = max(max_existing_row, row_idx)

                        # デバッグ: 最初の3件のみJ,S,T,W,Y-AB列の内容を確認
                        # ※2025-12: 操作項目列（C-F）追加により+4シフト
                        if len(existing_data) <= 3:
                            r = existing_data[pid]
                            logger.info(f"  既存データ 商品ID {pid}: J={r[9][:20] if len(r) > 9 and r[9] else ''}, S={r[18] if len(r) > 18 else ''}, T={r[19] if len(r) > 19 else ''}, W={r[22] if len(r) > 22 else ''}, Y-AB={r[24:28] if len(r) > 27 else ''}")
                    except ValueError:
                        pass
            logger.info(f"既存データを取得: {len(existing_data)}件（数式を保持）")
            # 注意: 既存データをクリアしない - 各行を個別に更新する

        # 既存データからY列（取引通貨）とZ列（為替種類）を収集
        # ※数式の場合は値を取得するためexistingから取得
        # ※2025-12: 5列追加（P-T: 製造国〜発行数）により T→Y(24), U→Z(25)
        currency_exchange_types = {}  # 通貨 -> 為替種類
        for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
            if len(row) > 25:
                currency = row[24].strip().upper() if len(row) > 24 and row[24] else ""
                exchange_type = row[25].strip() if len(row) > 25 and row[25] else "クレカ"
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
        # ※J列はVLOOKUP数式の場合があるため、値として取得したexistingを使用
        # ※2025-12: 操作項目列（C-F）追加により+4シフト: F→J(9), C→G(6)
        price_data = {}
        url_to_pid = {}  # URL -> 商品IDのマッピング（スクレイピング結果の紐付け用）
        if args.fetch_prices and existing_data:
            # J列（仕入れ先商品URL）を収集 - 値として取得したexistingから
            urls_to_fetch = []
            empty_url_count = 0
            formula_url_count = 0
            for row_idx, row in enumerate(existing[1:], start=1):  # ヘッダーをスキップ
                if len(row) > 9 and row[9]:  # J列: 仕入れ先商品URL（計算後の値）
                    url = row[9].strip()
                    if url.startswith("http"):
                        urls_to_fetch.append(url)
                        # 商品IDとの紐付け
                        if len(row) > 6 and row[6]:
                            try:
                                pid = int(row[6])
                                url_to_pid[url] = pid
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

                # スクレイピング結果から新しい通貨を収集し、為替レートを追加取得
                new_currencies = set()
                for scraped_with_extras in price_data.values():
                    scraped = scraped_with_extras.scraped_data
                    if not scraped.error and scraped.currency:
                        currency = scraped.currency.upper()
                        if currency != "JPY" and currency not in currency_exchange_types:
                            new_currencies.add(currency)

                if new_currencies:
                    logger.info(f"スクレイピング結果から新規通貨を検出: {new_currencies}")
                    # 新規通貨の為替レートを取得（デフォルトはクレカ）
                    new_currency_types = {c: "クレカ" for c in new_currencies}
                    new_rates = fetch_exchange_rates(list(new_currencies), new_currency_types)
                    exchange_rates.update(new_rates)
                    logger.info(f"新規通貨の為替レート取得完了: {len(new_rates)}件")

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

            # 88列分のデータを作成（A-CJ列: 操作項目を先頭に配置）
            # ※2025-12: C-F列に価格更新ON/OFF等の操作列を移動
            # ※2025-12: 5列追加（P-T: 製造国〜発行数）により 81列→86列
            # ※2025-12: カテゴリー・グループ名称2列追加により 86列→88列
            row = [""] * 88

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

            # A-F: 操作項目（既存値を保持、なければデフォルト値）
            row[0] = preserve_or_set(existing_row, 0, "変更なし", old_row_num, new_row_num)  # A: 同期モード
            row[1] = preserve_or_set(existing_row, 1, display_state, old_row_num, new_row_num)  # B: 掲載設定（既存値を保持）
            c_default = str(existing_row[2]) if len(existing_row) > 2 and existing_row[2] else "OFF"
            row[2] = preserve_or_set(existing_row, 2, c_default, old_row_num, new_row_num)  # C: 価格更新ON/OFF
            d_default = str(existing_row[3]) if len(existing_row) > 3 and existing_row[3] else "OFF"
            row[3] = preserve_or_set(existing_row, 3, d_default, old_row_num, new_row_num)  # D: 在庫連動ON/OFF
            e_default = str(existing_row[4]) if len(existing_row) > 4 and existing_row[4] else "OFF"
            row[4] = preserve_or_set(existing_row, 4, e_default, old_row_num, new_row_num)  # E: 表示連動
            row[5] = preserve_or_set(existing_row, 5, "ダウンロード済", old_row_num, new_row_num, preserve_existing=False)  # F: 同期ステータス

            # G-I: 識別情報
            row[6] = str(product_id)  # G: カラーミー商品ID
            row[7] = product.get("name", "")  # H: 商品名
            row[8] = f"https://ybx.jp/?pid={product_id}"  # I: カラーミー商品URL

            # J-O: 仕入れ先情報（VLOOKUPで商品仕入れ先一覧から取得）
            # 商品仕入れ先一覧: A=ID, B=商品名, C=URL(キー), D=サイト, E=最上位カテゴリ, F=親カテゴリ, G=子カテゴリ, H=製造国
            row[9] = preserve_or_set(existing_row, 9, "", old_row_num, new_row_num)   # J: 仕入れ先商品URL（手動入力）
            row[10] = preserve_or_set(existing_row, 10, f'=IFERROR(INDEX(商品仕入れ先一覧!$B:$B,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # K: 仕入れ先商品名 (B列をINDEX/MATCHで取得)
            row[11] = preserve_or_set(existing_row, 11, f'=IFERROR(INDEX(商品仕入れ先一覧!$D:$D,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # L: 仕入れ先サイト (D列)
            row[12] = preserve_or_set(existing_row, 12, f'=IFERROR(INDEX(商品仕入れ先一覧!$E:$E,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # M: 最上位カテゴリ (E列)
            row[13] = preserve_or_set(existing_row, 13, f'=IFERROR(INDEX(商品仕入れ先一覧!$F:$F,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # N: 親カテゴリ (F列)
            row[14] = preserve_or_set(existing_row, 14, f'=IFERROR(INDEX(商品仕入れ先一覧!$G:$G,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # O: 子カテゴリ (G列)

            # P-T: 新規追加5列（製造国、商品説明（英語）、仕様・スペック、発行年、発行数・限定数）
            # ※商品仕入れ先一覧シートからINDEX/MATCHで取得（J列=仕入れ先商品URLをキーに）
            # 商品仕入れ先一覧: H=製造国, AE=仕様・スペック, AF=商品説明（英語）, AH=発行年, AI=発行数・限定数
            row[15] = preserve_or_set(existing_row, 15, f'=IFERROR(INDEX(商品仕入れ先一覧!$H:$H,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # P: 製造国 (H列)
            row[16] = preserve_or_set(existing_row, 16, f'=IFERROR(INDEX(商品仕入れ先一覧!$AF:$AF,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # Q: 商品説明（英語） (AF列)
            row[17] = preserve_or_set(existing_row, 17, f'=IFERROR(INDEX(商品仕入れ先一覧!$AE:$AE,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # R: 仕様・スペック (AE列)
            row[18] = preserve_or_set(existing_row, 18, f'=IFERROR(INDEX(商品仕入れ先一覧!$AH:$AH,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # S: 発行年 (AH列)
            row[19] = preserve_or_set(existing_row, 19, f'=IFERROR(INDEX(商品仕入れ先一覧!$AI:$AI,MATCH($J{new_row_num},商品仕入れ先一覧!$C:$C,0)),"")', old_row_num, new_row_num)  # T: 発行数・限定数 (AI列)

            # U列: 仕入れ先在庫状況
            row[20] = preserve_or_set(existing_row, 20, "", old_row_num, new_row_num)  # U: 仕入れ先在庫状況

            # V-Y列: 価格情報（数式があれば保持、スクレイピング結果があれば更新）
            row[21] = preserve_or_set(existing_row, 21, "", old_row_num, new_row_num)  # V: 仕入れ先価格（現地通貨）
            row[22] = preserve_or_set(existing_row, 22, "", old_row_num, new_row_num)  # W: 前回仕入れ価格
            row[23] = preserve_or_set(existing_row, 23, "", old_row_num, new_row_num)  # X: 価格変動率
            row[24] = preserve_or_set(existing_row, 24, "", old_row_num, new_row_num)  # Y: 取引通貨

            # スクレイピング結果があり、かつ数式でない場合のみ更新
            # J列が数式（VLOOKUP等）の場合は、existingから計算後の値を取得
            supplier_url = ""
            if is_existing:
                row_idx = existing_row_map[product_id]
                if row_idx < len(existing) and len(existing[row_idx]) > 9:
                    supplier_url = existing[row_idx][9].strip() if existing[row_idx][9] else ""
            else:
                supplier_url_raw = row[9]
                supplier_url = str(supplier_url_raw).strip() if supplier_url_raw and not str(supplier_url_raw).startswith("=") else ""

            if supplier_url and supplier_url.startswith("http") and supplier_url in price_data:
                scraped_with_extras = price_data[supplier_url]
                scraped = scraped_with_extras.scraped_data
                if not scraped.error:
                    # P-T列: 新規追加5列の更新（スクレイピング結果から）
                    # P列(15): 製造国
                    if scraped_with_extras.location and not (row[15] and isinstance(row[15], str) and row[15].startswith("=")):
                        row[15] = scraped_with_extras.location
                    # Q列(16): 商品説明（英語）
                    if scraped_with_extras.description_en and not (row[16] and isinstance(row[16], str) and row[16].startswith("=")):
                        row[16] = scraped_with_extras.description_en
                    # R列(17): 仕様・スペック
                    if scraped_with_extras.specs and not (row[17] and isinstance(row[17], str) and row[17].startswith("=")):
                        row[17] = scraped_with_extras.specs
                    # S列(18): 発行年
                    if scraped_with_extras.mint_year and not (row[18] and isinstance(row[18], str) and row[18].startswith("=")):
                        row[18] = scraped_with_extras.mint_year
                    # T列(19): 発行数・限定数
                    if scraped_with_extras.mintage and not (row[19] and isinstance(row[19], str) and row[19].startswith("=")):
                        row[19] = scraped_with_extras.mintage

                    # U列(20): 仕入れ先在庫状況を更新
                    if not (row[20] and isinstance(row[20], str) and row[20].startswith("=")):
                        row[20] = "In Stock" if scraped.in_stock else "Out of Stock"

                    # V列(21)が数式でない場合のみ更新
                    if not (row[21] and isinstance(row[21], str) and row[21].startswith("=")):
                        # 前回価格をW列(22)に保存（W列が数式でない場合）
                        if not (row[22] and isinstance(row[22], str) and row[22].startswith("=")):
                            prev_val = existing_row[21] if len(existing_row) > 21 else ""
                            row[22] = str(prev_val) if prev_val else ""
                        # 新価格をV列(21)に設定
                        row[21] = str(scraped.price)

                    # Y列(24): スクレイピング結果の通貨で常に更新（数式があっても値で上書き）
                    old_currency = row[24]
                    row[24] = scraped.currency
                    if old_currency != scraped.currency:
                        if old_currency and str(old_currency).startswith("="):
                            logger.info(f"  商品ID {product_id}: 通貨更新（数式を値に置換） -> {scraped.currency}")
                        else:
                            logger.info(f"  商品ID {product_id}: 通貨更新 {old_currency} -> {scraped.currency}")

                    # X列(23)が数式でない場合のみ価格変動率を計算
                    if not (row[23] and isinstance(row[23], str) and row[23].startswith("=")):
                        prev_price_raw = existing_row[21] if len(existing_row) > 21 else ""
                        prev_price_str = str(prev_price_raw) if prev_price_raw else ""
                        if prev_price_str and not prev_price_str.startswith("="):
                            try:
                                prev_price = float(prev_price_str)
                                if prev_price > 0:
                                    change_rate = ((scraped.price - prev_price) / prev_price) * 100
                                    row[23] = f"{change_rate:+.2f}%"
                            except ValueError:
                                pass

            # Z-AL: 価格計算（既存データを保持、数式は行番号を調整）
            # ※2025-12: 5列追加により U→Z(25)〜AG→AL(37)
            for i in range(25, 38):
                row[i] = preserve_or_set(existing_row, i, "", old_row_num, new_row_num)

            # AA列（為替レート）を自動更新（数式でない場合のみ）
            # ※2025-12: 5列追加により V→AA(26)
            if not (row[26] and isinstance(row[26], str) and row[26].startswith("=")):
                # Y列とZ列は数式の場合があるため、既存の値データからも取得を試みる
                currency_val = row[24]
                if currency_val and isinstance(currency_val, str) and currency_val.startswith("="):
                    row_idx = existing_row_map.get(product_id)
                    if row_idx and row_idx < len(existing) and len(existing[row_idx]) > 24:
                        currency_val = existing[row_idx][24]
                currency = currency_val.strip().upper() if currency_val else ""

                exchange_type_val = row[25]
                if exchange_type_val and isinstance(exchange_type_val, str) and exchange_type_val.startswith("="):
                    row_idx = existing_row_map.get(product_id)
                    if row_idx and row_idx < len(existing) and len(existing[row_idx]) > 25:
                        exchange_type_val = existing[row_idx][25]
                exchange_type = exchange_type_val.strip() if exchange_type_val else "クレカ"

                if currency == "JPY":
                    row[26] = "1"  # AA: 為替レート（JPYは1）
                elif currency:
                    rate_key = f"{currency}_{exchange_type}"
                    if rate_key in exchange_rates:
                        row[26] = str(round(exchange_rates[rate_key], 4))  # AA: 為替レート
                        logger.info(f"  商品ID {product_id}: 為替レート更新 {rate_key} = {row[26]}")
                    else:
                        logger.warning(f"  商品ID {product_id}: 為替レート見つからず {rate_key}, 利用可能: {list(exchange_rates.keys())}")

            # AM-AR: カラーミー価格情報（数式があれば保持、値はAPIから取得）
            # ※2025-12: 5列追加により AH→AM(38)
            row[38] = preserve_or_set(existing_row, 38, str(product.get("sales_price") or product.get("price") or 0), old_row_num, new_row_num, preserve_existing=False)  # AM: 販売価格
            row[39] = preserve_or_set(existing_row, 39, str(product.get("price") or 0), old_row_num, new_row_num, preserve_existing=False)  # AN: 定価
            row[40] = preserve_or_set(existing_row, 40, str(product.get("members_price") or 0), old_row_num, new_row_num, preserve_existing=False)  # AO: 会員価格
            row[41] = preserve_or_set(existing_row, 41, str(product.get("cost") or 0), old_row_num, new_row_num, preserve_existing=False)  # AP: 原価
            row[42] = preserve_or_set(existing_row, 42, "", old_row_num, new_row_num)  # AQ: 消費税込販売価格
            row[43] = preserve_or_set(existing_row, 43, "", old_row_num, new_row_num)  # AR: 消費税額

            # AS-AX: カテゴリー・グループ（6列: ID・名称）
            # ※2025-12: 5列追加により AN→AS(44)
            # ※2025-12: カテゴリー・グループ名称2列追加（AS-AX: 6列）
            row[44] = preserve_or_set(existing_row, 44, str(category_id_big) if category_id_big else "", old_row_num, new_row_num)  # AS: 大カテゴリーID
            row[45] = preserve_or_set(existing_row, 45, "", old_row_num, new_row_num)  # AT: 大カテゴリー名称（API取得不可、手動または別途設定）
            row[46] = preserve_or_set(existing_row, 46, str(category_id_small) if category_id_small else "", old_row_num, new_row_num)  # AU: 小カテゴリーID
            row[47] = preserve_or_set(existing_row, 47, "", old_row_num, new_row_num)  # AV: 小カテゴリー名称（API取得不可、手動または別途設定）
            row[48] = preserve_or_set(existing_row, 48, group_ids_str, old_row_num, new_row_num)  # AW: グループID
            row[49] = preserve_or_set(existing_row, 49, "", old_row_num, new_row_num)  # AX: グループ名（API取得不可、手動または別途設定）

            # AY: 型番（1列）
            row[50] = preserve_or_set(existing_row, 50, product.get("model_number", "") or "", old_row_num, new_row_num)  # AY: 型番

            # AZ-BF: 在庫管理（7列）
            # ※2025-12: カテゴリー名称2列追加により AW→AZ(51)
            row[51] = preserve_or_set(existing_row, 51, str(product.get("stocks") or 0), old_row_num, new_row_num, preserve_existing=False)  # AZ: 在庫数
            row[52] = preserve_or_set(existing_row, 52, "する" if product.get("stock_managed", True) else "しない", old_row_num, new_row_num, preserve_existing=False)  # BA: 在庫管理
            row[53] = preserve_or_set(existing_row, 53, str(product.get("few_num") or 0), old_row_num, new_row_num, preserve_existing=False)  # BB: 残りわずか数
            soldout_display = product.get("soldout_display", True)
            row[54] = preserve_or_set(existing_row, 54, "表示" if soldout_display else "非表示", old_row_num, new_row_num, preserve_existing=False)  # BC: 売切れ表示
            row[55] = preserve_or_set(existing_row, 55, str(product.get("min_num") or 1), old_row_num, new_row_num, preserve_existing=False)  # BD: 最小購入数
            row[56] = preserve_or_set(existing_row, 56, str(product.get("max_num") or 0), old_row_num, new_row_num, preserve_existing=False)  # BE: 最大購入数
            row[57] = preserve_or_set(existing_row, 57, product.get("unit", "") or "", old_row_num, new_row_num, preserve_existing=False)  # BF: 単位

            # BG-BJ: 送料・配送（4列）
            # ※2025-12: カテゴリー名称2列追加により BD→BG(58)
            row[58] = preserve_or_set(existing_row, 58, str(product.get("delivery_charge") or 0), old_row_num, new_row_num)  # BG: 個別送料
            row[59] = preserve_or_set(existing_row, 59, "", old_row_num, new_row_num)  # BH: クール便料金
            row[60] = preserve_or_set(existing_row, 60, "", old_row_num, new_row_num)  # BI: 重量(g)
            row[61] = preserve_or_set(existing_row, 61, "", old_row_num, new_row_num)  # BJ: 配送不要

            # BK-BN: 商品説明（4列）
            # ※2025-12: カテゴリー名称2列追加により BH→BK(62)
            row[62] = preserve_or_set(existing_row, 62, product.get("expl", "") or "", old_row_num, new_row_num)  # BK: 商品説明
            row[63] = preserve_or_set(existing_row, 63, product.get("simple_expl", "") or "", old_row_num, new_row_num)  # BL: 簡易説明
            row[64] = preserve_or_set(existing_row, 64, "", old_row_num, new_row_num)  # BM: スマホ説明
            row[65] = preserve_or_set(existing_row, 65, "", old_row_num, new_row_num)  # BN: 備考

            # BO-BX: 画像URL1〜10（10列）
            # ※2025-12: カテゴリー名称2列追加により BL→BO(66)
            for i in range(10):
                img_url = image_urls[i] if i < len(image_urls) else ""
                row[66 + i] = preserve_or_set(existing_row, 66 + i, img_url, old_row_num, new_row_num)  # BO-BX: 画像URL1-10

            # BY-CF: SEO、フラグ、掲載期間（8列）
            # ※2025-12: カテゴリー名称2列追加により BV→BY(76)
            for i in range(76, 86):
                default_val = str(existing_row[i]) if len(existing_row) > i and existing_row[i] else ""
                row[i] = preserve_or_set(existing_row, i, default_val, old_row_num, new_row_num)

            # CI-CJ: システム情報（2列）
            # ※2025-12: カテゴリー名称2列追加により CF→CI(86)
            row[86] = preserve_or_set(existing_row, 86, now, old_row_num, new_row_num, preserve_existing=False)  # CI: 同期日時
            make_date = product.get("make_date", "")
            row[87] = preserve_or_set(existing_row, 87, make_date if make_date else "", old_row_num, new_row_num, preserve_existing=False)  # CJ: 商品作成日時

            # 既存商品か新規商品かで振り分け
            if is_existing:
                update_rows[existing_row_map[product_id]] = row
            else:
                new_rows.append(row)

        # バッチ書き込み（API呼び出し回数を最小化）
        updated_count = 0
        added_count = 0

        # 既存行の更新（batch_updateで一括処理）
        # ※2025-12: 5列追加により CC→CH列に変更
        # ※2025-12: カテゴリー名称2列追加により CH→CJ列に変更（86列→88列）
        if update_rows:
            logger.info(f"既存商品を更新中: {len(update_rows)}件")
            batch_data = []
            for row_num, row_data in update_rows.items():
                sheet_row = row_num + 1  # 1-indexed（ヘッダー含む）
                batch_data.append({
                    'range': f'A{sheet_row}:CJ{sheet_row}',
                    'values': [row_data]
                })
            # batch_updateで一括更新（1回のAPI呼び出し）
            sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
            updated_count = len(update_rows)
            logger.info(f"既存商品の更新完了: {updated_count}件")

        # 新規行の追加（末尾に一括追加）
        # ※2025-12: 5列追加により CC→CH列に変更
        # ※2025-12: カテゴリー名称2列追加により CH→CJ列に変更（86列→88列）
        if new_rows:
            start_row = max_existing_row + 2  # ヘッダー含む
            end_row = start_row + len(new_rows) - 1
            logger.info(f"新規商品を追加中: {len(new_rows)}件 (行{start_row}〜{end_row})")
            sheet.update(values=new_rows, range_name=f'A{start_row}:CJ{end_row}', value_input_option='USER_ENTERED')
            added_count = len(new_rows)
            logger.info(f"新規商品の追加完了: {added_count}件")

        logger.info(f"シート更新完了: 更新{updated_count}件, 追加{added_count}件")

    except Exception as e:
        logger.error(f"シート書き込みエラー: {e}")
        sys.exit(1)

    logger.info("=== カラーミー商品ダウンロード完了 ===")


if __name__ == "__main__":
    main()
