"""
仕入れ先画像URLの選別ロジック（共通モジュール）

仕入れ先サイト（APMEX等）の画像URLには、関連商品やUI要素（ロゴ・アイコン）
が混入することがある。また、APMEXは商品タイプによってURLサフィックス
（_obv/_rev/_slab/_a など）の意味が変わるため、サフィックス判定は不可能。

シート上の並び順は通常APMEXページの表示順に対応するため、
先頭の数枚（同一商品IDのみ）を採用する方針とする。

利用ケース:
- BU/通常: 1=表, 2=裏, 3=ケース → 1枚不要
- PCGS鑑定: 1=スラブ, 2=表, 3=裏 → スラブ1枚不要
- 記念モデル: 1=箱, 2=コイン, 3=コイン → 箱1枚不要

どのパターンでも 3枚のうち 1〜2枚は不要画像になり得るため、
ユーザー側で手動削除する想定。

使い方:
    from src.image_filter import select_product_images
    filtered = select_product_images(supplier_url, image_urls)
"""

import re
import logging

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_PRODUCT = 3

# UI要素・ロゴ等を除外するパターン（APMEX用、URL小文字で判定）
APMEX_JUNK_PATTERNS = [
    "/content/images/content/",  # ロゴ、アイコン類
    "/content/cms/",  # CMS素材
    "logo",
    "icon",
    "menu-tag",
    "camera",
    "sportsshop",
    "bullioncardimg",
]


def is_valid_product_image_url(url: str) -> bool:
    """
    商品画像URLとして有効か判定（ジャンク除外）

    新しい仕入れ先サイトを追加したらここにルールを追加。
    """
    url_lower = url.lower()

    # APMEX: /images/products/ パスのみ採用
    if "images-apmex.com" in url_lower:
        if "/images/products/" not in url_lower:
            return False
        # 念のためジャンクパターンも除外
        return not any(pat in url_lower for pat in APMEX_JUNK_PATTERNS)

    # Bullionstar: 明らかなUI要素を除外
    if "bullionstar.com" in url_lower:
        excludes = ["/logo", "/icon", "/banner", "/header", "/footer"]
        return not any(ex in url_lower for ex in excludes)

    # その他のドメインはとりあえず全部許可
    return True


def select_product_images(
    supplier_url: str,
    image_urls: list[str],
    max_images: int = MAX_IMAGES_PER_PRODUCT,
) -> list[str]:
    """
    仕入れ先URL・画像URLリストから、商品の主要画像のみを選別する。

    手順:
    1. ジャンク画像（ロゴ・アイコン）を除外
    2. APMEXの場合、仕入れ先URLから商品IDを抽出して同一商品IDの画像のみ採用
       （関連商品の画像を除外）
    3. 先頭 max_images 枚のみ採用

    Args:
        supplier_url: 仕入れ先商品ページURL（例: https://www.apmex.com/product/316824/...）
        image_urls: 画像URLリスト（仕入れ先シートの画像URL列）
        max_images: 採用する最大枚数

    Returns:
        選別後の画像URLリスト（最大 max_images 枚）
    """
    # ステップ1: ジャンク画像を除外
    valid = [u for u in image_urls if u and is_valid_product_image_url(u)]

    # ステップ2: APMEXは商品IDで関連商品を除外
    if "apmex.com" in supplier_url.lower():
        m = re.search(r"/product/(\d+)/", supplier_url)
        if m:
            pid = m.group(1)
            same_product = [u for u in valid if pid in u]
            if same_product:
                valid = same_product
            else:
                logger.warning(f"APMEX商品ID {pid} 一致画像なし: {supplier_url}")

    # ステップ3: 先頭 max_images 枚を採用
    return valid[:max_images]
