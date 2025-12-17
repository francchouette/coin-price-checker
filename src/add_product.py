"""
新商品情報自動入力スクリプト

カラーミー商品管理シートで、D列（取得元URL）が入力されていて
B列（商品名）が空の行を検出し、自動で情報を埋める。

使用方法:
    # ドライラン（確認のみ）
    python -m src.add_product

    # 実際に処理を実行
    python -m src.add_product --execute
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from .colorme import ColorMeClient, ColorMeProduct
from .config import Config
from .exchange_rate import ExchangeRateClient
from .spreadsheet import SpreadsheetClient

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# 商品説明生成用プロンプトテンプレート
DESCRIPTION_PROMPT = """あなたは貴金属コイン・地金のECサイトの商品説明を作成するエキスパートです。

以下の商品情報を元に、日本語の商品説明を2つ作成してください。

## 入力情報
- 商品名: {product_name}
- 価格: {price} {currency}
- 仕入れサイト説明: {source_description}
- 仕入れサイト仕様: {source_specs}

## 出力形式

### 商品説明（詳細）
以下の形式で作成してください：

【品位】　（純度情報、例: 約99.99％K24純金）
【重量】　（重量情報、例: １オンス（31.1g））
【直径】　（サイズ情報、あれば）

<strong><span style="font-size:large;">キャッチコピー（15-25文字）</span></strong>

本文（200-300文字）:
- 発行年・発行元の説明
- デザインの特徴（表面・裏面）
- 品質・純度の特徴
- 投資価値・メリット

### 簡易説明
1-2文（80-120文字）で以下を含める：
- 製造国・素材
- 重量・デザインの特徴
- 投資メリット

## 注意事項
- HTMLタグは商品説明のキャッチコピー部分のみ使用
- 専門用語は適切に使用（地金型金貨、純金、K24など）
- 日本の投資家向けに魅力的な表現を使用

