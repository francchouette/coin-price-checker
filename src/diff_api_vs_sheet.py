"""
カラーミーAPI と 新カラーミー商品管理シート の差分チェック

主要フィールドを比較し、差分のある商品を一覧表示する。
update_date(API) vs BY列(同期日時) でどちらが新しいか推奨する。

使い方:
  python -m src.diff_api_vs_sheet                       # コンソール出力
  python -m src.diff_api_vs_sheet --csv diff.csv        # CSV出力
  python -m src.diff_api_vs_sheet --limit 50            # 件数制限
  python -m src.diff_api_vs_sheet --product-ids "192324420,192324431"  # 特定商品のみ
  python -m src.diff_api_vs_sheet --fields price,stock  # 比較項目を絞る
"""

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.colorme import ColorMeClient
from src.config import Config
from src.cm_sheet_columns import Col, get_cell

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# 比較対象フィールド: name -> (取得関数(row,product), 表示ラベル)
def _sheet_int(row, col):
    v = get_cell(row, col)
    if not v:
        return 0
    # カンマ区切り (41,200) や 通貨記号 (¥41,200) を除去
    cleaned = str(v).replace(",", "").replace("¥", "").strip()
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _normalize_str(v):
    if v is None:
        return ""
    return str(v).strip()


def _normalize_group_ids(v):
    """グループID文字列/リストを set(int) に正規化"""
    if v is None:
        return set()
    if isinstance(v, list):
        return set(int(g) for g in v if g)
    s = str(v).lstrip("'").strip()
    if not s:
        return set()
    return set(int(x.strip()) for x in s.split(",") if x.strip().isdigit())


# 表示方向: 'shop_to_api' = シート→API, 'api_to_shop' = API→シート
COMPARATORS = [
    {
        "label": "商品名",
        "sheet_col": Col.NAME,
        "sheet_getter": lambda r: _normalize_str(get_cell(r, Col.NAME)),
        "api_getter": lambda p: _normalize_str(p.get("name", "")),
    },
    {
        "label": "表示状態",
        "sheet_col": Col.DISPLAY_SETTING,
        "sheet_getter": lambda r: _normalize_str(get_cell(r, Col.DISPLAY_SETTING)),
        # APIのdisplay_state を日本語化
        "api_getter": lambda p: {
            "showing": "掲載する", "hidden": "掲載しない",
            "showing_for_members": "会員のみ表示", "sale_for_members": "会員のみ購入可",
        }.get(p.get("display_state", ""), p.get("display_state", "")),
    },
    {
        "label": "販売価格",
        "sheet_col": Col.SALES_PRICE,
        "sheet_getter": lambda r: _sheet_int(r, Col.SALES_PRICE),
        "api_getter": lambda p: int(p.get("sales_price") or 0),
    },
    {
        "label": "カテゴリID",
        "sheet_col": Col.CATEGORY_ID_BIG,
        "sheet_getter": lambda r: _sheet_int(r, Col.CATEGORY_ID_BIG),
        "api_getter": lambda p: int((p.get("category") or {}).get("id_big") or 0),
    },
    {
        "label": "グループID",
        "sheet_col": Col.GROUP_IDS,
        "sheet_getter": lambda r: _normalize_group_ids(get_cell(r, Col.GROUP_IDS)),
        "api_getter": lambda p: _normalize_group_ids(p.get("group_ids")),
    },
    {
        "label": "型番",
        "sheet_col": Col.MODEL_NUMBER,
        "sheet_getter": lambda r: _normalize_str(get_cell(r, Col.MODEL_NUMBER)),
        "api_getter": lambda p: _normalize_str(p.get("model_number", "")),
    },
    {
        "label": "在庫数",
        "sheet_col": Col.STOCKS,
        # 在庫連動(D列)=ON かつ U列(仕入れ先在庫)=Out of Stock かつ API=0 は意図された自動0設定なので一致扱い
        "sheet_getter": lambda r: (
            0 if (get_cell(r, Col.STOCK_SYNC).upper() == "ON"
                  and get_cell(r, Col.SUPPLIER_STOCK).strip() == "Out of Stock")
            else _sheet_int(r, Col.STOCKS)
        ),
        "api_getter": lambda p: int(p.get("stocks") or 0),
    },
    {
        "label": "個別送料",
        "sheet_col": Col.DELIVERY_CHARGE,
        "sheet_getter": lambda r: _sheet_int(r, Col.DELIVERY_CHARGE),
        "api_getter": lambda p: int(p.get("delivery_charge") or 0),
    },
    {
        "label": "商品説明文字数",
        "sheet_col": Col.EXPL,
        "sheet_getter": lambda r: len(_normalize_str(get_cell(r, Col.EXPL))),
        "api_getter": lambda p: len(_normalize_str(p.get("expl", ""))),
    },
    {
        "label": "簡易説明文字数",
        "sheet_col": Col.SIMPLE_EXPL,
        "sheet_getter": lambda r: len(_normalize_str(get_cell(r, Col.SIMPLE_EXPL))),
        "api_getter": lambda p: len(_normalize_str(p.get("simple_expl", ""))),
    },
    {
        "label": "画像数",
        "sheet_col": Col.MAIN_IMAGE,  # 代表
        # シート画像列構成: BE=メイン, BF=サムネイル(API images[0]に相当), BG-BN=画像URL1-8(API images[1-8])
        "sheet_getter": lambda r: sum(
            1 for c in [Col.MAIN_IMAGE, Col.THUMBNAIL, Col.IMAGE_URL_1, Col.IMAGE_URL_2, Col.IMAGE_URL_3,
                        Col.IMAGE_URL_4, Col.IMAGE_URL_5, Col.IMAGE_URL_6, Col.IMAGE_URL_7, Col.IMAGE_URL_8]
            if get_cell(r, c)
        ),
        # APIの images[] + image_url(メイン) を合算
        "api_getter": lambda p: (1 if p.get("image_url") else 0) + len(p.get("images") or []),
    },
]


