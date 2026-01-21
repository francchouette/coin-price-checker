"""
商品仕入れ先一覧同期スクリプト

ブリオンスター商品ページ一覧シート（83列: A-CE）で「カラーミー登録状況」(B列)が「登録済」
の商品を商品仕入れ先一覧シート（35列: A-AI）にコピーする。

シート構造:
- ブリオンスター商品ページ一覧（83列: A-CE）
  - A-B列: 採用・登録管理列
  - C-AG列: 仕入れ先商品情報（31列）
  - AH-CE列: カラーミー登録用項目（40列）※CM商品名を追加
- 商品仕入れ先一覧（35列: A-AI）
  - ブリオンスターのC-AG列からマッピング

トリガー:
- カラーミーに商品を登録した後、B列を「登録済」に変更
- このスクリプトを実行して商品仕入れ先一覧に反映

用途:
- 新カラーミー商品管理シートからVLOOKUPで仕入れ先情報を参照可能にする
"""

import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient

logger = logging.getLogger(__name__)

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

# ブリオンスター商品ページ一覧の列インデックス（84列: A-CF、0-based）
# 84列構造 - D列にCM商品名を追加

# === A-C列: 管理列（3列）===
BS_COL_ADOPTED_FLAG = 0       # A列: 採用フラグ
BS_COL_REGISTRATION = 1       # B列: カラーミー登録状況
BS_COL_SUPPLIER_ID = 2        # C列: 仕入れ先商品ID

# === D列: CM商品名（1列）===
BS_COL_CM_PRODUCT_NAME = 3    # D列: CM商品名（AI生成）

# === E-Q列: 仕入れ先商品情報（13列）===
BS_COL_COLORME_URL = 4        # E列: カラーミー商品URL（登録後自動）
BS_COL_URL = 5                # F列: 仕入れ先商品URL（ユニークキー）
BS_COL_NAME = 6               # G列: 仕入れ先商品名
BS_COL_SITE = 7               # H列: 仕入れ先サイト（自動: Bullionstar）
BS_COL_TOP_CATEGORY = 8       # I列: 最上位カテゴリ
BS_COL_PARENT_CATEGORY = 9    # J列: 親カテゴリ
BS_COL_CHILD_CATEGORY = 10    # K列: 子カテゴリ
BS_COL_COUNTRY = 11           # L列: 製造国
BS_COL_DESC_EN = 12           # M列: 商品説明（英語）
BS_COL_SPECS = 13             # N列: 仕様・スペック
BS_COL_YEAR = 14              # O列: 発行年
BS_COL_MINTAGE = 15           # P列: 発行数・限定数
BS_COL_STOCK_STATUS = 16      # Q列: 仕入れ先在庫状況

# === R-X列: 価格情報（7列）===
BS_COL_PRICE = 17             # R列: 仕入れ先価格（現地通貨）
BS_COL_PREV_PRICE = 18        # S列: 前回仕入れ価格
BS_COL_PRICE_CHANGE = 19      # T列: 価格変動率
BS_COL_CURRENCY = 20          # U列: 取引通貨
BS_COL_EXCHANGE_TYPE = 21     # V列: 為替種類
BS_COL_EXCHANGE_RATE = 22     # W列: 為替レート
BS_COL_PRICE_JPY = 23         # X列: 仕入れ額(日本円)

# === BG-BJ列: 商品説明（4列）===
BS_COL_EXPL = 58              # BG列: 商品説明（自動生成）
BS_COL_SIMPLE_EXPL = 59       # BH列: 簡易説明（自動生成）
BS_COL_SMARTPHONE_EXPL = 60   # BI列: スマホ説明
BS_COL_MEMO = 61              # BJ列: 備考