JSON形式で出力してください:
{{
    "description": "商品説明（詳細）",
    "simple_description": "簡易説明"
}}
"""


class ProductScraper:
    """仕入れサイトから商品情報をスクレイピングするクラス"""

    def __init__(self):
        self.browser = None
        self.context = None
        self.playwright = None

    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def scrape(self, url: str) -> Optional[dict]:
        """
        URLから商品情報を取得する

        Returns:
            dict: {
                "name": 商品名,
                "price": 価格,
                "currency": 通貨,
                "in_stock": 在庫有無,
                "description": 説明,
                "specs": 仕様,
                "image_urls": 画像URLリスト,
                "sku": SKU/型番
            }
        """
        domain = urlparse(url).netloc.lower()

        if "apmex.com" in domain:
            return self._scrape_apmex(url)
        elif "britanniacoincompany.com" in domain:
            return self._scrape_britannia(url)
        elif "bullionstar.com" in domain:
            return self._scrape_bullionstar(url)
        else:
            logger.warning(f"未対応のサイト: {domain}")
            return None

    def _scrape_apmex(self, url: str) -> Optional[dict]:
        """APMEXから商品情報を取得"""
        try:
            page = self.context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            result = {
                "name": "",
                "price": 0.0,
                "currency": "USD",
                "in_stock": False,
                "description": "",
                "specs": "",
                "image_urls": [],
                "sku": ""
            }

            # 商品名
            try:
                name_elem = page.query_selector("h1.product-title, h1[itemprop='name'], h1")
                if name_elem:
                    result["name"] = name_elem.inner_text().strip()
            except Exception:
                pass

            # 価格
            try:
                price_elem = page.query_selector("[data-price], .product-price, .price-value, .mod-product-stats__current-price")
                if price_elem:
                    price_text = price_elem.inner_text()
                    price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                    if price_match:
                        result["price"] = float(price_match.group().replace(',', ''))
            except Exception:
                pass

            # 在庫状況
            try:
                # APMEXでは「Add to Cart」ボタンがあれば在庫あり
                add_to_cart = page.query_selector("button.add-to-cart, .btn-add-to-cart, [data-action='add-to-cart']")
                result["in_stock"] = add_to_cart is not None
            except Exception:
                pass

            # 説明
            try:
                desc_elem = page.query_selector(".product-description, #product-description, [itemprop='description'], .mod-product-description")
                if desc_elem:
                    result["description"] = desc_elem.inner_text().strip()
            except Exception:
                pass

            # 仕様
            try:
                specs_elem = page.query_selector(".product-specs, .specifications, .product-details, .mod-product-specs")
                if specs_elem:
                    result["specs"] = specs_elem.inner_text().strip()
            except Exception:
                pass

            # 画像URL
            try:
                img_elems = page.query_selector_all(".product-image img, .gallery img, [itemprop='image'], .mod-product-gallery img")
                for img in img_elems[:10]:
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if src and src.startswith("http") and src not in result["image_urls"]:
                        result["image_urls"].append(src)
            except Exception:
                pass

            # SKU
            try:
                sku_elem = page.query_selector("[itemprop='sku'], .sku, .product-sku")
                if sku_elem:
                    result["sku"] = sku_elem.inner_text().strip()
            except Exception:
                pass

            page.close()
            return result

        except Exception as e:
            logger.error(f"APMEXスクレイピングエラー: {e}")
            return None

    def _scrape_britannia(self, url: str) -> Optional[dict]:
        """Britanniaから商品情報を取得"""
        try:
            page = self.context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)

            result = {
                "name": "",
                "price": 0.0,
                "currency": "GBP",
                "in_stock": False,
                "description": "",
                "specs": "",
                "image_urls": [],
                "sku": ""
            }

            # 商品名
            try:
                name_elem = page.query_selector("h1.product-title, h1")
                if name_elem:
                    result["name"] = name_elem.inner_text().strip()
            except Exception:
                pass

            # 価格
            try:
                price_elem = page.query_selector(".price, .product-price")
                if price_elem:
                    price_text = price_elem.inner_text()
                    price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                    if price_match:
                        result["price"] = float(price_match.group().replace(',', ''))
            except Exception:
                pass

            # 在庫状況
            try:
                stock_elem = page.query_selector(".stock-status, .availability")
                if stock_elem:
                    stock_text = stock_elem.inner_text().lower()
                    result["in_stock"] = "in stock" in stock_text or "available" in stock_text
                else:
                    buy_btn = page.query_selector("button[type='submit'], .add-to-cart")
                    result["in_stock"] = buy_btn is not None
            except Exception:
                pass

            # 説明
            try:
                desc_elem = page.query_selector(".product-description, .description")
                if desc_elem:
                    result["description"] = desc_elem.inner_text().strip()
            except Exception:
                pass

            # 画像URL
            try:
                img_elems = page.query_selector_all(".product-image img, .gallery img")
                for img in img_elems[:10]:
                    src = img.get_attribute("src") or img.get_attribute("data-src")
                    if src:
                        if not src.startswith("http"):
                            src = f"https://www.britanniacoincompany.com{src}"
                        if src not in result["image_urls"]:
                            result["image_urls"].append(src)
            except Exception:
                pass

            page.close()
            return result

        except Exception as e:
            logger.error(f"Britanniaスクレイピングエラー: {e}")
            return None

    def _scrape_bullionstar(self, url: str) -> Optional[dict]:
        """BullionStarから商品情報を取得"""
        try:
            page = self.context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            result = {
                "name": "",
                "price": 0.0,
                "currency": "SGD",  # シンガポールドル
                "in_stock": False,
                "description": "",
                "specs": "",
                "image_urls": [],
                "sku": ""
            }

            # 商品キーワードは商品名取得後に設定
            product_keywords = []

            # 商品名
            try:
                name_elem = page.query_selector("h1.product-name, h1[itemprop='name'], h1")
                if name_elem:
                    result["name"] = name_elem.inner_text().strip()
                    # 商品名からキーワードを抽出（画像フィルタリング用）
                    # 例: "2024 1 oz Silver Dragon Round" → ["2024", "silver", "dragon", "round"]
                    name_words = re.findall(r'[a-zA-Z0-9]+', result["name"].lower())
                    # 3文字以上の単語をキーワードとして使用（一般的な単語を除外）
                    common_words = {"the", "and", "for", "with", "from", "new", "buy"}
                    product_keywords = [w for w in name_words if len(w) >= 3 and w not in common_words]
                    logger.info(f"  商品キーワード: {product_keywords[:10]}")
            except Exception:
                pass

            # 価格（SGD）
            try:
                price_elem = page.query_selector(".product-price, .price, [itemprop='price'], .current-price")
                if price_elem:
                    price_text = price_elem.inner_text()
                    # SGD/USD/EURなどの通貨記号を除去
                    price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                    if price_match:
                        result["price"] = float(price_match.group().replace(',', ''))
                    # 通貨判定
                    if 'USD' in price_text or '$' in price_text:
                        result["currency"] = "USD"
                    elif 'EUR' in price_text or '€' in price_text:
                        result["currency"] = "EUR"
                    elif 'SGD' in price_text or 'S$' in price_text:
                        result["currency"] = "SGD"
            except Exception:
                pass

            # 在庫状況
            try:
                # 「Add to Cart」ボタンがあれば在庫あり
                add_to_cart = page.query_selector("button.add-to-cart, .btn-add-to-cart, button[type='submit']:has-text('Add'), .add-to-cart-button")
                if add_to_cart:
                    result["in_stock"] = True
                else:
                    # 在庫状況テキストを確認
                    stock_elem = page.query_selector(".stock-status, .availability, [itemprop='availability']")
                    if stock_elem:
                        stock_text = stock_elem.inner_text().lower()
                        result["in_stock"] = "in stock" in stock_text or "available" in stock_text
            except Exception:
                pass

            # 説明
            try:
                desc_elem = page.query_selector(".product-description, #product-description, [itemprop='description'], .description")
                if desc_elem:
                    result["description"] = desc_elem.inner_text().strip()
            except Exception:
                pass

            # 仕様
            try:
                specs_elem = page.query_selector(".product-specs, .specifications, .product-details, .specs")
                if specs_elem:
                    result["specs"] = specs_elem.inner_text().strip()
            except Exception:
                pass

            # 画像URL
            try:
                # 関連商品セクションを除外して商品画像を取得
                # 除外するセクションのセレクタ
                exclude_selectors = [
                    "[class*='customer']",      # customers-who-viewed等
                    "[class*='related']",       # related products
                    "[class*='recommend']",     # recommended
                    "[class*='blog']",          # blog posts
                    "[class*='search-result']", # search results
                    "[class*='footer']",        # footer
                    "[class*='header']",        # header
                    "[class*='nav']",           # navigation
                ]

                # 除外セクション内の画像URLを収集
                excluded_urls = set()
                for selector in exclude_selectors:
                    try:
                        sections = page.query_selector_all(selector)
                        for section in sections:
                            imgs = section.query_selector_all("img[src*='/files/']")
                            for img in imgs:
                                src = img.get_attribute("src")
                                if src:
                                    excluded_urls.add(src)
                    except Exception:
                        pass

                # 全ての/files/画像を取得（除外セクション外のもの）
                all_imgs = page.query_selector_all("img[src*='/files/']")
                candidate_urls = []
                first_image_pattern = None

                for img in all_imgs:
                    src = img.get_attribute("src")
                    if not src or src in excluded_urls:
                        continue

                    # 最初の商品画像からパターンを学習
                    if first_image_pattern is None and "/files/" in src:
                        # ファイル名から商品識別パターンを抽出
                        # 例: coin-silver-bstar-dragonround2024 から "dragonround2024" や "bstar" を取得
                        filename = src.split("/")[-1]
                        # 数字プレフィックス（サイズ）を除去
                        filename_clean = re.sub(r'^\d+_\d+_', '', filename)
                        # ファイル名のパターンを保存
                        first_image_pattern = filename_clean.split("-")[0:4]  # 最初の4セグメント
                        logger.info(f"  画像パターン: {first_image_pattern}")

                    # サムネイルサイズを高解像度に変換
                    high_res_url = re.sub(r'/(\d+)_(\d+)_', '/1200_1200_', src)
                    if high_res_url not in candidate_urls:
                        candidate_urls.append(high_res_url)

                # 同じパターンの画像のみをフィルタリング
                if first_image_pattern and len(candidate_urls) > 2:
                    filtered_urls = []
                    pattern_str = "-".join(first_image_pattern[:3])  # 最初の3セグメントでマッチ
                    for url in candidate_urls:
                        if pattern_str in url.lower():
                            filtered_urls.append(url)
                    if filtered_urls:
                        candidate_urls = filtered_urls

                # 重複を除去して追加
                for url in candidate_urls[:10]:
                    if url not in result["image_urls"]:
                        result["image_urls"].append(url)

                logger.info(f"  取得画像: {len(result['image_urls'])}枚")
            except Exception as e:
                logger.warning(f"画像取得エラー: {e}")

            # SKU
            try:
                sku_elem = page.query_selector("[itemprop='sku'], .sku, .product-sku, .product-code")
                if sku_elem:
                    result["sku"] = sku_elem.inner_text().strip()
            except Exception:
                pass

            page.close()
            return result

        except Exception as e:
            logger.error(f"BullionStarスクレイピングエラー: {e}")
            return None


class DescriptionGenerator:
    """商品説明をAIで生成するクラス"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.client = None
        if self.api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                logger.warning("anthropicライブラリがインストールされていません")

    def generate(self, product_info: dict) -> tuple[str, str]:
        """
        商品説明を生成する

        Args:
            product_info: スクレイピングで取得した商品情報

        Returns:
            tuple[str, str]: (商品説明, 簡易説明)
        """
        if not self.client:
            logger.warning("ANTHROPIC_API_KEYが設定されていないか、ライブラリがありません。説明は空になります。")
            return "", ""

        prompt = DESCRIPTION_PROMPT.format(
            product_name=product_info.get("name", ""),
            price=product_info.get("price", 0),
            currency=product_info.get("currency", "USD"),
            source_description=product_info.get("description", ""),
            source_specs=product_info.get("specs", "")
        )

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                data = json.loads(json_match.group())
                return data.get("description", ""), data.get("simple_description", "")

        except Exception as e:
            logger.error(f"説明生成エラー: {e}")

        return "", ""


