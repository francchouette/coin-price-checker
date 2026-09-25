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

import nest_asyncio
from playwright.sync_api import sync_playwright

# ネストされたイベントループを許可
nest_asyncio.apply()

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

## ⚠ 最重要ルール（絶対厳守）
1. **数値情報（重量・直径・純度・発行年・発行数）は「仕入れサイト仕様」または「仕入れサイト説明」に明記されているものだけ書くこと**
2. **記載がない数値は絶対に憶測・捏造・下記の例の値のコピーをしないこと**（例: 直径が仕様に無ければ【直径】行は書かない）
3. **「1オンス」「約XXmm」等はソースに書かれた通りの値を使うこと**。小数点は保持（0.56オンス、38.61mmなど）
4. **重量の使い分け**:
   - 「total weight」「gross weight」「coin weight」等 → コイン全体重量
   - 「Fine Silver: X oz」「Silver content: X oz」「Actual Silver Weight」等 → 純銀含有量
   - 両方ある場合はコイン重量を主表示、純銀含有量を補足
   - コイン重量が不明で純銀含有量のみあれば、それを使う（例: 「0.56オンス純銀」）
5. **商品名に「1 oz Silver」等と書いてあっても、それが純銀含有量か全体重量か不明な場合は判断せず、仕様に明記されていない限り「1オンス」と断定しない**
6. **仕入れ先のショップ名（販売代理店名）を商品説明・簡易説明に含めないこと（絶対厳守）**
   - 禁止例: BullionStar, Bullionstar, BullionStar社, ブリオンスター, APMEX, Apmex, エイペックス 等
   - 「発行元」は必ず実際のミント・造幣局・メーカーを記載する
     - 造幣局例: Perth Mint, Royal Canadian Mint, Austrian Mint (Münze Österreich), US Mint, Royal Mint, Singapore Mint 等
     - インゴットメーカー例: PAMP, Valcambi, Argor-Heraeus, Heraeus, Metalor, Umicore 等
   - ソースに造幣局・メーカー名が明記されていない場合は「発行元」への言及を省略する（推測しない）
   - 販売代理店の名前を出さないことで、他店への流用リスクや誤情報を回避する

## 商品説明（詳細）の要件
- 500文字以内に収めること（厳守）
- HTMLタグは使用しないこと
- 専門用語は正確に使用（地金型金貨、純金、K24など）
- **主観的表現・営業文句は禁止**（「魅力的な」「輝きを放つ」「ぜひお手元に」「大変喜ばれる」「希少価値」「資産形成」等）
- **判明している事実のみを記載する**（推測・憶測・美辞麗句は書かない）
- **判明する事実が少ない場合は無理に文字数を稼がない**（450文字未満でも良い）
- 文章は句点（。）で自然に終わること

## 商品説明（詳細）の出力フォーマット

### ブロック1: スペック行（【】囲み）
以下の項目を上から順に、**判明しているもののみ**記載。不明な項目は該当行を丸ごと省略。

- 【品位】　例: 純度99.9% 純銀（SV999） / 純度99.99% 純金（K24）
- 【重量】　例: 1オンス（約31.1g） / 1/10オンス（約3.11g） / 100g
- 【直径】　例: 約38.6mm
- 【額面】　例: 1ドル / 50ユーロ / 100元
- 【発行】　例: パースミント / ロイヤル・カナディアン・ミント / オーストリア造幣局

### ブロック2: 空行

### ブロック3: 本文（判明している事実のみ、以下の順序）
1. **発行国・造幣局・法定通貨としての位置づけ**（例: 「○○国の法定通貨として、○○造幣局が発行する銀貨」）
2. **表面・裏面のデザインと、その由来**（史実・出典が示せる範囲のみ。「〜を象徴する」等の主観的解釈は書かない）
3. **素材と製造上の特徴**（偽造防止技術、鏡面仕上げ、リーデッドエッジ等、事実として確認できるもののみ）
4. **同銘柄の他年号・他サイズとの違い**（データがある場合のみ。無ければ書かない）

## 商品説明（詳細）の具体例（※以下の数値はサンプルであり、実際の商品で流用してはいけない）
【品位】　純度99.9% 純銀（SV999）
【重量】　1オンス（約31.1g）
【直径】　約38.6mm
【額面】　1ドル
【発行】　パースミント

オーストラリアの法定通貨として、西オーストラリア州政府所有のパースミントが発行する銀貨です。表面にはエリザベス2世女王の肖像とその名の元号が刻まれています。裏面には干支シリーズの主題である辰（ドラゴン）が中央に配置され、周囲に発行年と重量・純度が刻印されています。純度99.9%の銀を使用した地金型銀貨で、リーデッドエッジ（ふち加工）により偽造防止と識別性が確保されています。本シリーズは1996年開始のオーストラリア・ルナーシリーズの第三期にあたり、1オンスのほか1/2オンス、2オンス、5オンス、10オンス、1キログラムのサイズ展開があります。

## スペック情報が不足している場合の対応例
仕様に明記がない項目は【】行を省略する。例:
- 直径・額面・発行元が不明なら「【品位】」「【重量】」の2行のみ出力
- スペックが完全に空なら【】行を一切出力せず、本文のみで説明する
- 本文の4項目についても、判明していない項目は該当箇所ごと丸ごと省略する（無理に埋めない）

## 簡易説明の要件
- 1-2文（80-120文字）
- HTMLタグなし・改行なし
- 製造国・素材・重量・デザインの特徴を、判明している事実のみで簡潔に記載
- 主観的表現・営業文句は禁止（「投資メリット」「魅力的」等は書かない）
- 判明する事実が少ない場合は80文字未満でも良い

JSON形式で出力してください:
{{
    "description": "商品説明（詳細）",
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


# AI商品名生成用プロンプトテンプレート
# 事前に生成された商品説明を「正解データ」として参照し、それに合致する商品名を生成する
NAME_PROMPT = """あなたは貴金属コイン・地金のECサイトの商品名策定担当です。

## 入力情報
- 仕入れ先の英語商品名: {product_name}
- 仕入れ先の仕様: {source_specs}
- 生成済み日本語商品説明（この内容と矛盾しない商品名にすること）: {ja_description}

## 出力フォーマット
必ず以下の形式で日本語商品名を生成してください:
「[年号] [国名] [シリーズ名/メーカー名] [重量] [製品種類] 新品未使用 【{quantity}{unit_word}】」

## 各要素の書き方
- **年号**: 商品説明・英語名から抽出。不明なら省略。
- **国名**: 発行国/製造国。日本語表記（例: オーストリア, カナダ, アメリカ, スイス, オーストラリア, フィジー, ニウエ, ツバル 等）。パースミント/PAMP/Valcambi等はメーカー名で、国名（スイス/オーストラリア）を別途付ける。
- **シリーズ名/メーカー名**: 商品説明が最重要ソース。地金型の場合はシリーズ名（ウィーン、ブリタニア、メイプルリーフ、カンガルー、コアラ、パンダ、イーグル、クルーガーランド、ドラゴン等）。インゴットの場合はメーカー名（PAMP、Valcambi、Argor Heraeus、パースミント等）。プルーフや記念コインの場合は該当テーマ名。
- **重量**: 小数点も正確に反映（例: 0.84オンス、1オンス、1/2オンス、10オンス、100グラム）。「84オンス」のような桁ずれは絶対禁止。
  - **重量の優先順位**: (1) コイン全体の重量（total/gross weight）が明記されていればそれを使用。(2) 明記されず「〇oz Fine Silver」「Silver content: 〇oz」等の**純銀/純金含有量**しか書かれていない場合は、その含有量を使用（例: 0.56オンス）。
  - **重量が全く不明な場合は省略**する（例: 「2025 カメルーン 花シリーズ マティス 地金型銀貨 新品未使用 【1枚】」）。憶測で「1オンス」等を書かない。
- **製品種類**:
  - 地金型金貨/銀貨/プラチナコイン/パラジウムコイン
  - シルバーインゴット/ゴールドインゴット/プラチナインゴット
  - プルーフ〇〇貨（プルーフ品の場合）
  - 鑑定済み〇〇貨 (グレード) (鑑定機関)（鑑定品の場合）

## 重要な注意事項
- **説明文と矛盾しないこと**（説明文が最も信頼できるソース）
- **重量の小数点を絶対に落とさない**
- 「造幣局」「ミント」は使わず、「パース」「RCM」等の短縮表記
- 出力は商品名1行のみ（余計な説明・引用符・JSONは不要）

## 出力
"""


class AIProductNameGenerator:
    """AIで日本語商品名を生成するクラス（商品説明を参照して精度を上げる）"""

    def __init__(self):
        self.genai_model = None
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project="coin-price-tracker-479614", location="us-central1")
            self.genai_model = GenerativeModel("gemini-2.5-pro")
            logger.info("AI商品名生成器: Vertex AI Gemini初期化完了")
        except Exception as e:
            logger.warning(f"AI商品名生成器: Vertex AI初期化エラー: {e}")

    def generate(self, product_info: dict, quantity: int = 1) -> str:
        """商品説明を参照して日本語商品名を生成

        Args:
            product_info: 商品情報
                - name: 英語商品名
                - specs: 仕様
                - ja_description: AI生成済みの日本語商品説明（必須）
            quantity: 枚数

        Returns:
            str: 日本語商品名（失敗時は空文字）
        """
        if not self.genai_model:
            return ""

        ja_desc = product_info.get("ja_description", "")
        if not ja_desc:
            logger.warning("  AI商品名生成: 日本語説明がないためスキップ")
            return ""

        # 単位語（インゴットは【1本】、コインは【1枚】など）
        # 説明にインゴット/バーが含まれる場合は「本」、それ以外は「枚」
        desc_lower = ja_desc.lower()
        if "インゴット" in ja_desc or "バー" in ja_desc:
            unit_word = "本"
        else:
            unit_word = "枚"

        prompt = NAME_PROMPT.format(
            product_name=product_info.get("name", ""),
            source_specs=product_info.get("specs", ""),
            ja_description=ja_desc[:600],
            quantity=quantity,
            unit_word=unit_word,
        )

        try:
            logger.info("  Gemini APIを呼び出し中（商品名生成）...")
            response = self.genai_model.generate_content(prompt)
            name = response.text.strip()
            # 余計な引用符や改行、コードブロックを削除
            name = name.strip('"\'` \n\r\t')
            # 複数行の場合は最初の非空行
            for line in name.splitlines():
                line = line.strip('"\'` \n\r\t')
                if line:
                    name = line
                    break
            if name:
                logger.info(f"  AI商品名生成成功: {name[:60]}")
                return name
        except Exception as e:
            logger.error(f"  AI商品名生成エラー: {e}")

        return ""


class DescriptionGenerator:
    """商品説明をAIで生成するクラス"""

    def __init__(self):
        self.genai_model = None
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project="coin-price-tracker-479614", location="us-central1")
            self.genai_model = GenerativeModel("gemini-2.5-pro")
            logger.info("説明生成器: Vertex AI Gemini初期化完了")
        except Exception as e:
            logger.warning(f"説明生成器: Vertex AI初期化エラー: {e}")

    def generate(self, product_info: dict) -> tuple[str, str]:
        """
        商品説明を生成する

        Args:
            product_info: スクレイピングで取得した商品情報

        Returns:
            tuple[str, str]: (商品説明, 簡易説明)
        """
        if not self.genai_model:
            logger.warning("Vertex AI Geminiが初期化されていません。説明は空になります。")
            return "", ""

        prompt = DESCRIPTION_PROMPT.format(
            product_name=product_info.get("name", ""),
            price=product_info.get("price", 0),
            currency=product_info.get("currency", "USD"),
            source_description=product_info.get("description", ""),
            source_specs=product_info.get("specs", "")
        )

        try:
            logger.info("  Gemini APIを呼び出し中（説明生成）...")
            response = self.genai_model.generate_content(prompt)
            response_text = response.text
            logger.debug(f"  API応答（先頭200文字）: {response_text[:200]}")

            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    desc = data.get("description", "")
                    simple_desc = data.get("simple_description", "")
                    # 500文字超の場合は最後の句点で切る
                    if len(desc) > 500:
                        truncated = desc[:500]
                        last_period = truncated.rfind("。")
                        desc = truncated[:last_period + 1] if last_period != -1 else truncated
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


