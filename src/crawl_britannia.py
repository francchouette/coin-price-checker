"""
Britannia Coin Company 商品一覧クロール実行スクリプト

商品一覧を取得してスプレッドシートに保存する。
"""

import sys
import logging
from datetime import datetime, timezone, timedelta

import gspread
from google.auth import default
from google.oauth2.service_account import Credentials

from .config import Config
from .crawlers.britannia import BritanniaCrawler, BritanniaProduct

# 日本時間 (JST = UTC+9)
JST = timezone(timedelta(hours=9))

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# シートのヘッダー
SHEET_HEADERS = [
    "URL",
    "商品名",
    "価格(GBP)",
    "在庫",
    "カテゴリ",
    "サブカテゴリ",
    "説明",
    "仕様",
    "画像URL1",
    "画像URL2",
    "画像URL3",
    "画像URL4",
    "画像URL5",
    "取得日時",
]


def get_spreadsheet_client():
    """スプレッドシートクライアントを取得"""
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]

    # サービスアカウントJSON
    creds_json = Config.get_google_credentials()
    if creds_json:
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        logger.info("サービスアカウント認証を使用")
    else:
        # ADC
        creds, _ = default(scopes=scopes)
        logger.info("ADC認証を使用")

    return gspread.authorize(creds)


def save_to_spreadsheet(products: list[BritanniaProduct]) -> bool:
    """
    商品一覧をスプレッドシートに保存

    Args:
        products: 商品リスト

    Returns:
        bool: 保存成功時True
    """
    if not products:
        logger.warning("保存する商品がありません")
        return False

    try:
        client = get_spreadsheet_client()
        spreadsheet = client.open_by_key(Config.SPREADSHEET_ID)

        # シートを取得または作成
        sheet_name = Config.SHEET_MASTER_BRITANNIA
        try:
            sheet = spreadsheet.worksheet(sheet_name)
            logger.info(f"既存シート '{sheet_name}' を使用")
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
            logger.info(f"新規シート '{sheet_name}' を作成")

        # 既存データを取得
        existing_data = sheet.get_all_values()
        existing_urls = set()

        if existing_data and len(existing_data) > 1:
            # ヘッダー行をスキップして既存URLを取得
            for row in existing_data[1:]:
                if row:
                    existing_urls.add(row[0])

        # 新規・更新データを準備
        new_rows = []
        update_rows = []

        # URL -> 行番号のマッピング
        url_to_row = {}
        for i, row in enumerate(existing_data[1:], start=2):
            if row:
                url_to_row[row[0]] = i

        for product in products:
            # 画像URLを最大5つまで展開
            images = product.images[:5] if product.images else []
            images.extend([""] * (5 - len(images)))  # 5つになるまで空文字で埋める

            row_data = [
                product.url,
                product.name,
                str(product.price),
                "○" if product.in_stock else "×",
                product.category,
                product.subcategory,
                product.description[:500] if product.description else "",
                product.specification[:200] if product.specification else "",
                images[0],
                images[1],
                images[2],
                images[3],
                images[4],
                product.scraped_at,
            ]

            if product.url in existing_urls:
                # 更新
                row_num = url_to_row.get(product.url)
                if row_num:
                    update_rows.append((row_num, row_data))
            else:
                # 新規追加
                new_rows.append(row_data)

        # ヘッダーがない場合は追加
        if not existing_data:
            sheet.append_row(SHEET_HEADERS, value_input_option='RAW')
            logger.info("ヘッダー行を追加")

        # 既存行を更新
        if update_rows:
            logger.info(f"既存商品を更新中: {len(update_rows)}件")
            for row_num, row_data in update_rows:
                sheet.update(f'A{row_num}:N{row_num}', [row_data], value_input_option='RAW')

        # 新規行を追加
        if new_rows:
            logger.info(f"新規商品を追加中: {len(new_rows)}件")
            sheet.append_rows(new_rows, value_input_option='RAW')

        logger.info(
            f"スプレッドシート保存完了: "
            f"更新 {len(update_rows)}件, "
            f"新規 {len(new_rows)}件"
        )

        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        return False


def run(include_details: bool = False):
    """
    メイン処理

    Args:
        include_details: 商品詳細ページも取得するか
    """
    logger.info("=" * 60)
    logger.info("Britannia Coin Company 商品一覧クロールを開始します")
    logger.info("=" * 60)

    start_time = datetime.now()

    # 設定検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        logger.error("設定エラーのため終了します")
        sys.exit(1)

    # クロール実行
    logger.info("商品一覧を取得中...")
    with BritanniaCrawler() as crawler:
        products = crawler.crawl_all(include_details=include_details)

    if not products:
        logger.warning("商品が取得できませんでした")
        sys.exit(1)

    logger.info(f"取得完了: {len(products)}件")

    # 在庫状況を集計
    in_stock = sum(1 for p in products if p.in_stock)
    out_of_stock = len(products) - in_stock
    logger.info(f"  在庫あり: {in_stock}件, 在庫なし: {out_of_stock}件")

    # スプレッドシートに保存
    logger.info("スプレッドシートに保存中...")
    if save_to_spreadsheet(products):
        logger.info("保存完了")
    else:
        logger.error("保存に失敗しました")
        sys.exit(1)

    # 完了
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info(f"クロール完了（{elapsed:.1f}秒）")
    logger.info("=" * 60)


if __name__ == "__main__":
    # --details オプションで詳細も取得
    include_details = "--details" in sys.argv
    run(include_details=include_details)
