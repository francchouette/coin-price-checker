"""
カラーミー商品説明ダウンロードスクリプト

カラーミーAPIから指定期間内に更新された商品の説明文を取得し、
新カラーミー商品管理シートのBA列（商品説明）・BB列（簡易説明）に反映する。

使い方:
    # 今日10:00 JST以降に更新された商品の説明を反映
    python -m src.download_colorme_descriptions --since "2026-03-11 10:00"

    # 直近N時間以内に更新された商品
    python -m src.download_colorme_descriptions --hours 3

    # 直近N日以内
    python -m src.download_colorme_descriptions --days 1

    # ドライラン（対象商品の確認のみ）
    python -m src.download_colorme_descriptions --since "2026-03-11 10:00" --dry-run
"""

import argparse
import logging
import sys
from datetime import datetime, timezone, timedelta

from .colorme import ColorMeClient
from .spreadsheet import SpreadsheetClient
from .config import Config
from .cm_sheet_columns import Col, get_cell

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


def parse_args():
    parser = argparse.ArgumentParser(description="カラーミー商品説明をスプレッドシートに反映")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--since", type=str, help="更新日時の下限 (例: '2026-03-11 10:00')")
    group.add_argument("--hours", type=float, help="直近N時間以内に更新された商品")
    group.add_argument("--days", type=float, help="直近N日以内に更新された商品")
    group.add_argument("--all", action="store_true", help="全商品の説明を反映")
    parser.add_argument("--dry-run", action="store_true", help="対象商品の確認のみ（書き込みしない）")
    return parser.parse_args()


def get_cutoff_timestamp(args) -> int | None:
    """フィルター用のUNIXタイムスタンプを計算（--allの場合はNone）"""
    if args.all:
        logger.info("フィルター: 全商品")
        return None
    elif args.since:
        dt = datetime.strptime(args.since, "%Y-%m-%d %H:%M")
        dt = dt.replace(tzinfo=JST)
    elif args.hours:
        dt = datetime.now(JST) - timedelta(hours=args.hours)
    else:
        dt = datetime.now(JST) - timedelta(days=args.days)

    logger.info(f"フィルター: {dt.strftime('%Y-%m-%d %H:%M JST')} 以降")
    return int(dt.timestamp())


def main():
    args = parse_args()
    cutoff_ts = get_cutoff_timestamp(args)

    # カラーミーから商品取得
    colorme = ColorMeClient()
    logger.info("カラーミーから全商品を取得中...")
    products = colorme.get_all_products()
    logger.info(f"全商品数: {len(products)}件")

    # 更新日時でフィルター
    if cutoff_ts is None:
        recent = list(products)
    else:
        recent = [p for p in products if p.get("update_date", 0) >= cutoff_ts]
    recent.sort(key=lambda x: x.get("update_date", 0), reverse=True)
    logger.info(f"対象商品数: {len(recent)}件")

    if not recent:
        logger.info("対象商品がありません")
        return

    # 一覧表示
    print(f"\n{'No':>3} | {'商品ID':>10} | {'更新日時':>19} | 商品名")
    print("-" * 100)
    for i, p in enumerate(recent, 1):
        ud = datetime.fromtimestamp(p["update_date"], tz=JST)
        name = p.get("name", "")[:55]
        expl_len = len(p.get("expl", "") or "")
        simple_len = len(p.get("simple_expl", "") or "")
        print(f"{i:3} | {p['id']:>10} | {ud.strftime('%Y-%m-%d %H:%M:%S')} | {name}")
        print(f"      説明: {expl_len}文字 / 簡易説明: {simple_len}文字")

    if args.dry_run:
        print(f"\n[ドライラン] {len(recent)}件の商品が対象です。--dry-run を外して実行してください。")
        return

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

    # 既存データからカラーミー商品ID -> 行番号のマッピングを作成
    existing = sheet.get_all_values()
    pid_to_row = {}  # 商品ID -> (シート行番号, 行データ)
    for row_idx, row in enumerate(existing[1:], start=2):  # ヘッダー=1行目なので2から
        pid_val = get_cell(row, Col.PRODUCT_ID)
        if pid_val:
            try:
                pid_to_row[int(pid_val)] = (row_idx, row)
            except ValueError:
                pass

    logger.info(f"シート上の既存商品数: {len(pid_to_row)}件")

    # バッチ更新データを構築
    batch_data = []
    updated = 0
    skipped = 0
    not_found = 0

    for p in recent:
        pid = p["id"]
        row_info = pid_to_row.get(pid)

        if row_info is None:
            logger.warning(f"商品ID {pid} ({p.get('name', '')[:30]}) はシートに存在しません。スキップ。")
            not_found += 1
            continue

        row_num, existing_row = row_info

        # シートに既存値がある場合はスキップ（初回はAPI優先、運用後はシート優先）
        existing_expl = get_cell(existing_row, Col.EXPL)
        existing_simple_expl = get_cell(existing_row, Col.SIMPLE_EXPL)

        if existing_expl and existing_simple_expl:
            skipped += 1
            continue

        expl = p.get("expl", "") or ""
        simple_expl = p.get("simple_expl", "") or ""

        # BA列（商品説明）: シートが空の場合のみAPIの値を書き込み
        if not existing_expl:
            batch_data.append({
                "range": f"{Col.EXPL.letter}{row_num}",
                "values": [[expl]]
            })
        # BB列（簡易説明）: シートが空の場合のみAPIの値を書き込み
        if not existing_simple_expl:
            batch_data.append({
                "range": f"{Col.SIMPLE_EXPL.letter}{row_num}",
                "values": [[simple_expl]]
            })
        updated += 1

    if batch_data:
        # バッチ更新実行
        sheet.batch_update(batch_data, value_input_option="RAW")
        logger.info(f"スプレッドシート更新完了: {updated}件の商品説明を反映")
    else:
        logger.info("更新対象がありませんでした")

    if skipped:
        logger.info(f"シート既存値保持のためスキップ: {skipped}件")
    if not_found:
        logger.warning(f"{not_found}件の商品がシートに見つかりませんでした")

    print(f"\n完了: {updated}件更新 / {skipped}件スキップ（シート既存値保持） / {not_found}件スキップ（シートに未登録）")


if __name__ == "__main__":
    main()
