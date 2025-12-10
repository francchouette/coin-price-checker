"""
スクレイパーのテスト
"""

import pytest
from unittest.mock import MagicMock, patch

from src.shops.base import BaseScraper, ScrapedData
from src.shops.bullionstar import BullionstarScraper
from src.shops.apmex import ApmexScraper
from src.scraper import detect_shop_from_url


class TestDetectShopFromUrl:
    """URLからショップ名を推定するテスト"""

    def test_bullionstar_url(self):
        url = "https://www.bullionstar.com/buy/product/gold-pamp-5g"
        assert detect_shop_from_url(url) == "Bullionstar"

    def test_apmex_url(self):
        url = "https://www.apmex.com/product/299042/2025-1-oz-american-silver-eagle-coin-bu"
        assert detect_shop_from_url(url) == "APMEX"

    def test_unknown_url(self):
        url = "https://www.example.com/product/123"
        assert detect_shop_from_url(url) == ""


class TestBaseScraper:
    """基底スクレイパーのテスト"""

    def test_parse_price_with_yen(self):
        result = BaseScraper.parse_price("¥126,112.25", "¥")
        assert result == 126112.25

    def test_parse_price_with_dollar(self):
        result = BaseScraper.parse_price("$60.35", "$")
        assert result == 60.35

    def test_parse_price_with_comma(self):
        result = BaseScraper.parse_price("$1,234.56", "$")
        assert result == 1234.56

    def test_parse_price_no_decimal(self):
        result = BaseScraper.parse_price("¥126,112", "¥")
        assert result == 126112.0

    def test_parse_price_invalid(self):
        result = BaseScraper.parse_price("Not a price", "¥")
        assert result is None


class TestBullionstarScraper:
    """Bullionstarスクレイパーのテスト"""

    def test_shop_name(self):
        assert BullionstarScraper.SHOP_NAME == "Bullionstar"

    def test_currency(self):
        # デフォルト通貨はUSD（実際はページの表示通貨を検出）
        assert BullionstarScraper.CURRENCY == "USD"

    def test_wait_time(self):
        assert BullionstarScraper.WAIT_TIME_MS == 5000


class TestApmexScraper:
    """APMEXスクレイパーのテスト"""

    def test_shop_name(self):
        assert ApmexScraper.SHOP_NAME == "APMEX"

    def test_currency(self):
        assert ApmexScraper.CURRENCY == "USD"

    def test_wait_time(self):
        # Bot検出対策のため長めに設定されていること
        assert ApmexScraper.WAIT_TIME_MS >= 8000


class TestScrapedData:
    """ScrapedDataのテスト"""

    def test_create_success(self):
        data = ScrapedData(
            product_name="Test Product",
            price=100.0,
            currency="USD",
            url="https://example.com",
            in_stock=True
        )
        assert data.product_name == "Test Product"
        assert data.price == 100.0
        assert data.currency == "USD"
        assert data.in_stock is True
        assert data.error is None

    def test_create_with_error(self):
        data = ScrapedData(
            product_name="",
            price=0.0,
            currency="USD",
            url="https://example.com",
            in_stock=False,
            error="Connection failed"
        )
        assert data.error == "Connection failed"
        assert data.in_stock is False