class CategoryDetector:
    """商品名からカテゴリーを自動判定するクラス"""

    # 素材キーワードマッピング
    METAL_KEYWORDS = {
        "gold": ["gold", "金貨", "ゴールド", "金", "au"],
        "silver": ["silver", "銀貨", "シルバー", "銀", "ag"],
        "platinum": ["platinum", "プラチナ", "白金", "pt"],
        "palladium": ["palladium", "パラジウム", "pd"],
    }

    # 商品タイプキーワード
    TYPE_KEYWORDS = {
        "coin": ["coin", "コイン", "貨", "round", "ラウンド"],
        "bar": ["bar", "ingot", "インゴット", "バー", "地金"],
    }

    def __init__(self, categories: list[dict], groups: list[dict]):
        self.categories = categories
        self.groups = groups
        # カテゴリー名をログ出力（デバッグ用）
        for cat in categories:
            logger.info(f"  カテゴリー: {cat.get('name_big', '')} / {cat.get('name_small', '')} (ID: {cat.get('id_big', 0)})")

    def detect(self, product_name: str) -> tuple[int, int, list[int]]:
        """
        商品名からカテゴリーIDとグループIDを判定

        Returns:
            tuple[int, int, list[int]]: (大カテゴリーID, 小カテゴリーID, グループIDリスト)
        """
        name_lower = product_name.lower()

        category_id_big = 0
        category_id_small = 0
        group_ids = []

        # 1. 商品名から素材タイプを判定
        detected_metal = None
        for metal, keywords in self.METAL_KEYWORDS.items():
            if any(kw in name_lower for kw in keywords):
                detected_metal = metal
                break

        # 2. 商品名から商品タイプを判定
        detected_type = None
        for ptype, keywords in self.TYPE_KEYWORDS.items():
            if any(kw in name_lower for kw in keywords):
                detected_type = ptype
                break

        logger.info(f"  判定: 素材={detected_metal}, タイプ={detected_type}")

        # 3. カテゴリーマッチング（より柔軟に）
        best_match = None
        best_score = 0

        for cat in self.categories:
            cat_name = (cat.get("name_big", "") + cat.get("name_small", "")).lower()
            score = 0

            # 素材マッチング
            if detected_metal:
                for kw in self.METAL_KEYWORDS[detected_metal]:
                    if kw in cat_name:
                        score += 10
                        break

            # タイプマッチング
            if detected_type:
                for kw in self.TYPE_KEYWORDS[detected_type]:
                    if kw in cat_name:
                        score += 5
                        break

            if score > best_score:
                best_score = score
                best_match = cat

        # 4. マッチしたカテゴリーがあれば使用、なければ最初のカテゴリーをデフォルト
        if best_match and best_score > 0:
            category_id_big = best_match.get("id_big", 0)
            category_id_small = best_match.get("id_small", 0)
        elif self.categories:
            # デフォルト: 最初のカテゴリー
            category_id_big = self.categories[0].get("id_big", 0)
            category_id_small = self.categories[0].get("id_small", 0)
            logger.info(f"  カテゴリーマッチなし、デフォルト使用: {category_id_big}")

        # 5. グループ判定（商品名のキーワードとグループ名でマッチング）
        for grp in self.groups:
            grp_name = grp.get("name", "").lower()
            if not grp_name:
                continue

            # グループ名の単語と商品名をマッチング
            grp_words = [w for w in grp_name.replace("-", " ").split() if len(w) >= 2]
            for word in grp_words:
                if word in name_lower:
                    grp_id = grp.get("id", 0)
                    if grp_id and grp_id not in group_ids:
                        group_ids.append(grp_id)
                    break

        return category_id_big, category_id_small, group_ids


