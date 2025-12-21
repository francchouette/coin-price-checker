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
# ※2025-12: 操作項目列（価格更新ON/OFF等）をB列の直後に移動
class ColIndex:
    """新カラーミー商品管理シートの列インデックス"""
    # A-F: 操作項目
    SYNC_MODE = 0          # A: 同期モード
    DISPLAY_STATE = 1      # B: 掲載設定
    PRICE_UPDATE = 2       # C: 価格更新ON/OFF
    STOCK_SYNC = 3         # D: 在庫連動ON/OFF
    DISPLAY_SYNC = 4       # E: 表示連動
    SYNC_STATUS = 5        # F: 同期ステータス
    # G-I: 識別情報
    PRODUCT_ID = 6         # G: カラーミー商品ID
    NAME = 7               # H: 商品名
    COLORME_URL = 8        # I: カラーミー商品URL
    # J-T: 仕入れ先情報
    SUPPLIER_URL = 9       # J: 仕入れ先商品URL
    SUPPLIER_NAME = 10     # K: 仕入れ先商品名
    SUPPLIER_SITE = 11     # L: 仕入れ先サイト
    TOP_CATEGORY = 12      # M: 最上位カテゴリ
    PARENT_CATEGORY = 13   # N: 親カテゴリ
    CHILD_CATEGORY = 14    # O: 子カテゴリ
    SUPPLIER_STOCK = 15    # P: 仕入れ先在庫状況
    SUPPLIER_PRICE = 16    # Q: 仕入れ先価格（現地通貨）
    PREV_PRICE = 17        # R: 前回仕入れ価格
    PRICE_CHANGE_RATE = 18 # S: 価格変動率
    CURRENCY = 19          # T: 取引通貨
    # U-AG: 価格計算
    EXCHANGE_TYPE = 20     # U: 為替種類
    EXCHANGE_RATE = 21     # V: 為替レート
    PURCHASE_PRICE_JPY = 22 # W: 仕入れ額(日本円)
    QUANTITY = 23          # X: 枚数
    PURCHASE_TOTAL = 24    # Y: 仕入れ合計
    MARGIN_RATE = 25       # Z: 設定マージン率
    MARGIN_AMOUNT = 26     # AA: 設定マージン額
    SHIPPING = 27          # AB: 送料
    FEE = 28               # AC: 手数料
    TOTAL_COST = 29        # AD: 合計原価
    PROPER_PRICE = 30      # AE: 適正価格
    GROSS_PROFIT = 31      # AF: 粗利額
    GROSS_PROFIT_RATE = 32 # AG: 粗利率
    # AH-AM: カラーミー価格情報
    SALES_PRICE = 33       # AH: 販売価格
    REGULAR_PRICE = 34     # AI: 定価
    MEMBERS_PRICE = 35     # AJ: 会員価格
    COST = 36              # AK: 原価
    TAX_INCLUDED_PRICE = 37 # AL: 消費税込販売価格
    TAX_AMOUNT = 38        # AM: 消費税額
    # AN-AQ: カテゴリー・グループ
    CATEGORY_ID_BIG = 39   # AN: 大カテゴリーID
    CATEGORY_ID_SMALL = 40 # AO: 小カテゴリーID
    GROUP_IDS = 41         # AP: グループID
    MODEL_NUMBER = 42      # AQ: 型番
    # AR-AX: 在庫管理
    STOCKS = 43            # AR: 在庫数
    STOCK_MANAGED = 44     # AS: 在庫管理
    FEW_NUM = 45           # AT: 残りわずか数
    SOLDOUT_DISPLAY = 46   # AU: 売切れ表示
    MIN_NUM = 47           # AV: 最小購入数
    MAX_NUM = 48           # AW: 最大購入数
    UNIT = 49              # AX: 単位
    # AY-BB: 送料・配送
    DELIVERY_CHARGE = 50   # AY: 個別送料
    COOL_CHARGE = 51       # AZ: クール便料金
    WEIGHT = 52            # BA: 重量(g)
    NO_DELIVERY = 53       # BB: 配送不要
    # BC-BF: 商品説明
    EXPL = 54              # BC: 商品説明
    SIMPLE_EXPL = 55       # BD: 簡易説明
    MOBILE_EXPL = 56       # BE: スマホ説明
    MEMO = 57              # BF: 備考
    # BG-BP: 画像
    MAIN_IMAGE = 58        # BG: メイン画像URL
    THUMBNAIL = 59         # BH: サムネイルURL
    IMAGE_URL_START = 60   # BI: 画像URL1（BI-BP）
    # BQ-BS: SEO
    PAGE_TITLE = 68        # BQ: ページタイトル
    META_DESC = 69         # BR: メタディスクリプション
    META_KEYWORDS = 70     # BS: メタキーワード
    # BT-BX: フラグ
    REDUCED_TAX = 71       # BT: 軽減税率対象
    DIGITAL_CONTENT = 72   # BU: デジタルコンテンツ
    SUBSCRIPTION = 73      # BV: 定期購入
    DISPLAY_ORDER = 74     # BW: 表示順
    DISABLED_PAYMENTS = 75 # BX: 利用不可決済
    # BY-BZ: 掲載期間
    START_DATE = 76        # BY: 掲載開始日時
    END_DATE = 77          # BZ: 掲載終了日時
    # CA-CC: システム情報
    SYNC_DATETIME = 78     # CA: 同期日時
    CREATED_DATE = 79      # CB: 商品作成日時
    UPDATED_DATE = 80      # CC: 商品更新日時


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
    colorme = ColorMeClient()

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

        # API更新
        if colorme.update_product(product_id, updates):
            success_count += 1
            logger.info(f"  → 更新成功")

            # シートのステータスを更新
            # ※A列（同期モード）は手動で変更する想定のため、自動リセットしない
            try:
                # BZ列: 同期ステータス
                sheet.update_cell(row_num, ColIndex.SYNC_STATUS + 1, "同期済み")
                # CA列: 同期日時
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
