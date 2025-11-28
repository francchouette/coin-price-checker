"""
ページ構造調査スクリプト v2
より詳細な調査を行う
"""

from playwright.sync_api import sync_playwright
import re

def check_bullionstar_detail(page):
    """Bullionstarの詳細調査"""
    print("\n" + "="*60)
    print("BULLIONSTAR 詳細構造調査")
    print("="*60)

    url = "https://www.bullionstar.com/buy/product/silver-coin-canadian-maple-1oz-2025"
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    # 商品名
    print("\n【商品名】")
    h1 = page.query_selector("h1")
    if h1:
        print(f"  セレクタ: h1")
        print(f"  値: {h1.inner_text().strip()}")

    # 価格関連のクラスをすべて調査
    print("\n【価格関連要素の詳細調査】")

    # ページ全体のHTMLを取得して価格パターンを検索
    html_content = page.content()

    # USD価格パターンを検索 ($XX.XX形式)
    usd_pattern = r'\$[\d,]+\.?\d*'
    usd_matches = re.findall(usd_pattern, html_content)
    unique_prices = list(set(usd_matches))[:10]
    print(f"  ページ内のUSD価格パターン: {unique_prices}")

    # 特定のセレクタを詳細に調査
    detailed_selectors = [
        ".product-default-wrap",
        ".product-price-update",
        "[data-product-id]",
        ".buy-price",
        ".unit-price",
        ".price-value",
        ".product-price",
        "span[class*='price']",
        "div[class*='price']",
        ".sgd",  # SGD価格
        ".eur",  # EUR価格
    ]

    for selector in detailed_selectors:
        elements = page.query_selector_all(selector)
        if elements:
            print(f"\n  {selector}: {len(elements)}件")
            for i, el in enumerate(elements[:3]):
                text = el.inner_text().strip()[:200]
                classes = el.get_attribute("class") or ""
                data_attrs = {
                    k: el.get_attribute(k)
                    for k in ["data-product-id", "data-price", "status"]
                    if el.get_attribute(k)
                }
                if text or data_attrs:
                    print(f"    [{i}] class='{classes}' data={data_attrs}")
                    if text:
                        print(f"        text='{text[:100]}...'")

    # 在庫状態を確認
    print("\n【在庫状態】")
    unavailable = page.query_selector("[status='UNAVAILABLE']")
    if unavailable:
        print("  ⚠️ 商品はUNAVAILABLE状態です")
    else:
        available = page.query_selector("[status='AVAILABLE']")
        if available:
            print("  ✓ 商品はAVAILABLE状態です")

    # JavaScript実行して価格を直接取得を試みる
    print("\n【JavaScript経由での価格取得】")
    try:
        # ページ内のproduct dataを探す
        product_data = page.evaluate("""() => {
            // グローバル変数を探す
            if (typeof productData !== 'undefined') return productData;
            if (typeof product !== 'undefined') return product;

            // data属性から取得
            const el = document.querySelector('[data-product-id]');
            if (el) {
                return {
                    productId: el.getAttribute('data-product-id'),
                    status: el.getAttribute('status')
                };
            }
            return null;
        }""")
        print(f"  Product Data: {product_data}")
    except Exception as e:
        print(f"  エラー: {e}")


def check_apmex_detail(page):
    """APMEXの詳細調査"""
    print("\n" + "="*60)
    print("APMEX 詳細構造調査")
    print("="*60)

    url = "https://www.apmex.com/product/299042/2025-1-oz-american-silver-eagle-coin-bu"
    print(f"\nアクセス中: {url}")

    try:
        # domcontentloadedで待機（networkidleより早い）
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)  # 長めに待機

        print(f"  ページタイトル: {page.title()}")

        # 商品名
        print("\n【商品名】")
        h1 = page.query_selector("h1")
        if h1:
            print(f"  セレクタ: h1")
            print(f"  値: {h1.inner_text().strip()}")

        # 価格関連の調査
        print("\n【価格関連要素】")
        html_content = page.content()

        # USD価格パターンを検索
        usd_pattern = r'\$[\d,]+\.?\d*'
        usd_matches = re.findall(usd_pattern, html_content)
        unique_prices = list(set(usd_matches))[:15]
        print(f"  ページ内のUSD価格: {unique_prices}")

        # セレクタ調査
        selectors = [
            "h1",
            "[class*='price']",
            "[class*='Price']",
            ".product-price",
            ".buy-box",
            "[data-price]",
            "[data-product]",
            ".price-box",
            ".amount",
        ]

        for selector in selectors:
            elements = page.query_selector_all(selector)
            if elements:
                text = elements[0].inner_text().strip()[:100]
                print(f"  {selector}: {len(elements)}件, 例='{text}'")

    except Exception as e:
        print(f"  エラー: {e}")


def check_bullionstar_available_product(page):
    """在庫のある商品で調査"""
    print("\n" + "="*60)
    print("BULLIONSTAR 在庫商品調査 (Gold PAMP 1g)")
    print("="*60)

    url = "https://www.bullionstar.com/buy/product/gold-pamp-1-g"
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    # 商品名
    h1 = page.query_selector("h1")
    print(f"\n【商品名】: {h1.inner_text().strip() if h1 else 'N/A'}")

    # 在庫状態
    status_el = page.query_selector("[data-product-id]")
    if status_el:
        status = status_el.get_attribute("status")
        print(f"【在庫状態】: {status}")

    # 価格要素を詳細に調査
    print("\n【価格要素の詳細】")

    # ページ内のすべてのテキストから価格を探す
    html = page.content()

    # SGD/USD/EUR価格クラスを探す
    for currency in ["sgd", "usd", "eur"]:
        elements = page.query_selector_all(f".{currency}")
        if elements:
            print(f"\n  .{currency} クラス: {len(elements)}件")
            for i, el in enumerate(elements[:5]):
                text = el.inner_text().strip()
                visible = el.is_visible()
                parent_class = ""
                parent = el.query_selector("xpath=..")
                if parent:
                    parent_class = parent.get_attribute("class") or ""
                print(f"    [{i}] text='{text}', visible={visible}, parent='{parent_class[:50]}'")

    # buy-price / unit-price
    print("\n【buy-price / unit-price】")
    for selector in [".buy-price", ".unit-price", ".price-value"]:
        elements = page.query_selector_all(selector)
        if elements:
            print(f"  {selector}: {len(elements)}件")
            for i, el in enumerate(elements[:3]):
                text = el.inner_text().strip()
                print(f"    [{i}] '{text}'")


def main():
    print("詳細ページ構造調査を開始します...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        page = context.new_page()

        # Bullionstar詳細
        check_bullionstar_detail(page)

        # Bullionstar在庫商品
        check_bullionstar_available_product(page)

        # APMEX詳細
        check_apmex_detail(page)

        browser.close()

    print("\n" + "="*60)
    print("調査完了")
    print("="*60)


if __name__ == "__main__":
    main()
