"""
採用商品カラーミー登録スクリプト

ブリオンスター商品ページ一覧（83列: A-CE）で A列=「採用」かつ B列≠「登録済」の商品を
カラーミーAPIで自動登録し、登録完了後に以下を実行する：

1. B列（カラーミー登録状況）を「登録済」に更新
2. D列（カラーミー商品URL）を更新
3. CC列（同期日時）を更新
4. 商品仕入れ先一覧シートに同期

シート構造（83列: A-CE）:
- A-C列: 管理列（採用フラグ、登録状況、仕入れ先商品ID）
- D-P列: 仕入れ先商品情報（13列）
- Q-AG列: 価格情報（17列）
- AH-AM列: CM商品名、画像URL等
- AN-AS列: カテゴリー・グループ（ID・名称: 6列）
- AT列: 型番
- AU-CE列: カラーミー登録用項目

トリガー:
- A列を「採用」に変更後、このスクリプトを実行

使用方法:
    python -m src.register_adopted_products
    python -m src.register_adopted_products --dry-run  # 実際の登録なし
    python -m src.register_adopted_products --limit 5  # 5件のみ処理
"""

import argparse
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

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

# ブリオンスター商品ページ一覧の列インデックス（0-based）
# 83列構造 (A-CE)

# === A-C列: 管理列（3列）===
COL_ADOPTED_FLAG = 0       # A列: 採用フラグ
COL_REGISTRATION_STATUS = 1  # B列: カラーミー登録状況
COL_SUPPLIER_ID = 2        # C列: 仕入れ先商品ID

# === D-P列: 仕入れ先商品情報（13列）===
COL_COLORME_URL = 3        # D列: カラーミー商品URL（登録後自動）
COL_PRODUCT_URL = 4        # E列: 仕入れ先商品URL（ユニークキー）
COL_PRODUCT_NAME = 5       # F列: 仕入れ先商品名
COL_SITE = 6               # G列: 仕入れ先サイト（自動: Bullionstar）
COL_TOP_CATEGORY = 7       # H列: 最上位カテゴリ
COL_PARENT_CATEGORY = 8    # I列: 親カテゴリ
COL_CHILD_CATEGORY = 9     # J列: 子カテゴリ
COL_COUNTRY = 10           # K列: 製造国
COL_DESC_EN = 11           # L列: 商品説明（英語）
COL_SPECS = 12             # M列: 仕様・スペック
COL_YEAR = 13              # N列: 発行年
COL_MINTAGE = 14           # O列: 発行数・限定数
COL_STOCK_STATUS = 15      # P列: 仕入れ先在庫状況

# === Q-AG列: 価格情報（17列）===
COL_PRICE = 16             # Q列: 仕入れ先価格（現地通貨）
COL_PREV_PRICE = 17        # R列: 前回仕入れ価格
COL_PRICE_CHANGE = 18      # S列: 価格変動率
COL_CURRENCY = 19          # T列: 取引通貨
COL_EXCHANGE_TYPE = 20     # U列: 為替種類
COL_EXCHANGE_RATE = 21     # V列: 為替レート
COL_PRICE_JPY = 22         # W列: 仕入れ額(日本円)
COL_QUANTITY = 23          # X列: 枚数
COL_TOTAL_PURCHASE = 24    # Y列: 仕入れ合計
COL_MARGIN_RATE = 25       # Z列: 設定マージン率
COL_MARGIN_AMOUNT = 26     # AA列: 設定マージン額
COL_SHIPPING = 27          # AB列: 送料
COL_MISC_COST = 28         # AC列: 諸経費
COL_TOTAL_COST = 29        # AD列: 合計原価
COL_PROPER_PRICE = 30      # AE列: 適正価格
COL_PROFIT = 31            # AF列: 粗利額
COL_PROFIT_RATE = 32       # AG列: 粗利率

