#!/usr/bin/env python3
"""
「使い方」シートをスプレッドシートに追加するスクリプト
"""

import sys
sys.path.insert(0, "/Users/abehiroshi/cursor/coin-price-check-script")

from src.spreadsheet import SpreadsheetClient


def create_usage_sheet():
    """使い方シートを作成する"""

    client = SpreadsheetClient()
    if not client.connect():
        print("スプレッドシートに接続できませんでした")
        return False

    spreadsheet = client._spreadsheet

    # 既存のシートをチェック
    sheet_names = [s.title for s in spreadsheet.worksheets()]
    if "使い方" in sheet_names:
        print("「使い方」シートは既に存在します。削除して再作成します。")
        spreadsheet.del_worksheet(spreadsheet.worksheet("使い方"))

    # 新しいシートを作成（最初に配置）
    usage_sheet = spreadsheet.add_worksheet(title="使い方", rows=100, cols=10, index=0)

    # 使い方の内容を定義
    content = [
        ["カラーミー商品管理 同期機能 使い方ガイド"],
        [""],
        ["■ 概要"],
        ["この機能では、スプレッドシートとカラーミーショップ間で商品情報を同期できます。"],
        [""],
        ["■ 同期モード（AC列に入力）"],
        ["値", "動作"],
        ["ドラフト作成", "取得元URLから商品情報をスクレイピングしてシートに反映（カラーミー登録なし）"],
        ["取得のみ", "カラーミーから商品情報を取得してシートに反映します"],
        ["更新", "シートの内容でカラーミーの商品情報を更新します"],
        ["新規登録", "新しい商品としてカラーミーに登録します（商品ID空欄時のみ）"],
        ["なし / 空欄", "この商品は同期処理の対象外になります"],
        [""],
        ["【重要】値は完全一致で判定されます"],
        ["・「ドラフト作成」「取得のみ」「更新」「新規登録」のいずれかを正確に入力してください"],
        ["・スペースや余分な文字が含まれると認識されません"],
        ["・「取得のみ / 更新」のように複数の値を1セルに入れないでください"],
        [""],
        ["■ コマンド実行方法"],
        [""],
        ["【テスト実行（推奨）】"],
        ["COLORME_DRY_RUN=true python -m src.main --sync-colorme"],
        ["※実際の更新は行われず、処理内容のみ確認できます"],
        [""],
        ["【本番実行】"],
        ["COLORME_DRY_RUN=false python -m src.main --sync-colorme"],
        ["※実際にカラーミーの商品情報が更新されます"],
        [""],
        ["■ 新規登録の必須項目"],
        ["列", "項目名", "備考"],
        ["B", "商品名", "空欄不可"],
        ["D", "販売価格", "0以上の整数"],
        ["AE", "カテゴリーID", "カラーミー管理画面で確認"],
        [""],
        ["■ シート列の説明（AC〜BC列）"],
        ["列", "項目名", "説明"],
        ["AC", "同期モード", "ドラフト作成 / 取得のみ / 更新 / 新規登録 / なし"],
        ["AD", "型番", "商品の型番・モデル番号"],
        ["AE", "カテゴリーID", "大カテゴリーのID（新規登録時は必須）"],
        ["AF", "サブカテゴリーID", "小カテゴリーのID"],
        ["AG", "グループID", "グループID（カンマ区切りで複数指定可）"],
        ["AH", "定価", "定価（メーカー希望小売価格）"],
        ["AI", "会員価格", "会員向け特別価格"],
        ["AJ", "個別送料", "商品個別の送料設定"],
        ["AK", "在庫管理", "する / しない"],
        ["AL", "売切れ表示", "表示 / 非表示"],
        ["AM", "適正在庫数", "残り僅か表示の閾値"],
        ["AN", "最小購入数", "一度に購入できる最小数量"],
        ["AO", "最大購入数", "一度に購入できる最大数量（0=無制限）"],
        ["AP", "商品説明", "商品の詳細説明（HTML可）"],
        ["AQ", "簡易説明", "商品の短い説明"],
        ["AR〜BA", "画像URL1〜10", "商品画像のURL（最大10枚）"],
        ["BB", "同期ステータス", "処理結果（成功 / エラー: xxx）"],
        ["BC", "同期日時", "最後に同期した日時"],
        [""],
        ["■ 画像のアップロード"],
        ["・AR列（画像URL1）にメイン画像のURLを入力すると自動でアップロードされます"],
        ["・AS〜BA列（画像URL2〜10）に追加画像のURLを入力できます"],
        ["・対応形式: JPEG, PNG, GIF"],
        [""],
        ["■ エラー確認方法"],
        ["BB列（同期ステータス）に処理結果が記録されます："],
        ["・成功 → 処理が正常に完了"],
        ["・エラー: xxx → エラーが発生（xxxにエラー内容）"],
        ["・スキップ: xxx → 処理がスキップされた理由"],
        [""],
        ["■ よくあるエラー"],
        ["エラー", "原因", "対処法"],
        ["商品名が空です", "B列が空欄", "商品名を入力"],
        ["販売価格が無効です", "D列が空欄または不正", "有効な価格を入力"],
        ["カテゴリーIDが必須です", "AD列が空欄（新規登録時）", "カテゴリーIDを入力"],
        ["商品が見つかりません", "商品IDが無効", "正しい商品IDを確認"],
        ["APIエラー: 401", "認証エラー", "アクセストークンを確認"],
        ["APIエラー: 429", "レート制限", "しばらく待ってから再実行"],
        [""],
        ["■ 注意事項"],
        ["・初めて実行する際は必ずドライランモード（COLORME_DRY_RUN=true）で確認してください"],
        ["・カラーミーAPIには 120リクエスト/分 の制限があります"],
        ["・「取得のみ」モードではシートの内容が上書きされます"],
        ["・重要な編集内容がある場合は事前にバックアップしてください"],
        [""],
        ["■ 必要な環境変数"],
        ["変数名", "説明"],
        ["SPREADSHEET_ID", "Google スプレッドシートのID"],
        ["COLORME_ACCESS_TOKEN", "カラーミーのアクセストークン"],
        ["COLORME_DRY_RUN", "true=テスト実行 / false=本番実行"],
    ]

    # 一括で書き込み
    usage_sheet.update(values=content, range_name="A1")

    # 書式設定
    # タイトル行を太字に
    usage_sheet.format("A1", {"textFormat": {"bold": True, "fontSize": 14}})

    # セクションヘッダーを太字に
    section_rows = [3, 6, 13, 23, 29, 51, 56, 62, 70, 77]
    for row in section_rows:
        usage_sheet.format(f"A{row}", {"textFormat": {"bold": True}})

    # 表のヘッダー行を太字に
    table_header_rows = [7, 24, 30, 63, 78]
    for row in table_header_rows:
        usage_sheet.format(f"A{row}:C{row}", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
        })

    # 列幅を調整（Sheets APIを直接使用）
    sheet_id = usage_sheet.id
    requests = [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 1
                },
                "properties": {"pixelSize": 150},
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 1,
                    "endIndex": 2
                },
                "properties": {"pixelSize": 300},
                "fields": "pixelSize"
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 2,
                    "endIndex": 3
                },
                "properties": {"pixelSize": 400},
                "fields": "pixelSize"
            }
        }
    ]
    spreadsheet.batch_update({"requests": requests})

    print("「使い方」シートを作成しました！")
    return True


if __name__ == "__main__":
    create_usage_sheet()