class JapaneseProductNameGenerator:
    """英語の商品名から日本語の商品名を生成するクラス"""

    # 国名マッピング
    COUNTRY_MAP = {
        "singapore": "シンガポール",
        "usa": "アメリカ",
        "us": "アメリカ",
        "united states": "アメリカ",
        "america": "アメリカ",
        "uk": "イギリス",
        "united kingdom": "イギリス",
        "britain": "イギリス",
        "british": "イギリス",
        "canada": "カナダ",
        "canadian": "カナダ",
        "australia": "オーストラリア",
        "australian": "オーストラリア",
        "austria": "オーストリア",
        "austrian": "オーストリア",
        "china": "中国",
        "chinese": "中国",
        "south africa": "南アフリカ",
        "switzerland": "スイス",
        "swiss": "スイス",
        "germany": "ドイツ",
        "german": "ドイツ",
        "turkey": "トルコ",
        "turkish": "トルコ",
        "mexico": "メキシコ",
        "mexican": "メキシコ",
    }

    # シリーズ名マッピング
    SERIES_MAP = {
        "dragon": "ドラゴン",
        "eagle": "イーグル",
        "britannia": "ブリタニア",
        "maple leaf": "メイプルリーフ",
        "maple": "メイプルリーフ",
        "kangaroo": "カンガルー",
        "koala": "コアラ",
        "kookaburra": "カワセミ",
        "panda": "パンダ",
        "philharmonic": "ウィーン",
        "vienna": "ウィーン",
        "krugerrand": "クルーガーランド",
        "buffalo": "バッファロー",
        "libertad": "リベルタード",
        "lunar": "干支",
        "queen's beast": "クイーンズビースト",
        "queens beast": "クイーンズビースト",
        "tudor beast": "チューダービースト",
        "royal arms": "ロイヤルアームズ",
    }

    # メーカー名マッピング
    MAKER_MAP = {
        "pamp": "PAMP",
        "valcambi": "ヴァルカンビ",
        "nadir": "ナディール",
        "9fine mint": "9ファインミント",
        "perth mint": "パースミント",
        "royal mint": "ロイヤルミント",
        "bullionstar": "ブリオンスター",
    }

    # 素材マッピング
    METAL_MAP = {
        "gold": ("金貨", "ゴールド"),
        "silver": ("銀貨", "シルバー"),
        "platinum": ("プラチナ貨", "プラチナ"),
        "palladium": ("パラジウム貨", "パラジウム"),
    }

    def generate(self, product_info: dict, quantity: int = 1) -> str:
        """
        英語の商品情報から日本語の商品名を生成する

        Args:
            product_info: スクレイピングで取得した商品情報
            quantity: 枚数（E列から取得）

        Returns:
            str: 日本語の商品名
        """
        name = product_info.get("name", "")
        specs = product_info.get("specs", "")
        description = product_info.get("description", "")
        full_text = f"{name} {specs} {description}".lower()

        # 年号を抽出
        year = self._extract_year(full_text)

        # 重量を抽出
        weight = self._extract_weight(full_text)

        # 素材を判定
        metal_type, is_ingot = self._detect_metal_and_type(full_text)

        # 国名を抽出
        country = self._extract_country(full_text)

        # シリーズ名またはメーカー名を抽出
        series_or_maker = self._extract_series_or_maker(full_text, is_ingot)

        # 枚数の単位
        unit = "本" if is_ingot else "枚"

        if is_ingot:
            # インゴットの命名規則
            # [メーカー名] [国名] [重量] [種類]インゴット 新品未使用【[個数]】
            ingot_type = "ゴールドインゴット" if "gold" in metal_type else "シルバーインゴット"
            if "platinum" in metal_type:
                ingot_type = "プラチナインゴット"

            parts = []
            if series_or_maker:
                parts.append(series_or_maker)
            if country:
                parts.append(country)
            if weight:
                parts.append(weight)
            parts.append(ingot_type)
            parts.append("新品未使用")
            parts.append(f"【{quantity}{unit}】")

            return " ".join(parts)
        else:
            # コインの命名規則
            # [年号] [シリーズ名] [国名] [額面] [重量] 新品未使用 [種類] 【[枚数]】 ([付属品])
            coin_type = self.METAL_MAP.get(metal_type, ("地金型銀貨", "シルバー"))[0]
            coin_type = f"地金型{coin_type}"

            parts = []
            if year:
                parts.append(year)
            if series_or_maker:
                parts.append(series_or_maker)
            if country:
                parts.append(country)
            if weight:
                parts.append(weight)
            parts.append("新品未使用")
            parts.append(coin_type)
            parts.append(f"【{quantity}{unit}】")
            parts.append("(コインケース付)")

            return " ".join(parts)

    def _extract_year(self, text: str) -> str:
        """年号を抽出"""
        import re
        # 2020-2030の範囲で年号を検索
        match = re.search(r'\b(20[2-3][0-9])\b', text)
        if match:
            return match.group(1)
        return ""

    def _extract_weight(self, text: str) -> str:
        """重量を抽出して日本語形式に変換"""
        import re

        # オンス表記
        oz_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:oz|ounce)', text)
        if oz_match:
            oz_val = float(oz_match.group(1))
            if oz_val == 1:
                return "1オンス"
            elif oz_val == 0.5:
                return "1/2オンス"
            elif oz_val == 0.25:
                return "1/4オンス"
            elif oz_val == 0.1:
                return "1/10オンス"
            else:
                return f"{oz_val}オンス"

        # グラム表記
        g_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:g|gram)(?:s)?(?!\w)', text)
        if g_match:
            g_val = g_match.group(1)
            return f"{g_val}g"

        # kg表記
        kg_match = re.search(r'(\d+(?:\.\d+)?)\s*kg', text)
        if kg_match:
            kg_val = kg_match.group(1)
            return f"{kg_val}kg"

        return ""

    def _detect_metal_and_type(self, text: str) -> tuple[str, bool]:
        """素材とインゴットかどうかを判定"""
        is_ingot = any(kw in text for kw in ["ingot", "bar", "インゴット", "バー"])

        if "gold" in text or "金" in text:
            return "gold", is_ingot
        elif "silver" in text or "銀" in text:
            return "silver", is_ingot
        elif "platinum" in text or "プラチナ" in text:
            return "platinum", is_ingot
        elif "palladium" in text:
            return "palladium", is_ingot

        return "silver", is_ingot  # デフォルト

    def _extract_country(self, text: str) -> str:
        """国名を抽出"""
        for eng, jpn in self.COUNTRY_MAP.items():
            if eng in text:
                return jpn
        return ""

    def _extract_series_or_maker(self, text: str, is_ingot: bool) -> str:
        """シリーズ名またはメーカー名を抽出"""
        if is_ingot:
            for eng, jpn in self.MAKER_MAP.items():
                if eng in text:
                    return jpn
        else:
            for eng, jpn in self.SERIES_MAP.items():
                if eng in text:
                    return jpn
        return ""


