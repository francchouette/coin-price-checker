"""
新商品情報自動入力スクリプト

カラーミー商品管理シートで、D列（取得元URL）が入力されていて
B列（商品名）が空の行を検出し、自動で情報を埋める。

AC列の同期モードに応じて処理を行う:
- ドラフト作成: スプレッドシートのみ更新（カラーミー登録なし）
- 取得のみ: スプレッドシートのみ更新
- 更新: カラーミーの既存商品を更新
- 新規登録: カラーミーに新規商品登録

使用方法:
    python -m src.add_product
"""

import asyncio
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
from .colorme_image_uploader import ColorMeImageUploader
from .config import Config
from .exchange_rate import ExchangeRateClient, WiseRateClient
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
以下の形式で、<br>タグを使って適切に改行を入れてください：

【品位】　純度情報<br>
【重量】　重量情報<br>
【直径】　サイズ情報<br>
<br>
<strong><span style="font-size:large;">キャッチコピー（15-25文字）</span></strong><br>
<br>
本文（段落ごとに<br><br>で区切る）

具体例：
【品位】　約99.99％K24純金<br>
【重量】　１オンス（31.1g）<br>
【直径】　約32.7mm<br>
<br>
<strong><span style="font-size:large;">2024年 辰年の輝き</span></strong><br>
<br>
シンガポール BullionStar社発行の2024年干支シリーズ「ドラゴン」銀貨です。<br>
<br>
表面には躍動感あふれる龍のデザインが施され、裏面には12の干支動物が配置されています。<br>
<br>
純度99.9%の高品質シルバーを使用し、投資用としても人気の高いコインです。

### 簡易説明
1-2文（80-120文字）で以下を含める（改行不要）：
- 製造国・素材
- 重量・デザインの特徴
- 投資メリット

## 重要な注意事項
- 商品説明には必ず<br>タグで改行を入れること（読みやすさのため）
- 各項目の後、キャッチコピーの前後、段落ごとに<br>を入れる
- 専門用語は適切に使用（地金型金貨、純金、K24など）
- 日本の投資家向けに魅力的な表現を使用

