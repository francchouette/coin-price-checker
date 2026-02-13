"""
カラーミー商品同期スクリプト

新カラーミー商品管理シートから同期モードが「更新」の商品を取得し、
カラーミーショップAPIで商品情報を更新する。
"""

import argparse
import logging
import sys
import time
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


def row_to_update_data(row: list, price_only: bool = False) -> dict:
    """
    シートの行データをAPI更新用の辞書に変換する

    Args:
        row: シートの行データ
        price_only: Trueの場合、価格・在庫・表示状態のみ（カテゴリ・型番・説明等を除外）

    Returns:
        dict: API更新用のデータ（操作フラグ含む）
    """
    # 商品ID
    product_id = get_cell_int(row, Col.PRODUCT_ID)
    if product_id <= 0:
        return {}

    # 操作フラグを取得
    # C列が"OFF"の場合のみ価格更新を無効にする（デフォルトは更新あり）
    price_update_value = get_cell(row, Col.PRICE_UPDATE).upper()
    price_update_enabled = price_update_value != "OFF"
    stock_sync_enabled = get_cell_bool(row, Col.STOCK_SYNC, "ON")
    display_sync_mode = get_cell(row, Col.DISPLAY_SYNC)
    supplier_stock = get_cell(row, Col.SUPPLIER_STOCK)

    # 在庫状況をブール値に変換（"Out of Stock" の場合のみ在庫なし）
    is_in_stock = supplier_stock.lower() != "out of stock"

    # 更新データを構築
    updates = {}
    log_parts = []  # ログ用の更新内容

    # 商品名（price_only時はスキップ）
    name = get_cell(row, Col.NAME)
    if name and not price_only:
        updates["name"] = name

    # 価格情報（C列が"OFF"でない限り更新する）
    # AE列（販売価格）の数式で計算された値を優先、なければAB列（適正価格）をフォールバック
    if price_update_enabled:
        # デバッグ: 生の値をログに記録（数式エラー等の検出用）
        sales_price_raw = get_cell(row, Col.SALES_PRICE)
        proper_price_raw = get_cell(row, Col.PROPER_PRICE)
        sales_price = get_cell_int(row, Col.SALES_PRICE)
        proper_price = get_cell_int(row, Col.PROPER_PRICE)
        logger.debug(f"  価格デバッグ: AE列(生)='{sales_price_raw}' → {sales_price}, AB列(生)='{proper_price_raw}' → {proper_price}")
        price = sales_price if sales_price > 0 else proper_price
        if price > 0:
            updates["price"] = price           # 定価
            updates["sales_price"] = price     # 販売価格
            updates["members_price"] = price   # 会員価格
            updates["cost"] = price            # 原価
            source = "販売価格(AE)" if sales_price > 0 else "適正価格(AB)"
            log_parts.append(f"価格: {price:,}円（{source}から更新）")
        else:
            logger.warning(f"  価格が0のため更新スキップ: AE列='{sales_price_raw}', AB列='{proper_price_raw}'")

    # カテゴリー（price_only時はスキップ）
    if not price_only:
        category_id_big = get_cell_int(row, Col.CATEGORY_ID_BIG)
        if category_id_big > 0:
            updates["category_id_big"] = category_id_big

    # 型番（price_only時はスキップ）
    if not price_only:
        model_number = get_cell(row, Col.MODEL_NUMBER)
        if model_number:
            updates["model_number"] = model_number

    # 在庫連動（D列がONの場合）
    if stock_sync_enabled:
        # 仕入れ先の在庫状態に連動
        # - 在庫あり: AP列の在庫数をそのまま使用（ユーザーが0に設定した場合もそのまま）
        # - 在庫なし: 0に設定
        if is_in_stock:
            stocks = get_cell_int(row, Col.STOCKS, 10)  # デフォルト10（セルが空の場合のみ）
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
    # ただし、B列=「掲載しない」の場合は常に掲載しない（E列より優先）
    display_state_map = {
        "掲載する": "showing",
        "掲載しない": "hidden",
        "会員のみ表示": "showing_for_members",
        "会員のみ購入可": "sale_for_members",
    }

    # B列の掲載設定を取得
    display_setting = get_cell(row, Col.DISPLAY_SETTING)

    # B列=「掲載しない」の場合は、E列の設定に関係なく常に掲載しない
    if display_setting == "掲載しない":
        updates["display_state"] = "hidden"
        log_parts.append("表示: 掲載しない（B列で固定）")
    elif display_sync_mode == "連動" or display_sync_mode.upper() == "ON":
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
        if display_setting in display_state_map:
            updates["display_state"] = display_state_map[display_setting]
    else:
        # その他の値はB列の掲載設定を使用
        if display_setting in display_state_map:
            updates["display_state"] = display_state_map[display_setting]

    # 以下はprice_only時はスキップ（在庫管理フラグ、送料、説明文等）
    if not price_only:
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


