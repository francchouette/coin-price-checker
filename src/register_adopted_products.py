"""
採用商品カラーミー登録スクリプト

ブリオンスター商品ページ一覧（83列: A-CE）で A列=「採用」かつ B列≠「登録済」の商品を
カラーミーAPIで自動登録し、登録完了後に以下を実行する：

1. B列（カラーミー登録状況）を「登録済」に更新
2. R列（カラーミー商品ID）を更新
3. CC列（登録日時）を更新
4. 商品仕入れ先一覧シートに同期

シート構造（83列: A-CE）:
- A-B列: 採用・登録管理列
- C-AG列: 仕入れ先商品情報（31列）
- AH-CE列: カラーミー登録用項目（40列）※CM商品名を追加

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
from src.add_product import CategoryDetector, DescriptionGenerator, SEOGenerator, JapaneseProductNameGenerator

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

# ブリオンスター商品ページ一覧の列インデックス（0-based）
# 83列構造 (A-CE) - CM商品名（AH列）を追加

# === A-B列: 採用・登録管理列 ===
COL_ADOPTED_FLAG = 0       # A列: 採用フラグ
COL_REGISTRATION_STATUS = 1  # B列: カラーミー登録状況

# === C-AG列: 仕入れ先商品情報（31列）===
COL_SUPPLIER_ID = 2        # C列: 仕入れ先商品ID
COL_PRODUCT_NAME = 3       # D列: 仕入れ先商品名
COL_PRODUCT_URL = 4        # E列: 仕入れ先商品URL
COL_SITE = 5               # F列: 仕入れ先サイト（自動: Bullionstar）
COL_TOP_CATEGORY = 6       # G列: 最上位カテゴリ
COL_PARENT_CATEGORY = 7    # H列: 親カテゴリ
COL_CHILD_CATEGORY = 8     # I列: 子カテゴリ
COL_COUNTRY = 9            # J列: 製造国（サイトからスクレイピング）
COL_FIRST_FETCHED = 10     # K列: 初回取得日（自動）
COL_STOCK_STATUS = 11      # L列: 在庫状況（サイトからスクレイピング）
COL_PRICE = 12             # M列: 現在価格（現地通貨）
COL_CURRENCY = 13          # N列: 取引通貨（サイトからスクレイピング）
COL_EXCHANGE_TYPE = 14     # O列: 為替種類（手入力）
COL_EXCHANGE_RATE = 15     # P列: 為替レート（自動取得）
COL_PRICE_JPY = 16         # Q列: 日本円換算価格（計算式）
COL_COLORME_ID = 17        # R列: カラーミー商品ID（登録後自動）
# 画像URL（S-AB列: 10列）
COL_IMAGE_1 = 18           # S列: 画像URL1
COL_IMAGE_2 = 19           # T列: 画像URL2
COL_IMAGE_3 = 20           # U列: 画像URL3
COL_IMAGE_4 = 21           # V列: 画像URL4
COL_IMAGE_5 = 22           # W列: 画像URL5
COL_IMAGE_6 = 23           # X列: 画像URL6
COL_IMAGE_7 = 24           # Y列: 画像URL7
COL_IMAGE_8 = 25           # Z列: 画像URL8
COL_IMAGE_9 = 26           # AA列: 画像URL9
COL_IMAGE_10 = 27          # AB列: 画像URL10
# 仕入れ先詳細情報（AC-AG列: 5列）
COL_SPECS = 28             # AC列: 仕様・スペック
COL_DESC_EN = 29           # AD列: 商品説明（英語）
COL_DESC_JA = 30           # AE列: 商品説明（日本語）- AI翻訳
COL_YEAR = 31              # AF列: 発行年（サイトからスクレイピング）
COL_MINTAGE = 32           # AG列: 発行数・限定数（サイトからスクレイピング）

# === AH-CE列: カラーミー登録用項目（40列）===

# C. カラーミー商品名（AH列: 1列）- 新規追加
COL_CM_PRODUCT_NAME = 33   # AH列: CM商品名（AI翻訳、手入力可）

# D. 価格計算（AI-AT列: 12列）
COL_CM_EXCHANGE_RATE = 34  # AI列: 為替レート(CM) = P列
COL_CM_PRICE_JPY = 35      # AJ列: 仕入れ額(日本円) = Q列
COL_CM_QUANTITY = 36       # AK列: 枚数（手入力、デフォルト1）
COL_CM_TOTAL_PURCHASE = 37 # AL列: 仕入れ合計（計算式 =AJ*AK）
COL_CM_MARGIN_RATE = 38    # AM列: 設定マージン率（手入力、デフォルト1.1）
COL_CM_MARGIN_AMOUNT = 39  # AN列: 設定マージン額（手入力）
COL_CM_SHIPPING = 40       # AO列: 送料（手入力）
COL_CM_FEE = 41            # AP列: 手数料（手入力）
COL_CM_TOTAL_COST = 42     # AQ列: 合計原価（計算式）
COL_CM_PROPER_PRICE = 43   # AR列: 適正価格（計算式）
COL_CM_PROFIT = 44         # AS列: 粗利額（計算式）
COL_CM_PROFIT_RATE = 45    # AT列: 粗利率（計算式）

# E. カラーミー価格情報（AU-AZ列: 6列）
COL_CM_SALES_PRICE = 46    # AU列: 販売価格（計算式 =AR）
COL_CM_REGULAR_PRICE = 47  # AV列: 定価（手入力）
COL_CM_MEMBERS_PRICE = 48  # AW列: 会員価格（手入力）
COL_CM_COST = 49           # AX列: 原価（計算式 =AQ）
COL_CM_TAX_INCLUDED = 50   # AY列: 消費税込販売価格（計算式）
COL_CM_TAX_AMOUNT = 51     # AZ列: 消費税額（計算式）

# F. カテゴリー・グループ（BA-BD列: 4列）- Add Productロジックで自動設定
COL_CM_CATEGORY_BIG = 52   # BA列: CM大カテゴリーID（自動）
COL_CM_CATEGORY_SMALL = 53 # BB列: CM小カテゴリーID（自動）
COL_CM_GROUP_ID = 54       # BC列: CMグループID（自動、カンマ区切り）
COL_CM_MODEL_NUMBER = 55   # BD列: 型番（自動 =仕入れ先商品ID）

# G. 在庫管理（BE-BK列: 7列）
COL_CM_STOCK = 56          # BE列: 在庫数（手入力、デフォルト10）
COL_CM_STOCK_MANAGED = 57  # BF列: 在庫管理（手入力、デフォルト「する」）
COL_CM_FEW_NUM = 58        # BG列: 残りわずか数（手入力、デフォルト3）
COL_CM_SOLDOUT_DISPLAY = 59  # BH列: 売切れ表示（手入力、デフォルト「表示」）
COL_CM_MIN_NUM = 60        # BI列: 最小購入数（手入力、デフォルト1）
COL_CM_MAX_NUM = 61        # BJ列: 最大購入数（手入力、デフォルト0）
COL_CM_UNIT = 62           # BK列: 単位（手入力、空欄可）

# H. 送料・配送（BL-BO列: 4列）
COL_CM_DELIVERY_CHARGE = 63  # BL列: 個別送料（手入力、デフォルト0）
COL_CM_COOL_CHARGE = 64    # BM列: クール便料金（手入力）
COL_CM_WEIGHT = 65         # BN列: 重量(g)（手入力）
COL_CM_NO_DELIVERY = 66    # BO列: 配送不要（手入力、デフォルト「必要」）

# I. 商品説明（BP-BR列: 3列）- AIで生成
COL_CM_EXPL = 67           # BP列: CM商品説明（AIで生成）
COL_CM_SIMPLE_EXPL = 68    # BQ列: CM簡易説明（AIで生成）
COL_CM_SMARTPHONE_EXPL = 69  # BR列: CMスマホ説明（AIで生成）

# J. SEO項目（BS-BU列: 3列）- AIで生成、Playwrightで登録
COL_CM_PAGE_TITLE = 70     # BS列: ページタイトル（AIで生成）
COL_CM_META_DESC = 71      # BT列: メタディスクリプション（AIで生成）
COL_CM_META_KEYWORDS = 72  # BU列: メタキーワード（AIで生成）

# K. フラグ・設定（BV-BZ列: 5列）
COL_CM_REDUCED_TAX = 73    # BV列: 軽減税率対象（手入力、デフォルト「対象外」）
COL_CM_DIGITAL = 74        # BW列: デジタルコンテンツ（手入力、デフォルト「対象外」）
COL_CM_SUBSCRIPTION = 75   # BX列: 定期購入（手入力、デフォルト「対象外」）
COL_CM_SORT = 76           # BY列: 表示順（手入力、デフォルト0）
COL_CM_DISABLED_PAYMENT = 77  # BZ列: 利用不可決済（手入力）

# L. 掲載期間（CA-CB列: 2列）
COL_CM_START_DATE = 78     # CA列: 掲載開始日時（手入力、空欄可）
COL_CM_END_DATE = 79       # CB列: 掲載終了日時（手入力、空欄可）

# M. システム情報（CC-CE列: 3列）- 自動
COL_CM_REGISTERED_AT = 80  # CC列: 登録日時（自動）
COL_CM_CREATED_AT = 81     # CD列: 商品作成日時（自動）
COL_CM_UPDATED_AT = 82     # CE列: 商品更新日時（自動）

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
    try:
        categories = colorme_client.get_categories()
        groups = colorme_client.get_groups()
        category_detector = CategoryDetector(categories, groups, colorme_client)
        logger.info(f"カテゴリー: {len(categories)}件, グループ: {len(groups)}件 を取得")
    except Exception as e:
        logger.warning(f"カテゴリー/グループ取得エラー: {e}")
        logger.warning("カテゴリー自動判定は無効化されます")

    # AI生成器を初期化（商品説明・SEO・商品名）
    description_generator = DescriptionGenerator()
    seo_generator = SEOGenerator()
    name_generator = JapaneseProductNameGenerator()

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

            # CM商品名（AH列）- 既存値があればそれを使用、なければ自動生成
            existing_cm_name = get_cell_value(row, COL_CM_PRODUCT_NAME)
            desc_en = get_cell_value(row, COL_DESC_EN)  # AD列: 仕入れ先英語説明
            specs = get_cell_value(row, COL_SPECS)      # AC列: 仕様・スペック

            if existing_cm_name:
                cm_product_name = existing_cm_name
                logger.info(f"  CM商品名: 既存値を使用")
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

            # 商品説明（BP列）- 既存値があればそれを使用、なければAI生成
            desc_ja = get_cell_value(row, COL_DESC_JA)  # AE列: 仕入れ先日本語説明
            # desc_en, specsは上で取得済み

            # カラーミー用説明（BP列）をチェック
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
                    "description": desc_en or desc_ja or "",
                    "specs": specs
                }

                if description_generator.client:
                    description, simple_description = description_generator.generate(product_info)
                    if description:
                        logger.info(f"  商品説明: AI生成成功 ({len(description)}文字)")

                # フォールバック: 仕入れ先情報から構築
                if not description:
                    description = desc_ja or desc_en or ""
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
                        "description": desc_en or desc_ja or "",
                        "specs": specs
                    }
                    page_title, meta_description, meta_keywords = seo_generator.generate(product_info)
                    if page_title:
                        logger.info(f"  SEO項目: AI生成成功")

            # 画像URL取得（最大10枚: 画像URL1〜10）
            image_urls = []
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
                model_number=supplier_id,  # 型番に仕入れ先商品IDを設定
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
                # 83列構造: B列=登録状況, R列=カラーミー商品ID, CC列=登録日時
                # カテゴリー・グループ: BA-BC列, 商品説明: BP-BR列, SEO: BS-BU列
                timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                batch_data = [
                    {
                        'range': f"B{row_idx}",  # B列: カラーミー登録状況
                        'values': [["登録済"]]
                    },
                    {
                        'range': f"R{row_idx}",  # R列: カラーミー商品ID
                        'values': [[str(new_product_id)]]
                    },
                    {
                        'range': f"CC{row_idx}",  # CC列: 登録日時
                        'values': [[timestamp]]
                    }
                ]

                # CM商品名をスプレッドシートに保存（AH列）
                if cm_product_name and not existing_cm_name:
                    batch_data.append({
                        'range': f"AH{row_idx}",  # AH列: CM商品名
                        'values': [[cm_product_name]]
                    })

                # カテゴリー・グループ情報をスプレッドシートに保存（BA-BC列）
                if category_big:
                    batch_data.append({
                        'range': f"BA{row_idx}",  # BA列: CM大カテゴリーID
                        'values': [[str(category_big)]]
                    })
                if category_small:
                    batch_data.append({
                        'range': f"BB{row_idx}",  # BB列: CM小カテゴリーID
                        'values': [[str(category_small)]]
                    })
                if group_ids:
                    batch_data.append({
                        'range': f"BC{row_idx}",  # BC列: CMグループID
                        'values': [[",".join(str(g) for g in group_ids)]]
                    })

                # 商品説明をスプレッドシートに保存（BP-BR列）
                if description and not existing_cm_expl:
                    batch_data.append({
                        'range': f"BP{row_idx}",  # BP列: CM商品説明
                        'values': [[description]]
                    })
                if simple_description and not existing_simple_expl:
                    batch_data.append({
                        'range': f"BQ{row_idx}",  # BQ列: CM簡易説明
                        'values': [[simple_description]]
                    })

                # SEO項目をスプレッドシートに保存（BS-BU列）
                if page_title and not existing_page_title:
                    batch_data.append({
                        'range': f"BS{row_idx}",  # BS列: ページタイトル
                        'values': [[page_title]]
                    })
                if meta_description and not existing_meta_desc:
                    batch_data.append({
                        'range': f"BT{row_idx}",  # BT列: メタディスクリプション
                        'values': [[meta_description]]
                    })
                if meta_keywords and not existing_meta_keywords:
                    batch_data.append({
                        'range': f"BU{row_idx}",  # BU列: メタキーワード
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
