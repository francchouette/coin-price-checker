"""
APMEX 商品ページ一覧取得スクリプト

APMEXの全商品ページURLとカテゴリー情報を取得し、
スプレッドシートの「APMEX商品ページ一覧」シートに保存する。

列構造（84列: A-CF）:
  ブリオンスター商品ページ一覧と同一構造。
  bs_sheet_columns.py の Col / Formula をそのまま使用する。

取得方式:
  Bright Data Browser API 経由で HTML スクレイピング。
  APMEX は Cloudflare によるボット検出があるため必須。

コマンド例:
  python -m src.apmex_products
  python -m src.apmex_products --fetch-prices --limit 10
  python -m src.apmex_products --category gold-coins -v
"""

import argparse
import asyncio
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.spreadsheet import SpreadsheetClient
from src.exchange_rate import ExchangeRateClient, WiseRateClient
from src.add_product import (
    JapaneseProductNameGenerator,
    CategoryDetector,
    DescriptionGenerator,
    SEOGenerator,
    ModelNumberGenerator,
)
from src.colorme import ColorMeClient
from src.bs_sheet_columns import Col, Formula, get_cell, cell_ref
from src.bullionstar_products import generate_supplier_id
from src.crawl_apmex import BrightDataBrowserClient, parse_html_products

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# カテゴリ定義
# ---------------------------------------------------------------------------
APMEX_CATEGORIES = [
    {"slug": "25000/gold-coins", "name": "gold-coins", "top": "Gold", "parent": "Gold Coins"},
    {"slug": "26000/silver-coins", "name": "silver-coins", "top": "Silver", "parent": "Silver Coins"},
    {"slug": "27000/platinum", "name": "platinum", "top": "Platinum", "parent": "Platinum Coins"},
    {"slug": "28000/palladium", "name": "palladium", "top": "Palladium", "parent": "Palladium Coins"},
    {"slug": "29000/copper", "name": "copper", "top": "Copper", "parent": "Copper Coins"},
    {"slug": "35000/gold-bars", "name": "gold-bars", "top": "Gold", "parent": "Gold Bars"},
    {"slug": "36000/silver-bars", "name": "silver-bars", "top": "Silver", "parent": "Silver Bars"},
    {"slug": "37000/platinum-bars", "name": "platinum-bars", "top": "Platinum", "parent": "Platinum Bars"},
    {"slug": "38000/palladium-bars", "name": "palladium-bars", "top": "Palladium", "parent": "Palladium Bars"},
]

BASE_URL = "https://www.apmex.com"
WAIT_SELECTOR = ".mod-product-card, [class*='product-item'], .product-list"


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------
@dataclass
class ApmexProduct:
    """APMEX商品データ（84列対応: A-CF）"""
    name: str
    url: str
    top_category: str
    parent_category: str
    child_category: str
    location: str  # 製造国

    adopted_flag: str = "検討中"
    colorme_registration: str = "未登録"
    supplier_id: str = ""
    cm_product_name: str = ""
    colorme_url: str = ""
    site: str = "APMEX"
    description_en: str = ""
    specs: str = ""
    mint_year: str = ""
    mintage: str = ""
    in_stock: Optional[bool] = None

    price: Optional[float] = None
    prev_price: float = 0.0
    currency: str = "USD"
    exchange_type: str = "クレカ"
    exchange_rate: float = 0.0
    price_jpy: float = 0.0

    image_url1: str = ""
    image_url2: str = ""
    image_url3: str = ""
    image_url4: str = ""
    image_url5: str = ""
    image_url6: str = ""
    image_url7: str = ""
    image_url8: str = ""
    image_url9: str = ""
    image_url10: str = ""

    model_number: str = ""
    product_id: str = ""
    fetched_at: str = ""
    last_price_updated: str = ""


# ---------------------------------------------------------------------------
# 詳細ページスクレイパー
# ---------------------------------------------------------------------------
_YEAR_RE = re.compile(r'^((?:19|20)\d{2})\s+')
_PRICE_RE = re.compile(r'\$\s*([0-9,]+(?:\.[0-9]{2})?)')