def get_incomplete_rows(sheet_client: SpreadsheetClient) -> list[dict]:
    """
    D列（取得元URL）が入力されていて、B列（商品名）が空の行を取得する
    AC列（同期モード）が「取得のみ」または「更新」の行が対象

    同期モード:
        - 「取得のみ」: スプレッドシートのみ更新（カラーミー登録なし）
        - 「更新」: カラーミーへ登録
        - 空または他の値: スキップ

    Returns:
        list[dict]: 未処理行のリスト
            - row_num: 行番号
            - source_url: 取得元URL
            - quantity: 枚数
            - margin_rate: マージン率
            - shipping: 送料（R列）
            - sync_mode: 同期モード（AC列）
    """
    if not sheet_client._spreadsheet:
        return []

    try:
        sheet = sheet_client._spreadsheet.worksheet(Config.SHEET_COLORME)
        all_data = sheet.get_all_values()

        incomplete_rows = []
        for i, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
            if len(row) >= 4:
                source_url = row[3].strip() if len(row) > 3 else ""  # D列
                product_name = row[1].strip() if len(row) > 1 else ""  # B列
                sync_mode = row[28].strip() if len(row) > 28 else ""  # AC列（index 28）

                # D列にURLがあり、B列が空の行を検出
                # AC列が「ドラフト作成」「取得のみ」「更新」「新規登録」の場合のみ処理
                if source_url and not product_name:
                    if sync_mode not in ["ドラフト作成", "取得のみ", "更新", "新規登録"]:
                        continue  # 空や他の値はスキップ

                    # 枚数（E列）
                    quantity = 1
                    if len(row) > 4 and row[4].strip():
                        try:
                            quantity = int(row[4].strip())
                        except ValueError:
                            pass

                    # マージン率（F列）
                    margin_rate = 1.1
                    if len(row) > 5 and row[5].strip():
                        try:
                            margin_rate = float(row[5].strip())
                        except ValueError:
                            pass

                    # 送料（R列、index 17）
                    shipping = 0
                    if len(row) > 17 and row[17].strip():
                        try:
                            shipping = int(float(row[17].strip().replace(',', '')))
                        except ValueError:
                            pass

                    # 既存の通貨（N列、index 13）
                    existing_currency = ""
                    if len(row) > 13 and row[13].strip():
                        existing_currency = row[13].strip()

                    # 既存のカテゴリーID（AE列、index 30）
                    existing_category_id = 0
                    if len(row) > 30 and row[30].strip():
                        try:
                            existing_category_id = int(row[30].strip())
                        except ValueError:
                            pass

                    # 既存のサブカテゴリーID（AF列、index 31）
                    existing_subcategory_id = 0
                    if len(row) > 31 and row[31].strip():
                        try:
                            existing_subcategory_id = int(row[31].strip())
                        except ValueError:
                            pass

                    incomplete_rows.append({
                        "row_num": i,
                        "source_url": source_url,
                        "quantity": quantity,
                        "margin_rate": margin_rate,
                        "shipping": shipping,
                        "sync_mode": sync_mode,
                        "existing_currency": existing_currency,
                        "existing_category_id": existing_category_id,
                        "existing_subcategory_id": existing_subcategory_id
                    })

        logger.info(f"未処理行を検出: {len(incomplete_rows)}件")
        return incomplete_rows

    except Exception as e:
        logger.error(f"未処理行の取得エラー: {e}")
        return []


