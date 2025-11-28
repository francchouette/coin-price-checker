"""
為替レート取得モジュール

リアルタイム為替レートを取得する。
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class ExchangeRateClient:
    """為替レート取得クライアント"""

    # 無料の為替API（APIキー不要）
    API_URL = "https://api.exchangerate-api.com/v4/latest/USD"

    def __init__(self):
        self._rates: dict = {}
        self._fetched = False

    def fetch_rates(self) -> bool:
        """
        為替レートを取得する

        Returns:
            bool: 取得成功時True
        """
        try:
            response = requests.get(self.API_URL, timeout=10)
            response.raise_for_status()
            data = response.json()

            self._rates = data.get("rates", {})
            self._fetched = True

            jpy_rate = self._rates.get("JPY", 0)
            logger.info(f"為替レートを取得しました: 1 USD = {jpy_rate:.2f} JPY")

            return True

        except requests.RequestException as e:
            logger.error(f"為替レート取得エラー: {e}")
            return False

    def get_rate(self, from_currency: str, to_currency: str = "JPY") -> Optional[float]:
        """
        為替レートを取得する

        Args:
            from_currency: 変換元通貨（例: "USD"）
            to_currency: 変換先通貨（デフォルト: "JPY"）

        Returns:
            float: 為替レート（取得失敗時はNone）
        """
        if not self._fetched:
            if not self.fetch_rates():
                return None

        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        # USDからの変換
        if from_currency == "USD":
            return self._rates.get(to_currency)

        # USD以外からの変換（USD経由で計算）
        from_rate = self._rates.get(from_currency)
        to_rate = self._rates.get(to_currency)

        if from_rate and to_rate:
            return to_rate / from_rate

        return None

    def convert(self, amount: float, from_currency: str, to_currency: str = "JPY") -> Optional[float]:
        """
        金額を変換する

        Args:
            amount: 金額
            from_currency: 変換元通貨
            to_currency: 変換先通貨

        Returns:
            float: 変換後の金額（変換失敗時はNone）
        """
        rate = self.get_rate(from_currency, to_currency)
        if rate is None:
            return None

        return amount * rate