_DESC_SELECTORS = [
    '.product-description',
    '.mod-product-description',
    '#product-description',
    '.description-content',
    '.product-details-description',
]
_SPEC_SELECTORS = [
    '.product-specifications',
    '.mod-product-specs',
    '.specifications-table',
    '#product-specs',
    '.product-attributes',
    'table.specs',
]
_IMAGE_SIZE_RE = re.compile(r'width=\d+&height=\d+')
_IMAGE_HIGH_RES = 'width=900&height=900'
_STOCK_OUT_PATTERNS = ['out of stock', 'sold out', 'unavailable', 'no longer available']


def _parse_detail_html(html: str, product_name: str = "") -> dict:
    """詳細ページHTMLから各種情報を抽出"""
    soup = BeautifulSoup(html, 'html.parser')
    result = {
        "price": None,
        "in_stock": True,
        "description": "",
        "specs": "",
        "country": "",
        "year": "",
        "mintage": "",
        "images": [],
    }

    # --- 価格 ---
    # 割引価格を優先
    disc = soup.select_one('.price.discounted, .discounted-price')
    if disc:
        m = _PRICE_RE.search(disc.get_text())
        if m:
            result["price"] = float(m.group(1).replace(",", ""))

    if result["price"] is None:
        for sel in ['.product-price-value', '.product-buy-price', '.add-to-cart-price',
                    '.mod-product-pricing .price', '[data-price]']:
            el = soup.select_one(sel)
            if el:
                dp = el.get('data-price', '')
                if dp:
                    try:
                        result["price"] = float(dp)
                        break
                    except ValueError:
                        pass
                m = _PRICE_RE.search(el.get_text())
                if m:
                    result["price"] = float(m.group(1).replace(",", ""))
                    break

    if result["price"] is None:
        # ページ全体からの最初の $ 金額
        m = _PRICE_RE.search(soup.get_text())
        if m:
            val = float(m.group(1).replace(",", ""))
            if val > 1:
                result["price"] = val

    # --- 在庫 ---
    page_text = soup.get_text().lower()
    for pat in _STOCK_OUT_PATTERNS:
        if pat in page_text:
            result["in_stock"] = False
            break

    # --- 説明 ---
    for sel in _DESC_SELECTORS:
        el = soup.select_one(sel)
        if el:
            result["description"] = el.get_text(separator=' ', strip=True)[:2000]
            break

    # --- スペック ---
    for sel in _SPEC_SELECTORS:
        el = soup.select_one(sel)
        if el:
            result["specs"] = el.get_text(separator=' | ', strip=True)[:1000]
            break

    # --- スペック表からの追加抽出（製造国・発行数） ---
    spec_text = result["specs"].lower()
    for row in soup.select('tr, .spec-row, .attribute-row'):
        cells = row.find_all(['td', 'th', 'dt', 'dd', 'span'])
        for i, cell in enumerate(cells):
            label = cell.get_text(strip=True).lower()
            if 'country' in label or 'origin' in label:
                if i + 1 < len(cells):
                    result["country"] = cells[i + 1].get_text(strip=True)
            elif 'mintage' in label:
                if i + 1 < len(cells):
                    result["mintage"] = cells[i + 1].get_text(strip=True)

    # --- 発行年（商品名 or スペック表） ---
    m = _YEAR_RE.match(product_name)
    if m:
        result["year"] = m.group(1)
    else:
        for row in soup.select('tr, .spec-row'):
            cells = row.find_all(['td', 'th', 'span'])
            for i, cell in enumerate(cells):
                if 'year' in cell.get_text(strip=True).lower():
                    if i + 1 < len(cells):
                        yr = cells[i + 1].get_text(strip=True)
                        if re.match(r'^(19|20)\d{2}$', yr):
                            result["year"] = yr

    # --- 画像（最大10枚） ---
    # APMEX の商品ギャラリーは div.carousel-inner > div.item > a.img > img
    # サムネイル(65x65)を高解像度(900x900)に置換して取得
    seen_base = set()
    for img in soup.select('div.carousel-inner div.item a.img img'):
        src = img.get('src') or ''
        if not src or 'images/products/' not in src:
            continue
        # ベースURL（サイズパラメータ除去）で重複チェック
        base = _IMAGE_SIZE_RE.sub('', src)
        if base in seen_base:
            continue
        seen_base.add(base)
        # 高解像度に変換
        src = _IMAGE_SIZE_RE.sub(_IMAGE_HIGH_RES, src)
        result["images"].append(src)
        if len(result["images"]) >= 10:
            break

    return result


