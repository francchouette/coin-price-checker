"""
カラーミー CSV 出力スクリプト

ベースとなるカラーミーCSVシート（カラーミーから事前にダウンロードしたフル情報）を読み込み、
新カラーミー商品管理シートの指定フィールドで該当列を上書きしてCSV出力する。

カラーミーのCSVインポートは空欄上書きしてしまうため、全列データを保持しつつ、
編集したい列だけをシートの値で上書きする方式。

使い方:
  # SEOのみ上書き（デフォルト）
  python -m src.export_colorme_csv --base-sheet product0414変更後

  # SEO + 価格を上書き
  python -m src.export_colorme_csv --base-sheet product0414変更後 --fields seo,price

  # ドライラン（差分のみ表示、CSV出力なし）
  python -m src.export_colorme_csv --base-sheet product0414変更後 --dry-run

利用可能なフィールドグループ:
  seo          : ページタイトル・メタディスクリプション・メタキーワード
  price        : 販売価格・定価・会員価格・原価
  name         : 商品名
  description  : 商品説明・簡易説明
  model        : 型番
  stock        : 在庫数
  shipping     : 個別送料
"""

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

from .spreadsheet import SpreadsheetClient
from .config import Config
from .cm_sheet_columns import Col, get_cell

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# カラーミーCSV の列位置（0-indexed）
# product0414変更後 シートの構造に基づく
CSV_COLS = {
    'product_id':      0,   # A: 商品ID
    'category_big':    1,   # B: 大カテゴリー
    'category_small':  2,   # C: 小カテゴリー
    'model_number':    3,   # D: 型番
    'name':            4,   # E: 商品名
    'sales_price':    16,   # Q: 販売価格
    'members_price':  17,   # R: 会員価格
    'regular_price':  18,   # S: 定価
    'cost':           19,   # T: 原価
    'stocks':         20,   # U: 在庫数
    'simple_expl':    33,   # AH: 簡易説明
    'expl':           34,   # AI: 商品説明
    'title':          52,   # BA: タイトル
    'keywords':       53,   # BB: キーワード
    'page_overview':  54,   # BC: ページ概要
    'delivery_charge': 55,  # BD: 個別送料
}

# フィールドグループ定義: group_name -> list of (Column, csv_col_key, 日本語ラベル)
FIELD_GROUPS = {
    'seo': [
        (Col.PAGE_TITLE,    'title',         'ページタイトル → BA:タイトル'),
        (Col.META_DESC,     'page_overview', 'メタディス → BC:ページ概要'),
        (Col.META_KEYWORDS, 'keywords',      'メタキーワード → BB:キーワード'),
    ],
    'price': [
        (Col.SALES_PRICE,   'sales_price',   '販売価格 → Q:販売価格'),
        (Col.REGULAR_PRICE, 'regular_price', '定価 → S:定価'),
        (Col.MEMBERS_PRICE, 'members_price', '会員価格 → R:会員価格'),
        (Col.COST,          'cost',          '原価 → T:原価'),
    ],
    'name': [
        (Col.NAME, 'name', '商品名 → E:商品名'),
    ],
    'description': [
        (Col.EXPL,        'expl',        '商品説明 → AI:商品説明'),
        (Col.SIMPLE_EXPL, 'simple_expl', '簡易説明 → AH:簡易説明'),
    ],
    'model': [
        (Col.MODEL_NUMBER, 'model_number', '型番 → D:型番'),
    ],
    'stock': [
        (Col.STOCKS, 'stocks', '在庫数 → U:在庫数'),
    ],
    'shipping': [
        (Col.DELIVERY_CHARGE, 'delivery_charge', '個別送料 → BD:個別送料'),
    ],
}