# === BK-BT列: 画像URL（10列）===
BS_COL_IMAGE_1 = 62           # BK列: 画像URL1
BS_COL_IMAGE_2 = 63           # BL列: 画像URL2
BS_COL_IMAGE_3 = 64           # BM列: 画像URL3
BS_COL_IMAGE_4 = 65           # BN列: 画像URL4
BS_COL_IMAGE_5 = 66           # BO列: 画像URL5
BS_COL_IMAGE_6 = 67           # BP列: 画像URL6
BS_COL_IMAGE_7 = 68           # BQ列: 画像URL7
BS_COL_IMAGE_8 = 69           # BR列: 画像URL8
BS_COL_IMAGE_9 = 70           # BS列: 画像URL9
BS_COL_IMAGE_10 = 71          # BT列: 画像URL10

# === CE-CF列: システム情報（2列）===
BS_COL_SYNC_AT = 82           # CE列: 同期日時
BS_COL_CREATED_AT = 83        # CF列: 商品作成日時

# 互換用（旧列構造からの移行）- 新構造では存在しないがコードで使用されている
BS_COL_FIRST_FETCHED = BS_COL_SYNC_AT  # 初回取得日 → 同期日時で代替
BS_COL_DESC_JA = BS_COL_EXPL           # 日本語説明 → 商品説明で代替

# 商品仕入れ先一覧の列インデックス（35列: A-AI、0-based）
SP_COL_SUPPLIER_ID = 0        # A列: 仕入れ先商品ID
SP_COL_NAME = 1               # B列: 仕入れ先商品名
SP_COL_URL = 2                # C列: 仕入れ先商品URL
SP_COL_SITE = 3               # D列: 仕入れ先サイト
SP_COL_TOP_CATEGORY = 4       # E列: 最上位カテゴリ
SP_COL_PARENT_CATEGORY = 5    # F列: 親カテゴリ
SP_COL_CHILD_CATEGORY = 6     # G列: 子カテゴリ
SP_COL_COUNTRY = 7            # H列: 製造国
SP_COL_FIRST_FETCHED = 8      # I列: 初回取得日
SP_COL_STOCK_STATUS = 9       # J列: 在庫状況
SP_COL_PRICE = 10             # K列: 現在価格（現地通貨）
SP_COL_CURRENCY = 11          # L列: 取引通貨
SP_COL_EXCHANGE_TYPE = 12     # M列: 為替種類
SP_COL_EXCHANGE_RATE = 13     # N列: 為替レート
SP_COL_PRICE_JPY = 14         # O列: 日本円換算価格
SP_COL_LAST_UPDATED = 15      # P列: 最終価格更新日時
SP_COL_PREV_PRICE = 16        # Q列: 前回価格（現地通貨）
SP_COL_PRICE_CHANGE = 17      # R列: 価格変動率
SP_COL_COLORME_ID = 18        # S列: カラーミー商品ID
SP_COL_MEMO = 19              # T列: 備考
SP_COL_IMAGE_1 = 20           # U列: 画像URL1
SP_COL_IMAGE_2 = 21           # V列: 画像URL2
SP_COL_IMAGE_3 = 22           # W列: 画像URL3
SP_COL_IMAGE_4 = 23           # X列: 画像URL4
SP_COL_IMAGE_5 = 24           # Y列: 画像URL5
SP_COL_IMAGE_6 = 25           # Z列: 画像URL6
SP_COL_IMAGE_7 = 26           # AA列: 画像URL7
SP_COL_IMAGE_8 = 27           # AB列: 画像URL8
SP_COL_IMAGE_9 = 28           # AC列: 画像URL9
SP_COL_IMAGE_10 = 29          # AD列: 画像URL10
SP_COL_SPECS = 30             # AE列: 仕様・スペック
SP_COL_DESC_EN = 31           # AF列: 商品説明（英語）
SP_COL_DESC_JA = 32           # AG列: 商品説明（日本語）
SP_COL_YEAR = 33              # AH列: 発行年
SP_COL_MINTAGE = 34           # AI列: 発行数・限定数


def generate_supplier_id(existing_ids: set[str], prefix: str = "SP") -> str:
    """
    新しい仕入れ先商品IDを生成（{prefix}-XXXXXX形式）
    """
    max_num = 0
    prefix_with_dash = f"{prefix}-"
    for sid in existing_ids:
        if sid.startswith(prefix_with_dash):
            try:
                num = int(sid[len(prefix_with_dash):])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"{prefix}-{max_num + 1:06d}"


