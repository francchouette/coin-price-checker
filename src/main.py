"""
メインエントリーポイント

価格追跡プログラムの全体フローを制御する。
カラーミー商品管理シートを基準にスクレイピングを行う。
"""

import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

from .config import Config
from .spreadsheet import SpreadsheetClient, PriceRecord
from .scraper import ScraperManager, ScrapeTarget, detect_shop_from_url
from .shops import ScrapedData
from .notifier import (
    SlackNotifier,
    GoogleChatNotifier,
    ConsoleNotifier,
    PriceAlert,
    calculate_change_rate,
    check_alert_threshold,
)
from .exchange_rate import ExchangeRateClient, WiseRateClient
from .colorme import ColorMeClient, ColorMeProduct

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def run():
    """
    メイン処理を実行する

    カラーミー商品管理シートを基準にスクレイピングを行う。
    トラッキング対象シートは使用しない。
    """
    logger.info("=" * 60)
    logger.info("価格追跡プログラムを開始します")
    logger.info("=" * 60)

    start_time = datetime.now()

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        logger.error("設定エラーのため終了します")
        sys.exit(1)

    # スプレッドシートに接続
    logger.info("スプレッドシートに接続中...")
    sheet_client = SpreadsheetClient()
    if not sheet_client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    # カラーミー商品一覧をシートに同期
    if Config.is_colorme_enabled():
        sync_colorme_to_sheet(sheet_client)

    # カラーミー商品管理シートから対象を取得
    colorme_products = sheet_client.get_colorme_products()
    if not colorme_products:
        logger.warning("カラーミー商品管理シートに対象商品がありません")
        sys.exit(0)

    # 重複を除いたURLリストを作成
    unique_urls = list(set(p.source_url for p in colorme_products if p.source_url))

    # URL → 指定通貨のマッピング（L列で指定された通貨を使用）
    url_to_currency = {}
    for p in colorme_products:
        if p.source_url and p.source_currency:
            url_to_currency[p.source_url] = p.source_currency

    logger.info(f"スクレイピング対象URL: {len(unique_urls)}件（カラーミー商品: {len(colorme_products)}件）")

    # アラート閾値を取得
    alert_threshold = sheet_client.get_alert_threshold()
    logger.info(f"アラート閾値: ±{alert_threshold}%")

    # 直近の価格と在庫状況を事前に取得
    previous_prices = sheet_client.get_latest_prices(unique_urls)
    previous_stock = sheet_client.get_latest_stock_status(unique_urls)
    logger.info(f"直近価格データ: {len(previous_prices)}件")
    logger.info(f"直近在庫データ: {len(previous_stock)}件")

    # スクレイピング実行
    logger.info("スクレイピングを開始します...")
    scraped_results = scrape_urls(unique_urls)

    # 結果を処理
    logger.info("結果を処理中...")
    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    price_records = []
    alerts = []

    # URL -> スクレイピング結果の辞書を作成
    url_to_result = {url: result for url, result in zip(unique_urls, scraped_results)}

    for url, result in url_to_result.items():
        if result.error:
            logger.warning(f"スクレイピング失敗: {url} - {result.error}")
            continue

        # 価格変動を計算
        previous_price = previous_prices.get(url)
        change_rate = 0.0
        if previous_price is not None and previous_price > 0:
            change_rate = calculate_change_rate(result.price, previous_price)

        # ショップ名をURLから推定
        shop_name = detect_shop_from_url(url)

        # 通貨：シートで指定された通貨を優先、なければ自動検出した通貨を使用
        specified_currency = url_to_currency.get(url)
        currency = specified_currency if specified_currency else result.currency

        # 指定通貨と検出通貨が異なる場合は警告
        if specified_currency and result.currency and specified_currency != result.currency:
            logger.warning(
                f"通貨不一致: {result.product_name} - "
                f"指定={specified_currency}, 検出={result.currency} → 指定通貨を使用"
            )

        # 価格レコードを作成（在庫状況・差分を含む）
        record = PriceRecord(
            timestamp=timestamp,
            shop_name=shop_name,
            product_name=result.product_name,
            price=result.price,
            currency=currency,
            previous_price=previous_price if previous_price else 0.0,
            change_rate=change_rate,
            in_stock=result.in_stock,
            url=url
        )
        price_records.append(record)

        # ログ出力
        stock_status = "In Stock" if result.in_stock else "Out of Stock"
        if not result.in_stock:
            logger.info(f"取得: {result.product_name} - [{stock_status}]")
        elif previous_price:
            logger.info(
                f"取得: {result.product_name} - {currency} {result.price:,.2f} "
                f"(前回: {previous_price:,.2f}, 変動: {change_rate:+.2f}%) [{stock_status}]"
            )
        else:
            logger.info(
                f"取得: {result.product_name} - {currency} {result.price:,.2f} "
                f"(初回取得) [{stock_status}]"
            )

        # 価格変動アラートをチェック
        if previous_price is not None and check_alert_threshold(change_rate, alert_threshold):
            alert = PriceAlert(
                product_name=result.product_name,
                shop_name=shop_name,
                current_price=result.price,
                previous_price=previous_price,
                change_rate=change_rate,
                currency=result.currency,
                url=url,
                timestamp=timestamp,
                alert_type="price",
                in_stock=result.in_stock
            )
            alerts.append(alert)
            logger.info(
                f"価格アラート検出: {result.product_name} "
                f"({previous_price:,.2f} → {result.price:,.2f}, {change_rate:+.2f}%)"
            )

        # 在庫切れアラートをチェック（前回In Stock → 今回Out of Stock）
        was_in_stock = previous_stock.get(url, True)
        if was_in_stock and not result.in_stock:
            alert = PriceAlert(
                product_name=result.product_name,
                shop_name=shop_name,
                current_price=result.price,
                previous_price=previous_price if previous_price else result.price,
                change_rate=0.0,
                currency=result.currency,
                url=url,
                timestamp=timestamp,
                alert_type="stock",
                in_stock=False
            )
            alerts.append(alert)
            logger.info(f"在庫切れアラート検出: {result.product_name}")

        # 在庫復活アラートをチェック（前回Out of Stock → 今回In Stock）
        if not was_in_stock and result.in_stock:
            alert = PriceAlert(
                product_name=result.product_name,
                shop_name=shop_name,
                current_price=result.price,
                previous_price=previous_price if previous_price else result.price,
                change_rate=0.0,
                currency=result.currency,
                url=url,
                timestamp=timestamp,
                alert_type="stock_restored",
                in_stock=True
            )
            alerts.append(alert)
            logger.info(f"在庫復活アラート検出: {result.product_name}")

    # 価格履歴を保存
    if price_records:
        logger.info(f"価格履歴を保存中... ({len(price_records)}件)")
        if sheet_client.save_price_records(price_records):
            logger.info("価格履歴を保存しました")
        else:
            logger.error("価格履歴の保存に失敗しました")

    # アラートを送信
    if alerts:
        logger.info(f"アラートを送信中... ({len(alerts)}件)")
        send_alerts(alerts)
    else:
        logger.info("アラートはありません")

    # カラーミー価格更新
    if Config.is_colorme_enabled():
        logger.info("=" * 60)
        logger.info("カラーミー価格更新を開始します")
        logger.info("  → G列「CM-価格更新」がONの商品のみ更新されます")
        update_colorme_prices(sheet_client, colorme_products, price_records, dry_run=False)
    else:
        logger.info("カラーミー連携は無効です（COLORME_ACCESS_TOKEN未設定）")

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info(f"価格追跡プログラムが完了しました（{elapsed:.1f}秒）")
    logger.info(f"  - 処理件数: {len(price_records)}件")
    logger.info(f"  - アラート: {len(alerts)}件")
    logger.info("=" * 60)


