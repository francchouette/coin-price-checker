"""
スクレイピング動作確認スクリプト
実際のページにアクセスして価格取得をテストする
"""

import sys
sys.path.insert(0, '/Users/abehiroshi/cursor/coin-price-check-script')

from src.scraper import ScraperManager, ScrapeTarget

# テスト用URL
TEST_URLS = [
    # Bullionstar (在庫あり商品)
    ScrapeTarget(
        shop_name="Bullionstar",
        url="https://www.bullionstar.com/buy/product/gold-pamp-5g",
        product_name_hint="Gold PAMP 5g"
    ),
    # APMEX
    ScrapeTarget(
        shop_name="APMEX",
        url="https://www.apmex.com/product/299042/2025-1-oz-american-silver-eagle-coin-bu",
        product_name_hint="Silver Eagle 1oz"
    ),
]


def main():
    print("=" * 60)
    print("スクレイピング動作確認テスト")
    print("=" * 60)

    with ScraperManager() as manager:
        for target in TEST_URLS:
            print(f"\n--- {target.shop_name} ---")
            print(f"URL: {target.url}")

            result = manager.scrape(target)

            if result.error:
                print(f"エラー: {result.error}")
            else:
                print(f"商品名: {result.product_name}")
                print(f"価格: {result.currency} {result.price:,.2f}")
                print(f"在庫: {'あり' if result.in_stock else 'なし'}")

    print("\n" + "=" * 60)
    print("テスト完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
