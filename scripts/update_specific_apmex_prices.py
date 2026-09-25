"""指定したAPMEX商品の価格を直接HTTP取得し、APMEX商品ページ一覧を更新する

apmex_products.py の HTTP スクレイピングロジックを再利用。

使い方:
    python scripts/update_specific_apmex_prices.py --supplier-ids AP-026058,AP-031099
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.config import Config
from src.bs_sheet_columns import Col, get_cell
from src.apmex_products import (
    _create_http_session, _parse_detail_html, fetch_exchange_rates, ApmexProduct,
)

logger = logging.getLogger(__name__)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("urls", nargs="*", help="APMEX商品URL（複数可）")
    p.add_argument("--supplier-ids", type=str, default="", help="仕入れ先ID（カンマ区切り）")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("シート接続失敗")
        sys.exit(1)
    sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_PRODUCTS)
    all_data = sheet.get_all_values()

    sids = set(s.strip() for s in args.supplier_ids.split(",") if s.strip()) if args.supplier_ids else set()
    target_urls = set(args.urls)

    target_rows = []  # [(row_idx, url, existing_row, name)]
    for row_idx, row in enumerate(all_data[1:], start=2):
        url = get_cell(row, Col.PRODUCT_URL)
        sid = get_cell(row, Col.SUPPLIER_ID)
        if (url in target_urls) or (sid in sids):
            name = get_cell(row, Col.PRODUCT_NAME)
            target_rows.append((row_idx, url, row, name))

    if not target_rows:
        logger.error("対象URL/IDが見つかりません")
        sys.exit(1)

    logger.info(f"対象: {len(target_rows)}件")

    # 為替レート
    rates = fetch_exchange_rates("クレカ")
    usd_rate = rates.get("USD", 0)
    logger.info(f"USD/JPY: {usd_rate}")

    # HTTP セッション
    session = _create_http_session()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates = []
    success = 0

    for row_idx, url, existing_row, name in target_rows:
        logger.info(f"  行{row_idx}: {name[:40] or url[-40:]}")
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"    HTTPエラー: {resp.status_code}")
                continue
            if 'Just a moment' in resp.text[:500]:
                logger.warning(f"    Cloudflare検出、スキップ")
                continue

            data = _parse_detail_html(resp.text, name)
            price = data.get('price', 0)
            in_stock = data.get('in_stock', False)
            if not price:
                logger.warning(f"    価格取得失敗")
                continue

            prev_price = get_cell(existing_row, Col.PRICE) or ""
            change_rate = ""
            if prev_price:
                try:
                    pp = float(prev_price)
                    if pp > 0:
                        change_rate = f"{(price - pp) / pp * 100:+.2f}%"
                except ValueError:
                    pass
            stock_str = "In Stock" if in_stock else "Out of Stock"
            price_jpy = int(price * usd_rate) if usd_rate else 0

            logger.info(f"    → ${price} ({stock_str}) JPY={price_jpy:,}  変動率={change_rate}")
            success += 1

            sn = Config.SHEET_APMEX_PRODUCTS
            updates.extend([
                {'range': f"{sn}!{Col.PRICE.letter}{row_idx}", 'values': [[str(price)]]},
                {'range': f"{sn}!{Col.PREV_PRICE.letter}{row_idx}", 'values': [[prev_price]]},
                {'range': f"{sn}!{Col.PRICE_CHANGE.letter}{row_idx}", 'values': [[change_rate]]},
                {'range': f"{sn}!{Col.STOCK_STATUS.letter}{row_idx}", 'values': [[stock_str]]},
                {'range': f"{sn}!{Col.EXCHANGE_RATE.letter}{row_idx}", 'values': [[str(usd_rate) if usd_rate else ""]]},
                {'range': f"{sn}!{Col.PRICE_JPY.letter}{row_idx}", 'values': [[str(price_jpy) if price_jpy else ""]]},
                {'range': f"{sn}!{Col.CM_SYNC_AT.letter}{row_idx}", 'values': [[now]]},
            ])
        except Exception as e:
            logger.warning(f"    エラー: {e}")

    if updates:
        sheet.spreadsheet.values_batch_update({'data': updates, 'valueInputOption': 'USER_ENTERED'})
        logger.info(f"\n=== 完了: 成功{success}件 / {len(updates)}セル更新 ===")
    else:
        logger.warning("更新対象なし")


if __name__ == "__main__":
    main()
