"""一覧から消えたブリオンスター採用商品の在庫・価格を再取得する

背景:
    bullionstar_products.py --fetch-prices は「カテゴリ一覧のクロールで見つかった商品」しか
    スクレイピングしない。在庫切れになった商品は BullionStar の一覧から消えるため、
    採用済みであっても二度と更新されず、シート上は最後に見えたときの In Stock のまま凍結する。
    2026-09-18 に 41件が「仕入れ先は在庫切れなのにショップで購入可能」になっていた。

やること（新カラーミー商品管理に載っている Bullionstar 商品だけが対象）:
    1. カテゴリ一覧をクロールして「今一覧に載っているURL」を得る（約35秒）
    2. 新カラーミー商品管理の J列URL のうち、一覧に無いものを対象にする（通常 300件弱）
    3. 対象を1件ずつスクレイピングし、ブリオンスター商品ページ一覧の
       在庫状況・価格・前回価格・通貨・為替・日本円換算・同期日時 を更新
       （bullionstar_products.py の既存行更新と同じ列。数式セルには触らない）
    4. sync_supplier_list --source bs で商品仕入れ先一覧へ反映（M列/N列の数式が追従する）
    5. 在庫状況が変わった商品だけ sync_colorme_products --price-only --product-ids で即時反映

使い方:
    python -m src.bs_refresh_delisted --dry-run --limit 3   # 書き込まずに動作確認
    python -m src.bs_refresh_delisted                       # 本番
    python -m src.bs_refresh_delisted --no-push             # カラーミー即時反映なし
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from .spreadsheet import SpreadsheetClient
from .config import Config
from .cm_sheet_columns import Col as CM
from .bs_sheet_columns import Col as BS
from .bullionstar_products import fetch_bullionstar_products, fetch_exchange_rates
from .scraper import ScraperManager
from .download_colorme_products import scrape_url

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
MIN_LISTED = 500          # クロール結果がこれ未満なら異常とみなして中止（対象が全件に膨らむのを防ぐ）
SAVE_EVERY = 20           # 中間保存の間隔（件）
EXCHANGE_TYPE = "Wise"    # bs-scrape.sh と同じ
CURRENCIES = ["SGD", "USD", "EUR", "AUD", "NZD"]
IN_STOCK = "In Stock"
OUT_OF_STOCK = "Out of Stock"


def normalize(url: str) -> str:
    """地域ドメイン違いを同一商品として扱う（download_colorme_products と同じ扱い）"""
    return (url.strip()
            .replace("bullionstar.co.nz", "bullionstar.com")
            .replace("bullionstar.us", "bullionstar.com"))


def _cell(row: list, idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) else ""


def _to_float(s: str):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def build_cell_updates(existing: list, existing_fml: list, scraped, stock: str,
                       rates: dict, now_str: str) -> list[tuple[int, str]]:
    """ブリオンスターシート1行ぶんの更新セルを作る（bullionstar_products.py の既存行更新と同じ列）

    在庫状況は取得できた時点で必ず書く（この処理の主目的）。
    価格まわりは価格が取れたときだけ。数式が入っているセルは書かない。
    """
    cells: list[tuple[int, str]] = [(BS.STOCK_STATUS.index, stock)]

    if scraped.price and scraped.price > 0:
        old_price = _to_float(_cell(existing, BS.PRICE.index))
        if old_price is not None:
            cells.append((BS.PREV_PRICE.index, str(old_price)))
        cells.append((BS.PRICE.index, str(scraped.price)))

        currency = (scraped.currency or "").upper()
        if currency:
            cells.append((BS.CURRENCY.index, currency))
        if currency == "JPY":
            rate, exchange_type = 1.0, "なし"
        elif rates.get(currency):
            rate, exchange_type = rates[currency], EXCHANGE_TYPE
        else:
            # レートが取れなかった通貨は既存の為替をそのまま使う
            rate = _to_float(_cell(existing, BS.EXCHANGE_RATE.index))
            exchange_type = _cell(existing, BS.EXCHANGE_TYPE.index)
        if rate:
            cells.append((BS.EXCHANGE_TYPE.index, exchange_type))
            cells.append((BS.EXCHANGE_RATE.index, str(rate)))
            cells.append((BS.PRICE_JPY.index, str(int(round(scraped.price * rate)))))

    cells.append((BS.CM_SYNC_AT.index, now_str))

    return [(idx, val) for idx, val in cells
            if not _cell(existing_fml, idx).startswith("=")]


def run_subprocess(label: str, args: list[str]) -> bool:
    logger.info("-" * 60)
    logger.info(f"{label}: {' '.join(args)}")
    proc = subprocess.run([sys.executable, "-m", *args], cwd=os.path.dirname(os.path.dirname(__file__)))
    if proc.returncode != 0:
        logger.error(f"{label} が失敗しました (exit code: {proc.returncode})")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="一覧から消えたブリオンスター採用商品の在庫・価格を再取得")
    parser.add_argument("--dry-run", action="store_true", help="シート・カラーミーに書き込まない")
    parser.add_argument("--limit", type=int, default=0, help="スクレイピング件数の上限（0=無制限、テスト用）")
    parser.add_argument("--no-sync", action="store_true", help="商品仕入れ先一覧への同期を行わない")
    parser.add_argument("--no-push", action="store_true", help="在庫が変わった商品のカラーミー即時反映を行わない")
    args = parser.parse_args()

    started = time.time()
    logger.info("=" * 60)
    logger.info("一覧から消えたブリオンスター商品の再取得 開始" + ("（ドライラン）" if args.dry_run else ""))
    logger.info("=" * 60)

    # 1. 今カテゴリ一覧に載っているURL
    listed = {normalize(p.url) for p in fetch_bullionstar_products()}
    logger.info(f"カテゴリ一覧に載っている商品: {len(listed)}件")
    if len(listed) < MIN_LISTED:
        logger.error(f"クロール結果が少なすぎます（{len(listed)} < {MIN_LISTED}）。一覧取得に失敗した可能性があるため中止します")
        return 2

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return 2
    ss = client._spreadsheet

    # 2. 新カラーミー商品管理の Bullionstar 商品のうち、一覧に無いもの
    cm_ws = ss.worksheet(Config.SHEET_COLORME_V2)
    cm_rows = cm_ws.get(f"A2:{CM.last_column_letter()}{cm_ws.row_count}", value_render_option="FORMATTED_VALUE")
    by_url: dict[str, list[dict]] = {}   # 生URL -> [{pid, name, m}] （セット商品はURLを共有する）
    cm_bs_total = 0
    for r in cm_rows:
        pid = _cell(r, CM.PRODUCT_ID.index)
        url = _cell(r, CM.SUPPLIER_URL.index)
        if not pid or "bullionstar" not in url.lower():
            continue
        cm_bs_total += 1
        if normalize(url) in listed:
            continue
        by_url.setdefault(url, []).append({
            "pid": pid,
            "name": _cell(r, CM.NAME.index)[:34],
            "m": _cell(r, CM.SUPPLIER_STOCK.index),
        })
    logger.info(f"カラーミー管理シートの Bullionstar 商品: {cm_bs_total}件 / うち一覧に無い: "
                f"{sum(len(v) for v in by_url.values())}件（{len(by_url)} URL）")
    if not by_url:
        logger.info("対象なし。終了します")
        return 0

    # 3. ブリオンスター商品ページ一覧（値と数式）
    bs_ws = ss.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
    bs_vals = bs_ws.get_all_values()
    bs_fmls = bs_ws.get(f"A1:{BS.CM_SYNC_AT.letter}{len(bs_vals)}", value_render_option="FORMULA")
    url_to_row: dict[str, int] = {}
    for n, r in enumerate(bs_vals[1:], start=2):
        u = _cell(r, BS.PRODUCT_URL.index)
        if u:
            url_to_row.setdefault(u, n)

    # 4. 為替レート（取れなければ既存値を使う）
    rates: dict = {}
    try:
        rates = fetch_exchange_rates(CURRENCIES, EXCHANGE_TYPE)
    except Exception as e:
        logger.warning(f"為替レートの取得に失敗しました。既存の為替レートを使います: {e}")

    # 5. スクレイピングと更新
    items = list(by_url.items())
    if args.limit:
        items = items[:args.limit]
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    stats = Counter()
    pending: list[dict] = []
    backup: dict[str, str] = {}
    changed_pids: list[str] = []
    stock_counter = Counter()

    def flush():
        if not pending or args.dry_run:
            pending.clear()
            return
        for i in range(0, len(pending), 500):
            ss.values_batch_update({"data": pending[i:i + 500], "valueInputOption": "USER_ENTERED"})
        logger.info(f"  中間保存: {len(pending)}セル")
        pending.clear()

    logger.info(f"スクレイピング開始: {len(items)} URL")
    with ScraperManager() as sm:
        for k, (url, cm_items) in enumerate(items, 1):
            label = f"[{k}/{len(items)}] {cm_items[0]['name']}"
            row = url_to_row.get(url)
            if row is None:
                stats["BSシートに行なし"] += 1
                logger.warning(f"{label}: ブリオンスターシートに該当行がありません {url[:70]}")
                continue

            scraped = scrape_url(sm, normalize(url)).scraped_data
            if scraped.error:
                stats["スクレイピング失敗"] += 1
                logger.warning(f"{label}: 失敗 {scraped.error}")
                continue

            stock = IN_STOCK if scraped.in_stock else OUT_OF_STOCK
            stock_counter[stock] += 1
            stats["取得成功"] += 1
            logger.info(f"{label} → {stock} / {scraped.currency} {scraped.price}")

            existing = bs_vals[row - 1]
            existing_fml = bs_fmls[row - 1] if row - 1 < len(bs_fmls) else []
            for idx, val in build_cell_updates(existing, existing_fml, scraped, stock, rates, now_str):
                a1 = f"{_idx_to_letter(idx)}{row}"
                backup[a1] = _cell(existing, idx)
                pending.append({"range": f"{Config.SHEET_BULLIONSTAR_PRODUCTS}!{a1}", "values": [[val]]})

            for it in cm_items:
                if it["m"] != stock:
                    changed_pids.append(it["pid"])

            if k % SAVE_EVERY == 0:
                flush()
        flush()

    if backup and not args.dry_run:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"bs-refresh-backup-{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w") as f:
            json.dump(backup, f, ensure_ascii=False, indent=1)
        logger.info(f"更新前の値を退避: {path}")

    logger.info("")
    logger.info("--- 集計 ---")
    for key, n in stats.most_common():
        logger.info(f"  {key:<16}: {n}件")
    logger.info(f"  在庫あり          : {stock_counter[IN_STOCK]}件")
    logger.info(f"  在庫切れ          : {stock_counter[OUT_OF_STOCK]}件")
    logger.info(f"  在庫状況が変わった商品: {len(changed_pids)}件 {changed_pids[:10]}{'...' if len(changed_pids) > 10 else ''}")

    if args.dry_run:
        logger.info("ドライランのため書き込み・同期・カラーミー反映は行いませんでした")
        return 0

    ok = True
    # 6. 商品仕入れ先一覧へ反映（M列/N列の数式が追従する）
    if stats["取得成功"] and not args.no_sync:
        ok = run_subprocess("仕入れ先一覧同期", ["src.sync_supplier_list", "--source", "bs"]) and ok

    # 7. 在庫状況が変わった商品だけカラーミーに即時反映（価格・在庫・表示のみ）
    if changed_pids and not args.no_push and not args.no_sync:
        ok = run_subprocess("カラーミー即時反映",
                            ["src.sync_colorme_products", "--price-only",
                             "--product-ids", ",".join(changed_pids)]) and ok
    elif changed_pids:
        logger.info(f"カラーミー即時反映はスキップ（次回の定期同期で反映されます）: {len(changed_pids)}件")

    elapsed = time.time() - started
    logger.info("=" * 60)
    logger.info(f"完了（所要時間: {int(elapsed // 60)}分{int(elapsed % 60)}秒）")
    logger.info("=" * 60)
    return 0 if ok else 1


def _idx_to_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx > 0:
        idx -= 1
        s = chr(65 + idx % 26) + s
        idx //= 26
    return s


if __name__ == "__main__":
    sys.exit(main())
