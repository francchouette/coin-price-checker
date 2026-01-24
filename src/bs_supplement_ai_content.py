"""
ブリオンスター商品のAI生成コンテンツ補完スクリプト

ブリオンスター商品ページ一覧シートの既存商品に対して、
空欄になっているフィールドを再生成・補完する。

対象フィールド:
- D列（カラーミー商品名）: Col.CM_PRODUCT_NAME - JapaneseProductNameGenerator
- V-X列（為替種類/為替レート/日本円換算）: 為替レート取得
- AO-AT列（カテゴリー・グループ）: CategoryDetector
- AU列（型番）: Col.CM_MODEL_NUMBER - ModelNumberGenerator
- BG列（商品説明）: Col.CM_EXPL - DescriptionGenerator
- BH列（簡易説明）: Col.CM_SIMPLE_EXPL - DescriptionGenerator
- BU列（ページタイトル）: Col.CM_PAGE_TITLE - SEOGenerator
- BV列（メタディスクリプション）: Col.CM_META_DESC - SEOGenerator
- BW列（メタキーワード）: Col.CM_META_KEYWORDS - SEOGenerator

想定オペレーション:
1. ユーザーがシートを確認
2. 生成が漏れた部分はそのまま（空欄）
3. 生成がおかしい部分はクリア（空欄に）
4. このスクリプトを実行 → 空欄箇所のみ再生成

使用方法:
    python -m src.bs_supplement_ai_content [--dry-run] [--limit N] [--target TARGET]

オプション:
    --dry-run: 実際の更新を行わない（確認用）
    --limit N: 処理件数を制限
    --target: 生成対象を指定（all/name/exchange/category/model/description/seo）
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.add_product import (
    JapaneseProductNameGenerator,
    CategoryDetector,
    ModelNumberGenerator,
    DescriptionGenerator,
    SEOGenerator,
)
from src.colorme import ColorMeClient
from src.exchange_rate import ExchangeRateClient, WiseRateClient
from src.bs_sheet_columns import Col, get_cell, get_cell_int, get_cell_float, cell_ref

logger = logging.getLogger(__name__)


def fetch_exchange_rate(currency: str, exchange_type: str = "クレカ") -> float:
    """
    指定通貨の為替レートを取得

    Args:
        currency: 通貨コード（USD, SGD等）
        exchange_type: 為替種類（"クレカ" または "Wise"）

    Returns:
        float: 為替レート（取得失敗時は0.0）
    """
    if not currency or currency.upper() == "JPY":
        return 1.0

    try:
        if exchange_type == "Wise":
            client = WiseRateClient()
        else:
            client = ExchangeRateClient()

        rate = client.get_rate(currency.upper(), "JPY")
        return rate if rate else 0.0
    except Exception as e:
        logger.warning(f"為替レート取得エラー ({currency}): {e}")
        return 0.0


def supplement_ai_content(
    dry_run: bool = False,
    limit: int = None,
    target: str = "all",
    exchange_type: str = "クレカ"
) -> dict:
    """
    空欄になっているフィールドを補完する

    Args:
        dry_run: Trueの場合、実際の更新を行わない
        limit: 処理件数制限（Noneで全件）
        target: 生成対象（all/name/exchange/category/model/description/seo）
        exchange_type: 為替種類（"クレカ" または "Wise"）

    Returns:
        dict: 更新結果の統計
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return {"error": "connection_failed"}

    # 生成器を初期化
    name_generator = None
    category_detector = None
    model_generator = None
    desc_generator = None
    seo_generator = None

    # カテゴリー名・グループ名マップ
    category_name_map = {}
    group_name_map = {}

    if target in ("all", "name"):
        name_generator = JapaneseProductNameGenerator()
        logger.info("CM商品名生成器を初期化")

    # 為替レートキャッシュ（同じ通貨は再取得しない）
    exchange_rate_cache = {}

    if target in ("all", "exchange"):
        logger.info(f"為替レート補完モード（{exchange_type}）")

    if target in ("all", "category"):
        try:
            colorme_client = ColorMeClient()
            categories = colorme_client.get_categories()
            groups = colorme_client.get_groups()
            category_detector = CategoryDetector(categories, groups, colorme_client)

            # カテゴリー名・グループ名マップ構築
            for cat in categories:
                category_name_map[cat.get("id")] = cat.get("name", "")
            for grp in groups:
                group_name_map[grp.get("id")] = grp.get("name", "")

            logger.info("カテゴリー判定器を初期化")
        except Exception as e:
            logger.warning(f"カテゴリー判定器の初期化エラー: {e}")

    if target in ("all", "model"):
        model_generator = ModelNumberGenerator()
        logger.info("型番生成器を初期化")

    if target in ("all", "description"):
        desc_generator = DescriptionGenerator()
        logger.info("商品説明生成器を初期化")

    if target in ("all", "seo"):
        seo_generator = SEOGenerator()
        logger.info("SEO生成器を初期化")

    try:
        # ブリオンスター商品ページ一覧シートを取得
        sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        all_data = sheet.get_all_values()

        if len(all_data) <= 1:
            logger.info("データがありません")
            return {"processed": 0}

        logger.info(f"総商品数: {len(all_data) - 1}件")

        # 補完対象の商品を抽出
        target_rows = []
        for row_idx, row in enumerate(all_data[1:], start=2):  # ヘッダースキップ
            product_name = get_cell(row, Col.PRODUCT_NAME)
            if not product_name:
                continue  # 商品名がない行はスキップ

            # 各フィールドの空欄状態をチェック
            needs = {
                "name": False,
                "exchange": False,
                "category": False,
                "model": False,
                "description": False,
                "seo": False,
            }

            if target in ("all", "name"):
                cm_name = get_cell(row, Col.CM_PRODUCT_NAME)
                if not cm_name:
                    needs["name"] = True

            if target in ("all", "exchange"):
                # 為替レートが空欄で、通貨がJPY以外で、価格がある場合
                currency = get_cell(row, Col.CURRENCY)
                exchange_rate = get_cell(row, Col.EXCHANGE_RATE)
                price = get_cell(row, Col.PRICE)
                if price and currency and currency.upper() != "JPY" and not exchange_rate:
                    needs["exchange"] = True

            if target in ("all", "category"):
                cat_big = get_cell(row, Col.CM_CATEGORY_BIG)
                if not cat_big:
                    needs["category"] = True

            if target in ("all", "model"):
                model_number = get_cell(row, Col.CM_MODEL_NUMBER)
                if not model_number:
                    needs["model"] = True

            if target in ("all", "description"):
                cm_expl = get_cell(row, Col.CM_EXPL)
                cm_simple_expl = get_cell(row, Col.CM_SIMPLE_EXPL)
                if not cm_expl or not cm_simple_expl:
                    needs["description"] = True

            if target in ("all", "seo"):
                page_title = get_cell(row, Col.CM_PAGE_TITLE)
                meta_desc = get_cell(row, Col.CM_META_DESC)
                meta_keywords = get_cell(row, Col.CM_META_KEYWORDS)
                if not page_title or not meta_desc or not meta_keywords:
                    needs["seo"] = True

            if any(needs.values()):
                target_rows.append({
                    "row_idx": row_idx,
                    "row": row,
                    "needs": needs,
                })

        logger.info(f"補完対象: {len(target_rows)}件")

        if limit:
            target_rows = target_rows[:limit]
            logger.info(f"件数制限適用: {len(target_rows)}件")

        if not target_rows:
            logger.info("補完対象の商品がありません")
            return {"processed": 0}

        # バッチ更新用リスト
        update_cells = []
        stats = {
            "processed": 0,
            "name_generated": 0,
            "name_failed": 0,
            "exchange_generated": 0,
            "exchange_failed": 0,
            "category_generated": 0,
            "category_failed": 0,
            "model_generated": 0,
            "model_failed": 0,
            "desc_generated": 0,
            "desc_failed": 0,
            "seo_generated": 0,
            "seo_failed": 0,
        }

        for item in target_rows:
            row_idx = item["row_idx"]
            row = item["row"]
            needs = item["needs"]

            supplier_id = get_cell(row, Col.SUPPLIER_ID)
            product_name = get_cell(row, Col.PRODUCT_NAME)
            product_url = get_cell(row, Col.PRODUCT_URL)
            desc_en = get_cell(row, Col.DESC_EN)
            specs = get_cell(row, Col.SPECS)
            price_jpy = get_cell(row, Col.PRICE_JPY)
            quantity = get_cell_int(row, Col.QUANTITY, 1)

            logger.info(f"\n[{supplier_id}] {product_name[:50]}...")

            # 商品情報を構築
            product_info = {
                "name": product_name,
                "description": desc_en,
                "specs": specs,
                "price": price_jpy,
                "currency": "JPY",
            }

            # 1. CM商品名の生成
            if needs["name"] and name_generator:
                logger.info("  CM商品名を生成中...")
                cm_name = name_generator.generate(product_info, quantity=quantity)
                if cm_name:
                    update_cells.append({
                        'range': cell_ref(Col.CM_PRODUCT_NAME, row_idx),
                        'values': [[cm_name]]
                    })
                    stats["name_generated"] += 1
                    logger.info(f"    → {cm_name}")
                else:
                    stats["name_failed"] += 1
                    logger.warning("    CM商品名の生成に失敗")

            # 2. 為替レートの補完
            if needs["exchange"]:
                currency = get_cell(row, Col.CURRENCY)
                price = get_cell_float(row, Col.PRICE, 0.0)
                logger.info(f"  為替レートを取得中... ({currency})")

                # キャッシュをチェック
                if currency.upper() in exchange_rate_cache:
                    rate = exchange_rate_cache[currency.upper()]
                else:
                    rate = fetch_exchange_rate(currency, exchange_type)
                    exchange_rate_cache[currency.upper()] = rate

                if rate > 0:
                    price_jpy = round(price * rate, 0)
                    update_cells.append({
                        'range': cell_ref(Col.EXCHANGE_TYPE, row_idx),
                        'values': [[exchange_type]]
                    })
                    update_cells.append({
                        'range': cell_ref(Col.EXCHANGE_RATE, row_idx),
                        'values': [[str(rate)]]
                    })
                    update_cells.append({
                        'range': cell_ref(Col.PRICE_JPY, row_idx),
                        'values': [[str(int(price_jpy))]]
                    })
                    stats["exchange_generated"] += 1
                    logger.info(f"    レート: {rate}, 日本円: {int(price_jpy)}")
                else:
                    stats["exchange_failed"] += 1
                    logger.warning(f"    為替レート取得に失敗 ({currency})")

            # 3. カテゴリー・グループの生成
            if needs["category"] and category_detector:
                logger.info("  カテゴリーを判定中...")
                try:
                    cat_big, cat_small, group_ids = category_detector.detect(product_name, product_url)
                    if cat_big:
                        update_cells.append({
                            'range': cell_ref(Col.CM_CATEGORY_BIG, row_idx),
                            'values': [[str(cat_big)]]
                        })
                        cat_big_name = category_name_map.get(cat_big, "")
                        if cat_big_name:
                            update_cells.append({
                                'range': cell_ref(Col.CM_CATEGORY_BIG_NAME, row_idx),
                                'values': [[cat_big_name]]
                            })
                        if cat_small:
                            update_cells.append({
                                'range': cell_ref(Col.CM_CATEGORY_SMALL, row_idx),
                                'values': [[str(cat_small)]]
                            })
                            cat_small_name = category_name_map.get(cat_small, "")
                            if cat_small_name:
                                update_cells.append({
                                    'range': cell_ref(Col.CM_CATEGORY_SMALL_NAME, row_idx),
                                    'values': [[cat_small_name]]
                                })
                        if group_ids:
                            update_cells.append({
                                'range': cell_ref(Col.CM_GROUP_ID, row_idx),
                                'values': [["'" + ",".join(str(g) for g in group_ids)]]
                            })
                            group_names = [group_name_map.get(g, "") for g in group_ids]
                            group_names_str = ",".join(n for n in group_names if n)
                            if group_names_str:
                                update_cells.append({
                                    'range': cell_ref(Col.CM_GROUP_NAME, row_idx),
                                    'values': [[group_names_str]]
                                })
                        stats["category_generated"] += 1
                        logger.info(f"    大カテゴリ: {cat_big}({cat_big_name})")
                    else:
                        stats["category_failed"] += 1
                        logger.warning("    カテゴリー判定に失敗")
                except Exception as e:
                    stats["category_failed"] += 1
                    logger.warning(f"    カテゴリー判定エラー: {e}")

            # 4. 型番の生成
            if needs["model"] and model_generator:
                logger.info("  型番を生成中...")
                model_number = model_generator.generate(product_info, quantity)
                if model_number:
                    update_cells.append({
                        'range': cell_ref(Col.CM_MODEL_NUMBER, row_idx),
                        'values': [[model_number]]
                    })
                    stats["model_generated"] += 1
                    logger.info(f"    → {model_number}")
                else:
                    stats["model_failed"] += 1
                    logger.warning("    型番の生成に失敗")

            # 5. 商品説明の生成
            if needs["description"] and desc_generator:
                cm_expl = get_cell(row, Col.CM_EXPL)
                cm_simple_expl = get_cell(row, Col.CM_SIMPLE_EXPL)

                if not cm_expl or not cm_simple_expl:
                    logger.info("  商品説明を生成中...")
                    desc, simple_desc = desc_generator.generate(product_info)

                    if desc or simple_desc:
                        if not cm_expl and desc:
                            update_cells.append({
                                'range': cell_ref(Col.CM_EXPL, row_idx),
                                'values': [[desc]]
                            })
                            logger.info(f"    商品説明: {len(desc)}文字")
                        if not cm_simple_expl and simple_desc:
                            update_cells.append({
                                'range': cell_ref(Col.CM_SIMPLE_EXPL, row_idx),
                                'values': [[simple_desc]]
                            })
                            logger.info(f"    簡易説明: {len(simple_desc)}文字")
                        stats["desc_generated"] += 1
                    else:
                        stats["desc_failed"] += 1
                        logger.warning("    商品説明の生成に失敗")

            # 6. SEO情報の生成
            if needs["seo"] and seo_generator:
                page_title = get_cell(row, Col.CM_PAGE_TITLE)
                meta_desc = get_cell(row, Col.CM_META_DESC)
                meta_keywords = get_cell(row, Col.CM_META_KEYWORDS)

                if not page_title or not meta_desc or not meta_keywords:
                    logger.info("  SEO情報を生成中...")
                    new_title, new_desc, new_keywords = seo_generator.generate(product_info)

                    if new_title or new_desc or new_keywords:
                        if not page_title and new_title:
                            update_cells.append({
                                'range': cell_ref(Col.CM_PAGE_TITLE, row_idx),
                                'values': [[new_title]]
                            })
                            logger.info(f"    タイトル: {new_title[:40]}...")
                        if not meta_desc and new_desc:
                            update_cells.append({
                                'range': cell_ref(Col.CM_META_DESC, row_idx),
                                'values': [[new_desc]]
                            })
                            logger.info(f"    メタ説明: {len(new_desc)}文字")
                        if not meta_keywords and new_keywords:
                            update_cells.append({
                                'range': cell_ref(Col.CM_META_KEYWORDS, row_idx),
                                'values': [[new_keywords]]
                            })
                            logger.info(f"    キーワード: {new_keywords[:40]}...")
                        stats["seo_generated"] += 1
                    else:
                        stats["seo_failed"] += 1
                        logger.warning("    SEO情報の生成に失敗")

            stats["processed"] += 1

            # API制限対策：各商品の処理後に少し待機
            time.sleep(0.5)

        logger.info(f"\n生成結果:")
        logger.info(f"  処理件数: {stats['processed']}件")
        logger.info(f"  CM商品名: 成功 {stats['name_generated']}件, 失敗 {stats['name_failed']}件")
        logger.info(f"  為替レート: 成功 {stats['exchange_generated']}件, 失敗 {stats['exchange_failed']}件")
        logger.info(f"  カテゴリー: 成功 {stats['category_generated']}件, 失敗 {stats['category_failed']}件")
        logger.info(f"  型番: 成功 {stats['model_generated']}件, 失敗 {stats['model_failed']}件")
        logger.info(f"  商品説明: 成功 {stats['desc_generated']}件, 失敗 {stats['desc_failed']}件")
        logger.info(f"  SEO情報: 成功 {stats['seo_generated']}件, 失敗 {stats['seo_failed']}件")

        # スプレッドシートを更新
        if not dry_run and update_cells:
            logger.info(f"\nスプレッドシートを更新中... ({len(update_cells)}セル)")

            # バッチ更新（100件ずつ）
            batch_size = 100
            for i in range(0, len(update_cells), batch_size):
                batch = update_cells[i:i + batch_size]
                sheet.batch_update(batch, value_input_option='RAW')
                logger.info(f"  更新完了: {min(i + batch_size, len(update_cells))}/{len(update_cells)}件")
                time.sleep(1)  # API制限対策

            logger.info(f"更新完了: {len(update_cells)}セル")
        elif dry_run:
            logger.info(f"\n[ドライラン] 実際の更新は行いませんでした（{len(update_cells)}セル予定）")

        return stats

    except Exception as e:
        logger.error(f"エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description='ブリオンスター商品のAI生成コンテンツ補完'
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
        '--target',
        choices=['all', 'name', 'exchange', 'category', 'model', 'description', 'seo'],
        default='all',
        help='生成対象（all=全て, name=CM商品名, exchange=為替レート, category=カテゴリー, model=型番, description=商品説明, seo=SEO情報）'
    )
    parser.add_argument(
        '--exchange-type',
        choices=['クレカ', 'Wise'],
        default='クレカ',
        help='為替種類（デフォルト: クレカ）'
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
    logger.info("AI生成コンテンツ補完スクリプト")
    logger.info("=" * 60)
    logger.info(f"モード: {'ドライラン' if args.dry_run else '本番'}")
    logger.info(f"対象: {args.target}")
    if args.limit:
        logger.info(f"件数制限: {args.limit}件")
    logger.info("=" * 60)

    start_time = time.time()
    result = supplement_ai_content(
        dry_run=args.dry_run,
        limit=args.limit,
        target=args.target,
        exchange_type=args.exchange_type
    )
    elapsed = time.time() - start_time

    logger.info("=" * 60)
    logger.info("処理完了")
    if "error" not in result:
        logger.info(f"  処理件数: {result.get('processed', 0)}件")
        logger.info(f"  CM商品名: {result.get('name_generated', 0)}件")
        logger.info(f"  為替レート: {result.get('exchange_generated', 0)}件")
        logger.info(f"  カテゴリー: {result.get('category_generated', 0)}件")
        logger.info(f"  型番: {result.get('model_generated', 0)}件")
        logger.info(f"  商品説明: {result.get('desc_generated', 0)}件")
        logger.info(f"  SEO: {result.get('seo_generated', 0)}件")
    logger.info(f"  所要時間: {elapsed:.1f}秒")
    logger.info("=" * 60)

    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
