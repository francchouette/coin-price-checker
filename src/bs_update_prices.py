"""
価格・在庫状況更新スクリプト

既存商品の価格・為替レート・在庫状況を再取得・再計算する軽量スクリプト。
新規商品の追加やAI生成コンテンツの更新は行わない。

為替変動が多いため、4時間毎に実行して価格・在庫を最新に保つ。

更新対象列:
- Q列: 仕入れ先在庫状況（In Stock / Out of Stock）
- R列: 仕入れ先価格（現地通貨）
- S列: 前回仕入れ価格
- U列: 取引通貨
- V列: 為替種類
- W列: 為替レート
- X列: 仕入れ額(日本円)

使用方法:
    python -m src.bs_update_prices [--limit N] [--dry-run] [--exchange-type {クレカ,Wise}] [--registered-only]

オプション:
    --limit N: 処理件数制限（テスト用）
    --dry-run: 実際の更新を行わない（確認用）
    --exchange-type: 為替種類（デフォルト: クレカ）
    --pending-only: 検討中の商品のみを対象（デフォルト: 全商品）
"""

import argparse
import logging
import sys
import time
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.exchange_rate import ExchangeRateClient, WiseRateClient
from src.bs_sheet_columns import Col, get_cell, get_cell_float, cell_ref

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


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

        try:
            if exchange_type == "Wise":
                rate = wise_client.get_rate(currency, "JPY")
            else:  # クレカ
                rate = exchange_client.get_rate(currency, "JPY")

            if rate:
                rates[currency] = rate
                logger.info(f"  {currency}/JPY: {rate:.4f} ({exchange_type})")
            else:
                logger.warning(f"  {currency}/JPY: 取得失敗")
        except Exception as e:
            logger.warning(f"  {currency}/JPY: エラー ({e})")

    return rates


