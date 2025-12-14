"""
設定管理モジュール

環境変数からの設定読み込みと、デフォルト値の管理を行う。
GitHub Secretsからの認証情報取得に対応。
"""

import os
import json
from typing import Optional

import google.auth


class Config:
    """アプリケーション設定クラス"""

    # Google Sheets設定
    SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

    # シート名
    SHEET_TRACKING: str = "トラッキング対象"
    SHEET_HISTORY: str = "価格履歴"
    SHEET_DASHBOARD: str = "ダッシュボード"
    SHEET_SETTINGS: str = "設定"

    # Slack通知設定
    SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")

    # Google Chat通知設定
    GOOGLE_CHAT_WEBHOOK_URL: str = os.getenv("GOOGLE_CHAT_WEBHOOK_URL", "")

    # カラーミーショップAPI設定
    COLORME_ACCESS_TOKEN: str = os.getenv("COLORME_ACCESS_TOKEN", "")
    COLORME_DRY_RUN: bool = os.getenv("COLORME_DRY_RUN", "true").lower() == "true"  # デフォルトはドライラン（更新しない）

    # スクレイピング設定
    SCRAPE_MIN_WAIT: float = 2.0  # 最小待機時間（秒）
    SCRAPE_MAX_WAIT: float = 5.0  # 最大待機時間（秒）
    SCRAPE_TIMEOUT: int = 30000  # タイムアウト（ミリ秒）

    # アラート設定（デフォルト値、スプレッドシートから上書き可能）
    DEFAULT_ALERT_THRESHOLD: float = 5.0  # 変動率閾値（%）

    # リトライ設定
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 2.0  # リトライ間隔（秒）

    @classmethod
    def get_google_credentials(cls) -> Optional[dict]:
        """
        Google認証情報を取得する

        Returns:
            dict: サービスアカウントの認証情報（JSON形式）
            None: 認証情報が設定されていない場合
        """
        if not cls.GOOGLE_SERVICE_ACCOUNT_JSON:
            return None

        try:
            return json.loads(cls.GOOGLE_SERVICE_ACCOUNT_JSON)
        except json.JSONDecodeError:
            # ファイルパスとして解釈を試みる
            if os.path.exists(cls.GOOGLE_SERVICE_ACCOUNT_JSON):
                with open(cls.GOOGLE_SERVICE_ACCOUNT_JSON, "r") as f:
                    return json.load(f)
            return None

    @classmethod
    def is_adc_available(cls) -> bool:
        """
        Application Default Credentials (ADC) が利用可能かどうかを確認する

        Returns:
            bool: ADCが利用可能な場合True
        """
        try:
            google.auth.default()
            return True
        except google.auth.exceptions.DefaultCredentialsError:
            return False

    @classmethod
    def validate(cls) -> list[str]:
        """
        必須設定の検証を行う

        Returns:
            list[str]: エラーメッセージのリスト（空なら正常）
        """
        errors = []

        if not cls.SPREADSHEET_ID:
            errors.append("SPREADSHEET_ID が設定されていません")

        # ADCまたはサービスアカウントJSONのいずれかが必要
        if not cls.is_adc_available() and not cls.get_google_credentials():
            errors.append("Google認証情報が設定されていません（ADCまたはGOOGLE_SERVICE_ACCOUNT_JSON）")

        return errors

    @classmethod
    def is_slack_enabled(cls) -> bool:
        """Slack通知が有効かどうかを返す"""
        return bool(cls.SLACK_WEBHOOK_URL)

    @classmethod
    def is_google_chat_enabled(cls) -> bool:
        """Google Chat通知が有効かどうかを返す"""
        return bool(cls.GOOGLE_CHAT_WEBHOOK_URL)

    @classmethod
    def is_colorme_enabled(cls) -> bool:
        """カラーミー連携が有効かどうかを返す"""
        return bool(cls.COLORME_ACCESS_TOKEN)

    # シート名
    SHEET_COLORME: str = "カラーミー商品管理"

    # カラーミー商品管理シートの拡張列ヘッダー（AB列以降）
    COLORME_EXTENDED_HEADERS: list[str] = [
        "同期モード",      # AB列 (index 27)
        "型番",           # AC列 (index 28)
        "カテゴリーID",    # AD列 (index 29)
        "サブカテゴリーID", # AE列 (index 30)
        "グループID",      # AF列 (index 31)
        "定価",           # AG列 (index 32)
        "会員価格",        # AH列 (index 33)
        "個別送料",        # AI列 (index 34)
        "在庫管理",        # AJ列 (index 35)
        "売切れ表示",      # AK列 (index 36)
        "適正在庫数",      # AL列 (index 37)
        "最小購入数",      # AM列 (index 38)
        "最大購入数",      # AN列 (index 39)
        "商品説明",        # AO列 (index 40)
        "簡易説明",        # AP列 (index 41)
        "商品画像URL",     # AQ列 (index 42)
        "追加画像URL",     # AR列 (index 43)
        "同期ステータス",   # AS列 (index 44)
        "同期日時",        # AT列 (index 45)
    ]

    # 商品マスタシート名（サイト別）
    SHEET_MASTER_BRITANNIA: str = "商品マスタ_Britannia"
    SHEET_MASTER_APMEX: str = "商品マスタ_APMEX"

    # Bright Data Proxy設定（レガシー）
    BRIGHTDATA_HOST: str = os.getenv("BRIGHTDATA_HOST", "")
    BRIGHTDATA_USERNAME: str = os.getenv("BRIGHTDATA_USERNAME", "")
    BRIGHTDATA_PASSWORD: str = os.getenv("BRIGHTDATA_PASSWORD", "")

    # Bright Data Web Unlocker API設定
    BRIGHTDATA_API_KEY: str = os.getenv("BRIGHTDATA_API_KEY", "")
    BRIGHTDATA_ZONE: str = os.getenv("BRIGHTDATA_ZONE", "web_unlocker1")

    # Bright Data Browser API設定
    BRIGHTDATA_BROWSER_WS: str = os.getenv("BRIGHTDATA_BROWSER_WS", "")

    @classmethod
    def is_brightdata_enabled(cls) -> bool:
        """Bright Data Proxyが有効かどうかを返す"""
        return bool(cls.BRIGHTDATA_HOST and cls.BRIGHTDATA_USERNAME and cls.BRIGHTDATA_PASSWORD)

    @classmethod
    def is_brightdata_api_enabled(cls) -> bool:
        """Bright Data Web Unlocker APIが有効かどうかを返す"""
        return bool(cls.BRIGHTDATA_API_KEY)

    @classmethod
    def is_brightdata_browser_enabled(cls) -> bool:
        """Bright Data Browser APIが有効かどうかを返す"""
        return bool(cls.BRIGHTDATA_BROWSER_WS)

    @classmethod
    def get_brightdata_proxy_url(cls) -> str:
        """Bright DataのProxy URLを返す"""
        if not cls.is_brightdata_enabled():
            return ""
        return f"http://{cls.BRIGHTDATA_USERNAME}:{cls.BRIGHTDATA_PASSWORD}@{cls.BRIGHTDATA_HOST}"
