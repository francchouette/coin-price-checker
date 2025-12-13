"""
通知モジュール

価格変動アラートを各種チャネルに送信する。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class PriceAlert:
    """価格アラートのデータクラス"""
    product_name: str
    shop_name: str
    current_price: float
    previous_price: float
    change_rate: float  # パーセント
    currency: str
    url: str
    timestamp: str
    alert_type: str = "price"  # "price" or "stock"
    in_stock: bool = True


class BaseNotifier(ABC):
    """通知の基底クラス"""

    @abstractmethod
    def send(self, alert: PriceAlert) -> bool:
        """
        アラートを送信する

        Args:
            alert: 価格アラート

        Returns:
            bool: 送信成功時True
        """
        pass

    @abstractmethod
    def send_batch(self, alerts: list[PriceAlert]) -> bool:
        """
        複数のアラートをまとめて送信する

        Args:
            alerts: 価格アラートのリスト

        Returns:
            bool: 送信成功時True
        """
        pass

    def format_message(self, alert: PriceAlert) -> str:
        """
        アラートメッセージをフォーマットする

        Args:
            alert: 価格アラート

        Returns:
            str: フォーマット済みメッセージ
        """
        if alert.alert_type == "stock":
            return (
                f"【在庫切れアラート】\n"
                f"商品: {alert.product_name}\n"
                f"ショップ: {alert.shop_name}\n"
                f"最終価格: {alert.currency} {alert.current_price:,.2f}\n"
                f"日時: {alert.timestamp}\n"
                f"URL: {alert.url}"
            )

        direction = "上昇" if alert.change_rate > 0 else "下落"
        symbol = "+" if alert.change_rate > 0 else ""

        return (
            f"【価格{direction}アラート】\n"
            f"商品: {alert.product_name}\n"
            f"ショップ: {alert.shop_name}\n"
            f"現在価格: {alert.currency} {alert.current_price:,.2f}\n"
            f"前回価格: {alert.currency} {alert.previous_price:,.2f}\n"
            f"変動率: {symbol}{alert.change_rate:.2f}%\n"
            f"日時: {alert.timestamp}\n"
            f"URL: {alert.url}"
        )


class SlackNotifier(BaseNotifier):
    """Slack通知クラス"""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Args:
            webhook_url: Slack Webhook URL（省略時は環境変数から取得）
        """
        self.webhook_url = webhook_url or Config.SLACK_WEBHOOK_URL

    def send(self, alert: PriceAlert) -> bool:
        """単一のアラートを送信する"""
        if not self.webhook_url:
            logger.warning("Slack Webhook URLが設定されていません")
            return False

        try:
            payload = self._build_payload(alert)
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"Slack通知を送信しました: {alert.product_name}")
            return True
        except requests.RequestException as e:
            logger.error(f"Slack通知の送信に失敗しました: {e}")
            return False

    def send_batch(self, alerts: list[PriceAlert]) -> bool:
        """複数のアラートをまとめて送信する"""
        if not alerts:
            return True

        if not self.webhook_url:
            logger.warning("Slack Webhook URLが設定されていません")
            return False

        try:
            payload = self._build_batch_payload(alerts)
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            logger.info(f"Slack通知を{len(alerts)}件送信しました")
            return True
        except requests.RequestException as e:
            logger.error(f"Slack通知の送信に失敗しました: {e}")
            return False

    def _build_payload(self, alert: PriceAlert) -> dict:
        """単一アラート用のペイロードを構築する"""
        direction = "上昇" if alert.change_rate > 0 else "下落"
        emoji = ":chart_with_upwards_trend:" if alert.change_rate > 0 else ":chart_with_downwards_trend:"
        color = "#36a64f" if alert.change_rate > 0 else "#ff0000"
        symbol = "+" if alert.change_rate > 0 else ""

        return {
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": f"{emoji} 価格{direction}アラート",
                                "emoji": True
                            }
                        },
                        {
                            "type": "section",
                            "fields": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"*商品:*\n{alert.product_name}"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*ショップ:*\n{alert.shop_name}"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*現在価格:*\n{alert.currency} {alert.current_price:,.2f}"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*前回価格:*\n{alert.currency} {alert.previous_price:,.2f}"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*変動率:*\n{symbol}{alert.change_rate:.2f}%"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*日時:*\n{alert.timestamp}"
                                }
                            ]
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"<{alert.url}|商品ページを開く>"
                            }
                        }
                    ]
                }
            ]
        }

    def _build_batch_payload(self, alerts: list[PriceAlert]) -> dict:
        """複数アラート用のペイロードを構築する"""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":bell: 価格変動アラート ({len(alerts)}件)",
                    "emoji": True
                }
            },
            {"type": "divider"}
        ]

        for alert in alerts:
            direction = "上昇" if alert.change_rate > 0 else "下落"
            emoji = ":arrow_up:" if alert.change_rate > 0 else ":arrow_down:"
            symbol = "+" if alert.change_rate > 0 else ""

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *{alert.product_name}*\n"
                        f"_{alert.shop_name}_ | "
                        f"{alert.currency} {alert.previous_price:,.2f} → "
                        f"{alert.currency} {alert.current_price:,.2f} "
                        f"(*{symbol}{alert.change_rate:.2f}%*)\n"
                        f"<{alert.url}|詳細>"
                    )
                }
            })

        return {"blocks": blocks}


