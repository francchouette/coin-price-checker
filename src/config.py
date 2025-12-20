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
        "ロケーション",     # F列
        "取得日",          # G列
    ]

    # ========================================
    # 新シート構造（2025-12 新設計）
    # ========================================

    # 商品仕入れ先一覧シート
    SHEET_SUPPLIERS: str = "商品仕入れ先一覧"

    # 商品仕入れ先一覧シートのヘッダー（23列: A-W）
    SUPPLIER_HEADERS: list[str] = [
        "仕入れ先商品ID",      # A列
        "仕入れ先商品名",      # B列
        "仕入れ先商品URL",     # C列
        "仕入れ先サイト",      # D列
        "最上位カテゴリ",      # E列
        "親カテゴリ",          # F列
        "子カテゴリ",          # G列
        "ロケーション",        # H列
        "初回取得日",          # I列
        "商品グループID",      # J列
        "現在価格（現地通貨）", # K列
        "取引通貨",            # L列
        "在庫状況",            # M列
        "為替種類",            # N列
        "為替レート",          # O列
        "日本円換算価格",      # P列
        "最終価格更新日時",    # Q列
        "前回価格（現地通貨）", # R列
        "価格変動率",          # S列
        "採用フラグ",          # T列
        "採用理由",            # U列
        "カラーミー商品ID",    # V列
        "備考",                # W列
    ]

    # 新カラーミー商品初期登録一覧シート
    SHEET_COLORME_INITIAL: str = "新カラーミー商品初期登録一覧"

    # 新カラーミー商品管理シート
    SHEET_COLORME_V2: str = "新カラーミー商品管理"

    # 新カラーミー商品管理シートのヘッダー（80列: A-CB）
    # A-B列に操作項目を配置（担当者が操作しやすいよう先頭に配置）
    COLORME_V2_HEADERS: list[str] = [
        # A. 操作項目（A-B列）
        "同期モード",              # A列: "変更なし" / "更新" / "削除"
        "掲載設定",                # B列: "掲載する" / "掲載しない" / "会員のみ表示" / "会員のみ購入可"
        # B. 識別情報（C-E列）
        "カラーミー商品ID",        # C列
        "商品名",                  # D列
        "カラーミー商品URL",       # E列
        # C. 仕入れ先情報（F-O列）
        "仕入れ先商品URL",         # F列
        "仕入れ先商品名",          # G列
        "仕入れ先サイト",          # H列
        "最上位カテゴリ",          # I列
        "親カテゴリ",              # J列
        "子カテゴリ",              # K列
        "仕入れ先価格（現地通貨）", # L列
        "前回仕入れ価格",          # M列（旧CA列）
        "価格変動率",              # N列（旧CB列）
        "取引通貨",                # O列
        # D. 価格計算（P-AB列）
        "為替種類",                # P列
        "為替レート",              # Q列
        "仕入れ額(日本円)",        # R列
        "枚数",                    # S列
        "仕入れ合計",              # T列
        "設定マージン率",          # U列
        "設定マージン額",          # V列
        "送料",                    # W列
        "手数料",                  # X列
        "合計原価",                # Y列
        "適正価格",                # Z列
        "粗利額",                  # AA列
        "粗利率",                  # AB列
        # E. カラーミー価格情報（AC-AH列）
        "販売価格",                # AC列
        "定価",                    # AD列
        "会員価格",                # AE列
        "原価",                    # AF列
        "消費税込販売価格",        # AG列
        "消費税額",                # AH列
        # F. カテゴリー・グループ（AI-AL列）
        "大カテゴリーID",          # AI列
        "小カテゴリーID",          # AJ列
        "グループID",              # AK列
        "型番",                    # AL列
        # G. 在庫管理（AM-AS列）
        "在庫数",                  # AM列
        "在庫管理",                # AN列
        "残りわずか数",            # AO列
        "売切れ表示",              # AP列
        "最小購入数",              # AQ列
        "最大購入数",              # AR列
        "単位",                    # AS列
        # H. 送料・配送（AT-AW列）
        "個別送料",                # AT列
        "クール便料金",            # AU列
        "重量(g)",                 # AV列
        "配送不要",                # AW列
        # I. 商品説明（AX-BA列）
        "商品説明",                # AX列
        "簡易説明",                # AY列
        "スマホ説明",              # AZ列
        "備考",                    # BA列
        # J. 画像（BB-BK列）
        "メイン画像URL",           # BB列
        "サムネイルURL",           # BC列
        "画像URL1",                # BD列
        "画像URL2",                # BE列
        "画像URL3",                # BF列
        "画像URL4",                # BG列
        "画像URL5",                # BH列
        "画像URL6",                # BI列
        "画像URL7",                # BJ列
        "画像URL8",                # BK列
        # K. SEO項目（BL-BN列）※API非対応
        "ページタイトル",          # BL列
        "メタディスクリプション",  # BM列
        "メタキーワード",          # BN列
        # L. フラグ・設定（BO-BS列）
        "軽減税率対象",            # BO列
        "デジタルコンテンツ",      # BP列
        "定期購入",                # BQ列
        "表示順",                  # BR列
        "利用不可決済",            # BS列
        # M. 掲載期間（BT-BU列）
        "掲載開始日時",            # BT列
        "掲載終了日時",            # BU列
        # N. 更新制御（BV-BZ列）
        "価格更新ON/OFF",          # BV列
        "在庫連動ON/OFF",          # BW列
        "表示連動",                # BX列
        "同期ステータス",          # BY列
        "同期日時",                # BZ列
        # O. システム情報（CA-CB列）
        "商品作成日時",            # CA列
        "商品更新日時",            # CB列
    ]

    # 初期登録専用列ヘッダー（CC-CF列）
    COLORME_INITIAL_EXTRA_HEADERS: list[str] = [
        "登録ステータス",          # CC列
        "登録日時",                # CD列
        "要確認フラグ",            # CE列
        "確認メモ",                # CF列
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
