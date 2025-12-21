"""
カラーミー商品アップロードスクリプト

新カラーミー商品初期登録一覧シートから「確認済」ステータスの商品を取得し、
カラーミーショップに新規登録する。
"""

import logging
import sys
from datetime import datetime

from .spreadsheet import SpreadsheetClient
from .colorme import ColorMeClient, ColorMeProduct
from .config import Config

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def row_to_colorme_product(row_data: dict) -> ColorMeProduct:
    """
    シートの行データをColorMeProductに変換する

    Args:
        row_data: シートの行データ

    Returns:
        ColorMeProduct: カラーミー商品オブジェクト
    """
    # グループIDの処理（カンマ区切り文字列 → リスト）
    group_ids = []
    group_id_str = str(row_data.get("グループID", ""))
    if group_id_str:
        for gid in group_id_str.split(","):
            gid = gid.strip()
            if gid.isdigit():
                group_ids.append(int(gid))

    # 画像URLの処理
    image_urls = []
    for i in range(1, 9):
        url = row_data.get(f"画像URL{i}", "")
        if url:
            image_urls.append(url)

    # 販売価格の取得（適正価格がなければ0）
    selling_price = 0
    try:
        selling_price = int(float(row_data.get("販売価格", 0) or 0))
    except (ValueError, TypeError):
        pass

    # 定価の取得
    regular_price = 0
    try:
        regular_price = int(float(row_data.get("定価", 0) or 0))
    except (ValueError, TypeError):
        pass

    # 価格が設定されていない場合は販売価格を使用
    if regular_price == 0:
        regular_price = selling_price

    # カテゴリーIDの取得
    category_id_big = 0
    try:
        category_id_big = int(float(row_data.get("大カテゴリーID", 0) or 0))
    except (ValueError, TypeError):
        pass

    category_id_small = 0
    try:
        category_id_small = int(float(row_data.get("小カテゴリーID", 0) or 0))
    except (ValueError, TypeError):
        pass

    # 在庫数の取得
    stock_quantity = 10
    try:
        stock_quantity = int(float(row_data.get("在庫数", 10) or 10))
    except (ValueError, TypeError):
        pass

    # 在庫管理フラグ
    stock_managed = row_data.get("在庫管理", "する") == "する"

    # 売切れ表示フラグ
    soldout_display = row_data.get("売切れ表示", "表示") == "表示"

    return ColorMeProduct(
        product_id=0,  # 新規登録なので0
        name=row_data.get("商品名", ""),
        current_price=selling_price,
        colorme_url="",
        source_url=row_data.get("仕入れ先商品URL", ""),
        quantity=1,
        margin_rate=1.0,
        model_number=row_data.get("型番", ""),
        category_id_big=category_id_big,
        category_id_small=category_id_small,
        group_ids=group_ids,
        regular_price=regular_price,
        members_price=int(float(row_data.get("会員価格", 0) or 0)),
        cost=int(float(row_data.get("原価", 0) or 0)),
        delivery_charge=int(float(row_data.get("個別送料", 0) or 0)),
        stock_managed=stock_managed,
        stock_quantity=stock_quantity,
        soldout_display=soldout_display,
        few_num=int(float(row_data.get("残りわずか数", 3) or 3)),
        min_num=int(float(row_data.get("最小購入数", 1) or 1)),
        max_num=int(float(row_data.get("最大購入数", 0) or 0)),
        expl=row_data.get("商品説明", ""),
        simple_expl=row_data.get("簡易説明", ""),
        image_urls=image_urls,
        display_control=row_data.get("掲載設定", "showing"),
        selling_price=selling_price,
    )


def main():
    """メイン処理"""
    logger.info("=== カラーミー商品アップロード開始 ===")

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

    # 確認済み商品を取得
    logger.info("確認済み商品を取得中...")
    registrations = client.get_confirmed_registrations()
    logger.info(f"対象商品数: {len(registrations)}件")

    if not registrations:
        logger.info("アップロード対象の商品はありません")
        return

    # 商品を登録
    success_count = 0
    fail_count = 0

    for reg in registrations:
        row_num = reg.get("_row_num", 0)
        product_name = reg.get("商品名", "不明")
        logger.info(f"処理中: {product_name} (行: {row_num})")

        try:
            # ColorMeProductに変換
            product = row_to_colorme_product(reg)

            # バリデーション
            if not product.name:
                logger.error(f"  → 商品名が未設定です")
                client.update_initial_registration_status(
                    row_num, "エラー", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                fail_count += 1
                continue

            if product.regular_price <= 0:
                logger.error(f"  → 価格が未設定です")
                client.update_initial_registration_status(
                    row_num, "エラー", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                fail_count += 1
                continue

            if product.category_id_big <= 0:
                logger.error(f"  → カテゴリーIDが未設定です")
                client.update_initial_registration_status(
                    row_num, "エラー", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                fail_count += 1
                continue

            # カラーミーに登録
            colorme_id, error_msg = colorme.create_product(product)

            if colorme_id > 0:
                logger.info(f"  → 登録成功: カラーミー商品ID = {colorme_id}")

                # ステータス更新
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                client.update_initial_registration_status(
                    row_num, "登録済み", str(colorme_id), now
                )

                # 商品管理シートにコピー
                reg["カラーミー商品ID"] = colorme_id
                reg["カラーミー商品URL"] = f"https://ybx.jp/?pid={colorme_id}"
                reg["同期ステータス"] = "登録済み"
                reg["同期日時"] = now
                reg["商品作成日時"] = now

                if client.copy_to_colorme_management(reg):
                    logger.info(f"  → 商品管理シートにコピー完了")
                else:
                    logger.warning(f"  → 商品管理シートへのコピーに失敗")

                # 仕入れ先一覧のカラーミー商品IDを更新
                supplier_url = reg.get("仕入れ先商品URL", "")
                if supplier_url:
                    client.update_supplier_colorme_id(supplier_url, str(colorme_id))

                success_count += 1

            else:
                logger.error(f"  → 登録失敗: {error_msg}")
                client.update_initial_registration_status(
                    row_num, f"エラー: {error_msg[:50]}", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                fail_count += 1

        except Exception as e:
            logger.error(f"  → 例外エラー: {e}")
            client.update_initial_registration_status(
                row_num, f"エラー: {str(e)[:50]}", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            fail_count += 1

    # 結果サマリー
    logger.info("=== 結果 ===")
    logger.info(f"成功: {success_count}件")
    logger.info(f"失敗: {fail_count}件")

    if fail_count > 0:
        logger.warning("一部の商品の登録に失敗しました")
        sys.exit(1)

    logger.info("=== カラーミー商品アップロード完了 ===")


if __name__ == "__main__":
    main()