class GoogleChatNotifier(BaseNotifier):
    """Google Chat通知クラス"""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Args:
            webhook_url: Google Chat Webhook URL（省略時は環境変数から取得）
        """
        self.webhook_url = webhook_url or Config.GOOGLE_CHAT_WEBHOOK_URL

    def send(self, alert: PriceAlert) -> bool:
        """単一のアラートを送信する"""
        if not self.webhook_url:
            logger.warning("Google Chat Webhook URLが設定されていません")
            return False

        try:
            payload = self._build_payload(alert)
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"Google Chat通知を送信しました: {alert.product_name}")
            return True
        except requests.RequestException as e:
            logger.error(f"Google Chat通知の送信に失敗しました: {e}")
            return False

    def send_batch(self, alerts: list[PriceAlert]) -> bool:
        """複数のアラートをまとめて送信する"""
        if not alerts:
            return True

        if not self.webhook_url:
            logger.warning("Google Chat Webhook URLが設定されていません")
            return False

        try:
            payload = self._build_batch_payload(alerts)
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            logger.info(f"Google Chat通知を{len(alerts)}件送信しました")
            return True
        except requests.RequestException as e:
            logger.error(f"Google Chat通知の送信に失敗しました: {e}")
            return False

    def _build_payload(self, alert: PriceAlert) -> dict:
        """単一アラート用のペイロードを構築する"""
        if alert.alert_type == "stock":
            text = (
                f"*【⚠️ 在庫切れアラート】*\n\n"
                f"*商品:* {alert.product_name}\n"
                f"*ショップ:* {alert.shop_name}\n"
                f"*最終価格:* {alert.currency} {alert.current_price:,.2f}\n"
                f"*日時:* {alert.timestamp}\n\n"
                f"<{alert.url}|商品ページを開く>"
            )
        elif alert.alert_type == "stock_restored":
            text = (
                f"*【✅ 在庫復活アラート】*\n\n"
                f"*商品:* {alert.product_name}\n"
                f"*ショップ:* {alert.shop_name}\n"
                f"*現在価格:* {alert.currency} {alert.current_price:,.2f}\n"
                f"*日時:* {alert.timestamp}\n\n"
                f"<{alert.url}|商品ページを開く>"
            )
        else:
            direction = "上昇 📈" if alert.change_rate > 0 else "下落 📉"
            symbol = "+" if alert.change_rate > 0 else ""

            text = (
                f"*【価格{direction}アラート】*\n\n"
                f"*商品:* {alert.product_name}\n"
                f"*ショップ:* {alert.shop_name}\n"
                f"*現在価格:* {alert.currency} {alert.current_price:,.2f}\n"
                f"*前回価格:* {alert.currency} {alert.previous_price:,.2f}\n"
                f"*変動率:* {symbol}{alert.change_rate:.2f}%\n"
                f"*日時:* {alert.timestamp}\n\n"
                f"<{alert.url}|商品ページを開く>"
            )

        return {"text": text}

    def _build_batch_payload(self, alerts: list[PriceAlert]) -> dict:
        """複数アラート用のペイロードを構築する"""
        lines = [f"*🔔 アラート ({len(alerts)}件)*\n"]

        for alert in alerts:
            if alert.alert_type == "stock":
                lines.append(
                    f"⚠️ *{alert.product_name}* - 在庫切れ\n"
                    f"   _{alert.shop_name}_ | "
                    f"最終価格: {alert.currency} {alert.current_price:,.2f}\n"
                    f"   <{alert.url}|詳細>\n"
                )
            elif alert.alert_type == "stock_restored":
                lines.append(
                    f"✅ *{alert.product_name}* - 在庫復活\n"
                    f"   _{alert.shop_name}_ | "
                    f"現在価格: {alert.currency} {alert.current_price:,.2f}\n"
                    f"   <{alert.url}|詳細>\n"
                )
            else:
                emoji = "📈" if alert.change_rate > 0 else "📉"
                symbol = "+" if alert.change_rate > 0 else ""

                lines.append(
                    f"{emoji} *{alert.product_name}*\n"
                    f"   _{alert.shop_name}_ | "
                    f"{alert.currency} {alert.previous_price:,.2f} → "
                    f"{alert.currency} {alert.current_price:,.2f} "
                    f"(*{symbol}{alert.change_rate:.2f}%*)\n"
                    f"   <{alert.url}|詳細>\n"
                )

        return {"text": "\n".join(lines)}


class ConsoleNotifier(BaseNotifier):
    """コンソール通知クラス（デバッグ用）"""

    def send(self, alert: PriceAlert) -> bool:
        """コンソールにアラートを表示する"""
        message = self.format_message(alert)
        print("\n" + "=" * 50)
        print(message)
        print("=" * 50 + "\n")
        return True

    def send_batch(self, alerts: list[PriceAlert]) -> bool:
        """複数のアラートをコンソールに表示する"""
        for alert in alerts:
            self.send(alert)
        return True


def calculate_change_rate(current_price: float, previous_price: float) -> float:
    """
    価格変動率を計算する

    Args:
        current_price: 現在価格
        previous_price: 前回価格

    Returns:
        float: 変動率（%）
    """
    if previous_price == 0:
        return 0.0
    return ((current_price - previous_price) / previous_price) * 100


def check_alert_threshold(change_rate: float, threshold: float = None) -> bool:
    """
    変動率がアラート閾値を超えているか確認する

    Args:
        change_rate: 変動率（%）
        threshold: 閾値（%）、省略時はデフォルト値

    Returns:
        bool: 閾値を超えている場合True
    """
    if threshold is None:
        threshold = Config.DEFAULT_ALERT_THRESHOLD

    return abs(change_rate) >= threshold
