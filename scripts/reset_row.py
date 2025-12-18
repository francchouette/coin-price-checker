#!/usr/bin/env python3
"""
スプレッドシートの行をリセットするスクリプト

手入力項目（D列:取得元URL、AC列:同期モード、T列:販売価格、O列:掛け率）を残して
自動入力された項目をクリアする。
"""

import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.config import Config


# 手入力項目として残す列（0-indexed）
MANUAL_COLUMNS = {
    3,   # D: 取得元URL
    14,  # O: 掛け率
    19,  # T: 販売価格
    28,  # AC: 同期モード
}

# 自動入力される列（クリア対象）
AUTO_FILLED_COLUMNS = [
    'A',   # 商品ID
    'B',   # 商品名
    'C',   # カラーミー商品URL
    'M',   # 取得元価格
    'N',   # 取得通貨
    'P',   # 外部-為替レート
    'Q',   # 外部-本体計算価格
    'Y',   # 最終更新
    'AB',  # 在庫状況
    'AD',  # 型番
    'AE',  # カテゴリーID（大）
    'AF',  # カテゴリーID（小）
    'AG',  # グループID
    'AP',  # 商品説明
    'AQ',  # 簡易説明
    # AR〜BA: 画像URL1〜10
    'AR', 'AS', 'AT', 'AU', 'AV', 'AW', 'AX', 'AY', 'AZ', 'BA',
]


def reset_row(row_num: int, dry_run: bool = False) -> bool:
    """
    指定行の自動入力項目をクリア

    Args:
        row_num: 行番号（1-indexed、ヘッダーは1行目）
        dry_run: Trueの場合は実際に更新せずにプレビューのみ

    Returns:
        bool: 成功時True
    """
    print(f"行 {row_num} をリセット中...")

    # スプレッドシートに接続
    client = SpreadsheetClient()
    if not client.connect():
        print("スプレッドシートへの接続に失敗しました")
        return False

    sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME)

    # 現在の行データを取得
    row_data = sheet.row_values(row_num)

    # D列の値を確認
    source_url = row_data[3] if len(row_data) > 3 else ""
    if not source_url:
        print(f"警告: D列（取得元URL）が空です")
    else:
        print(f"取得元URL: {source_url}")

    # 同期モードを確認
    sync_mode = row_data[28] if len(row_data) > 28 else ""
    print(f"同期モード: {sync_mode or '(空)'}")

    if dry_run:
        print("\n[ドライラン] 以下の列をクリアします:")
        for col in AUTO_FILLED_COLUMNS:
            col_idx = column_letter_to_index(col)
            current_value = row_data[col_idx] if len(row_data) > col_idx else ""
            if current_value:
                print(f"  {col}列: '{current_value[:50]}{'...' if len(current_value) > 50 else ''}'")
        return True

    # クリア用のデータを準備
    updates = []
    for col in AUTO_FILLED_COLUMNS:
        updates.append({
            'range': f'{col}{row_num}',
            'values': [['']]
        })

    # バッチ更新
    sheet.batch_update(updates, value_input_option='RAW')

    print(f"✅ 行 {row_num} のリセット完了")
    print(f"   クリアした列: {len(AUTO_FILLED_COLUMNS)}列")
    print(f"   残した列: D(取得元URL), O(掛け率), T(販売価格), AC(同期モード)")

    return True


def column_letter_to_index(col: str) -> int:
    """列文字をインデックスに変換（A=0, B=1, ..., AA=26, ...）"""
    result = 0
    for char in col:
        result = result * 26 + (ord(char.upper()) - ord('A') + 1)
    return result - 1


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="スプレッドシートの行をリセット（手入力項目は残す）"
    )
    parser.add_argument(
        "row_num",
        type=int,
        help="リセットする行番号（ヘッダーは1行目）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際に更新せずにプレビューのみ表示"
    )

    args = parser.parse_args()

    if args.row_num < 2:
        print("エラー: 行番号は2以上を指定してください（1行目はヘッダー）")
        sys.exit(1)

    success = reset_row(args.row_num, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
