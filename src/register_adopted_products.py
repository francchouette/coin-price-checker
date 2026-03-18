"""
採用商品カラーミー登録スクリプト

ブリオンスター商品ページ一覧（84列: A-CF）で A列=「採用」かつ B列≠「登録済」の商品を
カラーミーAPIで自動登録し、登録完了後に以下を実行する：

1. B列（カラーミー登録状況）を「登録済」に更新
2. E列（カラーミー商品URL）を更新
3. CE列（同期日時）を更新
4. 商品仕入れ先一覧シートに同期

シート構造（84列: A-CF）:
- A-C列: 管理列（採用フラグ、登録状況、仕入れ先商品ID）
- D列: CM商品名（AI生成）
- E-Q列: 仕入れ先商品情報（13列）
- R-AH列: 価格情報（17列）
- AI-AN列: CM価格情報（6列）
- AO-AT列: カテゴリー・グループ（ID・名称: 6列）
- AU列: 型番
- AV-CF列: カラーミー登録用項目

トリガー:
- A列を「採用」に変更後、このスクリプトを実行

使用方法:
    python -m src.register_adopted_products
    python -m src.register_adopted_products --dry-run  # 実際の登録なし
    python -m src.register_adopted_products --limit 5  # 5件のみ処理
"""

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.colorme import ColorMeClient, ColorMeProduct
from src.sync_supplier_list import sync_registered_products_to_supplier_list
from src.add_product import CategoryDetector, DescriptionGenerator, SEOGenerator, JapaneseProductNameGenerator, ModelNumberGenerator
from src.colorme_image_uploader import ColorMeImageUploader
from src.bs_sheet_columns import Col, get_cell, get_cell_float, cell_ref

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

# デフォルトのカテゴリーID（貴金属コイン）
DEFAULT_CATEGORY_BIG = 174936    # 大カテゴリー: 金貨・銀貨・プラチナコイン
DEFAULT_CATEGORY_SMALL = 174938  # 小カテゴリー: その他金貨


def batch_update_with_retry(sheet, batch_data: list, max_retries: int = 3, retry_delay: int = 10):
    """
    スプレッドシートのbatch_updateをリトライ付きで実行

    ネットワークエラーや認証トークン更新エラーを回避するため、
    失敗時にリトライを行う
    """
    import copy
    for attempt in range(max_retries):
        try:
            # gspreadはbatch_dataを変更するため、コピーを使用
            data_copy = copy.deepcopy(batch_data)
            sheet.batch_update(data_copy, value_input_option='RAW')
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"  スプレッドシート更新エラー（リトライ {attempt + 1}/{max_retries}）: {e}")
                logger.info(f"  {retry_delay}秒後にリトライ...")
                time.sleep(retry_delay)
            else:
                logger.error(f"  スプレッドシート更新エラー（最終）: {e}")
                raise


def map_category(top_category: str, parent_category: str, child_category: str) -> tuple[int, int]:
    """
    Bullionstarカテゴリーをカラーミーカテゴリーにマッピング

    Returns:
        (大カテゴリーID, 小カテゴリーID)
    """
    # TODO: カテゴリーマッピング表を作成して精緻化
    # 現在は暫定的にデフォルトカテゴリーを返す

    # Gold Coins -> 金貨
    if "gold" in top_category.lower() or "gold" in parent_category.lower():
        return (174936, 174937)  # 金貨・銀貨 > 金貨

    # Silver Coins -> 銀貨
    if "silver" in top_category.lower() or "silver" in parent_category.lower():
        return (174936, 174939)  # 金貨・銀貨 > 銀貨

    # Platinum -> プラチナ
    if "platinum" in top_category.lower() or "platinum" in parent_category.lower():
        return (174936, 174940)  # 金貨・銀貨 > プラチナコイン

    return (DEFAULT_CATEGORY_BIG, DEFAULT_CATEGORY_SMALL)