def compare_prices(args):
    """シートの価格とカラーミーAPIの価格を比較する（診断用）"""
    logger.info("=== 価格比較モード（同期は実行しません）===")

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    if not Config.is_colorme_enabled():
        logger.error("カラーミーアクセストークンが設定されていません")
        sys.exit(1)

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    colorme = ColorMeClient()

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
        all_data = sheet.get_all_values()
    except Exception as e:
        logger.error(f"シート読み込みエラー: {e}")
        sys.exit(1)

    if len(all_data) <= 1:
        logger.info("データがありません")
        return

    # A列が「更新」の商品を収集
    targets = []
    for row_num, row in enumerate(all_data[1:], start=2):
        if len(row) <= Col.SYNC_MODE.index:
            continue
        sync_mode = get_cell(row, Col.SYNC_MODE)
        if sync_mode == "更新":
            product_id = get_cell_int(row, Col.PRODUCT_ID)
            if product_id > 0:
                targets.append((row_num, row, product_id))

    if args.limit > 0:
        targets = targets[:args.limit]

    logger.info(f"比較対象: {len(targets)}件（A列=「更新」）")

    if not targets:
        logger.info("A列が「更新」の商品がありません")
        return

    # 比較結果
    match_count = 0
    mismatch_count = 0
    error_count = 0
    skip_count = 0

    for i, (row_num, row, product_id) in enumerate(targets):
        name = get_cell(row, Col.NAME)[:30]
        sales_price_raw = get_cell(row, Col.SALES_PRICE)
        proper_price_raw = get_cell(row, Col.PROPER_PRICE)
        sales_price = get_cell_int(row, Col.SALES_PRICE)
        proper_price = get_cell_int(row, Col.PROPER_PRICE)
        price_update = get_cell(row, Col.PRICE_UPDATE).upper()
        supplier_price_raw = get_cell(row, Col.SUPPLIER_PRICE)
        exchange_rate_raw = get_cell(row, Col.EXCHANGE_RATE)

        # シートで使われる価格
        sheet_price = sales_price if sales_price > 0 else proper_price

        logger.info(f"[{i+1}/{len(targets)}] {name} (ID: {product_id}, 行: {row_num})")
        logger.info(f"  シート: N列(仕入価格)={supplier_price_raw}, S列(為替)={exchange_rate_raw}")
        logger.info(f"  シート: AB列(適正価格)='{proper_price_raw}' → {proper_price:,}円")
        logger.info(f"  シート: AE列(販売価格)='{sales_price_raw}' → {sales_price:,}円")
        logger.info(f"  シート: 同期価格 = {sheet_price:,}円, C列(価格更新)={price_update}")

        if sheet_price <= 0:
            logger.warning(f"  ⚠ シート価格が0です！数式チェーンに問題がある可能性があります")
            error_count += 1
            continue

        if price_update == "OFF":
            logger.info(f"  → C列=OFF: 価格更新スキップ対象")
            skip_count += 1
            continue

        # カラーミーAPIから現在価格を取得
        cm_product = colorme.get_product(product_id)
        if not cm_product:
            logger.error(f"  ✗ カラーミーAPI取得失敗（商品が削除された可能性）")
            error_count += 1
            continue

        cm_price = cm_product.get("sales_price") or cm_product.get("price") or 0

        if sheet_price == cm_price:
            logger.info(f"  ✓ 一致: シート {sheet_price:,}円 = カラーミー {cm_price:,}円")
            match_count += 1
        else:
            diff = sheet_price - cm_price
            logger.warning(f"  ✗ 不一致: シート {sheet_price:,}円 ≠ カラーミー {cm_price:,}円 (差額: {diff:+,}円)")
            mismatch_count += 1

        # API制限対策
        if (i + 1) % 10 == 0:
            time.sleep(1)

    # サマリー
    logger.info("")
    logger.info("=== 比較結果 ===")
    logger.info(f"一致: {match_count}件")
    logger.info(f"不一致: {mismatch_count}件")
    logger.info(f"エラー/価格0: {error_count}件")
    logger.info(f"価格更新OFF: {skip_count}件")

    if mismatch_count > 0:
        logger.info("")
        logger.info("不一致がある場合は、以下を実行して同期してください:")
        logger.info("  python -m src.sync_colorme_products --price-only --verbose")


