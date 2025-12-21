"""
カラーミー商品同期スクリプト

新カラーミー商品管理シートから同期モードが「更新」の商品を取得し、
カラーミーショップAPIで商品情報を更新する。
"""

import argparse
import logging
import sys
from datetime import datetime

from .spreadsheet import SpreadsheetClient
from .colorme import ColorMeClient
from .config import Config

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# 列インデックス定義（A列=0から始まる）
# ※L列に「仕入れ先在庫状況」を追加したため、旧L列以降は+1シフト
class ColIndex:
    """新カラーミー商品管理シートの列インデックス"""
    # A-B: 操作項目
    SYNC_MODE = 0          # A: 同期モード
    DISPLAY_STATE = 1      # B: 掲載設定
    # C-E: 識別情報
    PRODUCT_ID = 2         # C: カラーミー商品ID
    NAME = 3               # D: 商品名
    COLORME_URL = 4        # E: カラーミー商品URL
    # F-P: 仕入れ先情報
    SUPPLIER_URL = 5       # F: 仕入れ先商品URL
    SUPPLIER_NAME = 6      # G: 仕入れ先商品名
    SUPPLIER_SITE = 7      # H: 仕入れ先サイト
    TOP_CATEGORY = 8       # I: 最上位カテゴリ
    PARENT_CATEGORY = 9    # J: 親カテゴリ
    CHILD_CATEGORY = 10    # K: 子カテゴリ
    SUPPLIER_STOCK = 11    # L: 仕入れ先在庫状況（新規追加）
    SUPPLIER_PRICE = 12    # M: 仕入れ先価格（現地通貨）（旧L列）
    PREV_PRICE = 13        # N: 前回仕入れ価格（旧M列）
    PRICE_CHANGE_RATE = 14 # O: 価格変動率（旧N列）
    CURRENCY = 15          # P: 取引通貨（旧O列）
    # Q-AC: 価格計算
    EXCHANGE_TYPE = 16     # Q: 為替種類（旧P列）
    EXCHANGE_RATE = 17     # R: 為替レート（旧Q列）
    PURCHASE_PRICE_JPY = 18 # S: 仕入れ額(日本円)（旧R列）
    QUANTITY = 19          # T: 枚数（旧S列）
    PURCHASE_TOTAL = 20    # U: 仕入れ合計（旧T列）
    MARGIN_RATE = 21       # V: 設定マージン率（旧U列）
    MARGIN_AMOUNT = 22     # W: 設定マージン額（旧V列）
    SHIPPING = 23          # X: 送料（旧W列）
    FEE = 24               # Y: 手数料（旧X列）
    TOTAL_COST = 25        # Z: 合計原価（旧Y列）
    PROPER_PRICE = 26      # AA: 適正価格（旧Z列）
    GROSS_PROFIT = 27      # AB: 粗利額（旧AA列）
    GROSS_PROFIT_RATE = 28 # AC: 粗利率（旧AB列）
    # AD-AI: カラーミー価格情報
    SALES_PRICE = 29       # AD: 販売価格（旧AC列）
    REGULAR_PRICE = 30     # AE: 定価（旧AD列）
    MEMBERS_PRICE = 31     # AF: 会員価格（旧AE列）
    COST = 32              # AG: 原価（旧AF列）
    TAX_INCLUDED_PRICE = 33 # AH: 消費税込販売価格（旧AG列）
    TAX_AMOUNT = 34        # AI: 消費税額（旧AH列）
    # AJ-AM: カテゴリー・グループ
    CATEGORY_ID_BIG = 35   # AJ: 大カテゴリーID（旧AI列）
    CATEGORY_ID_SMALL = 36 # AK: 小カテゴリーID（旧AJ列）
    GROUP_IDS = 37         # AL: グループID（旧AK列）
    MODEL_NUMBER = 38      # AM: 型番（旧AL列）
    # AN-AT: 在庫管理
    STOCKS = 39            # AN: 在庫数（旧AM列）
    STOCK_MANAGED = 40     # AO: 在庫管理（旧AN列）
    FEW_NUM = 41           # AP: 残りわずか数（旧AO列）
    SOLDOUT_DISPLAY = 42   # AQ: 売切れ表示（旧AP列）
    MIN_NUM = 43           # AR: 最小購入数（旧AQ列）
    MAX_NUM = 44           # AS: 最大購入数（旧AR列）
    UNIT = 45              # AT: 単位（旧AS列）
    # AU-AX: 送料・配送
    DELIVERY_CHARGE = 46   # AU: 個別送料（旧AT列）
    COOL_CHARGE = 47       # AV: クール便料金（旧AU列）
    WEIGHT = 48            # AW: 重量(g)（旧AV列）
    NO_DELIVERY = 49       # AX: 配送不要（旧AW列）
    # AY-BB: 商品説明
    EXPL = 50              # AY: 商品説明（旧AX列）
    SIMPLE_EXPL = 51       # AZ: 簡易説明（旧AY列）
    MOBILE_EXPL = 52       # BA: スマホ説明（旧AZ列）
    MEMO = 53              # BB: 備考（旧BA列）
    # BC-BL: 画像
    MAIN_IMAGE = 54        # BC: メイン画像URL（旧BB列）
    THUMBNAIL = 55         # BD: サムネイルURL（旧BC列）
    IMAGE_URL_START = 56   # BE: 画像URL1（BE-BL）（旧BD列）
    # BM-BO: SEO
    PAGE_TITLE = 64        # BM: ページタイトル（旧BL列）
    META_DESC = 65         # BN: メタディスクリプション（旧BM列）
    META_KEYWORDS = 66     # BO: メタキーワード（旧BN列）
    # BP-BT: フラグ
    REDUCED_TAX = 67       # BP: 軽減税率対象（旧BO列）
    DIGITAL_CONTENT = 68   # BQ: デジタルコンテンツ（旧BP列）
    SUBSCRIPTION = 69      # BR: 定期購入（旧BQ列）
    DISPLAY_ORDER = 70     # BS: 表示順（旧BR列）
    DISABLED_PAYMENTS = 71 # BT: 利用不可決済（旧BS列）
    # BU-BV: 掲載期間
    START_DATE = 72        # BU: 掲載開始日時（旧BT列）
    END_DATE = 73          # BV: 掲載終了日時（旧BU列）
    # BW-CA: 更新制御
    PRICE_UPDATE = 74      # BW: 価格更新ON/OFF（旧BV列）
    STOCK_SYNC = 75        # BX: 在庫連動ON/OFF（旧BW列）
    DISPLAY_SYNC = 76      # BY: 表示連動（旧BX列）
    SYNC_STATUS = 77       # BZ: 同期ステータス（旧BY列）
    SYNC_DATETIME = 78     # CA: 同期日時（旧BZ列）
    # CB-CC: システム情報
    CREATED_DATE = 79      # CB: 商品作成日時（旧CA列）
    UPDATED_DATE = 80      # CC: 商品更新日時（旧CB列）


