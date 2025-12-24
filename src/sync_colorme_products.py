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
# ※2025-12: P-T列に5列追加（製造国、商品説明（英語）、仕様・スペック、発行年、発行数・限定数）
# ※2025-12: AS-AX列をカテゴリー・グループ（ID・名称）6列に拡張、型番をAY列に移動（86列→88列）
class ColIndex:
    """新カラーミー商品管理シートの列インデックス（88列: A-CJ）"""
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
    # J-Y: 仕入れ先情報（5列追加により拡張）
    SUPPLIER_URL = 9       # J: 仕入れ先商品URL
    SUPPLIER_NAME = 10     # K: 仕入れ先商品名
    SUPPLIER_SITE = 11     # L: 仕入れ先サイト
    TOP_CATEGORY = 12      # M: 最上位カテゴリ
    PARENT_CATEGORY = 13   # N: 親カテゴリ
    CHILD_CATEGORY = 14    # O: 子カテゴリ
    # P-T: 新規追加5列
    COUNTRY = 15           # P: 製造国
    DESCRIPTION_EN = 16    # Q: 商品説明（英語）
    SPECS = 17             # R: 仕様・スペック
    MINT_YEAR = 18         # S: 発行年
    MINTAGE = 19           # T: 発行数・限定数
    # U-Y: 仕入れ先価格情報
    SUPPLIER_STOCK = 20    # U: 仕入れ先在庫状況
    SUPPLIER_PRICE = 21    # V: 仕入れ先価格（現地通貨）
    PREV_PRICE = 22        # W: 前回仕入れ価格
    PRICE_CHANGE_RATE = 23 # X: 価格変動率
    CURRENCY = 24          # Y: 取引通貨
    # Z-AL: 価格計算
    EXCHANGE_TYPE = 25     # Z: 為替種類
    EXCHANGE_RATE = 26     # AA: 為替レート
    PURCHASE_PRICE_JPY = 27 # AB: 仕入れ額(日本円)
    QUANTITY = 28          # AC: 枚数
    PURCHASE_TOTAL = 29    # AD: 仕入れ合計
    MARGIN_RATE = 30       # AE: 設定マージン率
    MARGIN_AMOUNT = 31     # AF: 設定マージン額
    SHIPPING = 32          # AG: 送料
    FEE = 33               # AH: 手数料
    TOTAL_COST = 34        # AI: 合計原価
    PROPER_PRICE = 35      # AJ: 適正価格
    GROSS_PROFIT = 36      # AK: 粗利額
    GROSS_PROFIT_RATE = 37 # AL: 粗利率
    # AM-AR: カラーミー価格情報
    SALES_PRICE = 38       # AM: 販売価格
    REGULAR_PRICE = 39     # AN: 定価
    MEMBERS_PRICE = 40     # AO: 会員価格
    COST = 41              # AP: 原価
    TAX_INCLUDED_PRICE = 42 # AQ: 消費税込販売価格
    TAX_AMOUNT = 43        # AR: 消費税額
    # AS-AX: カテゴリー・グループ（6列: ID・名称）
    CATEGORY_ID_BIG = 44       # AS: 大カテゴリーID
    CATEGORY_NAME_BIG = 45     # AT: 大カテゴリー名称
    CATEGORY_ID_SMALL = 46     # AU: 小カテゴリーID
    CATEGORY_NAME_SMALL = 47   # AV: 小カテゴリー名称
    GROUP_IDS = 48             # AW: グループID
    GROUP_NAMES = 49           # AX: グループ名
    # AY: 型番（1列）
    MODEL_NUMBER = 50      # AY: 型番
    # AZ-BF: 在庫管理（7列）
    STOCKS = 51            # AZ: 在庫数
    STOCK_MANAGED = 52     # BA: 在庫管理
    FEW_NUM = 53           # BB: 残りわずか数
    SOLDOUT_DISPLAY = 54   # BC: 売切れ表示
    MIN_NUM = 55           # BD: 最小購入数
    MAX_NUM = 56           # BE: 最大購入数
    UNIT = 57              # BF: 単位
    # BG-BJ: 送料・配送（4列）
    DELIVERY_CHARGE = 58   # BG: 個別送料
    COOL_CHARGE = 59       # BH: クール便料金
    WEIGHT = 60            # BI: 重量(g)
    NO_DELIVERY = 61       # BJ: 配送不要
    # BK-BN: 商品説明（4列）
    EXPL = 62              # BK: 商品説明
    SIMPLE_EXPL = 63       # BL: 簡易説明
    MOBILE_EXPL = 64       # BM: スマホ説明
    MEMO = 65              # BN: 備考
    # BO-BX: 画像（10列）
    MAIN_IMAGE = 66        # BO: メイン画像URL
    THUMBNAIL = 67         # BP: サムネイルURL
    IMAGE_URL_START = 68   # BQ: 画像URL1（BQ-BX）
    # BY-CA: SEO（3列）
    PAGE_TITLE = 76        # BY: ページタイトル
    META_DESC = 77         # BZ: メタディスクリプション
    META_KEYWORDS = 78     # CA: メタキーワード
    # CB-CF: フラグ（5列）
    REDUCED_TAX = 79       # CB: 軽減税率対象
    DIGITAL_CONTENT = 80   # CC: デジタルコンテンツ
    SUBSCRIPTION = 81      # CD: 定期購入
    DISPLAY_ORDER = 82     # CE: 表示順
    DISABLED_PAYMENTS = 83 # CF: 利用不可決済
    # CG-CH: 掲載期間（2列）
    START_DATE = 84        # CG: 掲載開始日時
    END_DATE = 85          # CH: 掲載終了日時
    # CI-CJ: システム情報（2列）※最後の列を削除して調整
    SYNC_DATETIME = 86     # CI: 同期日時
    CREATED_DATE = 87      # CJ: 商品作成日時


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
        dict: API更新用のデータ（操作フラグ含む）
    """
    def get_cell(index: int, default: str = "") -> str:
        return row[index].strip() if len(row) > index and row[index] else default

    # 商品ID
    product_id = parse_int(get_cell(ColIndex.PRODUCT_ID))
    if product_id <= 0:
        return {}

    # 操作フラグを取得
    price_update_enabled = get_cell(ColIndex.PRICE_UPDATE).upper() == "ON"  # C列: 価格更新ON/OFF
    stock_sync_enabled = get_cell(ColIndex.STOCK_SYNC).upper() == "ON"      # D列: 在庫連動ON/OFF
    display_sync_mode = get_cell(ColIndex.DISPLAY_SYNC)                     # E列: 表示連動
    supplier_stock = get_cell(ColIndex.SUPPLIER_STOCK)                      # U列: 仕入れ先在庫状況

    # 在庫状況をブール値に変換（"Out of Stock" の場合のみ在庫なし）
    is_in_stock = supplier_stock.lower() != "out of stock"

    # 更新データを構築
    updates = {}
    log_parts = []  # ログ用の更新内容

    # 商品名
    name = get_cell(ColIndex.NAME)
    if name:
        updates["name"] = name

    # 価格情報（C列がONの場合のみ更新）
    # AJ列（適正価格）の値を販売価格、定価、会員価格、原価に一括適用
    if price_update_enabled:
        proper_price = parse_int(get_cell(ColIndex.PROPER_PRICE))  # AJ列: 適正価格
        if proper_price > 0:
            # AJ列の適正価格を全ての価格フィールドに適用
            updates["price"] = proper_price         # 定価
            updates["sales_price"] = proper_price   # 販売価格
            updates["members_price"] = proper_price # 会員価格
            updates["cost"] = proper_price          # 原価
            log_parts.append(f"価格: {proper_price:,}円（適正価格から一括更新）")

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

    # 在庫連動（D列がONの場合）
    if stock_sync_enabled:
        # 仕入れ先の在庫状態に連動
        # - 在庫あり: AW列の在庫数を使用（0以下の場合はデフォルト10）
        # - 在庫なし: 0に設定
        if is_in_stock:
            stocks = parse_int(get_cell(ColIndex.STOCKS), 10)  # デフォルト10
            # In Stockなのに在庫0以下の場合はデフォルト値を使用
            if stocks <= 0:
                stocks = 10
            updates["stocks"] = stocks
            log_parts.append(f"在庫: {stocks}（在庫あり連動）")
        else:
            updates["stocks"] = 0
            log_parts.append("在庫: 0（在庫なし連動）")
    else:
        # 在庫連動OFFの場合はAW列の値をそのまま使用
        stocks = parse_int(get_cell(ColIndex.STOCKS), -1)
        if stocks >= 0:
            updates["stocks"] = stocks

    # 表示連動（E列の値に応じて処理）
    display_state_map = {
        "掲載する": "showing",
        "掲載しない": "hidden",
        "会員のみ表示": "showing_for_members",
        "会員のみ購入可": "sale_for_members",
    }

    if display_sync_mode == "連動":
        # 在庫に連動（在庫あり=表示、なし=非表示）
        if is_in_stock:
            updates["display_state"] = "showing"
            log_parts.append("表示: 掲載する（在庫連動）")
        else:
            updates["display_state"] = "hidden"
            log_parts.append("表示: 掲載しない（在庫連動）")
    elif display_sync_mode == "表示":
        updates["display_state"] = "showing"
        log_parts.append("表示: 掲載する")
    elif display_sync_mode == "非表示":
        updates["display_state"] = "hidden"
        log_parts.append("表示: 掲載しない")
    elif display_sync_mode == "変更しない" or not display_sync_mode:
        # E列が「変更しない」または空欄の場合はB列の値を使用
        display_state_ja = get_cell(ColIndex.DISPLAY_STATE)
        if display_state_ja in display_state_map:
            updates["display_state"] = display_state_map[display_state_ja]
    else:
        # その他の値はB列の掲載設定を使用
        display_state_ja = get_cell(ColIndex.DISPLAY_STATE)
        if display_state_ja in display_state_map:
            updates["display_state"] = display_state_map[display_state_ja]

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
        "row": row,
        "log_parts": log_parts,
        "flags": {
            "price_update": price_update_enabled,
            "stock_sync": stock_sync_enabled,
            "display_sync": display_sync_mode,
            "is_in_stock": is_in_stock,
            "supplier_stock": supplier_stock,
        }
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

    # 更新対象を抽出（A列が「更新」の商品のみ）
    update_targets = []
    for row_num, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
        if len(row) <= ColIndex.SYNC_MODE:
            continue

        sync_mode = row[ColIndex.SYNC_MODE].strip()

        if sync_mode == "更新":
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
        log_parts = target.get("log_parts", [])
        flags = target.get("flags", {})

        logger.info(f"処理中: {product_name} (ID: {product_id}, 行: {row_num})")

        # フラグ情報をログ出力
        flag_info = []
        if flags.get("price_update"):
            flag_info.append("価格更新ON")
        if flags.get("stock_sync"):
            flag_info.append(f"在庫連動ON({flags.get('supplier_stock', '')})")
        if flags.get("display_sync"):
            flag_info.append(f"表示連動={flags.get('display_sync')}")
        if flag_info:
            logger.info(f"  フラグ: {', '.join(flag_info)}")

        if not updates:
            logger.warning(f"  → 更新項目なし")
            continue

        # 更新内容をログ出力
        if log_parts:
            logger.info(f"  連動更新: {', '.join(log_parts)}")
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
    logger.info(f"更新成功: {success_count}件")
    logger.info(f"更新失敗: {fail_count}件")

    if fail_count > 0:
        logger.warning("一部の商品の更新に失敗しました")
        sys.exit(1)

    logger.info("=== カラーミー商品同期完了 ===")


if __name__ == "__main__":
    main()