def extract_colorme_id_from_url(url: str) -> str:
    """
    カラーミー商品URLから商品IDを抽出

    Args:
        url: カラーミー商品URL (例: https://www.ybx.jp/?pid=123456)

    Returns:
        str: 商品ID（抽出失敗時は空文字）
    """
    import re
    if not url:
        return ""
    match = re.search(r'pid=(\d+)', url)
    if match:
        return match.group(1)
    return ""


def sync_registered_products_to_supplier_list() -> bool:
    """
    ブリオンスター商品ページ一覧から「登録済」商品を商品仕入れ先一覧にコピー

    処理フロー:
    1. ブリオンスター商品ページ一覧からB列=「登録済」の商品を取得
    2. 商品仕入れ先一覧に存在しない商品のみ追加
    3. 既存商品は価格情報を更新

    Returns:
        bool: 成功時True
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    try:
        # ブリオンスター商品ページ一覧シートを取得
        bs_sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        bs_data = bs_sheet.get_all_values()

        if len(bs_data) <= 1:
            logger.info("ブリオンスター商品ページ一覧にデータがありません")
            return True

        # B列=「登録済」の商品をフィルタ（88列構造: A-CJ）
        # B列(index 1) = カラーミー登録状況
        # E列(index 4) = 仕入れ先商品URL（ユニークキー）
        registered_products = []
        for row_idx, row in enumerate(bs_data[1:], start=2):
            if len(row) > BS_COL_REGISTRATION and row[BS_COL_REGISTRATION] == "登録済":
                registered_products.append((row_idx, row))

        if not registered_products:
            logger.info("「登録済」の商品がありません")
            return True

        logger.info(f"「登録済」商品数: {len(registered_products)}件")

        # 商品仕入れ先一覧シートを取得
        try:
            supplier_sheet = client._spreadsheet.worksheet(Config.SHEET_SUPPLIERS)
        except Exception:
            # シートがない場合は作成
            supplier_sheet = client._spreadsheet.add_worksheet(
                title=Config.SHEET_SUPPLIERS,
                rows=10000,
                cols=40
            )
            supplier_sheet.update('A1:AI1', [Config.SUPPLIER_HEADERS])
            logger.info(f"商品仕入れ先一覧シートを作成しました")

        supplier_data = supplier_sheet.get_all_values()
        if not supplier_data:
            supplier_sheet.update('A1:AI1', [Config.SUPPLIER_HEADERS])
            supplier_data = [Config.SUPPLIER_HEADERS]

        # 既存データをURLでインデックス化（C列=index 2がURL）
        existing_by_url: dict[str, tuple[int, list[str]]] = {}
        existing_ids: set[str] = set()
        for row_idx, row in enumerate(supplier_data[1:], start=2):
            if len(row) > SP_COL_URL and row[SP_COL_URL]:  # C列: URL
                existing_by_url[row[SP_COL_URL]] = (row_idx, row)
            if len(row) > SP_COL_SUPPLIER_ID and row[SP_COL_SUPPLIER_ID]:  # A列: 仕入れ先商品ID
                existing_ids.add(row[SP_COL_SUPPLIER_ID])

        logger.info(f"商品仕入れ先一覧の既存商品数: {len(existing_by_url)}件")

        # 新規追加行と更新行を分類
        new_rows = []
        update_cells = []
        new_count = 0
        updated_count = 0
        skipped_count = 0

        def get_bs_value(row: list, index: int) -> str:
            """ブリオンスター行から安全に値を取得"""
            return row[index] if len(row) > index else ""

        for bs_row_idx, bs_row in registered_products:
            # ブリオンスター商品ページ一覧の列構造（88列: A-CJ）
            # E列(index 4) = URL
            url = get_bs_value(bs_row, BS_COL_URL)
            if not url:
                skipped_count += 1
                continue

            if url in existing_by_url:
                # 既存商品: 価格関連列を更新
                row_idx, existing_row = existing_by_url[url]

                stock_status = get_bs_value(bs_row, BS_COL_STOCK_STATUS)
                price = get_bs_value(bs_row, BS_COL_PRICE)

                if price:
                    # 前回価格を保存（SP側のK列の現在価格をQ列に移動）
                    if len(existing_row) > SP_COL_PRICE and existing_row[SP_COL_PRICE]:
                        try:
                            prev_price = float(existing_row[SP_COL_PRICE])
                            update_cells.append((row_idx, SP_COL_PREV_PRICE + 1, str(prev_price)))  # Q列（1-based）
                        except ValueError:
                            pass

                    # J列: 在庫状況
                    if stock_status:
                        update_cells.append((row_idx, SP_COL_STOCK_STATUS + 1, stock_status))

                    # K列: 現在価格
                    update_cells.append((row_idx, SP_COL_PRICE + 1, price))

                    # L列: 取引通貨
                    currency = get_bs_value(bs_row, BS_COL_CURRENCY)
                    if currency:
                        update_cells.append((row_idx, SP_COL_CURRENCY + 1, currency))

                    # 注: 82列構造では以下の列は削除済み
                    # P列: 最終価格更新日時 - 現在時刻を設定
                    import datetime
                    now_str = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                    update_cells.append((row_idx, SP_COL_LAST_UPDATED + 1, now_str))

                    # S列: カラーミー商品ID（E列のURLから抽出）
                    colorme_url = get_bs_value(bs_row, BS_COL_COLORME_URL)
                    colorme_id = extract_colorme_id_from_url(colorme_url)
                    if colorme_id:
                        update_cells.append((row_idx, SP_COL_COLORME_ID + 1, colorme_id))

                    updated_count += 1
            else:
                # 新規商品: 35列のデータを作成
                supplier_id = generate_supplier_id(existing_ids, prefix="SP")
                existing_ids.add(supplier_id)

                # ブリオンスター商品ページ一覧（84列: A-CF）から商品仕入れ先一覧（35列: A-AI）にマッピング
                # BS: A=採用フラグ, B=登録状況, C=ID, D=CM商品名, E=カラーミーURL, F=仕入れ先URL, G=商品名, ...
                # SP: A=ID, B=名前, C=URL, D=サイト, ...
                # 注: BSで削除された列（最終価格更新日時、前回価格、価格変動率、備考）は空欄を設定
                import datetime
                now_str = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
                new_row = [
                    supplier_id,                                              # A: 仕入れ先商品ID
                    get_bs_value(bs_row, BS_COL_NAME),                        # B: 仕入れ先商品名
                    get_bs_value(bs_row, BS_COL_URL),                         # C: 仕入れ先商品URL
                    get_bs_value(bs_row, BS_COL_SITE),                        # D: 仕入れ先サイト
                    get_bs_value(bs_row, BS_COL_TOP_CATEGORY),                # E: 最上位カテゴリ
                    get_bs_value(bs_row, BS_COL_PARENT_CATEGORY),             # F: 親カテゴリ
                    get_bs_value(bs_row, BS_COL_CHILD_CATEGORY),              # G: 子カテゴリ
                    get_bs_value(bs_row, BS_COL_COUNTRY),                     # H: 製造国
                    get_bs_value(bs_row, BS_COL_FIRST_FETCHED),               # I: 初回取得日
                    get_bs_value(bs_row, BS_COL_STOCK_STATUS),                # J: 在庫状況
                    get_bs_value(bs_row, BS_COL_PRICE),                       # K: 現在価格
                    get_bs_value(bs_row, BS_COL_CURRENCY),                    # L: 取引通貨
                    get_bs_value(bs_row, BS_COL_EXCHANGE_TYPE),               # M: 為替種類
                    get_bs_value(bs_row, BS_COL_EXCHANGE_RATE),               # N: 為替レート
                    get_bs_value(bs_row, BS_COL_PRICE_JPY),                   # O: 日本円換算価格
                    now_str,                                                  # P: 最終価格更新日時（現在時刻）
                    "",                                                       # Q: 前回価格（BS側で削除済み、空欄）
                    "",                                                       # R: 価格変動率（BS側で削除済み、空欄）
                    extract_colorme_id_from_url(get_bs_value(bs_row, BS_COL_COLORME_URL)),  # S: カラーミー商品ID（URLから抽出）
                    "",                                                       # T: 備考（BS側で削除済み、空欄）
                    # 画像URL（U-AD列: 10列）
                    get_bs_value(bs_row, BS_COL_IMAGE_1),                     # U: 画像URL1
                    get_bs_value(bs_row, BS_COL_IMAGE_2),                     # V: 画像URL2
                    get_bs_value(bs_row, BS_COL_IMAGE_3),                     # W: 画像URL3
                    get_bs_value(bs_row, BS_COL_IMAGE_4),                     # X: 画像URL4
                    get_bs_value(bs_row, BS_COL_IMAGE_5),                     # Y: 画像URL5
                    get_bs_value(bs_row, BS_COL_IMAGE_6),                     # Z: 画像URL6
                    get_bs_value(bs_row, BS_COL_IMAGE_7),                     # AA: 画像URL7
                    get_bs_value(bs_row, BS_COL_IMAGE_8),                     # AB: 画像URL8
                    get_bs_value(bs_row, BS_COL_IMAGE_9),                     # AC: 画像URL9
                    get_bs_value(bs_row, BS_COL_IMAGE_10),                    # AD: 画像URL10
                    # 商品情報（AE-AI列: 5列）
                    get_bs_value(bs_row, BS_COL_SPECS),                       # AE: 仕様・スペック
                    get_bs_value(bs_row, BS_COL_DESC_EN),                     # AF: 商品説明（英語）
                    get_bs_value(bs_row, BS_COL_DESC_JA),                     # AG: 商品説明（日本語）
                    get_bs_value(bs_row, BS_COL_YEAR),                        # AH: 発行年
                    get_bs_value(bs_row, BS_COL_MINTAGE),                     # AI: 発行数・限定数
                ]
                new_rows.append(new_row)
                new_count += 1

        # 新規行を追加
        if new_rows:
            supplier_sheet.append_rows(new_rows, value_input_option='RAW')
            logger.info(f"新規追加: {new_count}件")

        # 既存行を更新
        if update_cells:
            batch_data = []
            for row_idx, col_idx, value in update_cells:
                col_letter = chr(ord('A') + col_idx - 1) if col_idx <= 26 else \
                            chr(ord('A') + (col_idx - 1) // 26 - 1) + chr(ord('A') + (col_idx - 1) % 26)
                cell_ref = f"{col_letter}{row_idx}"
                batch_data.append({
                    'range': cell_ref,
                    'values': [[value]]
                })

            batch_size = 100
            for i in range(0, len(batch_data), batch_size):
                batch_chunk = batch_data[i:i + batch_size]
                supplier_sheet.batch_update(batch_chunk, value_input_option='RAW')
                if i + batch_size < len(batch_data):
                    time.sleep(1)

            logger.info(f"価格更新: {updated_count}件")

        if skipped_count > 0:
            logger.info(f"スキップ（URLなし）: {skipped_count}件")

        if not new_rows and not update_cells:
            logger.info("追加・更新する商品はありませんでした")

        return True

    except Exception as e:
        logger.error(f"同期エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """メイン処理"""
    import argparse

    parser = argparse.ArgumentParser(
        description="ブリオンスター商品ページ一覧から「登録済」商品を商品仕入れ先一覧に同期"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logger.info("=" * 60)
    logger.info("商品仕入れ先一覧同期開始")
    logger.info("=" * 60)

    start_time = datetime.now()

    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    if sync_registered_products_to_supplier_list():
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info(f"同期完了（所要時間: {elapsed:.1f}秒）")
        logger.info("=" * 60)
        return 0
    else:
        logger.error("同期失敗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
