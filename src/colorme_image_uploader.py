"""
カラーミーショップ 画像アップロードモジュール（Playwright版）

ColorMe APIでは画像アップロードが実質的にサポートされていないため、
Playwrightを使用したブラウザ自動化で画像をアップロードする。

3000件以上の商品に対応:
- 進捗の永続化（JSONファイル）で中断時に再開可能
- バッチ処理でGitHub Actions実行時間制限に対応
- エラー時のリトライとスキップ機能
"""

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests
from PIL import Image
from playwright.async_api import async_playwright, Browser, Page, Playwright

logger = logging.getLogger(__name__)

# デフォルトの進捗ファイルパス
DEFAULT_PROGRESS_FILE = Path("data/image_upload_progress.json")


@dataclass
class ImageUploadResult:
    """画像アップロード結果"""
    product_id: int
    success: bool
    error_message: str = ""
    uploaded_url: str = ""  # メイン画像URL（後方互換性）
    uploaded_urls: list[str] = None  # 全画像URLリスト
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if self.uploaded_urls is None:
            self.uploaded_urls = []


@dataclass
class UploadProgress:
    """
    アップロード進捗管理（3000件以上対応）

    JSONファイルに保存して、中断時に再開可能にする。
    """
    total_products: int = 0
    processed_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    completed_product_ids: list[int] = None
    failed_product_ids: list[int] = None
    last_updated: str = ""
    started_at: str = ""

    def __post_init__(self):
        if self.completed_product_ids is None:
            self.completed_product_ids = []
        if self.failed_product_ids is None:
            self.failed_product_ids = []
        if not self.started_at:
            self.started_at = datetime.now().isoformat()

    def mark_completed(self, product_id: int):
        """成功した商品を記録"""
        if product_id not in self.completed_product_ids:
            self.completed_product_ids.append(product_id)
            self.success_count += 1
        self.processed_count += 1
        self.last_updated = datetime.now().isoformat()

    def mark_failed(self, product_id: int):
        """失敗した商品を記録"""
        if product_id not in self.failed_product_ids:
            self.failed_product_ids.append(product_id)
            self.failed_count += 1
        self.processed_count += 1
        self.last_updated = datetime.now().isoformat()

    def mark_skipped(self, product_id: int):
        """スキップした商品を記録（既に完了済み等）"""
        self.skipped_count += 1
        self.last_updated = datetime.now().isoformat()

    def is_completed(self, product_id: int) -> bool:
        """商品が処理済みかどうか"""
        return product_id in self.completed_product_ids

    def save(self, filepath: Path = DEFAULT_PROGRESS_FILE):
        """進捗をJSONファイルに保存"""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"進捗を保存: {filepath}")

    @classmethod
    def load(cls, filepath: Path = DEFAULT_PROGRESS_FILE) -> "UploadProgress":
        """JSONファイルから進捗を読み込み"""
        if not filepath.exists():
            return cls()
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"進捗を読み込み: {filepath}")
            return cls(**data)
        except Exception as e:
            logger.warning(f"進捗ファイル読み込みエラー: {e}")
            return cls()

    def get_summary(self) -> str:
        """進捗サマリーを取得"""
        return (
            f"進捗: {self.processed_count}/{self.total_products}件 "
            f"(成功: {self.success_count}, 失敗: {self.failed_count}, "
            f"スキップ: {self.skipped_count})"
        )


