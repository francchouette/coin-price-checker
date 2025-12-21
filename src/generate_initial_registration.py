"""
初期登録一覧生成スクリプト

商品仕入れ先一覧シートから採用フラグがTRUEの商品を取得し、
新カラーミー商品初期登録一覧シートに追加する。
"""

import logging
import sys
from datetime import datetime

from .spreadsheet import SpreadsheetClient
from .config import Config

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def generate_initial_data(supplier: dict) -> dict:
    """
    仕入れ先商品データから初期登録データを生成する

    Args:
        supplier: 仕入れ先商品データ

    Returns:
        dict: 初期登録シート用のデータ
    """
    # 現在日時
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # カラーミー商品名を生成（仕入れ先商品名をベースに）
    product_name = supplier.get("supplier_product_name", "")

    # 初期データを構築
    data = {
        # A. 識別情報（A-C列）- カラーミー商品IDは登録後に設定
        "カラーミー商品ID": "",
        "商品名": product_name,
        "カラーミー商品URL": "",

        # B. 仕入れ先情報（D-K列）
        "仕入れ先商品URL": supplier.get("supplier_product_url", ""),
        "仕入れ先商品名": supplier.get("supplier_product_name", ""),
        "仕入れ先サイト": supplier.get("supplier_site", ""),
        "最上位カテゴリ": supplier.get("top_category", ""),
        "親カテゴリ": supplier.get("parent_category", ""),
        "子カテゴリ": supplier.get("child_category", ""),
        "仕入れ先価格（現地通貨）": supplier.get("current_price", ""),
        "取引通貨": supplier.get("currency", "SGD"),

        # C. 価格計算（L-X列）
        "為替種類": supplier.get("exchange_type", "クレカ"),
        "為替レート": supplier.get("exchange_rate", ""),
        "仕入れ額(日本円)": "",  # 数式で計算
        "枚数": 1,
        "仕入れ合計": "",  # 数式で計算
        "設定マージン率": 0.2,  # デフォルト20%
        "設定マージン額": "",  # 数式で計算
        "送料": 0,
        "手数料": 0,
        "合計原価": "",  # 数式で計算
        "適正価格": "",  # 数式で計算
        "粗利額": "",  # 数式で計算
        "粗利率": "",  # 数式で計算

        # D. カラーミー価格情報（Y-AD列）
        "販売価格": "",  # 適正価格から設定
        "定価": "",
        "会員価格": "",
        "原価": "",
        "消費税込販売価格": "",  # 数式で計算
        "消費税額": "",  # 数式で計算

        # E. カテゴリー・グループ（AE-AI列）
        "大カテゴリーID": "",
        "小カテゴリーID": "",
        "グループID": "",
        "型番": "",
        "掲載設定": "showing",

        # F. 在庫管理（AJ-AP列）
        "在庫数": 10,
        "在庫管理": "する",
        "残りわずか数": 3,
        "売切れ表示": "表示",
        "最小購入数": 1,
        "最大購入数": 0,  # 無制限
        "単位": "",

        # G. 送料・配送（AQ-AT列）
        "個別送料": 0,
        "クール便料金": 0,
        "重量(g)": 0,
        "配送不要": False,

        # H. 商品説明（AU-AX列）
        "商品説明": "",
        "簡易説明": "",
        "スマホ説明": "",
        "備考": supplier.get("note", ""),

        # I. 画像（AY-BH列）
        "メイン画像URL": "",
        "サムネイルURL": "",
        "画像URL1": "",
        "画像URL2": "",
        "画像URL3": "",
        "画像URL4": "",
        "画像URL5": "",
        "画像URL6": "",
        "画像URL7": "",
        "画像URL8": "",

        # J. SEO項目（BI-BK列）
        "ページタイトル": "",
        "メタディスクリプション": "",
        "メタキーワード": "",

        # K. フラグ・設定（BL-BP列）
        "軽減税率対象": False,
        "デジタルコンテンツ": False,
        "定期購入": False,
        "表示順": 0,
        "利用不可決済": "",

        # L. 掲載期間（BQ-BR列）
        "掲載開始日時": "",
        "掲載終了日時": "",

        # M. 更新制御（BS-BX列）
        "価格更新ON/OFF": "ON",
        "在庫連動ON/OFF": "ON",
        "表示連動": "連動",
        "同期モード": "新規登録",
        "同期ステータス": "",
        "同期日時": "",

        # N. システム情報（BY-CB列）
        "商品作成日時": "",
        "商品更新日時": "",
        "前回仕入れ価格": "",
        "価格変動率": "",

        # 初期登録専用列（CC-CF列）
        "登録ステータス": "未登録",
        "登録日時": now,
        "要確認フラグ": "",
        "確認メモ": "",
    }

    return data


def main():
    """メイン処理"""
    logger.info("=== 初期登録一覧生成開始 ===")

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

    # 未登録の採用商品を取得
    logger.info("採用済み未登録商品を取得中...")
    suppliers = client.get_unregistered_suppliers()
    logger.info(f"対象商品数: {len(suppliers)}件")

    if not suppliers:
        logger.info("新規登録対象の商品はありません")
        return

    # 初期登録一覧に追加
    success_count = 0
    fail_count = 0

    for supplier in suppliers:
        product_name = supplier.get("supplier_product_name", "不明")
        logger.info(f"処理中: {product_name}")

        try:
            # 初期データを生成
            initial_data = generate_initial_data(supplier)

            # シートに追加
            if client.add_initial_registration_row(initial_data):
                success_count += 1
                logger.info(f"  → 追加成功")
            else:
                fail_count += 1
                logger.error(f"  → 追加失敗")

        except Exception as e:
            fail_count += 1
            logger.error(f"  → エラー: {e}")

    # 結果サマリー
    logger.info("=== 結果 ===")
    logger.info(f"成功: {success_count}件")
    logger.info(f"失敗: {fail_count}件")

    if fail_count > 0:
        logger.warning("一部の商品の登録に失敗しました")
        sys.exit(1)

    logger.info("=== 初期登録一覧生成完了 ===")


if __name__ == "__main__":
    main()
