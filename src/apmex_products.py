"""
APMEX 商品ページ一覧取得スクリプト

APMEXの全商品ページURLとカテゴリー情報を取得し、
スプレッドシートの「APMEX商品ページ一覧」シートに保存する。

列構造（84列: A-CF）:
  ブリオンスター商品ページ一覧と同一構造。
  bs_sheet_columns.py の Col / Formula をそのまま使用する。

取得方式:
  直接HTTPリクエスト（requests）でスクレイピング。
  カテゴリ一覧: XHR（X-Requested-With: XMLHttpRequest）でJSON取得
  商品詳細: Schema.org JSON-LD + HTML解析

コマンド例:
  python -m src.apmex_products
  python -m src.apmex_products --fetch-prices --limit 10
  python -m src.apmex_products --category gold-coins -v
"""

import argparse
import json
import logging
import re
import signal
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
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

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------------------
# カテゴリ定義
# ---------------------------------------------------------------------------
APMEX_CATEGORIES = [
    {"slug": "10010/gold-coins", "name": "gold-coins", "top": "Gold", "parent": "Gold Coins"},
    {"slug": "20005/silver-coins", "name": "silver-coins", "top": "Silver", "parent": "Silver Coins"},
    {"slug": "30040/platinum-coins", "name": "platinum-coins", "top": "Platinum", "parent": "Platinum Coins"},
    {"slug": "32603/palladium-coins", "name": "palladium-coins", "top": "Palladium", "parent": "Palladium Coins"},
    {"slug": "34001/copper-bullion", "name": "copper-bullion", "top": "Copper", "parent": "Copper Coins"},
    {"slug": "19000/gold-bars-rounds", "name": "gold-bars", "top": "Gold", "parent": "Gold Bars"},
    {"slug": "25400/silver-bars", "name": "silver-bars", "top": "Silver", "parent": "Silver Bars"},
    {"slug": "32050/platinum-bars-rounds", "name": "platinum-bars", "top": "Platinum", "parent": "Platinum Bars"},
    {"slug": "33500/palladium-bars-rounds", "name": "palladium-bars", "top": "Palladium", "parent": "Palladium Bars"},
]

BASE_URL = "https://www.apmex.com"

# HTTP セッション設定
_HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}


def _create_http_session() -> requests.Session:
    """APMEX直接アクセス用HTTPセッションを作成"""
    session = requests.Session()
    session.headers.update(_HTTP_HEADERS)
    return session


# ---------------------------------------------------------------------------
# AI生成タイムアウトユーティリティ
# ---------------------------------------------------------------------------
AI_TIMEOUT_SECONDS = 60


def _run_with_timeout(func, *args, timeout=AI_TIMEOUT_SECONDS, default=None, **kwargs):
    """関数をタイムアウト付きで実行する。タイムアウト時はdefaultを返す。"""
    def _handler(signum, frame):
        raise TimeoutError(f"{timeout}秒タイムアウト")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout)
    try:
        result = func(*args, **kwargs)
        signal.alarm(0)
        return result
    except TimeoutError:
        logger.warning(f"    AI生成タイムアウト ({timeout}秒)")
        return default
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


# ---------------------------------------------------------------------------
# 商品一覧キャッシュ
# ---------------------------------------------------------------------------
CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_TTL_HOURS = 24


def _get_category_cache_path(cat_name: str) -> Path:
    """カテゴリ別キャッシュファイルパスを返す"""
    return CACHE_DIR / f"apmex_{cat_name}.json"