def main():
    parser = argparse.ArgumentParser(
        description="カラーミーCSV出力（シートでの編集内容をベースCSVに反映）"
    )
    parser.add_argument("--base-sheet", required=True,
                        help="ベースとするカラーミーCSVシート名（必須、例: product0414変更後）")
    parser.add_argument("--fields", default="seo",
                        help="上書きするフィールドグループ（カンマ区切り、デフォルト: seo）")
    parser.add_argument("--dry-run", action="store_true",
                        help="CSV出力せず、差分内容のみ表示")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # フィールドグループをパース
    selected_groups = [g.strip() for g in args.fields.split(",") if g.strip()]
    for g in selected_groups:
        if g not in FIELD_GROUPS:
            logger.error(f"未知のフィールドグループ: {g}")
            logger.info(f"利用可能: {', '.join(FIELD_GROUPS.keys())}")
            sys.exit(1)

    # 上書き対象列を集計
    overrides = []  # [(Col, csv_col_key, label), ...]
    for g in selected_groups:
        overrides.extend(FIELD_GROUPS[g])

    mode = "DRY-RUN（CSV出力なし）" if args.dry_run else "本実行"
    logger.info(f"=== カラーミーCSV出力 開始 [{mode}] ===")
    logger.info(f"ベースシート: {args.base_sheet}")
    logger.info(f"フィールドグループ: {', '.join(selected_groups)}")
    logger.info(f"上書き対象:")
    for _, _, label in overrides:
        logger.info(f"  - {label}")

    # スプレッドシート接続
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシート接続失敗")
        sys.exit(1)

    # ベースCSVシート読み込み
    try:
        base_sheet = client._spreadsheet.worksheet(args.base_sheet)
    except Exception:
        logger.error(f"シート「{args.base_sheet}」が見つかりません")
        sys.exit(1)

    logger.info(f"「{args.base_sheet}」を読み込み中...")
    base_data = base_sheet.get_all_values()
    if len(base_data) < 2:
        logger.error("ベースシートにデータがありません")
        sys.exit(1)
    base_header = base_data[0]
    base_rows = [list(r) for r in base_data[1:]]  # 可変にするためリスト化
    logger.info(f"ベースCSV: {len(base_rows)}行（列数: {len(base_header)}）")

    # ベースCSVの重複商品IDを検出
    csv_id_rows = {}  # pid -> [row_num, ...]
    for i, row in enumerate(base_rows, start=2):
        pid = row[0].strip() if row else ''
        if pid and pid.isdigit():
            csv_id_rows.setdefault(pid, []).append(i)
    csv_duplicates = {pid: rows for pid, rows in csv_id_rows.items() if len(rows) > 1}

    # 新カラーミー商品管理シート読み込み
    cm_sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    cm_data = cm_sheet.get_all_values()

    # 商品ID → シート行 のマップを構築（重複は後勝ち）
    cm_map = {}
    cm_id_rows = {}  # pid -> [row_num, ...]
    for i, row in enumerate(cm_data[1:], start=2):
        pid = get_cell(row, Col.PRODUCT_ID)
        if pid and pid.isdigit():
            cm_map[pid] = row
            cm_id_rows.setdefault(pid, []).append(i)
    cm_duplicates = {pid: rows for pid, rows in cm_id_rows.items() if len(rows) > 1}
    logger.info(f"新カラーミー商品管理: {len(cm_map)}件（商品ID付き）")

    # 重複IDを警告
    if csv_duplicates:
        logger.warning("")
        logger.warning(f"⚠️ ベースCSVに重複商品IDを検出: {len(csv_duplicates)}件")
        for pid, rows in list(csv_duplicates.items())[:10]:
            logger.warning(f"    商品ID: {pid} → 行 {rows}")
        if len(csv_duplicates) > 10:
            logger.warning(f"    ...他 {len(csv_duplicates) - 10}件")
        logger.warning("  （CSV側の重複は後ろの行が採用されます）")

    if cm_duplicates:
        logger.warning("")
        logger.warning(f"⚠️ 新カラーミー商品管理シートに重複商品IDを検出: {len(cm_duplicates)}件")
        for pid, rows in list(cm_duplicates.items())[:10]:
            logger.warning(f"    商品ID: {pid} → 行 {rows}")
        if len(cm_duplicates) > 10:
            logger.warning(f"    ...他 {len(cm_duplicates) - 10}件")
        logger.warning("  （シート側の重複は下の行の値が採用されます）")

    # 上書き処理
    changed_rows = 0
    changed_cells = 0
    skipped_empty = 0
    csv_only_ids = []     # CSVにあってシートにない（そのままCSVを維持）
    change_samples = []   # 変更内容サンプル

    for row in base_rows:
        pid = row[0].strip() if row else ''
        if not pid or not pid.isdigit():
            continue

        if pid not in cm_map:
            csv_only_ids.append(pid)
            continue

        cm_row = cm_map[pid]
        row_changed = False

        for sheet_col, csv_col_key, label in overrides:
            csv_col_idx = CSV_COLS[csv_col_key]
            sheet_val = get_cell(cm_row, sheet_col)
            csv_val = row[csv_col_idx] if len(row) > csv_col_idx else ''

            # --- 個別送料(BD)の特別処理 ---
            # カラーミーでは「0」は送料無料扱いになり送料無料ラインが無効化される。
            # シート上では「0」= 「個別送料なし（デフォルト送料適用）」の意味マーカーとして使用。
            # CSVへは以下の変換ルールで出力:
            #   シート値が「0」「0.0」「空欄」 → CSV BD を明示的に空欄で出力（カラーミー側をクリア）
            #   シート値が正の数             → CSV BD にその値（個別送料設定）
            if csv_col_key == 'delivery_charge':
                normalized = sheet_val.strip()
                if normalized in ('', '0', '0.0'):
                    new_val = ''  # 明示的に空欄（カラーミー管理画面の個別送料をクリア）
                else:
                    new_val = normalized
                if new_val == csv_val:
                    if not new_val:
                        skipped_empty += 1
                    continue
                while len(row) <= csv_col_idx:
                    row.append('')
                row[csv_col_idx] = new_val
                row_changed = True
                changed_cells += 1
                if len(change_samples) < 10:
                    change_samples.append({
                        'product_id': pid,
                        'label': label,
                        'old': (csv_val[:60] if csv_val else '(空欄)'),
                        'new': (new_val[:60] if new_val else '(空欄=カラーミー側クリア)'),
                    })
                continue

            # --- 通常カラムの処理 ---
            # 空欄の場合はスキップ（空欄で上書きしない）
            if not sheet_val:
                skipped_empty += 1
                continue

            # スプレッドシート数式エラー値はスキップ
            if sheet_val in ("#N/A", "#REF!", "#ERROR!", "#VALUE!", "#NAME?", "#NULL!", "#DIV/0!"):
                skipped_empty += 1
                continue

            # 変更がない場合はスキップ
            if sheet_val == csv_val:
                continue

            # CSV行の長さが足りなければ拡張
            while len(row) <= csv_col_idx:
                row.append('')
            row[csv_col_idx] = sheet_val
            row_changed = True
            changed_cells += 1

            # サンプル保存（最初の10件）
            if len(change_samples) < 10:
                change_samples.append({
                    'product_id': pid,
                    'label': label,
                    'old': csv_val[:60],
                    'new': sheet_val[:60],
                })

        if row_changed:
            changed_rows += 1

    # シート側のみに存在する商品（警告）
    csv_ids = {row[0].strip() for row in base_rows if row and row[0].strip().isdigit()}
    sheet_only = sorted(set(cm_map.keys()) - csv_ids)

    # 集計結果
    logger.info("")
    logger.info("=== 集計 ===")
    logger.info(f"  ベースCSV総行数: {len(base_rows)}")
    logger.info(f"  上書き適用行数: {changed_rows}")
    logger.info(f"  上書きセル数: {changed_cells}")
    logger.info(f"  シート空欄スキップ（CSV既存値保持）: {skipped_empty}セル")
    logger.info(f"  CSVのみに存在（ベース値を維持）: {len(csv_only_ids)}件")
    logger.info(f"  シートのみに存在（CSVには含まれず）: {len(sheet_only)}件")

    if sheet_only:
        logger.warning("")
        logger.warning("⚠️ 以下の商品はシート側のみに存在しCSVには含まれません（カラーミー未登録）:")
        for sid in sheet_only[:10]:
            logger.warning(f"    商品ID: {sid}")
        if len(sheet_only) > 10:
            logger.warning(f"    ...他 {len(sheet_only) - 10}件")

    if change_samples:
        logger.info("")
        logger.info("=== 変更サンプル（先頭10件）===")
        for s in change_samples:
            logger.info(f"  [ID:{s['product_id']}] {s['label']}")
            logger.info(f"    旧: {s['old']}")
            logger.info(f"    新: {s['new']}")

    if args.dry_run:
        logger.info("")
        logger.info("[DRY-RUN] CSVファイル出力をスキップしました")
        return

    if changed_rows == 0:
        logger.warning("")
        logger.warning("変更なし。CSV出力をスキップします。")
        return

    # CSVファイル出力
    export_dir = Path(__file__).resolve().parent.parent / 'exports'
    export_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = export_dir / f'colorme_import_{timestamp}.csv'

    logger.info("")
    logger.info(f"CSVファイルを出力中: {output_path}")
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(base_header)
        for row in base_rows:
            writer.writerow(row)

    logger.info("")
    logger.info(f"=== 出力完了 ===")
    logger.info(f"ファイル: {output_path}")
    logger.info(f"行数: {len(base_rows)}（ヘッダー除く）")
    logger.info("")
    logger.info("次のステップ:")
    logger.info("  1. カラーミー管理画面にログイン")
    logger.info("  2. 「商品一括登録」メニューから上記CSVファイルをアップロード")
    logger.info("  3. カラーミー側で変更内容を確認")


if __name__ == "__main__":
    main()
