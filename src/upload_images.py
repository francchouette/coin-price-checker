"""
カラーミーショップ 商品画像アップロードスクリプト

スプレッドシートから商品データを読み込み、画像URLがある商品の
画像をカラーミー管理画面にアップロードする。

GitHub Actionsでの実行を想定:
- 環境変数から認証情報を取得
- 進捗をキャッシュで保持して中断再開
- 1回の実行で処理する件数を制限
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.spreadsheet import SpreadsheetClient
from src.colorme_image_uploader import (
    ColorMeImageUploader,
    UploadProgress,
    DEFAULT_PROGRESS_FILE
)

logger = logging.getLogger(__name__)


def get_products_needing_images(spreadsheet: SpreadsheetClient) -> list[tuple[int, list[str]]]:
    """
    画像アップロードが必要な商品を取得

    条件:
    - 商品IDがある
    - AR〜BA列（画像URL1〜10）に外部URLがある
    - 画像がカラーミーにまだ登録されていない（img*.shop-pro.jpではない）

    Returns:
        list[tuple[int, list[str]]]: (商品ID, 画像URLリスト)のリスト
    """
    products = spreadsheet.get_colorme_products()

    needs_upload = []

    for product in products:
        if not product.product_id:
            continue

        # image_urlsはリスト（AR〜BA列、最大10枚）
        if not product.image_urls:
            continue

        # 外部URLのみ抽出（shop-pro.jp以外）
        external_urls = []
        for url in product.image_urls:
            if url and "shop-pro.jp" not in url:
                external_urls.append(url)

        if not external_urls:
            logger.debug(f"スキップ（全て登録済み）: {product.product_id}")
            continue

        # 外部URLがあればアップロード対象
        needs_upload.append((product.product_id, external_urls))
        logger.debug(f"アップロード対象: {product.product_id} - {len(external_urls)}枚")

    total_images = sum(len(urls) for _, urls in needs_upload)
    logger.info(f"画像アップロード対象: {len(needs_upload)}商品, {total_images}枚")
    return needs_upload


async def run_upload(
    max_per_run: int = 100,
    progress_file: Path = DEFAULT_PROGRESS_FILE,
    headless: bool = True
):
    """
    画像アップロードを実行

    Args:
        max_per_run: 1回の実行で処理する最大件数
        progress_file: 進捗ファイルパス
        headless: ヘッドレスモードで実行
    """
    # スプレッドシートに接続
    logger.info("スプレッドシートに接続中...")
    spreadsheet = SpreadsheetClient()
    if not spreadsheet.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    # アップロード対象を取得
    products = get_products_needing_images(spreadsheet)

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

    # 成功した商品の画像URLをスプレッドシートに更新
    successful_updates = [
        (r.product_id, r.uploaded_urls)
        for r in results
        if r.success and r.uploaded_urls
    ]

    if successful_updates:
        logger.info(f"スプレッドシートの画像URLを更新中... ({len(successful_updates)}件)")
        updated_count = spreadsheet.update_product_image_urls_batch(successful_updates)
        logger.info(f"スプレッドシート更新完了: {updated_count}件")

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
        return True  # 部分完了も成功とする
    else:
        logger.info("すべての商品の処理が完了しました")
        return True


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="カラーミーショップ商品画像アップローダー"
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
        default="data/image_upload_progress.json",
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
        print("画像アップロード進捗状況")
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
        sys.exit(1)


if __name__ == "__main__":
    main()