# ---------------------------------------------------------------------------
# 為替レート取得
# ---------------------------------------------------------------------------
def fetch_exchange_rates(exchange_type: str = "クレカ") -> dict[str, float]:
    """USD→JPY の為替レートを取得"""
    rates: dict[str, float] = {}
    exchange_client = ExchangeRateClient()

    if exchange_type == "Wise":
        wise_client = WiseRateClient()
        rate = wise_client.get_rate("USD", "JPY")
        if rate:
            rates["USD"] = rate
            logger.info(f"  Wise: 1 USD = {rate:.4f} JPY")
        else:
            rate = exchange_client.get_rate("USD", "JPY")
            if rate:
                rates["USD"] = rate
                logger.info(f"  Wise(代替): 1 USD = {rate:.4f} JPY")
    else:
        rate = exchange_client.get_credit_card_rate("USD", "JPY")
        if rate:
            rates["USD"] = rate
            logger.info(f"  クレカ: 1 USD = {rate:.4f} JPY")

    return rates


# ---------------------------------------------------------------------------
# 商品一覧取得（Bright Data Browser API）
# ---------------------------------------------------------------------------
async def fetch_product_list(
    category_filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[ApmexProduct]:
    """Bright Data Browser API 経由で APMEX 商品一覧を取得"""

    ws_endpoint = Config.BRIGHTDATA_BROWSER_WS
    if not ws_endpoint:
        logger.error("BRIGHTDATA_BROWSER_WS が設定されていません")
        return []

    categories = APMEX_CATEGORIES
    if category_filter:
        categories = [c for c in categories if c["name"] == category_filter]
        if not categories:
            logger.error(f"不明なカテゴリ: {category_filter}")
            return []

    all_products: list[ApmexProduct] = []
    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    for cat in categories:
        if limit and len(all_products) >= limit:
            break

        cat_slug = cat["slug"]
        cat_name = cat["name"]
        top_cat = cat["top"]
        parent_cat = cat["parent"]
        logger.info(f"\n=== カテゴリ: {cat_name} ({top_cat} > {parent_cat}) ===")

        client = BrightDataBrowserClient(ws_endpoint)
        try:
            await client.connect()
            url = f"{BASE_URL}/category/{cat_slug}"
            logger.info(f"  初回アクセス: {url}")
            html = await client.navigate(url, wait_selector=WAIT_SELECTOR)

            if not html:
                logger.warning(f"  HTML取得失敗、スキップ")
                continue

            # Cloudflare 検出
            if 'Just a moment' in html or 'challenge-platform' in html:
                logger.warning(f"  Cloudflare検出、カテゴリスキップ: {cat_name}")
                continue

            page_num = 1
            while True:
                raw_products = parse_html_products(html, cat_name, timestamp, BASE_URL)
                logger.info(f"  ページ{page_num}: {len(raw_products)}件")

                for rp in raw_products:
                    if limit and len(all_products) >= limit:
                        break
                    # crawl_apmex の ApmexProduct → 本モジュールの ApmexProduct に変換
                    ap = ApmexProduct(
                        name=rp.name,
                        url=rp.url,
                        top_category=top_cat,
                        parent_category=parent_cat,
                        child_category="",
                        location="",
                        in_stock=rp.in_stock,
                        price=rp.price if rp.price > 0 else None,
                        product_id=rp.product_id,
                        fetched_at=timestamp,
                    )
                    # 発行年を商品名から抽出
                    m = _YEAR_RE.match(rp.name)
                    if m:
                        ap.mint_year = m.group(1)
                    # リスト画像（1枚目）
                    if rp.images:
                        ap.image_url1 = rp.images[0] if len(rp.images) > 0 else ""
                    all_products.append(ap)

                if limit and len(all_products) >= limit:
                    break

                # 次ページ
                success, next_html = await client.click_next_page(wait_selector=WAIT_SELECTOR)
                if not success or not next_html:
                    break
                html = next_html
                page_num += 1
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"  カテゴリ {cat_name} でエラー: {e}")
        finally:
            await client.close()

        logger.info(f"  → {cat_name}: 累計 {len(all_products)}件")

    logger.info(f"\n商品一覧取得完了: {len(all_products)}件")
    return all_products


