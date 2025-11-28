"""
ページ構造調査スクリプト
BullionstarとAPMEXのページ構造を確認する
"""

from playwright.sync_api import sync_playwright
import json

URLS = {
    "bullionstar": "https://www.bullionstar.com/buy/product/silver-coin-canadian-maple-1oz-2025",
    "apmex": "https://www.apmex.com/product/299042/2025-1-oz-american-silver-eagle-coin-bu"
}

def check_bullionstar(page):
    """Bullionstarのページ構造を確認"""
    print("\n" + "="*60)
    print("BULLIONSTAR 構造調査")
    print("="*60)

    url = URLS["bullionstar"]
    print(f"\nアクセス中: {url}")

    page.goto(url, wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(3000)  # 追加待機

    # 商品名を取得
    print("\n【商品名】")
    h1_elements = page.query_selector_all("h1")
    for i, el in enumerate(h1_elements):
        text = el.inner_text().strip()
        if text:
            print(f"  h1[{i}]: {text}")

    # 価格関連の要素を調査
    print("\n【価格要素】")

    # .price クラス
    price_elements = page.query_selector_all(".price")
    print(f"  .price 要素数: {len(price_elements)}")

    # USD価格
    usd_elements = page.query_selector_all(".usd")
    print(f"  .usd 要素数: {len(usd_elements)}")
    for i, el in enumerate(usd_elements[:5]):  # 最初の5件
        text = el.inner_text().strip()
        visible = el.is_visible()
        print(f"    [{i}] text='{text}', visible={visible}")

    # 商品価格の特定を試みる
    print("\n【価格セレクタ候補の検証】")

    selectors_to_try = [
        "h1",
        ".product-name",
        ".product-title",
        "[class*='price']",
        ".usd:not(.hide)",
        ".price .usd",
        ".product-price",
        ".buy-price",
        "[data-price]",
        ".as-low-as .usd",
    ]

    for selector in selectors_to_try:
        try:
            elements = page.query_selector_all(selector)
            if elements:
                first_text = elements[0].inner_text().strip()[:100]
                print(f"  {selector}: {len(elements)}件, 例='{first_text}'")
        except Exception as e:
            print(f"  {selector}: エラー - {e}")

    # HTMLスニペットを取得
    print("\n【HTML構造サンプル】")
    try:
        # メイン価格エリアのHTML
        price_area = page.query_selector(".product-details, .product-info, [class*='price-container']")
        if price_area:
            html = price_area.inner_html()[:500]
            print(f"  価格エリア: {html}...")
    except:
        pass

    return {
        "site": "bullionstar",
        "product_name_selector": "h1",
        "price_selector": ".usd"  # 要調整
    }


def check_apmex(page):
    """APMEXのページ構造を確認"""
    print("\n" + "="*60)
    print("APMEX 構造調査")
    print("="*60)

    url = URLS["apmex"]
    print(f"\nアクセス中: {url}")

    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)  # Bot検出回避のため長めに待機

        # ページタイトルを確認
        title = page.title()
        print(f"  ページタイトル: {title}")

        # 403エラーチェック
        content = page.content()
        if "403" in content or "Access Denied" in content:
            print("  ⚠️  アクセス拒否されました（403）")
            return None

        # 商品名を取得
        print("\n【商品名】")
        h1_elements = page.query_selector_all("h1")
        for i, el in enumerate(h1_elements):
            text = el.inner_text().strip()
            if text:
                print(f"  h1[{i}]: {text}")

        # 価格関連の要素を調査
        print("\n【価格要素】")

        selectors_to_try = [
            "h1",
            ".product-name",
            ".product-title",
            "[class*='price']",
            "[class*='Price']",
            ".price",
            ".product-price",
            "[data-price]",
            ".buy-box",
            ".purchase-price",
        ]

        for selector in selectors_to_try:
            try:
                elements = page.query_selector_all(selector)
                if elements:
                    first_text = elements[0].inner_text().strip()[:100]
                    print(f"  {selector}: {len(elements)}件, 例='{first_text}'")
            except Exception as e:
                print(f"  {selector}: エラー - {e}")

        return {
            "site": "apmex",
            "product_name_selector": "h1",
            "price_selector": "TBD"
        }

    except Exception as e:
        print(f"  エラー: {e}")
        return None


def main():
    print("ページ構造調査を開始します...")

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

        # Bullionstar
        bullionstar_result = check_bullionstar(page)

        # APMEX
        apmex_result = check_apmex(page)

        browser.close()

    print("\n" + "="*60)
    print("調査完了")
    print("="*60)


if __name__ == "__main__":
    main()
