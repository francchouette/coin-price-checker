"""
メインエントリーポイント

価格追跡プログラムの全体フローを制御する。
"""

import sys
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

from .config import Config
from .spreadsheet import SpreadsheetClient, PriceRecord, TrackingTarget
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
from .exchange_rate import ExchangeRateClient
from .colorme import ColorMeClient

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
    """メイン処理を実行する"""
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

    # トラッキング対象を取得
    targets = sheet_client.get_tracking_targets()
    if not targets:
        logger.warning("トラッキング対象が見つかりません")
        sys.exit(0)

    logger.info(f"トラッキング対象: {len(targets)}件")

    # アラート閾値を取得
    alert_threshold = sheet_client.get_alert_threshold()
    logger.info(f"アラート閾値: ±{alert_threshold}%")

    # 直近の価格と在庫状況を事前に取得
    urls = [t.url for t in targets]
    previous_prices = sheet_client.get_latest_prices(urls)
    previous_stock = sheet_client.get_latest_stock_status(urls)
    logger.info(f"直近価格データ: {len(previous_prices)}件")
    logger.info(f"直近在庫データ: {len(previous_stock)}件")

    # スクレイピング実行
    logger.info("スクレイピングを開始します...")
    scraped_results = scrape_targets(targets)

    # 結果を処理
    logger.info("結果を処理中...")
    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    price_records = []
    alerts = []

    for target, result in zip(targets, scraped_results):
        if result.error:
            logger.warning(f"スクレイピング失敗: {target.url} - {result.error}")
            continue

        # 価格変動を計算
        previous_price = previous_prices.get(target.url)
        change_rate = 0.0
        if previous_price is not None and previous_price > 0:
            change_rate = calculate_change_rate(result.price, previous_price)

        # 価格レコードを作成（在庫状況・差分を含む）
        record = PriceRecord(
            timestamp=timestamp,
            shop_name=target.shop_name,
            product_name=result.product_name,
            price=result.price,
            currency=result.currency,
            previous_price=previous_price if previous_price else 0.0,
            change_rate=change_rate,
            in_stock=result.in_stock,
            url=target.url
        )
        price_records.append(record)

        # ログ出力
        stock_status = "In Stock" if result.in_stock else "Out of Stock"
        if previous_price:
            logger.info(
                f"取得: {result.product_name} - {result.currency} {result.price:,.2f} "
                f"(前回: {previous_price:,.2f}, 変動: {change_rate:+.2f}%) [{stock_status}]"
            )
        else:
            logger.info(
                f"取得: {result.product_name} - {result.currency} {result.price:,.2f} "
                f"(初回取得) [{stock_status}]"
            )

        # 価格変動アラートをチェック
        if previous_price is not None and check_alert_threshold(change_rate, alert_threshold):
            alert = PriceAlert(
                product_name=result.product_name,
                shop_name=target.shop_name,
                current_price=result.price,
                previous_price=previous_price,
                change_rate=change_rate,
                currency=result.currency,
                url=target.url,
                timestamp=timestamp,
                alert_type="price",
                in_stock=result.in_stock
            )
            alerts.append(alert)
            logger.info(
                f"価格アラート検出: {result.product_name} "
                f"({previous_price:,.2f} → {result.price:,.2f}, "
                f"{change_rate:+.2f}%)"
            )

        # 在庫切れアラートをチェック（前回In Stock → 今回Out of Stock）
        was_in_stock = previous_stock.get(target.url, True)
        if was_in_stock and not result.in_stock:
            alert = PriceAlert(
                product_name=result.product_name,
                shop_name=target.shop_name,
                current_price=result.price,
                previous_price=previous_price if previous_price else result.price,
                change_rate=0.0,
                currency=result.currency,
                url=target.url,
                timestamp=timestamp,
                alert_type="stock",
                in_stock=False
            )
            alerts.append(alert)
            logger.info(f"在庫切れアラート検出: {result.product_name}")

    # 価格履歴を保存
    if price_records:
        logger.info(f"価格履歴を保存中... ({len(price_records)}件)")
        if sheet_client.save_price_records(price_records):
            logger.info("価格履歴を保存しました")
        else:
            logger.error("価格履歴の保存に失敗しました")

        # ダッシュボードを更新（最新価格のみ）
        logger.info("ダッシュボードを更新中...")
        if sheet_client.update_dashboard(price_records):
            logger.info("ダッシュボードを更新しました")
        else:
            logger.warning("ダッシュボードの更新に失敗しました")

    # アラートを送信
    if alerts:
        logger.info(f"アラートを送信中... ({len(alerts)}件)")
        send_alerts(alerts)
    else:
        logger.info("アラートはありません")

    # カラーミー価格更新
    if Config.is_colorme_enabled():
        logger.info("=" * 60)
        if Config.COLORME_DRY_RUN:
            logger.info("カラーミー価格更新を開始します（ドライランモード - 実際の更新なし）")
        else:
            logger.info("カラーミー価格更新を開始します")
        update_colorme_prices(sheet_client, price_records)
    else:
        logger.info("カラーミー連携は無効です（COLORME_ACCESS_TOKEN未設定）")

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info(f"価格追跡プログラムが完了しました（{elapsed:.1f}秒）")
    logger.info(f"  - 処理件数: {len(price_records)}件")
    logger.info(f"  - アラート: {len(alerts)}件")
    logger.info("=" * 60)


