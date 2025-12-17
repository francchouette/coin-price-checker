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

from .colorme import ColorMeClient
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

            # 商品名
            try:
                name_elem = page.query_selector("h1.product-name, h1[itemprop='name'], h1")
                if name_elem:
                    result["name"] = name_elem.inner_text().strip()
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
                # BullionStarはstatic.bullionstar.comから画像を配信、classなしのimg要素
                img_elems = page.query_selector_all("img[src*='static.bullionstar.com'], img[src*='bullionstar.com/files']")
                for img in img_elems[:10]:
                    src = img.get_attribute("src")
                    if src and "static.bullionstar.com" in src:
                        # サムネイル（73_73_）ではなく大きい画像（300_300_以上）を取得
                        if "73_73_" not in src and src not in result["image_urls"]:
                            result["image_urls"].append(src)
            except Exception:
                pass

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

    def __init__(self, categories: list[dict], groups: list[dict]):
        self.categories = categories
        self.groups = groups

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

        # キーワードマッチングでカテゴリーを判定
        for cat in self.categories:
            cat_name = (cat.get("name_big", "") + cat.get("name_small", "")).lower()

            # 金貨
            if any(kw in name_lower for kw in ["gold", "金貨", "ゴールド", "金"]):
                if "金" in cat_name or "gold" in cat_name:
                    category_id_big = cat.get("id_big", 0)
                    category_id_small = cat.get("id_small", 0)
                    break

            # 銀貨
            if any(kw in name_lower for kw in ["silver", "銀貨", "シルバー", "銀"]):
                if "銀" in cat_name or "silver" in cat_name:
                    category_id_big = cat.get("id_big", 0)
                    category_id_small = cat.get("id_small", 0)
                    break

            # プラチナ
            if any(kw in name_lower for kw in ["platinum", "プラチナ"]):
                if "プラチナ" in cat_name or "platinum" in cat_name:
                    category_id_big = cat.get("id_big", 0)
                    category_id_small = cat.get("id_small", 0)
                    break

            # インゴット
            if any(kw in name_lower for kw in ["ingot", "インゴット", "バー"]):
                if "インゴット" in cat_name or "バー" in cat_name:
                    category_id_big = cat.get("id_big", 0)
                    category_id_small = cat.get("id_small", 0)
                    break

        # グループ判定
        for grp in self.groups:
            grp_name = grp.get("name", "").lower()
            if grp_name and any(kw in name_lower for kw in grp_name.split()):
                group_ids.append(grp.get("id", 0))

        return category_id_big, category_id_small, group_ids


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
                # AC列が「ドラフト作成」「取得のみ」「更新」の場合のみ処理
                if source_url and not product_name:
                    if sync_mode not in ["ドラフト作成", "取得のみ", "更新"]:
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

                    incomplete_rows.append({
                        "row_num": i,
                        "source_url": source_url,
                        "quantity": quantity,
                        "margin_rate": margin_rate,
                        "shipping": shipping,
                        "sync_mode": sync_mode
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
    sync_mode: str = "ドラフト作成"
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
    """
    if not sheet_client._spreadsheet:
        return False

    try:
        sheet = sheet_client._spreadsheet.worksheet(Config.SHEET_COLORME)

        cat_big, cat_small, grp_ids = category_info

        updates = []

        # B列: 商品名
        updates.append({
            'range': f'B{row_num}',
            'values': [[product_info.get("name", "")]]
        })

        # M列: 取得元価格
        updates.append({
            'range': f'M{row_num}',
            'values': [[str(product_info.get("price", ""))]]
        })

        # N列: 取得通貨
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

    # 7. スクレイパーを初期化して各行を処理
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

            logger.info(f"  商品名: {product_info['name']}")
            logger.info(f"  価格: {product_info['price']} {product_info['currency']}")
            logger.info(f"  在庫: {'あり' if product_info['in_stock'] else 'なし'}")
            logger.info(f"  画像: {len(product_info['image_urls'])}枚")

            # カテゴリー判定
            cat_big, cat_small, grp_ids = detector.detect(product_info["name"])
            logger.info(f"  カテゴリー: 大={cat_big}, 小={cat_small}")

            # 為替レートと計算価格を取得
            exchange_rate = None
            calculated_price = None
            currency = product_info.get("currency", "USD")
            price = product_info.get("price", 0)
            if price > 0:
                exchange_rate = exchange_client.get_rate(currency, "JPY")
                if exchange_rate:
                    calculated_price = price * exchange_rate
                    logger.info(f"  為替レート: 1 {currency} = {exchange_rate:.4f} JPY")
                    logger.info(f"  計算価格: {int(calculated_price)} 円")

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
                logger.info(f"  更新予定: B列={product_info['name'][:30]}...")
                logger.info(f"  更新予定: M列={product_info['price']}, N列={product_info['currency']}")
                logger.info(f"  更新予定: 画像={len(product_info['image_urls'])}枚")
                if sync_mode == "更新":
                    logger.info("  → カラーミーへの登録が実行されます")
                elif sync_mode == "ドラフト作成":
                    logger.info("  → スプレッドシートのみ更新（ドラフト作成）")
                else:
                    logger.info("  → スプレッドシートのみ更新（取得のみ）")
            else:
                # sync_mode が「更新」の場合のみカラーミーへ登録
                if sync_mode == "更新" and colorme_client:
                    logger.info("  カラーミーへ商品登録中...")
                    # TODO: カラーミーへの商品登録・画像アップロード処理
                    # colorme_image_urls = colorme_client.upload_images(product_info["image_urls"])
                    # colorme_client.register_product(...)
                    logger.info("  → カラーミー登録機能は今後実装予定")
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
                    sync_mode
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