def sync_colorme_to_sheet(sheet_client: SpreadsheetClient):
    """
    カラーミーAPIから商品一覧を取得し、シートに同期する

    - 新規商品は追加（取得元URLは空欄）
    - 既存商品は商品名を更新
    - シートにあってAPIにない商品はそのまま保持

    Args:
        sheet_client: スプレッドシートクライアント
    """
    logger.info("カラーミー商品一覧をシートに同期中...")

    colorme_client = ColorMeClient()
    api_products = colorme_client.get_all_products()

    if not api_products:
        logger.warning("カラーミーAPIから商品を取得できませんでした")
        return

    result = sheet_client.sync_colorme_products(api_products)

    logger.info(
        f"カラーミー商品同期完了: "
        f"追加 {result['added']}件, "
        f"更新 {result['updated']}件, "
        f"変更なし {result['unchanged']}件"
    )


def scrape_urls(urls: list[str]) -> list[ScrapedData]:
    """
    URLリストをスクレイピングする

    Args:
        urls: スクレイピング対象のURLリスト

    Returns:
        list[ScrapedData]: スクレイピング結果のリスト
    """
    scrape_targets_list = []

    for url in urls:
        # ショップ名はURLから推定
        shop_name = detect_shop_from_url(url)

        scrape_targets_list.append(ScrapeTarget(
            shop_name=shop_name,
            url=url,
            product_name_hint=""
        ))

    with ScraperManager() as manager:
        return manager.scrape_all(scrape_targets_list)