def scrape_targets(targets: list[TrackingTarget]) -> list[ScrapedData]:
    """
    トラッキング対象をスクレイピングする

    Args:
        targets: トラッキング対象のリスト

    Returns:
        list[ScrapedData]: スクレイピング結果のリスト
    """
    scrape_targets_list = []

    for target in targets:
        # ショップ名が空の場合はURLから推定
        shop_name = target.shop_name
        if not shop_name:
            shop_name = detect_shop_from_url(target.url)

        scrape_targets_list.append(ScrapeTarget(
            shop_name=shop_name,
            url=target.url,
            product_name_hint=target.product_name
        ))

    with ScraperManager() as manager:
        return manager.scrape_all(scrape_targets_list)


def update_colorme_prices(sheet_client: SpreadsheetClient, price_records: list[PriceRecord]):
    """
    カラーミーショップの価格を更新する

    Args:
        sheet_client: スプレッドシートクライアント
        price_records: 今回取得した価格レコードのリスト
    """
    # カラーミー商品リストを取得
    colorme_products = sheet_client.get_colorme_products()
    if not colorme_products:
        logger.info("カラーミー商品管理シートに対象商品がありません")
        return

    logger.info(f"カラーミー価格更新対象: {len(colorme_products)}件")

    # 今回取得したBullionstar価格をURL -> 価格の辞書に変換
    # Bullionstarの価格のみを対象とする
    bullionstar_prices = {}
    for record in price_records:
        if "bullionstar" in record.url.lower():
            bullionstar_prices[record.url] = record.price

    if not bullionstar_prices:
        logger.warning("Bullionstarの価格データがありません")
        return

    logger.info(f"Bullionstar価格データ: {len(bullionstar_prices)}件")

    # 為替レートを取得
    exchange_client = ExchangeRateClient()
    if not exchange_client.fetch_rates():
        logger.error("為替レートの取得に失敗しました")
        return

    exchange_rate = exchange_client.get_rate("USD", "JPY")
    if not exchange_rate:
        logger.error("USD/JPY為替レートが取得できません")
        return

    logger.info(f"為替レート: 1 USD = {exchange_rate:.2f} JPY")

    # カラーミー価格を更新
    colorme_client = ColorMeClient()
    result = colorme_client.update_prices_batch(
        colorme_products,
        bullionstar_prices,
        exchange_rate
    )

    logger.info(
        f"カラーミー価格更新完了: "
        f"成功 {result['success']}件, "
        f"失敗 {result['failed']}件, "
        f"スキップ {result['skipped']}件"
    )


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


if __name__ == "__main__":
    run()
