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

        全ての画像をPNG形式で保存する（透過対応のため）。

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

            # 全てPNG形式で保存（透過対応）
            if has_alpha:
                if img.mode == 'P':
                    img = img.convert('RGBA')
                elif img.mode == 'LA':
                    img = img.convert('RGBA')
            else:
                # 透過なしの場合もRGBAに変換（将来の透過処理に対応可能）
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')

            temp_file = temp_dir / f"upload_{hash(image_url) & 0xFFFFFFFF}.png"
            img.save(temp_file, format='PNG', optimize=True)
            logger.info(f"    → PNG保存: {temp_file.stat().st_size:,} bytes")

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

        # 初回チェック前に少し待機（アップロード開始を待つ）
        await asyncio.sleep(3)

        while (asyncio.get_event_loop().time() - start_time) < max_wait:
            try:
                # 方法1: pluploadのアップロード中表示を確認
                # アップロード中のプログレスバーやスピナーが消えるのを待つ
                uploading_indicators = await self._page.query_selector_all(
                    '.plupload_progress, .uploading, [class*="upload"][class*="progress"], '
                    '.plupload_file_status:not(.plupload_done), '
                    '.moxie-shim-html5, .plupload_button[style*="disabled"]'
                )

                # プログレス表示が消えたらアップロード完了
                if not uploading_indicators:
                    # 追加確認: 画像プレビューが表示されているか
                    preview_images = await self._page.query_selector_all(
                        '.plupload_file_thumb img, .uploaded-image, '
                        'img[src*="shop-pro.jp"], .image-preview img, '
                        '.product-image-thumbnail img, .thumb img'
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
                            // グローバルのpluploadインスタンスを探す（様々な命名パターン）
                            const uploaderNames = ['uploader', 'imageUploader', 'plu', 'pluploader', 'fileUploader'];
                            for (const name of uploaderNames) {
                                if (typeof window[name] !== 'undefined' && window[name].files) {
                                    const pending = window[name].files.filter(f =>
                                        f.status !== plupload.DONE && f.status !== plupload.FAILED
                                    ).length;
                                    return { pending: pending, total: window[name].files.length, name: name };
                                }
                            }
                            // window上の全オブジェクトからpluploadインスタンスを探す
                            for (const key in window) {
                                try {
                                    if (window[key] && window[key].files && window[key].start && window[key].state !== undefined) {
                                        const pending = window[key].files.filter(f =>
                                            f.status !== plupload.DONE && f.status !== plupload.FAILED
                                        ).length;
                                        return { pending: pending, total: window[key].files.length, name: key };
                                    }
                                } catch (e) {}
                            }
                            return null;
                        }
                    """)
                    if plupload_state:
                        logger.debug(f"  plupload状態: {plupload_state}")
                        if plupload_state.get('pending', 0) == 0 and plupload_state.get('total', 0) > 0:
                            logger.info(f"  → pluploadアップロード完了（{plupload_state.get('total', 0)}ファイル, {plupload_state.get('name', 'unknown')}）")
                            return True
                except Exception as e:
                    logger.debug(f"  plupload状態確認JS例外: {e}")

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

            # 商品画像URLのパターン（複数形式に対応）
            # - imgXX.shop-pro.jp/PA01517/852/product/XXXXX.jpg
            # - imgXX.shop-pro.jp/PA01517/852/product/XXXXX_XXX.jpg（サムネイル付き）
            # - fileXXX.shop-pro.jp/PA01517/852/ファイル名.jpg
            # アイコンや共通画像は除外（/img/new/icons, /img/common など）
            import re
            # より柔軟なパターン: shop-pro.jpの商品画像を広く取得
            product_image_pattern = re.compile(
                r'https?://(img|file)\d+\.shop-pro\.jp/PA\d+/\d+/[^"\'<>\s]+\.(jpg|jpeg|png|gif|webp)',
                re.IGNORECASE
            )

            # 除外パターン（共通アイコン等）
            exclude_patterns = [
                '/img/new/icons',
                '/img/common',
                '/img/layout',
                '/img/button',
                '/img/icon',
                'noimage',
                'no_image',
                'blank.gif',
                'spacer.gif',
            ]

            # img要素のsrc属性から商品画像のみ取得
            img_elements = await self._page.query_selector_all('img[src*="shop-pro.jp"]')
            for img in img_elements:
                src = await img.get_attribute("src")
                if not src:
                    continue

                # 除外パターンをチェック
                is_excluded = any(pattern in src.lower() for pattern in exclude_patterns)
                if is_excluded:
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

            # 追加: 画像プレビュー領域から取得（pluploadアップロード済み画像）
            # カラーミー管理画面の画像表示領域から取得
            preview_selectors = [
                '.product_image_area img[src*="shop-pro.jp"]',
                '.image_list img[src*="shop-pro.jp"]',
                '.uploaded_image img[src*="shop-pro.jp"]',
                '.photo_list img[src*="shop-pro.jp"]',
                '[id*="image"] img[src*="shop-pro.jp"]',
                '[class*="image"] img[src*="shop-pro.jp"]',
                '[class*="photo"] img[src*="shop-pro.jp"]',
            ]
            for selector in preview_selectors:
                try:
                    preview_imgs = await self._page.query_selector_all(selector)
                    for img in preview_imgs:
                        src = await img.get_attribute("src")
                        if not src:
                            continue
                        is_excluded = any(pattern in src.lower() for pattern in exclude_patterns)
                        if is_excluded:
                            continue
                        # サムネイルサイズを除去
                        original_src = src
                        for size in ["/50_50/", "/100_100/", "/200_200/", "/300_300/", "/400_400/"]:
                            if size in original_src:
                                original_src = original_src.replace(size, "/")
                                break
                        if product_image_pattern.match(original_src) and original_src not in urls:
                            urls.append(original_src)
                except Exception:
                    pass

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
                await asyncio.sleep(5)  # ページ読み込み待機を延長

                # 画像ファイル入力を全て探す（plupload）
                file_inputs = await self._page.query_selector_all('input[type="file"]')
                logger.info(f"  ファイル入力フィールド検出: {len(file_inputs)}個")

                # 画像用の入力フィールドをフィルタ
                image_inputs = []
                for fi in file_inputs:
                    accept = await fi.get_attribute("accept")
                    input_id = await fi.get_attribute("id")
                    input_name = await fi.get_attribute("name")
                    logger.debug(f"    input: id={input_id}, name={input_name}, accept={accept}")
                    # 画像を受け付けるinput（video以外）
                    if accept:
                        if ("image" in accept or "jpeg" in accept or "png" in accept) and "video" not in accept:
                            image_inputs.append(fi)

                # 見つからない場合、代替セレクタを試す
                if not image_inputs:
                    logger.info("  代替セレクタで画像入力を検索中...")
                    # plupload用のコンテナを探す
                    plupload_inputs = await self._page.query_selector_all('.plupload input[type="file"], .moxie-shim input[type="file"]')
                    if plupload_inputs:
                        image_inputs = plupload_inputs
                        logger.info(f"  pluploadセレクタで検出: {len(image_inputs)}個")

                if not image_inputs:
                    # デバッグ用にページのHTMLの一部をログ出力
                    try:
                        page_content = await self._page.content()
                        if 'input type="file"' in page_content:
                            logger.warning("  ページにはfile inputが存在しますが、セレクタで検出できませんでした")
                        else:
                            logger.warning("  ページにfile inputが存在しません")
                    except:
                        pass
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
                            # changeイベントを発火してpluploadをトリガー
                            await image_inputs[i].dispatch_event("change")
                            await asyncio.sleep(3)
                    else:
                        # 入力フィールドが1つの場合は複数ファイルを一度にセット
                        logger.info(f"  複数ファイルを一括アップロード中 ({len(temp_paths)}枚)...")
                        file_paths = [str(p) for p in temp_paths]
                        await image_inputs[0].set_input_files(file_paths)
                        # changeイベントを発火してpluploadをトリガー
                        await image_inputs[0].dispatch_event("change")

                    # pluploadのアップロード開始を明示的にトリガー
                    await asyncio.sleep(2)
                    try:
                        await self._page.evaluate("""
                            () => {
                                // pluploadインスタンスを探してアップロード開始
                                if (typeof uploader !== 'undefined' && uploader.start) {
                                    uploader.start();
                                    return 'started uploader';
                                }
                                if (typeof imageUploader !== 'undefined' && imageUploader.start) {
                                    imageUploader.start();
                                    return 'started imageUploader';
                                }
                                // 全てのpluploadインスタンスを探す
                                if (typeof plupload !== 'undefined') {
                                    for (var key in window) {
                                        if (window[key] && window[key].start && window[key].files) {
                                            window[key].start();
                                            return 'started ' + key;
                                        }
                                    }
                                }
                                return 'no uploader found';
                            }
                        """)
                        logger.info("  pluploadアップロード開始トリガー実行")
                    except Exception as e:
                        logger.debug(f"  plupload開始トリガーエラー（無視）: {e}")

                    # pluploadの非同期アップロード完了を待機
                    # GitHub Actions環境では時間がかかるため、長めのタイムアウトを設定
                    plupload_timeout = max(60.0, wait_after_upload * 4 + len(temp_paths) * 10)
                    logger.info(f"  pluploadアップロード完了待機中（最大{plupload_timeout:.0f}秒）...")
                    plupload_completed = await self._wait_for_plupload_complete(plupload_timeout)

                    if not plupload_completed:
                        logger.warning("  pluploadがタイムアウトしましたが、保存を試みます...")
                        # 追加の待機時間（念のため）
                        await asyncio.sleep(10)

                    # 保存ボタン実行（JavaScript経由）
                    logger.info("  保存ボタン実行中...")
                    await self._page.evaluate("jf_Submit('UPD')")

                    # 保存完了待機（サーバー処理時間を考慮して長めに）
                    await asyncio.sleep(5)
                    await self._page.wait_for_load_state("networkidle")
                    await asyncio.sleep(5)  # 追加の待機（画像処理完了待ち）

                logger.info(f"  → 商品ID {product_id}: {uploaded_count}枚アップロード成功")

                # ページから直接画像URLを取得（リトライ付き）
                uploaded_urls = []
                for page_attempt in range(3):
                    # 商品編集ページをリロードして最新の状態を取得
                    edit_url = f"{self.ADMIN_URL}?mode=product_edt&type=UPD&product_id={product_id}"
                    await self._page.goto(edit_url, wait_until="networkidle")
                    await asyncio.sleep(3)

                    uploaded_urls = await self._extract_image_urls_from_page(product_id)
                    if len(uploaded_urls) >= uploaded_count:
                        # 期待した枚数が取得できた
                        break
                    logger.info(f"  画像URL取得: {len(uploaded_urls)}/{uploaded_count}枚 (リトライ {page_attempt + 1}/3)")
                    await asyncio.sleep(3)

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

                if len(uploaded_urls) < uploaded_count:
                    logger.warning(f"  警告: アップロード{uploaded_count}枚に対し、取得できたURL{len(uploaded_urls)}枚")

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

    async def update_seo_fields(
        self,
        product_id: int,
        page_title: str = "",
        meta_description: str = "",
        meta_keywords: str = "",
        max_retries: int = 2
    ) -> tuple[bool, str]:
        """
        商品のSEO項目を更新する

        カラーミーAPIではSEO項目（title, meta_description, meta_keywords）を
        設定できないため、管理画面からPlaywrightで更新する。

        Args:
            product_id: 商品ID
            page_title: ページタイトル（SEO用）
            meta_description: メタディスクリプション
            meta_keywords: メタキーワード
            max_retries: リトライ回数

        Returns:
            tuple[bool, str]: (成功フラグ, エラーメッセージ)
        """
        if not page_title and not meta_description and not meta_keywords:
            return True, "SEO項目が指定されていません"

        if not self._logged_in:
            if not await self.login():
                return False, "ログイン失敗"

        logger.info(f"商品ID {product_id} のSEO項目を更新中...")

        for attempt in range(max_retries):
            try:
                # 商品編集ページに移動
                edit_url = f"{self.ADMIN_URL}?mode=product_edt&type=UPD&product_id={product_id}"
                await self._page.goto(edit_url, wait_until="networkidle")
                await asyncio.sleep(3)

                # SEOセクションを展開（デフォルトで非表示の場合がある）
                await self._page.evaluate("""
                    () => {
                        const metaTitle = document.querySelector('input[name="meta_title"]');
                        if (!metaTitle) return;

                        let parent = metaTitle.parentElement;
                        while (parent && parent !== document.body) {
                            const style = window.getComputedStyle(parent);
                            if (style.display === 'none') {
                                parent.style.display = 'block';
                            }
                            parent = parent.parentElement;
                        }
                    }
                """)
                await asyncio.sleep(1)

                updated_fields = []

                # ページタイトル（meta_title）
                if page_title:
                    title_input = await self._page.query_selector('input[name="meta_title"]')
                    if title_input:
                        await title_input.fill(page_title)
                        updated_fields.append("meta_title")
                        logger.debug(f"  meta_title設定: {page_title[:50]}...")

                # メタディスクリプション（meta_description）
                if meta_description:
                    desc_input = await self._page.query_selector('input[name="meta_description"]')
                    if desc_input:
                        await desc_input.fill(meta_description)
                        updated_fields.append("meta_description")
                        logger.debug(f"  meta_description設定: {meta_description[:50]}...")

                # メタキーワード（meta_keywords）
                if meta_keywords:
                    keywords_input = await self._page.query_selector('input[name="meta_keywords"]')
                    if keywords_input:
                        await keywords_input.fill(meta_keywords)
                        updated_fields.append("meta_keywords")
                        logger.debug(f"  meta_keywords設定: {meta_keywords[:50]}...")

                if not updated_fields:
                    # SEOフィールドが見つからなかった場合、別のセレクタを試す
                    logger.warning(f"  SEOフィールドが見つかりません（試行 {attempt + 1}）")

                    # デバッグ: ページ内のinput要素を確認
                    all_inputs = await self._page.query_selector_all('input[type="text"], textarea')
                    for inp in all_inputs[:20]:
                        name = await inp.get_attribute("name")
                        placeholder = await inp.get_attribute("placeholder")
                        logger.debug(f"    input: name={name}, placeholder={placeholder}")

                    if attempt < max_retries - 1:
                        await asyncio.sleep(3)
                        continue
                    return False, "SEOフィールドが見つかりません"

                # 保存ボタン実行（JavaScript経由）
                logger.info(f"  SEO項目更新: {', '.join(updated_fields)}")
                await self._page.evaluate("jf_Submit('UPD')")

                # 保存完了待機
                await asyncio.sleep(3)
                await self._page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)

                logger.info(f"  → 商品ID {product_id}: SEO更新成功")
                return True, ""

            except Exception as e:
                error_msg = str(e)
                logger.warning(f"  → 試行 {attempt + 1}/{max_retries} 失敗: {error_msg}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                else:
                    return False, error_msg

        return False, "リトライ上限到達"

    async def update_delivery_charge(
        self,
        product_id: int,
        delivery_charge: int,
        max_retries: int = 2
    ) -> tuple[bool, str]:
        """
        商品の個別送料を更新する

        カラーミーAPIでは個別送料（delivery_charge）を設定できないため、
        管理画面からPlaywrightで更新する。

        Args:
            product_id: 商品ID
            delivery_charge: 個別送料（円）
            max_retries: リトライ回数

        Returns:
            tuple[bool, str]: (成功フラグ, エラーメッセージ)
        """
        if delivery_charge < 0:
            return True, "個別送料が指定されていません"

        if not self._logged_in:
            if not await self.login():
                return False, "ログイン失敗"

        logger.info(f"商品ID {product_id} の個別送料を更新中... ({delivery_charge}円)")

        for attempt in range(max_retries):
            try:
                # 商品編集ページに移動
                edit_url = f"{self.ADMIN_URL}?mode=product_edt&type=UPD&product_id={product_id}"
                await self._page.goto(edit_url, wait_until="networkidle")
                await asyncio.sleep(3)

                # 個別送料フィールドを探す
                # カラーミー管理画面では "postage" という名前
                delivery_input = await self._page.query_selector('input[name="postage"]')

                if not delivery_input:
                    # 代替セレクタを試す
                    delivery_input = await self._page.query_selector('input[name="delivery_charge"]')

                if not delivery_input:
                    delivery_input = await self._page.query_selector('input[name="individual_shipping_fee"]')

                if not delivery_input:
                    # デバッグ: ページ内のinput要素を確認
                    logger.warning(f"  個別送料フィールドが見つかりません（試行 {attempt + 1}）")
                    all_inputs = await self._page.query_selector_all('input[type="text"], input[type="number"]')
                    for inp in all_inputs[:30]:
                        name = await inp.get_attribute("name")
                        inp_id = await inp.get_attribute("id")
                        placeholder = await inp.get_attribute("placeholder")
                        logger.debug(f"    input: name={name}, id={inp_id}, placeholder={placeholder}")

                    if attempt < max_retries - 1:
                        await asyncio.sleep(3)
                        continue
                    return False, "個別送料フィールドが見つかりません"

                # 個別送料を設定
                await delivery_input.fill(str(delivery_charge))
                logger.info(f"  個別送料設定: {delivery_charge}円")

                # 保存ボタン実行（JavaScript経由）
                await self._page.evaluate("jf_Submit('UPD')")

                # 保存完了待機
                await asyncio.sleep(3)
                await self._page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)

                logger.info(f"  → 商品ID {product_id}: 個別送料更新成功 ({delivery_charge}円)")
                return True, ""

            except Exception as e:
                error_msg = str(e)
                logger.warning(f"  → 試行 {attempt + 1}/{max_retries} 失敗: {error_msg}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                else:
                    return False, error_msg

        return False, "リトライ上限到達"

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
