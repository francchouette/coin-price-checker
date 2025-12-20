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
class ColIndex:
    """新カラーミー商品管理シートの列インデックス"""
    # A-B: 操作項目
    SYNC_MODE = 0          # A: 同期モード
    DISPLAY_STATE = 1      # B: 掲載設定
    # C-E: 識別情報
    PRODUCT_ID = 2         # C: カラーミー商品ID
    NAME = 3               # D: 商品名
    COLORME_URL = 4        # E: カラーミー商品URL
    # F-O: 仕入れ先情報
    SUPPLIER_URL = 5       # F: 仕入れ先商品URL
    SUPPLIER_NAME = 6      # G: 仕入れ先商品名
    SUPPLIER_SITE = 7      # H: 仕入れ先サイト
    TOP_CATEGORY = 8       # I: 最上位カテゴリ
    PARENT_CATEGORY = 9    # J: 親カテゴリ
    CHILD_CATEGORY = 10    # K: 子カテゴリ
    SUPPLIER_PRICE = 11    # L: 仕入れ先価格（現地通貨）
    PREV_PRICE = 12        # M: 前回仕入れ価格
    PRICE_CHANGE_RATE = 13 # N: 価格変動率
    CURRENCY = 14          # O: 取引通貨
    # P-AB: 価格計算
    EXCHANGE_TYPE = 15     # P: 為替種類
    EXCHANGE_RATE = 16     # Q: 為替レート
    PURCHASE_PRICE_JPY = 17 # R: 仕入れ額(日本円)
    QUANTITY = 18          # S: 枚数
    PURCHASE_TOTAL = 19    # T: 仕入れ合計
    MARGIN_RATE = 20       # U: 設定マージン率
    MARGIN_AMOUNT = 21     # V: 設定マージン額
    SHIPPING = 22          # W: 送料
    FEE = 23               # X: 手数料
    TOTAL_COST = 24        # Y: 合計原価
    PROPER_PRICE = 25      # Z: 適正価格
    GROSS_PROFIT = 26      # AA: 粗利額
    GROSS_PROFIT_RATE = 27 # AB: 粗利率
    # AC-AH: カラーミー価格情報
    SALES_PRICE = 28       # AC: 販売価格
    REGULAR_PRICE = 29     # AD: 定価
    MEMBERS_PRICE = 30     # AE: 会員価格
    COST = 31              # AF: 原価
    TAX_INCLUDED_PRICE = 32 # AG: 消費税込販売価格
    TAX_AMOUNT = 33        # AH: 消費税額
    # AI-AL: カテゴリー・グループ
    CATEGORY_ID_BIG = 34   # AI: 大カテゴリーID
    CATEGORY_ID_SMALL = 35 # AJ: 小カテゴリーID
    GROUP_IDS = 36         # AK: グループID
    MODEL_NUMBER = 37      # AL: 型番
    # AM-AS: 在庫管理
    STOCKS = 38            # AM: 在庫数
    STOCK_MANAGED = 39     # AN: 在庫管理
    FEW_NUM = 40           # AO: 残りわずか数
    SOLDOUT_DISPLAY = 41   # AP: 売切れ表示
    MIN_NUM = 42           # AQ: 最小購入数
    MAX_NUM = 43           # AR: 最大購入数
    UNIT = 44              # AS: 単位
    # AT-AW: 送料・配送
    DELIVERY_CHARGE = 45   # AT: 個別送料
    COOL_CHARGE = 46       # AU: クール便料金
    WEIGHT = 47            # AV: 重量(g)
    NO_DELIVERY = 48       # AW: 配送不要
    # AX-BA: 商品説明
    EXPL = 49              # AX: 商品説明
    SIMPLE_EXPL = 50       # AY: 簡易説明
    MOBILE_EXPL = 51       # AZ: スマホ説明
    MEMO = 52              # BA: 備考
    # BB-BK: 画像
    MAIN_IMAGE = 53        # BB: メイン画像URL
    THUMBNAIL = 54         # BC: サムネイルURL
    IMAGE_URL_START = 55   # BD: 画像URL1（BD-BK）
    # BL-BN: SEO
    PAGE_TITLE = 63        # BL: ページタイトル
    META_DESC = 64         # BM: メタディスクリプション
    META_KEYWORDS = 65     # BN: メタキーワード
    # BO-BS: フラグ
    REDUCED_TAX = 66       # BO: 軽減税率対象
    DIGITAL_CONTENT = 67   # BP: デジタルコンテンツ
    SUBSCRIPTION = 68      # BQ: 定期購入
    DISPLAY_ORDER = 69     # BR: 表示順
    DISABLED_PAYMENTS = 70 # BS: 利用不可決済
    # BT-BU: 掲載期間
    START_DATE = 71        # BT: 掲載開始日時
    END_DATE = 72          # BU: 掲載終了日時
    # BV-BZ: 更新制御
    PRICE_UPDATE = 73      # BV: 価格更新ON/OFF
    STOCK_SYNC = 74        # BW: 在庫連動ON/OFF
    DISPLAY_SYNC = 75      # BX: 表示連動
    SYNC_STATUS = 76       # BY: 同期ステータス
    SYNC_DATETIME = 77     # BZ: 同期日時
    # CA-CB: システム情報
    CREATED_DATE = 78      # CA: 商品作成日時
    UPDATED_DATE = 79      # CB: 商品更新日時


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
