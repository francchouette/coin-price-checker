#!/usr/bin/env python3
"""
ベンチマーク確認スクリプト - 指定行の価格チェーン診断

シートの価格計算チェーン（N→S→T→AB→AE）とカラーミーAPIの
現在価格を比較し、不一致を検出する。

使い方:
    python scripts/check_row3.py              # 3行目をチェック（テキスト出力）
    python scripts/check_row3.py --row 5      # 5行目をチェック
    python scripts/check_row3.py --json       # JSON出力（ダッシュボード連携用）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spreadsheet import SpreadsheetClient
from src.colorme import ColorMeClient
from src.config import Config
from src.cm_sheet_columns import Col, get_cell, get_cell_int


def check_row(row_num: int) -> dict:
    """指定行の診断データを取得してdictで返す"""
    result = {"row_num": row_num, "error": None}

    try:
        client = SpreadsheetClient()
        if not client.connect():
            result["error"] = "スプレッドシート接続失敗"
            return result

        sheet = client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
        all_data = sheet.get_all_values()

        if row_num < 2 or row_num > len(all_data):
            result["error"] = f"行番号 {row_num} は範囲外です（2-{len(all_data)}）"
            return result

        row = all_data[row_num - 1]  # 0-indexed

        # 操作列
        result["operation"] = {
            "sync_mode": get_cell(row, Col.SYNC_MODE),
            "display_setting": get_cell(row, Col.DISPLAY_SETTING),
            "price_update": get_cell(row, Col.PRICE_UPDATE),
            "stock_sync": get_cell(row, Col.STOCK_SYNC),
            "display_sync": get_cell(row, Col.DISPLAY_SYNC),
            "sync_status": get_cell(row, Col.SYNC_STATUS),
        }

        # 識別情報
        product_id = get_cell_int(row, Col.PRODUCT_ID)
        result["name"] = get_cell(row, Col.NAME)
        result["product_id"] = product_id
        result["colorme_url"] = get_cell(row, Col.COLORME_URL)

        # 仕入れ先情報
        result["supplier"] = {
            "url": get_cell(row, Col.SUPPLIER_URL),
            "name": get_cell(row, Col.SUPPLIER_NAME),
            "stock": get_cell(row, Col.SUPPLIER_STOCK),
            "price": get_cell(row, Col.SUPPLIER_PRICE),
            "prev_price": get_cell(row, Col.PREV_PRICE),
            "change_rate": get_cell(row, Col.PRICE_CHANGE_RATE),
            "currency": get_cell(row, Col.CURRENCY),
        }

        # 価格チェーン
        sales_price = get_cell_int(row, Col.SALES_PRICE)
        proper_price = get_cell_int(row, Col.PROPER_PRICE)
        final_price = sales_price if sales_price > 0 else proper_price
        result["price_chain"] = {
            "exchange_type": get_cell(row, Col.EXCHANGE_TYPE),
            "exchange_rate": get_cell(row, Col.EXCHANGE_RATE),
            "purchase_jpy": get_cell(row, Col.PURCHASE_PRICE_JPY),
            "margin_rate": get_cell(row, Col.MARGIN_RATE),
            "total_cost": get_cell(row, Col.TOTAL_COST),
            "proper_price_raw": get_cell(row, Col.PROPER_PRICE),
            "sales_price_raw": get_cell(row, Col.SALES_PRICE),
            "proper_price": proper_price,
            "sales_price": sales_price,
            "final_price": final_price,
            "price_source": "AE列" if sales_price > 0 else ("AB列" if proper_price > 0 else "なし"),
        }

        # 在庫
        result["stocks"] = get_cell_int(row, Col.STOCKS)

        # 数式確認
        formulas_data = sheet.get(f'A{row_num}:BY{row_num}', value_render_option='FORMULA')
        formulas = {}
        if formulas_data and formulas_data[0]:
            frow = formulas_data[0]
            for col_def in [Col.PURCHASE_PRICE_JPY, Col.PROPER_PRICE, Col.SALES_PRICE]:
                if len(frow) > col_def.index:
                    val = frow[col_def.index]
                    is_formula = isinstance(val, str) and val.startswith("=")
                    formulas[f"{col_def.letter}列({col_def.name})"] = {
                        "value": str(val),
                        "is_formula": is_formula,
                    }
        result["formulas"] = formulas

        # カラーミーAPI
        result["colorme"] = None
        result["match"] = None
        result["diff"] = None

        if product_id > 0:
            try:
                colorme = ColorMeClient()
                cm = colorme.get_product(product_id)
                if cm:
                    cm_price = cm.get("sales_price") or cm.get("price") or 0
                    result["colorme"] = {
                        "sales_price": cm.get("sales_price", 0),
                        "price": cm.get("price", 0),
                        "stocks": cm.get("stocks", 0),
                        "display_state": cm.get("display_state", ""),
                    }
                    result["match"] = (final_price == cm_price)
                    result["diff"] = final_price - cm_price
                else:
                    result["colorme"] = {"error": "API取得失敗（商品が削除された可能性）"}
            except Exception as e:
                result["colorme"] = {"error": str(e)}

    except Exception as e:
        result["error"] = str(e)

    return result


def print_text(data: dict):
    """テキスト形式で出力"""
    if data.get("error"):
        print(f"エラー: {data['error']}")
        return

    row_num = data["row_num"]
    print("=" * 60)
    print(f"{row_num}行目のベンチマーク確認")
    print("=" * 60)

    op = data["operation"]
    print(f"\n--- 操作列 ---")
    print(f"  A列（同期モード）: '{op['sync_mode']}'")
    print(f"  B列（掲載設定）:  '{op['display_setting']}'")
    print(f"  C列（価格更新）:  '{op['price_update']}'")
    print(f"  D列（在庫連動）:  '{op['stock_sync']}'")
    print(f"  E列（表示連動）:  '{op['display_sync']}'")
    print(f"  F列（同期ステータス）: '{op['sync_status']}'")

    print(f"\n--- 識別情報 ---")
    print(f"  G列（商品ID）:    {data['product_id']}")
    print(f"  H列（商品名）:    '{data['name']}'")

    sup = data["supplier"]
    print(f"\n--- 仕入れ先情報 ---")
    print(f"  J列（仕入先URL）: '{sup['url']}'")
    print(f"  M列（在庫状況）:  '{sup['stock']}'")
    print(f"  N列（仕入価格）:  '{sup['price']}'")
    print(f"  O列（前回価格）:  '{sup['prev_price']}'")
    print(f"  Q列（通貨）:      '{sup['currency']}'")

    pc = data["price_chain"]
    print(f"\n--- 価格チェーン ---")
    print(f"  R列（為替種類）:  '{pc['exchange_type']}'")
    print(f"  S列（為替レート）: '{pc['exchange_rate']}'")
    print(f"  T列（仕入額JPY）: '{pc['purchase_jpy']}'")
    print(f"  W列（マージン率）: '{pc['margin_rate']}'")
    print(f"  AB列（適正価格）:  '{pc['proper_price_raw']}' → {pc['proper_price']:,}円")
    print(f"  AE列（販売価格）:  '{pc['sales_price_raw']}' → {pc['sales_price']:,}円")
    print(f"  最終価格: {pc['final_price']:,}円 ({pc['price_source']})")

    if data.get("formulas"):
        print(f"\n--- 数式確認 ---")
        for key, val in data["formulas"].items():
            tag = "数式" if val["is_formula"] else "値"
            print(f"  {key}: [{tag}] {val['value']}")

    cm = data.get("colorme")
    if cm and "error" not in cm:
        print(f"\n--- カラーミー現在値 ---")
        print(f"  販売価格: {cm['sales_price']:,}円")
        print(f"  定価:     {cm['price']:,}円")
        print(f"  在庫数:   {cm['stocks']}")
        print(f"  表示状態: {cm['display_state']}")

        print(f"\n--- 比較結果 ---")
        if data["match"]:
            print(f"  OK 価格一致: {pc['final_price']:,}円")
        else:
            print(f"  NG 価格不一致: シート {pc['final_price']:,}円 / カラーミー {cm['sales_price']:,}円 (差: {data['diff']:+,}円)")
    elif cm and "error" in cm:
        print(f"\n  カラーミーAPI: {cm['error']}")


def main():
    parser = argparse.ArgumentParser(description="ベンチマーク確認")
    parser.add_argument("--row", type=int, default=3, help="確認する行番号（デフォルト: 3）")
    parser.add_argument("--json", action="store_true", help="JSON出力")
    args = parser.parse_args()

    data = check_row(args.row)

    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print_text(data)


if __name__ == "__main__":
    main()