async def register_adopted_products(
    dry_run: bool = False,
    limit: Optional[int] = None,
    upload_images: bool = False,
    source: str = "bs"
) -> tuple[int, int, int]:
    """
    採用商品をカラーミーに登録

    Args:
        dry_run: Trueの場合、実際の登録は行わない
        limit: 処理する最大件数（デバッグ用）
        upload_images: Trueの場合、Playwright経由で画像をアップロード
        source: 対象シート（"bs"=ブリオンスター, "ap"=APMEX）

    Returns:
        (登録成功数, 登録失敗数, スキップ数)
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return (0, 0, 0)

    colorme_client = ColorMeClient(dry_run=dry_run)

    # カテゴリー・グループ情報を取得してCategoryDetectorを初期化
    categories = []
    groups = []
    category_detector = None
    # カテゴリー・グループ名称引き用辞書
    category_name_map = {}  # {(id_big, id_small): (name_big, name_small)}
    group_name_map = {}     # {group_id: group_name}
    try:
        categories = colorme_client.get_categories()
        groups = colorme_client.get_groups()
        category_detector = CategoryDetector(categories, groups, colorme_client)

        # カテゴリー名称マップを構築
        for cat in categories:
            id_big = cat.get("id_big", 0)
            id_small = cat.get("id_small", 0)
            name_big = cat.get("name_big", "")
            name_small = cat.get("name_small", "")
            category_name_map[(id_big, id_small)] = (name_big, name_small)

        # グループ名称マップを構築
        for grp in groups:
            grp_id = grp.get("id", 0)
            grp_name = grp.get("name", "")
            group_name_map[grp_id] = grp_name

        logger.info(f"カテゴリー: {len(categories)}件, グループ: {len(groups)}件 を取得")
    except Exception as e:
        logger.warning(f"カテゴリー/グループ取得エラー: {e}")
        logger.warning("カテゴリー自動判定は無効化されます")

    # AI生成器を初期化（商品説明・SEO・商品名）
    description_generator = DescriptionGenerator()
    seo_generator = SEOGenerator()
    name_generator = JapaneseProductNameGenerator()

    try:
        # 対象シートを取得
        source_name = "APMEX" if source == "ap" else "ブリオンスター"
        sheet_name = Config.SHEET_APMEX_PRODUCTS if source == "ap" else Config.SHEET_BULLIONSTAR_PRODUCTS
        bs_sheet = client._spreadsheet.worksheet(sheet_name)
        bs_data = bs_sheet.get_all_values()

        if len(bs_data) <= 1:
            logger.info(f"{source_name}商品ページ一覧にデータがありません")
            return (0, 0, 0)

        # 既存の型番を収集して重複チェック用にModelNumberGeneratorを初期化
        existing_model_numbers = set()
        for row in bs_data[1:]:
            model_num = get_cell(row, Col.CM_MODEL_NUMBER)
            if model_num:
                existing_model_numbers.add(model_num)
        model_number_generator = ModelNumberGenerator(existing_model_numbers)
        logger.info(f"型番生成器を初期化（既存型番: {len(existing_model_numbers)}件）")

        # A列=「採用」かつ B列≠「登録済」の商品をフィルタ
        target_products = []
        for row_idx, row in enumerate(bs_data[1:], start=2):
            adopted_flag = get_cell(row, Col.ADOPTED_FLAG)
            registration_status = get_cell(row, Col.REGISTRATION_STATUS)

            if adopted_flag == "採用" and registration_status != "登録済":
                target_products.append((row_idx, row))

        if not target_products:
            logger.info("登録対象の商品がありません（A列=「採用」かつ B列≠「登録済」）")
            return (0, 0, 0)

        if limit:
            target_products = target_products[:limit]

        logger.info(f"登録対象商品数: {len(target_products)}件")

        success_count = 0
        error_count = 0
        skip_count = 0

        # Playwright用ブラウザセッションを1回だけ起動（全商品で再利用）
        uploader = None
        if upload_images:
            uploader = ColorMeImageUploader(headless=True)
            await uploader.__aenter__()
            logger.info("Playwrightブラウザセッション開始（全商品で再利用）")

        try:
            for row_idx, row in target_products:
                product_name = get_cell(row, Col.PRODUCT_NAME)  # G列: 仕入れ先商品名（英語）
                product_url = get_cell(row, Col.PRODUCT_URL)
                supplier_id = get_cell(row, Col.SUPPLIER_ID)
                supplier_site = get_cell(row, Col.SITE)  # H列: 仕入れ先サイト（型番生成用）

                logger.info(f"処理中: {product_name[:50]}... (行{row_idx})")

                # 必須情報チェック
                if not product_name:
                    logger.warning(f"  スキップ: 商品名がありません (行{row_idx})")
                    skip_count += 1
                    continue

                # 価格情報取得
                price_jpy = get_cell_float(row, Col.PRICE_JPY)
                if price_jpy <= 0:
                    # 日本円換算価格がない場合は計算を試みる
                    price = get_cell_float(row, Col.PRICE)
                    exchange_rate = get_cell_float(row, Col.EXCHANGE_RATE, 1.0)
                    price_jpy = price * exchange_rate

                if price_jpy <= 0:
                    logger.warning(f"  スキップ: 価格情報がありません (行{row_idx})")
                    skip_count += 1
                    continue

                # CM商品名 - D列に既存値があればそれを使用、なければ自動生成
                existing_cm_name = get_cell(row, Col.CM_PRODUCT_NAME)  # D列: CM商品名
                desc_en = get_cell(row, Col.DESC_EN)  # M列: 商品説明（英語）
                specs = get_cell(row, Col.SPECS)      # N列: 仕様・スペック

                if existing_cm_name:
                    # D列に既存のCM商品名がある場合はそれを使用
                    cm_product_name = existing_cm_name
                    logger.info(f"  CM商品名: D列の既存値を使用 → {cm_product_name[:50]}...")
                else:
                    # JapaneseProductNameGeneratorで自動生成
                    product_info = {
                        "name": product_name,
                        "specs": specs,
                        "description": desc_en,
                    }
                    cm_product_name = name_generator.generate(product_info, quantity=1)
                    if cm_product_name:
                        logger.info(f"  CM商品名: 自動生成 → {cm_product_name[:50]}...")
                    else:
                        # フォールバック: 仕入れ先商品名をそのまま使用
                        cm_product_name = product_name
                        logger.info(f"  CM商品名: フォールバック（仕入れ先商品名を使用）")

                # カテゴリー・グループ自動判定（CategoryDetector使用）
                category_big = 0
                category_small = 0
                group_ids = []

                # まずスプレッドシートの既存値を確認
                existing_cat_big = get_cell(row, Col.CM_CATEGORY_BIG)
                existing_cat_small = get_cell(row, Col.CM_CATEGORY_SMALL)
                existing_group_id = get_cell(row, Col.CM_GROUP_ID)

                if existing_cat_big:
                    # 既存値がある場合はそれを使用
                    try:
                        category_big = int(existing_cat_big)
                        category_small = int(existing_cat_small) if existing_cat_small else 0
                        if existing_group_id:
                            # 先頭のシングルクォート（テキスト保存用）を除去
                            group_id_str = existing_group_id.lstrip("'")
                            group_ids = [int(g.strip()) for g in group_id_str.split(",") if g.strip().isdigit()]
                        logger.info(f"  カテゴリー(既存値使用): 大={category_big}, 小={category_small}, グループ={group_ids}")
                    except ValueError as e:
                        logger.warning(f"  カテゴリー解析エラー: {e}")
                        pass

                if not category_big:
                    # CategoryDetectorで自動判定
                    if category_detector:
                        category_big, category_small, group_ids = category_detector.detect(product_name, product_url)
                        logger.info(f"  カテゴリー(自動判定): 大={category_big}, 小={category_small}, グループ={group_ids}")
                    else:
                        # フォールバック: 従来のmap_category関数を使用
                        top_cat = get_cell(row, Col.TOP_CATEGORY)
                        parent_cat = get_cell(row, Col.PARENT_CATEGORY)
                        category_big, category_small = map_category(top_cat, parent_cat, "")
                        logger.info(f"  カテゴリー(フォールバック): 大={category_big}, 小={category_small}")

                # 商品説明（BC列）- 既存値があればそれを使用、なければAI生成
                # desc_en, specsは上で取得済み

                # カラーミー用説明（BC列）をチェック
                existing_cm_expl = get_cell(row, Col.CM_EXPL)
                existing_simple_expl = get_cell(row, Col.CM_SIMPLE_EXPL)

                # SEO項目（BR-BT列）をチェック
                existing_page_title = get_cell(row, Col.CM_PAGE_TITLE)
                existing_meta_desc = get_cell(row, Col.CM_META_DESC)
                existing_meta_keywords = get_cell(row, Col.CM_META_KEYWORDS)

                description = ""
                simple_description = ""
                page_title = ""
                meta_description = ""
                meta_keywords = ""

                # 商品説明の取得またはAI生成
                if existing_cm_expl:
                    # 既存値があればそれを使用
                    description = existing_cm_expl
                    simple_description = existing_simple_expl
                    logger.info(f"  商品説明: 既存値を使用")
                else:
                    # AI生成を試みる
                    product_info = {
                        "name": product_name,
                        "price": int(price_jpy),
                        "currency": "JPY",
                        "description": desc_en or "",
                        "specs": specs
                    }

                    if description_generator.genai_model:
                        description, simple_description = description_generator.generate(product_info)
                        if description:
                            logger.info(f"  商品説明: AI生成成功 ({len(description)}文字)")

                    # フォールバック: 仕入れ先情報から構築
                    if not description:
                        description = desc_en or ""
                        if specs and description:
                            description = f"{specs}\n\n{description}"
                        elif specs:
                            description = specs

                # SEO項目の取得またはAI生成
                if existing_page_title:
                    page_title = existing_page_title
                    meta_description = existing_meta_desc
                    meta_keywords = existing_meta_keywords
                    logger.info(f"  SEO項目: 既存値を使用")
                else:
                    if seo_generator.genai_model:
                        product_info = {
                            "name": product_name,
                            "price": int(price_jpy),
                            "description": desc_en or "",
                            "specs": specs
                        }
                        page_title, meta_description, meta_keywords = seo_generator.generate(product_info)
                        if page_title:
                            logger.info(f"  SEO項目: AI生成成功")

                # 型番生成（AQ列）- 既存値があればそれを使用、なければAI生成
                existing_model_number = get_cell(row, Col.CM_MODEL_NUMBER)
                model_number = ""

                if existing_model_number:
                    model_number = existing_model_number
                    logger.info(f"  型番: 既存値を使用 → {model_number}")
                else:
                    # ModelNumberGeneratorでAI生成（仕入れ先コード付き、重複チェックあり）
                    quantity = int(get_cell_float(row, Col.QUANTITY, 1.0)) or 1
                    product_info_for_model = {
                        "name": product_name,
                        "specs": specs,
                        "description": desc_en or "",
                    }
                    model_number = model_number_generator.generate(product_info_for_model, quantity, supplier_site)
                    if model_number:
                        logger.info(f"  型番: AI生成成功 → {model_number}")
                    else:
                        # フォールバック: 仕入れ先商品IDを使用
                        model_number = supplier_id
                        logger.info(f"  型番: フォールバック（仕入れ先ID使用） → {model_number}")

                # 画像URL取得（BK-BT列: 画像URL1-10）
                image_urls = []
                # 画像URL1-10（BK-BT列: Col.IMAGE_1〜Col.IMAGE_10）
                for i in range(10):
                    img_idx = Col.IMAGE_1.index + i
                    img_url = row[img_idx].strip() if img_idx < len(row) and row[img_idx] else ""
                    if img_url and img_url not in image_urls:
                        image_urls.append(img_url)

                # 価格関連情報を取得（スプレッドシートの値を優先）
                # AH列: 販売価格, AI列: 定価, AJ列: 会員価格, AK列: 原価
                sales_price = int(get_cell_float(row, Col.CM_SALES_PRICE))
                regular_price = int(get_cell_float(row, Col.CM_REGULAR_PRICE))
                members_price = int(get_cell_float(row, Col.CM_MEMBERS_PRICE))
                cost = int(get_cell_float(row, Col.CM_COST))
                delivery_charge = int(get_cell_float(row, Col.CM_DELIVERY_CHARGE))

                # スプレッドシートに値がない場合はデフォルト計算
                if sales_price <= 0:
                    # 販売価格（マージン10%を加算）
                    sales_price = int(price_jpy * 1.1)
                if regular_price <= 0:
                    # 定価は販売価格と同じ
                    regular_price = sales_price
                if cost <= 0:
                    # 原価は仕入れ価格
                    cost = int(price_jpy)

                logger.info(f"  価格: 販売={sales_price:,}, 定価={regular_price:,}, 会員={members_price:,}, 原価={cost:,}, 個別送料={delivery_charge:,}")

                # ColorMeProduct作成
                colorme_product = ColorMeProduct(
                    product_id=0,  # 新規登録
                    name=cm_product_name,  # CM商品名（日本語）を使用
                    current_price=sales_price,  # 販売価格
                    colorme_url="",
                    source_url=product_url,
                    quantity=1,
                    margin_rate=1.1,
                    regular_price=regular_price,  # 定価
                    members_price=members_price,  # 会員価格
                    cost=cost,  # 原価
                    delivery_charge=delivery_charge,  # 個別送料
                    category_id_big=category_big,
                    category_id_small=category_small,
                    group_ids=group_ids,  # グループIDを追加
                    stock_quantity=10,  # デフォルト在庫数
                    stock_managed=True,
                    soldout_display=True,
                    display_control="表示",
                    expl=description,
                    simple_expl=simple_description,  # 簡易説明
                    page_title=page_title,  # ページタイトル（SEO）
                    meta_description=meta_description,  # メタディスクリプション（SEO）
                    meta_keywords=meta_keywords,  # メタキーワード（SEO）
                    image_urls=image_urls[:10],
                    model_number=model_number,  # 型番（AI生成または仕入れ先ID）
                )

                if dry_run:
                    logger.info(f"  [DRY-RUN] 登録予定: {cm_product_name[:30]}... 価格: ¥{sales_price:,}")
                    logger.info(f"    カテゴリー: 大={category_big}, 小={category_small}, グループ={group_ids}")
                    success_count += 1
                    continue

                # カラーミーAPI登録
                new_product_id, error = colorme_client.create_product(colorme_product)

                if new_product_id > 0:
                    logger.info(f"  登録成功: カラーミー商品ID={new_product_id}")

                    # Playwright経由で個別送料、SEO項目、画像アップロードを設定
                    # （カラーミーAPIでは個別送料とSEO項目が設定できない、画像アップロードも非対応）
                    needs_delivery = delivery_charge > 0
                    needs_seo = page_title or meta_description or meta_keywords
                    needs_images = upload_images and image_urls
                    playwright_updates_needed = needs_delivery or needs_seo or needs_images

                    if playwright_updates_needed and uploader:
                        logger.info(f"  Playwright経由で追加設定を適用中...")
                        try:
                            # 個別送料を設定
                            if needs_delivery:
                                success, error = await uploader.update_delivery_charge(new_product_id, delivery_charge)
                                if success:
                                    logger.info(f"    個別送料設定成功: {delivery_charge}円")
                                else:
                                    logger.warning(f"    個別送料設定失敗: {error}")

                            # SEO項目を設定
                            if needs_seo:
                                success, error = await uploader.update_seo_fields(
                                    new_product_id,
                                    page_title=page_title,
                                    meta_description=meta_description,
                                    meta_keywords=meta_keywords
                                )
                                if success:
                                    logger.info(f"    SEO項目設定成功")
                                else:
                                    logger.warning(f"    SEO項目設定失敗: {error}")

                            # 画像アップロード（--upload-images指定時のみ）
                            if needs_images:
                                logger.info(f"    画像アップロード中（{len(image_urls)}枚）...")
                                result = await uploader.upload_product_images(new_product_id, image_urls)
                                if result.success:
                                    logger.info(f"    画像アップロード成功: {len(result.uploaded_urls)}枚")
                                else:
                                    logger.warning(f"    画像アップロード失敗: {result.error_message}")
                        except Exception as e:
                            logger.warning(f"  Playwright処理エラー（続行）: {e}")

                    # スプレッドシート更新
                    # 84列構造 (A-CF): B列=登録状況, E列=カラーミー商品URL, CE列=同期日時
                    # カテゴリー・グループ: AO-AT列(6列), 型番: AU列, 商品説明: BG-BH列, SEO: BU-BW列
                    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                    colorme_url = f"https://www.ybx.jp/?pid={new_product_id}"
                    batch_data = [
                        {
                            'range': cell_ref(Col.REGISTRATION_STATUS, row_idx),  # B列: カラーミー登録状況
                            'values': [["登録済"]]
                        },
                        {
                            'range': cell_ref(Col.COLORME_URL, row_idx),  # E列: カラーミー商品URL
                            'values': [[colorme_url]]
                        },
                        {
                            'range': cell_ref(Col.CM_SYNC_AT, row_idx),  # CE列: 同期日時
                            'values': [[timestamp]]
                        }
                    ]

                    # カテゴリー・グループ情報をスプレッドシートに保存（AO-AT列: 6列）
                    if category_big:
                        batch_data.append({
                            'range': cell_ref(Col.CM_CATEGORY_BIG, row_idx),  # AO列: 大カテゴリーID
                            'values': [[str(category_big)]]
                        })
                        # AP列: 大カテゴリー名称を取得して保存
                        cat_big_name = ""
                        for (id_big, id_small), (name_big, name_small) in category_name_map.items():
                            if id_big == category_big:
                                cat_big_name = name_big
                                break
                        if cat_big_name:
                            batch_data.append({
                                'range': cell_ref(Col.CM_CATEGORY_BIG_NAME, row_idx),  # AP列: 大カテゴリー名称
                                'values': [[cat_big_name]]
                            })
                    if category_small:
                        batch_data.append({
                            'range': cell_ref(Col.CM_CATEGORY_SMALL, row_idx),  # AQ列: 小カテゴリーID
                            'values': [[str(category_small)]]
                        })
                        # AR列: 小カテゴリー名称を取得して保存
                        cat_small_name = category_name_map.get((category_big, category_small), ("", ""))[1]
                        if cat_small_name:
                            batch_data.append({
                                'range': cell_ref(Col.CM_CATEGORY_SMALL_NAME, row_idx),  # AR列: 小カテゴリー名称
                                'values': [[cat_small_name]]
                            })
                    if group_ids:
                        batch_data.append({
                            'range': cell_ref(Col.CM_GROUP_ID, row_idx),  # AS列: グループID
                            # 先頭にシングルクォートを付けてテキストとして保存（桁区切り防止）
                            'values': [["'" + ",".join(str(g) for g in group_ids)]]
                        })
                        # AT列: グループ名を取得して保存
                        group_names = [group_name_map.get(g, "") for g in group_ids]
                        group_names = [n for n in group_names if n]  # 空文字を除外
                        if group_names:
                            batch_data.append({
                                'range': cell_ref(Col.CM_GROUP_NAME, row_idx),  # AT列: グループ名
                                'values': [[",".join(group_names)]]
                            })

                    # 型番をスプレッドシートに保存（AU列）- AI生成された場合のみ
                    if model_number and not existing_model_number:
                        batch_data.append({
                            'range': cell_ref(Col.CM_MODEL_NUMBER, row_idx),  # AU列: 型番
                            'values': [[model_number]]
                        })

                    # 商品説明をスプレッドシートに保存（BG-BH列）
                    if description and not existing_cm_expl:
                        batch_data.append({
                            'range': cell_ref(Col.CM_EXPL, row_idx),  # BG列: 商品説明
                            'values': [[description]]
                        })
                    if simple_description and not existing_simple_expl:
                        batch_data.append({
                            'range': cell_ref(Col.CM_SIMPLE_EXPL, row_idx),  # BH列: 簡易説明
                            'values': [[simple_description]]
                        })

                    # SEO項目をスプレッドシートに保存（BU-BW列）
                    if page_title and not existing_page_title:
                        batch_data.append({
                            'range': cell_ref(Col.CM_PAGE_TITLE, row_idx),  # BU列: ページタイトル
                            'values': [[page_title]]
                        })
                    if meta_description and not existing_meta_desc:
                        batch_data.append({
                            'range': cell_ref(Col.CM_META_DESC, row_idx),  # BV列: メタディスクリプション
                            'values': [[meta_description]]
                        })
                    if meta_keywords and not existing_meta_keywords:
                        batch_data.append({
                            'range': cell_ref(Col.CM_META_KEYWORDS, row_idx),  # BW列: メタキーワード
                            'values': [[meta_keywords]]
                        })

                    try:
                        batch_update_with_retry(bs_sheet, batch_data)
                    except Exception as e:
                        logger.error(f"  スプレッドシート更新失敗（続行）: {e}")

                    success_count += 1
                else:
                    logger.error(f"  登録失敗: {error}")
                    error_count += 1

                # API制限対策: 1秒待機
                time.sleep(1)

        finally:
            # Playwrightブラウザセッションを終了
            if uploader:
                await uploader.__aexit__(None, None, None)
                logger.info("Playwrightブラウザセッション終了")

        return (success_count, error_count, skip_count)

    except Exception as e:
        logger.error(f"処理エラー: {e}")
        import traceback
        traceback.print_exc()
        return (0, 0, 0)


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description="採用商品をカラーミーに自動登録"
    )
    parser.add_argument("--dry-run", action="store_true", help="実際の登録は行わない")
    parser.add_argument("--limit", type=int, help="処理する最大件数")
    parser.add_argument("--upload-images", action="store_true", help="画像アップロードを有効化（デフォルト: OFF）")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")
    parser.add_argument("--skip-sync", action="store_true", help="仕入れ先一覧同期をスキップ")
    parser.add_argument("--source", choices=["bs", "ap"], default="bs",
                        help="対象シート（bs=ブリオンスター, ap=APMEX）")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    source_name = "APMEX" if args.source == "ap" else "ブリオンスター"
    logger.info("=" * 60)
    logger.info(f"採用商品カラーミー自動登録開始（{source_name}）")
    if args.dry_run:
        logger.info("※ ドライランモード（実際の登録は行いません）")
    if args.upload_images:
        logger.info("※ 画像アップロード有効")
    logger.info("=" * 60)

    start_time = datetime.now()

    # 設定検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    if not Config.is_colorme_enabled():
        logger.error("カラーミーアクセストークンが設定されていません")
        sys.exit(1)

    # 採用商品をカラーミーに登録
    success, error, skip = asyncio.run(register_adopted_products(
        dry_run=args.dry_run,
        limit=args.limit,
        upload_images=args.upload_images,
        source=args.source
    ))

    logger.info("-" * 60)
    logger.info(f"カラーミー登録結果: 成功={success}件, 失敗={error}件, スキップ={skip}件")

    # 仕入れ先一覧同期（登録成功があり、スキップ指定がない場合）
    if success > 0 and not args.skip_sync and not args.dry_run:
        logger.info("-" * 60)
        logger.info("商品仕入れ先一覧への同期を開始...")
        if sync_registered_products_to_supplier_list():
            logger.info("仕入れ先一覧同期完了")
        else:
            logger.error("仕入れ先一覧同期に失敗しました")

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info(f"処理完了（所要時間: {elapsed:.1f}秒）")
    logger.info("=" * 60)

    return 0 if error == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