# === AH-AM列: カラーミー価格情報（6列）===
COL_CM_SALES_PRICE = 33    # AH列: 販売価格
COL_CM_REGULAR_PRICE = 34  # AI列: 定価
COL_CM_MEMBERS_PRICE = 35  # AJ列: 会員価格
COL_CM_COST = 36           # AK列: 原価
COL_CM_TAX_INCLUDED = 37   # AL列: 消費税込販売価格
COL_CM_TAX_AMOUNT = 38     # AM列: 消費税額

# === AN-AS列: カテゴリー・グループ（6列）===
COL_CM_CATEGORY_BIG = 39       # AN列: 大カテゴリーID
COL_CM_CATEGORY_BIG_NAME = 40  # AO列: 大カテゴリー名称
COL_CM_CATEGORY_SMALL = 41     # AP列: 小カテゴリーID
COL_CM_CATEGORY_SMALL_NAME = 42  # AQ列: 小カテゴリー名称
COL_CM_GROUP_ID = 43           # AR列: グループID
COL_CM_GROUP_NAME = 44         # AS列: グループ名

# === AT列: 型番（1列）===
COL_CM_MODEL_NUMBER = 45   # AT列: 型番

# === AU-BA列: 在庫管理（7列）===
COL_CM_STOCK = 46          # AU列: 在庫数
COL_CM_STOCK_MANAGED = 47  # AV列: 在庫管理
COL_CM_FEW_NUM = 48        # AW列: 残りわずか数
COL_CM_SOLDOUT_DISPLAY = 49  # AX列: 売切れ表示
COL_CM_MIN_NUM = 50        # AY列: 最小購入数
COL_CM_MAX_NUM = 51        # AZ列: 最大購入数
COL_CM_UNIT = 52           # BA列: 単位

# === BB-BE列: 送料・配送（4列）===
COL_CM_DELIVERY_CHARGE = 53  # BB列: 個別送料
COL_CM_COOL_CHARGE = 54    # BC列: クール便料金
COL_CM_WEIGHT = 55         # BD列: 重量(g)
COL_CM_NO_DELIVERY = 56    # BE列: 配送不要

# === BF-BI列: 商品説明（4列）===
COL_CM_EXPL = 57           # BF列: 商品説明
COL_CM_SIMPLE_EXPL = 58    # BG列: 簡易説明
COL_CM_SMARTPHONE_EXPL = 59  # BH列: スマホ説明
COL_CM_MEMO = 60           # BI列: 備考

# === BJ-BS列: 画像URL（10列）===
COL_IMAGE_1 = 61           # BJ列: 画像URL1
COL_IMAGE_2 = 62           # BK列: 画像URL2
COL_IMAGE_3 = 63           # BL列: 画像URL3
COL_IMAGE_4 = 64           # BM列: 画像URL4
COL_IMAGE_5 = 65           # BN列: 画像URL5
COL_IMAGE_6 = 66           # BO列: 画像URL6
COL_IMAGE_7 = 67           # BP列: 画像URL7
COL_IMAGE_8 = 68           # BQ列: 画像URL8
COL_IMAGE_9 = 69           # BR列: 画像URL9
COL_IMAGE_10 = 70          # BS列: 画像URL10

# === BT-BV列: SEO項目（3列）===
COL_CM_PAGE_TITLE = 71     # BT列: ページタイトル
COL_CM_META_DESC = 72      # BU列: メタディスクリプション
COL_CM_META_KEYWORDS = 73  # BV列: メタキーワード

# === BW-CA列: フラグ・設定（5列）===
COL_CM_REDUCED_TAX = 74    # BW列: 軽減税率対象
COL_CM_DIGITAL = 75        # BX列: デジタルコンテンツ
COL_CM_SUBSCRIPTION = 76   # BY列: 定期購入
COL_CM_SORT = 77           # BZ列: 表示順
COL_CM_DISABLED_PAYMENT = 78  # CA列: 利用不可決済

# === CB-CC列: 掲載期間（2列）===
COL_CM_START_DATE = 79     # CB列: 掲載開始日時
COL_CM_END_DATE = 80       # CC列: 掲載終了日時

# === CD-CE列: システム情報（2列）===
COL_CM_SYNC_AT = 81        # CD列: 同期日時
COL_CM_CREATED_AT = 82     # CE列: 商品作成日時

