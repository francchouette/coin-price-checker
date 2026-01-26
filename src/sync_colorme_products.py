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
from .cm_sheet_columns import Col, get_cell, get_cell_int, get_cell_bool

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
    # 商品ID
    product_id = get_cell_int(row, Col.PRODUCT_ID)
    if product_id <= 0:
        return {}

    # 操作フラグを取得
    price_update_enabled = get_cell_bool(row, Col.PRICE_UPDATE, "ON")
    stock_sync_enabled = get_cell_bool(row, Col.STOCK_SYNC, "ON")
    display_sync_mode = get_cell(row, Col.DISPLAY_SYNC)
    supplier_stock = get_cell(row, Col.SUPPLIER_STOCK)

    # 在庫状況をブール値に変換（"Out of Stock" の場合のみ在庫なし）
    is_in_stock = supplier_stock.lower() != "out of stock"

    # 更新データを構築
    updates = {}
    log_parts = []  # ログ用の更新内容

    # 商品名
    name = get_cell(row, Col.NAME)
    if name:
        updates["name"] = name

    # 価格情報（C列がONの場合のみ更新）
    # AJ列（適正価格）の値を販売価格、定価、会員価格、原価に一括適用
    if price_update_enabled:
        proper_price = get_cell_int(row, Col.PROPER_PRICE)
        if proper_price > 0:
            # AJ列の適正価格を全ての価格フィールドに適用
            updates["price"] = proper_price         # 定価
            updates["sales_price"] = proper_price   # 販売価格
            updates["members_price"] = proper_price # 会員価格
            updates["cost"] = proper_price          # 原価
            log_parts.append(f"価格: {proper_price:,}円（適正価格から一括更新）")

    # カテゴリー
    category_id_big = get_cell_int(row, Col.CATEGORY_ID_BIG)
    category_id_small = get_cell_int(row, Col.CATEGORY_ID_SMALL)
    if category_id_big > 0:
        updates["category"] = {
            "id_big": category_id_big,
            "id_small": category_id_small
        }

    # 型番
    model_number = get_cell(row, Col.MODEL_NUMBER)
    if model_number:
        updates["model_number"] = model_number

    # 在庫連動（D列がONの場合）
    if stock_sync_enabled:
        # 仕入れ先の在庫状態に連動
        # - 在庫あり: AZ列の在庫数を使用（0以下の場合はデフォルト10）
        # - 在庫なし: 0に設定
        if is_in_stock:
            stocks = get_cell_int(row, Col.STOCKS, 10)  # デフォルト10
            # In Stockなのに在庫0以下の場合はデフォルト値を使用
            if stocks <= 0:
                stocks = 10
            updates["stocks"] = stocks
            log_parts.append(f"在庫: {stocks}（在庫あり連動）")
        else:
            updates["stocks"] = 0
            log_parts.append("在庫: 0（在庫なし連動）")
    else:
        # 在庫連動OFFの場合はAZ列の値をそのまま使用
        stocks = get_cell_int(row, Col.STOCKS, -1)
        if stocks >= 0:
            updates["stocks"] = stocks

    # 表示連動（E列の値に応じて処理）
    display_state_map = {
        "掲載する": "showing",
        "掲載しない": "hidden",
        "会員のみ表示": "showing_for_members",
        "会員のみ購入可": "sale_for_members",
    }

    if display_sync_mode == "連動" or display_sync_mode.upper() == "ON":
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
    elif display_sync_mode == "変更しない" or display_sync_mode.upper() == "OFF" or not display_sync_mode:
        # E列が「変更しない」「OFF」または空欄の場合はB列の値を使用
        display_state_ja = get_cell(row, Col.DISPLAY_SETTING)
        if display_state_ja in display_state_map:
            updates["display_state"] = display_state_map[display_state_ja]
    else:
        # その他の値はB列の掲載設定を使用
        display_state_ja = get_cell(row, Col.DISPLAY_SETTING)
        if display_state_ja in display_state_map:
            updates["display_state"] = display_state_map[display_state_ja]

    # 在庫管理
    stock_managed_str = get_cell(row, Col.STOCK_MANAGED)
    if stock_managed_str:
        updates["stock_managed"] = parse_bool_ja(stock_managed_str, "する")

    # 残りわずか数
    few_num = get_cell_int(row, Col.FEW_NUM, -1)
    if few_num >= 0:
        updates["few_num"] = few_num

    # 売切れ表示
    soldout_display_str = get_cell(row, Col.SOLDOUT_DISPLAY)
    if soldout_display_str:
        updates["soldout_display"] = parse_bool_ja(soldout_display_str, "表示")

    # 購入数量制限
    min_num = get_cell_int(row, Col.MIN_NUM, -1)
    if min_num >= 1:
        updates["min_num"] = min_num

    max_num = get_cell_int(row, Col.MAX_NUM, -1)
    if max_num >= 0:
        updates["max_num"] = max_num

    # 単位
    unit = get_cell(row, Col.UNIT)
    if unit:
        updates["unit"] = unit

    # 個別送料
    delivery_charge = get_cell_int(row, Col.DELIVERY_CHARGE, -1)
    if delivery_charge >= 0:
        updates["delivery_charge"] = delivery_charge

    # 商品説明
    expl = get_cell(row, Col.EXPL)
    if expl:
        updates["expl"] = expl

    simple_expl = get_cell(row, Col.SIMPLE_EXPL)
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
        if len(row) <= Col.SYNC_MODE.index:
            continue

        sync_mode = get_cell(row, Col.SYNC_MODE)

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
                # F列: 同期ステータス
                sheet.update_cell(row_num, Col.SYNC_STATUS.index + 1, "同期済み")
                # CI列: 同期日時
                sheet.update_cell(row_num, Col.SYNC_DATETIME.index + 1, now)
            except Exception as e:
                logger.warning(f"  ステータス更新失敗: {e}")
        else:
            fail_count += 1
            logger.error(f"  → 更新失敗")

            # エラーステータスを記録
            try:
                # F列: 同期ステータス
                sheet.update_cell(row_num, Col.SYNC_STATUS.index + 1, "エラー")
                # CI列: 同期日時
                sheet.update_cell(row_num, Col.SYNC_DATETIME.index + 1, now)
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
