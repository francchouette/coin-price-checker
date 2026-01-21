"""
CM商品名一括生成スクリプト

ブリオンスター商品ページ一覧シートの登録済み商品に対して、
スプレッドシートの既存情報からCM商品名（D列）を生成する。

スクレイピングは行わず、既存のG列（商品名）、M列（説明）、N列（仕様）を使用。

使用方法:
    python -m src.generate_cm_product_names [--dry-run] [--limit N] [--all]

オプション:
    --dry-run: 実際の更新を行わない（確認用）
    --limit N: 処理件数を制限
    --all: 登録済み以外も含めてD列が空の全商品を処理
"""

import argparse
import logging
import sys
import time
from datetime import timezone, timedelta
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.add_product import JapaneseProductNameGenerator
from src.bs_sheet_columns import Col, get_cell, get_cell_int, cell_ref

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))


def generate_cm_product_names(dry_run: bool = False, limit: int = None) -> int:
    """
    D列が空の商品のCM商品名を一括生成

    Args:
        dry_run: Trueの場合、実際の更新を行わない
        limit: 処理件数制限（Noneで全件）

    Returns:
        int: 更新した件数
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return 0

    # 日本語商品名生成器を初期化
    name_generator = JapaneseProductNameGenerator()

    try:
        # ブリオンスター商品ページ一覧シートを取得
        sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        all_data = sheet.get_all_values()

        if len(all_data) <= 1:
            logger.info("データがありません")
            return 0

        logger.info(f"総商品数: {len(all_data) - 1}件")

        # 対象商品を抽出
        # 条件: D列（CM商品名）が空で、商品名がある
        target_rows = []
        for row_idx, row in enumerate(all_data[1:], start=2):  # ヘッダースキップ、行番号は2から
            cm_name = get_cell(row, Col.CM_PRODUCT_NAME)
            product_name = get_cell(row, Col.PRODUCT_NAME)

            # D列が空で、商品名がある場合のみ対象
            if not cm_name and product_name:
                target_rows.append((row_idx, row))

        logger.info(f"処理対象: {len(target_rows)}件（D列が空の商品）")

        if limit:
            target_rows = target_rows[:limit]
            logger.info(f"件数制限適用: {len(target_rows)}件")

        if not target_rows:
            logger.info("処理対象の商品がありません")
            return 0

        # バッチ更新用リスト
        update_cells = []
        generated_count = 0
        failed_count = 0

        for row_idx, row in target_rows:
            product_name = get_cell(row, Col.PRODUCT_NAME)
            desc_en = get_cell(row, Col.DESC_EN)
            specs = get_cell(row, Col.SPECS)
            quantity = get_cell_int(row, Col.QUANTITY, 1)
            supplier_id = get_cell(row, Col.SUPPLIER_ID)

            # CM商品名を生成
            product_info = {
                "name": product_name,
                "specs": specs,
                "description": desc_en,
            }
            cm_product_name = name_generator.generate(product_info, quantity=quantity)

            if cm_product_name:
                update_cells.append({
                    'range': cell_ref(Col.CM_PRODUCT_NAME, row_idx),
                    'values': [[cm_product_name]]
                })
                generated_count += 1
                logger.info(f"  [{supplier_id}] {product_name[:40]}...")
                logger.info(f"    → {cm_product_name}")
            else:
                failed_count += 1
                logger.warning(f"  [{supplier_id}] 生成失敗: {product_name[:50]}...")

        logger.info(f"\n生成結果: 成功 {generated_count}件, 失敗 {failed_count}件")

        # スプレッドシートを更新
        if not dry_run and update_cells:
            logger.info(f"\nスプレッドシートを更新中...")

            # バッチ更新（100件ずつ）
            batch_size = 100
            for i in range(0, len(update_cells), batch_size):
                batch = update_cells[i:i + batch_size]
                sheet.batch_update(batch, value_input_option='RAW')
                logger.info(f"  更新完了: {min(i + batch_size, len(update_cells))}/{len(update_cells)}件")
                time.sleep(1)  # API制限対策

            logger.info(f"更新完了: {len(update_cells)}件")
        elif dry_run:
            logger.info("\n[ドライラン] 実際の更新は行いませんでした")

        return generated_count

    except Exception as e:
        logger.error(f"エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return 0


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description='登録済み商品のCM商品名を一括生成'
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
    logger.info("CM商品名一括生成")
    logger.info("=" * 60)
    logger.info(f"モード: {'ドライラン' if args.dry_run else '本番'}")
    logger.info(f"対象: D列が空の全商品")
    if args.limit:
        logger.info(f"件数制限: {args.limit}件")
    logger.info("=" * 60)

    start_time = time.time()
    count = generate_cm_product_names(
        dry_run=args.dry_run,
        limit=args.limit
    )
    elapsed = time.time() - start_time

    logger.info("=" * 60)
    logger.info("処理完了")
    logger.info(f"  生成件数: {count}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒")
    logger.info("=" * 60)

    return 0 if count >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