def _save_category_cache(cat_name: str, products: list) -> None:
    """1カテゴリ分の商品リストをキャッシュに保存"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _get_category_cache_path(cat_name)
    cache_data = {
        "cached_at": datetime.now(JST).isoformat(),
        "category": cat_name,
        "count": len(products),
        "products": [asdict(p) for p in products],
    }
    cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2))
    logger.info(f"  キャッシュ保存: {cat_name} ({len(products)}件)")


def _load_category_cache(cat_name: str) -> Optional[list]:
    """カテゴリ別キャッシュを読み込む。期限切れ・なければ None"""
    cache_path = _get_category_cache_path(cat_name)
    if not cache_path.exists():
        return None

    try:
        cache_data = json.loads(cache_path.read_text())
        cached_at = datetime.fromisoformat(cache_data["cached_at"])
        age = datetime.now(JST) - cached_at
        age_hours = age.total_seconds() / 3600

        if age_hours > CACHE_TTL_HOURS:
            logger.info(f"  キャッシュ期限切れ: {cat_name} ({age_hours:.1f}時間経過)")
            return None

        products = [ApmexProduct(**item) for item in cache_data["products"]]
        logger.info(f"  キャッシュから読み込み: {cat_name} ({len(products)}件, {age_hours:.1f}時間前)")
        return products
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"  キャッシュ読み込みエラー ({cat_name}): {e}")
        return None


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


# ---------------------------------------------------------------------------
# 商品名からカテゴリ自動判定
# ---------------------------------------------------------------------------
# 金属種別キーワード（優先度順: 長いキーワードを先に判定）
_METAL_KEYWORDS = [
    # Platinum
    ("platinum", "Platinum"),
    ("1/10 oz pt", "Platinum"),
    ("1/4 oz pt", "Platinum"),
    ("1/2 oz pt", "Platinum"),
    ("1 oz pt", "Platinum"),
    # Palladium
    ("palladium", "Palladium"),
    # Copper
    ("copper", "Copper"),
    # Gold（silver を含まないことを確認）
    ("gold", "Gold"),
    # Silver（化学記号 Ag 含む）
    ("silver", "Silver"),
]

# 商品名に "Silver" がなくても銀貨として判定する米国歴史的銀貨パターン
_SILVER_COIN_PATTERNS = [
    "morgan dollar", "morgan dollars",
    "mercury dime", "mercury dimes",
    "walking liberty", "franklin half", "franklin halves",
    "kennedy half", "barber dime", "barber dimes",
    "barber quarter", "barber quarters", "barber half", "barber halves",
    "standing liberty quarter", "standing liberty quarters",
    "peace dollar", "peace dollars",
    "seated liberty",
    "90% ", "40% ",  # "90% Mercury Dime" 等（junk silver）
    " ag ", " ag$",  # 化学記号 Ag（"1 oz Ag" 等）
]

# URL パスからの金属判定
_URL_METAL_PATTERNS = [
    (r'/gold-coins', "Gold"),
    (r'/gold-bars', "Gold"),
    (r'/silver-coins', "Silver"),
    (r'/silver-bars', "Silver"),
    (r'/platinum', "Platinum"),
    (r'/palladium', "Palladium"),
    (r'/copper', "Copper"),
]

# バー/ラウンド判定キーワード
_BAR_KEYWORDS = [" bar ", " bar,", " bars ", " bars,", " round ", " round,",
                 " ingot", " kilo ", " kilogram"]


def detect_category_from_name(name: str, url: str = "") -> tuple[str, str]:
    """商品名とURLから (top_category, parent_category) を判定する。

    Returns:
        ("Gold", "Gold Coins"), ("Silver", "Silver Bars") 等
    """
    name_lower = name.lower()
    url_lower = url.lower()

    # 1. 商品名から金属種別を判定
    metal = ""
    for keyword, metal_type in _METAL_KEYWORDS:
        if keyword in name_lower:
            metal = metal_type
            break

    # 1.5. 歴史的銀貨パターン（商品名に "Silver" がないケース）
    if not metal:
        for pattern in _SILVER_COIN_PATTERNS:
            if pattern in name_lower:
                metal = "Silver"
                break

    # 2. 商品名で判定できない場合、URLから判定
    if not metal:
        for pattern, metal_type in _URL_METAL_PATTERNS:
            if pattern in url_lower:
                metal = metal_type
                break

    # 3. それでも判定できない場合、デフォルト
    if not metal:
        metal = "Other"

    # 4. バー or コイン判定
    is_bar = any(kw in f" {name_lower} " or name_lower.endswith(kw.strip())
                 for kw in _BAR_KEYWORDS)

    if is_bar:
        product_type = f"{metal} Bars"
    else:
        product_type = f"{metal} Coins"

    return metal, product_type


def _parse_jsonld_from_html(html: str) -> dict:
    """HTMLからSchema.org JSON-LDのProduct情報を抽出"""
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                if data.get('@type') == 'Product':
                    return data
                if '@graph' in data:
                    for item in data['@graph']:
                        if isinstance(item, dict) and item.get('@type') == 'Product':
                            return item
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get('@type') == 'Product':
                        return item
        except (json.JSONDecodeError, TypeError):
            continue
    return {}


def _parse_detail_html(html: str, product_name: str = "") -> dict:
    """詳細ページHTMLから各種情報を抽出（Schema.org JSON-LD優先）"""
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

    # === Schema.org JSON-LD から抽出（優先） ===
    jsonld = _parse_jsonld_from_html(html)
    if jsonld:
        # 価格: offers.price or priceSpecification
        offers = jsonld.get('offers', {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        price_str = offers.get('price', '')
        if price_str:
            try:
                # "$205.87 USD" -> 205.87
                clean = re.sub(r'[^\d.]', '', str(price_str).split()[0] if ' ' in str(price_str) else str(price_str))
                result["price"] = float(clean)
            except (ValueError, TypeError, IndexError):
                pass

        # priceSpecification からクレジットカード価格を取得
        if not result["price"]:
            price_specs = offers.get('priceSpecification', [])
            if isinstance(price_specs, list):
                for spec in price_specs:
                    name_lower = spec.get('name', '').lower()
                    if 'credit' in name_lower or 'paypal' in name_lower:
                        try:
                            result["price"] = float(spec.get('price', 0))
                        except (ValueError, TypeError):
                            pass
                        break
                # フォールバック: 最初の価格仕様
                if not result["price"] and price_specs:
                    try:
                        result["price"] = float(price_specs[0].get('price', 0))
                    except (ValueError, TypeError):
                        pass

        # 在庫状況
        availability = (offers.get('availability', '') or '').lower()
        if 'instock' in availability:
            result["in_stock"] = True
        elif 'outofstock' in availability:
            result["in_stock"] = False

        # 説明
        desc = jsonld.get('description', '')
        if desc:
            result["description"] = str(desc)[:2000]

        # 画像
        images = jsonld.get('image', [])
        if isinstance(images, str):
            images = [images]
        elif isinstance(images, dict):
            images = [images.get('url', '')]
        jsonld_images = [img for img in images if img]

    # === HTML から補完 ===

    # --- 価格（JSON-LDで取れなかった場合）---
    if result["price"] is None:
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

    # --- 在庫（HTML テキストからの追加チェック）---
    page_text = soup.get_text().lower()
    for pat in _STOCK_OUT_PATTERNS:
        if pat in page_text:
            result["in_stock"] = False
            break

    # --- 説明（HTML）---
    if not result["description"]:
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
    # 1. HTML ギャラリーから取得
    seen_base = set()
    for img in soup.select('div.carousel-inner div.item a.img img'):
        src = img.get('src') or ''
        if not src or 'images/products/' not in src:
            continue
        base = _IMAGE_SIZE_RE.sub('', src)
        if base in seen_base:
            continue
        seen_base.add(base)
        src = _IMAGE_SIZE_RE.sub(_IMAGE_HIGH_RES, src)
        result["images"].append(src)
        if len(result["images"]) >= 10:
            break

    # 2. images-apmex.com の画像を追加検索
    if len(result["images"]) < 10:
        for img in soup.select('img[src*="images-apmex.com"]'):
            src = img.get('src') or ''
            if not src:
                continue
            base = _IMAGE_SIZE_RE.sub('', src)
            if base in seen_base:
                continue
            seen_base.add(base)
            src = _IMAGE_SIZE_RE.sub(_IMAGE_HIGH_RES, src)
            result["images"].append(src)
            if len(result["images"]) >= 10:
                break

    # 3. JSON-LD の画像をフォールバック
    if not result["images"] and jsonld:
        result["images"] = jsonld_images[:10]

    return result


def _parse_category_products(html: str, timestamp: str) -> list['ApmexProduct']:
    """カテゴリページ（またはXHRレスポンス）のHTMLから商品リストを抽出"""
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    items = soup.select('.mod-product-card')
    if not items:
        items = soup.select('[class*="product-card"]')

    for item in items:
        # URL
        link = item.select_one('a.item-link[href]')
        if not link:
            link = item.select_one('a[href*="/product/"]')
        if not link:
            # タイトル付きリンクにフォールバック
            link = item.select_one('a[title][href*="/product/"]')
        if not link:
            continue

        href = link.get('href', '')
        if not href or '/product/' not in href:
            continue
        url = href if href.startswith('http') else f"{BASE_URL}{href}"

        # 商品名
        name = link.get('data-product-name', '') or link.get('title', '')
        if not name:
            title_elem = item.select_one('.mod-product-title')
            if title_elem:
                name = title_elem.get_text(strip=True)
        if not name or len(name) < 3:
            continue

        # 価格
        price = None
        text = item.get_text()
        match = _PRICE_RE.search(text)
        if match:
            price = float(match.group(1).replace(',', ''))

        # 商品ID
        product_id = link.get('data-product-id', '')
        if not product_id:
            m = re.search(r'/product/(\d+)', url)
            if m:
                product_id = m.group(1)

        # 画像
        img_urls = []
        for img in item.select('img[src]'):
            src = img.get('src') or img.get('data-src')
            if src and not src.startswith('data:'):
                full_src = src if src.startswith('http') else f"{BASE_URL}{src}"
                if full_src not in img_urls:
                    img_urls.append(full_src)

        # 在庫
        in_stock = True
        text_lower = text.lower()
        for pat in _STOCK_OUT_PATTERNS:
            if pat in text_lower:
                in_stock = False
                break

        # カテゴリ判定
        detected_top, detected_parent = detect_category_from_name(name, url)

        ap = ApmexProduct(
            name=name,
            url=url,
            top_category=detected_top,
            parent_category=detected_parent,
            child_category="",
            location="",
            in_stock=in_stock,
            price=price if price and price > 0 else None,
            product_id=product_id,
            fetched_at=timestamp,
        )
        m = _YEAR_RE.match(name)
        if m:
            ap.mint_year = m.group(1)
        if img_urls:
            ap.image_url1 = img_urls[0]
        products.append(ap)

    return products


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
# 商品一覧取得（HTTP直接リクエスト）
# ---------------------------------------------------------------------------
def fetch_product_list(
    category_filter: Optional[str] = None,
    limit: Optional[int] = None,
    use_cache: bool = True,
) -> list[ApmexProduct]:
    """HTTP直接リクエストでAPMEX商品一覧を取得

    カテゴリページにXHR（X-Requested-With: XMLHttpRequest）を送信し、
    JSONレスポンスから商品HTML断片を取得してパースする。

    Args:
        use_cache: True の場合、有効なキャッシュがあればそこから読み込む
    """
    categories = APMEX_CATEGORIES
    if category_filter:
        categories = [c for c in categories if c["name"] == category_filter]
        if not categories:
            logger.error(f"不明なカテゴリ: {category_filter}")
            return []

    all_products: list[ApmexProduct] = []
    session = None  # 必要になったら作成
    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    for cat in categories:
        if limit and len(all_products) >= limit:
            break

        cat_name = cat["name"]
        top_cat = cat["top"]
        parent_cat = cat["parent"]
        logger.info(f"\n=== カテゴリ: {cat_name} ({top_cat} > {parent_cat}) ===")

        # カテゴリ別キャッシュチェック
        if use_cache:
            cached = _load_category_cache(cat_name)
            if cached is not None:
                for p in cached:
                    if limit and len(all_products) >= limit:
                        break
                    all_products.append(p)
                continue

        # キャッシュなし → HTTPで取得
        if session is None:
            session = _create_http_session()

        cat_slug = cat["slug"]
        cat_products: list[ApmexProduct] = []
        page_num = 1
        seen_urls_in_cat: set[str] = set()

        while True:
            if limit and len(all_products) + len(cat_products) >= limit:
                break

            url = f"{BASE_URL}/category/{cat_slug}"
            if page_num > 1:
                url += f"?page={page_num}"

            try:
                # XHRリクエスト（JSONレスポンス）
                xhr_headers = {'X-Requested-With': 'XMLHttpRequest'}
                resp = session.get(url, headers=xhr_headers, timeout=30)

                if resp.status_code != 200:
                    logger.warning(f"  ページ{page_num}: HTTPエラー {resp.status_code}")
                    break

                # Cloudflare 検出
                if 'Just a moment' in resp.text[:500] or 'challenge-platform' in resp.text[:500]:
                    logger.warning(f"  Cloudflare検出、カテゴリスキップ: {cat_name}")
                    break

                # JSONレスポンスからproducts HTMLを取得
                products_html = ""
                try:
                    data = resp.json()
                    products_html = data.get('products', '')
                except (json.JSONDecodeError, ValueError):
                    # JSONでない場合はフルHTMLとして扱う
                    products_html = resp.text

                if not products_html:
                    logger.info(f"  ページ{page_num}: レスポンスが空")
                    break

                page_products = _parse_category_products(products_html, timestamp)
                logger.info(f"  ページ{page_num}: {len(page_products)}件")

                if not page_products:
                    logger.info(f"  商品なし → ページネーション終了")
                    break

                # 重複チェック
                new_in_page = 0
                for ap in page_products:
                    if limit and len(all_products) + len(cat_products) >= limit:
                        break
                    if ap.url in seen_urls_in_cat:
                        continue
                    seen_urls_in_cat.add(ap.url)
                    new_in_page += 1
                    cat_products.append(ap)

                if new_in_page == 0:
                    logger.info(f"  全商品が既出 → ページネーション終了")
                    break

                page_num += 1
                time.sleep(1.5)

            except requests.RequestException as e:
                logger.error(f"  カテゴリ {cat_name} ページ{page_num} でエラー: {e}")
                break

        # カテゴリ完了 → 即キャッシュ保存
        if cat_products and not limit:
            _save_category_cache(cat_name, cat_products)

        all_products.extend(cat_products)
        logger.info(f"  → {cat_name}: {len(cat_products)}件 (累計 {len(all_products)}件)")

    logger.info(f"\n商品一覧取得完了: {len(all_products)}件")
    return all_products


def _save_with_retry(save_callback, products, max_retries=3, wait_seconds=30):
    """save_callbackをリトライ付きで実行する。成功時True、全失敗時False"""
    for attempt in range(max_retries):
        try:
            result = save_callback(products)
            if result is not False:
                return True
        except Exception as e:
            logger.error(f"  保存エラー (リトライ {attempt + 1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
            wait = wait_seconds * (attempt + 1)
            logger.info(f"  {wait}秒後にリトライします...")
            time.sleep(wait)
    return False


# ---------------------------------------------------------------------------
# 詳細ページスクレイピング（HTTP直接リクエスト）
# ---------------------------------------------------------------------------
def fetch_prices_for_products(
    products: list[ApmexProduct],
    limit: Optional[int] = None,
    exchange_type: str = "クレカ",
    save_callback=None,
    batch_size: int = 10,
) -> list[ApmexProduct]:
    """HTTP直接リクエストで詳細ページから価格・画像・スペック等を取得

    Args:
        save_callback: 中間保存用コールバック関数（商品リストを受け取る）
        batch_size: 中間保存の間隔（デフォルト10件）
    """
    session = _create_http_session()

    # 為替レート
    logger.info("為替レートを取得中...")
    rates = fetch_exchange_rates(exchange_type)
    usd_rate = rates.get("USD", 0.0)
    if usd_rate <= 0:
        logger.warning("USD為替レート取得失敗。JPY変換なしで続行します。")

    targets = products[:limit] if limit else products
    logger.info(f"詳細ページ取得対象: {len(targets)}件")
    if save_callback:
        logger.info(f"  → {batch_size}件ごとに中間保存します")

    success_count = 0
    fail_count = 0
    last_saved_index = 0

    for i, product in enumerate(targets):
        logger.info(f"  [{i + 1}/{len(targets)}] {product.name[:50]}")

        try:
            resp = session.get(product.url, timeout=30)
            if resp.status_code == 404:
                logger.warning(f"    404 Not Found")
                fail_count += 1
                continue
            if resp.status_code != 200:
                logger.warning(f"    HTTPエラー: {resp.status_code}")
                fail_count += 1
                continue

            html = resp.text

            if 'Just a moment' in html[:500]:
                logger.warning(f"    Cloudflare検出、スキップ")
                fail_count += 1
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
            success_count += 1

            # 中間保存（batch_size件ごと）
            if save_callback and success_count % batch_size == 0:
                logger.info(f"\n{'='*40}")
                logger.info(f"中間保存: {success_count}/{len(targets)}件完了")
                logger.info(f"{'='*40}")
                batch_products = targets[last_saved_index:i+1]
                saved = _save_with_retry(save_callback, batch_products)
                if saved:
                    last_saved_index = i + 1
                    logger.info(f"中間保存完了: {len(batch_products)}件")
                else:
                    logger.error(f"中間保存失敗: {len(batch_products)}件 → 次回バッチに含めてリトライ")

        except requests.RequestException as e:
            logger.warning(f"    リクエストエラー: {e}")
            fail_count += 1
        except Exception as e:
            logger.warning(f"    詳細取得エラー: {e}")
            fail_count += 1

        time.sleep(1.5)

    # 最終保存（残りの商品）
    if save_callback and last_saved_index < len(targets):
        remaining = targets[last_saved_index:]
        logger.info(f"\n{'='*40}")
        logger.info(f"最終保存: {len(remaining)}件")
        logger.info(f"{'='*40}")
        saved = _save_with_retry(save_callback, remaining)
        if saved:
            logger.info("最終保存完了")
        else:
            logger.error(f"最終保存失敗: {len(remaining)}件が未保存 → 次回実行時に再処理")

    logger.info(f"詳細取得完了: 成功={success_count}件, 失敗={fail_count}件")
    return products


# ---------------------------------------------------------------------------
# 既存シートのカテゴリ修正
# ---------------------------------------------------------------------------
def fix_categories_in_spreadsheet(dry_run: bool = False) -> None:
    """既存シートの I列（最上位カテゴリ）と J列（親カテゴリ）を商品名から再判定して修正する。"""
    client = SpreadsheetClient()
    client.connect()
    sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_PRODUCTS)

    all_data = sheet.get_all_values()
    if len(all_data) <= 1:
        logger.info("データなし")
        return

    rows = all_data[1:]  # ヘッダースキップ
    logger.info(f"既存データ: {len(rows)}件")

    # 修正が必要な行を検出
    updates_i = []  # I列（最上位カテゴリ）の更新
    updates_j = []  # J列（親カテゴリ）の更新
    fix_count = 0

    for idx, row in enumerate(rows):
        row_num = idx + 2  # シート上の行番号（1-based, ヘッダー分+1）
        name = get_cell(row, Col.PRODUCT_NAME) or ""
        url = get_cell(row, Col.PRODUCT_URL) or ""
        current_top = get_cell(row, Col.TOP_CATEGORY) or ""
        current_parent = get_cell(row, Col.PARENT_CATEGORY) or ""

        if not name:
            continue

        detected_top, detected_parent = detect_category_from_name(name, url)

        if detected_top != current_top or detected_parent != current_parent:
            fix_count += 1
            if fix_count <= 20:  # 最初の20件をログ表示
                logger.info(f"  修正: {name[:50]}")
                logger.info(f"    {current_top}/{current_parent} → {detected_top}/{detected_parent}")

            # I列 = Col.TOP_CATEGORY, J列 = Col.PARENT_CATEGORY
            i_col_letter = chr(ord('A') + Col.TOP_CATEGORY.index)
            j_col_letter = chr(ord('A') + Col.PARENT_CATEGORY.index)
            updates_i.append({'range': f'{i_col_letter}{row_num}', 'values': [[detected_top]]})
            updates_j.append({'range': f'{j_col_letter}{row_num}', 'values': [[detected_parent]]})

    if fix_count > 20:
        logger.info(f"  ... 他 {fix_count - 20}件")

    logger.info(f"\n修正対象: {fix_count}件 / {len(rows)}件")

    if fix_count == 0:
        logger.info("修正不要")
        return

    if dry_run:
        logger.info("[ドライラン] シート更新をスキップ")
        return

    # バッチ更新
    all_updates = updates_i + updates_j
    logger.info(f"シート更新中... ({len(all_updates)}セル)")
    sheet.batch_update(all_updates)
    logger.info("カテゴリ修正完了")


# ---------------------------------------------------------------------------
# 既存URL取得（差分チェック用）
# ---------------------------------------------------------------------------
def get_existing_urls_from_spreadsheet(price_fetched_only: bool = False) -> set[str]:
    """スプレッドシートから既存商品のURLを取得（差分チェック用）

    Args:
        price_fetched_only: Trueの場合、価格取得済み（R列に値あり）のURLのみ返す
    """
    try:
        client = SpreadsheetClient()
        client.connect()
        sheet = client._spreadsheet.worksheet(Config.SHEET_APMEX_PRODUCTS)

        if price_fetched_only:
            # F列（URL）とR列（価格）を両方取得し、価格ありのURLのみ返す
            all_data = sheet.get_all_values()
            existing_urls = set()
            for row in all_data[1:]:  # ヘッダースキップ
                url_val = get_cell(row, Col.PRODUCT_URL)
                price_val = get_cell(row, Col.PRICE)
                if url_val and price_val:
                    existing_urls.add(url_val)
            logger.info(f"価格取得済み商品URL: {len(existing_urls)}件")
            return existing_urls
        else:
            url_column = sheet.col_values(Col.PRODUCT_URL.index + 1)  # F列（1-based）
            existing_urls = set(url_column[1:])  # ヘッダー行をスキップ
            logger.info(f"既存商品URL: {len(existing_urls)}件")
            return existing_urls
    except Exception as e:
        logger.warning(f"既存URL取得エラー: {e}")
        return set()


# ---------------------------------------------------------------------------
# スプレッドシート保存（84列）
# ---------------------------------------------------------------------------
def save_products_to_spreadsheet(products: list[ApmexProduct], dry_run: bool = False, skip_ai: bool = False) -> bool:
    """APMEX商品ページ一覧シートに84列構造で保存

    Args:
        skip_ai: Trueの場合、AI生成（日本語名・カテゴリ・説明・SEO・型番）をスキップ
    """

    if dry_run:
        logger.info("[ドライラン] スプレッドシートへの保存をスキップ")
        for p in products:
            logger.info(f"  {p.name[:50]} | {p.url[:60]} | ${p.price or 0}")
        return True

    client = SpreadsheetClient()
    if not client.connect():
        logger.error("スプレッドシートへの接続に失敗しました")
        return False

    # AI生成器を初期化（skip_ai時はスキップ）
    name_generator = None
    description_generator = None
    seo_generator = None
    model_number_generator = None
    category_detector = None
    category_name_map = {
        2977963: "ゴールド（金）",
        2977964: "シルバー（銀）",
        2977965: "プラチナ",
        2977966: "パラジウム",
        2977967: "カッパー（銅）",
    }
    group_name_map = {}

    if not skip_ai:
        name_generator = JapaneseProductNameGenerator()
        description_generator = DescriptionGenerator()
        seo_generator = SEOGenerator()

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
    else:
        logger.info("AI生成スキップモード（基本情報のみ保存）")

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
        if not skip_ai:
            existing_model_numbers = set()
            for row in existing_data[1:]:
                mn = get_cell(row, Col.CM_MODEL_NUMBER)
                if mn:
                    existing_model_numbers.add(mn)
            model_number_generator = ModelNumberGenerator(existing_model_numbers)

        # 新規行を構築
        new_rows = []
        skipped_count = 0
        BATCH_SAVE_INTERVAL = 200

        def save_batch(new_rows_batch, start_row_offset):
            """バッチ保存（接続切れ・429エラー時リトライ付き）"""
            nonlocal sheet
            if not new_rows_batch:
                return 0
            start_row = len(existing_data) + start_row_offset + 1
            for k, row in enumerate(new_rows_batch):
                Formula.set_all_formulas(row, start_row + k)
            end_row = start_row + len(new_rows_batch) - 1
            range_str = f"A{start_row}:{Col.last_column_letter()}{end_row}"
            logger.info(f"  保存中: {range_str} ({len(new_rows_batch)}件)")

            for attempt in range(3):
                try:
                    # 書き込み前にシート接続をリフレッシュ（長時間AI生成後の接続切れ対策）
                    fresh_client = SpreadsheetClient()
                    fresh_client.connect()
                    sheet = fresh_client._spreadsheet.worksheet(sheet_name)

                    result = sheet.update(values=new_rows_batch, range_name=range_str, value_input_option='USER_ENTERED')
                    logger.info(f"  保存完了: {result.get('updatedCells', 0)}セル")
                    break
                except Exception as e:
                    wait = 30 * (attempt + 1)
                    logger.warning(f"  保存エラー ({attempt + 1}/3): {e}")
                    if attempt < 2:
                        logger.info(f"  {wait}秒後にリトライ...")
                        time.sleep(wait)
                    else:
                        raise

            # データ検証（A列・B列にドロップダウン）- skip_ai時はスキップ
            if not skip_ai:
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
            if name_generator:
                try:
                    info = {"name": product.name, "specs": product.specs, "description": product.description_en}
                    cm_product_name = _run_with_timeout(
                        name_generator.generate, info, quantity=1, default=""
                    ) or ""
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
                    result = _run_with_timeout(
                        category_detector.detect, product.name, product.url,
                        default=(None, None, [])
                    )
                    if result:
                        cat_big, cat_small, group_ids = result
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
            if description_generator and description_generator.genai_model:
                try:
                    price_jpy = int(product.price_jpy) if product.price_jpy else 0
                    desc_info = {
                        "name": product.name,
                        "price": price_jpy,
                        "currency": "JPY",
                        "description": product.description_en or "",
                        "specs": product.specs or "",
                    }
                    result = _run_with_timeout(
                        description_generator.generate, desc_info,
                        default=("", "")
                    )
                    if result:
                        cm_description, cm_simple_description = result
                except Exception as e:
                    logger.debug(f"  商品説明生成エラー: {e}")

            # --- AI生成: SEO ---
            page_title = ""
            meta_description = ""
            meta_keywords = ""
            if seo_generator and seo_generator.genai_model:
                try:
                    seo_info = {
                        "name": product.name,
                        "price": int(product.price_jpy) if product.price_jpy else 0,
                        "description": product.description_en or "",
                        "specs": product.specs or "",
                    }
                    result = _run_with_timeout(
                        seo_generator.generate, seo_info,
                        default=("", "", "")
                    )
                    if result:
                        page_title, meta_description, meta_keywords = result
                except Exception as e:
                    logger.debug(f"  SEO生成エラー: {e}")

            # --- AI生成: 型番 ---
            model_number = ""
            if model_number_generator and model_number_generator.genai_model:
                try:
                    model_info = {"name": product.name, "specs": product.specs or "", "description": product.description_en or ""}
                    model_number = _run_with_timeout(
                        model_number_generator.generate, model_info,
                        quantity=1, supplier_site="APMEX", default=""
                    ) or ""
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
    parser.add_argument("--fix-categories", action="store_true", help="既存シートのカテゴリを商品名から再判定して修正")
    parser.add_argument("--no-cache", action="store_true", help="キャッシュを使わず商品一覧を再取得")
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

    # --fix-categories: 既存シートのカテゴリ修正モード
    if args.fix_categories:
        logger.info("=== カテゴリ修正モード ===")
        fix_categories_in_spreadsheet(dry_run=args.dry_run)
        return 0

    use_cache = not args.no_cache

    logger.info("=== APMEX 商品ページ一覧取得 ===")
    logger.info(f"カテゴリ: {category or '全カテゴリ'}")
    logger.info(f"件数制限: {limit or '無制限'}")
    logger.info(f"詳細取得: {'あり' if args.fetch_prices else 'なし'}")
    logger.info(f"キャッシュ: {'使用' if use_cache else '無効（再取得）'}")
    logger.info(f"ドライラン: {'はい' if args.dry_run else 'いいえ'}")

    # 商品一覧取得
    products = fetch_product_list(
        category_filter=category, limit=limit, use_cache=use_cache
    )
    if not products:
        logger.warning("取得商品なし")
        return 0

    logger.info(f"商品一覧: {len(products)}件取得")

    # 詳細ページ取得
    if args.fetch_prices:
        logger.info("\n=== 詳細ページ取得開始 ===")

        # 既存商品URLを取得して差分チェック（価格取得済みのみスキップ）
        if not args.dry_run:
            existing_urls = get_existing_urls_from_spreadsheet(price_fetched_only=True)
            new_products = [p for p in products if p.url not in existing_urls]
            skipped_count = len(products) - len(new_products)
            if skipped_count > 0:
                logger.info(f"既存商品をスキップ: {skipped_count}件")
                logger.info(f"スクレイピング対象: {len(new_products)}件（新規のみ）")
            products = new_products

            if not products:
                logger.info("新規商品はありません。終了します。")
                return 0

        # 中間保存用コールバック（ドライランでない場合のみ）
        save_callback = None
        if not args.dry_run:
            def save_callback(processed_products):
                """スクレイピング中間保存用コールバック"""
                return save_products_to_spreadsheet(processed_products)

        products = fetch_prices_for_products(
            products, limit=limit, exchange_type=args.exchange_type,
            save_callback=save_callback, batch_size=10,
        )

        # fetch_pricesモードでは中間保存で既に保存済み
        if not args.dry_run:
            logger.info("スプレッドシートへの保存完了（中間保存済み）")
        return 0

    # 詳細なしの場合は一覧のみ保存（AI生成スキップで高速保存）
    logger.info("\n=== スプレッドシート保存（基本情報のみ） ===")
    success = save_products_to_spreadsheet(products, dry_run=args.dry_run, skip_ai=True)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
