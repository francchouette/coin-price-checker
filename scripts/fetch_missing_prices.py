"""価格未取得商品のみスクレイピングするスクリプト"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
import logging
import time

from src.spreadsheet import SpreadsheetClient
from src.config import Config
from src.cm_sheet_columns import Col, get_cell
from src.scraper import ScraperManager
from src.download_colorme_products import fetch_exchange_rates, scrape_url

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('manual_fetch')

client = SpreadsheetClient()
client.connect()
sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
data = sheet.get_all_values()

# 価格未取得の商品を収集
targets = []
for i, row in enumerate(data[1:], start=2):
    url = get_cell(row, Col.SUPPLIER_URL)
    price = get_cell(row, Col.SUPPLIER_PRICE)
    if url and url.startswith("http") and not price:
        targets.append((i, url, row))

logger.info(f"価格未取得の商品: {len(targets)}件")

# 為替レート取得
exchange_rates = fetch_exchange_rates(
    ["SGD", "USD", "NZD", "AUD"],
    {"SGD": "クレカ", "USD": "クレカ", "NZD": "クレカ", "AUD": "クレカ"},
)

success = 0
fail = 0
price_cache = {}

with ScraperManager() as mgr:
    for idx, (row_num, url, value_row) in enumerate(targets):
        name = get_cell(value_row, Col.NAME)[:35]

        # URL変換
        scrape_url_target = url
        if "bullionstar.co.nz" in scrape_url_target:
            scrape_url_target = scrape_url_target.replace("bullionstar.co.nz", "bullionstar.com")
        elif "bullionstar.us" in scrape_url_target:
            scrape_url_target = scrape_url_target.replace("bullionstar.us", "bullionstar.com")

        logger.info(f"[{idx+1}/{len(targets)}] 行{row_num}: {name}")

        if scrape_url_target in price_cache:
            result = price_cache[scrape_url_target]
            logger.info("  キャッシュ使用")
        else:
            result = scrape_url(mgr, scrape_url_target)
            price_cache[scrape_url_target] = result

        scraped = result.scraped_data
        if scraped.error:
            fail += 1
            logger.warning(f"  失敗: {scraped.error[:80]}")
            continue

        success += 1
        stock = "In Stock" if scraped.in_stock else "Out of Stock"
        logger.info(f"  成功: {scraped.currency} {scraped.price:,.2f} ({stock})")

        # シート更新
        updates = [
            {"range": f"{Col.SUPPLIER_STOCK.letter}{row_num}", "values": [[stock]]},
            {"range": f"{Col.SUPPLIER_PRICE.letter}{row_num}", "values": [[str(scraped.price)]]},
            {"range": f"{Col.CURRENCY.letter}{row_num}", "values": [[scraped.currency]]},
        ]
        data_copy = copy.deepcopy(updates)
        sheet.batch_update(data_copy, value_input_option="USER_ENTERED")

logger.info(f"=== 完了: 成功={success}件, 失敗={fail}件 ===")