# ---------------------------------------------------------------------------
# 詳細ページスクレイピング
# ---------------------------------------------------------------------------
async def fetch_prices_for_products(
    products: list[ApmexProduct],
    limit: Optional[int] = None,
    exchange_type: str = "クレカ",
) -> list[ApmexProduct]:
    """Bright Data 経由で詳細ページから価格・画像・スペック等を取得"""

    ws_endpoint = Config.BRIGHTDATA_BROWSER_WS
    if not ws_endpoint:
        logger.error("BRIGHTDATA_BROWSER_WS が設定されていません")
        return products

    # 為替レート
    logger.info("為替レートを取得中...")
    rates = fetch_exchange_rates(exchange_type)
    usd_rate = rates.get("USD", 0.0)
    if usd_rate <= 0:
        logger.warning("USD為替レート取得失敗。JPY変換なしで続行します。")

    targets = products[:limit] if limit else products
    logger.info(f"詳細ページ取得対象: {len(targets)}件")

    for i, product in enumerate(targets):
        logger.info(f"  [{i + 1}/{len(targets)}] {product.name[:50]}")

        # Bright Data は1セッションあたりのナビゲーション数に制限があるため、
        # 商品ごとに新しいセッションを開く
        client = BrightDataBrowserClient(ws_endpoint)
        try:
            await client.connect()
            html = await client.navigate(product.url, wait_selector='.product-description, .product-details, .mod-product-pricing')
            if not html:
                logger.warning(f"    HTML取得失敗")
                continue
            if 'Just a moment' in html:
                logger.warning(f"    Cloudflare検出、スキップ")
                continue

            detail = _parse_detail_html(html, product.name)

            # 価格
            if detail["price"] and detail["price"] > 1:
                product.price = detail["price"]
            product.in_stock = detail["in_stock"]

            # 為替変換
            if product.price and usd_rate > 0:
                product.exchange_type = exchange_type
                product.exchange_rate = usd_rate
                product.price_jpy = product.price * usd_rate

            # テキスト情報
            if detail["description"]:
                product.description_en = detail["description"]
            if detail["specs"]:
                product.specs = detail["specs"]
            if detail["country"]:
                product.location = detail["country"]
            if detail["year"]:
                product.mint_year = detail["year"]
            if detail["mintage"]:
                product.mintage = detail["mintage"]

            # 画像（最大10枚）
            imgs = detail["images"]
            for idx, img_url in enumerate(imgs[:10]):
                setattr(product, f"image_url{idx + 1}", img_url)

            product.last_price_updated = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"    → 価格=${product.price}, 在庫={'○' if product.in_stock else '×'}, 画像{len(imgs)}枚")

        except Exception as e:
            logger.warning(f"    詳細取得エラー: {e}")
        finally:
            await client.close()

        await asyncio.sleep(1)

    return products


