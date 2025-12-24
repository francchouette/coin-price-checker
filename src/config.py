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

    # ブリオンスター商品ページ一覧シートのヘッダー（83列: A-CE）
    # ※A-C列: 管理列（採用フラグ、登録状況、仕入れ先商品ID）
    # ※D-CE列: 商品情報・カラーミー登録用項目
    BULLIONSTAR_PRODUCT_HEADERS: list[str] = [
        # === 管理列（A-C列: 3列）===
        "採用フラグ",              # A列: 「採用」「未採用」「検討中」
        "カラーミー登録状況",      # B列: 「登録済」「未登録」
        "仕入れ先商品ID",          # C列: BS-XXXXXX形式

        # === 仕入れ先商品情報（D-P列: 13列）===
        "カラーミー商品URL",       # D列: 登録後自動設定
        "仕入れ先商品URL",         # E列: ユニークキー
        "仕入れ先商品名",          # F列: 商品名（英語）
        "仕入れ先サイト",          # G列: 固定値「Bullionstar」
        "最上位カテゴリ",          # H列
        "親カテゴリ",              # I列
        "子カテゴリ",              # J列
        "製造国",                  # K列: サイトからスクレイピング
        "商品説明（英語）",        # L列: 仕入れ先の商品説明
        "仕様・スペック",          # M列: 重量、純度、サイズ等
        "発行年",                  # N列: 発行年/ミント年
        "発行数・限定数",          # O列: ミンテージ情報
        "仕入れ先在庫状況",        # P列: In Stock/Out of Stock

        # === 価格情報（Q-AG列: 17列）===
        "仕入れ先価格（現地通貨）", # Q列: スクレイピング
        "前回仕入れ価格",          # R列: 前回価格（現地通貨）
        "価格変動率",              # S列: 計算式
        "取引通貨",                # T列: USD/SGD等
        "為替種類",                # U列: Wise/クレカ（手入力）
        "為替レート",              # V列: 自動取得
        "仕入れ額(日本円)",        # W列: =Q*V
        "枚数",                    # X列: セット枚数（手入力、デフォルト1）
        "仕入れ合計",              # Y列: 計算式 =W*X
        "設定マージン率",          # Z列: 利益率（手入力、デフォルト1.1）
        "設定マージン額",          # AA列: 固定マージン額（手入力）
        "送料",                    # AB列: 仕入れ送料（手入力）
        "諸経費",                  # AC列: その他手数料（手入力）
        "合計原価",                # AD列: 計算式 =Y+AB+AC
        "適正価格",                # AE列: 計算式 =AD*Z+AA
        "粗利額",                  # AF列: 計算式 =AE-AD
        "粗利率",                  # AG列: 計算式 =AF/AE*100

        # === カラーミー価格情報（AH-AM列: 6列）===
        "販売価格",                # AH列: 計算式 =AE
        "定価",                    # AI列: 定価（手入力）
        "会員価格",                # AJ列: 会員価格（手入力）
        "原価",                    # AK列: 計算式 =AD
        "消費税込販売価格",        # AL列: 計算式 =AH*1.1
        "消費税額",                # AM列: 計算式 =AH*0.1

        # === カテゴリー・グループ（AN-AS列: 6列）===
        "大カテゴリーID",          # AN列: カラーミー大カテゴリーID（自動）
        "大カテゴリー名称",        # AO列: カラーミー大カテゴリー名称（自動）
        "小カテゴリーID",          # AP列: カラーミー小カテゴリーID（自動）
        "小カテゴリー名称",        # AQ列: カラーミー小カテゴリー名称（自動）
        "グループID",              # AR列: カラーミーグループID（自動、カンマ区切り）
        "グループ名",              # AS列: カラーミーグループ名（自動、カンマ区切り）

        # === 型番（AT列: 1列）===
        "型番",                    # AT列: 自動=仕入れ先商品ID

        # === 在庫管理（AU-BA列: 7列）===
        "在庫数",                  # AU列: 初期在庫数（手入力、デフォルト10）
        "在庫管理",                # AV列: 「する」「しない」
        "残りわずか数",            # AW列: few_num（手入力、デフォルト3）
        "売切れ表示",              # AX列: 「表示」「非表示」
        "最小購入数",              # AY列: min_num（手入力、デフォルト1）
        "最大購入数",              # AZ列: max_num（手入力、デフォルト0）
        "単位",                    # BA列: unit（手入力、空欄可）

        # === 送料・配送（BB-BE列: 4列）===
        "個別送料",                # BB列: delivery_charge（手入力、デフォルト0）
        "クール便料金",            # BC列: cool_charge（手入力）
        "重量(g)",                 # BD列: weight（手入力、空欄可）
        "配送不要",                # BE列: 「不要」「必要」

        # === 商品説明（BF-BI列: 4列）===
        "商品説明",                # BF列: カラーミー用商品説明（AIで生成）
        "簡易説明",                # BG列: カラーミー用簡易説明（AIで生成）
        "スマホ説明",              # BH列: スマホ用説明（AIで生成）
        "備考",                    # BI列: 備考（手入力）

        # === 画像URL（BJ-BS列: 10列）===
        "画像URL1",                # BJ列
        "画像URL2",                # BK列
        "画像URL3",                # BL列
        "画像URL4",                # BM列
        "画像URL5",                # BN列
        "画像URL6",                # BO列
        "画像URL7",                # BP列
        "画像URL8",                # BQ列
        "画像URL9",                # BR列
        "画像URL10",               # BS列

        # === SEO項目（BT-BV列: 3列）===
        "ページタイトル",          # BT列: page_title（AIで生成）
        "メタディスクリプション",  # BU列: meta_description（AIで生成）
        "メタキーワード",          # BV列: meta_keywords（AIで生成）

        # === フラグ・設定（BW-CA列: 5列）===
        "軽減税率対象",            # BW列: 「対象」「対象外」
        "デジタルコンテンツ",      # BX列: 「対象」「対象外」
        "定期購入",                # BY列: 「対象」「対象外」
        "表示順",                  # BZ列: sort（手入力、デフォルト0）
        "利用不可決済",            # CA列: カンマ区切りの決済ID（手入力）

        # === 掲載期間（CB-CC列: 2列）===
        "掲載開始日時",            # CB列: 掲載開始日時（手入力、空欄可）
        "掲載終了日時",            # CC列: 掲載終了日時（手入力、空欄可）

        # === システム情報（CD-CE列: 2列）===
        "同期日時",                # CD列: 最終同期日時
        "商品更新日時",            # CE列: カラーミー側の更新日時
    ]

    # ========================================
    # 新シート構造（2025-12 新設計）
    # ========================================

    # 商品仕入れ先一覧シート
    SHEET_SUPPLIERS: str = "商品仕入れ先一覧"

    # 商品仕入れ先一覧シートのヘッダー（32列: A-AF）
    # ※ブリオンスター商品ページ一覧シートのC-AK列と同じ構造（採用フラグ・登録状況列なし）
    # ※採用フラグ・カラーミー登録状況はブリオンスター商品ページ一覧のみ
    SUPPLIER_HEADERS: list[str] = [
        "仕入れ先商品ID",      # A列
        "仕入れ先商品名",      # B列
        "仕入れ先商品URL",     # C列
        "仕入れ先サイト",      # D列
        "最上位カテゴリ",      # E列
        "親カテゴリ",          # F列
        "子カテゴリ",          # G列
        "製造国",              # H列
        "初回取得日",          # I列
        "在庫状況",            # J列: In Stock/Out of Stock
        "現在価格（現地通貨）", # K列
        "取引通貨",            # L列
        "為替種類",            # M列
        "為替レート",          # N列
        "日本円換算価格",      # O列
        "最終価格更新日時",    # P列
        "前回価格（現地通貨）", # Q列
        "価格変動率",          # R列
        "カラーミー商品ID",    # S列
        "備考",                # T列
        # 画像URL（U-AD列: 10列）
        "画像URL1",            # U列
        "画像URL2",            # V列
        "画像URL3",            # W列
        "画像URL4",            # X列
        "画像URL5",            # Y列
        "画像URL6",            # Z列
        "画像URL7",            # AA列
        "画像URL8",            # AB列
        "画像URL9",            # AC列
        "画像URL10",           # AD列
        # 詳細情報（AE-AF列: 5列）
        "仕様・スペック",      # AE列: 重量、純度、サイズ等
        "商品説明（英語）",    # AF列: 仕入れ先の商品説明
        "商品説明（日本語）",  # AG列: AI翻訳後の説明
        "発行年",              # AH列: 発行年/ミント年
        "発行数・限定数",      # AI列: ミンテージ情報
    ]

    # 新カラーミー商品初期登録一覧シート
    SHEET_COLORME_INITIAL: str = "新カラーミー商品初期登録一覧"

    # 新カラーミー商品管理シート
    SHEET_COLORME_V2: str = "新カラーミー商品管理"

    # 新カラーミー商品管理シートのヘッダー（90列: A-CL）
    # A-F列に操作項目を配置（担当者が操作しやすいよう先頭に配置）
    COLORME_V2_HEADERS: list[str] = [
        # A. 操作項目（A-F列）
        "同期モード",              # A列: "変更なし" / "更新" / "削除"
        "掲載設定",                # B列: "掲載する" / "掲載しない" / "会員のみ表示" / "会員のみ購入可"
        "価格更新ON/OFF",          # C列: "ON" / "OFF" - 適正価格をカラーミーに反映するか
        "在庫連動ON/OFF",          # D列: "ON" / "OFF" - 在庫を連動するか
        "在庫によっての掲載連動",  # E列: "ON" / "OFF" - 在庫による掲載連動
        "同期ステータス",          # F列: "同期済み" / "エラー" / "ダウンロード済"
        # B. 識別情報（G-I列）
        "カラーミー商品ID",        # G列
        "商品名",                  # H列
        "カラーミー商品URL",       # I列
        # C. 仕入れ先情報（J-Y列）
        "仕入れ先商品URL",         # J列
        "仕入れ先商品名",          # K列
        "仕入れ先サイト",          # L列
        "最上位カテゴリ",          # M列
        "親カテゴリ",              # N列
        "子カテゴリ",              # O列
        "製造国",                  # P列
        "商品説明（英語）",        # Q列
        "仕様・スペック",          # R列
        "発行年",                  # S列
        "発行数・限定数",          # T列
        "仕入れ先在庫状況",        # U列: "In Stock" / "Out of Stock"
        "仕入れ先価格（現地通貨）", # V列
        "前回仕入れ価格",          # W列
        "価格変動率",              # X列
        "取引通貨",                # Y列
        # D. 価格計算（Z-AL列）
        "為替種類",                # Z列
        "為替レート",              # AA列
        "仕入れ額(日本円)",        # AB列
        "枚数",                    # AC列
        "仕入れ合計",              # AD列
        "設定マージン率",          # AE列
        "設定マージン額",          # AF列
        "送料",                    # AG列
        "諸経費",                  # AH列
        "合計原価",                # AI列
        "適正価格",                # AJ列
        "粗利額",                  # AK列
        "粗利率",                  # AL列
        # E. カラーミー価格情報（AM-AR列）
        "販売価格",                # AM列
        "定価",                    # AN列
        "会員価格",                # AO列
        "原価",                    # AP列
        "消費税込販売価格",        # AQ列
        "消費税額",                # AR列
        # F. カテゴリー・グループ（AS-AV列）
        "大カテゴリーID",          # AS列
        "小カテゴリーID",          # AT列
        "グループID",              # AU列
        "型番",                    # AV列
        # G. 在庫管理（AW-BC列）
        "在庫数",                  # AW列
        "在庫管理",                # AX列
        "残りわずか数",            # AY列
        "売切れ表示",              # AZ列
        "最小購入数",              # BA列
        "最大購入数",              # BB列
        "単位",                    # BC列
        # H. 送料・配送（BD-BG列）
        "個別送料",                # BD列
        "クール便料金",            # BE列
        "重量(g)",                 # BF列
        "配送不要",                # BG列
        # I. 商品説明（BH-BK列）
        "商品説明",                # BH列
        "簡易説明",                # BI列
        "スマホ説明",              # BJ列
        "備考",                    # BK列
        # J. 画像（BL-BU列: 10列）
        "メイン画像URL",           # BL列
        "サムネイルURL",           # BM列
        "画像URL1",                # BN列
        "画像URL2",                # BO列
        "画像URL3",                # BP列
        "画像URL4",                # BQ列
        "画像URL5",                # BR列
        "画像URL6",                # BS列
        "画像URL7",                # BT列
        "画像URL8",                # BU列
        # K. SEO項目（BV-BX列）※API非対応
        "ページタイトル",          # BV列
        "メタディスクリプション",  # BW列
        "メタキーワード",          # BX列
        # L. フラグ・設定（BY-CC列）
        "軽減税率対象",            # BY列
        "デジタルコンテンツ",      # BZ列
        "定期購入",                # CA列
        "表示順",                  # CB列
        "利用不可決済",            # CC列
        # M. 掲載期間（CD-CE列）
        "掲載開始日時",            # CD列
        "掲載終了日時",            # CE列
        # N. システム情報（CF-CH列）
        "同期日時",                # CF列
        "商品作成日時",            # CG列
        "商品更新日時",            # CH列
    ]

    # 初期登録専用列ヘッダー（CI-CL列）
    COLORME_INITIAL_EXTRA_HEADERS: list[str] = [
        "登録ステータス",          # CI列
        "登録日時",                # CJ列
        "要確認フラグ",            # CK列
        "確認メモ",                # CL列
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