def parse_int(value: str, default: int = 0) -> int:
    """文字列を整数に変換"""
    try:
        return int(float(value)) if value else default
    except (ValueError, TypeError):
        return default


def parse_bool_ja(value: str, true_value: str = "する") -> bool:
    """日本語のブール値を変換"""
    return value.strip() == true_value


def row_to_update_data(row: list) -> dict:
    """
    シートの行データをAPI更新用の辞書に変換する

    Args:
        row: シートの行データ

    Returns:
        dict: API更新用のデータ
    """
    def get_cell(index: int, default: str = "") -> str:
        return row[index].strip() if len(row) > index and row[index] else default

    # 商品ID
    product_id = parse_int(get_cell(ColIndex.PRODUCT_ID))
    if product_id <= 0:
        return {}

    # 更新データを構築
    updates = {}

    # 商品名
    name = get_cell(ColIndex.NAME)
    if name:
        updates["name"] = name

    # 価格情報
    sales_price = parse_int(get_cell(ColIndex.SALES_PRICE))
    if sales_price > 0:
        updates["price"] = sales_price
        updates["sales_price"] = sales_price

    regular_price = parse_int(get_cell(ColIndex.REGULAR_PRICE))
    if regular_price > 0 and "price" not in updates:
        updates["price"] = regular_price

    members_price = parse_int(get_cell(ColIndex.MEMBERS_PRICE))
    if members_price > 0:
        updates["members_price"] = members_price

    cost = parse_int(get_cell(ColIndex.COST))
    if cost > 0:
        updates["cost"] = cost

    # カテゴリー
    category_id_big = parse_int(get_cell(ColIndex.CATEGORY_ID_BIG))
    category_id_small = parse_int(get_cell(ColIndex.CATEGORY_ID_SMALL))
    if category_id_big > 0:
        updates["category"] = {
            "id_big": category_id_big,
            "id_small": category_id_small
        }

    # グループID
    group_ids_str = get_cell(ColIndex.GROUP_IDS)
    if group_ids_str:
        group_ids = []
        for gid in group_ids_str.split(","):
            gid = gid.strip()
            if gid.isdigit():
                group_ids.append(int(gid))
        if group_ids:
            updates["group_ids"] = group_ids

    # 型番
    model_number = get_cell(ColIndex.MODEL_NUMBER)
    if model_number:
        updates["model_number"] = model_number

    # 掲載設定（日本語からAPIの値に変換）
    display_state_ja = get_cell(ColIndex.DISPLAY_STATE)
    display_state_map = {
        "掲載する": "showing",
        "掲載しない": "hidden",
        "会員のみ表示": "showing_for_members",
        "会員のみ購入可": "sale_for_members",
    }
    if display_state_ja in display_state_map:
        updates["display_state"] = display_state_map[display_state_ja]

    # 在庫数
    stocks = parse_int(get_cell(ColIndex.STOCKS), -1)
    if stocks >= 0:
        updates["stocks"] = stocks

    # 在庫管理
    stock_managed_str = get_cell(ColIndex.STOCK_MANAGED)
    if stock_managed_str:
        updates["stock_managed"] = parse_bool_ja(stock_managed_str, "する")

    # 残りわずか数
    few_num = parse_int(get_cell(ColIndex.FEW_NUM), -1)
    if few_num >= 0:
        updates["few_num"] = few_num

    # 売切れ表示
    soldout_display_str = get_cell(ColIndex.SOLDOUT_DISPLAY)
    if soldout_display_str:
        updates["soldout_display"] = parse_bool_ja(soldout_display_str, "表示")

    # 購入数量制限
    min_num = parse_int(get_cell(ColIndex.MIN_NUM), -1)
    if min_num >= 1:
        updates["min_num"] = min_num

    max_num = parse_int(get_cell(ColIndex.MAX_NUM), -1)
    if max_num >= 0:
        updates["max_num"] = max_num

    # 単位
    unit = get_cell(ColIndex.UNIT)
    if unit:
        updates["unit"] = unit

    # 個別送料
    delivery_charge = parse_int(get_cell(ColIndex.DELIVERY_CHARGE), -1)
    if delivery_charge >= 0:
        updates["delivery_charge"] = delivery_charge

    # 商品説明
    expl = get_cell(ColIndex.EXPL)
    if expl:
        updates["expl"] = expl

    simple_expl = get_cell(ColIndex.SIMPLE_EXPL)
    if simple_expl:
        updates["simple_expl"] = simple_expl

    return {
        "product_id": product_id,
        "name": name,
        "updates": updates,
        "row": row
    }


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="カラーミー商品同期")
    parser.add_argument("--dry-run", action="store_true", help="ドライラン（更新しない）")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=== カラーミー商品同期開始 ===")

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # カラーミーAPI確認
    if not Config.is_colorme_enabled():
        logger.error("カラーミーアクセストークンが設定されていません")
        sys.exit(1)

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    # カラーミークライアント初期化
    colorme = ColorMeClient(dry_run=args.dry_run)

    # シートからデータを取得
    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
        all_data = sheet.get_all_values()
    except Exception as e:
        logger.error(f"シート読み込みエラー: {e}")
        sys.exit(1)

    if len(all_data) <= 1:
        logger.info("データがありません")
        return

    # 更新対象を抽出
    update_targets = []
    for row_num, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
        if len(row) <= ColIndex.SYNC_MODE:
            continue

        sync_mode = row[ColIndex.SYNC_MODE].strip()
        if sync_mode != "更新":
            continue

        data = row_to_update_data(row)
        if data and data.get("product_id", 0) > 0:
            data["row_num"] = row_num
            update_targets.append(data)

    logger.info(f"更新対象: {len(update_targets)}件")

    if not update_targets:
        logger.info("更新対象の商品がありません")
        return

    # 更新実行
    success_count = 0
    fail_count = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for target in update_targets:
        product_id = target["product_id"]
        product_name = target.get("name", "不明")[:30]
        row_num = target["row_num"]
        updates = target["updates"]

        logger.info(f"処理中: {product_name} (ID: {product_id}, 行: {row_num})")

        if not updates:
            logger.warning(f"  → 更新項目なし")
            continue

        # 更新内容をログ出力
        update_keys = list(updates.keys())
        logger.info(f"  更新項目: {', '.join(update_keys)}")

        if args.dry_run:
            logger.info(f"  → [ドライラン] 更新スキップ")
            success_count += 1
            continue

        # API更新
        if colorme.update_product(product_id, updates):
            success_count += 1
            logger.info(f"  → 更新成功")

            # シートのステータスを更新
            try:
                # A列: 同期モードを「変更なし」に戻す
                sheet.update_cell(row_num, ColIndex.SYNC_MODE + 1, "変更なし")
                # BW列: 同期ステータス
                sheet.update_cell(row_num, ColIndex.SYNC_STATUS + 1, "同期済み")
                # BX列: 同期日時
                sheet.update_cell(row_num, ColIndex.SYNC_DATETIME + 1, now)
            except Exception as e:
                logger.warning(f"  ステータス更新失敗: {e}")
        else:
            fail_count += 1
            logger.error(f"  → 更新失敗")

            # エラーステータスを記録
            try:
                sheet.update_cell(row_num, ColIndex.SYNC_STATUS + 1, "エラー")
                sheet.update_cell(row_num, ColIndex.SYNC_DATETIME + 1, now)
            except Exception:
                pass

    # 結果サマリー
    logger.info("=== 結果 ===")
    logger.info(f"成功: {success_count}件")
    logger.info(f"失敗: {fail_count}件")

    if fail_count > 0:
        logger.warning("一部の商品の更新に失敗しました")
        sys.exit(1)

    logger.info("=== カラーミー商品同期完了 ===")


if __name__ == "__main__":
    main()