# デフォルトのカテゴリーID（貴金属コイン）
DEFAULT_CATEGORY_BIG = 174936    # 大カテゴリー: 金貨・銀貨・プラチナコイン
DEFAULT_CATEGORY_SMALL = 174938  # 小カテゴリー: その他金貨


def get_cell_value(row: list, index: int, default: str = "") -> str:
    """行から安全に値を取得"""
    if len(row) > index:
        return str(row[index]).strip()
    return default


def get_cell_float(row: list, index: int, default: float = 0.0) -> float:
    """行から安全にfloat値を取得"""
    val = get_cell_value(row, index)
    if not val:
        return default
    try:
        return float(val.replace(",", ""))
    except ValueError:
        return default


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


def register_adopted_products(
    dry_run: bool = False,
    limit: Optional[int] = None
) -> tuple[int, int, int]:
    """
    採用商品をカラーミーに登録

    Args:
        dry_run: Trueの場合、実際の登録は行わない
        limit: 処理する最大件数（デバッグ用）

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

    # AI生成器を初期化（商品説明・SEO・商品名・型番）
    description_generator = DescriptionGenerator()
    seo_generator = SEOGenerator()
    name_generator = JapaneseProductNameGenerator()
    model_number_generator = ModelNumberGenerator()

    try:
        # ブリオンスター商品ページ一覧シートを取得
        bs_sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        bs_data = bs_sheet.get_all_values()

        if len(bs_data) <= 1:
            logger.info("ブリオンスター商品ページ一覧にデータがありません")
            return (0, 0, 0)

        # A列=「採用」かつ B列≠「登録済」の商品をフィルタ
        target_products = []
        for row_idx, row in enumerate(bs_data[1:], start=2):
            adopted_flag = get_cell_value(row, COL_ADOPTED_FLAG)
            registration_status = get_cell_value(row, COL_REGISTRATION_STATUS)

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

        for row_idx, row in target_products:
            product_name = get_cell_value(row, COL_PRODUCT_NAME)  # D列: 仕入れ先商品名（英語）
            product_url = get_cell_value(row, COL_PRODUCT_URL)
            supplier_id = get_cell_value(row, COL_SUPPLIER_ID)

            logger.info(f"処理中: {product_name[:50]}... (行{row_idx})")

            # 必須情報チェック
            if not product_name:
                logger.warning(f"  スキップ: 商品名がありません (行{row_idx})")
                skip_count += 1
                continue

            # 価格情報取得
            price_jpy = get_cell_float(row, COL_PRICE_JPY)
            if price_jpy <= 0:
                # 日本円換算価格がない場合は計算を試みる
                price = get_cell_float(row, COL_PRICE)
                exchange_rate = get_cell_float(row, COL_EXCHANGE_RATE, 1.0)
                price_jpy = price * exchange_rate

            if price_jpy <= 0:
                logger.warning(f"  スキップ: 価格情報がありません (行{row_idx})")
                skip_count += 1
                continue

            # CM商品名 - JapaneseProductNameGeneratorで自動生成
            desc_en = get_cell_value(row, COL_DESC_EN)  # L列: 商品説明（英語）
            specs = get_cell_value(row, COL_SPECS)      # M列: 仕様・スペック

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
            existing_cat_big = get_cell_value(row, COL_CM_CATEGORY_BIG)
            existing_cat_small = get_cell_value(row, COL_CM_CATEGORY_SMALL)
            existing_group_id = get_cell_value(row, COL_CM_GROUP_ID)

            if existing_cat_big:
                # 既存値がある場合はそれを使用
                try:
                    category_big = int(existing_cat_big)
                    category_small = int(existing_cat_small) if existing_cat_small else 0
                    if existing_group_id:
                        group_ids = [int(g.strip()) for g in existing_group_id.split(",") if g.strip()]
                    logger.info(f"  カテゴリー(既存値使用): 大={category_big}, 小={category_small}, グループ={group_ids}")
                except ValueError:
                    pass

            if not category_big:
                # CategoryDetectorで自動判定
                if category_detector:
                    category_big, category_small, group_ids = category_detector.detect(product_name, product_url)
                    logger.info(f"  カテゴリー(自動判定): 大={category_big}, 小={category_small}, グループ={group_ids}")
                else:
                    # フォールバック: 従来のmap_category関数を使用
                    top_cat = get_cell_value(row, COL_TOP_CATEGORY)
                    parent_cat = get_cell_value(row, COL_PARENT_CATEGORY)
                    category_big, category_small = map_category(top_cat, parent_cat, "")
                    logger.info(f"  カテゴリー(フォールバック): 大={category_big}, 小={category_small}")

            # 商品説明（BC列）- 既存値があればそれを使用、なければAI生成
            # desc_en, specsは上で取得済み

            # カラーミー用説明（BC列）をチェック
            existing_cm_expl = get_cell_value(row, COL_CM_EXPL)
            existing_simple_expl = get_cell_value(row, COL_CM_SIMPLE_EXPL)

            # SEO項目（BR-BT列）をチェック
            existing_page_title = get_cell_value(row, COL_CM_PAGE_TITLE)
            existing_meta_desc = get_cell_value(row, COL_CM_META_DESC)
            existing_meta_keywords = get_cell_value(row, COL_CM_META_KEYWORDS)

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

                if description_generator.client:
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
                if seo_generator.client:
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
            existing_model_number = get_cell_value(row, COL_CM_MODEL_NUMBER)
            model_number = ""

            if existing_model_number:
                model_number = existing_model_number
                logger.info(f"  型番: 既存値を使用 → {model_number}")
            else:
                # ModelNumberGeneratorでAI生成
                quantity = int(get_cell_float(row, COL_QUANTITY, 1.0)) or 1
                product_info_for_model = {
                    "name": product_name,
                    "specs": specs,
                    "description": desc_en or "",
                }
                model_number = model_number_generator.generate(product_info_for_model, quantity)
                if model_number:
                    logger.info(f"  型番: AI生成成功 → {model_number}")
                else:
                    # フォールバック: 仕入れ先商品IDを使用
                    model_number = supplier_id
                    logger.info(f"  型番: フォールバック（仕入れ先ID使用） → {model_number}")

            # 画像URL取得（BG-BP列: 画像URL1-10）
            image_urls = []
            # 画像URL1-10（BG-BP列）
            for i in range(10):
                img_url = get_cell_value(row, COL_IMAGE_1 + i) if COL_IMAGE_1 + i < len(row) else ""
                if img_url and img_url not in image_urls:
                    image_urls.append(img_url)

            # 販売価格（マージン10%を加算）
            selling_price = int(price_jpy * 1.1)

            # ColorMeProduct作成
            colorme_product = ColorMeProduct(
                product_id=0,  # 新規登録
                name=cm_product_name,  # CM商品名（日本語）を使用
                current_price=selling_price,
                colorme_url="",
                source_url=product_url,
                quantity=1,
                margin_rate=1.1,
                regular_price=selling_price,
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
                logger.info(f"  [DRY-RUN] 登録予定: {cm_product_name[:30]}... 価格: ¥{selling_price:,}")
                logger.info(f"    カテゴリー: 大={category_big}, 小={category_small}, グループ={group_ids}")
                success_count += 1
                continue

            # カラーミーAPI登録
            new_product_id, error = colorme_client.create_product(colorme_product)

            if new_product_id > 0:
                logger.info(f"  登録成功: カラーミー商品ID={new_product_id}")

                # スプレッドシート更新
                # 83列構造: B列=登録状況, D列=カラーミー商品URL, CD列=同期日時
                # カテゴリー・グループ: AN-AS列(6列), 型番: AT列, 商品説明: BF-BG列, SEO: BT-BV列
                timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                colorme_url = f"https://www.ybx.jp/?pid={new_product_id}"
                batch_data = [
                    {
                        'range': f"B{row_idx}",  # B列: カラーミー登録状況
                        'values': [["登録済"]]
                    },
                    {
                        'range': f"D{row_idx}",  # D列: カラーミー商品URL
                        'values': [[colorme_url]]
                    },
                    {
                        'range': f"CD{row_idx}",  # CD列: 同期日時
                        'values': [[timestamp]]
                    }
                ]

                # カテゴリー・グループ情報をスプレッドシートに保存（AN-AS列: 6列）
                if category_big:
                    batch_data.append({
                        'range': f"AN{row_idx}",  # AN列: 大カテゴリーID
                        'values': [[str(category_big)]]
                    })
                    # AO列: 大カテゴリー名称を取得して保存
                    cat_big_name = ""
                    for (id_big, id_small), (name_big, name_small) in category_name_map.items():
                        if id_big == category_big:
                            cat_big_name = name_big
                            break
                    if cat_big_name:
                        batch_data.append({
                            'range': f"AO{row_idx}",  # AO列: 大カテゴリー名称
                            'values': [[cat_big_name]]
                        })
                if category_small:
                    batch_data.append({
                        'range': f"AP{row_idx}",  # AP列: 小カテゴリーID
                        'values': [[str(category_small)]]
                    })
                    # AQ列: 小カテゴリー名称を取得して保存
                    cat_small_name = category_name_map.get((category_big, category_small), ("", ""))[1]
                    if cat_small_name:
                        batch_data.append({
                            'range': f"AQ{row_idx}",  # AQ列: 小カテゴリー名称
                            'values': [[cat_small_name]]
                        })
                if group_ids:
                    batch_data.append({
                        'range': f"AR{row_idx}",  # AR列: グループID
                        # 先頭にシングルクォートを付けてテキストとして保存（桁区切り防止）
                        'values': [["'" + ",".join(str(g) for g in group_ids)]]
                    })
                    # AS列: グループ名を取得して保存
                    group_names = [group_name_map.get(g, "") for g in group_ids]
                    group_names = [n for n in group_names if n]  # 空文字を除外
                    if group_names:
                        batch_data.append({
                            'range': f"AS{row_idx}",  # AS列: グループ名
                            'values': [[",".join(group_names)]]
                        })

                # 型番をスプレッドシートに保存（AT列）- AI生成された場合のみ
                if model_number and not existing_model_number:
                    batch_data.append({
                        'range': f"AT{row_idx}",  # AT列: 型番
                        'values': [[model_number]]
                    })

                # 商品説明をスプレッドシートに保存（BF-BG列）
                if description and not existing_cm_expl:
                    batch_data.append({
                        'range': f"BF{row_idx}",  # BF列: 商品説明
                        'values': [[description]]
                    })
                if simple_description and not existing_simple_expl:
                    batch_data.append({
                        'range': f"BG{row_idx}",  # BG列: 簡易説明
                        'values': [[simple_description]]
                    })

                # SEO項目をスプレッドシートに保存（BT-BV列）
                if page_title and not existing_page_title:
                    batch_data.append({
                        'range': f"BT{row_idx}",  # BT列: ページタイトル
                        'values': [[page_title]]
                    })
                if meta_description and not existing_meta_desc:
                    batch_data.append({
                        'range': f"BU{row_idx}",  # BU列: メタディスクリプション
                        'values': [[meta_description]]
                    })
                if meta_keywords and not existing_meta_keywords:
                    batch_data.append({
                        'range': f"BV{row_idx}",  # BV列: メタキーワード
                        'values': [[meta_keywords]]
                    })

                bs_sheet.batch_update(batch_data, value_input_option='RAW')

                success_count += 1
            else:
                logger.error(f"  登録失敗: {error}")
                error_count += 1

            # API制限対策: 1秒待機
            time.sleep(1)

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
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")
    parser.add_argument("--skip-sync", action="store_true", help="仕入れ先一覧同期をスキップ")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logger.info("=" * 60)
    logger.info("採用商品カラーミー自動登録開始")
    if args.dry_run:
        logger.info("※ ドライランモード（実際の登録は行いません）")
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
    success, error, skip = register_adopted_products(
        dry_run=args.dry_run,
        limit=args.limit
    )

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