def _parse_sheet_datetime(s: str) -> int:
    """シートのBY列(YYYY-MM-DD HH:MM:SS)→Unix timestamp。失敗時 0"""
    if not s:
        return 0
    try:
        return int(datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").timestamp())
    except (ValueError, AttributeError):
        return 0


def _ts_to_str(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _diff_value_format(label, sheet_v, api_v):
    """差分行の表示用"""
    if label == "グループID":
        return (
            "{" + ",".join(str(x) for x in sorted(sheet_v)) + "}",
            "{" + ",".join(str(x) for x in sorted(api_v)) + "}",
        )
    return (str(sheet_v), str(api_v))


def compare(limit=0, product_ids=None, fields_filter=None):
    """
    Returns: (results, summary)
    results: [{'id', 'name', 'diffs': [{'label', 'sheet', 'api', 'recommend'}], 'sheet_sync_ts', 'api_update_ts'}]
    summary: {'total_sheet', 'total_api', 'diff_count', 'recommend_shop_to_api', 'recommend_api_to_shop', 'recommend_unknown'}
    """
    logger.info("カラーミーAPI から全商品取得中...")
    cm_client = ColorMeClient()
    products = cm_client.get_all_products(limit=20000)
    api_by_id = {p["id"]: p for p in products}
    logger.info(f"API商品数: {len(api_by_id)}件")

    logger.info("スプレッドシート読込中...")
    sheet_client = SpreadsheetClient()
    if not sheet_client.connect():
        logger.error("シート接続失敗")
        sys.exit(1)
    sheet = sheet_client._spreadsheet.worksheet(Config.SHEET_COLORME_V2)
    rows = sheet.get_all_values()
    sheet_by_id = {}
    for r in rows[1:]:
        pid_s = get_cell(r, Col.PRODUCT_ID)
        if pid_s and pid_s.isdigit():
            sheet_by_id[int(pid_s)] = r
    logger.info(f"シート商品数: {len(sheet_by_id)}件")

    # 比較対象抽出
    if product_ids:
        common_ids = [pid for pid in product_ids if pid in sheet_by_id and pid in api_by_id]
    else:
        common_ids = sorted(set(sheet_by_id.keys()) & set(api_by_id.keys()))
    if limit:
        common_ids = common_ids[:limit]
    logger.info(f"比較対象: {len(common_ids)}件")

    # fields フィルタ
    comparators = COMPARATORS
    if fields_filter:
        comparators = [c for c in COMPARATORS if c["label"] in fields_filter]

    results = []
    summary = {
        "total_sheet": len(sheet_by_id),
        "total_api": len(api_by_id),
        "compared": len(common_ids),
        "diff_count": 0,
        "recommend_shop_to_api": 0,
        "recommend_api_to_shop": 0,
        "recommend_unknown": 0,
    }

    for pid in common_ids:
        product = api_by_id[pid]
        row = sheet_by_id[pid]
        sheet_sync_ts = _parse_sheet_datetime(get_cell(row, Col.SYNC_DATETIME))
        api_update_ts = int(product.get("update_date") or 0)

        diffs = []
        for comp in comparators:
            try:
                sv = comp["sheet_getter"](row)
                av = comp["api_getter"](product)
            except Exception as e:
                logger.debug(f"  比較エラー ID={pid} field={comp['label']}: {e}")
                continue
            if sv == av:
                continue
            # 推奨方向
            if sheet_sync_ts == 0 or api_update_ts == 0:
                recommend = "unknown"
            elif sheet_sync_ts > api_update_ts:
                recommend = "shop_to_api"
            elif sheet_sync_ts < api_update_ts:
                recommend = "api_to_shop"
            else:
                recommend = "unknown"
            diffs.append({
                "label": comp["label"],
                "sheet": sv,
                "api": av,
                "recommend": recommend,
            })

        if diffs:
            summary["diff_count"] += 1
            # 商品単位の推奨は最頻値（同数なら unknown）
            recs = [d["recommend"] for d in diffs if d["recommend"] != "unknown"]
            if not recs:
                pass
            elif recs.count("shop_to_api") > recs.count("api_to_shop"):
                summary["recommend_shop_to_api"] += 1
            elif recs.count("api_to_shop") > recs.count("shop_to_api"):
                summary["recommend_api_to_shop"] += 1
            else:
                summary["recommend_unknown"] += 1
            results.append({
                "id": pid,
                "name": product.get("name", "")[:50],
                "diffs": diffs,
                "sheet_sync_ts": sheet_sync_ts,
                "api_update_ts": api_update_ts,
            })

    return results, summary


def print_results(results, summary):
    print()
    print("=" * 80)
    print(f"比較結果: シート{summary['total_sheet']}件 / API{summary['total_api']}件 / 比較{summary['compared']}件")
    print(f"差分あり: {summary['diff_count']}件")
    print(f"  → シート→API推奨: {summary['recommend_shop_to_api']}件")
    print(f"  → API→シート推奨: {summary['recommend_api_to_shop']}件")
    print(f"  → 判定不能 (timestamp不明): {summary['recommend_unknown']}件")
    print("=" * 80)
    print()

    rec_label = {"shop_to_api": "シート→API", "api_to_shop": "API→シート", "unknown": "不明"}
    for r in results:
        print(f"ID {r['id']} {r['name']}")
        print(f"  シート同期: {_ts_to_str(r['sheet_sync_ts'])}  /  API更新: {_ts_to_str(r['api_update_ts'])}")
        for d in r["diffs"]:
            sv_str, av_str = _diff_value_format(d["label"], d["sheet"], d["api"])
            if len(sv_str) > 50:
                sv_str = sv_str[:47] + "..."
            if len(av_str) > 50:
                av_str = av_str[:47] + "..."
            print(f"  {d['label']:<10}  シート={sv_str}  API={av_str}  → {rec_label[d['recommend']]}")
        print()


def save_csv(results, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "product_id", "product_name", "field",
            "sheet_value", "api_value",
            "sheet_sync_at", "api_update_at", "recommendation",
        ])
        rec_label = {"shop_to_api": "sheet_to_api", "api_to_shop": "api_to_sheet", "unknown": "unknown"}
        for r in results:
            for d in r["diffs"]:
                sv_str, av_str = _diff_value_format(d["label"], d["sheet"], d["api"])
                w.writerow([
                    r["id"], r["name"], d["label"],
                    sv_str, av_str,
                    _ts_to_str(r["sheet_sync_ts"]), _ts_to_str(r["api_update_ts"]),
                    rec_label[d["recommend"]],
                ])
    print(f"CSV保存: {path}")


def main():
    parser = argparse.ArgumentParser(description="カラーミーAPI と シート の差分チェック")
    parser.add_argument("--limit", type=int, default=0, help="比較件数制限 (0=全件)")
    parser.add_argument("--product-ids", type=str, default="", help="特定商品IDのみ (カンマ区切り)")
    parser.add_argument("--fields", type=str, default="", help="比較項目を絞る (カンマ区切り、例: 販売価格,グループID)")
    parser.add_argument("--csv", type=str, default="", help="CSV出力パス (例: diff.csv)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    pids = None
    if args.product_ids:
        pids = set(int(x.strip()) for x in args.product_ids.split(",") if x.strip().isdigit())
        logger.info(f"product_ids 指定: {sorted(pids)}")

    fields = None
    if args.fields:
        fields = set(f.strip() for f in args.fields.split(",") if f.strip())
        logger.info(f"fields フィルタ: {sorted(fields)}")

    results, summary = compare(limit=args.limit, product_ids=pids, fields_filter=fields)
    print_results(results, summary)
    if args.csv:
        save_csv(results, args.csv)


if __name__ == "__main__":
    main()