def main():
    """メイン処理"""
    parser = argparse.ArgumentParser(description="カラーミー商品同期")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    parser.add_argument("--price-only", action="store_true",
                        help="価格・在庫・表示状態のみ同期（カテゴリ・型番・説明等を除外）")
    parser.add_argument("--compare", action="store_true",
                        help="シートの価格とカラーミーの価格を比較（同期は実行しない）")
    parser.add_argument("--limit", type=int, default=0,
                        help="処理件数制限（0=無制限）")
    args = parser.parse_args()

    if args.verbose or args.compare:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.compare:
        return compare_prices(args)

    mode_label = "価格のみ同期" if args.price_only else "商品同期"
    logger.info(f"=== カラーミー{mode_label}開始 ===")

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
            data = row_to_update_data(row, price_only=args.price_only)
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
    pending_sheet_updates = []  # スプレッドシートへのバッチ更新用

    def flush_sheet_updates():
        """溜まったスプレッドシート更新をバッチで書き込む"""
        nonlocal pending_sheet_updates
        if not pending_sheet_updates:
            return
        try:
            sheet.batch_update(pending_sheet_updates, value_input_option='USER_ENTERED')
            logger.info(f"  [シート保存] {len(pending_sheet_updates) // 2}件のステータスを保存しました")
        except Exception as e:
            logger.warning(f"  [シート保存] バッチ更新エラー、リトライします: {e}")
            time.sleep(10)
            try:
                sheet.batch_update(pending_sheet_updates, value_input_option='USER_ENTERED')
                logger.info(f"  [シート保存] リトライ成功")
            except Exception as e2:
                logger.error(f"  [シート保存] リトライも失敗: {e2}")
        pending_sheet_updates = []

    for i, target in enumerate(update_targets):
        product_id = target["product_id"]
        product_name = target.get("name", "不明")[:30]
        row_num = target["row_num"]
        updates = target["updates"]
        log_parts = target.get("log_parts", [])
        flags = target.get("flags", {})

        logger.info(f"[{i+1}/{len(update_targets)}] {product_name} (ID: {product_id}, 行: {row_num})")

        # フラグ情報をログ出力
        flag_info = []
        if flags.get("price_update"):
            flag_info.append("価格更新ON")
        else:
            flag_info.append("価格更新OFF")
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

            # F列: 同期ステータス
            pending_sheet_updates.append({
                'range': f'{Col.SYNC_STATUS.letter}{row_num}',
                'values': [["同期済み"]]
            })
            # BY列: 同期日時
            pending_sheet_updates.append({
                'range': f'{Col.SYNC_DATETIME.letter}{row_num}',
                'values': [[now]]
            })
        else:
            fail_count += 1
            logger.error(f"  → 更新失敗")

            # F列: 同期ステータス
            pending_sheet_updates.append({
                'range': f'{Col.SYNC_STATUS.letter}{row_num}',
                'values': [["エラー"]]
            })
            # BY列: 同期日時
            pending_sheet_updates.append({
                'range': f'{Col.SYNC_DATETIME.letter}{row_num}',
                'values': [[now]]
            })

        # 50商品ごとにスプレッドシートに保存（レートリミット対策）
        if (i + 1) % 50 == 0:
            flush_sheet_updates()
            time.sleep(1)

    # 残りのステータスを保存
    flush_sheet_updates()

    # 結果サマリー
    logger.info("=== 結果 ===")
    logger.info(f"更新成功: {success_count}件")
    logger.info(f"更新失敗: {fail_count}件")

    if fail_count > 0:
        logger.warning("一部の商品の更新に失敗しました")
        sys.exit(1)

    logger.info(f"=== カラーミー{mode_label}完了 ===")


if __name__ == "__main__":
    main()