JSON形式で出力してください:
{{
    "description": "商品説明（<br>タグ含む）",
    "simple_description": "簡易説明"
}}
"""


class ProductScraper:
    """仕入れサイトから商品情報をスクレイピングするクラス"""

    def __init__(self, image_analyzer: 'ImageAnalyzer' = None):
        self.browser = None
        self.context = None
        self.playwright = None
        self.image_analyzer = image_analyzer

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

            # JPY表示用のCookieを設定（ページアクセス前に設定が必要）
            self.context.add_cookies([
                {
                    "name": "currency",
                    "value": "JPY",
                    "domain": ".bullionstar.com",
                    "path": "/"
                }
            ])
            logger.info("  BullionStar: JPY通貨Cookieを設定")

            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            result = {
                "name": "",
                "price": 0.0,
                "currency": "JPY",  # Cookie設定によりJPY表示
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

            # 価格（数量別価格テーブルから取得 - shops/bullionstar.pyと同じ方式）
            try:
                rows = page.query_selector_all(".info tr")

                # JPYを優先、他の通貨もフォールバックとして対応
                patterns = [
                    (r'¥([\d,]+)', 'JPY'),           # JPY（優先）
                    (r'US\$([\d,]+\.?\d*)', 'USD'),  # USD
                    (r'S\$([\d,]+\.?\d*)', 'SGD'),   # SGD
                    (r'€([\d,]+\.?\d*)', 'EUR'),     # EUR
                    (r'£([\d,]+\.?\d*)', 'GBP'),     # GBP
                ]

                for row in rows:
                    text = row.inner_text().strip()

                    # ヘッダー行はスキップ
                    if "Quantity" in text or "Price" in text:
                        continue

                    # 数量パターンがある行のみ処理（"1 - 9" や "1 - 99" など）
                    if not re.search(r'\d+\s*-\s*\d+', text):
                        continue

                    for pattern, currency in patterns:
                        match = re.search(pattern, text)
                        if match:
                            price_str = match.group(1).replace(',', '')
                            price = float(price_str)
                            if price > 0:
                                result["price"] = price
                                result["currency"] = currency
                                logger.info(f"  価格検出: {currency} {price:,.0f}")
                                break
                    if result["price"] > 0:
                        break
            except Exception as e:
                logger.warning(f"価格抽出エラー: {e}")

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

            # 画像URL（Schema.org構造化データを優先的に使用）
            try:
                image_urls_found = []

                # 方法1: Schema.org JSON-LDから画像を取得（最も信頼性が高い）
                try:
                    json_ld_scripts = page.query_selector_all('script[type="application/ld+json"]')
                    for script in json_ld_scripts:
                        try:
                            json_text = script.inner_text()
                            data = json.loads(json_text)
                            # Product型のデータを探す
                            if isinstance(data, dict):
                                if data.get("@type") == "Product" and "image" in data:
                                    images = data["image"]
                                    if isinstance(images, list):
                                        image_urls_found.extend(images)
                                    elif isinstance(images, str):
                                        image_urls_found.append(images)
                                    logger.info(f"  Schema.orgから画像取得: {len(image_urls_found)}枚")
                        except json.JSONDecodeError:
                            pass
                except Exception:
                    pass

                # 方法2: URLから商品識別子を抽出してパターンマッチ
                if not image_urls_found:
                    # URLから商品識別子を抽出
                    # 例: silver-round-bstar-lunar-series-dragon-1oz-2024 → "dragon", "lunar", "bstar"
                    url_path = url.split("/")[-1] if "/" in url else ""
                    product_keywords = []
                    for part in url_path.split("-"):
                        if len(part) > 2 and part not in ["buy", "product", "silver", "gold", "1oz", "2oz", "round", "coin"]:
                            product_keywords.append(part.lower())

                    # /files/内の画像を取得
                    all_imgs = page.query_selector_all("img[src*='/files/']")
                    for img in all_imgs:
                        src = img.get_attribute("src")
                        if not src:
                            continue

                        # ファイル名を抽出
                        filename = src.split("/")[-1].lower()

                        # 商品キーワードが含まれているかチェック
                        matches = sum(1 for kw in product_keywords if kw in filename)
                        if matches >= 2:  # 2つ以上のキーワードが一致
                            # 高解像度に変換
                            high_res_url = re.sub(r'/(\d+)_(\d+)_', '/1200_1200_', src)
                            if high_res_url not in image_urls_found:
                                image_urls_found.append(high_res_url)

                    if image_urls_found:
                        logger.info(f"  パターンマッチで画像取得: {len(image_urls_found)}枚")

                # 方法3: フォールバック - 商品画像セクションから取得
                if not image_urls_found:
                    # 商品画像コンテナを探す
                    product_img_selectors = [
                        ".product-image img",
                        ".product-gallery img",
                        "[class*='product-main'] img",
                        ".main-image img"
                    ]
                    for selector in product_img_selectors:
                        imgs = page.query_selector_all(selector)
                        for img in imgs:
                            src = img.get_attribute("src")
                            if src and "/files/" in src:
                                high_res_url = re.sub(r'/(\d+)_(\d+)_', '/1200_1200_', src)
                                if high_res_url not in image_urls_found:
                                    image_urls_found.append(high_res_url)

                # 方法4: AIによるスクリーンショット解析（最終フォールバック）
                if not image_urls_found and self.image_analyzer:
                    logger.info("  方法4: AIスクリーンショット解析を試行...")
                    image_urls_found = self.image_analyzer.select_best_images_from_screenshot(
                        page, result["name"]
                    )
                    if image_urls_found:
                        logger.info(f"  スクリーンショット解析で画像取得: {len(image_urls_found)}枚")

                # AIによる画像検証（画像が取得できた場合）
                if image_urls_found and self.image_analyzer and result["name"]:
                    logger.info("  AIによる画像検証中...")
                    verified_urls = self.image_analyzer.filter_product_images(
                        image_urls_found, result["name"]
                    )
                    if verified_urls:
                        image_urls_found = verified_urls
                        logger.info(f"  AI検証後の画像: {len(image_urls_found)}枚")

                # 結果を設定
                result["image_urls"] = image_urls_found[:10]
                logger.info(f"  最終取得画像: {len(result['image_urls'])}枚")

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
                logger.info("説明生成器: 初期化完了（APIキー設定済み）")
            except ImportError:
                logger.warning("anthropicライブラリがインストールされていません")
        else:
            logger.warning("説明生成器: ANTHROPIC_API_KEYが設定されていません")

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
            logger.info("  Claude APIを呼び出し中（説明生成）...")
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text
            logger.debug(f"  API応答（先頭200文字）: {response_text[:200]}")

            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    desc = data.get("description", "")
                    simple_desc = data.get("simple_description", "")
                    if desc:
                        logger.info(f"  説明生成成功: {len(desc)}文字")
                    else:
                        logger.warning("  JSONは取得できたが、descriptionが空です")
                    return desc, simple_desc
                except json.JSONDecodeError as je:
                    logger.error(f"  JSON解析エラー: {je}")
                    logger.error(f"  解析対象: {json_match.group()[:200]}")
            else:
                logger.warning("  応答にJSONが含まれていません")
                logger.warning(f"  応答内容: {response_text[:300]}")

        except Exception as e:
            logger.error(f"説明生成エラー: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return "", ""


class ImageAnalyzer:
    """AIを使って商品画像を解析・検証するクラス"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.client = None
        if self.api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                logger.warning("anthropicライブラリがインストールされていません")

    def filter_product_images(self, image_urls: list[str], product_name: str) -> list[str]:
        """
        画像URLリストから正しい商品画像のみをフィルタリングする

        Args:
            image_urls: 候補となる画像URLのリスト
            product_name: 商品名

        Returns:
            list[str]: 正しい商品画像のURLリスト
        """
        if not self.client or not image_urls:
            return image_urls

        try:
            # 最大5枚の画像をAIで検証（コスト削減）
            urls_to_check = image_urls[:5]

            # 画像コンテンツを構築
            content = []
            for i, url in enumerate(urls_to_check):
                content.append({
                    "type": "image",
                    "source": {"type": "url", "url": url}
                })
                content.append({
                    "type": "text",
                    "text": f"画像{i+1}: {url.split('/')[-1]}"
                })

            content.append({
                "type": "text",
                "text": f"""上記の画像を確認してください。

商品名: {product_name}

以下の基準で、この商品の正しい商品画像かどうかを判定してください：
1. コイン/地金の実物画像である（イラストやロゴではない）
2. 商品名に含まれる特徴（年号、デザイン、素材）と一致している
3. 明らかに別の商品ではない

JSON形式で回答してください:
{{
    "valid_images": [1, 2, 3],  // 正しい商品画像の番号リスト
    "reason": "判定理由"
}}
"""
            })

            logger.info(f"  AI画像検証中... ({len(urls_to_check)}枚)")
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{"role": "user", "content": content}]
            )

            response_text = response.content[0].text
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                data = json.loads(json_match.group())
                valid_indices = data.get("valid_images", [])
                reason = data.get("reason", "")
                logger.info(f"  AI判定: {len(valid_indices)}枚が正しい商品画像 - {reason}")

                # 有効な画像のみを返す
                filtered_urls = [urls_to_check[i-1] for i in valid_indices if 1 <= i <= len(urls_to_check)]

                # 未検証の画像（6枚目以降）は追加しない（正確性重視）
                return filtered_urls if filtered_urls else image_urls[:3]

        except Exception as e:
            logger.warning(f"  AI画像検証エラー: {e}")

        return image_urls

    def select_best_images_from_screenshot(self, page, product_name: str) -> list[str]:
        """
        ページスクリーンショットからAIで商品画像を特定する

        Args:
            page: Playwrightのページオブジェクト
            product_name: 商品名

        Returns:
            list[str]: 商品画像のURLリスト
        """
        if not self.client:
            return []

        try:
            # スクリーンショットを取得
            screenshot_bytes = page.screenshot(full_page=False)
            import base64
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')

            # ページ内のすべての画像URLを取得
            all_imgs = page.query_selector_all("img[src*='/files/']")
            img_info = []
            for i, img in enumerate(all_imgs[:20]):
                src = img.get_attribute("src")
                if src:
                    # 画像の位置情報を取得
                    box = img.bounding_box()
                    if box:
                        img_info.append({
                            "index": i,
                            "url": src,
                            "x": box["x"],
                            "y": box["y"],
                            "width": box["width"],
                            "height": box["height"]
                        })

            if not img_info:
                return []

            # AIに画像の位置と内容を分析させる
            img_list_text = "\n".join([
                f"{info['index']+1}. 位置({info['x']:.0f},{info['y']:.0f}) サイズ{info['width']:.0f}x{info['height']:.0f}: {info['url'].split('/')[-1]}"
                for info in img_info
            ])

            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_b64
                    }
                },
                {
                    "type": "text",
                    "text": f"""このスクリーンショットは貴金属商品のページです。

商品名: {product_name}

ページ内の画像一覧:
{img_list_text}

メインの商品画像（コインや地金の実物写真）の番号を特定してください。
関連商品、ブログ記事、ロゴなどは除外してください。

JSON形式で回答:
{{
    "product_image_indices": [1, 2],
    "reason": "判定理由"
}}
"""
                }
            ]

            logger.info("  スクリーンショットからAI画像特定中...")
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{"role": "user", "content": content}]
            )

            response_text = response.content[0].text
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                data = json.loads(json_match.group())
                indices = data.get("product_image_indices", [])
                reason = data.get("reason", "")
                logger.info(f"  AI特定: {len(indices)}枚 - {reason}")

                # 特定された画像URLを返す
                result_urls = []
                for idx in indices:
                    if 1 <= idx <= len(img_info):
                        src = img_info[idx-1]["url"]
                        # 高解像度に変換
                        high_res_url = re.sub(r'/(\d+)_(\d+)_', '/1200_1200_', src)
                        result_urls.append(high_res_url)
                return result_urls

        except Exception as e:
            logger.warning(f"  スクリーンショット解析エラー: {e}")

        return []