# SEO項目生成用プロンプトテンプレート
SEO_PROMPT = """あなたは貴金属コイン・地金のECサイトのSEO専門家です。

以下の商品情報を元に、SEO最適化された項目を作成してください。

## 入力情報
- 商品名: {product_name}
- 価格: {price}円
- 仕入れサイト説明: {source_description}
- 仕入れサイト仕様: {source_specs}

## 出力項目

### 1. ページタイトル（title タグ）
- 80文字以内
- 形式: 「[年号] [商品名] [国名] [重量] [素材]貨 | 世界の金貨・銀貨・インゴット専門店 | ワールドコインマーケット」
- 例: 「2026 メイプルリーフ カナダ 1oz 金貨 | 世界の金貨・銀貨・インゴット専門店 | ワールドコインマーケット」
- 商品名から年号、国名、重量、素材を抽出して構成

### 2. メタディスクリプション（meta description）
- 160文字以内
- 形式: 「【新品】[年号]年版[商品名][重量]（[純度]）を販売中。世界の金、銀、地金型コイン、プレミアムコイン、インゴットを豊富に取り扱う専門店「ワールドコインマーケット」公式通販。長期的な資産形成はもちろん、趣味のコレクションにも。手元に残る確かな価値を、当店でお探しください。」
- 例: 「【新品】2026年版メイプルリーフ金貨1オンス(純金)を販売中。世界の金、銀、地金型コイン、プレミアムコイン、インゴットを豊富に取り扱う専門店「ワールドコインマーケット」公式通販。長期的な資産形成はもちろん、趣味のコレクションにも。手元に残る確かな価値を、当店でお探しください。」

### 3. メタキーワード（meta keywords）
- カンマ区切りで10-15個
- 商品名、素材、製造国、用途、関連ワード
- 例: 「メイプルリーフ金貨,カナダ金貨,1オンス金貨,純金コイン,2026年金貨,投資用金貨,ゴールドコイン,地金型金貨,金貨購入,ワールドコインマーケット」

## 重要な注意事項
- 日本語で出力
- キーワードを自然に含める
- 誇張表現は避ける
- 店舗名は「ワールドコインマーケット」を使用
- 重量の表記: 1oz, 1/2oz, 1/4oz, 1/10oz などを使用
- 素材の表記: 金貨、銀貨、プラチナ貨、インゴット など
- 「造幣局」「ミント」という表現は使用禁止
- 製造元名は可能な限り短くする（例: Perth Mint → パース、Royal Canadian Mint → RCM、PAMP → PAMP）

JSON形式で出力してください:
{{
    "page_title": "ページタイトル",
    "meta_description": "メタディスクリプション",
    "meta_keywords": "キーワード1,キーワード2,キーワード3,..."
}}
"""


class SEOGenerator:
    """SEO項目（ページタイトル、メタディスクリプション、メタキーワード）をAIで生成するクラス"""

    def __init__(self):
        self.genai_model = None
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project="coin-price-tracker-479614", location="us-central1")
            self.genai_model = GenerativeModel("gemini-2.5-pro")
            logger.info("SEO生成器: Vertex AI Gemini初期化完了")
        except Exception as e:
            logger.warning(f"SEO生成器: Vertex AI初期化エラー: {e}")

    def generate(self, product_info: dict) -> tuple[str, str, str]:
        """
        SEO項目を生成する

        Args:
            product_info: スクレイピングで取得した商品情報
                - name: 商品名
                - price: 価格（日本円）
                - description: 商品説明
                - specs: 仕様

        Returns:
            tuple[str, str, str]: (ページタイトル, メタディスクリプション, メタキーワード)
        """
        if not self.genai_model:
            logger.warning("Vertex AI Geminiが初期化されていません。SEO項目は空になります。")
            return "", "", ""

        prompt = SEO_PROMPT.format(
            product_name=product_info.get("name", ""),
            price=product_info.get("price", 0),
            source_description=product_info.get("description", "")[:500],
            source_specs=product_info.get("specs", "")
        )

        try:
            logger.info("  Gemini APIを呼び出し中（SEO生成）...")
            response = self.genai_model.generate_content(prompt)
            response_text = response.text
            logger.debug(f"  API応答（先頭200文字）: {response_text[:200]}")

            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    page_title = data.get("page_title", "")
                    meta_desc = data.get("meta_description", "")
                    meta_keywords = data.get("meta_keywords", "")
                    if page_title:
                        logger.info(f"  SEO生成成功: タイトル={page_title[:30]}...")
                    else:
                        logger.warning("  JSONは取得できたが、page_titleが空です")
                    return page_title, meta_desc, meta_keywords
                except json.JSONDecodeError as je:
                    logger.error(f"  JSON解析エラー: {je}")
                    logger.error(f"  解析対象: {json_match.group()[:200]}")
            else:
                logger.warning("  応答にJSONが含まれていません")
                logger.warning(f"  応答内容: {response_text[:300]}")

        except Exception as e:
            logger.error(f"SEO生成エラー: {e}")
            import traceback
            logger.error(traceback.format_exc())

        return "", "", ""


