"""
新カラーミー商品管理シートのデータマイグレーション

旧81列構造から新88列構造へのデータ移行を行う。
- P-T列（製造国〜発行数・限定数）の5列が新規追加された
- 旧データはP列から始まる位置に格納されているため、U列以降に5列シフト
- 画像列も同様にBO列以降に移動
- AS-AX列にカテゴリー・グループ名称列（2列追加）

問題の状況:
  - 旧コードが81列構造でデータを書き込んだ
  - 新コードが86列ヘッダーを設定し、一部のデータを新位置にもコピーした
  - 結果、P-T列に本来U-Y列のデータが入り、重複が発生している

旧構造（81列）のインデックス:
  0-14 (A-O): 同期モード〜子カテゴリ
  15 (P): 仕入れ先在庫状況
  16 (Q): 仕入れ先価格（現地通貨）
  17 (R): 前回仕入れ価格
  18 (S): 価格変動率
  19 (T): 取引通貨
  20-32 (U-AG): 為替種類〜粗利率
  33-38 (AH-AM): 販売価格〜消費税額
  39-42 (AN-AQ): 大カテゴリーID〜型番
  43-49 (AR-AX): 在庫数〜単位
  50-53 (AY-BB): 個別送料〜配送不要
  54-57 (BC-BF): 商品説明〜備考
  58-67 (BG-BP): メイン画像URL〜画像URL8
  68-70 (BQ-BS): ページタイトル〜メタキーワード
  71-75 (BT-BX): 軽減税率対象〜利用不可決済
  76-77 (BY-BZ): 掲載開始日時〜掲載終了日時
  78-80 (CA-CC): 同期日時〜商品更新日時

新構造（88列: A-CJ）のインデックス:
  0-14 (A-O): 同期モード〜子カテゴリ
  15-19 (P-T): 製造国〜発行数・限定数 [新規5列]
  20-24 (U-Y): 仕入れ先在庫状況〜取引通貨
  25-37 (Z-AL): 為替種類〜粗利率
  38-43 (AM-AR): 販売価格〜消費税額
  44-49 (AS-AX): 大カテゴリーID、大カテゴリー名称、小カテゴリーID、小カテゴリー名称、グループID、グループ名 [カテゴリー名称2列追加]
  50 (AY): 型番
  51-57 (AZ-BF): 在庫数〜単位
  58-61 (BG-BJ): 個別送料〜配送不要
  62-65 (BK-BN): 商品説明〜備考
  66-75 (BO-BX): メイン画像URL〜画像URL10
  76-78 (BY-CA): ページタイトル〜メタキーワード
  79-83 (CB-CF): 軽減税率対象〜利用不可決済
  84-85 (CG-CH): 掲載開始日時〜掲載終了日時
  86-87 (CI-CJ): 同期日時〜商品作成日時
"""

import argparse
import logging
import sys
from .spreadsheet import SpreadsheetClient
from .config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def truncate(s: str, max_len: int = 30) -> str:
    """文字列を指定長で切り詰める"""
    s = str(s) if s else ""
    return s[:max_len] + "..." if len(s) > max_len else s


def migrate_row(old_row: list) -> list:
    """
    旧81列データを新88列構造に変換する

    Args:
        old_row: 旧構造の行データ（実際には86列のデータだが、P-T列に旧データが入っている）

    Returns:
        list: 新構造の行データ（88列）
    """
    # 88列に満たない場合は拡張
    while len(old_row) < 88:
        old_row.append("")

    new_row = [""] * 88

    # A-O列（0-14）: そのまま（同期モード〜子カテゴリ）
    for i in range(15):
        new_row[i] = old_row[i] if i < len(old_row) else ""

    # P-T列（15-19）: 新規列、空で初期化
    # (製造国、商品説明（英語）、仕様・スペック、発行年、発行数・限定数)
    for i in range(15, 20):
        new_row[i] = ""

    # 旧P列（15）以降のデータを5列シフトして新U列（20）以降に配置
    # さらにカテゴリー名称列（2列追加）のためにAS列以降は追加シフト
    # 現在の状況:
    #   - old_row[15-19]には旧P-T（仕入れ先在庫〜取引通貨）のデータが入っている
    #   - old_row[20-85]には一部重複データが入っている可能性がある
    #
    # 旧データ位置（old_row[15]〜）を新位置（new_row[20]〜）にコピー（5列シフト）
    # ※AS列(44)以降はさらに2列シフトが必要だが、ここでは基本的な5列シフトのみ
    for old_idx in range(15, 81):
        new_idx = old_idx + 5  # 5列シフト
        # AS列(44)以降はカテゴリー名称追加により2列追加シフト
        if new_idx >= 44:
            new_idx += 2
        if new_idx < 88:
            new_row[new_idx] = old_row[old_idx] if old_idx < len(old_row) else ""

    return new_row