def update_colorme_prices(
    sheet_client: SpreadsheetClient,
    colorme_products: list[ColorMeProduct],
    price_records: list[PriceRecord],
    dry_run: bool = True
):
    """
    カラーミーショップの価格・在庫・表示状態を更新する

    Args:
        sheet_client: スプレッドシートクライアント
        colorme_products: カラーミー商品リスト
        price_records: 今回取得した価格レコードのリスト
        dry_run: ドライランモード（Trueの場合は実際に更新しない）
    """
    if not colorme_products:
        logger.info("カラーミー商品管理シートに対象商品がありません")
        return

    logger.info(f"カラーミー更新対象: {len(colorme_products)}件")

    # 今回取得した価格データをURL -> 価格/在庫/変動率の辞書に変換
    source_prices = {}
    source_stock = {}
    source_change_rates = {}
    for record in price_records:
        source_prices[record.url] = record.price
        source_stock[record.url] = record.in_stock
        source_change_rates[record.url] = record.change_rate

    if not source_prices:
        logger.warning("価格データがありません")
        return

    logger.info(f"取得元価格データ: {len(source_prices)}件")

    # 必要な通貨を特定
    currencies_needed = set()
    for product in colorme_products:
        currencies_needed.add(product.source_currency)

    logger.info(f"必要な通貨: {currencies_needed}")

    # 為替レートを取得（通貨別・種類別）
    exchange_client = ExchangeRateClient()
    wise_client = WiseRateClient()

    if not exchange_client.fetch_rates():
        logger.error("為替レートの取得に失敗しました")
        return

    # 為替レートを通貨・種類別に取得
    exchange_rates = {}
    for currency in currencies_needed:
        # クレカレート
        credit_rate = exchange_client.get_credit_card_rate(currency, "JPY")
        if credit_rate:
            exchange_rates[f"{currency}_クレカ"] = credit_rate
            logger.info(f"クレカレート: 1 {currency} = {credit_rate:.2f} JPY")

        # Wiseレート
        wise_rate = wise_client.get_rate(currency, "JPY")
        if wise_rate:
            exchange_rates[f"{currency}_Wise"] = wise_rate
            logger.info(f"Wiseレート: 1 {currency} = {wise_rate:.2f} JPY")

        # Wiseが取得できない場合は一般レートで代用
        if not wise_rate:
            general_rate = exchange_client.get_rate(currency, "JPY")
            if general_rate:
                exchange_rates[f"{currency}_Wise"] = general_rate
                logger.info(f"Wiseレート（代替）: 1 {currency} = {general_rate:.2f} JPY")

    # カラーミー商品を更新
    colorme_client = ColorMeClient(dry_run=dry_run)
    result = colorme_client.update_products_batch(
        colorme_products,
        source_prices,
        source_stock,
        exchange_rates,
        source_change_rates
    )

    logger.info(
        f"カラーミー更新完了: "
        f"成功 {result['success']}件, "
        f"失敗 {result['failed']}件, "
        f"スキップ {result['skipped']}件"
    )

    # 計算結果をスプレッドシートに保存
    if result.get("calc_results"):
        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        sheet_client.update_colorme_calc_results(result["calc_results"], timestamp)


