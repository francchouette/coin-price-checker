"""
カテゴリー・グループ一括同期スクリプト

スプレッドシート上で変更されたカテゴリーID・グループIDを
カラーミーに一括反映する軽量スクリプト。

全フィールドを更新する sync_colorme_products とは異なり、
カテゴリーとグループのみを更新するため高速。

使用方法:
    python -m src.sync_categories
    python -m src.sync_categories --dry-run
    python -m src.sync_categories --only-changed   # 差分のみ更新
"""

import argparse
import logging
import sys
import time
from .spreadsheet import SpreadsheetClient
from .colorme import ColorMeClient
from .config import Config
from .cm_sheet_columns import Col, get_cell, get_cell_int

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_group_ids(value: str) -> list[int]:
    """グループIDの文字列をリストに変換

    スプレッドシートでは '123,456,789 の形式（先頭アポストロフィ付き）で保存。
    get_all_values()で読むとアポストロフィは除去されて 123,456,789 になる。
    """
    if not value:
        return []
    # 先頭のアポストロフィを除去
    cleaned = value.lstrip("'").strip()
    if not cleaned:
        return []
    try:
        return [int(g.strip()) for g in cleaned.split(",") if g.strip().isdigit() and int(g.strip()) > 0]
    except ValueError:
        return []


def main():
    parser = argparse.ArgumentParser(description="カテゴリー・グループ一括同期")
    parser.add_argument("--dry-run", action="store_true", help="実際の更新は行わない")
    parser.add_argument("--include-groups", action="store_true",
                        help="グループIDも更新する（デフォルトはカテゴリーのみ）")
    parser.add_argument("--only-changed", action="store_true",
                        help="カラーミーAPIから現在値を取得し、変更があるものだけ更新する")
    parser.add_argument("--limit", type=int, default=0, help="更新件数上限（テスト用）")
    args = parser.parse_args()

    logger.info("=== カテゴリー・グループ一括同期 ===")

    # 設定検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    if not Config.is_colorme_enabled():
        logger.error("カラーミーアクセストークンが設定されていません")
        sys.exit(1)

    # スプレッドシート接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシート接続失敗")
        sys.exit(1)

    colorme = ColorMeClient(dry_run=args.dry_run)

    # シートデータ読み取り
    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
        all_data = sheet.get_all_values()
    except Exception as e:
        logger.error(f"シート読み込みエラー: {e}")
        sys.exit(1)

    if len(all_data) <= 1:
        logger.info("データがありません")
        return

    # 更新対象を収集（商品IDがある全行）
    targets = []
    for row_num, row in enumerate(all_data[1:], start=2):
        product_id = get_cell_int(row, Col.PRODUCT_ID)
        if product_id <= 0:
            continue

        category_id = get_cell_int(row, Col.CATEGORY_ID_BIG)
        group_ids = []
        if args.include_groups:
            group_ids_str = get_cell(row, Col.GROUP_IDS)
            group_ids = parse_group_ids(group_ids_str)
        product_name = get_cell(row, Col.NAME)[:40]

        if category_id <= 0 and not group_ids:
            continue

        targets.append({
            "product_id": product_id,
            "name": product_name,
            "row_num": row_num,
            "category_id_big": category_id,
            "group_ids": group_ids,
        })

    logger.info(f"対象商品: {len(targets)}件")

    if not targets:
        logger.info("更新対象がありません")
        return

    if args.limit > 0:
        targets = targets[:args.limit]
        logger.info(f"件数制限: {args.limit}件に絞り込み")

    # --only-changed: カラーミーから現在値を取得して比較
    if args.only_changed:
        logger.info("カラーミーから現在のカテゴリー・グループを取得中...")
        changed_targets = []
        for i, t in enumerate(targets):
            product = colorme.get_product(t["product_id"])
            if not product:
                logger.warning(f"  商品取得失敗: ID {t['product_id']}")
                continue

            current_category = (product.get("category") or {})
            current_cat_id = current_category.get("id_big", 0) if isinstance(current_category, dict) else 0
            current_groups = sorted(product.get("group_ids") or [])

            new_cat_id = t["category_id_big"]
            new_groups = sorted(t["group_ids"])

            if current_cat_id != new_cat_id or current_groups != new_groups:
                t["current_category"] = current_cat_id
                t["current_groups"] = current_groups
                changed_targets.append(t)

            if (i + 1) % 50 == 0:
                logger.info(f"  比較中: {i + 1}/{len(targets)}件")
                time.sleep(1)

        logger.info(f"変更あり: {len(changed_targets)}/{len(targets)}件")
        targets = changed_targets

        if not targets:
            logger.info("変更対象がありません")
            return

    # 更新実行
    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, t in enumerate(targets):
        product_id = t["product_id"]
        name = t["name"]
        cat_id = t["category_id_big"]
        group_ids = t["group_ids"]

        updates = {}
        log_parts = []

        if cat_id > 0:
            updates["category_id_big"] = cat_id
            log_parts.append(f"カテゴリー: {cat_id}")

        if group_ids:
            updates["group_ids"] = group_ids
            log_parts.append(f"グループ: {group_ids}")

        if not updates:
            skip_count += 1
            continue

        logger.info(f"[{i+1}/{len(targets)}] {name} (ID: {product_id}) → {', '.join(log_parts)}")

        if colorme.update_product(product_id, updates):
            success_count += 1
        else:
            fail_count += 1

        # レートリミット対策
        if (i + 1) % 100 == 0:
            time.sleep(1)

    # 結果
    logger.info("")
    logger.info("=== 結果 ===")
    logger.info(f"更新成功: {success_count}件")
    logger.info(f"更新失敗: {fail_count}件")
    logger.info(f"スキップ: {skip_count}件")

    if fail_count > 0:
        logger.warning("一部の商品で更新に失敗しました")
        sys.exit(1)

    logger.info("=== カテゴリー・グループ同期完了 ===")


if __name__ == "__main__":
    main()