# ---------------------------------------------------------------------------
# スプレッドシート保存（84列）
# ---------------------------------------------------------------------------
def save_products_to_spreadsheet(products: list[ApmexProduct], dry_run: bool = False) -> bool:
    """APMEX商品ページ一覧シートに84列構造で保存"""

    if dry_run:
        logger.info("[ドライラン] スプレッドシートへの保存をスキップ")
        for p in products:
            logger.info(f"  {p.name[:50]} | {p.url[:60]} | ${p.price or 0}")
        return True

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    # AI生成器を初期化
    name_generator = JapaneseProductNameGenerator()
    description_generator = DescriptionGenerator()
    seo_generator = SEOGenerator()
    model_number_generator = None

    # カテゴリー判定器
    category_detector = None
    category_name_map = {
        2977963: "ゴールド（金）",
        2977964: "シルバー（銀）",
        2977965: "プラチナ",
        2977966: "パラジウム",
        2977967: "カッパー（銅）",
    }
    group_name_map = {}
    try:
        colorme_client = ColorMeClient()
        categories = colorme_client.get_categories()
        groups = colorme_client.get_groups()
        category_detector = CategoryDetector(categories, groups, colorme_client)
        for grp in groups:
            gid = grp.get("id", 0)
            gname = grp.get("name", "")
            if gid and gname:
                group_name_map[gid] = gname
        logger.info(f"カテゴリー判定器: {len(categories)}カテゴリー, {len(groups)}グループ")
    except Exception as e:
        logger.warning(f"カテゴリー判定器の初期化に失敗: {e}")

    # ヘッダー（84列: bs_sheet_columns.py の Col 定義に合わせる）
    all_cols = Col.all_columns()
    headers = [c.name for c in all_cols]

    sheet_name = Config.SHEET_APMEX_PRODUCTS

    try:
        # シート取得または作成
        try:
            sheet = client._spreadsheet.worksheet(sheet_name)
            logger.info(f"既存シート '{sheet_name}' を使用")
        except Exception:
            sheet = client._spreadsheet.add_worksheet(
                title=sheet_name, rows=10000, cols=Col.TOTAL_COLUMNS + 1
            )
            sheet.update(f'A1:{Col.last_column_letter()}1', [headers])
            logger.info(f"シート '{sheet_name}' を作成しました")

        # 既存データ取得
        existing_data = sheet.get_all_values()
        if not existing_data:
            sheet.update(f'A1:{Col.last_column_letter()}1', [headers])
            existing_data = [headers]

        # URL でインデックス化（F列 = PRODUCT_URL）
        existing_by_url: dict[str, tuple[int, list[str]]] = {}
        existing_ids: set[str] = set()
        for row_idx, row in enumerate(existing_data[1:], start=2):
            url_val = get_cell(row, Col.PRODUCT_URL)
            if url_val:
                existing_by_url[url_val] = (row_idx, row)
            sid = get_cell(row, Col.SUPPLIER_ID)
            if sid:
                existing_ids.add(sid)

        logger.info(f"既存商品数: {len(existing_by_url)}件")

        # 型番生成器初期化
        existing_model_numbers = set()
        for row in existing_data[1:]:
            mn = get_cell(row, Col.CM_MODEL_NUMBER)
            if mn:
                existing_model_numbers.add(mn)
        model_number_generator = ModelNumberGenerator(existing_model_numbers)

        # 新規行を構築
        new_rows = []
        skipped_count = 0
        BATCH_SAVE_INTERVAL = 10

        def save_batch(new_rows_batch, start_row_offset):
            """バッチ保存"""
            if not new_rows_batch:
                return 0
            start_row = len(existing_data) + start_row_offset + 1
            for k, row in enumerate(new_rows_batch):
                Formula.set_all_formulas(row, start_row + k)
            end_row = start_row + len(new_rows_batch) - 1
            range_str = f"A{start_row}:{Col.last_column_letter()}{end_row}"
            logger.info(f"  保存中: {range_str} ({len(new_rows_batch)}件)")
            result = sheet.update(values=new_rows_batch, range_name=range_str, value_input_option='USER_ENTERED')
            logger.info(f"  保存完了: {result.get('updatedCells', 0)}セル")

            # データ検証（A列・B列にドロップダウン）
            sheet_id = sheet.id
            validation_requests = [
                {
                    'setDataValidation': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': start_row - 1,
                            'endRowIndex': end_row,
                            'startColumnIndex': 0,
                            'endColumnIndex': 1,
                        },
                        'rule': {
                            'condition': {
                                'type': 'ONE_OF_LIST',
                                'values': [
                                    {'userEnteredValue': '採用'},
                                    {'userEnteredValue': '未採用'},
                                    {'userEnteredValue': '検討中'},
                                ],
                            },
                            'showCustomUi': True,
                            'strict': False,
                        },
                    }
                },
                {
                    'setDataValidation': {
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': start_row - 1,
                            'endRowIndex': end_row,
                            'startColumnIndex': 1,
                            'endColumnIndex': 2,
                        },
                        'rule': {
                            'condition': {
                                'type': 'ONE_OF_LIST',
                                'values': [
                                    {'userEnteredValue': '登録済'},
                                    {'userEnteredValue': '未登録'},
                                ],
                            },
                            'showCustomUi': True,
                            'strict': False,
                        },
                    }
                },
            ]
            try:
                sheet.spreadsheet.batch_update({'requests': validation_requests})
            except Exception as e:
                logger.warning(f"  データ検証設定エラー: {e}")

            return len(new_rows_batch)

        total_new_saved = 0
        processed_count = 0

        for product in products:
            if product.url in existing_by_url:
                skipped_count += 1
                continue

            # 仕入れ先商品ID
            supplier_id = generate_supplier_id(existing_ids, prefix="AP")
            existing_ids.add(supplier_id)

            stock_status = ""
            if product.in_stock is not None:
                stock_status = "In Stock" if product.in_stock else "Out of Stock"

            # --- AI生成: CM商品名 ---
            cm_product_name = ""
            try:
                info = {"name": product.name, "specs": product.specs, "description": product.description_en}
                cm_product_name = name_generator.generate(info, quantity=1) or ""
            except Exception as e:
                logger.debug(f"  CM商品名生成エラー: {e}")

            # --- AI生成: カテゴリー・グループ ---
            category_big = ""
            category_big_name = ""
            category_small = ""
            category_small_name = ""
            group_ids_str = ""
            group_names_str = ""
            if category_detector:
                try:
                    cat_big, cat_small, group_ids = category_detector.detect(product.name, product.url)
                    if cat_big:
                        category_big = str(cat_big)
                        category_big_name = category_name_map.get(cat_big, "")
                        if cat_small:
                            category_small = str(cat_small)
                            category_small_name = category_name_map.get(cat_small, "")
                    if group_ids:
                        group_ids_str = "'" + ",".join(str(g) for g in group_ids)
                        group_names = [group_name_map.get(g, "") for g in group_ids]
                        group_names_str = ",".join(n for n in group_names if n)
                except Exception as e:
                    logger.debug(f"  カテゴリー判定エラー: {e}")

            # --- AI生成: 商品説明 ---
            cm_description = ""
            cm_simple_description = ""
            if description_generator.genai_model:
                try:
                    price_jpy = int(product.price_jpy) if product.price_jpy else 0
                    desc_info = {
                        "name": product.name,
                        "price": price_jpy,
                        "currency": "JPY",
                        "description": product.description_en or "",
                        "specs": product.specs or "",
                    }
                    cm_description, cm_simple_description = description_generator.generate(desc_info)
                except Exception as e:
                    logger.debug(f"  商品説明生成エラー: {e}")

            # --- AI生成: SEO ---
            page_title = ""
            meta_description = ""
            meta_keywords = ""
            if seo_generator.genai_model:
                try:
                    seo_info = {
                        "name": product.name,
                        "price": int(product.price_jpy) if product.price_jpy else 0,
                        "description": product.description_en or "",
                        "specs": product.specs or "",
                    }
                    page_title, meta_description, meta_keywords = seo_generator.generate(seo_info)
                except Exception as e:
                    logger.debug(f"  SEO生成エラー: {e}")

            # --- AI生成: 型番 ---
            model_number = ""
            if model_number_generator and model_number_generator.genai_model:
                try:
                    model_info = {"name": product.name, "specs": product.specs or "", "description": product.description_en or ""}
                    model_number = model_number_generator.generate(model_info, quantity=1, supplier_site="APMEX")
                except Exception as e:
                    logger.debug(f"  型番生成エラー: {e}")
            if not model_number:
                model_number = supplier_id

            # --- 84列の行データ構築 ---
            new_row = [
                # === 管理列（A-C: 3列）===
                product.adopted_flag,
                product.colorme_registration,
                supplier_id,

                # === CM商品名（D: 1列）===
                cm_product_name,

                # === 仕入れ先商品情報（E-Q: 13列）===
                "",                                                     # E: カラーミー商品URL
                product.url,                                            # F: 仕入れ先商品URL
                product.name,                                           # G: 仕入れ先商品名
                product.site,                                           # H: 仕入れ先サイト
                product.top_category,                                   # I: 最上位カテゴリ
                product.parent_category,                                # J: 親カテゴリ
                product.child_category,                                 # K: 子カテゴリ
                product.location,                                       # L: 製造国
                product.description_en,                                 # M: 商品説明（英語）
                product.specs,                                          # N: 仕様・スペック
                product.mint_year,                                      # O: 発行年
                product.mintage,                                        # P: 発行数・限定数
                stock_status,                                           # Q: 仕入れ先在庫状況

                # === 価格情報（R-AH: 17列）===
                str(product.price) if product.price else "",            # R: 仕入れ先価格
                "",                                                     # S: 前回仕入れ価格
                "",                                                     # T: 価格変動率（数式）
                product.currency,                                       # U: 取引通貨
                product.exchange_type,                                  # V: 為替種類
                str(product.exchange_rate) if product.exchange_rate else "",  # W: 為替レート
                str(int(product.price_jpy)) if product.price_jpy else "",    # X: 仕入れ額(日本円)
                "1",                                                    # Y: 枚数
                "",                                                     # Z: 仕入れ合計（数式）
                "1.1",                                                  # AA: 設定マージン率
                "",                                                     # AB: 設定マージン額
                "100",                                                  # AC: 送料
                "50",                                                   # AD: 諸経費
                "",                                                     # AE: 合計原価（数式）
                "",                                                     # AF: 適正価格（数式）
                "",                                                     # AG: 粗利額（数式）
                "",                                                     # AH: 粗利率（数式）

                # === カラーミー価格情報（AI-AN: 6列）===
                "",                                                     # AI: 販売価格（数式）
                "",                                                     # AJ: 定価（数式）
                "",                                                     # AK: 会員価格（数式）
                "",                                                     # AL: 原価（数式）
                "",                                                     # AM: 消費税込販売価格（数式）
                "",                                                     # AN: 消費税額（数式）

                # === カテゴリー・グループ（AO-AT: 6列）===
                category_big,                                           # AO: 大カテゴリーID
                category_big_name,                                      # AP: 大カテゴリー名称
                category_small,                                         # AQ: 小カテゴリーID
                category_small_name,                                    # AR: 小カテゴリー名称
                group_ids_str,                                          # AS: グループID
                group_names_str,                                        # AT: グループ名

                # === 型番（AU: 1列）===
                model_number,

                # === 在庫管理（AV-BB: 7列）===
                "10",                                                   # AV: 在庫数
                "する",                                                 # AW: 在庫管理
                "3",                                                    # AX: 残りわずか数
                "表示",                                                 # AY: 売切れ表示
                "1",                                                    # AZ: 最小購入数
                "10",                                                   # BA: 最大購入数
                "",                                                     # BB: 単位

                # === 送料・配送（BC-BF: 4列）===
                "1000",                                                 # BC: 個別送料
                "",                                                     # BD: クール便料金
                "",                                                     # BE: 重量(g)
                "",                                                     # BF: 配送不要

                # === 商品説明（BG-BJ: 4列）===
                cm_description,                                         # BG: 商品説明
                cm_simple_description,                                  # BH: 簡易説明
                "",                                                     # BI: スマホ説明
                "",                                                     # BJ: 備考

                # === 画像URL（BK-BT: 10列）===
                product.image_url1,
                product.image_url2,
                product.image_url3,
                product.image_url4,
                product.image_url5,
                product.image_url6,
                product.image_url7,
                product.image_url8,
                product.image_url9,
                product.image_url10,

                # === SEO項目（BU-BW: 3列）===
                page_title,
                meta_description,
                meta_keywords,

                # === フラグ・設定（BX-CB: 5列）===
                "対象外",                                               # BX: 軽減税率対象
                "対象外",                                               # BY: デジタルコンテンツ
                "対象外",                                               # BZ: 定期購入
                "0",                                                    # CA: 表示順
                "",                                                     # CB: 利用不可決済

                # === 掲載期間（CC-CD: 2列）===
                "",                                                     # CC: 掲載開始日時
                "",                                                     # CD: 掲載終了日時

                # === システム情報（CE-CF: 2列）===
                product.fetched_at,                                     # CE: 同期日時
                product.last_price_updated,                             # CF: 商品更新日時
            ]
            new_rows.append(new_row)

            processed_count += 1
            if processed_count % BATCH_SAVE_INTERVAL == 0 and new_rows:
                logger.info(f"中間保存中... ({processed_count}/{len(products)}件処理済み)")
                saved = save_batch(new_rows, total_new_saved)
                total_new_saved += saved
                new_rows = []

        # 残りを保存
        if new_rows:
            logger.info(f"最終保存中... (残り{len(new_rows)}件)")
            saved = save_batch(new_rows, total_new_saved)
            total_new_saved += saved

        logger.info(f"\n=== 結果 ===")
        logger.info(f"新規追加: {total_new_saved}件")
        logger.info(f"スキップ（既存）: {skipped_count}件")
        return True

    except Exception as e:
        logger.error(f"スプレッドシート保存エラー: {e}")
        import traceback
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="APMEX商品ページ一覧取得")
    parser.add_argument("--verbose", "-v", action="store_true", help="詳細ログ")
    parser.add_argument("--dry-run", action="store_true", help="ドライラン（シート書き込みなし）")
    parser.add_argument("--fetch-prices", action="store_true", help="詳細ページから価格・画像等を取得")
    parser.add_argument("--limit", type=int, default=0, help="取得件数制限（0=無制限）")
    parser.add_argument("--category", type=str, default="", help="カテゴリフィルタ（例: gold-coins）")
    parser.add_argument("--exchange-type", type=str, default="クレカ", help="為替種類: クレカ or Wise")
    args = parser.parse_args()

    # ロギング設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    limit = args.limit if args.limit > 0 else None
    category = args.category or None

    logger.info("=== APMEX 商品ページ一覧取得 ===")
    logger.info(f"カテゴリ: {category or '全カテゴリ'}")
    logger.info(f"件数制限: {limit or '無制限'}")
    logger.info(f"詳細取得: {'あり' if args.fetch_prices else 'なし'}")
    logger.info(f"ドライラン: {'はい' if args.dry_run else 'いいえ'}")

    # 商品一覧取得
    products = asyncio.run(fetch_product_list(category_filter=category, limit=limit))
    if not products:
        logger.warning("取得商品なし")
        return 0

    logger.info(f"商品一覧: {len(products)}件取得")

    # 詳細ページ取得
    if args.fetch_prices:
        logger.info("\n=== 詳細ページ取得開始 ===")
        products = asyncio.run(fetch_prices_for_products(
            products, limit=limit, exchange_type=args.exchange_type
        ))

    # スプレッドシートに保存
    logger.info("\n=== スプレッドシート保存 ===")
    success = save_products_to_spreadsheet(products, dry_run=args.dry_run)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