class CategoryDetector:
    """商品名からカテゴリーを自動判定するクラス（AIサポート）"""

    # カテゴリーID（固定値）
    CATEGORY_IDS = {
        "gold": 2961572,
        "silver": 2961573,
    }

    # グループマスター（キー: 内部名, 値: {id, name, parent_key}）
    GROUP_MASTER = {
        # 素材グループ（親）
        "gold": {"id": 3110187, "name": "Gold", "parent_key": None},
        "silver": {"id": 3110193, "name": "Silver", "parent_key": None},
        "platinum": {"id": 3119343, "name": "Platinum", "parent_key": None},
        # シリーズ（金）- 親: gold
        "maple_gold": {"id": 3110188, "name": "メイプルリーフ カナダ", "parent_key": "gold"},
        "vienna_gold": {"id": 3110189, "name": "ウィーン オーストリア", "parent_key": "gold"},
        "britannia_gold": {"id": 3110190, "name": "ブリタニア イギリス", "parent_key": "gold"},
        "eagle_gold": {"id": 3110191, "name": "イーグル アメリカ", "parent_key": "gold"},
        "kangaroo_gold": {"id": 3110192, "name": "カンガルー オーストラリア", "parent_key": "gold"},
        "queens_beast": {"id": 3117632, "name": "クイーンズビースト", "parent_key": "gold"},
        # シリーズ（銀）- 親: silver
        "maple_silver": {"id": 3110200, "name": "メイプルリーフ カナダ", "parent_key": "silver"},
        "vienna_silver": {"id": 3110199, "name": "ウィーン オーストリア", "parent_key": "silver"},
        "britannia_silver": {"id": 3110198, "name": "ブリタニア イギリス", "parent_key": "silver"},
        "eagle_silver": {"id": 3110197, "name": "イーグル アメリカ", "parent_key": "silver"},
        "kookaburra": {"id": 3110194, "name": "カワセミ オーストラリア", "parent_key": "silver"},
        "kangaroo_silver": {"id": 3110196, "name": "カンガルー オーストラリア", "parent_key": "silver"},
        "koala": {"id": 3110195, "name": "コアラ オーストラリア", "parent_key": "silver"},
        # タイプ
        "coin": {"id": 3115142, "name": "コイン", "parent_key": None},
        "bar": {"id": 3115143, "name": "バー", "parent_key": None},
        "premier": {"id": 3115144, "name": "プレミア", "parent_key": None},
    }

    # 素材キーワードマッピング
    METAL_KEYWORDS = {
        "gold": ["gold", "金貨", "ゴールド", "金", "au"],
        "silver": ["silver", "銀貨", "シルバー", "銀", "ag"],
        "platinum": ["platinum", "プラチナ", "白金", "pt"],
    }

    # シリーズキーワードマッピング（キー: シリーズ内部名, 値: {keywords, display_name}）
    # 命名規則: 「シリーズ名 国名」形式（既存グループと一貫性を保つ）
    SERIES_MASTER = {
        "maple": {"keywords": ["maple", "メイプル"], "display_name": "メイプルリーフ カナダ"},
        "vienna": {"keywords": ["vienna", "philharmonic", "ウィーン", "フィルハーモニー"], "display_name": "ウィーン オーストリア"},
        "britannia": {"keywords": ["britannia", "ブリタニア"], "display_name": "ブリタニア イギリス"},
        "eagle": {"keywords": ["eagle", "イーグル"], "display_name": "イーグル アメリカ"},
        "kangaroo": {"keywords": ["kangaroo", "カンガルー"], "display_name": "カンガルー オーストラリア"},
        "kookaburra": {"keywords": ["kookaburra", "カワセミ"], "display_name": "カワセミ オーストラリア"},
        "koala": {"keywords": ["koala", "コアラ"], "display_name": "コアラ オーストラリア"},
        "queens_beast": {"keywords": ["queen's beast", "queens beast", "クイーンズビースト"], "display_name": "クイーンズビースト イギリス"},
        "panda": {"keywords": ["panda", "パンダ"], "display_name": "パンダ 中国"},
        # 干支シリーズは国別ではなく干支別にグループ化
        "dragon": {"keywords": ["dragon", "ドラゴン", "龍", "year of the dragon"], "display_name": "干支シリーズ"},
        "lunar": {"keywords": ["lunar", "干支", "year of"], "display_name": "干支シリーズ"},
        "tiger": {"keywords": ["tiger", "タイガー", "虎", "year of the tiger"], "display_name": "干支シリーズ"},
        "rabbit": {"keywords": ["rabbit", "ラビット", "兎", "year of the rabbit"], "display_name": "干支シリーズ"},
        "snake": {"keywords": ["snake", "スネーク", "蛇", "year of the snake"], "display_name": "干支シリーズ"},
        "horse": {"keywords": ["horse", "ホース", "馬", "year of the horse"], "display_name": "干支シリーズ"},
        "krugerrand": {"keywords": ["krugerrand", "クルーガーランド"], "display_name": "クルーガーランド 南アフリカ"},
        "buffalo": {"keywords": ["buffalo", "バッファロー"], "display_name": "バッファロー アメリカ"},
        "libertad": {"keywords": ["libertad", "リベルタード"], "display_name": "リベルタード メキシコ"},
    }

    # 商品タイプキーワード
    TYPE_KEYWORDS = {
        "coin": ["coin", "コイン", "貨", "round", "ラウンド"],
        "bar": ["bar", "ingot", "インゴット", "バー", "地金"],
    }

    def __init__(self, categories: list[dict], groups: list[dict], colorme_client=None):
        self.categories = categories
        self.groups = groups
        self.colorme_client = colorme_client

        # Claude APIクライアントを初期化
        self.client = None
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key)
            except Exception as e:
                logger.warning(f"Anthropic APIクライアント初期化エラー: {e}")

        # APIから取得したグループ名→{id, parent_id}のマッピングを作成
        self.existing_groups = {}
        for g in groups:
            parent_id = g.get("parent_id", 0) or 0
            self.existing_groups[g["name"]] = {"id": g["id"], "parent_id": parent_id}

    def _get_or_create_group(self, group_key: str, required_parent_id: int = None) -> int:
        """グループIDを取得、存在しない場合は作成

        Args:
            group_key: GROUP_MASTERのキー
            required_parent_id: 必須の親グループID（指定時は親が一致するものを探す）
        """
        if group_key not in self.GROUP_MASTER:
            return 0

        master = self.GROUP_MASTER[group_key]
        group_id = master["id"]
        group_name = master["name"]
        expected_parent_key = master.get("parent_key")

        # 期待される親グループIDを取得
        if required_parent_id is not None:
            expected_parent_id = required_parent_id
        elif expected_parent_key and expected_parent_key in self.GROUP_MASTER:
            expected_parent_id = self.GROUP_MASTER[expected_parent_key]["id"]
        else:
            expected_parent_id = 0

        # 既存グループに存在するか確認（親も一致するかチェック）
        if group_name in self.existing_groups:
            existing = self.existing_groups[group_name]
            existing_id = existing["id"]
            existing_parent = existing["parent_id"]

            # 親グループが一致する場合はそのまま返す
            if expected_parent_id == 0 or existing_parent == expected_parent_id:
                return existing_id

            # 親が一致しない場合は新しいグループを作成
            logger.info(f"  既存グループ '{group_name}' は親が不一致 (期待: {expected_parent_id}, 実際: {existing_parent})")

        # マスターのIDが存在するか確認（API取得グループと照合）
        for g in self.groups:
            if g["id"] == group_id:
                parent_id = g.get("parent_id", 0) or 0
                if expected_parent_id == 0 or parent_id == expected_parent_id:
                    return group_id

        # 存在しない場合は作成
        if self.colorme_client:
            logger.info(f"  グループ作成中: {group_name} (親ID: {expected_parent_id})")
            new_id, error = self.colorme_client.create_group(group_name, expected_parent_id)
            if new_id > 0:
                # マスターとキャッシュを更新
                self.GROUP_MASTER[group_key]["id"] = new_id
                self.existing_groups[group_name] = {"id": new_id, "parent_id": expected_parent_id}
                return new_id
            else:
                logger.warning(f"  グループ作成失敗: {error}")

        return group_id  # フォールバック: マスターのID

    def detect(self, product_name: str, url: str = "") -> tuple[int, int, list[int]]:
        """
        商品名からカテゴリーIDとグループIDを判定（AI判定対応）

        Args:
            product_name: 商品名
            url: 商品URL（AI判定の参考情報）

        Returns:
            tuple[int, int, list[int]]: (大カテゴリーID, 小カテゴリーID, グループIDリスト)
        """
        name_lower = product_name.lower()
        url_lower = url.lower() if url else ""
        group_ids = []

        # 1. 素材タイプを判定 → カテゴリーID決定
        # まずURLから判定（最も確実）
        detected_metal = None
        if "silver" in url_lower:
            detected_metal = "silver"
        elif "gold" in url_lower:
            detected_metal = "gold"
        elif "platinum" in url_lower:
            detected_metal = "platinum"

        # URLで判定できない場合はキーワードから判定
        if not detected_metal:
            for metal, keywords in self.METAL_KEYWORDS.items():
                if any(kw in name_lower for kw in keywords):
                    detected_metal = metal
                    break

        # それでも判定できない場合はAIで判定
        ai_result = {}
        if not detected_metal and self.client:
            logger.info("  素材をAIで判定中...")
            ai_result = self._detect_with_ai(product_name, url)
            if ai_result.get("metal"):
                detected_metal = ai_result["metal"]

        # カテゴリーID決定（金/銀のみ）
        if detected_metal == "gold":
            category_id_big = self.CATEGORY_IDS["gold"]
        elif detected_metal == "silver":
            category_id_big = self.CATEGORY_IDS["silver"]
        else:
            # デフォルトは銀
            category_id_big = self.CATEGORY_IDS["silver"]
            detected_metal = "silver"

        # 2. 素材グループを追加
        metal_group_id = self._get_or_create_group(detected_metal)
        if metal_group_id:
            group_ids.append(metal_group_id)

        # 3. シリーズを判定 → グループID追加
        detected_series = None
        for series, info in self.SERIES_MASTER.items():
            if any(kw in name_lower for kw in info["keywords"]):
                detected_series = series
                break

        # キーワードで判定できない場合はAI結果を使用
        if not detected_series and ai_result.get("series"):
            detected_series = ai_result["series"]
            logger.info(f"  シリーズをAI判定で使用: {detected_series}")

        if detected_series:
            # 素材に応じたシリーズグループを取得/作成
            series_key = f"{detected_series}_{detected_metal}"
            if series_key in self.GROUP_MASTER:
                series_group_id = self._get_or_create_group(series_key)
            else:
                # 素材別グループがない場合は正しい親の下にグループを作成
                series_group_id = self._get_or_create_series_group(detected_series, detected_metal)

            if series_group_id:
                group_ids.append(series_group_id)

        # 4. 商品タイプを判定 → グループID追加
        detected_type = None
        for ptype, keywords in self.TYPE_KEYWORDS.items():
            if any(kw in name_lower for kw in keywords):
                detected_type = ptype
                break

        # AI結果からタイプを取得
        if not detected_type and ai_result.get("type"):
            detected_type = ai_result["type"]

        if detected_type:
            type_group_id = self._get_or_create_group(detected_type)
            if type_group_id:
                group_ids.append(type_group_id)

        logger.info(f"  判定: 素材={detected_metal}, シリーズ={detected_series}, タイプ={detected_type}")
        logger.info(f"  → カテゴリー={category_id_big}, グループ={group_ids}")

        return category_id_big, 0, group_ids

    def _get_or_create_series_group(self, series: str, metal: str) -> int:
        """シリーズグループを取得、存在しない場合は正しい親の下に作成"""
        if series not in self.SERIES_MASTER:
            return 0

        series_info = self.SERIES_MASTER[series]
        group_name = series_info["display_name"]

        # 期待される親グループID（metalに対応するグループ）
        expected_parent_id = self.GROUP_MASTER.get(metal, {}).get("id", 0)

        # 既存グループに存在するか確認（親も一致するかチェック）
        if group_name in self.existing_groups:
            existing = self.existing_groups[group_name]
            existing_id = existing["id"]
            existing_parent = existing["parent_id"]

            # 親グループが一致する場合はそのまま返す
            if existing_parent == expected_parent_id:
                return existing_id

            # 親が一致しない場合は正しい親の下に新規作成
            logger.info(f"  既存グループ '{group_name}' は親が不一致 (期待: {expected_parent_id}/{metal}, 実際: {existing_parent})")
            logger.info(f"  → 正しい親の下に新規作成します")

        # 存在しない、または親が不一致の場合は作成
        if self.colorme_client:
            logger.info(f"  新シリーズグループ作成中: {group_name} (親: {metal}, ID: {expected_parent_id})")
            new_id, error = self.colorme_client.create_group(group_name, expected_parent_id)
            if new_id > 0:
                # マスターを更新
                new_key = f"{series}_{metal}"
                self.GROUP_MASTER[new_key] = {"id": new_id, "name": group_name, "parent_key": metal}
                self.existing_groups[group_name] = {"id": new_id, "parent_id": expected_parent_id}
                return new_id
            else:
                logger.warning(f"  グループ作成失敗: {error}")

        return 0

    def _detect_with_ai(self, product_name: str, url: str = "") -> dict:
        """
        AIを使って商品のカテゴリー情報を判定する

        Returns:
            dict: {"metal": "gold"|"silver"|"platinum", "series": str, "type": "coin"|"bar"|None}
        """
        if not self.client:
            return {}

        try:
            prompt = f"""以下の商品情報から、素材・シリーズ・タイプを判定してください。

商品名: {product_name}
URL: {url}

判定基準:
- 素材 (metal): "gold"（金貨/ゴールド）, "silver"（銀貨/シルバー）, "platinum"（プラチナ）
- シリーズ (series): "maple", "vienna", "britannia", "eagle", "kangaroo", "kookaburra", "koala", "dragon", "lunar", "panda" など
- タイプ (type): "coin"（コイン/ラウンド）, "bar"（バー/インゴット）, null（不明）

JSON形式で回答してください:
{{"metal": "...", "series": "...", "type": "..."}}

注意:
- URLに"silver"が含まれていれば銀貨、"gold"が含まれていれば金貨
- "dragon", "lunar"などの干支シリーズは素材と組み合わせて判定
- 判定できない場合はnullを設定
"""

            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                data = json.loads(json_match.group())
                logger.info(f"  AI判定結果: {data}")
                return data

        except Exception as e:
            logger.warning(f"  AI判定エラー: {e}")

        return {}


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


