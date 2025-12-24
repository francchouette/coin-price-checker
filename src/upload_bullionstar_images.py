"""
ブリオンスター商品画像アップロードスクリプト

ブリオンスター商品ページ一覧シートから登録済み商品の画像を
カラーミー管理画面にアップロードする。

対象条件:
- B列（カラーミー登録状況）= 「登録済」
- D列（カラーミー商品URL）がある（商品IDを抽出可能）
- BJ-BS列（画像URL1-10）に外部URLがある（shop-pro.jp以外）
"""

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.colorme_image_uploader import (
    ColorMeImageUploader,
    UploadProgress,
)

logger = logging.getLogger(__name__)

# 進捗ファイルパス（ブリオンスター専用）
PROGRESS_FILE = Path("data/bullionstar_image_upload_progress.json")

# ブリオンスター商品ページ一覧の列インデックス（0-based）
COL_REGISTRATION_STATUS = 1  # B列: カラーミー登録状況
COL_COLORME_URL = 3          # D列: カラーミー商品URL
COL_IMAGE_1 = 61             # BJ列: 画像URL1
COL_IMAGE_10 = 70            # BS列: 画像URL10


def extract_product_id_from_url(url: str) -> int:
    """
    カラーミー商品URLから商品IDを抽出

    Args:
        url: カラーミー商品URL (例: https://yokohamacoin.shop-pro.jp/?pid=123456)

    Returns:
        int: 商品ID（抽出失敗時は0）
    """
    if not url:
        return 0
    match = re.search(r'pid=(\d+)', url)
    if match:
        return int(match.group(1))
    return 0


def get_products_needing_images() -> list[tuple[int, list[str]]]:
    """
    画像アップロードが必要な商品を取得

    Returns:
        list[tuple[int, list[str]]]: (商品ID, 画像URLリスト)のリスト
    """
    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return []

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        data = sheet.get_all_values()

        if len(data) <= 1:
            logger.info("データがありません")
            return []

        products = []

        for row_idx, row in enumerate(data[1:], start=2):
            # B列: 登録状況チェック
            registration_status = row[COL_REGISTRATION_STATUS] if len(row) > COL_REGISTRATION_STATUS else ""
            if registration_status != "登録済":
                continue

            # D列: カラーミー商品URLから商品IDを抽出
            colorme_url = row[COL_COLORME_URL] if len(row) > COL_COLORME_URL else ""
            product_id = extract_product_id_from_url(colorme_url)
            if not product_id:
                continue

            # BJ-BS列: 画像URL1-10
            image_urls = []
            for i in range(COL_IMAGE_1, COL_IMAGE_10 + 1):
                url = row[i] if len(row) > i else ""
                if url and "shop-pro.jp" not in url:
                    # 外部URLのみ追加（カラーミー登録済みは除外）
                    image_urls.append(url)

            if not image_urls:
                continue

            products.append((product_id, image_urls))
            logger.debug(f"行{row_idx}: 商品ID {product_id}, 画像{len(image_urls)}枚")

        total_images = sum(len(urls) for _, urls in products)
        logger.info(f"画像アップロード対象: {len(products)}商品, {total_images}枚")
        return products

    except Exception as e:
        logger.error(f"商品取得エラー: {e}")
        return []


async def run_upload(
    max_per_run: int = 100,
    progress_file: Path = PROGRESS_FILE,
    headless: bool = True
) -> bool:
    """
    画像アップロードを実行

    Args:
        max_per_run: 1回の実行で処理する最大件数
        progress_file: 進捗ファイルパス
        headless: ヘッドレスモードで実行

    Returns:
        bool: 成功時True
    """
    # アップロード対象を取得
    products = get_products_needing_images()

    if not products:
        logger.info("アップロード対象の商品がありません")
        return True

    # 進捗を読み込み
    progress = UploadProgress.load(progress_file)
    progress.total_products = len(products)

    # 未処理の商品をフィルタ
    pending_products = [
        (pid, urls) for pid, urls in products
        if not progress.is_completed(pid)
    ]

    if not pending_products:
        logger.info("すべての商品が処理済みです")
        logger.info(progress.get_summary())
        return True

    total_images = sum(len(urls) for _, urls in pending_products)
    logger.info(f"未処理の商品: {len(pending_products)}件, 画像: {total_images}枚")

    # アップローダーを初期化してアップロード実行
    async with ColorMeImageUploader(headless=headless) as uploader:
        results = await uploader.upload_product_images_batch(
            pending_products,
            progress=progress,
            max_products_per_run=max_per_run,
            save_progress_every=5
        )

    # 結果サマリー
    success_count = sum(1 for r in results if r.success)
    failed_count = len(results) - success_count

    logger.info("=" * 50)
    logger.info("画像アップロード完了")
    logger.info(f"今回の実行: {success_count}件成功, {failed_count}件失敗")
    logger.info(progress.get_summary())

    # 全完了判定
    remaining = len(pending_products) - len(results)
    if remaining > 0:
        logger.info(f"残り{remaining}件 - 次回の実行で継続します")

    return True


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="ブリオンスター商品画像アップローダー"
    )
    parser.add_argument(
        "--max-per-run",
        type=int,
        default=100,
        help="1回の実行で処理する最大件数（デフォルト: 100）"
    )
    parser.add_argument(
        "--progress-file",
        type=str,
        default=str(PROGRESS_FILE),
        help="進捗ファイルパス"
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="ブラウザを表示する（デバッグ用）"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="詳細ログ出力"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="進捗をリセットして最初から実行"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="進捗状況を表示して終了"
    )

    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    progress_file = Path(args.progress_file)

    # 進捗確認モード
    if args.status:
        progress = UploadProgress.load(progress_file)
        print("=" * 50)
        print("ブリオンスター商品画像アップロード進捗")
        print("=" * 50)
        print(f"総商品数: {progress.total_products}件")
        print(f"処理済み: {progress.processed_count}件")
        print(f"  - 成功: {progress.success_count}件")
        print(f"  - 失敗: {progress.failed_count}件")
        print(f"  - スキップ: {progress.skipped_count}件")
        print(f"開始日時: {progress.started_at}")
        print(f"最終更新: {progress.last_updated}")
        if progress.failed_product_ids:
            print(f"\n失敗した商品ID（先頭20件）:")
            for pid in progress.failed_product_ids[:20]:
                print(f"  - {pid}")
        return

    # 進捗リセット
    if args.reset:
        if progress_file.exists():
            progress_file.unlink()
            logger.info(f"進捗をリセットしました: {progress_file}")
        else:
            logger.info("進捗ファイルは存在しません")

    # 設定検証
    errors = Config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    # アップロード実行
    try:
        success = asyncio.run(run_upload(
            max_per_run=args.max_per_run,
            progress_file=progress_file,
            headless=not args.no_headless
        ))
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("中断されました")
        sys.exit(130)
    except Exception as e:
        logger.error(f"エラー: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