def update_row_with_product_info(
    sheet_client: SpreadsheetClient,
    row_num: int,
    product_info: dict,
    category_info: tuple[int, int, list[int]],
    description: str,
    simple_description: str,
    colorme_image_urls: list[str],
    timestamp: str,
    exchange_rate: float = None,
    calculated_price: float = None,
    japanese_name: str = None,
    product_id: int = None,
    sync_mode: str = "ドラフト作成",
    existing_currency: str = None
) -> bool:
    """
    指定行に商品情報を更新する

    Args:
        sheet_client: スプレッドシートクライアント
        row_num: 行番号
        product_info: スクレイピングで取得した商品情報
        category_info: (大カテゴリーID, 小カテゴリーID, グループIDリスト)
        description: 商品説明
        simple_description: 簡易説明
        colorme_image_urls: カラーミー画像URLリスト
        timestamp: 更新日時
        sync_mode: 同期モード（ドラフト作成/新規登録）
        existing_currency: 既存の通貨（設定済みの場合は上書きしない）
    """
    if not sheet_client._spreadsheet:
        return False

    try:
        sheet = sheet_client._spreadsheet.worksheet(Config.SHEET_COLORME)

        cat_big, cat_small, grp_ids = category_info

        updates = []

        # A列: 商品ID（カラーミーに登録済みの場合）
        if product_id:
            updates.append({
                'range': f'A{row_num}',
                'values': [[str(product_id)]]
            })

        # B列: 商品名（日本語商品名があればそちらを使用）
        product_name = japanese_name if japanese_name else product_info.get("name", "")
        updates.append({
            'range': f'B{row_num}',
            'values': [[product_name]]
        })

        # M列: 取得元価格
        updates.append({
            'range': f'M{row_num}',
            'values': [[str(product_info.get("price", ""))]]
        })

        # N列: 取得通貨（既存の値がある場合は上書きしない）
        if not existing_currency:
            updates.append({
                'range': f'N{row_num}',
                'values': [[product_info.get("currency", "USD")]]
            })

        # P列: 外部-為替レート
        if exchange_rate is not None:
            updates.append({
                'range': f'P{row_num}',
                'values': [[str(round(exchange_rate, 4))]]
            })

        # Q列: 外部-本体計算価格（原価）
        if calculated_price is not None:
            updates.append({
                'range': f'Q{row_num}',
                'values': [[str(int(calculated_price))]]
            })

        # Y列: 最終更新
        updates.append({
            'range': f'Y{row_num}',
            'values': [[timestamp]]
        })

        # AB列: 在庫状況
        in_stock = "○" if product_info.get("in_stock") else "×"
        updates.append({
            'range': f'AB{row_num}',
            'values': [[in_stock]]
        })

        # AC列: 同期モードはユーザーが設定するため、ここでは更新しない

        # AD列: 型番
        updates.append({
            'range': f'AD{row_num}',
            'values': [[product_info.get("sku", "")]]
        })

        # AE列: カテゴリーID（大）
        if cat_big > 0:
            updates.append({
                'range': f'AE{row_num}',
                'values': [[str(cat_big)]]
            })

        # AF列: カテゴリーID（小）
        if cat_small > 0:
            updates.append({
                'range': f'AF{row_num}',
                'values': [[str(cat_small)]]
            })

        # AG列: グループID
        if grp_ids:
            updates.append({
                'range': f'AG{row_num}',
                'values': [[",".join(str(g) for g in grp_ids)]]
            })

        # AP列: 商品説明
        if description:
            updates.append({
                'range': f'AP{row_num}',
                'values': [[description]]
            })

        # AQ列: 簡易説明
        if simple_description:
            updates.append({
                'range': f'AQ{row_num}',
                'values': [[simple_description]]
            })

        # AR〜BA列: 画像URL1〜10
        image_urls = colorme_image_urls[:10] if colorme_image_urls else product_info.get("image_urls", [])[:10]
        if image_urls:
            # 画像URLを1列ずつ更新
            for i, img_url in enumerate(image_urls):
                col_letter = chr(ord('A') + 43 + i)  # AR=43+0, AS=43+1, ...
                if i < 26 - 17:  # AR〜AZ
                    col_letter = f'A{chr(ord("R") + i)}'
                else:  # BA
                    col_letter = 'BA'
                updates.append({
                    'range': f'{col_letter}{row_num}',
                    'values': [[img_url]]
                })

        # バッチ更新
        if updates:
            sheet.batch_update(updates, value_input_option='RAW')

        return True

    except Exception as e:
        logger.error(f"行更新エラー (行 {row_num}): {e}")
        return False


