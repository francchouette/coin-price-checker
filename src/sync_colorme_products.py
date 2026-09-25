"""
カラーミー商品同期スクリプト

新カラーミー商品管理シートから同期モードが「更新」の商品を取得し、
カラーミーショップAPIで商品情報を更新する。
"""

import argparse
import asyncio
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

# シートのエラー値。API送信前に空文字へ落とす（値が無い扱いにする）
SHEET_ERROR_VALUES = frozenset(
    ("#N/A", "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "#ERROR!")
)


def parse_bool_ja(value: str, true_value: str = "する") -> bool:
    """日本語のブール値を変換"""
    return value.strip() == true_value


async def _update_delivery_charges_via_playwright(targets: list) -> tuple[int, int]:
    """
    Playwright経由で個別送料を一括更新する

    Args:
        targets: [(product_id, delivery_charge, row_num), ...]

    Returns:
        (成功件数, 失敗件数)
    """
    from .colorme_image_uploader import ColorMeImageUploader

    success = 0
    fail = 0

    uploader = ColorMeImageUploader(headless=True)
    await uploader.__aenter__()
    try:
        for i, (product_id, delivery_charge, row_num) in enumerate(targets):
            logger.info(f"  [{i+1}/{len(targets)}] 商品ID {product_id}: 個別送料={delivery_charge}円 (行{row_num})")
            try:
                ok, err = await uploader.update_delivery_charge(product_id, delivery_charge)
                if ok:
                    success += 1
                    logger.info(f"    → 成功")
                else:
                    fail += 1
                    logger.warning(f"    → 失敗: {err}")
            except Exception as e:
                fail += 1
                logger.error(f"    → 例外: {e}")
    finally:
        try:
            await uploader.__aexit__(None, None, None)
        except Exception:
            pass

    return success, fail


def row_to_update_data(row: list, price_only: bool = False, sync_fields: set | None = None) -> dict:
    """
    シートの行データをAPI更新用の辞書に変換する

    Args:
        row: シートの行データ
        price_only: Trueの場合、価格・在庫・表示状態のみ（カテゴリ・型番・説明等を除外）
        sync_fields: 同期する項目セット（None=全項目）
            price, name, description, category, model, stock, display, stock_settings, shipping, seo, options, images

    Returns:
        dict: API更新用のデータ（操作フラグ含む）
    """
    # 安全策: 数式テキスト(=vlookup等)が混入している場合はAPI送信を防ぐ
    # download_colorme_products.py 側で計算値に置換されているはずだが、
    # 保険としてここでも数式リテラルを空文字に置換
    #
    # あわせてエラー値(#N/A 等)も空文字に落とす。
    # 例: BR列(軽減税率)が #N/A のとき `if reduced_tax:` を通過してしまい、
    #     意図せず False が送信される。M列(仕入れ先在庫)が #N/A の場合は
    #     "In Stock" 以外とみなされ在庫0が送信される危険もある。
    #     空文字にしておけば、各項目の「値が無ければ送らない」判定に正しく載る。
    row = [
        "" if isinstance(v, str) and (v.startswith("=") or v.strip() in SHEET_ERROR_VALUES) else v
        for v in row
    ]

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

    # sync_fieldsが指定されている場合、該当項目のみ同期
    def _enabled(field: str) -> bool:
        return sync_fields is None or field in sync_fields

    # 更新データを構築
    updates = {}
    log_parts = []  # ログ用の更新内容

    # 商品名（price_only時はスキップ）
    name = get_cell(row, Col.NAME)
    if name and not price_only and _enabled("name"):
        updates["name"] = name

    # 価格情報（C列が"OFF"でない限り更新する）
    # 定価(price)=AF列、販売価格(sales_price)=AE列 を送信
    # AE<AF の場合、カラーミー側で自動的にセール表示になる（定価 → 販売価格）
    # AE/AFが空の場合はAB(適正価格)をフォールバック
    if price_update_enabled and _enabled("price"):
        sales_price_raw = get_cell(row, Col.SALES_PRICE)
        regular_price_raw = get_cell(row, Col.REGULAR_PRICE)
        proper_price_raw = get_cell(row, Col.PROPER_PRICE)
        sales_price = get_cell_int(row, Col.SALES_PRICE)
        regular_price = get_cell_int(row, Col.REGULAR_PRICE)
        proper_price = get_cell_int(row, Col.PROPER_PRICE)
        logger.debug(f"  価格デバッグ: AE(販売)='{sales_price_raw}'→{sales_price}, AF(定価)='{regular_price_raw}'→{regular_price}, AB(適正)='{proper_price_raw}'→{proper_price}")

        # 定価: AF > AB フォールバック
        price = regular_price if regular_price > 0 else proper_price
        # 販売価格: AE > AB フォールバック
        sales = sales_price if sales_price > 0 else proper_price
        # 販売価格が定価より高い状態は不正 → 定価に揃える
        if sales > price > 0:
            logger.warning(f"  AE({sales}) > AF({price}) → 販売価格を定価に揃えます")
            sales = price

        if price > 0:
            updates["price"] = price           # 定価
            updates["sales_price"] = sales     # 販売価格（セール時はprice未満）
            updates["members_price"] = sales   # 会員価格
            updates["cost"] = sales            # 原価
            if sales < price:
                log_parts.append(f"価格: 定価{price:,}円 / セール{sales:,}円")
            else:
                log_parts.append(f"価格: {price:,}円")
        else:
            logger.warning(f"  価格が0のため更新スキップ: AE='{sales_price_raw}', AF='{regular_price_raw}', AB='{proper_price_raw}'")

    # カテゴリー・グループID（price_only時はスキップ）
    if not price_only and _enabled("category"):
        category_id_big = get_cell_int(row, Col.CATEGORY_ID_BIG)
        if category_id_big > 0:
            updates["category_id_big"] = category_id_big

        group_ids_str = get_cell(row, Col.GROUP_IDS).strip().lstrip("'")
        if group_ids_str:
            try:
                group_ids = [int(g.strip()) for g in group_ids_str.split(",") if g.strip()]
                if group_ids:
                    updates["group_ids"] = group_ids
            except ValueError:
                pass

    # 型番（price_only時はスキップ）
    if not price_only and _enabled("model"):
        model_number = get_cell(row, Col.MODEL_NUMBER)
        if model_number:
            updates["model_number"] = model_number

    # 在庫連動（D列がONの場合）
    if stock_sync_enabled and _enabled("stock"):
        # 仕入れ先の在庫状態に連動
        # - 在庫あり: AP列の在庫数をそのまま使用（ユーザーが0に設定した場合もそのまま）
        # - 在庫なし: 0に設定
        # - 判定不能(空文字): 在庫更新をスキップ（誤って在庫あり扱いにしない）
        if not supplier_stock:
            logger.warning(f"  在庫連動ON だが supplier_stock 空 → 在庫更新スキップ (安全策)")
        elif is_in_stock:
            stocks = get_cell_int(row, Col.STOCKS, 10)  # デフォルト10（セルが空の場合のみ）
            updates["stocks"] = stocks
            log_parts.append(f"在庫: {stocks}（在庫あり連動）")
        else:
            updates["stocks"] = 0
            log_parts.append("在庫: 0（在庫なし連動）")
    elif _enabled("stock"):
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

    if not _enabled("display"):
        pass  # 表示状態の同期をスキップ
    # B列=「掲載しない」の場合は、E列の設定に関係なく常に掲載しない
    elif display_setting == "掲載しない":
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
    if not price_only and _enabled("stock_settings"):
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

    # 個別送料はカラーミーAPIで設定不可のため、updatesには含めず
    # 別途Playwrightで更新するためにdelivery_charge_pendingに格納
    delivery_charge_pending = -1
    if not price_only and _enabled("shipping"):
        delivery_charge_pending = get_cell_int(row, Col.DELIVERY_CHARGE, -1)

    if not price_only and _enabled("description"):
        # 商品説明
        expl = get_cell(row, Col.EXPL)
        if expl:
            updates["expl"] = expl

        simple_expl = get_cell(row, Col.SIMPLE_EXPL)
        if simple_expl:
            updates["simple_expl"] = simple_expl

    if not price_only and _enabled("seo"):
        # SEO項目（ページタイトル・メタディスクリプション・メタキーワード）
        # ※カラーミーAPIでは現在これらのフィールドは無視される可能性あり
        #   Playwright経由の管理画面更新が必要な場合あり
        page_title = get_cell(row, Col.PAGE_TITLE)
        if page_title:
            updates["title_tag"] = page_title

        meta_desc = get_cell(row, Col.META_DESC)
        if meta_desc:
            updates["meta_description"] = meta_desc

        meta_keywords = get_cell(row, Col.META_KEYWORDS)
        if meta_keywords:
            updates["meta_keywords"] = meta_keywords

    if not price_only and _enabled("images"):
        # 画像URL（カラーミーAPIではURL指定での画像更新は非対応）
        # Playwright経由の管理画面アップロードが必要
        # ここではログ記録のみ行い、updatesには含めない
        image_cols = [Col.MAIN_IMAGE, Col.THUMBNAIL,
                      Col.IMAGE_URL_1, Col.IMAGE_URL_2, Col.IMAGE_URL_3, Col.IMAGE_URL_4,
                      Col.IMAGE_URL_5, Col.IMAGE_URL_6, Col.IMAGE_URL_7, Col.IMAGE_URL_8]
        img_count = sum(1 for c in image_cols if get_cell(row, c))
        if img_count > 0:
            log_parts.append(f"画像: {img_count}件（API非対応・ログのみ）")

    if not price_only and _enabled("options"):
        # 軽減税率対象
        reduced_tax = get_cell(row, Col.REDUCED_TAX)
        if reduced_tax:
            updates["tax_reduced"] = reduced_tax in ("対象", "する", "TRUE", "true", "True", "ON", "1")

        # デジタルコンテンツ
        digital_content = get_cell(row, Col.DIGITAL_CONTENT)
        if digital_content:
            updates["digital_content"] = digital_content in ("する", "TRUE", "true", "True", "ON", "1")

        # 定期購入
        subscription = get_cell(row, Col.SUBSCRIPTION)
        if subscription:
            updates["regular_purchase"] = subscription in ("する", "TRUE", "true", "True", "ON", "1")

    return {
        "product_id": product_id,
        "name": name,
        "updates": updates,
        "delivery_charge_pending": delivery_charge_pending,
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
    parser.add_argument("--sync-fields", type=str, default="",
                        help="同期項目をカンマ区切りで指定（空=全項目）: price,name,description,category,model,stock,display,stock_settings,shipping,seo,options,images")
    parser.add_argument("--product-ids", type=str, default="",
                        help="特定のカラーミー商品IDのみ同期（カンマ区切り、例: 192300627,192300718）")
    args = parser.parse_args()

    if args.verbose or args.compare:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.compare:
        return compare_prices(args)

    # 同期項目フィルター
    sync_fields = None
    if args.sync_fields:
        sync_fields = set(f.strip() for f in args.sync_fields.split(",") if f.strip())
        logger.info(f"同期項目フィルター: {', '.join(sorted(sync_fields))}")

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

    # product-ids 指定をパース
    target_pids = None
    if args.product_ids:
        target_pids = set()
        for s in args.product_ids.split(","):
            s = s.strip()
            if s.isdigit():
                target_pids.add(int(s))
        logger.info(f"product-ids 指定: {sorted(target_pids)}")

    # 更新対象を抽出（A列が「更新」の商品のみ）
    update_targets = []
    for row_num, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
        if len(row) <= Col.SYNC_MODE.index:
            continue

        sync_mode = get_cell(row, Col.SYNC_MODE)

        if sync_mode == "更新":
            data = row_to_update_data(row, price_only=args.price_only, sync_fields=sync_fields)
            if data and data.get("product_id", 0) > 0:
                # product-ids フィルタ
                if target_pids is not None and data["product_id"] not in target_pids:
                    continue
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
    delivery_charge_targets = []  # Playwrightで個別送料更新する対象 [(product_id, delivery_charge, row_num), ...]

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

        # 個別送料はAPIで設定不可なのでPlaywright更新キューに追加
        # 注: カラーミーでは「0円」は送料無料扱いになり送料無料ラインが無効化される
        #     AW列を空欄または負数にするとデフォルト送料（全商品共通）が適用される
        #     よって 0 はキューに追加しない（空欄扱い）
        delivery_charge_pending = target.get("delivery_charge_pending", -1)
        if delivery_charge_pending > 0:
            delivery_charge_targets.append((product_id, delivery_charge_pending, row_num))

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

    # 個別送料をPlaywrightで一括更新
    delivery_success = 0
    delivery_fail = 0
    if delivery_charge_targets:
        logger.info("")
        logger.info(f"=== 個別送料の更新（Playwright経由）===")
        logger.info(f"対象: {len(delivery_charge_targets)}件")
        try:
            delivery_success, delivery_fail = asyncio.run(
                _update_delivery_charges_via_playwright(delivery_charge_targets)
            )
        except Exception as e:
            logger.error(f"個別送料更新で例外発生: {e}")
            delivery_fail = len(delivery_charge_targets)
        logger.info(f"個別送料 更新成功: {delivery_success}件 / 失敗: {delivery_fail}件")

    # 結果サマリー
    logger.info("=== 結果 ===")
    logger.info(f"更新成功: {success_count}件")
    logger.info(f"更新失敗: {fail_count}件")
    if delivery_charge_targets:
        logger.info(f"個別送料更新成功: {delivery_success}件")
        logger.info(f"個別送料更新失敗: {delivery_fail}件")

    if fail_count > 0:
        logger.warning("一部の商品の更新に失敗しました")
        sys.exit(1)

    logger.info(f"=== カラーミー{mode_label}完了 ===")


if __name__ == "__main__":
    main()