def check_if_already_migrated(row: list) -> bool:
    """
    既にマイグレーション済みかどうかをチェック

    Args:
        row: 行データ

    Returns:
        bool: マイグレーション済みならTrue
    """
    if len(row) < 20:
        return True  # データが少なすぎる場合は済みとみなす

    p_value = str(row[15]).strip() if len(row) > 15 else ""

    # P列(15)に「Out of Stock」「In Stock」や数値が入っている場合は未マイグレーション
    # これらは本来U列（仕入れ先在庫状況）やV列（仕入れ先価格）のデータ
    if p_value in ["Out of Stock", "In Stock"]:
        return False

    # P列に価格データ（数値）が入っている場合も未マイグレーション
    # 数値文字列をチェック（カンマや小数点を許容）
    cleaned = p_value.replace(",", "").replace(".", "").replace("-", "")
    if cleaned.isdigit() and len(cleaned) > 0:
        return False

    # P列に通貨コード（SGD, USD等）が入っている場合も未マイグレーション
    if p_value.upper() in ["SGD", "USD", "EUR", "GBP", "AUD", "CAD", "JPY"]:
        return False

    # BG列(58)に画像URLが入っている場合も未マイグレーション
    # 新構造ではBG列は「配送不要」で、画像はBL列(63)以降
    if len(row) > 58:
        bg_value = str(row[58]).strip()
        if "shop-pro.jp" in bg_value or bg_value.startswith("http"):
            return False

    return True  # 判定できない場合は済みとみなす


def main():
    parser = argparse.ArgumentParser(description="新カラーミー商品管理シートのデータマイグレーション")
    parser.add_argument("--dry-run", action="store_true",
                        help="実際の書き込みを行わず、変更内容を表示のみ")
    parser.add_argument("--limit", type=int, default=0,
                        help="処理する行数の上限（0=無制限）")
    args = parser.parse_args()

    logger.info("=== 新カラーミー商品管理シート マイグレーション開始 ===")
    if args.dry_run:
        logger.info("※ドライランモード: 実際の書き込みは行いません")

    # 設定の検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        sys.exit(1)

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

        # 全データを取得
        all_data = sheet.get_all_values()
        if len(all_data) < 2:
            logger.info("マイグレーション対象のデータがありません")
            return

        headers = all_data[0]
        data_rows = all_data[1:]

        logger.info(f"ヘッダー列数: {len(headers)}")
        logger.info(f"データ行数: {len(data_rows)}")

        # ヘッダーの確認
        if len(headers) != 88:
            logger.warning(f"ヘッダーが88列ではありません: {len(headers)}列")

        # マイグレーション対象の特定
        rows_to_migrate = []
        for row_idx, row in enumerate(data_rows, start=2):  # 2行目から
            if not check_if_already_migrated(row):
                rows_to_migrate.append((row_idx, row))

        logger.info(f"マイグレーション対象行数: {len(rows_to_migrate)}件")

        if not rows_to_migrate:
            logger.info("マイグレーション対象がありません")
            return

        if args.limit > 0:
            rows_to_migrate = rows_to_migrate[:args.limit]
            logger.info(f"制限適用後: {len(rows_to_migrate)}件")

        # マイグレーション実行
        migrated_count = 0
        batch_data = []

        for row_idx, old_row in rows_to_migrate:
            new_row = migrate_row(old_row)

            # 変更内容をログ出力
            product_id = old_row[6] if len(old_row) > 6 else "(不明)"
            logger.info(f"行{row_idx} (商品ID: {product_id}):")

            # 変更箇所を詳細表示
            if args.dry_run:
                # P-T列（新規空列）
                logger.info(f"  P-T列(15-19): 空に設定（新規5列）")

                # 旧P列→U列（仕入れ先在庫状況）
                old_p = old_row[15] if len(old_row) > 15 else ""
                logger.info(f"  旧P(15)={truncate(old_p)} → 新U(20)={truncate(new_row[20])}")

                # 旧Q列→V列（仕入れ先価格）
                old_q = old_row[16] if len(old_row) > 16 else ""
                logger.info(f"  旧Q(16)={truncate(old_q)} → 新V(21)={truncate(new_row[21])}")

                # 旧T列→Y列（取引通貨）
                old_t = old_row[19] if len(old_row) > 19 else ""
                logger.info(f"  旧T(19)={truncate(old_t)} → 新Y(24)={truncate(new_row[24])}")

                # 旧BG列→BL列（メイン画像URL）
                old_bg = old_row[58] if len(old_row) > 58 else ""
                logger.info(f"  旧BG(58)={truncate(old_bg, 50)} → 新BL(63)={truncate(new_row[63], 50)}")

                # 旧CA列→CF列（同期日時）
                old_ca = old_row[78] if len(old_row) > 78 else ""
                logger.info(f"  旧CA(78)={truncate(old_ca)} → 新CF(83)={truncate(new_row[83])}")

            batch_data.append({
                'range': f'A{row_idx}:CJ{row_idx}',
                'values': [new_row]
            })
            migrated_count += 1

        if args.dry_run:
            logger.info(f"\n=== ドライラン完了: {migrated_count}行がマイグレーション対象 ===")
            logger.info("実際に書き込むには --dry-run オプションを外して実行してください")
        else:
            # バッチ書き込み
            if batch_data:
                logger.info(f"スプレッドシートに書き込み中... ({len(batch_data)}行)")
                sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
                logger.info(f"マイグレーション完了: {migrated_count}行")

    except Exception as e:
        logger.error(f"エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    logger.info("=== マイグレーション終了 ===")


if __name__ == "__main__":
    main()