class ColorMeImageUploader:
    """
    カラーミーショップ画像アップローダー（Playwright版）

    管理画面にログインして商品画像をアップロードする。
    GitHub Actions環境での実行を想定し、headlessモードで動作。
    """

    ADMIN_URL = "https://admin.shop-pro.jp/"

    def __init__(
        self,
        login_id: str = None,
        password: str = None,
        headless: bool = True
    ):
        """
        Args:
            login_id: 管理画面ログインID（省略時は環境変数 COLORME_LOGIN_ID）
            password: 管理画面パスワード（省略時は環境変数 COLORME_PASSWORD）
            headless: ヘッドレスモードで実行するか（GitHub Actionsでは必須）
        """
        self.login_id = login_id or os.environ.get("COLORME_LOGIN_ID", "yokohamacoin")
        self.password = password or os.environ.get("COLORME_PASSWORD", "Fran0833")
        self.headless = headless

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._logged_in = False

    async def __aenter__(self):
        """非同期コンテキストマネージャー: 開始"""
        await self._start_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """非同期コンテキストマネージャー: 終了"""
        await self._close_browser()

    async def _start_browser(self):
        """ブラウザを起動"""
        if self._browser:
            return

        logger.info("ブラウザを起動中...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._page = await self._browser.new_page()
        logger.info("ブラウザ起動完了")

    async def _close_browser(self):
        """ブラウザを終了"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._page = None
        self._logged_in = False
        logger.info("ブラウザ終了")

    async def login(self) -> bool:
        """
        管理画面にログイン

        Returns:
            bool: ログイン成功時True
        """
        if self._logged_in:
            return True

        if not self._page:
            await self._start_browser()

        try:
            logger.info("カラーミー管理画面にログイン中...")

            await self._page.goto(self.ADMIN_URL, wait_until="networkidle")

            # ログインフォーム入力
            await self._page.fill('input[name="login_id"]', self.login_id)
            await self._page.fill('input[name="password"]', self.password)
            await self._page.click('input[type="submit"]')

            # ログイン完了待機
            await self._page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            # ログイン成功確認（管理画面のURLになっているか）
            current_url = self._page.url
            if "mode=" in current_url or "admin.shop-pro.jp" in current_url:
                self._logged_in = True
                logger.info("ログイン成功")
                return True
            else:
                logger.error(f"ログイン失敗: リダイレクト先 {current_url}")
                return False

        except Exception as e:
            logger.error(f"ログインエラー: {e}")
            return False

    def _download_and_prepare_image(self, image_url: str, temp_dir: Path) -> Optional[Path]:
        """
        画像をダウンロードして一時ファイルに保存

        透過画像（アルファチャンネル付き）はPNG形式で保存し、
        それ以外はJPEG形式で保存する。

        Args:
            image_url: 画像URL
            temp_dir: 一時ファイル保存先ディレクトリ

        Returns:
            Path: 保存した一時ファイルのパス（失敗時None）
        """
        try:
            logger.info(f"  画像ダウンロード: {image_url[:80]}...")

            # 画像ダウンロード
            response = requests.get(image_url, timeout=60)
            response.raise_for_status()

            # 画像を開く
            img = Image.open(BytesIO(response.content))

            # 透過画像かどうか判定
            has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)

            if has_alpha:
                # 透過画像はPNGで保存
                if img.mode == 'P':
                    img = img.convert('RGBA')
                elif img.mode == 'LA':
                    img = img.convert('RGBA')

                temp_file = temp_dir / f"upload_{hash(image_url) & 0xFFFFFFFF}.png"
                img.save(temp_file, format='PNG', optimize=True)
                logger.info(f"    → PNG保存（透過あり）: {temp_file.stat().st_size:,} bytes")
            else:
                # 透過なしはJPEGで保存
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                temp_file = temp_dir / f"upload_{hash(image_url) & 0xFFFFFFFF}.jpg"
                img.save(temp_file, format='JPEG', quality=90)
                logger.info(f"    → JPEG保存: {temp_file.stat().st_size:,} bytes")

            return temp_file

        except Exception as e:
            logger.error(f"    → 画像準備エラー: {e}")
            return None

    async def _wait_for_plupload_complete(self, max_wait: float = 30.0) -> bool:
        """
        pluploadのアップロード完了を待機

        pluploadはファイル選択後に非同期でサーバーにアップロードするため、
        アップロード完了を待ってから保存する必要がある。

        Args:
            max_wait: 最大待機時間（秒）

        Returns:
            bool: アップロード完了時True
        """
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < max_wait:
            try:
                # 方法1: pluploadのアップロード中表示を確認
                # アップロード中のプログレスバーやスピナーが消えるのを待つ
                uploading_indicators = await self._page.query_selector_all(
                    '.plupload_progress, .uploading, [class*="upload"][class*="progress"], '
                    '.plupload_file_status:not(.plupload_done)'
                )

                # プログレス表示が消えたらアップロード完了
                if not uploading_indicators:
                    # 追加確認: 画像プレビューが表示されているか
                    preview_images = await self._page.query_selector_all(
                        '.plupload_file_thumb img, .uploaded-image, '
                        'img[src*="shop-pro.jp"], .image-preview img'
                    )
                    if preview_images:
                        logger.info(f"  → アップロード完了確認（プレビュー画像: {len(preview_images)}枚）")
                        return True

                # 方法2: ネットワークが静かになるのを待つ
                await self._page.wait_for_load_state("networkidle", timeout=5000)

                # 方法3: JavaScriptでpluploadの状態を確認
                try:
                    plupload_state = await self._page.evaluate("""
                        () => {
                            // グローバルのpluploadインスタンスを探す
                            if (typeof uploader !== 'undefined' && uploader.files) {
                                const pending = uploader.files.filter(f =>
                                    f.status !== plupload.DONE && f.status !== plupload.FAILED
                                ).length;
                                return { pending: pending, total: uploader.files.length };
                            }
                            // 他の命名パターン
                            if (typeof imageUploader !== 'undefined' && imageUploader.files) {
                                const pending = imageUploader.files.filter(f =>
                                    f.status !== plupload.DONE && f.status !== plupload.FAILED
                                ).length;
                                return { pending: pending, total: imageUploader.files.length };
                            }
                            return null;
                        }
                    """)
                    if plupload_state and plupload_state.get('pending', 0) == 0:
                        logger.info(f"  → pluploadアップロード完了（{plupload_state.get('total', 0)}ファイル）")
                        return True
                except Exception:
                    pass  # JavaScriptエラーは無視

                await asyncio.sleep(2)

            except Exception as e:
                logger.debug(f"  plupload状態確認エラー: {e}")
                await asyncio.sleep(2)

        # タイムアウト時も一応進む（ネットワーク静止を最終確認）
        logger.warning(f"  plupload完了待機タイムアウト（{max_wait}秒）")
        await self._page.wait_for_load_state("networkidle", timeout=10000)
        return False

    async def _extract_image_urls_from_page(self, product_id: int) -> list[str]:
        """
        商品編集ページから画像URLを直接抽出

        Args:
            product_id: 商品ID

        Returns:
            list[str]: 画像URLリスト
        """
        try:
            # 商品編集ページに移動（保存後のリダイレクト先の場合もある）
            edit_url = f"{self.ADMIN_URL}?mode=product_edt&type=UPD&product_id={product_id}"
            current_url = self._page.url
            if "product_id=" not in current_url or str(product_id) not in current_url:
                await self._page.goto(edit_url, wait_until="networkidle")
                await asyncio.sleep(2)

            urls = []

            # 商品画像URLのパターン（imgXX.shop-pro.jp/PA01517/852/product/...）
            # アイコンや共通画像は除外（/img/new/icons, /img/common など）
            import re
            product_image_pattern = re.compile(
                r'https?://img\d+\.shop-pro\.jp/PA\d+/\d+/product/\d+\.(jpg|jpeg|png|gif|webp)',
                re.IGNORECASE
            )

            # img要素のsrc属性から商品画像のみ取得
            img_elements = await self._page.query_selector_all('img[src*="shop-pro.jp"]')
            for img in img_elements:
                src = await img.get_attribute("src")
                if not src:
                    continue

                # サムネイルサイズを除去して元URLに
                original_src = src
                for size in ["/50_50/", "/100_100/", "/200_200/", "/300_300/", "/400_400/"]:
                    if size in original_src:
                        original_src = original_src.replace(size, "/")
                        break

                # 商品画像パターンにマッチするもののみ追加
                if product_image_pattern.match(original_src) and original_src not in urls:
                    urls.append(original_src)

            logger.info(f"  ページから画像URL取得: {len(urls)}枚")
            for i, url in enumerate(urls[:5]):
                logger.debug(f"    {i+1}: {url[:80]}...")

            return urls

        except Exception as e:
            logger.warning(f"  ページから画像URL取得エラー: {e}")
            return []

    async def _fetch_uploaded_image_urls(self, product_id: int) -> list[str]:
        """
        カラーミーAPIから商品の画像URLを取得

        Args:
            product_id: 商品ID

        Returns:
            list[str]: 画像URLリスト（メイン画像 + 追加画像）
        """
        import os

        access_token = os.environ.get("COLORME_ACCESS_TOKEN", "")
        if not access_token:
            logger.warning("COLORME_ACCESS_TOKENが未設定のため画像URL取得スキップ")
            return []

        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = requests.get(
                f"https://api.shop-pro.jp/v1/products/{product_id}.json",
                headers=headers,
                timeout=10
            )
            response.raise_for_status()

            data = response.json()
            product = data.get("product", {})
            urls = []

            # メイン画像
            main_image = product.get("image_url", "")
            if main_image:
                urls.append(main_image)
                logger.debug(f"    メイン画像: {main_image}")

            # 追加画像
            images = product.get("images", [])
            logger.debug(f"    追加画像数: {len(images)}")
            for img in images:
                src = img.get("src", "")
                if src and src not in urls:
                    urls.append(src)
                    logger.debug(f"    追加画像: {src}")

            logger.info(f"  画像URL取得: {len(urls)}枚")
            if not urls:
                # URLがない場合はAPIレスポンスの一部をログに出力
                logger.warning(f"    image_url: {product.get('image_url', '(なし)')}")
                logger.warning(f"    images: {product.get('images', [])}")
            return urls

        except Exception as e:
            logger.warning(f"  画像URL取得エラー: {e}")
            return []

    async def upload_product_image(
        self,
        product_id: int,
        image_url: str,
        wait_after_upload: float = 5.0,
        max_retries: int = 3
    ) -> ImageUploadResult:
        """
        単一商品の画像をアップロード（後方互換性のため残す）

        Args:
            product_id: 商品ID
            image_url: 画像URL
            wait_after_upload: アップロード後の待機時間（秒）
            max_retries: リトライ回数

        Returns:
            ImageUploadResult: アップロード結果
        """
        return await self.upload_product_images(
            product_id=product_id,
            image_urls=[image_url],
            wait_after_upload=wait_after_upload,
            max_retries=max_retries
        )

    async def upload_product_images(
        self,
        product_id: int,
        image_urls: list[str],
        wait_after_upload: float = 5.0,
        max_retries: int = 3
    ) -> ImageUploadResult:
        """
        商品の複数画像をアップロード

        Args:
            product_id: 商品ID
            image_urls: 画像URLのリスト（最大10枚）
            wait_after_upload: アップロード後の待機時間（秒）
            max_retries: リトライ回数

        Returns:
            ImageUploadResult: アップロード結果
        """
        if not self._logged_in:
            if not await self.login():
                return ImageUploadResult(
                    product_id=product_id,
                    success=False,
                    error_message="ログイン失敗"
                )

        image_count = len(image_urls)
        logger.info(f"商品ID {product_id} の画像を{image_count}枚アップロード中...")

        for attempt in range(max_retries):
            try:
                # 商品編集ページに移動
                edit_url = f"{self.ADMIN_URL}?mode=product_edt&type=UPD&product_id={product_id}"
                await self._page.goto(edit_url, wait_until="networkidle")
                await asyncio.sleep(2)

                # 画像ファイル入力を全て探す（plupload）
                file_inputs = await self._page.query_selector_all('input[type="file"]')

                # 画像用の入力フィールドをフィルタ
                image_inputs = []
                for fi in file_inputs:
                    accept = await fi.get_attribute("accept")
                    if accept and "image/jpeg" in accept and "video" not in accept:
                        image_inputs.append(fi)

                if not image_inputs:
                    return ImageUploadResult(
                        product_id=product_id,
                        success=False,
                        error_message="画像入力フィールドが見つかりません"
                    )

                logger.info(f"  画像入力フィールド: {len(image_inputs)}個検出")

                # 画像をダウンロードして一時ファイルに保存
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_paths = []
                    for i, url in enumerate(image_urls[:10]):  # 最大10枚
                        if not url:
                            continue
                        logger.info(f"  [{i+1}/{image_count}] ダウンロード中...")
                        temp_path = self._download_and_prepare_image(url, Path(temp_dir))
                        if temp_path:
                            temp_paths.append(temp_path)
                        else:
                            logger.warning(f"    → 画像{i+1}のダウンロード失敗、スキップ")

                    if not temp_paths:
                        return ImageUploadResult(
                            product_id=product_id,
                            success=False,
                            error_message="全ての画像のダウンロードに失敗"
                        )

                    # 画像をアップロード
                    uploaded_count = len(temp_paths)

                    if len(image_inputs) >= len(temp_paths):
                        # 入力フィールドが十分ある場合は各フィールドに1枚ずつ
                        for i, temp_path in enumerate(temp_paths):
                            logger.info(f"  [{i+1}/{len(temp_paths)}] アップロード中...")
                            await image_inputs[i].set_input_files(str(temp_path))
                            await asyncio.sleep(2)
                    else:
                        # 入力フィールドが1つの場合は複数ファイルを一度にセット
                        logger.info(f"  複数ファイルを一括アップロード中 ({len(temp_paths)}枚)...")
                        file_paths = [str(p) for p in temp_paths]
                        await image_inputs[0].set_input_files(file_paths)

                    # pluploadの非同期アップロード完了を待機
                    logger.info("  pluploadアップロード完了待機中...")
                    await self._wait_for_plupload_complete(wait_after_upload * 3)

                    # 保存ボタン実行（JavaScript経由）
                    logger.info("  保存ボタン実行中...")
                    await self._page.evaluate("jf_Submit('UPD')")

                    # 保存完了待機（サーバー処理時間を考慮して長めに）
                    await asyncio.sleep(5)
                    await self._page.wait_for_load_state("networkidle")
                    await asyncio.sleep(5)  # 追加の待機（画像処理完了待ち）

                logger.info(f"  → 商品ID {product_id}: {uploaded_count}枚アップロード成功")

                # ページから直接画像URLを取得
                uploaded_urls = await self._extract_image_urls_from_page(product_id)

                # ページから取得できなかった場合はAPIで取得
                if not uploaded_urls:
                    logger.info("  ページから画像URL取得できず、APIで取得中...")
                    for fetch_attempt in range(3):
                        await asyncio.sleep(3)
                        uploaded_urls = await self._fetch_uploaded_image_urls(product_id)
                        if uploaded_urls:
                            break
                        logger.info(f"  画像URL取得リトライ中... ({fetch_attempt + 1}/3)")
                        await asyncio.sleep(5)

                return ImageUploadResult(
                    product_id=product_id,
                    success=True,
                    uploaded_url=uploaded_urls[0] if uploaded_urls else "",
                    uploaded_urls=uploaded_urls
                )

            except Exception as e:
                error_msg = str(e)
                logger.warning(f"  → 試行 {attempt + 1}/{max_retries} 失敗: {error_msg}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                    # ページをリフレッシュしてリトライ
                    try:
                        await self._page.reload()
                    except Exception:
                        pass
                else:
                    return ImageUploadResult(
                        product_id=product_id,
                        success=False,
                        error_message=error_msg
                    )

        return ImageUploadResult(
            product_id=product_id,
            success=False,
            error_message="リトライ上限到達"
        )

    async def upload_product_images_batch(
        self,
        products: list[tuple[int, list[str] | str]],
        batch_size: int = 50,
        delay_between_products: float = 2.0,
        progress: UploadProgress = None,
        save_progress_every: int = 10,
        max_products_per_run: int = 0
    ) -> list[ImageUploadResult]:
        """
        複数商品の画像を一括アップロード

        3000件以上の商品に対応:
        - 進捗を定期的にファイルに保存（中断時に再開可能）
        - max_products_per_runでGitHub Actions実行時間制限に対応
        - 処理済み商品は自動スキップ

        Args:
            products: (商品ID, 画像URLリストまたは単一URL)のリスト
            batch_size: セッション更新間隔（件数）
            delay_between_products: 商品間の待機時間（秒）
            progress: 進捗管理オブジェクト（省略時は新規作成）
            save_progress_every: 進捗保存間隔（件数）
            max_products_per_run: 1回の実行で処理する最大件数（0=無制限）

        Returns:
            list[ImageUploadResult]: 各商品のアップロード結果
        """
        results = []
        total = len(products)

        # 進捗管理初期化
        if progress is None:
            progress = UploadProgress.load()
        progress.total_products = total

        logger.info(f"画像一括アップロード開始: {total}件")
        logger.info(progress.get_summary())

        processed_this_run = 0

        for i, (product_id, image_urls) in enumerate(products):
            # 最大処理件数チェック（GitHub Actions対応）
            if max_products_per_run > 0 and processed_this_run >= max_products_per_run:
                logger.info(f"最大処理件数到達 ({max_products_per_run}件): 次回の実行で継続")
                break

            # 処理済みスキップ
            if progress.is_completed(product_id):
                logger.debug(f"スキップ（処理済み）: 商品ID {product_id}")
                progress.mark_skipped(product_id)
                continue

            # バッチ境界でログインし直す（セッション維持）
            if processed_this_run > 0 and processed_this_run % batch_size == 0:
                logger.info(f"バッチ境界: セッション更新中...")
                self._logged_in = False
                await self.login()

            # image_urlsが文字列の場合はリストに変換（後方互換性）
            if isinstance(image_urls, str):
                image_urls = [image_urls]

            # 進捗表示
            remaining = total - progress.processed_count
            img_count = len(image_urls)
            logger.info(f"[{progress.processed_count + 1}/{total}] 商品ID {product_id} ({img_count}枚, 残り約{remaining}件)")

            result = await self.upload_product_images(product_id, image_urls)
            results.append(result)

            # 進捗記録
            if result.success:
                progress.mark_completed(product_id)
            else:
                progress.mark_failed(product_id)

            processed_this_run += 1

            # 定期的に進捗を保存
            if processed_this_run % save_progress_every == 0:
                progress.save()
                logger.info(progress.get_summary())

            # 商品間の待機
            await asyncio.sleep(delay_between_products)

        # 最終保存
        progress.save()

        # サマリー
        success_count = sum(1 for r in results if r.success)
        logger.info(f"今回の実行: {success_count}/{processed_this_run}件成功")
        logger.info(progress.get_summary())

        return results

    async def upload_with_resume(
        self,
        products: list[tuple[int, str]],
        max_products_per_run: int = 100,
        progress_file: Path = DEFAULT_PROGRESS_FILE
    ) -> tuple[list[ImageUploadResult], UploadProgress]:
        """
        再開可能な画像アップロード（GitHub Actions向け）

        前回の実行から中断した位置で再開する。
        1回の実行で処理する件数を制限して、GitHub Actionsの
        実行時間制限（6時間）に対応。

        Args:
            products: (商品ID, 画像URL)のリスト
            max_products_per_run: 1回の実行で処理する最大件数
            progress_file: 進捗ファイルパス

        Returns:
            tuple: (アップロード結果リスト, 進捗オブジェクト)
        """
        progress = UploadProgress.load(progress_file)
        progress.total_products = len(products)

        results = await self.upload_product_images_batch(
            products,
            progress=progress,
            max_products_per_run=max_products_per_run
        )

        return results, progress


async def upload_single_image(
    product_id: int,
    image_url: str,
    login_id: str = None,
    password: str = None,
    headless: bool = True
) -> ImageUploadResult:
    """
    単一商品の画像をアップロードするヘルパー関数

    Args:
        product_id: 商品ID
        image_url: 画像URL
        login_id: ログインID
        password: パスワード
        headless: ヘッドレスモード

    Returns:
        ImageUploadResult: アップロード結果
    """
    async with ColorMeImageUploader(
        login_id=login_id,
        password=password,
        headless=headless
    ) as uploader:
        return await uploader.upload_product_image(product_id, image_url)


async def upload_images_batch(
    products: list[tuple[int, str]],
    login_id: str = None,
    password: str = None,
    headless: bool = True,
    batch_size: int = 50,
    max_products_per_run: int = 0
) -> list[ImageUploadResult]:
    """
    複数商品の画像を一括アップロードするヘルパー関数

    Args:
        products: (商品ID, 画像URL)のリスト
        login_id: ログインID
        password: パスワード
        headless: ヘッドレスモード
        batch_size: バッチサイズ
        max_products_per_run: 1回の実行で処理する最大件数（0=無制限）

    Returns:
        list[ImageUploadResult]: 各商品のアップロード結果
    """
    async with ColorMeImageUploader(
        login_id=login_id,
        password=password,
        headless=headless
    ) as uploader:
        return await uploader.upload_product_images_batch(
            products,
            batch_size=batch_size,
            max_products_per_run=max_products_per_run
        )


async def upload_images_with_resume(
    products: list[tuple[int, str]],
    login_id: str = None,
    password: str = None,
    headless: bool = True,
    max_products_per_run: int = 100,
    progress_file: Path = DEFAULT_PROGRESS_FILE
) -> tuple[list[ImageUploadResult], UploadProgress]:
    """
    再開可能な画像一括アップロード（GitHub Actions向け）

    3000件以上の商品に対応:
    - 進捗をJSONファイルに保存
    - 中断した位置から再開可能
    - 1回の実行で処理する件数を制限

    Args:
        products: (商品ID, 画像URL)のリスト
        login_id: ログインID
        password: パスワード
        headless: ヘッドレスモード
        max_products_per_run: 1回の実行で処理する最大件数
        progress_file: 進捗ファイルパス

    Returns:
        tuple: (アップロード結果リスト, 進捗オブジェクト)
    """
    async with ColorMeImageUploader(
        login_id=login_id,
        password=password,
        headless=headless
    ) as uploader:
        return await uploader.upload_with_resume(
            products,
            max_products_per_run=max_products_per_run,
            progress_file=progress_file
        )


# CLI用のメイン関数
def main():
    """コマンドライン実行用"""
    import argparse

    parser = argparse.ArgumentParser(description="カラーミーショップ画像アップローダー")
    subparsers = parser.add_subparsers(dest="command", help="サブコマンド")

    # 単一画像アップロード
    single_parser = subparsers.add_parser("single", help="単一商品の画像をアップロード")
    single_parser.add_argument("--product-id", type=int, required=True, help="商品ID")
    single_parser.add_argument("--image-url", type=str, required=True, help="画像URL")

    # バッチアップロード（CSVファイルから）
    batch_parser = subparsers.add_parser("batch", help="複数商品の画像を一括アップロード")
    batch_parser.add_argument("--csv", type=str, required=True,
                              help="CSVファイル（product_id,image_url形式）")
    batch_parser.add_argument("--max-per-run", type=int, default=100,
                              help="1回の実行で処理する最大件数（デフォルト: 100）")
    batch_parser.add_argument("--progress-file", type=str,
                              default="data/image_upload_progress.json",
                              help="進捗ファイルパス")

    # 進捗確認
    status_parser = subparsers.add_parser("status", help="進捗状況を確認")
    status_parser.add_argument("--progress-file", type=str,
                               default="data/image_upload_progress.json",
                               help="進捗ファイルパス")

    # 進捗リセット
    reset_parser = subparsers.add_parser("reset", help="進捗をリセット")
    reset_parser.add_argument("--progress-file", type=str,
                              default="data/image_upload_progress.json",
                              help="進捗ファイルパス")

    # 共通オプション
    parser.add_argument("--login-id", type=str, help="ログインID（環境変数 COLORME_LOGIN_ID）")
    parser.add_argument("--password", type=str, help="パスワード（環境変数 COLORME_PASSWORD）")
    parser.add_argument("--no-headless", action="store_true", help="ブラウザを表示する")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ出力")

    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    if args.command == "single":
        # 単一画像アップロード
        result = asyncio.run(upload_single_image(
            product_id=args.product_id,
            image_url=args.image_url,
            login_id=args.login_id,
            password=args.password,
            headless=not args.no_headless
        ))

        if result.success:
            print(f"✅ アップロード成功: {result.uploaded_url}")
        else:
            print(f"❌ アップロード失敗: {result.error_message}")
            exit(1)

    elif args.command == "batch":
        # バッチアップロード
        import csv

        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"❌ CSVファイルが見つかりません: {csv_path}")
            exit(1)

        products = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                product_id = int(row.get('product_id', 0))
                image_url = row.get('image_url', '')
                if product_id and image_url:
                    products.append((product_id, image_url))

        if not products:
            print("❌ 有効な商品データがありません")
            exit(1)

        print(f"📦 {len(products)}件の商品を処理します（最大{args.max_per_run}件/実行）")

        results, progress = asyncio.run(upload_images_with_resume(
            products=products,
            login_id=args.login_id,
            password=args.password,
            headless=not args.no_headless,
            max_products_per_run=args.max_per_run,
            progress_file=Path(args.progress_file)
        ))

        print(f"\n{progress.get_summary()}")

        # 全完了チェック
        if progress.processed_count >= progress.total_products:
            print("✅ すべての商品の処理が完了しました")
        else:
            remaining = progress.total_products - progress.processed_count
            print(f"⏳ 残り{remaining}件 - 次回の実行で継続します")

    elif args.command == "status":
        # 進捗確認
        progress_file = Path(args.progress_file)
        progress = UploadProgress.load(progress_file)

        print("=== 画像アップロード進捗 ===")
        print(f"総商品数: {progress.total_products}件")
        print(f"処理済み: {progress.processed_count}件")
        print(f"  - 成功: {progress.success_count}件")
        print(f"  - 失敗: {progress.failed_count}件")
        print(f"  - スキップ: {progress.skipped_count}件")
        print(f"開始日時: {progress.started_at}")
        print(f"最終更新: {progress.last_updated}")

        if progress.failed_product_ids:
            print(f"\n失敗した商品ID: {progress.failed_product_ids[:20]}...")

    elif args.command == "reset":
        # 進捗リセット
        progress_file = Path(args.progress_file)
        if progress_file.exists():
            progress_file.unlink()
            print(f"✅ 進捗をリセットしました: {progress_file}")
        else:
            print(f"進捗ファイルは存在しません: {progress_file}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
