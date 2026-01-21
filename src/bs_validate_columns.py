"""
ブリオンスター商品ページ一覧シートの列定義検証スクリプト

スプレッドシートのヘッダー行と bs_sheet_columns.py の定義を比較し、
不整合があれば報告する。

使用方法:
    python -m src.bs_validate_columns
    python -m src.bs_validate_columns --fix  # 不整合を自動修正（未実装）

GitHub Actions:
    [BS0] Validate Sheet Columns ワークフローで定期実行
"""

import argparse
import logging
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.bs_sheet_columns import Col, Column

logger = logging.getLogger(__name__)

# シート名
SHEET_NAME = "ブリオンスター商品ページ一覧"


def validate_columns() -> tuple[bool, list[str]]:
    """
    スプレッドシートのヘッダーと列定義を検証

    Returns:
        tuple[bool, list[str]]: (検証成功, エラーメッセージのリスト)
    """
    errors = []
    warnings = []

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        return False, ["スプレッドシートへの接続に失敗しました"]

    try:
        sheet = client._spreadsheet.worksheet(SHEET_NAME)
        # ヘッダー行（1行目）を取得
        header_row = sheet.row_values(1)
        logger.info(f"ヘッダー行を取得: {len(header_row)}列")

    except Exception as e:
        return False, [f"シート '{SHEET_NAME}' の読み取りエラー: {e}"]

    # 列定義を取得
    defined_columns = Col.all_columns()
    logger.info(f"定義された列数: {len(defined_columns)}列")

    # 列数の検証
    if len(header_row) != Col.TOTAL_COLUMNS:
        errors.append(
            f"列数不一致: スプレッドシート={len(header_row)}列, 定義={Col.TOTAL_COLUMNS}列"
        )

    # 各列の検証
    for col in defined_columns:
        if col.index >= len(header_row):
            errors.append(
                f"列 {col.letter} ({col.index}): スプレッドシートに存在しない"
            )
            continue

        sheet_header = header_row[col.index].strip()
        defined_name = col.name

        # 完全一致チェック
        if sheet_header != defined_name:
            # 部分一致チェック（名前の主要部分が含まれているか）
            if defined_name in sheet_header or sheet_header in defined_name:
                warnings.append(
                    f"列 {col.letter} ({col.index}): 部分一致 "
                    f"[シート: '{sheet_header}'] vs [定義: '{defined_name}']"
                )
            else:
                errors.append(
                    f"列 {col.letter} ({col.index}): 名前不一致 "
                    f"[シート: '{sheet_header}'] vs [定義: '{defined_name}']"
                )

    # スプレッドシートに定義されていない追加列があるかチェック
    if len(header_row) > Col.TOTAL_COLUMNS:
        extra_cols = header_row[Col.TOTAL_COLUMNS:]
        for i, name in enumerate(extra_cols):
            if name.strip():
                warnings.append(
                    f"未定義の追加列 {Col.TOTAL_COLUMNS + i}: '{name}'"
                )

    # 結果をログ出力
    if warnings:
        logger.warning("=== 警告 ===")
        for w in warnings:
            logger.warning(f"  {w}")

    if errors:
        logger.error("=== エラー ===")
        for e in errors:
            logger.error(f"  {e}")
        return False, errors

    return True, []


def print_column_mapping():
    """列定義のマッピングを表示"""
    print("\n=== 列定義マッピング ===")
    print(f"{'Index':<6} {'Letter':<6} {'Name':<30}")
    print("-" * 45)

    for col in Col.all_columns():
        print(f"{col.index:<6} {col.letter:<6} {col.name:<30}")

    print(f"\n総列数: {Col.TOTAL_COLUMNS}")
    print(f"最終列: {Col.last_column_letter()}")


def print_comparison(header_row: list[str]):
    """スプレッドシートのヘッダーと定義を並べて表示"""
    print("\n=== ヘッダー比較 ===")
    print(f"{'Index':<6} {'Letter':<6} {'Sheet Header':<35} {'Defined Name':<35} {'Status'}")
    print("-" * 120)

    defined_columns = Col.all_columns()
    max_len = max(len(header_row), len(defined_columns))

    for i in range(max_len):
        letter = Col.all_columns()[i].letter if i < len(defined_columns) else "?"
        sheet_name = header_row[i].strip() if i < len(header_row) else "(なし)"
        defined_name = defined_columns[i].name if i < len(defined_columns) else "(未定義)"

        if i >= len(header_row):
            status = "MISSING IN SHEET"
        elif i >= len(defined_columns):
            status = "EXTRA IN SHEET"
        elif sheet_name == defined_name:
            status = "OK"
        elif defined_name in sheet_name or sheet_name in defined_name:
            status = "PARTIAL"
        else:
            status = "MISMATCH"

        print(f"{i:<6} {letter:<6} {sheet_name:<35} {defined_name:<35} {status}")


def main():
    parser = argparse.ArgumentParser(
        description="ブリオンスター商品ページ一覧シートの列定義を検証"
    )
    parser.add_argument(
        '--show-mapping',
        action='store_true',
        help='列定義のマッピングを表示'
    )
    parser.add_argument(
        '--compare',
        action='store_true',
        help='スプレッドシートのヘッダーと定義を並べて比較表示'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='詳細ログを出力'
    )

    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    if args.show_mapping:
        print_column_mapping()
        return 0

    logger.info("=" * 60)
    logger.info("ブリオンスター商品ページ一覧 列定義検証")
    logger.info("=" * 60)

    if args.compare:
        # 比較表示モード
        client = SpreadsheetClient()
        if not client.connect():
            logger.error("スプレッドシートへの接続に失敗しました")
            return 1

        try:
            sheet = client._spreadsheet.worksheet(SHEET_NAME)
            header_row = sheet.row_values(1)
            print_comparison(header_row)
            return 0
        except Exception as e:
            logger.error(f"シート読み取りエラー: {e}")
            return 1

    # 検証実行
    success, errors = validate_columns()

    logger.info("=" * 60)
    if success:
        logger.info("検証結果: OK - 列定義は正常です")
        return 0
    else:
        logger.error(f"検証結果: NG - {len(errors)}件のエラー")
        return 1


if __name__ == "__main__":
    sys.exit(main())
