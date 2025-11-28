"""
ページ構造調査スクリプト v4
Bullionstarの正確な価格セレクタ特定
"""

from playwright.sync_api import sync_playwright
import re

def check_bullionstar_detailed(page):
    """Bullionstarの価格セレクタを詳細に特定"""
    print("\n" + "="*60)
    print("BULLIONSTAR 価格セレクタ詳細特定")
    print("="*60)

    url = "https://www.bullionstar.com/buy/product/gold-pamp-5g"
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    # メインの価格表示エリアを特定
    print("\n【.default エリアの調査】")
    default_area = page.query_selector(".default")
    if default_area:
        # default内のすべてのテキストを取得
        text = default_area.inner_text()
        print(f"  .default 内容:\n{text[:500]}")

    # .buy-price を詳細調査
    print("\n【.buy-price 調査】")
    buy_prices = page.query_selector_all(".buy-price")
    for i, el in enumerate(buy_prices):
        text = el.inner_text().strip()
        html = el.inner_html()
        visible = el.is_visible()
        print(f"  [{i}] visible={visible}, text='{text[:100]}', html='{html[:200]}'")

    # unit-price調査
    print("\n【.unit-price 調査】")
    unit_prices = page.query_selector_all(".unit-price")
    for i, el in enumerate(unit_prices):
        text = el.inner_text().strip()
        html = el.inner_html()
        visible = el.is_visible()
        parent_class = ""
        parent = el.evaluate("el => el.parentElement?.className")
        print(f"  [{i}] visible={visible}, parent='{parent}', text='{text[:100]}'")

    # span要素で価格を含むもの
    print("\n【価格を含むspan要素】")
    spans = page.query_selector_all("span")
    for span in spans:
        text = span.inner_text().strip()
        if text and (text.startswith("¥") or text.startswith("$") or text.startswith("S$")):
            class_name = span.get_attribute("class") or ""
            visible = span.is_visible()
            if visible:
                print(f"  class='{class_name}', text='{text}'")

    # テーブル内の価格
    print("\n【info テーブル調査】")
    info_table = page.query_selector(".info")
    if info_table:
        rows = info_table.query_selector_all("tr")
        for i, row in enumerate(rows):
            text = row.inner_text().strip().replace("\n", " | ")
            print(f"  row[{i}]: {text[:100]}")


def check_bullionstar_usd(page):
    """BullionstarをUSD表示で確認"""
    print("\n" + "="*60)
    print("BULLIONSTAR USD表示での価格取得")
    print("="*60)

    url = "https://www.bullionstar.com/buy/product/gold-pamp-5g"
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    # 通貨切り替え
    print("\n【通貨をUSDに切り替え】")
    try:
        # まずcurrency selectorを探す
        usd_button = page.query_selector("#header-usd, [data-currency='USD'], input[value='USD']")
        if usd_button:
            usd_button.click()
            page.wait_for_timeout(2000)
            print("  USDに切り替えました")
        else:
            # URLパラメータで変更
            page.goto(url + "?currency=USD", wait_until="networkidle")
            page.wait_for_timeout(2000)
            print("  URLパラメータでUSD指定")
    except Exception as e:
        print(f"  切り替えエラー: {e}")

    # 再度価格を取得
    print("\n【USD切り替え後の価格】")
    spans = page.query_selector_all("span")
    for span in spans:
        text = span.inner_text().strip()
        if text and text.startswith("$"):
            class_name = span.get_attribute("class") or ""
            visible = span.is_visible()
            if visible and len(text) > 2:
                print(f"  class='{class_name}', text='{text}'")


def check_apmex_detailed(page):
    """APMEXの価格セレクタを詳細に確認"""
    print("\n" + "="*60)
    print("APMEX 価格セレクタ詳細確認")
    print("="*60)

    url = "https://www.apmex.com/product/299042/2025-1-oz-american-silver-eagle-coin-bu"
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(8000)

    # 商品名
    h1 = page.query_selector("h1")
    print(f"\n【商品名】: {h1.inner_text().strip() if h1 else 'N/A'}")

    # .mod-product-pricing の詳細
    print("\n【.mod-product-pricing 詳細】")
    pricing = page.query_selector(".mod-product-pricing")
    if pricing:
        text = pricing.inner_text().strip()
        html = pricing.inner_html()
        print(f"  text: {text}")
        print(f"  html: {html[:300]}")

    # 価格テーブルを調査
    print("\n【価格テーブル（数量別）】")
    qty_selectors = [
        ".pricing-table",
        "table",
        "[class*='qty']",
        "[class*='quantity']",
    ]
    for selector in qty_selectors:
        elements = page.query_selector_all(selector)
        for i, el in enumerate(elements[:2]):
            text = el.inner_text().strip()[:200]
            if "$" in text:
                print(f"  {selector}[{i}]: {text}")


def main():
    print("価格セレクタ詳細特定調査を開始します...")

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

        check_bullionstar_detailed(page)
        check_bullionstar_usd(page)
        check_apmex_detailed(page)

        browser.close()

    print("\n" + "="*60)
    print("調査完了")
    print("="*60)


if __name__ == "__main__":
    main()
