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

    # カラーミー商品管理シートの拡張列ヘッダー（AC列以降）
    # ※G列（固定マージン価格）追加により、旧AB列以降が1列ずれている
    COLORME_EXTENDED_HEADERS: list[str] = [
        "同期モード",      # AC列 (index 28)
        "型番",           # AD列 (index 29)
        "カテゴリーID",    # AE列 (index 30)
        "サブカテゴリーID", # AF列 (index 31)
        "グループID",      # AG列 (index 32)
        "定価",           # AH列 (index 33)
        "会員価格",        # AI列 (index 34)
        "個別送料",        # AJ列 (index 35)
        "在庫管理",        # AK列 (index 36)
        "売切れ表示",      # AL列 (index 37)
        "適正在庫数",      # AM列 (index 38)
        "最小購入数",      # AN列 (index 39)
        "最大購入数",      # AO列 (index 40)
        "商品説明",        # AP列 (index 41)
        "簡易説明",        # AQ列 (index 42)
        "画像URL1",       # AR列 (index 43)
        "画像URL2",       # AS列 (index 44)
        "画像URL3",       # AT列 (index 45)
        "画像URL4",       # AU列 (index 46)
        "画像URL5",       # AV列 (index 47)
        "画像URL6",       # AW列 (index 48)
        "画像URL7",       # AX列 (index 49)
        "画像URL8",       # AY列 (index 50)
        "画像URL9",       # AZ列 (index 51)
        "画像URL10",      # BA列 (index 52)
        "同期ステータス",   # BB列 (index 53)
        "同期日時",        # BC列 (index 54)
    ]

    # 商品マスタシート名（サイト別）
    SHEET_MASTER_BRITANNIA: str = "商品マスタ_Britannia"
    SHEET_MASTER_APMEX: str = "商品マスタ_APMEX"

    # APMEXカテゴリーシート名
    SHEET_APMEX_CATEGORIES: str = "APMEXカテゴリー"

    # APMEXカテゴリーシートのヘッダー
    APMEX_CATEGORY_HEADERS: list[str] = [
        "カテゴリー名",        # A列
        "親カテゴリー",        # B列 (階層構造用)
        "APMEX URL",           # C列
        "登録",                # D列 (TRUE/FALSE)
        "カラーミーグループID",  # E列
        "親グループID",        # F列 (親カテゴリーのグループID)
        "登録日時",            # G列
        "ステータス",          # H列
    ]

    # ブリオンスター商品ページ一覧シート名
    SHEET_BULLIONSTAR_PRODUCTS: str = "ブリオンスター商品ページ一覧"

    # ブリオンスター商品ページ一覧シートのヘッダー
    BULLIONSTAR_PRODUCT_HEADERS: list[str] = [
        "商品名",          # A列
        "URL",             # B列
        "最上位カテゴリ",   # C列
        "親カテゴリ",       # D列
        "子カテゴリ",       # E列
        "取得日",          # F列
    ]

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