def send_alerts(alerts: list[PriceAlert]):
    """
    アラートを送信する

    Args:
        alerts: 価格アラートのリスト
    """
    sent = False

    # Google Chatが設定されている場合はGoogle Chatに送信
    if Config.is_google_chat_enabled():
        notifier = GoogleChatNotifier()
        if notifier.send_batch(alerts):
            logger.info("Google Chatにアラートを送信しました")
            sent = True
        else:
            logger.warning("Google Chatへのアラート送信に失敗しました")

    # Slackが設定されている場合はSlackに送信
    if Config.is_slack_enabled():
        notifier = SlackNotifier()
        if notifier.send_batch(alerts):
            logger.info("Slackにアラートを送信しました")
            sent = True
        else:
            logger.warning("Slackへのアラート送信に失敗しました")

    # どちらも設定されていない場合はコンソールに出力
    if not sent:
        logger.info("通知サービス未設定のため、コンソールに出力します")
        notifier = ConsoleNotifier()
        notifier.send_batch(alerts)


# ========================================
# カラーミー商品管理同期機能
# ========================================

def sync_colorme_products_full():
    """
    カラーミー商品管理の完全同期を実行する

    同期モード（AB列）に応じて以下の処理を行う:
    - 取得のみ: カラーミーAPIから情報を取得してシートに反映
    - 更新: シートの内容でカラーミーAPIを更新
    - 新規登録: 新商品をカラーミーAPIに登録
    """
    logger.info("=" * 60)
    logger.info("カラーミー商品管理同期を開始します")
    logger.info("=" * 60)

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        logger.error("設定エラーのため終了します")
        sys.exit(1)

    if not Config.is_colorme_enabled():
        logger.error("カラーミーAPIトークンが設定されていません")
        sys.exit(1)

    # スプレッドシートに接続
    logger.info("スプレッドシートに接続中...")
    sheet_client = SpreadsheetClient()
    if not sheet_client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    # 拡張列のヘッダーを確認・追加
    sheet_client.ensure_colorme_headers()

    # カラーミー商品管理シートから対象を取得
    products = sheet_client.get_colorme_products()
    if not products:
        logger.warning("カラーミー商品管理シートに対象商品がありません")
        sys.exit(0)

    logger.info(f"対象商品: {len(products)}件")

    # カラーミークライアント
    colorme_client = ColorMeClient()

    # 現在時刻
    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    # 同期モード別に処理
    fetch_products = [p for p in products if p.sync_mode == "取得のみ"]
    update_products = [p for p in products if p.sync_mode == "更新"]
    create_products = [p for p in products if p.sync_mode == "新規登録"]

    logger.info(f"取得のみ: {len(fetch_products)}件")
    logger.info(f"更新: {len(update_products)}件")
    logger.info(f"新規登録: {len(create_products)}件")

    # 1. 取得のみ: カラーミーAPIから情報を取得してシートに反映
    if fetch_products:
        logger.info("-" * 40)
        logger.info("【取得のみ】処理開始")
        fetch_from_colorme(sheet_client, colorme_client, fetch_products, timestamp)

    # 2. 更新: シートの内容でカラーミーAPIを更新
    if update_products:
        logger.info("-" * 40)
        logger.info("【更新】処理開始")
        update_to_colorme(sheet_client, colorme_client, update_products, timestamp)

    # 3. 新規登録: 新商品をカラーミーAPIに登録
    if create_products:
        logger.info("-" * 40)
        logger.info("【新規登録】処理開始")
        create_in_colorme(sheet_client, colorme_client, create_products, timestamp)

    logger.info("=" * 60)
    logger.info("カラーミー商品管理同期が完了しました")
    logger.info("=" * 60)


def fetch_from_colorme(
    sheet_client: SpreadsheetClient,
    colorme_client: ColorMeClient,
    products: list[ColorMeProduct],
    timestamp: str
):
    """
    カラーミーAPIから商品情報を取得してシートに反映する

    Args:
        sheet_client: スプレッドシートクライアント
        colorme_client: カラーミーAPIクライアント
        products: 取得対象の商品リスト
        timestamp: 更新日時
    """
    fetched_products = []

    for product in products:
        logger.info(f"取得中: {product.name} (ID: {product.product_id})")

        api_product = colorme_client.get_product_full(product.product_id)
        if api_product:
            api_product.sync_status = "成功"
            api_product.sync_datetime = timestamp
            fetched_products.append(api_product)
            logger.info(f"  → 成功")
        else:
            product.sync_status = "エラー: 取得失敗"
            product.sync_datetime = timestamp
            fetched_products.append(product)
            logger.warning(f"  → 失敗")

    # シートに反映
    if fetched_products:
        sheet_client.update_colorme_full_data(fetched_products, timestamp)

    logger.info(f"取得完了: {len([p for p in fetched_products if p.sync_status == '成功'])}件成功")