class ModelNumberGenerator:
    """
    型番をAI（Claude API）で自動生成するクラス

    命名規則:
    - コイン: ITM-{年号}-{国コード}-{カテゴリ}-{シリーズ}-{枚数}
      例: ITM-2026-GBR-SCJ-BRT-100
    - インゴット: ITM-{国コード}-{カテゴリ}-{メーカー}-{数量}-{重量}
      例: ITM-CH-GIJ-PAM-001-100G
    """

    # 型番生成用プロンプト
    PROMPT_TEMPLATE = """あなたは貴金属商品の型番を生成するエキスパートです。

以下の商品情報から、指定された命名規則に従って型番を生成してください。

## 商品情報
- 商品名: {product_name}
- 仕様: {specs}
- 説明: {description}
- 数量: {quantity}

## 命名規則

### コイン（年号付き）の場合:
ITM-{{年号}}-{{国コード}}-{{カテゴリ}}-{{シリーズ}}-{{枚数3桁}}

### インゴット/バーの場合:
ITM-{{国コード}}-{{カテゴリ}}-{{メーカー}}-{{数量3桁}}-{{重量}}

## コード一覧

### 国コード:
- GBR: イギリス (UK, Britain, Great Britain)
- AUT: オーストリア (Austria)
- CAD: カナダ (Canada)
- USA: アメリカ (USA, US, America)
- AUS: オーストラリア (Australia)
- ZAF: 南アフリカ (South Africa)
- CH: スイス (Switzerland, Swiss)
- TR: トルコ (Turkey)
- CHN: 中国 (China)
- MEX: メキシコ (Mexico)
- DEU: ドイツ (Germany)
- SGP: シンガポール (Singapore)

### カテゴリコード:
- GCJ: 金貨 (Gold Coin)
- SCJ: 銀貨 (Silver Coin)
- PCJ: プラチナ貨 (Platinum Coin)
- GIJ: 金インゴット (Gold Ingot/Bar)
- SIJ: 銀インゴット (Silver Ingot/Bar)
- PIJ: プラチナインゴット (Platinum Ingot/Bar)

### シリーズコード（コイン用）:
- BRT: ブリタニア (Britannia)
- WIN: ウィーン (Vienna, Philharmonic)
- MPL: メイプルリーフ (Maple Leaf)
- EGL: イーグル (Eagle)
- KGR: カンガルー (Kangaroo)
- KKB: カワセミ (Kookaburra)
- KOA: コアラ (Koala)
- PND: パンダ (Panda)
- KUR: クルーガーランド (Krugerrand)
- BUF: バッファロー (Buffalo)
- LBT: リベルタード (Libertad)
- LNR: 干支/ルナー (Lunar)
- DRG: ドラゴン (Dragon)
- QUB: クイーンズビースト (Queen's Beast)
- TDB: チューダービースト (Tudor Beast)
- RYA: ロイヤルアームズ (Royal Arms)
- OTH: その他 (Other)

### メーカーコード（インゴット用）:
- PAM: PAMP
- VCB: ヴァルカンビ (Valcambi)
- NDR: ナディール (Nadir)
- QFM: 9ファインミント (9Fine Mint)
- PTH: パースミント (Perth Mint)
- RYM: ロイヤルミント (Royal Mint)
- BST: ブリオンスター (BullionStar)
- ARG: アルゴルヘレウス (Argor Heraeus)
- CRS: クレディスイス (Credit Suisse)
- OTH: その他 (Other)

### 重量コード（インゴット用）:
- 1G, 5G, 10G, 20G, 50G, 100G, 250G, 500G（グラム）
- 1K, 5K（キログラム）
- 1OZ（オンス、31g相当）

## 例
- "2026 1 oz Silver Britannia" (5枚) → ITM-2026-GBR-SCJ-BRT-005
- "PAMP Suisse 100g Gold Bar" (1本) → ITM-CH-GIJ-PAM-001-100G
- "2024 1 oz Silver Dragon Round USA" (5枚) → ITM-2024-USA-SCJ-DRG-005

## 出力形式
型番のみを1行で出力してください。説明は不要です。
"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.client = None
        if self.api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=self.api_key)
                logger.info("型番生成器: 初期化完了（APIキー設定済み）")
            except ImportError:
                logger.warning("anthropicライブラリがインストールされていません")
        else:
            logger.warning("型番生成器: ANTHROPIC_API_KEYが設定されていません")

    def generate(self, product_info: dict, quantity: int = 1) -> str:
        """
        商品情報から型番をAIで生成する

        Args:
            product_info: スクレイピングで取得した商品情報
            quantity: 枚数/本数

        Returns:
            str: 型番 (例: ITM-2026-GBR-SCJ-BRT-100)
        """
        if not self.client:
            logger.warning("ANTHROPIC_API_KEYが設定されていません。型番生成をスキップします。")
            return ""

        prompt = self.PROMPT_TEMPLATE.format(
            product_name=product_info.get("name", ""),
            specs=product_info.get("specs", ""),
            description=product_info.get("description", "")[:500],  # 長すぎる場合は切り詰め
            quantity=quantity
        )

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}]
            )

            model_number = response.content[0].text.strip()
            # 型番形式の検証（ITM-で始まる）
            if model_number.startswith("ITM-"):
                return model_number
            else:
                logger.warning(f"不正な型番形式: {model_number}")
                return ""

        except Exception as e:
            logger.error(f"型番生成エラー: {e}")
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

                    # 為替種類（O列、index 14）- "クレカ" or "Wise"
                    exchange_type = "クレカ"  # デフォルト
                    if len(row) > 14 and row[14].strip():
                        exchange_type = row[14].strip()

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

                    # 販売価格（T列、index 19）
                    selling_price = 0
                    if len(row) > 19 and row[19].strip():
                        try:
                            selling_price = int(float(row[19].strip().replace(',', '')))
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
                        "exchange_type": exchange_type,
                        "existing_category_id": existing_category_id,
                        "existing_subcategory_id": existing_subcategory_id,
                        "selling_price": selling_price
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
    existing_currency: str = None,
    model_number: str = None
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
        model_number: 型番（自動生成）
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

            # C列: カラーミー商品URL（商品IDから自動生成）
            colorme_url = f"https://ybx.jp/?pid={product_id}"
            updates.append({
                'range': f'C{row_num}',
                'values': [[colorme_url]]
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

        # AB列: 在庫状況（プライスチェックと同じ形式）
        in_stock = "In Stock" if product_info.get("in_stock") else "Out of Stock"
        updates.append({
            'range': f'AB{row_num}',
            'values': [[in_stock]]
        })

        # AC列: 同期モードはユーザーが設定するため、ここでは更新しない

        # AD列: 型番（自動生成された型番を使用、なければスクレイピングのSKU）
        sku_value = model_number if model_number else product_info.get("sku", "")
        updates.append({
            'range': f'AD{row_num}',
            'values': [[sku_value]]
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
            logger.info(f"  AP列（商品説明）を設定: {len(description)}文字")
        else:
            logger.warning(f"  AP列（商品説明）: 空のためスキップ")

        # AQ列: 簡易説明
        if simple_description:
            updates.append({
                'range': f'AQ{row_num}',
                'values': [[simple_description]]
            })
            logger.info(f"  AQ列（簡易説明）を設定: {len(simple_description)}文字")
        else:
            logger.warning(f"  AQ列（簡易説明）: 空のためスキップ")

        # AR〜BA列: 画像URL1〜10
        image_urls = colorme_image_urls[:10] if colorme_image_urls else product_info.get("image_urls", [])[:10]
        logger.info(f"  画像URL: {len(image_urls)}件")
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


def fill_incomplete_rows() -> bool:
    """
    未処理行を検出し、商品情報を自動入力する

    AC列の同期モードに応じて処理を行う:
    - ドラフト作成: スプレッドシートのみ更新（カラーミー登録なし）
    - 取得のみ: スプレッドシートのみ更新
    - 更新: カラーミーの既存商品を更新
    - 新規登録: カラーミーに新規商品登録

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

    # 5. カテゴリー判定器を初期化（グループ自動作成機能付き）
    detector = CategoryDetector(categories, groups, colorme_client)

    # 6. 為替レートクライアントを初期化
    exchange_client = ExchangeRateClient()
    wise_client = WiseRateClient()
    if exchange_client.fetch_rates():
        logger.info("為替レートを取得しました")
    else:
        logger.warning("為替レートの取得に失敗しました。P列・Q列は更新されません。")

    # 7. 日本語商品名ジェネレーターを初期化
    name_generator = JapaneseProductNameGenerator()

    # 8. 型番ジェネレーターを初期化
    model_number_generator = ModelNumberGenerator()

    # 9. 画像解析器を初期化（AI検証用）
    image_analyzer = ImageAnalyzer()
    if image_analyzer.client:
        logger.info("AI画像解析器: 初期化完了")
    else:
        logger.warning("AI画像解析器: APIキーが設定されていないため無効")

    # 10. スクレイパーを初期化して各行を処理
    success_count = 0
    error_count = 0

    with ProductScraper(image_analyzer=image_analyzer) as scraper:
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

            # 型番を生成
            model_number = model_number_generator.generate(product_info, quantity)
            logger.info(f"  型番: {model_number}")

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
                # なければ商品名とURLから判定（AIサポート）
                cat_big, cat_small, grp_ids = detector.detect(product_info["name"], source_url)
                logger.info(f"  カテゴリー(自動判定): 大={cat_big}, 小={cat_small}")

            # 為替レートと計算価格を取得
            # N列（既存通貨）の設定がある場合はそちらを優先
            # O列の為替種類に応じてレートを選択（クレカ or Wise）
            exchange_rate = None
            calculated_price = None
            existing_currency = row_info.get("existing_currency", "")
            exchange_type = row_info.get("exchange_type", "クレカ")
            # N列で通貨が指定されていればそれを使用、なければスクレイピングで検出した通貨
            currency = existing_currency if existing_currency else product_info.get("currency", "USD")
            price = product_info.get("price", 0)

            if price > 0:
                # JPYが指定されている場合は為替変換不要
                if currency.upper() == "JPY":
                    exchange_rate = 1.0
                    calculated_price = price
                    logger.info(f"  通貨: JPY（為替変換なし）")
                else:
                    # 外貨の場合は為替レートを適用
                    if exchange_type == "Wise":
                        # Wiseレートを使用
                        exchange_rate = wise_client.get_rate(currency, "JPY")
                        logger.info(f"  為替種類: Wise")
                    else:
                        # クレカレート（デフォルト）を使用
                        exchange_rate = exchange_client.get_credit_card_rate(currency, "JPY")
                        logger.info(f"  為替種類: クレカ")

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
                    logger.info(f"  → 説明生成完了 ({len(description)}文字)")
                else:
                    logger.warning("  → 説明生成失敗（空の説明が返されました）")
            else:
                logger.warning("  説明生成スキップ: APIクライアントが初期化されていません")

            # カラーミー画像URL
            colorme_image_urls = []

            # カラーミー登録用の商品ID
            registered_product_id = None

            # sync_mode が「新規登録」の場合、カラーミーへ新規登録
            if sync_mode == "新規登録" and colorme_client:
                logger.info("  カラーミーへ新規商品登録中...")

                # T列の販売価格を使用（設定されていない場合はcalculated_priceをフォールバック）
                selling_price = row_info.get("selling_price", 0)
                if selling_price > 0:
                    registration_price = selling_price
                    logger.info(f"  登録価格: {registration_price}円（T列から取得）")
                elif calculated_price:
                    registration_price = int(calculated_price)
                    logger.info(f"  登録価格: {registration_price}円（Q列から計算）")
                else:
                    registration_price = 0
                    logger.warning("  登録価格: 0円（価格情報なし）")

                # ColorMeProductを作成
                colorme_product = ColorMeProduct(
                    product_id=0,  # 新規登録なのでID未定
                    name=japanese_name or product_info.get("name", ""),
                    current_price=registration_price,
                    colorme_url="",
                    source_url=source_url,
                    quantity=quantity,
                    margin_rate=row_info.get("margin_rate", 1.1),
                    model_number=model_number,  # 自動生成した型番を使用
                    category_id_big=cat_big,
                    category_id_small=cat_small,
                    group_ids=grp_ids,
                    regular_price=registration_price,
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

                    # 商品説明を更新（新規登録APIでは説明が反映されない場合があるため）
                    if description or simple_description:
                        logger.info("  商品説明を更新中...")
                        colorme_product.product_id = new_product_id
                        update_success, update_error = colorme_client.update_product_full(colorme_product)
                        if update_success:
                            logger.info("  → 商品説明の更新成功")
                        else:
                            logger.warning(f"  → 商品説明の更新失敗: {update_error}")

                    # 画像をアップロード（Playwrightベース）
                    image_urls = product_info.get("image_urls", [])[:10]
                    if image_urls:
                        logger.info(f"  画像アップロード中（Playwright）... ({len(image_urls)}枚)")
                        try:
                            async def upload_images():
                                async with ColorMeImageUploader(headless=True) as uploader:
                                    return await uploader.upload_product_images(
                                        product_id=new_product_id,
                                        image_urls=image_urls
                                    )
                            upload_result = asyncio.run(upload_images())
                            if upload_result.success:
                                logger.info(f"  → 画像アップロード成功: {len(upload_result.uploaded_urls)}枚")
                                colorme_image_urls = upload_result.uploaded_urls
                            else:
                                logger.warning(f"  → 画像アップロード失敗: {upload_result.error_message}")
                        except Exception as e:
                            logger.warning(f"  → 画像アップロードエラー: {e}")
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
                existing_currency,
                model_number
            ):
                logger.info(f"  → 更新完了")
                success_count += 1
            else:
                logger.error(f"  → 更新失敗")
                error_count += 1

    # 結果サマリー
    logger.info("=" * 50)
    logger.info(f"処理完了: 成功 {success_count}件, 失敗 {error_count}件")

    return error_count == 0


def main():
    logger.info("=" * 50)
    logger.info("新商品情報自動入力スクリプト")
    logger.info("=" * 50)

    success = fill_incomplete_rows()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