class ImageAnalyzer:
    """AIを使って商品画像を解析・検証するクラス"""

    def __init__(self):
        self.genai_model = None
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project="coin-price-tracker-479614", location="us-central1")
            self.genai_model = GenerativeModel("gemini-2.5-pro")
            logger.info("画像解析器: Vertex AI Gemini初期化完了")
        except Exception as e:
            logger.warning(f"画像解析器: Vertex AI初期化エラー: {e}")

    def filter_product_images(self, image_urls: list[str], product_name: str) -> list[str]:
        """
        画像URLリストから正しい商品画像のみをフィルタリングする

        Args:
            image_urls: 候補となる画像URLのリスト
            product_name: 商品名

        Returns:
            list[str]: 正しい商品画像のURLリスト
        """
        if not self.genai_model or not image_urls:
            return image_urls

        try:
            from vertexai.generative_models import Part
            import requests

            # 最大5枚の画像をAIで検証（コスト削減）
            urls_to_check = image_urls[:5]

            # 画像コンテンツを構築（Gemini形式）
            content = []
            for i, url in enumerate(urls_to_check):
                try:
                    # 画像をダウンロード
                    img_response = requests.get(url, timeout=10)
                    if img_response.status_code == 200:
                        content_type = img_response.headers.get('content-type', 'image/jpeg')
                        content.append(Part.from_data(img_response.content, mime_type=content_type))
                        content.append(f"画像{i+1}: {url.split('/')[-1]}")
                except Exception as e:
                    logger.warning(f"  画像取得エラー: {url} - {e}")

            content.append(f"""上記の画像を確認してください。

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
""")

            logger.info(f"  AI画像検証中... ({len(urls_to_check)}枚)")
            response = self.genai_model.generate_content(content)
            response_text = response.text
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
        if not self.genai_model:
            return []

        try:
            from vertexai.generative_models import Part

            # スクリーンショットを取得
            screenshot_bytes = page.screenshot(full_page=False)

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

            # Gemini形式でコンテンツを構築
            content = [
                Part.from_data(screenshot_bytes, mime_type="image/png"),
                f"""このスクリーンショットは貴金属商品のページです。

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
            ]

            logger.info("  スクリーンショットからAI画像特定中...")
            response = self.genai_model.generate_content(content)
            response_text = response.text
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
    """商品名からカテゴリーを自動判定するクラス（AIサポート）

    新グループ階層構造（5階層）:
    L1: 素材（ゴールド/シルバー/プラチナ/パラジウム/カッパー）
      └─ L2: 形状（コイン（ラウンド）/ インゴット（バー））
          └─ L3: タイプ（地金型 / コレクション / 鑑定済み）
              └─ L4: 国名（アメリカ、カナダ、イギリス...）
                  └─ L5: シリーズ名（イーグル、メイプルリーフ...）※地金型の主要国のみ
    """

    # カテゴリーID（5素材対応）
    CATEGORY_IDS = {
        "gold": 2977963,      # ゴールド（金）
        "silver": 2977964,    # シルバー（銀）
        "platinum": 2977965,  # プラチナ
        "palladium": 2977966, # パラジウム
        "copper": 2977967,    # カッパー（銅）
    }

    # L1: 素材グループ（トップレベル）
    GROUP_L1 = {
        "gold": 3151359,
        "silver": 3151360,
        "platinum": 3151361,
        "palladium": 3151362,
        "copper": 3151363,
    }

    # L2: 形状グループ（素材の子）
    GROUP_L2 = {
        ("gold", "coin"): 3151364,
        ("gold", "bar"): 3151365,
        ("silver", "coin"): 3151366,
        ("silver", "bar"): 3151367,
        ("platinum", "coin"): 3151368,
        ("platinum", "bar"): 3151369,
        ("palladium", "coin"): 3151370,
        ("palladium", "bar"): 3151371,
        ("copper", "coin"): 3151372,
        ("copper", "bar"): 3151373,
    }

    # L3: タイプグループ（形状の子）
    # bullion=地金型, collection=コレクション, graded=鑑定済み
    GROUP_L3 = {
        # ゴールド
        ("gold", "coin", "bullion"): 3151374,
        ("gold", "coin", "collection"): 3151375,
        ("gold", "coin", "graded"): 3151376,
        ("gold", "bar", "bullion"): 3151377,
        # シルバー
        ("silver", "coin", "bullion"): 3151378,
        ("silver", "coin", "collection"): 3151379,
        ("silver", "coin", "graded"): 3151380,
        ("silver", "bar", "bullion"): 3151381,
        # プラチナ
        ("platinum", "coin", "bullion"): 3151382,
        ("platinum", "coin", "collection"): 3151383,
        ("platinum", "coin", "graded"): 3151384,
        ("platinum", "bar", "bullion"): 3151385,
        # パラジウム
        ("palladium", "coin", "bullion"): 3151386,
        ("palladium", "bar", "bullion"): 3151387,
        # カッパー
        ("copper", "coin", "bullion"): 3151388,
        ("copper", "bar", "bullion"): 3151389,
    }

    # L4: 国名グループ - 地金型コイン
    GROUP_L4_BULLION_COIN = {
        # ゴールド
        ("gold", "usa"): 3151390,
        ("gold", "canada"): 3151391,
        ("gold", "austria"): 3151392,
        ("gold", "uk"): 3151393,
        ("gold", "australia"): 3151394,
        ("gold", "china"): 3151395,
        ("gold", "south_africa"): 3151396,
        ("gold", "mexico"): 3151397,
        ("gold", "st_helena"): 3151398,
        ("gold", "singapore"): 3151399,
        ("gold", "palau"): 3151400,
        ("gold", "barbados"): 3151401,
        ("gold", "cameroon"): 3151402,
        ("gold", "chad"): 3151403,
        ("gold", "armenia"): 3151404,
        ("gold", "tuvalu"): 3151405,
        ("gold", "niue"): 3151406,
        ("gold", "cook_islands"): 3151407,
        ("gold", "fiji"): 3151408,
        ("gold", "solomon_islands"): 3151409,
        ("gold", "tokelau"): 3151410,
        ("gold", "france"): 3151411,
        ("gold", "samoa"): 3151412,
        ("gold", "other"): 3151413,
        # シルバー
        ("silver", "usa"): 3151447,
        ("silver", "canada"): 3151448,
        ("silver", "austria"): 3151449,
        ("silver", "uk"): 3151450,
        ("silver", "australia"): 3151451,
        ("silver", "china"): 3151452,
        ("silver", "south_africa"): 3151453,
        ("silver", "mexico"): 3151454,
        ("silver", "st_helena"): 3151455,
        ("silver", "singapore"): 3151456,
        ("silver", "palau"): 3151457,
        ("silver", "barbados"): 3151458,
        ("silver", "cameroon"): 3151459,
        ("silver", "chad"): 3151460,
        ("silver", "armenia"): 3151461,
        ("silver", "tuvalu"): 3151462,
        ("silver", "niue"): 3151463,
        ("silver", "cook_islands"): 3151464,
        ("silver", "fiji"): 3151465,
        ("silver", "solomon_islands"): 3151466,
        ("silver", "tokelau"): 3151467,
        ("silver", "france"): 3151468,
        ("silver", "samoa"): 3151469,
        ("silver", "other"): 3151470,
        # プラチナ
        ("platinum", "usa"): 3151504,
        ("platinum", "canada"): 3151505,
        ("platinum", "austria"): 3151506,
        ("platinum", "uk"): 3151507,
        ("platinum", "australia"): 3151508,
        ("platinum", "china"): 3151509,
        ("platinum", "south_africa"): 3151510,
        ("platinum", "mexico"): 3151511,
        ("platinum", "st_helena"): 3151512,
        ("platinum", "singapore"): 3151513,
        ("platinum", "palau"): 3151514,
        ("platinum", "barbados"): 3151515,
        ("platinum", "cameroon"): 3151516,
        ("platinum", "chad"): 3151517,
        ("platinum", "armenia"): 3151518,
        ("platinum", "tuvalu"): 3151519,
        ("platinum", "niue"): 3151520,
        ("platinum", "cook_islands"): 3151521,
        ("platinum", "fiji"): 3151522,
        ("platinum", "solomon_islands"): 3151523,
        ("platinum", "tokelau"): 3151524,
        ("platinum", "france"): 3151525,
        ("platinum", "samoa"): 3151526,
        ("platinum", "other"): 3151527,
        # パラジウム
        ("palladium", "usa"): 3151561,
        ("palladium", "other"): 3151562,
        # カッパー
        ("copper", "usa"): 3151565,
        ("copper", "other"): 3151566,
    }

    # L4: 国名グループ - 地金型バー
    GROUP_L4_BULLION_BAR = {
        # ゴールド
        ("gold", "usa"): 3151442,
        ("gold", "canada"): 3151443,
        ("gold", "uk"): 3151444,
        ("gold", "germany"): 3151445,
        ("gold", "other"): 3151446,
        # シルバー
        ("silver", "usa"): 3151499,
        ("silver", "canada"): 3151500,
        ("silver", "uk"): 3151501,
        ("silver", "germany"): 3151502,
        ("silver", "other"): 3151503,
        # プラチナ
        ("platinum", "usa"): 3151556,
        ("platinum", "canada"): 3151557,
        ("platinum", "uk"): 3151558,
        ("platinum", "germany"): 3151559,
        ("platinum", "other"): 3151560,
        # パラジウム
        ("palladium", "usa"): 3151563,
        ("palladium", "other"): 3151564,
        # カッパー
        ("copper", "usa"): 3151567,
        ("copper", "other"): 3151568,
    }

    # L4: 国名グループ - コレクション
    GROUP_L4_COLLECTION = {
        # ゴールド
        ("gold", "usa"): 3151414,
        ("gold", "canada"): 3151415,
        ("gold", "austria"): 3151416,
        ("gold", "uk"): 3151417,
        ("gold", "australia"): 3151418,
        ("gold", "china"): 3151419,
        ("gold", "south_africa"): 3151420,
        ("gold", "mexico"): 3151421,
        ("gold", "st_helena"): 3151422,
        ("gold", "singapore"): 3151423,
        ("gold", "palau"): 3151424,
        ("gold", "barbados"): 3151425,
        ("gold", "cameroon"): 3151426,
        ("gold", "chad"): 3151427,
        ("gold", "armenia"): 3151428,
        ("gold", "tuvalu"): 3151429,
        ("gold", "niue"): 3151430,
        ("gold", "cook_islands"): 3151431,
        ("gold", "fiji"): 3151432,
        ("gold", "solomon_islands"): 3151433,
        ("gold", "tokelau"): 3151434,
        ("gold", "france"): 3151435,
        ("gold", "samoa"): 3151436,
        ("gold", "other"): 3151437,
        # シルバー
        ("silver", "usa"): 3151471,
        ("silver", "canada"): 3151472,
        ("silver", "austria"): 3151473,
        ("silver", "uk"): 3151474,
        ("silver", "australia"): 3151475,
        ("silver", "china"): 3151476,
        ("silver", "south_africa"): 3151477,
        ("silver", "mexico"): 3151478,
        ("silver", "st_helena"): 3151479,
        ("silver", "singapore"): 3151480,
        ("silver", "palau"): 3151481,
        ("silver", "barbados"): 3151482,
        ("silver", "cameroon"): 3151483,
        ("silver", "chad"): 3151484,
        ("silver", "armenia"): 3151485,
        ("silver", "tuvalu"): 3151486,
        ("silver", "niue"): 3151487,
        ("silver", "cook_islands"): 3151488,
        ("silver", "fiji"): 3151489,
        ("silver", "solomon_islands"): 3151490,
        ("silver", "tokelau"): 3151491,
        ("silver", "france"): 3151492,
        ("silver", "samoa"): 3151493,
        ("silver", "other"): 3151494,
        # プラチナ
        ("platinum", "usa"): 3151528,
        ("platinum", "canada"): 3151529,
        ("platinum", "austria"): 3151530,
        ("platinum", "uk"): 3151531,
        ("platinum", "australia"): 3151532,
        ("platinum", "china"): 3151533,
        ("platinum", "south_africa"): 3151534,
        ("platinum", "mexico"): 3151535,
        ("platinum", "st_helena"): 3151536,
        ("platinum", "singapore"): 3151537,
        ("platinum", "palau"): 3151538,
        ("platinum", "barbados"): 3151539,
        ("platinum", "cameroon"): 3151540,
        ("platinum", "chad"): 3151541,
        ("platinum", "armenia"): 3151542,
        ("platinum", "tuvalu"): 3151543,
        ("platinum", "niue"): 3151544,
        ("platinum", "cook_islands"): 3151545,
        ("platinum", "fiji"): 3151546,
        ("platinum", "solomon_islands"): 3151547,
        ("platinum", "tokelau"): 3151548,
        ("platinum", "france"): 3151549,
        ("platinum", "samoa"): 3151550,
        ("platinum", "other"): 3151551,
    }

    # L4: テーマグループ - コレクション
    GROUP_L4_THEME = {
        ("gold", "movie"): 3151438,
        ("gold", "anime"): 3151439,
        ("gold", "music"): 3151440,
        ("gold", "other_theme"): 3151441,
        ("silver", "movie"): 3151495,
        ("silver", "anime"): 3151496,
        ("silver", "music"): 3151497,
        ("silver", "other_theme"): 3151498,
        ("platinum", "movie"): 3151552,
        ("platinum", "anime"): 3151553,
        ("platinum", "music"): 3151554,
        ("platinum", "other_theme"): 3151555,
    }

    # L5: シリーズグループ - 地金型コイン（主要国のみ）
    GROUP_L5_SERIES = {
        # ゴールド - アメリカ
        ("gold", "usa", "eagle"): 3151569,
        ("gold", "usa", "buffalo"): 3151570,
        ("gold", "usa", "other"): 3151571,
        # ゴールド - カナダ
        ("gold", "canada", "maple"): 3151572,
        ("gold", "canada", "other"): 3151573,
        # ゴールド - オーストリア
        ("gold", "austria", "vienna"): 3151574,
        ("gold", "austria", "other"): 3151575,
        # ゴールド - イギリス
        ("gold", "uk", "britannia"): 3151576,
        ("gold", "uk", "other"): 3151577,
        # ゴールド - オーストラリア
        ("gold", "australia", "kangaroo"): 3151578,
        ("gold", "australia", "koala"): 3151579,
        ("gold", "australia", "kookaburra"): 3151580,
        ("gold", "australia", "lunar"): 3151581,
        ("gold", "australia", "other"): 3151582,
        # ゴールド - 中国
        ("gold", "china", "panda"): 3151583,
        ("gold", "china", "other"): 3151584,
        # ゴールド - 南アフリカ
        ("gold", "south_africa", "krugerrand"): 3151585,
        ("gold", "south_africa", "other"): 3151586,
        # ゴールド - メキシコ
        ("gold", "mexico", "libertad"): 3151587,
        ("gold", "mexico", "other"): 3151588,
        # シルバー - アメリカ
        ("silver", "usa", "eagle"): 3151589,
        ("silver", "usa", "buffalo"): 3151590,
        ("silver", "usa", "other"): 3151591,
        # シルバー - カナダ
        ("silver", "canada", "maple"): 3151592,
        ("silver", "canada", "other"): 3151593,
        # シルバー - オーストリア
        ("silver", "austria", "vienna"): 3151594,
        ("silver", "austria", "other"): 3151595,
        # シルバー - イギリス
        ("silver", "uk", "britannia"): 3151596,
        ("silver", "uk", "other"): 3151597,
        # シルバー - オーストラリア
        ("silver", "australia", "kangaroo"): 3151598,
        ("silver", "australia", "koala"): 3151599,
        ("silver", "australia", "kookaburra"): 3151600,
        ("silver", "australia", "lunar"): 3151601,
        ("silver", "australia", "other"): 3151602,
        # シルバー - 中国
        ("silver", "china", "panda"): 3151603,
        ("silver", "china", "other"): 3151604,
        # シルバー - 南アフリカ
        ("silver", "south_africa", "krugerrand"): 3151605,
        ("silver", "south_africa", "elephant"): 3151606,
        ("silver", "south_africa", "other"): 3151607,
        # シルバー - メキシコ
        ("silver", "mexico", "libertad"): 3151608,
        ("silver", "mexico", "other"): 3151609,
        # プラチナ - アメリカ
        ("platinum", "usa", "eagle"): 3151610,
        ("platinum", "usa", "other"): 3151611,
        # プラチナ - カナダ
        ("platinum", "canada", "maple"): 3151612,
        ("platinum", "canada", "other"): 3151613,
        # プラチナ - オーストラリア
        ("platinum", "australia", "kangaroo"): 3151614,
        ("platinum", "australia", "koala"): 3151615,
        ("platinum", "australia", "other"): 3151616,
    }

    def __init__(self, categories: list[dict], groups: list[dict], colorme_client=None):
        self.categories = categories
        self.groups = groups
        self.colorme_client = colorme_client

        # Vertex AI Geminiクライアントを初期化
        self.genai_model = None
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project="coin-price-tracker-479614", location="us-central1")
            self.genai_model = GenerativeModel("gemini-2.5-pro")
            logger.info("カテゴリー判定器: Vertex AI Gemini初期化完了")
        except Exception as e:
            logger.warning(f"カテゴリー判定器: Vertex AI初期化エラー: {e}")

        # APIから取得したグループ名→{id, parent_id}のマッピングを作成
        self.existing_groups = {}
        for g in groups:
            parent_id = g.get("parent_id", 0) or 0
            self.existing_groups[g["name"]] = {"id": g["id"], "parent_id": parent_id}

    def detect(self, product_name: str, url: str = "") -> tuple[int, int, list[int]]:
        """
        商品名からカテゴリーIDとグループIDを判定（常にAI判定を使用）

        新グループ階層（5階層）:
        L1: 素材 → L2: 形状 → L3: タイプ → L4: 国名 → L5: シリーズ

        振り分けルール:
        - 地金型・主要国・有名シリーズ → L5シリーズグループ
        - 地金型・主要国・その他 → L5その他グループ
        - 地金型・マイナー国 → L4国名グループ
        - インゴット（バー） → L4国名グループ
        - 鑑定済み → L3鑑定済みグループ
        - コレクション（国のみ） → L4国名グループ
        - コレクション（国+テーマ） → L4国名 + L4テーマ（2グループ）

        Args:
            product_name: 商品名
            url: 商品URL

        Returns:
            tuple[int, int, list[int]]: (大カテゴリーID, 小カテゴリーID(常に0), グループIDリスト)
        """
        # AIで判定（常時実行）
        ai_result = self._detect_with_ai(product_name, url)

        # AI判定結果を取得（フォールバック付き）
        metal = ai_result.get("metal", "silver")
        if metal not in self.CATEGORY_IDS:
            metal = "silver"

        shape = ai_result.get("shape", "coin")
        if shape not in ["coin", "bar"]:
            shape = "coin"

        product_type = ai_result.get("type", "bullion")
        if product_type not in ["bullion", "collection", "graded"]:
            product_type = "bullion"

        country = ai_result.get("country", "other")
        series = ai_result.get("series", "other")
        theme = ai_result.get("theme")

        category_id = self.CATEGORY_IDS[metal]
        group_ids = []

        # === グループID決定 ===

        if product_type == "graded":
            # 鑑定済み → L3で終了
            key = (metal, shape, "graded")
            if key in self.GROUP_L3:
                group_ids.append(self.GROUP_L3[key])
            else:
                # 鑑定済みグループがない素材の場合はbullionにフォールバック
                fallback_key = (metal, shape, "bullion")
                if fallback_key in self.GROUP_L3:
                    group_ids.append(self.GROUP_L3[fallback_key])

        elif product_type == "collection":
            # コレクション → L4国名グループ
            key = (metal, country)
            if key in self.GROUP_L4_COLLECTION:
                group_ids.append(self.GROUP_L4_COLLECTION[key])
            else:
                # その他国にフォールバック
                fallback_key = (metal, "other")
                if fallback_key in self.GROUP_L4_COLLECTION:
                    group_ids.append(self.GROUP_L4_COLLECTION[fallback_key])

            # テーマ判定（映画/アニメ/ミュージック）→ 2グループ目
            if theme and theme in ["movie", "anime", "music"]:
                theme_key = (metal, theme)
                if theme_key in self.GROUP_L4_THEME:
                    group_ids.append(self.GROUP_L4_THEME[theme_key])

        elif shape == "bar":
            # インゴット（バー） → L4国名グループ
            key = (metal, country)
            if key in self.GROUP_L4_BULLION_BAR:
                group_ids.append(self.GROUP_L4_BULLION_BAR[key])
            else:
                # その他国にフォールバック
                fallback_key = (metal, "other")
                if fallback_key in self.GROUP_L4_BULLION_BAR:
                    group_ids.append(self.GROUP_L4_BULLION_BAR[fallback_key])

        else:
            # 地金型コイン
            # L5シリーズグループを探す
            l5_key = (metal, country, series)
            if l5_key in self.GROUP_L5_SERIES:
                group_ids.append(self.GROUP_L5_SERIES[l5_key])
            else:
                # L5「その他」グループを探す
                l5_other_key = (metal, country, "other")
                if l5_other_key in self.GROUP_L5_SERIES:
                    group_ids.append(self.GROUP_L5_SERIES[l5_other_key])
                else:
                    # L4国名グループにフォールバック
                    l4_key = (metal, country)
                    if l4_key in self.GROUP_L4_BULLION_COIN:
                        group_ids.append(self.GROUP_L4_BULLION_COIN[l4_key])
                    else:
                        # その他諸国にフォールバック
                        fallback_key = (metal, "other")
                        if fallback_key in self.GROUP_L4_BULLION_COIN:
                            group_ids.append(self.GROUP_L4_BULLION_COIN[fallback_key])

        logger.info(f"  判定: 素材={metal}, 形状={shape}, タイプ={product_type}, 国={country}")
        logger.info(f"  → カテゴリー={category_id}, グループ={group_ids}")

        return category_id, 0, group_ids

    def detect_v2(self, product_name: str, url: str = "") -> tuple[int, int, list[int]]:
        """
        商品名から大カテゴリーIDとグループIDを動的に判定（カラーミーAPI取得のグループ一覧から直接選択）

        旧 detect() はハードコード定数（GROUP_L1〜L5）に依存していたが、
        本メソッドは __init__ で取得した self.existing_groups からAIに最適なグループ名を選ばせる。
        グループ構成の変更に自動追従できる。

        Args:
            product_name: 商品名
            url: 商品URL

        Returns:
            tuple[int, int, list[int]]: (大カテゴリーID, 0, グループIDリスト)
        """
        if not self.genai_model:
            raise RuntimeError("AI APIが利用できません。Vertex AI認証を確認してください。")

        # 大カテゴリーID判定（素材） — 旧 _detect_with_ai の metal 判定を流用
        ai_meta = self._detect_with_ai(product_name, url)
        metal = ai_meta.get("metal", "silver")
        if metal not in self.CATEGORY_IDS:
            metal = "silver"
        category_id = self.CATEGORY_IDS[metal]

        # グループ名候補（カラーミー登録済、"なし"は除外）
        group_names = [n for n in self.existing_groups.keys() if n and n != "なし"]
        if not group_names:
            logger.warning("detect_v2: グループ一覧が空のためグループなしで返却")
            return (category_id, 0, [])

        prompt = f"""あなたは貴金属コイン・地金の専門家です。以下の商品を、提示されたグループ一覧から該当するグループに分類してください。

## 商品情報
- 商品名: {product_name}
- URL: {url}

## グループ一覧（この中から完全一致する名前のみ選択）
{chr(10).join('- ' + n for n in group_names)}

## 判定ルール
1. **素材グループ**（ゴールド/シルバー/プラチナ/パラジウム/カッパー）から必ず1つ選ぶ
2. **シリーズ/銘柄グループ**が該当する場合は追加で1つ選ぶ
   例: American Eagle金貨 → 「アメリカンイーグル金貨」、Maple Leaf銀貨 → 「メイプルリーフ銀貨」、Vienna金貨 → 「ウィーン金貨」
3. **インゴット系商品**（bar, ingot, バー, インゴット）:
   - メーカー名（PAMP / Heraeus / Valcambi / その他インゴットメーカー）+「インゴット・バー」を選ぶ
4. **コレクション系**（プルーフ・特殊仕上・キャラクター物・限定・記念）:
   - 該当するコレクション系グループを追加（「プルーフ・特殊仕上げコイン」「映画・音楽・キャラクター・コラボ系コイン」「アート・特殊デザインコイン」「コレクション・特集」など）
5. **鑑定済み**（NGC, PCGS, MS70, PF69 等）: 「鑑定済みコイン」を追加
6. **干支シリーズ**（Lunar, Year of the Dragon/Tiger 等）:
   - オーストラリアの干支 → 「オーストラリア・ルナーシリーズ(干支) 金貨/銀貨/プラチナコイン」
   - それ以外の国の干支 → 「その他諸国の干支シリーズ」
7. **特定シリーズグループに該当しない外国コイン**: 「世界各国のコイン」を追加
8. **クイーンズビースト/チューダービースト系**: 「イギリス王室紋章シリーズ(クイーンズビースト・チューダービースト)」
9. **オリンポスの神々シリーズ**: 「オリンポスの神々」
10. **ノアの箱舟シリーズ**: 「ノアの箱舟」
11. **アメリカ250周年関連**: 「アメリカ250周年記念」

## 出力形式
JSONのみ出力。説明や前置きは一切不要。グループ名はリスト内の名前と完全一致させること（漢字・カタカナ・スペース・括弧含む）。

{{"groups": ["グループ名1", "グループ名2", ...]}}
"""

        try:
            response = self.genai_model.generate_content(prompt)
            response_text = response.text
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if not json_match:
                logger.warning(f"detect_v2: JSON取得失敗 → グループなし: {response_text[:200]}")
                return (category_id, 0, [])

            data = json.loads(json_match.group())
            selected_names = data.get("groups", []) or []

            group_ids = []
            unknown = []
            for name in selected_names:
                if name in self.existing_groups:
                    gid = self.existing_groups[name]["id"]
                    if gid not in group_ids:
                        group_ids.append(gid)
                else:
                    unknown.append(name)

            if unknown:
                logger.warning(f"detect_v2: 未知のグループ名（スキップ）: {unknown}")

            name_pairs = [(g, self._gid_to_name(g)) for g in group_ids]
            logger.info(f"  detect_v2判定: カテゴリ={category_id}, グループ={name_pairs}")
            return (category_id, 0, group_ids)

        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"detect_v2 例外: {e}")
            return (category_id, 0, [])

    def _gid_to_name(self, gid: int) -> str:
        for name, info in self.existing_groups.items():
            if info["id"] == gid:
                return name
        return "?"

    def _detect_with_ai(self, product_name: str, url: str = "") -> dict:
        """
        AIを使って商品のカテゴリー情報を判定する（常時実行）

        Returns:
            dict: {"metal": "gold"|"silver"|"platinum"|"palladium"|"copper",
                   "shape": "coin"|"bar",
                   "type": "bullion"|"collection"|"graded",
                   "country": str, "series": str, "theme": str|None}

        Raises:
            RuntimeError: AI APIが利用できない場合、またはAI判定に失敗した場合
        """
        # AI APIがない場合はエラー
        if not self.genai_model:
            raise RuntimeError(
                "AI APIが利用できません。Vertex AI認証を確認してください。"
            )

        try:
            # 利用可能な国名リスト（グループマスターに登録されているもの）
            available_countries = [
                "usa", "canada", "austria", "uk", "australia", "china",
                "south_africa", "mexico", "st_helena", "singapore", "palau",
                "barbados", "cameroon", "chad", "armenia", "tuvalu", "niue",
                "cook_islands", "fiji", "solomon_islands", "tokelau", "france",
                "samoa", "germany", "other"
            ]

            # 利用可能なシリーズリスト
            available_series = [
                "eagle", "buffalo", "maple", "vienna", "britannia",
                "kangaroo", "koala", "kookaburra", "lunar", "panda",
                "krugerrand", "elephant", "libertad", "other"
            ]

            prompt = f"""あなたは貴金属コイン・地金の専門家です。以下の商品情報を分析し、カテゴリ情報をJSON形式で出力してください。

## 商品情報
- 商品名: {product_name}
- URL: {url}

## 判定ルール

### 1. 素材 (metal)
URLのパスに含まれる素材名を最優先で判定:
- /gold/ → "gold"
- /silver/ → "silver"
- /platinum/ → "platinum"
- /palladium/ → "palladium"
- /copper/ → "copper"

URLで判定できない場合は商品名から判定。デフォルトは "silver"。

### 2. 形状 (shape)
- "bar": bar, ingot, バー, インゴット, 地金 が含まれる場合
- "coin": それ以外（デフォルト）

### 3. タイプ (type)
- "graded": NGC, PCGS, PF70, PF69, MS70, MS69, PR70, 鑑定 などが含まれる場合
- "collection": proof, colorized, gilded, antiqued, high relief, piedfort, privy, 限定, 記念, または映画/アニメ/音楽関連キャラクター名が含まれる場合
- "bullion": 上記以外の地金型コイン（デフォルト）

### 4. 国名 (country)
発行国を判定。以下のリストから選択: {', '.join(available_countries)}

国名判定のヒント:
- American Eagle, Buffalo → usa
- Maple Leaf → canada
- Britannia, Royal Mint → uk
- Kangaroo, Koala, Kookaburra, Perth Mint → australia
- Philharmonic, Vienna → austria
- Panda → china
- Krugerrand → south_africa
- Libertad → mexico

### 5. シリーズ (series)
地金型コインのシリーズ名。以下のリストから選択: {', '.join(available_series)}

シリーズ判定:
- eagle: American Eagle
- buffalo: American Buffalo
- maple: Maple Leaf
- vienna: Philharmonic, ウィーン
- britannia: Britannia
- kangaroo: Kangaroo
- koala: Koala
- kookaburra: Kookaburra
- lunar: Lunar, 干支, Year of the Dragon/Tiger/etc.
- panda: Panda
- krugerrand: Krugerrand
- elephant: Elephant, Big Five
- libertad: Libertad
- other: 上記以外

### 6. テーマ (theme)
コレクションの場合のみ判定:
- "movie": Star Wars, Marvel, Disney, Batman, Superman, Harry Potter, Lord of the Rings など
- "anime": Pokemon, Pikachu, Hello Kitty, Dragon Ball, Gundam など
- "music": Beatles, Elvis, Queen, Rolling Stones など
- null: テーマなし

## 出力形式
JSONのみを出力してください。説明は不要です。

{{"metal": "...", "shape": "...", "type": "...", "country": "...", "series": "...", "theme": ...}}
"""

            response = self.genai_model.generate_content(prompt)
            response_text = response.text
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                data = json.loads(json_match.group())
                logger.info(f"  AI判定結果: {data}")
                return data

            # JSONが取得できなかった場合
            raise RuntimeError(
                f"AI判定結果からJSONを取得できませんでした。レスポンス: {response_text[:200]}"
            )

        except RuntimeError:
            # RuntimeErrorはそのまま再送出
            raise
        except Exception as e:
            # その他のエラーはRuntimeErrorにラップ
            raise RuntimeError(f"AI判定でエラーが発生しました: {e}") from e


class JapaneseProductNameGenerator:
    """英語の商品名から日本語の商品名を生成するクラス"""

    # 国名マッピング
    COUNTRY_MAP = {
        "singapore": "シンガポール",
        "usa": "アメリカ",
        "us": "アメリカ",
        "united states": "アメリカ",
        "america": "アメリカ",
        "american": "アメリカ",
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
        "solomon islands": "ソロモン諸島",
        "solomon": "ソロモン諸島",
        "si ": "ソロモン諸島",  # 略称 "SI" + 空白付きで誤マッチを避ける
        "niue": "ニウエ",
        "tuvalu": "ツバル",
        "fiji": "フィジー",
        "samoa": "サモア",
        "tokelau": "トケラウ",
        "barbados": "バルバドス",
        "cook islands": "クック諸島",
        "rwanda": "ルワンダ",
        "ghana": "ガーナ",
        "djibouti": "ジブチ",
        "armenia": "アルメニア",
        "andorra": "アンドラ",
        "isle of man": "マン島",
        "gibraltar": "ジブラルタル",
        "italy": "イタリア",
        "italian": "イタリア",
        "france": "フランス",
        "french": "フランス",
        "spain": "スペイン",
        "spanish": "スペイン",
        "portugal": "ポルトガル",
        "russia": "ロシア",
        "russian": "ロシア",
        "poland": "ポーランド",
        "czech": "チェコ",
        "hungary": "ハンガリー",
        "korea": "韓国",
        "japan": "日本",
        "japanese": "日本",
        "vietnam": "ベトナム",
        "thailand": "タイ",
        "india": "インド",
        "indian": "インド",
        "vatican city": "バチカン",
        "vatican": "バチカン",
    }

    # シリーズ名マッピング
    # ※ 複数単語のキー（例: "walking liberty"）は、1単語のキー（例: "liberty"）より先に記載すること
    SERIES_MAP = {
        # 複合語（優先マッチ）
        "incuse indian": "インディアン",
        "saint gaudens": "セント・ゴーデンス",
        "walking liberty": "ウォーキング・リバティ",
        "peace silver dollar": "ピースダラー",
        "peace dollar": "ピースダラー",
        "morgan silver dollar": "モルガン",
        "morgan dollar": "モルガン",
        "queen's beast": "クイーンズビースト",
        "queens beast": "クイーンズビースト",
        "tudor beast": "チューダービースト",
        "royal arms": "ロイヤルアームズ",
        "maple leaf": "メイプルリーフ",
        # ソブリン系（複合語を先にマッチ）
        "half sovereign": "ハーフソブリン",
        "quarter sovereign": "クォーターソブリン",
        "double sovereign": "ダブルソブリン",
        "sovereign": "ソブリン",
        # 教皇シリーズ
        "pope francis": "教皇フランシスコ",
        "pope benedict": "教皇ベネディクト",
        "pope john paul": "教皇ヨハネ・パウロ",
        # 単一語
        "dragon": "ドラゴン",
        "eagle": "イーグル",
        "britannia": "ブリタニア",
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
        "morgan": "モルガン",
    }

    # メーカー名マッピング
    # ※ 複数単語のキー（例: "argor heraeus"）は単一語より先に記載すること
    MAKER_MAP = {
        # 複合語（優先マッチ）
        "argor heraeus": "Argor Heraeus",
        "9fine mint": "9ファインミント",
        "perth mint": "パースミント",
        "royal mint": "ロイヤルミント",
        # 単一語
        "pamp": "PAMP",
        "heraeus": "Argor Heraeus",  # 単独 HERAEUS 表記でも Argor Heraeus に統一（同一社）
        "valcambi": "Valcambi",
        "nadir": "ナディール",
        "bullionstar": "ブリオンスター",
    }

    # スイス製造のメーカー（国名抽出フォールバック用）
    SWISS_MAKERS = ["pamp", "valcambi", "argor heraeus", "argor", "heraeus", "credit suisse"]

    # 素材マッピング
    METAL_MAP = {
        "gold": ("金貨", "ゴールド"),
        "silver": ("銀貨", "シルバー"),
        "platinum": ("プラチナコイン", "プラチナ"),
        "palladium": ("パラジウムコイン", "パラジウム"),
        "copper": ("銅コイン", "カッパー"),
        "bronze": ("ブロンズコイン", "ブロンズ"),
    }

    def generate(self, product_info: dict, quantity: int = 1) -> str:
        """
        英語の商品情報から日本語の商品名を生成する

        命名規則（統一フォーマット）:
          [年号] [国] [シリーズ/メーカー] [重量] [製品種類] 新品未使用 【数量+単位】

        Args:
            product_info: スクレイピングで取得した商品情報
                - name: 英語商品名
                - specs: 仕様
                - description: 商品説明
                - country: 製造国（スクレイパーが直接取得した値。最優先）
                - url: 商品URL（国名ヒントに使用）
            quantity: 枚数（E列から取得）

        Returns:
            str: 日本語の商品名
        """
        name = product_info.get("name", "")
        specs = product_info.get("specs", "")
        description = product_info.get("description", "")
        scraped_country = product_info.get("country", "")
        url = product_info.get("url", "")

        name_lower = name.lower()
        full_text = f"{name} {specs} {description}".lower()

        # 年号を抽出（仕入れ元タイトルのみ対象、description由来の誤検出を防ぐ）
        # タイトルに年号がない場合は空欄。必要なら人力で確認・追加。
        year = self._extract_year(name_lower)

        # 素材を判定（重量抽出で銅の場合はAVDP表記を保持するため、先に判定）
        metal_type, is_ingot = self._detect_metal_and_type(full_text)

        # 重量を抽出（銅・ブロンズの場合はAVDP表記を保持）
        weight = self._extract_weight(full_text, keep_avdp=(metal_type in ("copper", "bronze")))

        # 国名を抽出（優先順位付き）
        country = self._extract_country_prioritized(
            scraped_country=scraped_country,
            url=url,
            name_text=name_lower,
            full_text=full_text,
        )

        # シリーズ名またはメーカー名を抽出
        series_or_maker = self._extract_series_or_maker(full_text, is_ingot)

        # 枚数の単位
        unit = "本" if is_ingot else "枚"

        # 製品種類
        if is_ingot:
            if "gold" in metal_type:
                product_type = "ゴールドインゴット"
            elif "platinum" in metal_type:
                product_type = "プラチナインゴット"
            elif "palladium" in metal_type:
                product_type = "パラジウムインゴット"
            elif "copper" in metal_type or "bronze" in metal_type:
                product_type = "地金型銅メダル ブロンズ"
            else:
                product_type = "シルバーインゴット"
        else:
            # 銅/ブロンズは専用の表記（SEO・ショップ内検索対策）
            if metal_type in ("copper", "bronze"):
                product_type = "地金型銅メダル ブロンズ"
            else:
                coin_type = self.METAL_MAP.get(metal_type, ("地金型銀貨", "シルバー"))[0]
                product_type = f"地金型{coin_type}"  # 「地金型金貨」「地金型銀貨」等

        # 鑑定済み（MS70/PF69/PCGS/NGC等）の場合は「新品未使用」の代わりに鑑定ラベルを使う
        grade_label = self._extract_grade_label(name)

        # 統一フォーマットで組み立て
        # [年号] [国] [シリーズ/メーカー] [重量] [製品種類] {鑑定ラベル or 新品未使用} 【数量+単位】
        parts = []
        if year:
            parts.append(year)
        if country:
            parts.append(country)
        if series_or_maker:
            parts.append(series_or_maker)
        if weight:
            parts.append(weight)
        parts.append(product_type)
        if grade_label:
            parts.append(grade_label)  # 例: "MS-70 PCGS"、"PF-69 NGC"
        else:
            parts.append("新品未使用")
        parts.append(f"【{quantity}{unit}】")

        return " ".join(parts)

    def _extract_grade_label(self, text: str) -> str:
        """
        鑑定グレードラベルを抽出して「MS-70 PCGS」のような形に整形する

        対応パターン:
        - MS70, MS-70, MS 70 など
        - PF70, PF-70, PR70 など
        - 鑑定機関: PCGS, NGC, CAC

        Returns:
            空文字 = 鑑定情報なし（=新品未使用扱い）
        """
        import re

        # グレード抽出（MS/PF/PR/SP/BU は除外: BU=Brilliant Uncirculatedは鑑定ではない）
        grade_match = re.search(r'\b(MS|PF|PR|SP)[\s\-]?(70|69|68|67|66|65)\b', text, re.IGNORECASE)
        # 鑑定機関抽出
        grader_match = re.search(r'\b(PCGS|NGC|CAC)\b', text, re.IGNORECASE)

        parts = []
        if grade_match:
            prefix = grade_match.group(1).upper()
            suffix = grade_match.group(2)
            parts.append(f"{prefix}-{suffix}")
        if grader_match:
            parts.append(grader_match.group(1).upper())

        return " ".join(parts)

    def _extract_year(self, text: str) -> str:
        """年号を抽出（1800年代〜2099年）

        ヒストリカルコイン（モルガン1878、ピースダラー1921等）にも対応。
        通常は仕入れ元タイトルのみから抽出することを推奨（descriptionからの誤検出を防ぐため）。
        """
        import re
        # 1800-1899, 1900-1999, 2000-2099 の範囲で年号を検索
        match = re.search(r'\b(1[89]\d{2}|20\d{2})\b', text)
        if match:
            return match.group(1)
        return ""

    def _extract_weight(self, text: str, keep_avdp: bool = False) -> str:
        """重量を抽出して日本語形式に変換

        Args:
            text: 解析対象テキスト
            keep_avdp: Trueの場合、AVDP表記を保持（銅/ブロンズ商品用）
                       通常は「1オンス」、keep_avdp=Trueかつ元文に AVDP があれば「1AVDPオンス」
        """
        import re

        # 元文に AVDP が含まれているか
        has_avdp = bool(re.search(r'\bavdp\b', text, re.IGNORECASE))
        oz_unit = "AVDPオンス" if (has_avdp and keep_avdp) else "オンス"

        # AVDP oz / Troy oz などの修飾語があっても正規表現でマッチするように
        oz_suffix = r'(?:avdp\s+|troy\s+)?(?:oz|ounce)'

        # 1. 分数表記を先にチェック（1/4 oz, 1/2 oz 等）
        frac_match = re.search(rf'(\d+)\s*/\s*(\d+)\s*{oz_suffix}', text)
        if frac_match:
            numerator = int(frac_match.group(1))
            denominator = int(frac_match.group(2))
            if denominator > 0:
                from math import gcd
                g = gcd(numerator, denominator)
                n, d = numerator // g, denominator // g
                if d == 1:
                    return f"{n}{oz_unit}"
                return f"{n}/{d}{oz_unit}"

        # 2. 整数/小数のオンス表記
        oz_match = re.search(rf'(\d+(?:\.\d+)?)\s*{oz_suffix}', text)
        if oz_match:
            oz_val = float(oz_match.group(1))
            if oz_val == int(oz_val):
                return f"{int(oz_val)}{oz_unit}"
            return f"{oz_val}{oz_unit}"

        # 3. kg表記
        kg_match = re.search(r'(\d+(?:\.\d+)?)\s*kg', text)
        if kg_match:
            return f"{kg_match.group(1)}kg"

        # 4. グラム表記
        g_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:g|gram)(?:s)?(?!\w)', text)
        if g_match:
            return f"{g_match.group(1)}g"

        return ""

    def _detect_metal_and_type(self, text: str) -> tuple[str, bool]:
        """素材とインゴットかどうかを判定"""
        import re
        is_ingot = any(kw in text for kw in ["ingot", "bar", "インゴット", "バー"])

        # より具体的な素材から優先してチェック（gold/silver が description に含まれやすいため）
        if "platinum" in text or "プラチナ" in text:
            return "platinum", is_ingot
        elif "palladium" in text or "パラジウム" in text:
            return "palladium", is_ingot
        elif "copper" in text or "銅" in text:
            return "copper", is_ingot
        elif "bronze" in text or "ブロンズ" in text:
            return "bronze", is_ingot
        elif "gold" in text or "金" in text:
            return "gold", is_ingot
        elif "silver" in text or "銀" in text:
            return "silver", is_ingot

        # 化学記号（Au=金、Ag=銀、Pt=プラチナ、Pd=パラジウム、Cu=銅）
        # 単語境界チェックで "author", "again" 等の誤マッチを回避
        if re.search(r"\bpt\b", text):
            return "platinum", is_ingot
        elif re.search(r"\bpd\b", text):
            return "palladium", is_ingot
        elif re.search(r"\bcu\b", text):
            return "copper", is_ingot
        elif re.search(r"\bau\b", text):
            return "gold", is_ingot
        elif re.search(r"\bag\b", text):
            return "silver", is_ingot

        return "silver", is_ingot  # デフォルト

    # デザイン表現（国名の直後にこれらが来る場合は国名判定しない）
    _DESIGN_WORDS = r"(?:dragon|design|style|panda|motif|theme|symbol|pattern|art|edition)"

    def _country_word_search(self, text: str) -> str:
        """
        単語境界を考慮して国名辞書からマッチを返す。
        「Chinese dragon」等のデザイン表現は国名としてカウントしない。
        """
        import re
        for eng, jpn in self.COUNTRY_MAP.items():
            # 単語境界マッチ（"us"が"australia"に含まれる誤マッチを防ぐ）
            # ハイフンやスラッシュもword boundaryとして扱うため、\b を使う
            word_pattern = re.compile(rf"\b{re.escape(eng)}\b", re.IGNORECASE)
            match = word_pattern.search(text)
            if not match:
                continue

            # 除外チェック: 国名の直後がデザイン表現ならスキップ
            exclude_pattern = re.compile(
                rf"\b{re.escape(eng)}\s+{self._DESIGN_WORDS}\b", re.IGNORECASE
            )
            if exclude_pattern.search(text):
                # 他の国名がテキスト内にあれば、それを優先して探す
                other_hits_exist = any(
                    re.search(rf"\b{re.escape(other)}\b", text, re.IGNORECASE)
                    and not re.search(
                        rf"\b{re.escape(other)}\s+{self._DESIGN_WORDS}\b",
                        text, re.IGNORECASE,
                    )
                    for other in self.COUNTRY_MAP
                    if other != eng
                )
                if other_hits_exist:
                    continue
            return jpn
        return ""

    def _extract_country(self, text: str) -> str:
        """国名を抽出（単語境界マッチ、除外ワード考慮）"""
        return self._country_word_search(text)

    def _extract_country_prioritized(
        self,
        scraped_country: str = "",
        url: str = "",
        name_text: str = "",
        full_text: str = "",
    ) -> str:
        """
        国名を優先順位付きで抽出

        優先度:
          1. scraped_country（スクレイパーが直接取得した製造国）
          2. URL内のヒント
          3. 商品名のみ
          4. 全文（仕様・説明含む、デザイン除外付き）
        """
        # 1. スクレイピング済みの製造国を最優先
        if scraped_country:
            sc_lower = scraped_country.strip().lower()
            # 完全一致を最優先
            for eng, jpn in self.COUNTRY_MAP.items():
                if eng == sc_lower:
                    return jpn
            # 次に単語境界マッチ
            found = self._country_word_search(sc_lower)
            if found:
                return found
            # 辞書にない国名はそのまま返す（スクレイパーを信頼）
            return scraped_country

        # 2. URLから抽出
        if url:
            found = self._country_word_search(url.lower())
            if found:
                return found

        # 3. 商品名のみから抽出
        if name_text:
            found = self._country_word_search(name_text)
            if found:
                return found

        # 4. 全文から抽出（デザイン除外付き）
        if full_text:
            found = self._country_word_search(full_text)
            if found:
                return found

        # 5. フォールバック: スイスメーカー名から国名を推定
        # （PAMP/Valcambi/Heraeus/Argor 等は国名表記がなくてもスイスが正解）
        for src in [name_text, full_text]:
            src_lower = (src or "").lower()
            for maker in self.SWISS_MAKERS:
                if maker in src_lower:
                    return "スイス"

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
    - コイン: ITM-{年号}-{国コード}-{カテゴリ}-{シリーズ}-{仕入れ先コード}-{枚数}
      例: ITM-2026-GBR-SCJ-BRT-01-100
    - インゴット: ITM-{国コード}-{カテゴリ}-{メーカー}-{仕入れ先コード}-{数量}-{重量}
      例: ITM-CH-GIJ-PAM-01-001-100G

    仕入れ先コード:
    - 01: Bullionstar
    - 02: APMEX
    - 03: JM Bullion
    - 04: SD Bullion
    - 05: Money Metals
    - 99: その他
    """

    # 仕入れ先コードマッピング（他社にはわからない形式）
    SUPPLIER_CODES = {
        "bullionstar": "01",
        "apmex": "02",
        "jm bullion": "03",
        "jmbullion": "03",
        "sd bullion": "04",
        "sdbullion": "04",
        "money metals": "05",
        "moneymetals": "05",
    }
    DEFAULT_SUPPLIER_CODE = "99"

    # 型番生成用プロンプト
    PROMPT_TEMPLATE = """あなたは貴金属商品の型番を生成するエキスパートです。

以下の商品情報から、指定された命名規則に従って型番を生成してください。

## 商品情報
- 商品名: {product_name}
- 仕様: {specs}
- 説明: {description}
- 数量: {quantity}
- 仕入れ先コード: {supplier_code}

## 命名規則

### コイン（年号付き）の場合:
ITM-{{年号}}-{{国コード}}-{{カテゴリ}}-{{シリーズ}}-{{仕入れ先コード}}-{{枚数3桁}}
※仕入れ先コードは商品情報で指定された値をそのまま使用

### インゴット/バーの場合:
ITM-{{国コード}}-{{カテゴリ}}-{{メーカー}}-{{仕入れ先コード}}-{{数量3桁}}-{{重量}}
※仕入れ先コードは商品情報で指定された値をそのまま使用

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
- "2026 1 oz Silver Britannia" (5枚, 仕入れ先コード=01) → ITM-2026-GBR-SCJ-BRT-01-005
- "PAMP Suisse 100g Gold Bar" (1本, 仕入れ先コード=01) → ITM-CH-GIJ-PAM-01-001-100G
- "2024 1 oz Silver Dragon Round USA" (5枚, 仕入れ先コード=02) → ITM-2024-USA-SCJ-DRG-02-005

## 出力形式
型番のみを1行で出力してください。説明は不要です。
仕入れ先コードは必ず指定された値（{supplier_code}）を使用してください。
"""

    def __init__(self, existing_model_numbers: set = None):
        """
        型番生成器を初期化

        Args:
            existing_model_numbers: 既存の型番セット（重複チェック用）
        """
        self.genai_model = None
        self.existing_model_numbers = existing_model_numbers or set()
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project="coin-price-tracker-479614", location="us-central1")
            self.genai_model = GenerativeModel("gemini-2.5-pro")
            logger.info("型番生成器: Vertex AI Gemini初期化完了")
        except Exception as e:
            logger.warning(f"型番生成器: Vertex AI初期化エラー: {e}")

    def set_existing_model_numbers(self, existing_model_numbers: set):
        """既存の型番セットを設定（後から追加する場合用）"""
        self.existing_model_numbers = existing_model_numbers

    def add_existing_model_number(self, model_number: str):
        """既存の型番を追加（生成後に呼び出す）"""
        if model_number:
            self.existing_model_numbers.add(model_number)

    def get_supplier_code(self, supplier_site: str) -> str:
        """
        仕入れ先サイト名から仕入れ先コードを取得

        Args:
            supplier_site: 仕入れ先サイト名（例: "Bullionstar", "APMEX"）

        Returns:
            str: 仕入れ先コード（例: "01", "02"）
        """
        if not supplier_site:
            return self.DEFAULT_SUPPLIER_CODE

        supplier_lower = supplier_site.lower().strip()
        return self.SUPPLIER_CODES.get(supplier_lower, self.DEFAULT_SUPPLIER_CODE)

    def _make_unique(self, model_number: str) -> str:
        """
        重複チェックを行い、必要ならサフィックスを追加

        Args:
            model_number: 生成された型番

        Returns:
            str: ユニークな型番
        """
        if not model_number:
            return model_number

        if model_number not in self.existing_model_numbers:
            return model_number

        # 重複している場合、サフィックスを追加
        suffixes = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for suffix in suffixes:
            unique_number = f"{model_number}-{suffix}"
            if unique_number not in self.existing_model_numbers:
                logger.info(f"  型番重複検出: {model_number} → {unique_number}")
                return unique_number

        # 26個超えた場合は連番
        for i in range(2, 100):
            unique_number = f"{model_number}-{i:02d}"
            if unique_number not in self.existing_model_numbers:
                logger.info(f"  型番重複検出: {model_number} → {unique_number}")
                return unique_number

        logger.warning(f"型番のユニーク化に失敗: {model_number}")
        return model_number

    def generate(self, product_info: dict, quantity: int = 1, supplier_site: str = "") -> str:
        """
        商品情報から型番をAIで生成する

        Args:
            product_info: スクレイピングで取得した商品情報
            quantity: 枚数/本数
            supplier_site: 仕入れ先サイト名（例: "Bullionstar"）

        Returns:
            str: 型番 (例: ITM-2026-GBR-SCJ-BRT-01-100)
        """
        if not self.genai_model:
            logger.warning("Vertex AI Geminiが初期化されていません。型番生成をスキップします。")
            return ""

        # 仕入れ先コードを取得
        supplier_code = self.get_supplier_code(supplier_site)

        prompt = self.PROMPT_TEMPLATE.format(
            product_name=product_info.get("name", ""),
            specs=product_info.get("specs", ""),
            description=product_info.get("description", "")[:500],  # 長すぎる場合は切り詰め
            quantity=quantity,
            supplier_code=supplier_code
        )

        import time
        import signal

        def timeout_handler(signum, frame):
            raise TimeoutError("Vertex AI API call timed out")

        max_retries = 3
        timeout_seconds = 120

        for attempt in range(max_retries):
            try:
                # タイムアウト設定（Unix系のみ）
                old_handler = signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(timeout_seconds)

                try:
                    # APIを呼び出し
                    response = self.genai_model.generate_content(prompt)
                finally:
                    # タイムアウトを解除
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old_handler)

                model_number = response.text.strip()
                # 型番形式の検証（ITM-で始まる）
                if model_number.startswith("ITM-"):
                    # 重複チェックとユニーク化
                    unique_model_number = self._make_unique(model_number)
                    # 生成した型番を既存リストに追加
                    self.add_existing_model_number(unique_model_number)
                    return unique_model_number
                else:
                    logger.warning(f"不正な型番形式: {model_number}")
                    return ""

            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 10 * (attempt + 1)  # 10秒, 20秒と増加
                    logger.warning(f"型番生成エラー（リトライ {attempt + 1}/{max_retries}）: {e}")
                    logger.info(f"  {wait_time}秒待機後にリトライ...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"型番生成エラー（最終）: {e}")
                    return ""
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
    if image_analyzer.genai_model:
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
            if generator.genai_model:
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

                # 先にM/N/P/Q列を更新してT列の計算式を更新させる
                try:
                    sheet = sheet_client._spreadsheet.worksheet(Config.SHEET_COLORME)
                    price_updates = []
                    # M列: 取得元価格
                    price_updates.append({
                        'range': f'M{row_num}',
                        'values': [[str(product_info.get("price", ""))]]
                    })
                    # N列: 取得通貨
                    if not existing_currency:
                        price_updates.append({
                            'range': f'N{row_num}',
                            'values': [[product_info.get("currency", "USD")]]
                        })
                    # P列: 為替レート
                    if exchange_rate is not None:
                        price_updates.append({
                            'range': f'P{row_num}',
                            'values': [[str(round(exchange_rate, 4))]]
                        })
                    # Q列: 外部-本体計算価格（T列の計算式で使用）
                    if calculated_price is not None:
                        price_updates.append({
                            'range': f'Q{row_num}',
                            'values': [[str(int(calculated_price))]]
                        })
                    sheet.batch_update(price_updates, value_input_option='RAW')
                    logger.info("  M/N/P/Q列を先行更新（T列計算式用）")

                    # スプレッドシートの再計算を待機
                    import time
                    time.sleep(2)

                    # T列を再読み込み
                    updated_row = sheet.row_values(row_num)
                    if len(updated_row) > 19 and updated_row[19].strip():
                        try:
                            new_selling_price = int(float(updated_row[19].strip().replace(',', '')))
                            logger.info(f"  T列再読み込み: {new_selling_price}円")
                            row_info["selling_price"] = new_selling_price
                        except ValueError:
                            logger.warning(f"  T列の値が数値ではありません: {updated_row[19]}")
                except Exception as e:
                    logger.warning(f"  価格先行更新エラー: {e}")

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