def update_to_colorme(
    sheet_client: SpreadsheetClient,
    colorme_client: ColorMeClient,
    products: list[ColorMeProduct],
    timestamp: str
):
    """
    シートの内容でカラーミーAPIを更新する

    Args:
        sheet_client: スプレッドシートクライアント
        colorme_client: カラーミーAPIクライアント
        products: 更新対象の商品リスト
        timestamp: 更新日時
    """
    success_count = 0
    failed_count = 0

    for product in products:
        logger.info(f"更新中: {product.name} (ID: {product.product_id})")

        success, error = colorme_client.update_product_full(product)

        if success:
            status = "成功"
            success_count += 1
            logger.info(f"  → 成功")

            # 画像アップロード（URLが指定されている場合）
            if any(product.image_urls):
                img_success, img_error = colorme_client.upload_product_images(
                    product.product_id,
                    product.image_urls
                )
                if not img_success:
                    status = f"成功（画像エラー: {img_error}）"
                    logger.warning(f"  → 画像アップロードエラー: {img_error}")
        else:
            status = f"エラー: {error}"
            failed_count += 1
            logger.warning(f"  → 失敗: {error}")

        # ステータス更新
        sheet_client.update_colorme_sync_status(product.product_id, status, timestamp)

    logger.info(f"更新完了: 成功 {success_count}件, 失敗 {failed_count}件")


def create_in_colorme(
    sheet_client: SpreadsheetClient,
    colorme_client: ColorMeClient,
    products: list[ColorMeProduct],
    timestamp: str
):
    """
    新商品をカラーミーAPIに登録する

    Args:
        sheet_client: スプレッドシートクライアント
        colorme_client: カラーミーAPIクライアント
        products: 新規登録対象の商品リスト
        timestamp: 更新日時
    """
    success_count = 0
    failed_count = 0

    for product in products:
        # 既に商品IDがある場合はスキップ
        if product.product_id > 0:
            logger.warning(f"スキップ: {product.name} - 既に商品IDがあります (ID: {product.product_id})")
            sheet_client.update_colorme_sync_status(
                product.product_id,
                "スキップ: 既に登録済み",
                timestamp
            )
            continue

        logger.info(f"新規登録中: {product.name}")

        new_id, error = colorme_client.create_product(product)

        if new_id > 0:
            status = "成功"
            success_count += 1
            logger.info(f"  → 成功 (新ID: {new_id})")

            # 画像アップロード（URLが指定されている場合）
            if any(product.image_urls):
                img_success, img_error = colorme_client.upload_product_images(
                    new_id,
                    product.image_urls
                )
                if not img_success:
                    status = f"成功（画像エラー: {img_error}）"
                    logger.warning(f"  → 画像アップロードエラー: {img_error}")

            # シートに商品IDとステータスを反映
            sheet_client.update_colorme_sync_status(
                product.product_id,  # 元の行（商品ID=0または空）
                status,
                timestamp,
                new_product_id=new_id
            )
        else:
            status = f"エラー: {error}"
            failed_count += 1
            logger.warning(f"  → 失敗: {error}")
            sheet_client.update_colorme_sync_status(
                product.product_id,
                status,
                timestamp
            )

    logger.info(f"新規登録完了: 成功 {success_count}件, 失敗 {failed_count}件")


if __name__ == "__main__":
    # コマンドライン引数で処理を分岐
    import argparse

    parser = argparse.ArgumentParser(description="価格追跡・カラーミー商品管理ツール")
    parser.add_argument(
        "--sync-colorme",
        action="store_true",
        help="カラーミー商品管理の同期を実行（AB列の同期モードに従う）"
    )
    args = parser.parse_args()

    if args.sync_colorme:
        sync_colorme_products_full()
    else:
        run()
