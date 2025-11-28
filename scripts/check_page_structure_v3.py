"""
ページ構造調査スクリプト v3
価格セレクタの最終特定
"""

from playwright.sync_api import sync_playwright
import re

def check_bullionstar_in_stock(page):
    """Bullionstarの在庫商品で価格セレクタを特定"""
    print("\n" + "="*60)
    print("BULLIONSTAR 在庫商品の価格セレクタ特定")
    print("="*60)

    # 在庫のある商品
    url = "https://www.bullionstar.com/buy/product/gold-pamp-5g"
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    # 商品名
    h1 = page.query_selector("h1")
    print(f"\n【商品名】: {h1.inner_text().strip() if h1 else 'N/A'}")

    # メイン商品の在庫状態
    main_product = page.query_selector(".product-default-wrap.product-price-update")
    if main_product:
        status = main_product.get_attribute("status")
        product_id = main_product.get_attribute("data-product-id")
        print(f"【在庫状態】: {status}")
        print(f"【商品ID】: {product_id}")

    # 価格エリアの詳細調査
    print("\n【価格エリアのHTML構造】")

    # .prices クラス内を調査
    prices_div = page.query_selector(".prices")
    if prices_div:
        html = prices_div.inner_html()
        # 構造を整形して表示
        print("  .prices 内の構造:")
        print(f"    {html[:1000]}...")

    # 各通貨クラスを調査
    print("\n【通貨別価格要素】")
    for currency in ["jpy", "sgd", "usd", "eur"]:
        elements = page.query_selector_all(f".{currency}")
        visible_elements = [el for el in elements if el.is_visible()]
        if visible_elements:
            text = visible_elements[0].inner_text().strip()
            print(f"  .{currency} (visible): {len(visible_elements)}件, 値='{text}'")

    # .buy-price, .as-low-as, .price-new などを調査
    print("\n【価格セレクタ候補】")
    price_selectors = [
        ".buy-price",
        ".as-low-as",
        ".price-new",
        ".price-old",
        ".unit-price",
        ".price-value",
        ".prices .jpy",
        ".prices .sgd",
        ".prices .usd",
        ".product-default-wrap .jpy",
        ".product-default-wrap .sgd",
        ".product-default-wrap .usd",
    ]

    for selector in price_selectors:
        elements = page.query_selector_all(selector)
        if elements:
            visible = [el for el in elements if el.is_visible()]
            if visible:
                text = visible[0].inner_text().strip()
                print(f"  {selector}: visible={len(visible)}件, 値='{text}'")

    # JavaScript経由で価格データを取得
    print("\n【JavaScript経由での価格取得】")
    try:
        price_info = page.evaluate("""() => {
            const priceEl = document.querySelector('.prices');
            if (!priceEl) return null;

            // 表示されている通貨を特定
            const currencies = ['jpy', 'sgd', 'usd', 'eur'];
            for (const curr of currencies) {
                const el = priceEl.querySelector('.' + curr + ':not(.hide)');
                if (el && el.offsetParent !== null) {
                    return {
                        currency: curr,
                        text: el.innerText.trim(),
                        selector: '.prices .' + curr
                    };
                }
            }
            return null;
        }""")
        print(f"  検出された価格: {price_info}")
    except Exception as e:
        print(f"  エラー: {e}")


