"""
Bullionstar スクレイパー
"""

import re
import json
import logging
from typing import Optional

from .base import BaseScraper, ScrapedData

logger = logging.getLogger(__name__)


class BullionstarScraper(BaseScraper):
    """Bullionstar用スクレイパー"""

    SHOP_NAME = "Bullionstar"
    CURRENCY = "JPY"  # デフォルトをJPYに変更
    WAIT_TIME_MS = 5000  # Bot検出対策のため長めに設定

    # セレクタ
    NAME_SELECTOR = "h1"
    PRICE_TABLE_SELECTOR = ".info tr"
    STOCK_SELECTOR = ".product-default-wrap.product-price-update"

    # ロケーションセレクタ（製造国/発行国を取得）
    # 商品詳細のリスト項目から "Country: XXX" を探す
    PRODUCT_DETAILS_SELECTOR = ".product-overview li, .product-details li, .info li"

    # 画像セレクタ（Bullionstar固有の構造）
    # メイン画像: .photo セクション内の大きい画像
    MAIN_IMAGE_SELECTOR = ".photo > img, section.photo > img, .product-image img"
    # サムネイル: .photo ul li 内の画像
    THUMBNAIL_SELECTOR = ".photo ul li img, .photo li img, .product-thumbnails img"

    # 商品説明セレクタ
    DESCRIPTION_SELECTOR = ".product-description, .description, .product-info .expl"

    # 仕様セレクタ（商品詳細リスト）
    SPECS_SELECTOR = ".product-overview li, .product-details li, .info li, .specifications li"

    def __init__(self, page):
        super().__init__(page)
        self._detected_currency = None  # 検出された通貨（インスタンス変数）
        self._currency_set = False  # 通貨設定済みフラグ
        self._is_out_of_stock = False  # 在庫切れフラグ
        self._detected_location = None  # 検出されたロケーション
        # 追加フィールド（38列対応）
        self._main_image_url = ""
        self._thumbnail_url = ""
        self._image_urls: list[str] = []  # 画像URL1-8
        self._specs = ""
        self._description_en = ""
        self._mint_year = ""
        self._mintage = ""

    def _set_currency_cookie(self):
        """JPY表示用のCookieを設定する（全ドメイン対応）"""
        if self._currency_set:
            return

        try:
            # Bullionstarの全ドメインに通貨設定Cookieを追加
            context = self.page.context
            domains = [".bullionstar.com", ".bullionstar.us", ".bullionstar.co.nz"]
            cookies = [
                {
                    "name": "currency",
                    "value": "JPY",
                    "domain": domain,
                    "path": "/"
                }
                for domain in domains
            ]
            context.add_cookies(cookies)
            self._currency_set = True
            logger.info("Bullionstar: JPY通貨Cookieを設定しました（全ドメイン）")
        except Exception as e:
            logger.warning(f"通貨Cookie設定エラー: {e}")

    def scrape(self, url: str) -> ScrapedData:
        """
        商品ページをスクレイピングする（JPY通貨Cookie設定付き）
        画像、仕様、商品説明、発行年、発行数も取得する
        """
        # ページアクセス前にJPY通貨Cookieを設定
        self._set_currency_cookie()
        # 親クラスのscrapeを呼び出す
        result = super().scrape(url)

        # 追加情報を抽出（エラーでも試行）
        try:
            self._extract_images()
            self._extract_specs_and_details()
            self._extract_description()
        except Exception as e:
            logger.warning(f"追加情報抽出エラー: {e}")

        return result

    def _extract_price(self) -> Optional[float]:
        """
        価格を抽出する

        Bullionstarは数量別価格テーブルで表示される
        最初の価格行（1-9個の価格）を取得
        複数の通貨（JPY, USD, SGD等）に対応

        在庫切れ商品の場合はJSON-LD（構造化データ）から価格を取得する
        """
        try:
            # まず価格テーブルから取得を試みる
            price = self._extract_price_from_table()
            if price is not None:
                return price

            # 価格テーブルで見つからない場合、JSON-LDから取得を試みる
            logger.warning("価格テーブルから価格を取得できませんでした。JSON-LDから取得を試みます...")
            price = self._extract_price_from_json_ld()
            if price is not None:
                self._is_out_of_stock = True  # JSON-LDから取得 = 在庫切れの可能性が高い
                return price

            return None

        except Exception as e:
            logger.error(f"価格抽出エラー (Bullionstar): {e}")
            return None

    def _extract_price_from_table(self) -> Optional[float]:
        """価格テーブルから価格を抽出する"""
        try:
            rows = self.page.query_selector_all(self.PRICE_TABLE_SELECTOR)

            # 複数通貨パターン: US$, S$, ¥, €, £
            # US$を先に試す（$だけだとS$の一部とマッチする可能性があるため）
            patterns = [
                (r'US\$([\d,]+\.?\d*)', 'USD'),    # USD (US$表記)
                (r'¥([\d,]+\.?\d*)', 'JPY'),       # JPY
                (r'S\$([\d,]+\.?\d*)', 'SGD'),     # SGD
                (r'€([\d,]+\.?\d*)', 'EUR'),       # EUR
                (r'£([\d,]+\.?\d*)', 'GBP'),       # GBP
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
                            self._detected_currency = currency
                            logger.info(f"価格検出: {currency} {price} ({text[:50]}...)")
                            return price

            return None

        except Exception as e:
            logger.error(f"価格テーブル抽出エラー: {e}")
            return None

    def _extract_price_from_json_ld(self) -> Optional[float]:
        """JSON-LD（構造化データ）から価格を抽出する"""
        try:
            # JSON-LDスクリプトを取得
            scripts = self.page.query_selector_all('script[type="application/ld+json"]')

            for script in scripts:
                try:
                    content = script.inner_text()
                    data = json.loads(content)

                    # JSON-LDが配列形式の場合（[{...}, {...}]）
                    items = data if isinstance(data, list) else [data]

                    for item in items:
                        # 辞書でない場合はスキップ
                        if not isinstance(item, dict):
                            continue

                        # Productスキーマを探す
                        if item.get("@type") == "Product":
                            offers = item.get("offers", {})

                            # offersが配列の場合
                            if isinstance(offers, list):
                                for offer in offers:
                                    price = self._parse_offer_price(offer)
                                    if price:
                                        return price
                            # offersがオブジェクトの場合
                            elif isinstance(offers, dict):
                                price = self._parse_offer_price(offers)
                                if price:
                                    return price

                except json.JSONDecodeError:
                    continue

            return None

        except Exception as e:
            logger.error(f"JSON-LD抽出エラー: {e}")
            return None

    def _parse_offer_price(self, offer: dict) -> Optional[float]:
        """オファーデータから価格を抽出する（全通貨対応）"""
        try:
            currency = offer.get("priceCurrency", "")
            price_str = offer.get("price", "")

            if not price_str:
                return None

            # サポートする通貨リスト
            supported_currencies = ["JPY", "USD", "SGD", "EUR", "GBP"]

            if currency in supported_currencies:
                price = float(str(price_str).replace(",", ""))
                if price > 0:
                    self._detected_currency = currency
                    logger.info(f"JSON-LD価格検出: {currency} {price}")
                    return price

            return None

        except (ValueError, TypeError) as e:
            logger.error(f"オファー価格パースエラー: {e}")
            return None

    def _check_stock(self) -> bool:
        """
        在庫状態を確認する

        _is_out_of_stockフラグが設定されている場合は在庫なしを返す
        （JSON-LDから価格を取得した場合 = 価格テーブルがない = 在庫切れ）

        それ以外はstatus属性で判定:
        - IN_STOCK: 在庫あり
        - IN_TRANSIT: 入荷予定（PRE-SALE）- 購入可能なので在庫ありとして扱う
        - UNAVAILABLE: 在庫なし
        """
        # JSON-LDから価格を取得した場合は在庫切れ
        if self._is_out_of_stock:
            logger.info("在庫状態: Out of Stock（JSON-LDから価格取得のため）")
            return False

        try:
            element = self.page.query_selector(self.STOCK_SELECTOR)
            if element:
                status = element.get_attribute("status")
                if status:
                    # IN_STOCK と IN_TRANSIT は在庫あり（購入可能）として扱う
                    is_in_stock = status.upper() in ("IN_STOCK", "IN_TRANSIT")
                    logger.info(f"在庫状態: {'In Stock' if is_in_stock else 'Out of Stock'} (status={status})")
                    return is_in_stock
            return True  # 判定できない場合は在庫ありとみなす
        except Exception as e:
            logger.error(f"在庫状態確認エラー: {e}")
            return True

    def _get_currency(self) -> str:
        """
        検出された通貨を返す

        価格抽出時に検出された通貨があればそれを返す
        なければデフォルト（USD）を返す
        """
        return self._detected_currency or self.CURRENCY

    def _extract_location(self) -> Optional[str]:
        """
        製造国/発行国（Country）を抽出する

        商品詳細リストから "Country: XXX" パターンを探す
        """
        try:
            # 商品詳細のリスト項目を取得
            items = self.page.query_selector_all(self.PRODUCT_DETAILS_SELECTOR)

            for item in items:
                text = item.inner_text().strip()
                # "Country: XXX" パターンをマッチ
                match = re.match(r'^Country:\s*(.+)$', text, re.IGNORECASE)
                if match:
                    country = match.group(1).strip()
                    if country:
                        self._detected_location = country
                        logger.info(f"製造国検出: {country}")
                        return country

            # 見つからない場合はページ全体からテキストを検索
            page_text = self.page.inner_text("body")
            # "Country: XXX" または "Country\nXXX" パターンに対応
            match = re.search(r'Country[:\s]+([A-Za-z\s/,]+?)(?:\n|$|\t|Weight|Purity|Diameter)', page_text)
            if match:
                country = match.group(1).strip()
                if country and len(country) < 50:  # 妥当な長さをチェック
                    self._detected_location = country
                    logger.info(f"製造国検出（ページ検索）: {country}")
                    return country

            # JSON-LDからも検索
            country = self._extract_country_from_json_ld()
            if country:
                self._detected_location = country
                logger.info(f"製造国検出（JSON-LD）: {country}")
                return country

            logger.debug("製造国情報が見つかりませんでした")
            return None

        except Exception as e:
            logger.error(f"製造国抽出エラー: {e}")
            return None

    def get_location(self) -> str:
        """
        検出されたロケーション（製造国/発行国）を返す

        scrape()呼び出し後に使用する
        """
        if self._detected_location is None:
            self._extract_location()
        return self._detected_location or ""

    def _extract_images(self) -> None:
        """
        商品画像URLを抽出する

        Bullionstarの画像構造:
        - .photo > img: メイン画像（300x300）
        - .photo ul li img: サムネイル画像（73x73）
        - data-src-x2: 2倍解像度画像
        - data-zoom-image: ズーム用高解像度画像
        - JSON-LD image: 600x600の画像URL配列
        """
        try:
            seen_urls = set()
            image_urls = []

            # メイン画像を取得（高解像度版を優先）
            main_img = self.page.query_selector(self.MAIN_IMAGE_SELECTOR)
            if main_img:
                # 高解像度版を優先して取得
                src = (
                    main_img.get_attribute("data-zoom-image") or
                    main_img.get_attribute("data-src-x2") or
                    main_img.get_attribute("src") or
                    main_img.get_attribute("data-src")
                )
                if src:
                    full_src = self._get_full_resolution_url(src)
                    self._main_image_url = full_src
                    seen_urls.add(full_src)
                    logger.debug(f"メイン画像: {full_src[:80]}...")

            # サムネイル画像を取得（高解像度版を優先）
            thumbnails = self.page.query_selector_all(self.THUMBNAIL_SELECTOR)

            for thumb in thumbnails:
                # 高解像度版を優先して取得
                src = (
                    thumb.get_attribute("data-zoom-image") or
                    thumb.get_attribute("data-src-x2") or
                    thumb.get_attribute("src") or
                    thumb.get_attribute("data-src")
                )
                if src:
                    full_src = self._get_full_resolution_url(src)
                    if full_src not in seen_urls:
                        seen_urls.add(full_src)
                        image_urls.append(full_src)

            # 最初のサムネイルをサムネイルURLとして設定
            if image_urls:
                self._thumbnail_url = image_urls[0]
                self._image_urls = image_urls[:8]  # 最大8枚
                logger.debug(f"サムネイルから画像: {len(self._image_urls)}枚")

            # JSON-LDからも画像を取得（高品質な600x600画像が含まれる）
            self._extract_images_from_json_ld(seen_urls)

            logger.info(f"画像取得完了: メイン={1 if self._main_image_url else 0}枚, 追加={len(self._image_urls)}枚")

        except Exception as e:
            logger.warning(f"画像抽出エラー: {e}")

    def _get_full_resolution_url(self, url: str) -> str:
        """
        サムネイルURLを最大解像度のURLに変換する
        例: /image_thumb.jpg -> /image.jpg
        """
        if not url:
            return url

        # 相対URLを絶対URLに変換
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://www.bullionstar.com" + url

        # サムネイルサフィックスを除去
        for suffix in ["_thumb", "_small", "_medium", "_large", "-thumb", "-small"]:
            url = url.replace(suffix, "")

        return url

    def _extract_images_from_json_ld(self, seen_urls: set) -> None:
        """JSON-LDから画像URLを抽出する"""
        try:
            scripts = self.page.query_selector_all('script[type="application/ld+json"]')

            for script in scripts:
                try:
                    content = script.inner_text()
                    data = json.loads(content)
                    items = data if isinstance(data, list) else [data]

                    for item in items:
                        if not isinstance(item, dict):
                            continue

                        if item.get("@type") == "Product":
                            # imageフィールドを取得
                            images = item.get("image", [])
                            if isinstance(images, str):
                                images = [images]

                            for img_url in images:
                                if isinstance(img_url, str) and img_url not in seen_urls:
                                    full_url = self._get_full_resolution_url(img_url)
                                    seen_urls.add(full_url)

                                    if not self._main_image_url:
                                        self._main_image_url = full_url
                                    elif len(self._image_urls) < 8:
                                        self._image_urls.append(full_url)

                except json.JSONDecodeError:
                    continue

        except Exception as e:
            logger.debug(f"JSON-LD画像抽出エラー: {e}")

    def _extract_specs_and_details(self) -> None:
        """
        仕様・スペックと詳細情報（発行年、発行数）を抽出する

        商品詳細リストから以下を取得:
        - Weight / Purity / Diameter / Thickness 等 → 仕様・スペック
        - Year / Mint Year → 発行年
        - Mintage / Limited → 発行数・限定数
        """
        try:
            specs_parts = []
            items = self.page.query_selector_all(self.SPECS_SELECTOR)

            for item in items:
                text = item.inner_text().strip()
                if not text:
                    continue

                # 発行年を検出
                year_match = re.match(r'^(Year|Mint Year|Issue Year):\s*(.+)$', text, re.IGNORECASE)
                if year_match:
                    self._mint_year = year_match.group(2).strip()
                    logger.debug(f"発行年検出: {self._mint_year}")
                    continue

                # 発行数・限定数を検出
                mintage_match = re.match(r'^(Mintage|Limited|Edition|Issued):\s*(.+)$', text, re.IGNORECASE)
                if mintage_match:
                    self._mintage = mintage_match.group(2).strip()
                    logger.debug(f"発行数検出: {self._mintage}")
                    continue

                # 仕様情報を収集（Country以外）
                if re.match(r'^(Weight|Purity|Fineness|Diameter|Thickness|Size|Metal|Content|AGW|ASW):', text, re.IGNORECASE):
                    specs_parts.append(text)

            if specs_parts:
                self._specs = " | ".join(specs_parts)
                logger.debug(f"仕様: {self._specs[:100]}...")

            # JSON-LDからも詳細情報を取得
            self._extract_details_from_json_ld()

            # 発行年が見つからない場合、商品名から抽出を試みる
            if not self._mint_year:
                self._extract_year_from_product_name()

        except Exception as e:
            logger.warning(f"仕様抽出エラー: {e}")

    def _extract_year_from_product_name(self) -> None:
        """商品名から発行年を抽出する"""
        try:
            # H1タグまたはJSON-LDから商品名を取得
            product_name = ""
            h1 = self.page.query_selector("h1")
            if h1:
                product_name = h1.inner_text().strip()

            if not product_name:
                return

            # 商品名の先頭に年（4桁）があるかチェック
            # 例: "2026 1 oz United Kingdom Gold Britannia"
            year_match = re.match(r'^(20\d{2})\s+', product_name)
            if year_match:
                self._mint_year = year_match.group(1)
                logger.debug(f"発行年検出（商品名から）: {self._mint_year}")
                return

            # "Year of the XXX" パターンから年を抽出
            # 例: "2024 Year of the Dragon"
            year_match = re.search(r'(20\d{2})\s+Year of', product_name, re.IGNORECASE)
            if year_match:
                self._mint_year = year_match.group(1)
                logger.debug(f"発行年検出（Year ofパターン）: {self._mint_year}")
                return

        except Exception as e:
            logger.debug(f"商品名からの発行年抽出エラー: {e}")

    def _extract_details_from_json_ld(self) -> None:
        """JSON-LDから詳細情報を抽出する"""
        try:
            scripts = self.page.query_selector_all('script[type="application/ld+json"]')

            for script in scripts:
                try:
                    content = script.inner_text()
                    data = json.loads(content)
                    items = data if isinstance(data, list) else [data]

                    for item in items:
                        if not isinstance(item, dict):
                            continue

                        if item.get("@type") == "Product":
                            # 追加プロパティからスペックを取得
                            additional_props = item.get("additionalProperty", [])
                            for prop in additional_props:
                                if isinstance(prop, dict):
                                    name = prop.get("name", "")
                                    value = prop.get("value", "")
                                    if name and value:
                                        if name.lower() in ["year", "mint year"]:
                                            if not self._mint_year:
                                                self._mint_year = str(value)
                                        elif name.lower() in ["mintage", "limited"]:
                                            if not self._mintage:
                                                self._mintage = str(value)

                except json.JSONDecodeError:
                    continue

        except Exception as e:
            logger.debug(f"JSON-LD詳細抽出エラー: {e}")

    def _extract_country_from_json_ld(self) -> Optional[str]:
        """JSON-LDから製造国を抽出する"""
        try:
            scripts = self.page.query_selector_all('script[type="application/ld+json"]')

            for script in scripts:
                try:
                    content = script.inner_text()
                    data = json.loads(content)
                    items = data if isinstance(data, list) else [data]

                    for item in items:
                        if not isinstance(item, dict):
                            continue

                        if item.get("@type") == "Product":
                            # countryOfOriginを探す
                            country = item.get("countryOfOrigin")
                            if country:
                                if isinstance(country, dict):
                                    return country.get("name", "")
                                return str(country)

                            # additionalPropertyからCountryを探す
                            additional_props = item.get("additionalProperty", [])
                            for prop in additional_props:
                                if isinstance(prop, dict):
                                    name = prop.get("name", "").lower()
                                    if name == "country":
                                        return str(prop.get("value", ""))

                except json.JSONDecodeError:
                    continue

            return None

        except Exception as e:
            logger.debug(f"JSON-LD製造国抽出エラー: {e}")
            return None

    def _extract_description(self) -> None:
        """商品説明（英語）を抽出する"""
        try:
            desc_elem = self.page.query_selector(self.DESCRIPTION_SELECTOR)
            if desc_elem:
                text = desc_elem.inner_text().strip()
                if text:
                    # 長すぎる場合は切り詰め
                    self._description_en = text[:5000] if len(text) > 5000 else text
                    logger.debug(f"商品説明: {self._description_en[:100]}...")
                    return

            # JSON-LDからも説明を取得
            scripts = self.page.query_selector_all('script[type="application/ld+json"]')
            for script in scripts:
                try:
                    content = script.inner_text()
                    data = json.loads(content)
                    items = data if isinstance(data, list) else [data]

                    for item in items:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            desc = item.get("description", "")
                            if desc:
                                self._description_en = desc[:5000] if len(desc) > 5000 else desc
                                return

                except json.JSONDecodeError:
                    continue

        except Exception as e:
            logger.warning(f"商品説明抽出エラー: {e}")

    # ========================================
    # 追加フィールドのゲッター
    # ========================================

    def get_main_image_url(self) -> str:
        """メイン画像URLを返す"""
        return self._main_image_url

    def get_thumbnail_url(self) -> str:
        """サムネイルURLを返す"""
        return self._thumbnail_url

    def get_image_urls(self) -> list[str]:
        """追加画像URL（最大8枚）を返す"""
        return self._image_urls

    def get_specs(self) -> str:
        """仕様・スペックを返す"""
        return self._specs

    def get_description_en(self) -> str:
        """商品説明（英語）を返す"""
        return self._description_en

    def get_mint_year(self) -> str:
        """発行年を返す"""
        return self._mint_year

    def get_mintage(self) -> str:
        """発行数・限定数を返す"""
        return self._mintage

    def reset_extra_fields(self) -> None:
        """追加フィールドをリセットする（次の商品用）"""
        self._detected_location = None
        self._main_image_url = ""
        self._thumbnail_url = ""
        self._image_urls = []
        self._specs = ""
        self._description_en = ""
        self._mint_year = ""
        self._mintage = ""