def fill_incomplete_rows(dry_run: bool = True) -> bool:
    """
    未処理行を検出し、商品情報を自動入力する

    Args:
        dry_run: Trueの場合、実際の更新は行わない

    Returns:
        bool: 成功時True
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. スプレッドシートに接続
    logger.info("スプレッドシートに接続中...")
    sheet_client = SpreadsheetClient()
    if not sheet_client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    # 2. カラーミーAPIに接続
    colorme_client = None
    if Config.is_colorme_enabled():
        colorme_client = ColorMeClient()
        categories = colorme_client.get_categories()
        groups = colorme_client.get_groups()
        logger.info(f"カテゴリー: {len(categories)}件, グループ: {len(groups)}件")
    else:
        logger.warning("カラーミーAPIが設定されていません。カテゴリー判定と画像アップロードはスキップされます。")
        categories = []
        groups = []

    # 3. 未処理行を取得
    incomplete_rows = get_incomplete_rows(sheet_client)
    if not incomplete_rows:
        logger.info("未処理の行はありません")
        return True

    logger.info(f"処理対象: {len(incomplete_rows)}件")

    # 4. 説明生成器を初期化
    generator = DescriptionGenerator()

    # 5. カテゴリー判定器を初期化
    detector = CategoryDetector(categories, groups)

    # 6. 為替レートクライアントを初期化
    exchange_client = ExchangeRateClient()
    if exchange_client.fetch_rates():
        logger.info("為替レートを取得しました")
    else:
        logger.warning("為替レートの取得に失敗しました。P列・Q列は更新されません。")

    # 7. 日本語商品名ジェネレーターを初期化
    name_generator = JapaneseProductNameGenerator()

    # 8. スクレイパーを初期化して各行を処理
    success_count = 0
    error_count = 0

    with ProductScraper() as scraper:
        for row_info in incomplete_rows:
            row_num = row_info["row_num"]
            source_url = row_info["source_url"]
            sync_mode = row_info["sync_mode"]

            logger.info(f"処理中: 行 {row_num} - {source_url}")
            logger.info(f"  同期モード: {sync_mode}")

            # 商品情報をスクレイピング
            product_info = scraper.scrape(source_url)
            if not product_info:
                logger.error(f"  → スクレイピング失敗")
                error_count += 1
                continue

            logger.info(f"  商品名(英語): {product_info['name']}")
            logger.info(f"  価格: {product_info['price']} {product_info['currency']}")
            logger.info(f"  在庫: {'あり' if product_info['in_stock'] else 'なし'}")
            logger.info(f"  画像: {len(product_info['image_urls'])}枚")

            # 日本語商品名を生成
            quantity = row_info.get("quantity", 1)
            japanese_name = name_generator.generate(product_info, quantity)
            logger.info(f"  商品名(日本語): {japanese_name}")

            # カテゴリー判定（既存値を優先）
            existing_cat_id = row_info.get("existing_category_id", 0)
            existing_subcat_id = row_info.get("existing_subcategory_id", 0)

            if existing_cat_id > 0:
                # スプレッドシートに既存のカテゴリーIDがあれば使用
                cat_big = existing_cat_id
                cat_small = existing_subcat_id
                grp_ids = []
                logger.info(f"  カテゴリー(既存値使用): 大={cat_big}, 小={cat_small}")
            else:
                # なければ商品名から判定
                cat_big, cat_small, grp_ids = detector.detect(product_info["name"])
                logger.info(f"  カテゴリー(自動判定): 大={cat_big}, 小={cat_small}")

            # 為替レートと計算価格を取得
            # 既存の通貨設定がある場合はそちらを優先
            exchange_rate = None
            calculated_price = None
            existing_currency = row_info.get("existing_currency", "")
            currency = existing_currency if existing_currency else product_info.get("currency", "USD")
            price = product_info.get("price", 0)
            if price > 0:
                exchange_rate = exchange_client.get_rate(currency, "JPY")
                if exchange_rate:
                    calculated_price = price * exchange_rate
                    logger.info(f"  為替レート: 1 {currency} = {exchange_rate:.4f} JPY")
                    logger.info(f"  計算価格: {int(calculated_price)} 円")
                else:
                    logger.warning(f"  為替レート取得失敗: {currency}")

            # 商品説明を生成
            description, simple_description = "", ""
            if generator.client:
                logger.info("  商品説明を生成中...")
                description, simple_description = generator.generate(product_info)
                if description:
                    logger.info("  → 説明生成完了")

            # カラーミー画像URL
            colorme_image_urls = []

            if dry_run:
                logger.info("  === ドライラン ===")
                logger.info(f"  更新予定: B列={japanese_name[:50] if japanese_name else product_info['name'][:30]}...")
                logger.info(f"  更新予定: M列={product_info['price']}, N列={product_info['currency']}")
                logger.info(f"  更新予定: 画像={len(product_info['image_urls'])}枚")
                if sync_mode == "新規登録":
                    logger.info("  → カラーミーへ新規登録＋画像アップロードが実行されます")
                elif sync_mode == "更新":
                    logger.info("  → カラーミーへの更新が実行されます")
                elif sync_mode == "ドラフト作成":
                    logger.info("  → スプレッドシートのみ更新（ドラフト作成）")
                else:
                    logger.info("  → スプレッドシートのみ更新（取得のみ）")
            else:
                # カラーミー登録用の商品ID
                registered_product_id = None

                # sync_mode が「新規登録」の場合、カラーミーへ新規登録
                if sync_mode == "新規登録" and colorme_client:
                    logger.info("  カラーミーへ新規商品登録中...")

                    # ColorMeProductを作成
                    colorme_product = ColorMeProduct(
                        product_id=0,  # 新規登録なのでID未定
                        name=japanese_name or product_info.get("name", ""),
                        current_price=int(calculated_price) if calculated_price else 0,
                        colorme_url="",
                        source_url=source_url,
                        quantity=quantity,
                        margin_rate=row_info.get("margin_rate", 1.1),
                        model_number=product_info.get("sku", ""),
                        category_id_big=cat_big,
                        category_id_small=cat_small,
                        group_ids=grp_ids,
                        regular_price=int(calculated_price) if calculated_price else 0,
                        stock_quantity=10 if product_info.get("in_stock") else 0,
                        stock_managed=True,
                        expl=description,
                        simple_expl=simple_description,
                        image_urls=product_info.get("image_urls", [])[:10],
                        display_control="表示"
                    )

                    # 商品を新規登録
                    new_product_id, error = colorme_client.create_product(colorme_product)
                    if new_product_id > 0:
                        registered_product_id = new_product_id
                        logger.info(f"  → 商品登録成功: ID={new_product_id}")

                        # 画像をアップロード
                        image_urls = product_info.get("image_urls", [])[:10]
                        if image_urls:
                            logger.info(f"  画像アップロード中... ({len(image_urls)}枚)")
                            success, img_error = colorme_client.upload_product_images(new_product_id, image_urls)
                            if success:
                                logger.info("  → 画像アップロード成功")
                            else:
                                logger.warning(f"  → 画像アップロード一部失敗: {img_error}")
                    else:
                        logger.error(f"  → 商品登録失敗: {error}")

                elif sync_mode == "更新" and colorme_client:
                    logger.info("  更新モード: カラーミー既存商品の更新（未実装）")
                elif sync_mode == "ドラフト作成":
                    logger.info("  ドラフト作成モード: スプレッドシートのみ更新（カラーミー登録なし）")
                elif sync_mode == "取得のみ":
                    logger.info("  取得のみモード: スプレッドシートのみ更新")

                # スプレッドシートを更新
                if update_row_with_product_info(
                    sheet_client,
                    row_num,
                    product_info,
                    (cat_big, cat_small, grp_ids),
                    description,
                    simple_description,
                    colorme_image_urls,
                    timestamp,
                    exchange_rate,
                    calculated_price,
                    japanese_name,
                    registered_product_id,
                    sync_mode,
                    existing_currency
                ):
                    logger.info(f"  → 更新完了")
                    success_count += 1
                else:
                    logger.error(f"  → 更新失敗")
                    error_count += 1

    # 結果サマリー
    logger.info("=" * 50)
    if dry_run:
        logger.info(f"ドライラン完了: {len(incomplete_rows)}件を処理予定")
        logger.info("実際に更新するには --execute オプションを付けて実行してください")
    else:
        logger.info(f"処理完了: 成功 {success_count}件, 失敗 {error_count}件")

    return error_count == 0


def main():
    parser = argparse.ArgumentParser(
        description="カラーミー商品管理シートの未処理行に商品情報を自動入力する"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="実際に更新を実行する（デフォルトはドライラン）"
    )

    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("新商品情報自動入力スクリプト")
    logger.info("=" * 50)

    success = fill_incomplete_rows(dry_run=not args.execute)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