def check_apmex_price(page):
    """APMEXの価格セレクタを特定"""
    print("\n" + "="*60)
    print("APMEX 価格セレクタ特定")
    print("="*60)

    url = "https://www.apmex.com/product/299042/2025-1-oz-american-silver-eagle-coin-bu"
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)

    # 商品名
    h1 = page.query_selector("h1")
    print(f"\n【商品名】: {h1.inner_text().strip() if h1 else 'N/A'}")

    # 価格セレクタ候補を調査
    print("\n【価格セレクタ候補】")

    selectors = [
        ".price",
        ".product-price",
        ".buy-price",
        "[class*='Price']",
        "[class*='price']",
        ".mod-product-pricing",
        ".pricing",
        ".cost",
        ".amount",
        "span[data-price]",
        "[data-qa*='price']",
    ]

    for selector in selectors:
        elements = page.query_selector_all(selector)
        if elements:
            visible = [el for el in elements if el.is_visible()]
            if visible:
                text = visible[0].inner_text().strip()[:100]
                classes = visible[0].get_attribute("class") or ""
                print(f"  {selector}: {len(visible)}件, class='{classes[:50]}', text='{text}'")

    # buy boxエリアを調査
    print("\n【Buy Box エリア】")
    buy_box = page.query_selector(".product-buy-box, .buy-box, [class*='buy']")
    if buy_box:
        text = buy_box.inner_text().strip()[:500]
        print(f"  内容: {text}")

    # 価格テーブルを調査
    print("\n【価格テーブル】")
    tables = page.query_selector_all("table")
    for i, table in enumerate(tables[:3]):
        text = table.inner_text().strip()[:200]
        if "$" in text:
            print(f"  table[{i}]: {text}")

    # JavaScript経由でproduct dataを取得
    print("\n【JavaScript経由でのデータ取得】")
    try:
        product_data = page.evaluate("""() => {
            // window.__INITIAL_STATE__ などのグローバル変数を探す
            const keys = Object.keys(window).filter(k =>
                k.includes('product') || k.includes('INITIAL') || k.includes('DATA')
            );
            return keys.slice(0, 10);
        }""")
        print(f"  グローバル変数候補: {product_data}")
    except Exception as e:
        print(f"  エラー: {e}")


def check_bullionstar_final(page):
    """Bullionstar最終確認 - 複数商品で検証"""
    print("\n" + "="*60)
    print("BULLIONSTAR 複数商品での価格取得検証")
    print("="*60)

    test_urls = [
        ("Gold PAMP 5g", "https://www.bullionstar.com/buy/product/gold-pamp-5g"),
        ("Silver Nadir 1000g", "https://www.bullionstar.com/buy/product/nadir1000g"),
        ("Platinum Britannia", "https://www.bullionstar.com/buy/product/platinum-coin-uk-britannia-1oz-2025"),
    ]

    for name, url in test_urls:
        print(f"\n--- {name} ---")
        print(f"URL: {url}")

        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        # 商品名
        h1 = page.query_selector("h1")
        product_name = h1.inner_text().strip() if h1 else "N/A"
        print(f"商品名: {product_name}")

        # 在庫状態
        main_product = page.query_selector(".product-default-wrap.product-price-update")
        status = main_product.get_attribute("status") if main_product else "N/A"
        print(f"在庫: {status}")

        # 価格取得 - 複数のセレクタを試行
        price = None
        used_selector = None

        # 方法1: .as-low-as 内の通貨
        for curr in ["jpy", "sgd", "usd"]:
            selector = f".as-low-as .{curr}"
            el = page.query_selector(selector)
            if el and el.is_visible():
                price = el.inner_text().strip()
                used_selector = selector
                break

        # 方法2: .prices 内の通貨
        if not price:
            for curr in ["jpy", "sgd", "usd"]:
                selector = f".prices .{curr}"
                elements = page.query_selector_all(selector)
                visible = [el for el in elements if el.is_visible()]
                if visible:
                    price = visible[0].inner_text().strip()
                    used_selector = selector
                    break

        # 方法3: .buy-price
        if not price:
            el = page.query_selector(".buy-price")
            if el and el.is_visible():
                price = el.inner_text().strip()
                used_selector = ".buy-price"

        print(f"価格: {price}")
        print(f"セレクタ: {used_selector}")


def main():
    print("価格セレクタ最終特定調査を開始します...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        page = context.new_page()

        check_bullionstar_in_stock(page)
        check_bullionstar_final(page)
        check_apmex_price(page)

        browser.close()

    print("\n" + "="*60)
    print("調査完了")
    print("="*60)


if __name__ == "__main__":
    main()
