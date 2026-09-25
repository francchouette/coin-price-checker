"""
カラーミーAPIでSEO項目が送受信できるかを検証するテストスクリプト

使い方:
  python -m src.test_seo_api --product-id 190928711                        # 読み取りテストのみ
  python -m src.test_seo_api --product-id 190928711 --test-update          # 書き込みテストも実行
  python -m src.test_seo_api --product-id 190928711 --test-update --revert # 書き込み後に元に戻す
"""

import argparse
import json
import logging
import sys
import time

from .colorme import ColorMeClient
from .config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 検証対象のフィールド名候補（APIドキュメントが不明なため複数試す）
SEO_FIELD_CANDIDATES = [
    'title_tag',
    'meta_description',
    'meta_keywords',
    'page_title',
    'seo_title',
    'seo_description',
]


def main():
    parser = argparse.ArgumentParser(description="カラーミーAPIのSEO項目検証")
    parser.add_argument("--product-id", type=int, required=True, help="テスト対象の商品ID")
    parser.add_argument("--test-update", action="store_true", help="書き込みテストも実行")
    parser.add_argument("--revert", action="store_true", help="書き込み後に元の値に戻す")
    args = parser.parse_args()

    if not Config.is_colorme_enabled():
        logger.error("COLORME_ACCESS_TOKEN が設定されていません")
        sys.exit(1)

    client = ColorMeClient()

    # ================================
    # ステップ1: 商品情報をAPI経由で取得
    # ================================
    logger.info("=" * 60)
    logger.info(f"ステップ1: 商品ID {args.product_id} をAPIから取得")
    logger.info("=" * 60)

    product = client.get_product(args.product_id)
    if not product:
        logger.error("商品が見つかりません")
        sys.exit(1)

    logger.info(f"商品名: {product.get('name', '')[:60]}")
    logger.info("")
    logger.info("--- SEO関連フィールドの存在確認 ---")
    for field in SEO_FIELD_CANDIDATES:
        value = product.get(field)
        exists = "✅存在" if field in product else "❌なし"
        val_display = f'"{str(value)[:60]}..."' if value else ("(空)" if field in product else "-")
        logger.info(f"  {field:<25} {exists}  {val_display}")

    logger.info("")
    logger.info("--- 全フィールド一覧（APIレスポンス）---")
    for key in sorted(product.keys()):
        val = product[key]
        if isinstance(val, (dict, list)):
            val_display = f"[{type(val).__name__}]"
        else:
            val_display = str(val)[:80]
        logger.info(f"  {key:<30} = {val_display}")

    if not args.test_update:
        logger.info("")
        logger.info("読み取りテストのみ完了。--test-update で書き込みテストも実行できます。")
        return

    # ================================
    # ステップ2: 書き込みテスト
    # ================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("ステップ2: SEO項目の書き込みテスト")
    logger.info("=" * 60)

    original_title = product.get('title_tag', '')
    original_desc = product.get('meta_description', '')
    original_keywords = product.get('meta_keywords', '')

    test_marker = f" [★SEOテスト{int(time.time())}]"
    test_title = (original_title or "テスト") + test_marker
    test_desc = (original_desc or "テスト") + test_marker

    logger.info(f"元のtitle_tag: {original_title[:60]}")
    logger.info(f"テスト送信値: {test_title[:80]}")
    logger.info("")
    logger.info("APIに送信中...")

    updates = {
        "title_tag": test_title,
        "meta_description": test_desc,
    }

    success = client.update_product(args.product_id, updates)
    if not success:
        logger.error("APIリクエスト失敗")
        sys.exit(1)

    logger.info("APIリクエスト成功")
    logger.info("")
    logger.info("3秒待機後に再取得して反映確認...")
    time.sleep(3)

    # 再取得
    product2 = client.get_product(args.product_id)
    if product2:
        new_title = product2.get('title_tag', '')
        new_desc = product2.get('meta_description', '')

        logger.info("")
        logger.info("--- 結果 ---")
        logger.info(f"送信title_tag:    {test_title[:80]}")
        logger.info(f"取得title_tag:    {new_title[:80]}")
        logger.info(f"一致: {'✅反映されました' if new_title == test_title else '❌反映されていません'}")
        logger.info("")
        logger.info(f"送信meta_description: {test_desc[:80]}")
        logger.info(f"取得meta_description: {new_desc[:80]}")
        logger.info(f"一致: {'✅反映されました' if new_desc == test_desc else '❌反映されていません'}")

    # ================================
    # ステップ3: 元に戻す
    # ================================
    if args.revert:
        logger.info("")
        logger.info("=" * 60)
        logger.info("ステップ3: 元の値に戻す")
        logger.info("=" * 60)
        revert_updates = {
            "title_tag": original_title,
            "meta_description": original_desc,
        }
        client.update_product(args.product_id, revert_updates)
        logger.info("元の値に戻しました")


if __name__ == "__main__":
    main()