def update_prices(
    dry_run: bool = False,
    limit: Optional[int] = None,
    exchange_type: str = "クレカ",
    pending_only: bool = False
) -> dict:
    """
    既存商品の価格・為替レート・在庫状況を更新

    Args:
        dry_run: Trueの場合、実際の更新を行わない
        limit: 処理件数制限（Noneで全件）
        exchange_type: 為替種類（"クレカ" または "Wise"）
        pending_only: Trueの場合、検討中の商品のみを対象

    Returns:
        dict: 更新結果の統計
    """
    # Playwrightのインポート確認
    try:
        from playwright.sync_api import sync_playwright
        from src.shops.bullionstar import BullionstarScraper
    except ImportError:
        logger.error("Playwrightがインストールされていません。pip install playwright && playwright install chromium")
        return 0

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return 0

    try:
        # ブリオンスター商品ページ一覧シートを取得
        sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        all_data = sheet.get_all_values()

        if len(all_data) <= 1:
            logger.info("データがありません")
            return 0

        logger.info(f"総商品数: {len(all_data) - 1}件")

        # 価格更新対象を抽出
        target_rows = []
        for row_idx, row in enumerate(all_data[1:], start=2):  # ヘッダースキップ、行番号は2から
            url = get_cell(row, Col.PRODUCT_URL)
            if not url:
                continue

            # 検討中のみモードの場合、A列が「検討中」の商品のみ対象
            if pending_only:
                adopted_flag = get_cell(row, Col.ADOPTED_FLAG)
                if adopted_flag != "検討中":
                    continue

            target_rows.append((row_idx, row, url))

        if pending_only:
            logger.info(f"価格更新対象: {len(target_rows)}件（検討中の商品のみ）")
        else:
            logger.info(f"価格更新対象: {len(target_rows)}件（全商品）")

        if limit:
            target_rows = target_rows[:limit]
            logger.info(f"件数制限適用: {len(target_rows)}件")

        if not target_rows:
            logger.info("処理対象の商品がありません")
            return 0

        # 為替レートを事前取得
        logger.info("為替レートを事前取得中...")
        pre_fetched_rates = fetch_exchange_rates(["SGD", "USD", "EUR", "AUD", "NZD"], exchange_type)
        logger.info(f"為替レート取得完了: {pre_fetched_rates}")

        timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        update_cells = []
        success_count = 0
        error_count = 0
        skipped_count = 0
        in_stock_count = 0
        out_of_stock_count = 0
        total_saved_count = 0  # スプレッドシートに保存済みの商品数

        # 100商品ごとにスプレッドシートに保存する関数
        def flush_updates():
            nonlocal update_cells, total_saved_count
            if not dry_run and update_cells:
                sheet.batch_update(update_cells, value_input_option='RAW')
                total_saved_count += len(update_cells) // 7  # 1商品あたり約7セル
                logger.info(f"  [中間保存] {total_saved_count}商品をスプレッドシートに保存しました")
                time.sleep(1)  # API制限対策
            update_cells = []

        # Playwrightでスクレイピング
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

            for i, (row_idx, row, url) in enumerate(target_rows):
                product_name = get_cell(row, Col.PRODUCT_NAME)
                prev_price = get_cell_float(row, Col.PRICE)

                try:
                    logger.info(f"[{i+1}/{len(target_rows)}] {product_name[:40]}...")

                    result = scraper.scrape(url)

                    if result.error:
                        logger.warning(f"  エラー: {result.error}")
                        error_count += 1
                        scraper.reset_extra_fields()
                        continue

                    # 価格が取得できない場合はスキップ
                    if result.price is None:
                        logger.warning(f"  価格取得失敗")
                        skipped_count += 1
                        scraper.reset_extra_fields()
                        continue

                    # 在庫状況
                    stock_status = "In Stock" if result.in_stock else "Out of Stock"
                    if result.in_stock:
                        in_stock_count += 1
                    else:
                        out_of_stock_count += 1

                    # 為替レート適用
                    currency = result.currency.upper() if result.currency else "SGD"
                    if currency == "JPY":
                        rate = 1.0
                        price_jpy = result.price
                        ex_type = "なし"
                    else:
                        rate = pre_fetched_rates.get(currency, 0.0)
                        if rate:
                            price_jpy = round(result.price * rate, 0)
                            ex_type = exchange_type
                        else:
                            price_jpy = 0.0
                            ex_type = exchange_type
                            logger.warning(f"  為替レート未取得: {currency}")

                    # 価格変動ログ
                    if prev_price and prev_price != result.price:
                        change = ((result.price - prev_price) / prev_price) * 100
                        logger.info(f"  価格変動: {prev_price:.2f} → {result.price:.2f} ({change:+.2f}%)")

                    logger.info(
                        f"  {currency} {result.price:.2f} → JPY {price_jpy:.0f} "
                        f"(レート: {rate:.4f}), 在庫: {stock_status}"
                    )

                    # 更新データを作成
                    # Q列: 仕入れ先在庫状況
                    update_cells.append({
                        'range': cell_ref(Col.STOCK_STATUS, row_idx),
                        'values': [[stock_status]]
                    })
                    # R列: 仕入れ先価格（現地通貨）
                    update_cells.append({
                        'range': cell_ref(Col.PRICE, row_idx),
                        'values': [[str(result.price)]]
                    })
                    # S列: 前回仕入れ価格（現在のR列の値を保存）
                    if prev_price:
                        update_cells.append({
                            'range': cell_ref(Col.PREV_PRICE, row_idx),
                            'values': [[str(prev_price)]]
                        })
                    # U列: 取引通貨
                    update_cells.append({
                        'range': cell_ref(Col.CURRENCY, row_idx),
                        'values': [[currency]]
                    })
                    # V列: 為替種類
                    update_cells.append({
                        'range': cell_ref(Col.EXCHANGE_TYPE, row_idx),
                        'values': [[ex_type]]
                    })
                    # W列: 為替レート
                    update_cells.append({
                        'range': cell_ref(Col.EXCHANGE_RATE, row_idx),
                        'values': [[str(rate) if rate else ""]]
                    })
                    # X列: 仕入れ額(日本円)
                    update_cells.append({
                        'range': cell_ref(Col.PRICE_JPY, row_idx),
                        'values': [[str(int(price_jpy)) if price_jpy else ""]]
                    })

                    scraper.reset_extra_fields()
                    success_count += 1

                    # 100商品ごとにスプレッドシートに保存（エラー時のデータ損失を防ぐ）
                    if success_count % 100 == 0:
                        flush_updates()

                    # レート制限対策
                    time.sleep(random.uniform(0.5, 1.5))

                except Exception as e:
                    logger.error(f"  スクレイピングエラー: {e}")
                    error_count += 1
                    scraper.reset_extra_fields()
                    continue

            browser.close()

        # 残りのデータを保存
        if update_cells:
            if not dry_run:
                sheet.batch_update(update_cells, value_input_option='RAW')
                total_saved_count += len(update_cells) // 7
                logger.info(f"  [最終保存] 残り{len(update_cells) // 7}商品をスプレッドシートに保存しました")

        logger.info(f"\n価格取得完了: 成功={success_count}件, 失敗={error_count}件, スキップ={skipped_count}件")
        logger.info(f"在庫状況: In Stock={in_stock_count}件, Out of Stock={out_of_stock_count}件")

        if dry_run:
            logger.info("\n[ドライラン] 実際の更新は行いませんでした")
        else:
            logger.info(f"\nスプレッドシート更新完了: 合計{total_saved_count}商品")

        return {
            "success": success_count,
            "failed": error_count,
            "skipped": skipped_count,
        }

    except Exception as e:
        logger.error(f"エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description='既存商品の価格・為替レートを更新'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='ドライラン（実際の更新なし）'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='処理件数制限'
    )
    parser.add_argument(
        '--exchange-type',
        type=str,
        choices=['クレカ', 'Wise'],
        default='クレカ',
        help='為替種類'
    )
    parser.add_argument(
        '--pending-only',
        action='store_true',
        help='検討中の商品のみを対象'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='詳細ログを出力'
    )

    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("=" * 60)
    logger.info("価格・在庫状況更新")
    logger.info("=" * 60)
    logger.info(f"モード: {'ドライラン' if args.dry_run else '本番'}")
    logger.info(f"為替種類: {args.exchange_type}")
    logger.info(f"対象: {'検討中の商品のみ' if args.pending_only else '全商品'}")
    if args.limit:
        logger.info(f"件数制限: {args.limit}件")
    logger.info("=" * 60)

    start_time = time.time()
    result = update_prices(
        dry_run=args.dry_run,
        limit=args.limit,
        exchange_type=args.exchange_type,
        pending_only=args.pending_only
    )
    elapsed = time.time() - start_time

    logger.info("=" * 60)
    logger.info("処理完了")
    if "error" not in result:
        logger.info(f"  成功: {result.get('success', 0)}件")
        logger.info(f"  失敗: {result.get('failed', 0)}件")
        logger.info(f"  スキップ: {result.get('skipped', 0)}件")
    else:
        logger.error(f"  エラー: {result['error']}")
    logger.info(f"  所要時間: {elapsed:.1f}秒")
    logger.info("=" * 60)

    # 致命的エラーがなければ成功（個別の失敗は許容）
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
