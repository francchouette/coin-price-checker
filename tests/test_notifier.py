"""
通知機能のテスト
"""

import pytest
from unittest.mock import MagicMock, patch

from src.notifier import (
    PriceAlert,
    SlackNotifier,
    ConsoleNotifier,
    calculate_change_rate,
    check_alert_threshold,
)


class TestCalculateChangeRate:
    """価格変動率計算のテスト"""

    def test_price_increase(self):
        # 100 -> 110 = +10%
        rate = calculate_change_rate(110.0, 100.0)
        assert rate == 10.0

    def test_price_decrease(self):
        # 100 -> 90 = -10%
        rate = calculate_change_rate(90.0, 100.0)
        assert rate == -10.0

    def test_no_change(self):
        rate = calculate_change_rate(100.0, 100.0)
        assert rate == 0.0

    def test_large_increase(self):
        # 100 -> 200 = +100%
        rate = calculate_change_rate(200.0, 100.0)
        assert rate == 100.0

    def test_previous_price_zero(self):
        # ゼロ除算を避ける
        rate = calculate_change_rate(100.0, 0.0)
        assert rate == 0.0


class TestCheckAlertThreshold:
    """アラート閾値チェックのテスト"""

    def test_above_threshold(self):
        # 変動率6%、閾値5% -> True
        assert check_alert_threshold(6.0, 5.0) is True

    def test_below_threshold(self):
        # 変動率3%、閾値5% -> False
        assert check_alert_threshold(3.0, 5.0) is False

    def test_equal_threshold(self):
        # 変動率5%、閾値5% -> True
        assert check_alert_threshold(5.0, 5.0) is True

    def test_negative_above_threshold(self):
        # 変動率-6%、閾値5% -> True（絶対値で判定）
        assert check_alert_threshold(-6.0, 5.0) is True

    def test_negative_below_threshold(self):
        # 変動率-3%、閾値5% -> False
        assert check_alert_threshold(-3.0, 5.0) is False


class TestPriceAlert:
    """PriceAlertのテスト"""

    def test_create_alert(self):
        alert = PriceAlert(
            product_name="Gold Coin",
            shop_name="Bullionstar",
            current_price=110.0,
            previous_price=100.0,
            change_rate=10.0,
            currency="JPY",
            url="https://example.com",
            timestamp="2025-11-28 09:00:00"
        )
        assert alert.product_name == "Gold Coin"
        assert alert.shop_name == "Bullionstar"
        assert alert.change_rate == 10.0


class TestConsoleNotifier:
    """コンソール通知のテスト"""

    def test_format_message(self):
        notifier = ConsoleNotifier()
        alert = PriceAlert(
            product_name="Gold Coin",
            shop_name="Bullionstar",
            current_price=110.0,
            previous_price=100.0,
            change_rate=10.0,
            currency="JPY",
            url="https://example.com",
            timestamp="2025-11-28 09:00:00"
        )
        message = notifier.format_message(alert)

        assert "Gold Coin" in message
        assert "Bullionstar" in message
        assert "上昇" in message
        assert "+10.00%" in message

    def test_format_message_decrease(self):
        notifier = ConsoleNotifier()
        alert = PriceAlert(
            product_name="Silver Coin",
            shop_name="APMEX",
            current_price=90.0,
            previous_price=100.0,
            change_rate=-10.0,
            currency="USD",
            url="https://example.com",
            timestamp="2025-11-28 09:00:00"
        )
        message = notifier.format_message(alert)

        assert "下落" in message
        assert "-10.00%" in message


class TestSlackNotifier:
    """Slack通知のテスト"""

    def test_no_webhook_url(self):
        notifier = SlackNotifier(webhook_url="")
        alert = PriceAlert(
            product_name="Test",
            shop_name="Test",
            current_price=100.0,
            previous_price=100.0,
            change_rate=0.0,
            currency="USD",
            url="https://example.com",
            timestamp="2025-11-28 09:00:00"
        )
        result = notifier.send(alert)
        assert result is False

    @patch('requests.post')
    def test_send_success(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()

        notifier = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        alert = PriceAlert(
            product_name="Test",
            shop_name="Test",
            current_price=110.0,
            previous_price=100.0,
            change_rate=10.0,
            currency="USD",
            url="https://example.com",
            timestamp="2025-11-28 09:00:00"
        )
        result = notifier.send(alert)

        assert result is True
        mock_post.assert_called_once()
