"""
ブリオンスター商品画像アップロードスクリプト

ブリオンスター商品ページ一覧シートから登録済み商品の画像を
カラーミー管理画面にアップロードする。

対象条件:
- B列（カラーミー登録状況）= 「登録済」
- E列（カラーミー商品URL）がある（商品IDを抽出可能）
- BK-BT列（画像URL1-10）に外部URLがある（shop-pro.jp以外）
"""

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.colorme_image_uploader import (
    ColorMeImageUploader,
    UploadProgress,
)
from src.bs_sheet_columns import Col, get_cell, range_ref

logger = logging.getLogger(__name__)

# 進捗ファイルパス（ブリオンスター専用）
PROGRESS_FILE = Path("data/bullionstar_image_upload_progress.json")

# 商品IDと行番号のマッピングを保持
_product_row_map: dict[int, int] = {}
# 商品IDとSEO情報のマッピングを保持
_product_seo_map: dict[int, dict] = {}


def extract_product_id_from_url(url: str) -> int:
    """
    カラーミー商品URLから商品IDを抽出

    Args:
        url: カラーミー商品URL (例: https://www.ybx.jp/?pid=123456)

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
    global _product_row_map, _product_seo_map
    _product_row_map = {}
    _product_seo_map = {}

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
            registration_status = get_cell(row, Col.REGISTRATION_STATUS)
            if registration_status != "登録済":
                continue

            # E列: カラーミー商品URLから商品IDを抽出
            colorme_url = get_cell(row, Col.COLORME_URL)
            product_id = extract_product_id_from_url(colorme_url)
            if not product_id:
                continue

            # BK-BT列: 画像URL1-10
            image_urls = []
            for i in range(Col.IMAGE_1.index, Col.IMAGE_10.index + 1):
                url = row[i].strip() if len(row) > i and row[i] else ""
                if url and "shop-pro.jp" not in url:
                    # 外部URLのみ追加（カラーミー登録済みは除外）
                    image_urls.append(url)

            # SEO情報を取得（BU-BW列）
            page_title = get_cell(row, Col.CM_PAGE_TITLE)
            meta_desc = get_cell(row, Col.CM_META_DESC)
            meta_keywords = get_cell(row, Col.CM_META_KEYWORDS)

            # SEO情報がある場合はマップに保存
            if page_title or meta_desc or meta_keywords:
                _product_seo_map[product_id] = {
                    'page_title': page_title,
                    'meta_description': meta_desc,
                    'meta_keywords': meta_keywords
                }

            if not image_urls:
                continue

            products.append((product_id, image_urls))
            _product_row_map[product_id] = row_idx
            logger.debug(f"行{row_idx}: 商品ID {product_id}, 画像{len(image_urls)}枚")

        total_images = sum(len(urls) for _, urls in products)
        logger.info(f"画像アップロード対象: {len(products)}商品, {total_images}枚")
        logger.info(f"SEO更新対象: {len(_product_seo_map)}商品")
        return products

    except Exception as e:
        logger.error(f"商品取得エラー: {e}")
        return []


def update_image_urls_in_spreadsheet(updates: list[tuple[int, list[str]]]) -> int:
    """
    スプレッドシートの画像URLをカラーミーのURLに更新

    Args:
        updates: (商品ID, カラーミー画像URLリスト)のリスト

    Returns:
        int: 更新した行数
    """
    if not updates:
        return 0

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return 0

    try:
        sheet = client._spreadsheet.worksheet(Config.SHEET_BULLIONSTAR_PRODUCTS)
        batch_data = []

        for product_id, image_urls in updates:
            row_idx = _product_row_map.get(product_id)
            if not row_idx:
                logger.warning(f"商品ID {product_id} の行番号が見つかりません")
                continue

            # BK-BT列（画像URL1-10）を更新
            # 画像URLを10列分に展開（足りない分は空文字）
            padded_urls = image_urls[:10] + [""] * (10 - len(image_urls[:10]))
            range_str = range_ref(Col.IMAGE_1, Col.IMAGE_10, row_idx)
            batch_data.append({
                'range': range_str,
                'values': [padded_urls]
            })
            logger.debug(f"行{row_idx}: 画像URL {len(image_urls)}枚を更新")

        if batch_data:
            sheet.batch_update(batch_data, value_input_option='USER_ENTERED')
            logger.info(f"スプレッドシート更新完了: {len(batch_data)}行")
            return len(batch_data)

        return 0

    except Exception as e:
        logger.error(f"スプレッドシート更新エラー: {e}")
        return 0


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

        # SEO項目の更新（画像アップロード成功した商品のみ）
        seo_success_count = 0
        seo_failed_count = 0
        for r in results:
            if r.success and r.product_id in _product_seo_map:
                seo_info = _product_seo_map[r.product_id]
                success, error = await uploader.update_seo_fields(
                    r.product_id,
                    page_title=seo_info.get('page_title', ''),
                    meta_description=seo_info.get('meta_description', ''),
                    meta_keywords=seo_info.get('meta_keywords', '')
                )
                if success:
                    seo_success_count += 1
                else:
                    seo_failed_count += 1
                    logger.warning(f"  SEO更新失敗: 商品ID {r.product_id} - {error}")

        if seo_success_count > 0 or seo_failed_count > 0:
            logger.info(f"SEO更新: {seo_success_count}件成功, {seo_failed_count}件失敗")

    # 成功した商品の画像URLをスプレッドシートに更新
    successful_updates = [
        (r.product_id, r.uploaded_urls)
        for r in results
        if r.success and r.uploaded_urls
    ]

    if successful_updates:
        logger.info(f"スプレッドシートの画像URLを更新中... ({len(successful_updates)}件)")
        updated_count = update_image_urls_in_spreadsheet(successful_updates)
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
