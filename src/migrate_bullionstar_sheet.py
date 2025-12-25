"""
ブリオンスター商品ページ一覧シートを旧構造（83列）から新構造（81列）に移行するスクリプト

旧構造 → 新構造のマッピング:
- A-C列: 採用フラグ/登録状況/仕入れ先商品ID (変更なし)
- D列: 仕入れ先商品名 → F列
- E列: 仕入れ先商品URL → E列 (変更なし)
- F列: 仕入れ先サイト → G列
- G列: 最上位カテゴリ → H列
- H列: 親カテゴリ → I列
- I列: 子カテゴリ → J列
- J列: 製造国 → K列
- K列: 初回取得日 → 削除
- L列: 在庫状況 → P列
- M列: 現在価格 → Q列
- N列: 取引通貨 → T列
- O列: 為替種類 → U列
- P列: 為替レート → V列
- Q列: 日本円換算価格 → W列
- R列: カラーミー商品ID → D列(カラーミー商品URL)に変換
- S-AB列: 画像URL1-10 → BG-BP列（メイン画像+サムネイル+画像1-8）
- AC列: 仕様・スペック → M列
- AD列: 商品説明（英語）→ L列
- AE列: 商品説明（日本語）→ 削除
- AF列: 発行年 → N列
- AG列: 発行数・限定数 → O列
- AH列以降: カラーミー登録用項目 → 位置調整

使用方法:
    python -m src.migrate_bullionstar_sheet
    python -m src.migrate_bullionstar_sheet --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient

logger = logging.getLogger(__name__)

# 旧構造の列インデックス（0-based）
OLD_COL = {
    "採用フラグ": 0,           # A
    "登録状況": 1,             # B
    "仕入れ先商品ID": 2,       # C
    "仕入れ先商品名": 3,       # D
    "仕入れ先商品URL": 4,      # E
    "仕入れ先サイト": 5,       # F
    "最上位カテゴリ": 6,       # G
    "親カテゴリ": 7,           # H
    "子カテゴリ": 8,           # I
    "製造国": 9,               # J
    "初回取得日": 10,          # K (削除)
    "在庫状況": 11,            # L
    "現在価格": 12,            # M
    "取引通貨": 13,            # N
    "為替種類": 14,            # O
    "為替レート": 15,          # P
    "日本円換算価格": 16,      # Q
    "カラーミー商品ID": 17,    # R
    "画像URL1": 18,            # S
    "画像URL2": 19,            # T
    "画像URL3": 20,            # U
    "画像URL4": 21,            # V
    "画像URL5": 22,            # W
    "画像URL6": 23,            # X
    "画像URL7": 24,            # Y
    "画像URL8": 25,            # Z
    "画像URL9": 26,            # AA
    "画像URL10": 27,           # AB
    "仕様・スペック": 28,      # AC
    "商品説明（英語）": 29,    # AD
    "商品説明（日本語）": 30,  # AE (削除)
    "発行年": 31,              # AF
    "発行数・限定数": 32,      # AG
    "CM商品名": 33,            # AH (削除)
    "為替レート(CM)": 34,      # AI (削除)
    "仕入れ額(日本円)": 35,    # AJ (削除、W列と統合)
    # 以下は位置調整のみ
}


def get_old_value(row: list, index: int) -> str:
    """旧構造の行から安全に値を取得"""
    if len(row) > index:
        return str(row[index]) if row[index] else ""
    return ""


def migrate_row(old_row: list) -> list:
    """旧構造の行を新構造に変換"""

    # カラーミー商品IDからURLを生成
    colorme_id = get_old_value(old_row, OLD_COL["カラーミー商品ID"])
    colorme_url = f"https://www.ybx.jp/?pid={colorme_id}" if colorme_id else ""

    # 新構造（81列）の行を作成
    new_row = [
        # === 管理列（A-C列: 3列）===
        get_old_value(old_row, OLD_COL["採用フラグ"]),           # A: 採用フラグ
        get_old_value(old_row, OLD_COL["登録状況"]),             # B: カラーミー登録状況
        get_old_value(old_row, OLD_COL["仕入れ先商品ID"]),       # C: 仕入れ先商品ID

        # === 仕入れ先商品情報（D-P列: 13列）===
        colorme_url,                                              # D: カラーミー商品URL
        get_old_value(old_row, OLD_COL["仕入れ先商品URL"]),      # E: 仕入れ先商品URL
        get_old_value(old_row, OLD_COL["仕入れ先商品名"]),       # F: 仕入れ先商品名
        get_old_value(old_row, OLD_COL["仕入れ先サイト"]),       # G: 仕入れ先サイト
        get_old_value(old_row, OLD_COL["最上位カテゴリ"]),       # H: 最上位カテゴリ
        get_old_value(old_row, OLD_COL["親カテゴリ"]),           # I: 親カテゴリ
        get_old_value(old_row, OLD_COL["子カテゴリ"]),           # J: 子カテゴリ
        get_old_value(old_row, OLD_COL["製造国"]),               # K: 製造国
        get_old_value(old_row, OLD_COL["商品説明（英語）"]),     # L: 商品説明（英語）
        get_old_value(old_row, OLD_COL["仕様・スペック"]),       # M: 仕様・スペック
        get_old_value(old_row, OLD_COL["発行年"]),               # N: 発行年
        get_old_value(old_row, OLD_COL["発行数・限定数"]),       # O: 発行数・限定数
        get_old_value(old_row, OLD_COL["在庫状況"]),             # P: 仕入れ先在庫状況

        # === 価格情報（Q-AG列: 17列）===
        get_old_value(old_row, OLD_COL["現在価格"]),             # Q: 仕入れ先価格（現地通貨）
        "",                                                       # R: 前回仕入れ価格
        "",                                                       # S: 価格変動率
        get_old_value(old_row, OLD_COL["取引通貨"]),             # T: 取引通貨
        get_old_value(old_row, OLD_COL["為替種類"]),             # U: 為替種類
        get_old_value(old_row, OLD_COL["為替レート"]),           # V: 為替レート
        get_old_value(old_row, OLD_COL["日本円換算価格"]),       # W: 仕入れ額(日本円)
        "",                                                       # X: 枚数
        "",                                                       # Y: 仕入れ合計
        "",                                                       # Z: 設定マージン率
        "",                                                       # AA: 設定マージン額
        "",                                                       # AB: 送料
        "",                                                       # AC: 諸経費
        "",                                                       # AD: 合計原価
        "",                                                       # AE: 適正価格
        "",                                                       # AF: 粗利額
        "",                                                       # AG: 粗利率

        # === カラーミー価格情報（AH-AM列: 6列）===
        "",                                                       # AH: 販売価格
        "",                                                       # AI: 定価
        "",                                                       # AJ: 会員価格
        "",                                                       # AK: 原価
        "",                                                       # AL: 消費税込販売価格
        "",                                                       # AM: 消費税額

        # === カテゴリー・グループ（AN-AQ列: 4列）===
        get_old_value(old_row, 50) if len(old_row) > 50 else "",  # AN: 大カテゴリーID
        get_old_value(old_row, 51) if len(old_row) > 51 else "",  # AO: 小カテゴリーID
        get_old_value(old_row, 52) if len(old_row) > 52 else "",  # AP: グループID
        get_old_value(old_row, OLD_COL["仕入れ先商品ID"]),       # AQ: 型番（=仕入れ先商品ID）

        # === 在庫管理（AR-AX列: 7列）===
        get_old_value(old_row, 54) if len(old_row) > 54 else "",  # AR: 在庫数
        get_old_value(old_row, 55) if len(old_row) > 55 else "",  # AS: 在庫管理
        get_old_value(old_row, 56) if len(old_row) > 56 else "",  # AT: 残りわずか数
        get_old_value(old_row, 57) if len(old_row) > 57 else "",  # AU: 売切れ表示
        get_old_value(old_row, 58) if len(old_row) > 58 else "",  # AV: 最小購入数
        get_old_value(old_row, 59) if len(old_row) > 59 else "",  # AW: 最大購入数
        get_old_value(old_row, 60) if len(old_row) > 60 else "",  # AX: 単位

        # === 送料・配送（AY-BB列: 4列）===
        get_old_value(old_row, 61) if len(old_row) > 61 else "",  # AY: 個別送料
        get_old_value(old_row, 62) if len(old_row) > 62 else "",  # AZ: クール便料金
        get_old_value(old_row, 63) if len(old_row) > 63 else "",  # BA: 重量(g)
        get_old_value(old_row, 64) if len(old_row) > 64 else "",  # BB: 配送不要

        # === 商品説明（BC-BF列: 4列）===
        get_old_value(old_row, 65) if len(old_row) > 65 else "",  # BC: 商品説明
        get_old_value(old_row, 66) if len(old_row) > 66 else "",  # BD: 簡易説明
        get_old_value(old_row, 67) if len(old_row) > 67 else "",  # BE: スマホ説明
        "",                                                       # BF: 備考

        # === 画像URL（BG-BP列: 10列）===
        get_old_value(old_row, OLD_COL["画像URL1"]),             # BG: メイン画像URL
        "",                                                       # BH: サムネイルURL
        get_old_value(old_row, OLD_COL["画像URL2"]),             # BI: 画像URL1
        get_old_value(old_row, OLD_COL["画像URL3"]),             # BJ: 画像URL2
        get_old_value(old_row, OLD_COL["画像URL4"]),             # BK: 画像URL3
        get_old_value(old_row, OLD_COL["画像URL5"]),             # BL: 画像URL4
        get_old_value(old_row, OLD_COL["画像URL6"]),             # BM: 画像URL5
        get_old_value(old_row, OLD_COL["画像URL7"]),             # BN: 画像URL6
        get_old_value(old_row, OLD_COL["画像URL8"]),             # BO: 画像URL7
        get_old_value(old_row, OLD_COL["画像URL9"]),             # BP: 画像URL8

        # === SEO項目（BQ-BS列: 3列）===
        get_old_value(old_row, 68) if len(old_row) > 68 else "",  # BQ: ページタイトル
        get_old_value(old_row, 69) if len(old_row) > 69 else "",  # BR: メタディスクリプション
        get_old_value(old_row, 70) if len(old_row) > 70 else "",  # BS: メタキーワード

        # === フラグ・設定（BT-BX列: 5列）===
        get_old_value(old_row, 71) if len(old_row) > 71 else "",  # BT: 軽減税率対象
        get_old_value(old_row, 72) if len(old_row) > 72 else "",  # BU: デジタルコンテンツ
        get_old_value(old_row, 73) if len(old_row) > 73 else "",  # BV: 定期購入
        get_old_value(old_row, 74) if len(old_row) > 74 else "",  # BW: 表示順
        get_old_value(old_row, 75) if len(old_row) > 75 else "",  # BX: 利用不可決済

        # === 掲載期間（BY-BZ列: 2列）===
        get_old_value(old_row, 76) if len(old_row) > 76 else "",  # BY: 掲載開始日時
        get_old_value(old_row, 77) if len(old_row) > 77 else "",  # BZ: 掲載終了日時

        # === システム情報（CA-CC列: 3列）===
        get_old_value(old_row, 78) if len(old_row) > 78 else "",  # CA: 同期日時
        get_old_value(old_row, 79) if len(old_row) > 79 else "",  # CB: 商品作成日時
        get_old_value(old_row, 80) if len(old_row) > 80 else "",  # CC: 商品更新日時
    ]

    return new_row


def migrate_sheet(dry_run: bool = False) -> bool:
    """シートを移行する"""
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    sheet_name = Config.SHEET_BULLIONSTAR_PRODUCTS
    headers = Config.BULLIONSTAR_PRODUCT_HEADERS

    try:
        sheet = client._spreadsheet.worksheet(sheet_name)
        logger.info(f"シート '{sheet_name}' を取得しました")

        # 全データを取得
        all_data = sheet.get_all_values()

        # 現在のヘッダーを確認
        current_headers = all_data[0] if all_data else []
        logger.info(f"現在のヘッダー列数: {len(current_headers)}")
        logger.info(f"新しいヘッダー列数: {len(headers)}")

        # データ行がない場合はヘッダーのみ更新
        if len(all_data) <= 1:
            logger.info("データ行がありません。ヘッダーのみ更新します。")

            if dry_run:
                logger.info(f"\n[DRY-RUN] ヘッダーを更新予定: {len(current_headers)}列 → {len(headers)}列")
                return True

            # ヘッダーを更新
            logger.info("ヘッダーを更新中...")
            sheet.clear()
            sheet.update('A1:CC1', [headers])
            logger.info("ヘッダーの更新が完了しました")
            return True

        logger.info(f"既存データ: {len(all_data) - 1}行")

        # ヘッダー行をスキップしてデータ行を変換
        new_rows = []
        for i, old_row in enumerate(all_data[1:], start=2):
            new_row = migrate_row(old_row)
            new_rows.append(new_row)

            if i <= 3:  # 最初の2行のみログ出力
                logger.info(f"行{i}: {get_old_value(old_row, OLD_COL['仕入れ先商品名'])[:30]}...")
                logger.info(f"  旧E列(URL): {get_old_value(old_row, OLD_COL['仕入れ先商品URL'])[:50]}...")
                logger.info(f"  新E列(URL): {new_row[4][:50] if new_row[4] else '(空)'}...")

        if dry_run:
            logger.info(f"\n[DRY-RUN] 移行予定: {len(new_rows)}行")
            logger.info(f"新ヘッダー数: {len(headers)}列")
            logger.info(f"新データ列数: {len(new_rows[0])}列")
            return True

        # シートをクリアして新構造で書き直す
        logger.info("シートをクリア中...")
        sheet.clear()

        # ヘッダーを書き込む
        logger.info("新ヘッダーを書き込み中...")
        sheet.update('A1:CC1', [headers])

        # データを書き込む（一括更新）
        if new_rows:
            logger.info(f"データを書き込み中... ({len(new_rows)}行)")
            # 100行ずつ分割して書き込む
            batch_size = 100
            for i in range(0, len(new_rows), batch_size):
                batch = new_rows[i:i + batch_size]
                start_row = i + 2  # ヘッダーの次の行から
                end_row = start_row + len(batch) - 1
                range_str = f"A{start_row}:CC{end_row}"
                sheet.update(range_str, batch, value_input_option='RAW')
                logger.info(f"  {start_row}-{end_row}行を書き込み完了")

        logger.info(f"移行完了: {len(new_rows)}行")
        return True

    except Exception as e:
        logger.error(f"移行エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    parser = argparse.ArgumentParser(
        description="ブリオンスター商品ページ一覧シートを新構造に移行"
    )
    parser.add_argument("--dry-run", action="store_true", help="実際の変更は行わない")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("=" * 60)
    logger.info("ブリオンスター商品ページ一覧シート移行スクリプト")
    logger.info("旧構造（83列）→ 新構造（81列）")
    if args.dry_run:
        logger.info("※ ドライランモード")
    logger.info("=" * 60)

    # 設定検証
    errors = Config.validate()
    if errors and not args.dry_run:
        for error in errors:
            logger.error(error)
        return 1

    if migrate_sheet(dry_run=args.dry_run):
        logger.info("移行が完了しました")
        return 0
    else:
        logger.error("移行に失敗しました")
        return 1


if __name__ == "__main__":
    sys.exit(main())
