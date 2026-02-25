"""
Google Sheets連携モジュール

スプレッドシートへの接続、データ読み書きを管理する。
リトライ機構を含む。
"""

import time
import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

import gspread
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class TrackingTarget:
    """トラッキング対象のデータクラス"""
    status: str  # ON/OFF
    shop_name: str
    product_name: str
    url: str
    price_selector: str
    name_selector: str
    currency: str  # 取得通貨（USD, SGD, EUR等）
    row_index: int  # スプレッドシート上の行番号


@dataclass
class PriceRecord:
    """価格履歴レコードのデータクラス"""
    timestamp: str
    shop_name: str
    product_name: str
    price: float
    currency: str
    previous_price: float = 0.0
    change_rate: float = 0.0
    in_stock: bool = True
    url: str = ""


class SpreadsheetClient:
    """Google Sheets クライアント"""

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self):
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None

    def connect(self) -> bool:
        """
        スプレッドシートに接続する

        Workload Identity Federation（ADC）またはサービスアカウントJSONを使用。
        ADCが優先され、利用できない場合はJSONにフォールバック。

        Returns:
            bool: 接続成功時True
        """
        try:
            # まずApplication Default Credentials（ADC）を試す
            # GitHub ActionsでWorkload Identity Federationを使用する場合はこちら
            credentials, project = google.auth.default(scopes=self.SCOPES)
            # quota projectを明示的に設定してセッションを作成
            # gspread.authorize() はquota projectヘッダーを送信しないため、
            # AuthorizedSessionを直接作成して渡す
            if hasattr(credentials, 'with_quota_project'):
                credentials = credentials.with_quota_project("coin-price-tracker-479614")
            session = AuthorizedSession(credentials)
            self._client = gspread.authorize(credentials=None, session=session)
            self._spreadsheet = self._client.open_by_key(Config.SPREADSHEET_ID)
            logger.info(f"スプレッドシートに接続しました（ADC使用）: {self._spreadsheet.title}")
            return True
        except google.auth.exceptions.DefaultCredentialsError:
            logger.info("ADCが利用できません。サービスアカウントJSONを使用します。")

        # フォールバック: サービスアカウントJSON
        credentials_dict = Config.get_google_credentials()
        if not credentials_dict:
            logger.error("Google認証情報が取得できません")
            return False

        try:
            credentials = Credentials.from_service_account_info(
                credentials_dict,
                scopes=self.SCOPES
            )
            self._client = gspread.authorize(credentials)
            self._spreadsheet = self._client.open_by_key(Config.SPREADSHEET_ID)
            logger.info(f"スプレッドシートに接続しました（JSON使用）: {self._spreadsheet.title}")
            return True
        except Exception as e:
            logger.error(f"スプレッドシート接続エラー: {e}")
            return False

    def get_tracking_targets(self) -> list[TrackingTarget]:
        """
        トラッキング対象リストを取得する（ONのもののみ）

        Returns:
            list[TrackingTarget]: トラッキング対象のリスト
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return []

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_TRACKING)
            records = sheet.get_all_values()

            targets = []
            for i, row in enumerate(records[1:], start=2):  # ヘッダーをスキップ
                if len(row) >= 4 and row[0].upper() == "ON":
                    targets.append(TrackingTarget(
                        status=row[0],
                        shop_name=row[1],
                        product_name=row[2],
                        url=row[3],
                        price_selector=row[4] if len(row) > 4 else "",
                        name_selector=row[5] if len(row) > 5 else "",
                        currency=row[6].upper() if len(row) > 6 and row[6] else "USD",
                        row_index=i
                    ))

            logger.info(f"トラッキング対象を{len(targets)}件取得しました")
            return targets
        except Exception as e:
            logger.error(f"トラッキング対象の取得エラー: {e}")
            return []

    def get_settings(self) -> dict:
        """
        設定シートから設定値を取得する

        Returns:
            dict: 設定値の辞書
        """
        if not self._spreadsheet:
            return {}

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_SETTINGS)
            records = sheet.get_all_values()

            settings = {}
            for row in records[1:]:  # ヘッダーをスキップ
                if len(row) >= 2:
                    settings[row[0]] = row[1]

            logger.info(f"設定を取得しました: {settings}")
            return settings
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"設定シート '{Config.SHEET_SETTINGS}' が見つかりません。デフォルト値を使用します。")
            return {}
        except Exception as e:
            logger.error(f"設定の取得エラー: {e}")
            return {}

    def get_alert_threshold(self) -> float:
        """
        アラート閾値を取得する

        Returns:
            float: アラート閾値（%）
        """
        settings = self.get_settings()
        try:
            # 新旧両方の設定名に対応
            threshold = settings.get("ALERT_THRESHOLD（%）") or settings.get("ALERT_THRESHOLD")
            return float(threshold) if threshold else Config.DEFAULT_ALERT_THRESHOLD
        except ValueError:
            return Config.DEFAULT_ALERT_THRESHOLD

    def get_colorme_update_enabled(self) -> bool:
        """
        カラーミー価格更新が有効かどうかを取得する

        Returns:
            bool: 更新が有効な場合True
        """
        settings = self.get_settings()
        value = settings.get("COLORME_UPDATE_ENABLED", "OFF")
        return value.upper() == "ON"

    def _retry_operation(self, operation, *args, **kwargs):
        """
        リトライ機構付きで操作を実行する

        Args:
            operation: 実行する関数
            *args, **kwargs: 関数の引数

        Returns:
            操作の結果

        Raises:
            Exception: 最大リトライ回数を超えた場合
        """
        last_error = None
        for attempt in range(Config.MAX_RETRIES):
            try:
                return operation(*args, **kwargs)
            except Exception as e:
                last_error = e
                logger.warning(f"操作失敗 (試行 {attempt + 1}/{Config.MAX_RETRIES}): {e}")
                if attempt < Config.MAX_RETRIES - 1:
                    time.sleep(Config.RETRY_DELAY)

        raise last_error

    def save_price_record(self, record: PriceRecord) -> bool:
        """
        価格レコードを保存する（リトライ機構付き）

        Args:
            record: 保存する価格レコード

        Returns:
            bool: 保存成功時True
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        def _save():
            sheet = self._spreadsheet.worksheet(Config.SHEET_HISTORY)
            row = [
                record.timestamp,
                record.shop_name,
                record.product_name,
                str(record.price),
                record.currency,
                record.url
            ]
            sheet.append_row(row)
            return True

        try:
            self._retry_operation(_save)
            logger.info(f"価格レコードを保存しました: {record.product_name} - {record.price}")
            return True
        except Exception as e:
            logger.error(f"価格レコードの保存に失敗しました: {e}")
            return False

    def save_price_records(self, records: list[PriceRecord]) -> bool:
        """
        複数の価格レコードを一括保存する（リトライ機構付き）

        Args:
            records: 保存する価格レコードのリスト

        Returns:
            bool: 保存成功時True
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        if not records:
            return True

        def _save_batch():
            sheet = self._spreadsheet.worksheet(Config.SHEET_HISTORY)
            rows = [
                [
                    record.timestamp,
                    record.shop_name,
                    record.product_name,
                    str(record.price),
                    record.currency,
                    str(record.previous_price) if record.previous_price else "",
                    f"{record.change_rate:+.2f}%" if record.change_rate else "",
                    "In Stock" if record.in_stock else "Out of Stock",
                    record.url
                ]
                for record in records
            ]
            sheet.append_rows(rows)
            return True

        try:
            self._retry_operation(_save_batch)
            logger.info(f"価格レコードを{len(records)}件保存しました")
            return True
        except Exception as e:
            logger.error(f"価格レコードの一括保存に失敗しました: {e}")
            return False

    def get_latest_price(self, url: str) -> Optional[float]:
        """
        指定URLの直近の価格を取得する

        Args:
            url: 商品URL

        Returns:
            float: 直近の価格（見つからない場合はNone）
        """
        if not self._spreadsheet:
            return None

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_HISTORY)
            records = sheet.get_all_values()

            # 最新のレコードから逆順に検索
            for row in reversed(records[1:]):  # ヘッダーをスキップ
                if len(row) >= 6 and row[5] == url:
                    try:
                        return float(row[3])
                    except ValueError:
                        continue

            return None
        except Exception as e:
            logger.error(f"直近価格の取得エラー: {e}")
            return None

    def get_latest_stock_status(self, urls: list[str]) -> dict[str, bool]:
        """
        複数URLの直近の在庫状況を一括取得する（カラーミー商品管理シートから取得）

        Args:
            urls: 商品URLのリスト

        Returns:
            dict: URL -> 在庫状況（True=In Stock）の辞書
        """
        if not self._spreadsheet:
            return {}

        try:
            url_set = set(urls)
            stock_status = {}

            # カラーミー商品管理シートのAB列（在庫状況）から取得
            try:
                sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
                records = sheet.get_all_values()

                for row in records[1:]:  # ヘッダーをスキップ
                    if len(row) >= 28:
                        source_url = row[3].strip()  # D列: 取得元URL
                        if source_url in url_set and source_url not in stock_status:
                            # AB列: 在庫状況（index 27）
                            stock_status[source_url] = row[27].strip() == "In Stock"

                if stock_status:
                    logger.info(f"カラーミー商品管理シートから在庫状況を{len(stock_status)}件取得")

                return stock_status
            except Exception:
                return {}
        except Exception as e:
            logger.error(f"在庫状況の取得エラー: {e}")
            return {}

    def get_latest_prices(self, urls: list[str]) -> dict[str, float]:
        """
        複数URLの直近価格を一括取得する（カラーミー商品管理シートから取得）

        Args:
            urls: 商品URLのリスト

        Returns:
            dict: URL -> 価格の辞書
        """
        if not self._spreadsheet:
            return {}

        try:
            url_set = set(urls)
            latest_prices = {}

            # カラーミー商品管理シートのM列（取得元価格）から取得
            try:
                sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
                records = sheet.get_all_values()

                for row in records[1:]:  # ヘッダーをスキップ
                    if len(row) >= 13:
                        source_url = row[3].strip()  # D列: 取得元URL
                        if source_url in url_set and source_url not in latest_prices:
                            # M列: 取得元価格（index 12）
                            if row[12].strip():
                                try:
                                    latest_prices[source_url] = float(row[12].strip())
                                except ValueError:
                                    continue

                if latest_prices:
                    logger.info(f"カラーミー商品管理シートから価格を{len(latest_prices)}件取得")
                    # 見つからないURLがある場合はフォールバック
                    if len(latest_prices) < len(url_set):
                        pass  # 下のフォールバック処理へ
                    else:
                        return latest_prices
            except Exception:
                pass  # カラーミー商品管理シートがない場合は価格履歴から取得

            # フォールバック: 価格履歴から取得
            sheet = self._spreadsheet.worksheet(Config.SHEET_HISTORY)
            records = sheet.get_all_values()

            # 最新のレコードから逆順に検索
            for row in reversed(records[1:]):
                if len(row) >= 9:
                    url = row[8]  # URL列（新しい形式）
                    if url in url_set and url not in latest_prices:
                        try:
                            latest_prices[url] = float(row[3])
                        except ValueError:
                            continue

                # 全URL分見つかったら終了
                if len(latest_prices) == len(url_set):
                    break

            return latest_prices
        except Exception as e:
            logger.error(f"直近価格の一括取得エラー: {e}")
            return {}

    def get_colorme_products(self) -> list:
        """
        カラーミー商品管理シートから商品リストを取得する

        シート列:
        A: カラーミー商品ID (0) - 入力
        B: 商品名 (1) - 入力
        C: カラーミー商品URL (2) - 自動生成
        D: 取得元URL (3) - 入力
        E: 枚数 (4) - 入力
        F: マージン率 (5) - 入力
        G: 固定マージン価格 (6) - 入力（新規追加）
        H: 価格更新 (7) - 入力 ON/OFF
        I: 在庫連動 (8) - 入力 ON/OFF
        J: 在庫数量 (9) - 入力
        K: 表示連動 (10) - 入力 連動/表示/非表示/変更しない
        L: 現在価格 (11) - 自動（カラーミーAPIから取得）
        M: 取得元価格 (12) - 自動
        N: 取得通貨 (13) - 入力 USD, SGD, EUR等
        O: 為替種類 (14) - 入力 クレカ, Wise
        P: 為替レート (15) - 自動（JPYの場合は1）
        Q: 本体計算価格 (16) - 自動（取得価格×為替×枚数）
        R: 送料 (17) - 入力
        S: 諸経費 (18) - 入力
        T: 販売価格 (19) - 手入力/数式（プログラムから更新しない）
        U: 原価（諸経費込み） (20) - ユーザー入力/数式（更新しない）
        V: 販売粗利 (21) - ユーザー入力/数式（更新しない）
        W: 販売粗利率 (22) - 数式（更新しない）
        X: 差額 (23) - ユーザー入力/数式（更新しない）
        Y: 最終更新 (24) - 自動

        Returns:
            list: ColorMeProduct のリスト
        """
        from .colorme import ColorMeProduct

        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return []

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            records = sheet.get_all_values()

            products = []
            for row in records[1:]:  # ヘッダーをスキップ
                if len(row) >= 4:
                    product_id = row[0].strip()
                    source_url = row[3].strip()  # D列: 取得元URL

                    # 商品IDとURLが両方ある場合のみ追加
                    if product_id and source_url:
                        try:
                            # カラーミー商品URL（C列、index 2）- 自動生成
                            colorme_url = f"https://ybx.jp/?pid={product_id}"

                            # 枚数（E列、index 4）
                            quantity = 1
                            if len(row) >= 5 and row[4].strip():
                                try:
                                    quantity = int(row[4].strip())
                                except ValueError:
                                    pass

                            # マージン率（F列、index 5）
                            margin_rate = 1.1
                            if len(row) >= 6 and row[5].strip():
                                try:
                                    margin_rate = float(row[5].strip())
                                except ValueError:
                                    pass

                            # 固定マージン価格（G列、index 6）- 新規追加
                            fixed_margin = 0
                            if len(row) >= 7 and row[6].strip():
                                try:
                                    fixed_margin = int(float(row[6].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # 価格更新（H列、index 7）
                            update_enabled = False
                            if len(row) >= 8 and row[7].strip().upper() == "ON":
                                update_enabled = True

                            # 在庫連動（I列、index 8）
                            stock_sync = False
                            if len(row) >= 9 and row[8].strip().upper() == "ON":
                                stock_sync = True

                            # 在庫数量（J列、index 9）
                            stock_quantity = 10
                            if len(row) >= 10 and row[9].strip():
                                try:
                                    stock_quantity = int(row[9].strip())
                                except ValueError:
                                    pass

                            # 表示連動（K列、index 10）
                            display_control = ""
                            if len(row) >= 11:
                                display_control = row[10].strip()

                            # 現在価格（L列、index 11）- 計算結果から読み取り
                            current_price = 0
                            if len(row) >= 12 and row[11].strip():
                                try:
                                    # カンマ区切りの数値に対応
                                    current_price = int(row[11].strip().replace(',', ''))
                                except ValueError:
                                    pass

                            # 取得通貨（N列、index 13）
                            source_currency = "USD"
                            if len(row) >= 14 and row[13].strip():
                                source_currency = row[13].strip().upper()

                            # 為替種類（O列、index 14）
                            exchange_type = "クレカ"
                            if len(row) >= 15 and row[14].strip():
                                exchange_type = row[14].strip()

                            # 送料（R列、index 17）
                            shipping_cost = 0
                            if len(row) >= 18 and row[17].strip():
                                try:
                                    shipping_cost = int(float(row[17].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # 諸経費（S列、index 18）
                            misc_cost = 0
                            if len(row) >= 19 and row[18].strip():
                                try:
                                    misc_cost = int(float(row[18].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # 販売適正価格（T列、index 19）- カラーミーに登録する価格
                            selling_price = 0
                            if len(row) >= 20 and row[19].strip():
                                try:
                                    selling_price = int(float(row[19].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # U列: 原価（index 20）
                            cost = 0
                            if len(row) >= 21 and row[20].strip():
                                try:
                                    cost = int(float(row[20].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # Z列: 前回価格（index 25）
                            previous_source_price = 0.0
                            if len(row) >= 26 and row[25].strip():
                                try:
                                    previous_source_price = float(row[25].strip())
                                except ValueError:
                                    pass

                            # AA列: 変動率（index 26）
                            source_change_rate = 0.0
                            if len(row) >= 27 and row[26].strip():
                                try:
                                    rate_str = row[26].strip().replace('%', '').replace('+', '')
                                    source_change_rate = float(rate_str)
                                except ValueError:
                                    pass

                            # AB列: 在庫状況（index 27）
                            source_stock_status = ""
                            if len(row) >= 28:
                                source_stock_status = row[27].strip()

                            # === 拡張列（AC列以降）===
                            # AC列: 同期モード（index 28）
                            sync_mode = ""
                            if len(row) >= 29:
                                sync_mode = row[28].strip()

                            # AD列: 型番（index 29）
                            model_number = ""
                            if len(row) >= 30:
                                model_number = row[29].strip()

                            # AE列: カテゴリーID（index 30）
                            category_id_big = 0
                            if len(row) >= 31 and row[30].strip():
                                try:
                                    category_id_big = int(row[30].strip())
                                except ValueError:
                                    pass

                            # AF列: サブカテゴリーID（index 31）
                            category_id_small = 0
                            if len(row) >= 32 and row[31].strip():
                                try:
                                    category_id_small = int(row[31].strip())
                                except ValueError:
                                    pass

                            # AG列: グループID（index 32）- カンマ区切り
                            group_ids = []
                            if len(row) >= 33 and row[32].strip():
                                try:
                                    group_ids = [int(g.strip()) for g in row[32].split(",") if g.strip().isdigit()]
                                except ValueError:
                                    pass

                            # AH列: 定価（index 33）
                            regular_price = 0
                            if len(row) >= 34 and row[33].strip():
                                try:
                                    regular_price = int(float(row[33].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # AI列: 会員価格（index 34）
                            members_price = 0
                            if len(row) >= 35 and row[34].strip():
                                try:
                                    members_price = int(float(row[34].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # AJ列: 個別送料（index 35）
                            delivery_charge = 0
                            if len(row) >= 36 and row[35].strip():
                                try:
                                    delivery_charge = int(float(row[35].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # AK列: 在庫管理（index 36）
                            stock_managed = True
                            if len(row) >= 37 and row[36].strip():
                                stock_managed = row[36].strip() != "しない"

                            # AL列: 売切れ表示（index 37）
                            soldout_display = True
                            if len(row) >= 38 and row[37].strip():
                                soldout_display = row[37].strip() != "非表示"

                            # AM列: 適正在庫数（index 38）
                            few_num = 0
                            if len(row) >= 39 and row[38].strip():
                                try:
                                    few_num = int(row[38].strip())
                                except ValueError:
                                    pass

                            # AN列: 最小購入数（index 39）
                            min_num = 1
                            if len(row) >= 40 and row[39].strip():
                                try:
                                    min_num = int(row[39].strip())
                                except ValueError:
                                    pass

                            # AO列: 最大購入数（index 40）
                            max_num = 0
                            if len(row) >= 41 and row[40].strip():
                                try:
                                    max_num = int(row[40].strip())
                                except ValueError:
                                    pass

                            # AP列: 商品説明（index 41）
                            expl = ""
                            if len(row) >= 42:
                                expl = row[41]

                            # AQ列: 簡易説明（index 42）
                            simple_expl = ""
                            if len(row) >= 43:
                                simple_expl = row[42]

                            # AR〜BA列: 画像URL1〜10（index 43〜52）
                            image_urls = []
                            for img_idx in range(43, 53):  # index 43〜52
                                if len(row) > img_idx and row[img_idx].strip():
                                    image_urls.append(row[img_idx].strip())
                                else:
                                    image_urls.append("")

                            # BB列: 同期ステータス（index 53）
                            sync_status = ""
                            if len(row) >= 54:
                                sync_status = row[53].strip()

                            # BC列: 同期日時（index 54）
                            sync_datetime = ""
                            if len(row) >= 55:
                                sync_datetime = row[54].strip()

                            products.append(ColorMeProduct(
                                product_id=int(product_id),
                                name=row[1].strip(),
                                current_price=current_price,
                                colorme_url=colorme_url,
                                source_url=source_url,
                                quantity=quantity,
                                margin_rate=margin_rate,
                                fixed_margin=fixed_margin,
                                update_enabled=update_enabled,
                                stock_sync=stock_sync,
                                stock_quantity=stock_quantity,
                                display_control=display_control,
                                source_currency=source_currency,
                                exchange_type=exchange_type,
                                shipping_cost=shipping_cost,
                                misc_cost=misc_cost,
                                selling_price=selling_price,
                                cost=cost,
                                previous_source_price=previous_source_price,
                                source_change_rate=source_change_rate,
                                source_stock_status=source_stock_status,
                                # 拡張フィールド
                                sync_mode=sync_mode,
                                model_number=model_number,
                                category_id_big=category_id_big,
                                category_id_small=category_id_small,
                                group_ids=group_ids,
                                regular_price=regular_price,
                                members_price=members_price,
                                delivery_charge=delivery_charge,
                                stock_managed=stock_managed,
                                soldout_display=soldout_display,
                                few_num=few_num,
                                min_num=min_num,
                                max_num=max_num,
                                expl=expl,
                                simple_expl=simple_expl,
                                image_urls=image_urls,
                                sync_status=sync_status,
                                sync_datetime=sync_datetime,
                            ))
                        except ValueError as e:
                            logger.warning(f"行のパースエラー: {row} - {e}")
                            continue

            logger.info(f"カラーミー商品を{len(products)}件取得しました")
            return products

        except Exception as e:
            logger.warning(f"カラーミー商品管理シートの取得エラー: {e}")
            return []

    def update_colorme_calc_results(self, results: list[dict], timestamp: str) -> bool:
        """
        カラーミー商品管理シートに計算結果を更新する

        更新する列:
        C: カラーミー商品URL（自動生成）
        L: 現在価格（カラーミーAPIから取得）
        M: 取得元価格
        P: 為替レート（JPYの場合は1）
        Q: 本体計算価格（取得価格×為替×枚数）
        Y: 最終更新
        Z: 外部-前回価格（今回の取得元価格を次回の前回価格として保存）
        AA: 外部-変動率
        AB: 外部-在庫状況

        更新しない列（入力項目・数式）:
        G: 固定マージン価格（入力）
        N: 取得通貨（入力）
        O: 為替種類（入力）
        R: 送料（入力）
        S: 諸経費（入力）
        T: 販売価格（手入力/数式）
        U: 原価（諸経費込み）（ユーザー入力/数式）
        V: 販売粗利（ユーザー入力/数式）
        W: 販売粗利率（数式）
        X: 差額（ユーザー入力/数式）

        Args:
            results: 計算結果のリスト（change_rate, in_stockを含む）
            timestamp: 更新日時

        Returns:
            bool: 保存成功時True
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        if not results:
            return True

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            all_data = sheet.get_all_values()

            # 商品ID -> 行番号のマッピング
            id_to_row = {}
            for i, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
                if len(row) >= 1 and row[0].strip():
                    try:
                        id_to_row[int(row[0].strip())] = i
                    except ValueError:
                        continue

            # 更新データを準備
            updates = []
            for r in results:
                row_num = id_to_row.get(r["product_id"])
                if row_num:
                    # C列: カラーミー商品URL（自動生成）
                    colorme_url = f"https://ybx.jp/?pid={r['product_id']}"
                    updates.append({
                        'range': f'C{row_num}',
                        'values': [[colorme_url]]
                    })
                    # L-M列: 現在価格, 取得元価格
                    # M列は小数点以下2桁を保持するため文字列でフォーマット
                    source_price = r["source_price"]
                    source_price_formatted = f"{source_price:.2f}" if isinstance(source_price, float) else source_price
                    updates.append({
                        'range': f'L{row_num}:M{row_num}',
                        'values': [[
                            r["colorme_price"],
                            source_price_formatted,
                        ]]
                    })
                    # P-Q列: 為替レート, 本体計算価格
                    # （N列「取得通貨」とO列「為替種類」はスキップ）
                    updates.append({
                        'range': f'P{row_num}:Q{row_num}',
                        'values': [[
                            r["exchange_rate"],
                            r["base_price"],  # Q列: 取得価格×為替×枚数
                        ]]
                    })
                    # T列: 販売価格 → ユーザーが手動で数式を入れるためスキップ
                    # （R列「送料」, S列「諸経費」はユーザー入力）
                    # U-X列: 原価、販売粗利、販売粗利率、差額 → ユーザーが手動で計算式を入れるためスキップ
                    # Y列: 最終更新
                    updates.append({
                        'range': f'Y{row_num}',
                        'values': [[timestamp]]
                    })
                    # Z-AB列: 前回価格, 変動率, 在庫状況（ダッシュボード統合）
                    change_rate = r.get("change_rate", 0)
                    in_stock = r.get("in_stock", True)
                    updates.append({
                        'range': f'Z{row_num}:AB{row_num}',
                        'values': [[
                            source_price_formatted,  # Z列: 今回の価格を次回の前回価格として保存
                            f"{change_rate:+.2f}%" if change_rate else "",  # AA列: 変動率
                            "In Stock" if in_stock else "Out of Stock"  # AB列: 在庫状況
                        ]]
                    })

            if updates:
                sheet.batch_update(updates, value_input_option='USER_ENTERED')
                logger.info(f"カラーミー商品管理シートを更新しました: {len(results)}件")

            return True

        except Exception as e:
            logger.error(f"カラーミー商品管理シートの更新エラー: {e}")
            return False

    def sync_colorme_products(self, api_products: list[dict]) -> dict:
        """
        カラーミーAPIから取得した商品一覧をシートに同期する

        - 新規商品は追加（AB列以降のカラーミー情報も含む）
        - 既存商品は商品名とAB列以降のカラーミー情報を更新
        - シートにあってAPIにない商品はそのまま（削除しない）

        Args:
            api_products: カラーミーAPIから取得した商品一覧

        Returns:
            dict: {"added": int, "updated": int, "unchanged": int}
        """
        from .colorme import ColorMeClient

        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return {"added": 0, "updated": 0, "unchanged": 0}

        result = {"added": 0, "updated": 0, "unchanged": 0}

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            all_data = sheet.get_all_values()

            # ヘッダーを確認し、AB列以降がなければ追加
            self.ensure_colorme_headers()

            # 既存の商品ID -> 行番号・商品名のマッピング
            existing_products = {}
            for i, row in enumerate(all_data[1:], start=2):  # ヘッダーをスキップ
                if len(row) >= 2 and row[0].strip():
                    try:
                        product_id = int(row[0].strip())
                        existing_products[product_id] = {
                            "row": i,
                            "name": row[1].strip() if len(row) > 1 else ""
                        }
                    except ValueError:
                        continue

            # ColorMeClientのヘルパーメソッドを使用
            colorme_client = ColorMeClient()

            # 更新・追加データを準備
            updates = []
            new_rows = []

            for product_data in api_products:
                product_id = product_data.get("id")
                product_name = product_data.get("name", "")

                if not product_id:
                    continue

                # APIレスポンスをColorMeProductに変換してAB列以降の情報を取得
                product = colorme_client._api_response_to_product(product_data)

                if product_id in existing_products:
                    # 既存商品：商品名とAC列以降を更新
                    existing = existing_products[product_id]
                    row_num = existing["row"]

                    # B列: 商品名を更新
                    if existing["name"] != product_name:
                        updates.append({
                            'range': f'B{row_num}',
                            'values': [[product_name]]
                        })

                    # AC列以降のカラーミー情報を更新（AD列から開始、AC列の同期モードは入力項目なのでスキップ）
                    # AD列: 型番
                    updates.append({
                        'range': f'AD{row_num}',
                        'values': [[product.model_number or ""]]
                    })

                    # AE-AF列: カテゴリーID, サブカテゴリーID
                    updates.append({
                        'range': f'AE{row_num}:AF{row_num}',
                        'values': [[
                            product.category_id_big if product.category_id_big > 0 else "",
                            product.category_id_small if product.category_id_small > 0 else ""
                        ]]
                    })

                    # AG列: グループID（カンマ区切り）
                    group_ids_str = ",".join(str(g) for g in product.group_ids) if product.group_ids else ""
                    updates.append({
                        'range': f'AG{row_num}',
                        'values': [[group_ids_str]]
                    })

                    # AH-AJ列: 定価, 会員価格, 個別送料
                    updates.append({
                        'range': f'AH{row_num}:AJ{row_num}',
                        'values': [[
                            product.regular_price if product.regular_price > 0 else "",
                            product.members_price if product.members_price > 0 else "",
                            product.delivery_charge if product.delivery_charge > 0 else ""
                        ]]
                    })

                    # AK-AL列: 在庫管理, 売切れ表示
                    updates.append({
                        'range': f'AK{row_num}:AL{row_num}',
                        'values': [[
                            "する" if product.stock_managed else "しない",
                            "表示" if product.soldout_display else "非表示"
                        ]]
                    })

                    # AM-AO列: 適正在庫数, 最小購入数, 最大購入数
                    updates.append({
                        'range': f'AM{row_num}:AO{row_num}',
                        'values': [[
                            product.few_num if product.few_num > 0 else "",
                            product.min_num if product.min_num > 0 else "",
                            product.max_num if product.max_num > 0 else ""
                        ]]
                    })

                    # AP-AQ列: 商品説明, 簡易説明
                    updates.append({
                        'range': f'AP{row_num}:AQ{row_num}',
                        'values': [[
                            product.expl or "",
                            product.simple_expl or ""
                        ]]
                    })

                    # AR-BA列: 画像URL1〜10（10列）
                    image_values = []
                    for i in range(10):
                        if i < len(product.image_urls):
                            image_values.append(product.image_urls[i] or "")
                        else:
                            image_values.append("")
                    updates.append({
                        'range': f'AR{row_num}:BA{row_num}',
                        'values': [image_values]
                    })

                    result["updated"] += 1
                    logger.info(f"更新: {product_id} - {product_name}")
                else:
                    # 新規商品：行を追加（AC列以降のカラーミー情報も含む）
                    # 行番号を計算（現在の最終行 + 追加済み行数 + 1）
                    new_row_num = len(all_data) + len(new_rows) + 1

                    # グループIDをカンマ区切り文字列に
                    group_ids_str = ",".join(str(g) for g in product.group_ids) if product.group_ids else ""
                    # 画像URL（10列分）
                    image_values = []
                    for i in range(10):
                        if i < len(product.image_urls):
                            image_values.append(product.image_urls[i] or "")
                        else:
                            image_values.append("")

                    new_row = [
                        str(product_id),   # A: カラーミー商品ID
                        product_name,      # B: 商品名
                        "",                # C: カラーミーURL（自動生成）
                        "",                # D: 取得元URL
                        "1",               # E: 枚数
                        "1.1",             # F: マージン率
                        "",                # G: 固定マージン価格（入力項目）
                        "OFF",             # H: 価格更新
                        "OFF",             # I: 在庫連動
                        "10",              # J: 在庫数量
                        "",                # K: 表示連動
                        "",                # L: 現在価格
                        "",                # M: 取得元価格
                        "USD",             # N: 取得通貨（デフォルト）
                        "クレカ",          # O: 為替種類（デフォルト）
                        "",                # P: 為替レート
                        "",                # Q: 本体計算価格
                        "",                # R: 送料
                        "",                # S: 諸経費
                        "",                # T: 販売価格（手入力）
                        "",                # U: 原価
                        "",                # V: 販売粗利
                        "",                # W: 販売粗利率
                        "",                # X: 差額
                        "",                # Y: 最終更新
                        "",                # Z: 前回価格
                        "",                # AA: 変動率
                        "",                # AB: 在庫状況
                        "",                # AC: 同期モード（入力項目）
                        product.model_number or "",  # AD: 型番
                        product.category_id_big if product.category_id_big > 0 else "",  # AE: カテゴリーID
                        product.category_id_small if product.category_id_small > 0 else "",  # AF: サブカテゴリーID
                        group_ids_str,     # AG: グループID
                        product.regular_price if product.regular_price > 0 else "",  # AH: 定価
                        product.members_price if product.members_price > 0 else "",  # AI: 会員価格
                        product.delivery_charge if product.delivery_charge > 0 else "",  # AJ: 個別送料
                        "する" if product.stock_managed else "しない",  # AK: 在庫管理
                        "表示" if product.soldout_display else "非表示",  # AL: 売切れ表示
                        product.few_num if product.few_num > 0 else "",  # AM: 適正在庫数
                        product.min_num if product.min_num > 0 else "",  # AN: 最小購入数
                        product.max_num if product.max_num > 0 else "",  # AO: 最大購入数
                        product.expl or "",  # AP: 商品説明
                        product.simple_expl or "",  # AQ: 簡易説明
                    ] + image_values + [   # AR〜BA: 画像URL1〜10
                        "",                # BB: 同期ステータス
                        "",                # BC: 同期日時
                    ]
                    new_rows.append(new_row)
                    result["added"] += 1
                    logger.info(f"追加: {product_id} - {product_name}")

            # 既存行の更新（バッチで効率化）
            if updates:
                # 50件ずつバッチ更新
                batch_size = 50
                for i in range(0, len(updates), batch_size):
                    batch = updates[i:i + batch_size]
                    sheet.batch_update(batch, value_input_option='RAW')

            # 新規行の追加
            if new_rows:
                sheet.append_rows(new_rows, value_input_option='USER_ENTERED')

            logger.info(
                f"カラーミー商品同期完了: "
                f"追加 {result['added']}件, "
                f"更新 {result['updated']}件, "
                f"変更なし {result['unchanged']}件"
            )

            return result

        except Exception as e:
            logger.error(f"カラーミー商品同期エラー: {e}")
            return result

    def update_dashboard(self, records: list[PriceRecord]) -> bool:
        """
        ダッシュボードシートを更新する（最新の価格のみ）

        Args:
            records: 価格レコードのリスト

        Returns:
            bool: 更新成功時True
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        if not records:
            return True

        try:
            # ダッシュボードシートを取得または作成
            try:
                sheet = self._spreadsheet.worksheet(Config.SHEET_DASHBOARD)
            except Exception:
                # シートがなければ作成
                sheet = self._spreadsheet.add_worksheet(
                    title=Config.SHEET_DASHBOARD,
                    rows=100,
                    cols=10
                )
                # ヘッダーを追加
                sheet.update('A1:I1', [[
                    '商品名', 'ショップ名', '現在価格', '通貨',
                    '前回価格', '変動率', '在庫状況', '最終更新', 'URL'
                ]])

            # 既存データを取得
            existing_data = sheet.get_all_values()
            url_to_row = {}
            for i, row in enumerate(existing_data[1:], start=2):  # ヘッダーをスキップ
                if len(row) >= 9:
                    url_to_row[row[8]] = i

            # 更新データを準備
            updates = []
            new_rows = []

            for record in records:
                row_data = [
                    record.product_name,
                    record.shop_name,
                    str(record.price),
                    record.currency,
                    str(record.previous_price) if record.previous_price else "",
                    f"{record.change_rate:+.2f}%" if record.change_rate else "",
                    "In Stock" if record.in_stock else "Out of Stock",
                    record.timestamp,
                    record.url
                ]

                if record.url in url_to_row:
                    # 既存行を更新
                    row_num = url_to_row[record.url]
                    updates.append({
                        'range': f'A{row_num}:I{row_num}',
                        'values': [row_data]
                    })
                else:
                    # 新規行を追加
                    new_rows.append(row_data)

            # バッチ更新
            if updates:
                sheet.batch_update(updates)

            # 新規行を追加
            if new_rows:
                sheet.append_rows(new_rows)

            logger.info(f"ダッシュボードを更新しました（更新: {len(updates)}件, 新規: {len(new_rows)}件）")
            return True

        except Exception as e:
            logger.error(f"ダッシュボード更新エラー: {e}")
            return False

    # ========================================
    # カラーミー商品管理シート拡張メソッド
    # ========================================

    def ensure_colorme_headers(self) -> bool:
        """
        カラーミー商品管理シートの拡張列ヘッダーを確認・追加する

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            header_row = sheet.row_values(1)

            # 既存のヘッダー数を確認
            existing_count = len(header_row)

            # AC列（index 28）から追加が必要か確認
            # ※G列（固定マージン価格）追加により、拡張列はAC列から開始
            if existing_count < 29:
                # ヘッダーが足りない場合は追加
                headers_to_add = Config.COLORME_EXTENDED_HEADERS
                start_col = existing_count + 1

                # 列番号をアルファベットに変換
                def col_to_letter(col_num):
                    result = ""
                    while col_num > 0:
                        col_num, remainder = divmod(col_num - 1, 26)
                        result = chr(65 + remainder) + result
                    return result

                # ヘッダーを追加
                start_letter = col_to_letter(start_col)
                end_letter = col_to_letter(start_col + len(headers_to_add) - 1)

                sheet.update(
                    f'{start_letter}1:{end_letter}1',
                    [headers_to_add],
                    value_input_option='RAW'
                )
                logger.info(f"カラーミー商品管理シートにヘッダーを追加: {start_letter}1:{end_letter}1")
            else:
                logger.info("カラーミー商品管理シートのヘッダーは既に存在します")

            return True

        except Exception as e:
            logger.error(f"ヘッダー追加エラー: {e}")
            return False

    def update_colorme_full_data(
        self,
        products: list,
        timestamp: str
    ) -> bool:
        """
        カラーミー商品管理シートの拡張列を更新する

        Args:
            products: ColorMeProductのリスト（APIから取得したデータ）
            timestamp: 更新日時

        Returns:
            bool: 成功時True
        """
        from .colorme import ColorMeProduct

        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        if not products:
            return True

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            all_data = sheet.get_all_values()

            # 商品ID -> 行番号のマッピング
            id_to_row = {}
            for i, row in enumerate(all_data[1:], start=2):
                if len(row) >= 1 and row[0].strip():
                    try:
                        id_to_row[int(row[0].strip())] = i
                    except ValueError:
                        continue

            updates = []

            for product in products:
                if not isinstance(product, ColorMeProduct):
                    continue

                row_num = id_to_row.get(product.product_id)
                if not row_num:
                    continue

                # 拡張列の更新（AC〜AU列）
                # AC列（同期モード）は入力なので更新しない
                # AD〜AS列を更新

                # AD列: 型番
                updates.append({
                    'range': f'AD{row_num}',
                    'values': [[product.model_number or ""]]
                })

                # AE-AF列: カテゴリーID, サブカテゴリーID
                updates.append({
                    'range': f'AE{row_num}:AF{row_num}',
                    'values': [[
                        product.category_id_big if product.category_id_big > 0 else "",
                        product.category_id_small if product.category_id_small > 0 else ""
                    ]]
                })

                # AG列: グループID（カンマ区切り）
                group_ids_str = ",".join(str(g) for g in product.group_ids) if product.group_ids else ""
                updates.append({
                    'range': f'AG{row_num}',
                    'values': [[group_ids_str]]
                })

                # AH-AJ列: 定価, 会員価格, 個別送料
                updates.append({
                    'range': f'AH{row_num}:AJ{row_num}',
                    'values': [[
                        product.regular_price if product.regular_price > 0 else "",
                        product.members_price if product.members_price > 0 else "",
                        product.delivery_charge if product.delivery_charge > 0 else ""
                    ]]
                })

                # AK-AL列: 在庫管理, 売切れ表示
                updates.append({
                    'range': f'AK{row_num}:AL{row_num}',
                    'values': [[
                        "する" if product.stock_managed else "しない",
                        "表示" if product.soldout_display else "非表示"
                    ]]
                })

                # AM-AO列: 適正在庫数, 最小購入数, 最大購入数
                updates.append({
                    'range': f'AM{row_num}:AO{row_num}',
                    'values': [[
                        product.few_num if product.few_num > 0 else "",
                        product.min_num if product.min_num > 0 else "",
                        product.max_num if product.max_num > 0 else ""
                    ]]
                })

                # AP-AQ列: 商品説明, 簡易説明
                updates.append({
                    'range': f'AP{row_num}:AQ{row_num}',
                    'values': [[
                        product.expl or "",
                        product.simple_expl or ""
                    ]]
                })

                # AR-BA列: 画像URL1〜10（10列）
                image_values = []
                for i in range(10):
                    if i < len(product.image_urls):
                        image_values.append(product.image_urls[i] or "")
                    else:
                        image_values.append("")
                updates.append({
                    'range': f'AR{row_num}:BA{row_num}',
                    'values': [image_values]
                })

                # BB-BC列: 同期ステータス, 同期日時
                updates.append({
                    'range': f'BB{row_num}:BC{row_num}',
                    'values': [[
                        product.sync_status or "成功",
                        timestamp
                    ]]
                })

            if updates:
                # バッチ更新（50件ずつ）
                batch_size = 50
                for i in range(0, len(updates), batch_size):
                    batch = updates[i:i + batch_size]
                    sheet.batch_update(batch, value_input_option='RAW')

                logger.info(f"カラーミー商品管理シート（拡張列）を更新しました: {len(products)}件")

            return True

        except Exception as e:
            logger.error(f"カラーミー商品管理シート（拡張列）の更新エラー: {e}")
            return False

    def update_colorme_sync_status(
        self,
        product_id: int,
        status: str,
        timestamp: str,
        new_product_id: int = None
    ) -> bool:
        """
        カラーミー商品の同期ステータスを更新する

        Args:
            product_id: 商品ID（シート上のA列の値、新規登録前は0の場合あり）
            status: ステータス（"成功", "エラー: xxx"）
            timestamp: 更新日時
            new_product_id: 新規登録時に取得した商品ID

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            all_data = sheet.get_all_values()

            # 商品IDで行を検索
            row_num = None
            for i, row in enumerate(all_data[1:], start=2):
                if len(row) >= 1:
                    cell_value = row[0].strip()
                    if cell_value:
                        try:
                            if int(cell_value) == product_id:
                                row_num = i
                                break
                        except ValueError:
                            continue

            if not row_num:
                return False

            updates = []

            # 新規登録で商品IDが付与された場合、A列を更新
            if new_product_id and new_product_id > 0:
                updates.append({
                    'range': f'A{row_num}',
                    'values': [[str(new_product_id)]]
                })

            # BB-BC列: 同期ステータス, 同期日時
            updates.append({
                'range': f'BB{row_num}:BC{row_num}',
                'values': [[status, timestamp]]
            })

            if updates:
                sheet.batch_update(updates, value_input_option='RAW')

            return True

        except Exception as e:
            logger.error(f"同期ステータス更新エラー: {e}")
            return False

    def add_colorme_product_row(
        self,
        product_data: dict,
        timestamp: str
    ) -> bool:
        """
        カラーミー商品管理シートに新規行を追加する

        Args:
            product_data: 商品データ
                - name: 商品名 (B列)
                - source_url: 取得元URL (D列)
                - source_price: 取得元価格 (M列)
                - currency: 取得通貨 (N列)
                - in_stock: 在庫状況 (AB列)
                - model_number: 型番 (AD列)
                - category_id_big: 大カテゴリーID (AE列)
                - category_id_small: 小カテゴリーID (AF列)
                - group_ids: グループID (AG列)
                - description: 商品説明 (AP列)
                - simple_description: 簡易説明 (AQ列)
                - image_urls: 画像URLリスト (AR〜BA列)
            timestamp: 更新日時

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)

            # 新規行のデータを作成（BC列まで = 55列）
            row = [""] * 55

            # A列: カラーミー商品ID（新規なので空）
            row[0] = ""
            # B列: 商品名
            row[1] = product_data.get("name", "")
            # C列: カラーミー商品URL（空）
            row[2] = ""
            # D列: 取得元URL
            row[3] = product_data.get("source_url", "")
            # E列: 枚数（デフォルト1）
            row[4] = "1"
            # F列: マージン率（デフォルト1.1）
            row[5] = "1.1"
            # G列: 固定マージン価格（空）
            row[6] = ""
            # H列: 価格更新（デフォルトOFF）
            row[7] = "FALSE"
            # I列: 在庫連動（デフォルトOFF）
            row[8] = "FALSE"
            # J列: 在庫数量（デフォルト1）
            row[9] = "1"
            # K列: 表示連動（デフォルト: 変更しない）
            row[10] = "変更しない"
            # L列: 現在価格（空）
            row[11] = ""
            # M列: 取得元価格
            row[12] = str(product_data.get("source_price", ""))
            # N列: 取得通貨
            row[13] = product_data.get("currency", "USD")
            # O列: 為替種類（デフォルト: クレカ）
            row[14] = "クレカ"
            # P列: 為替レート（空、自動計算）
            row[15] = ""
            # Q列: 本体計算価格（空、自動計算）
            row[16] = ""
            # R列: 送料（空、手入力）
            row[17] = ""
            # S列: 諸経費（空、手入力）
            row[18] = ""
            # T列: 販売価格（空、手入力）
            row[19] = ""
            # U〜X列: 各種計算（空）
            row[20] = ""
            row[21] = ""
            row[22] = ""
            row[23] = ""
            # Y列: 最終更新
            row[24] = timestamp
            # Z列: 外部-前回価格
            row[25] = ""
            # AA列: 外部-変動率
            row[26] = ""
            # AB列: 外部-在庫状況
            row[27] = "○" if product_data.get("in_stock") else "×"
            # AC列: 同期モード（新規登録）
            row[28] = "新規登録"
            # AD列: 型番
            row[29] = product_data.get("model_number", "")
            # AE列: カテゴリーID（大）
            row[30] = str(product_data.get("category_id_big", ""))
            # AF列: カテゴリーID（小）
            row[31] = str(product_data.get("category_id_small", ""))
            # AG列: グループID
            group_ids = product_data.get("group_ids", [])
            row[32] = ",".join(str(g) for g in group_ids) if group_ids else ""
            # AH列: 定価（空、手入力）
            row[33] = ""
            # AI列: 会員価格（空）
            row[34] = ""
            # AJ列: 個別送料（R列を参照する数式）
            row[35] = "=R:R"  # R列の値を参照
            # AK列: 在庫管理（デフォルト: する）
            row[36] = "する"
            # AL列: 売切れ表示（デフォルト: 表示する）
            row[37] = "表示する"
            # AM列: 適正在庫数（デフォルト1）
            row[38] = "1"
            # AN列: 最小購入数（デフォルト1）
            row[39] = "1"
            # AO列: 最大購入数（デフォルト99）
            row[40] = "99"
            # AP列: 商品説明
            row[41] = product_data.get("description", "")
            # AQ列: 簡易説明
            row[42] = product_data.get("simple_description", "")
            # AR〜BA列: 画像URL1〜10
            image_urls = product_data.get("image_urls", [])
            for i in range(10):
                if i < len(image_urls):
                    row[43 + i] = image_urls[i]
                else:
                    row[43 + i] = ""
            # BB列: 同期ステータス（空）
            row[53] = ""
            # BC列: 同期日時（空）
            row[54] = ""

            # 行を追加
            sheet.append_row(row, value_input_option='USER_ENTERED')
            logger.info(f"カラーミー商品管理シートに新規行を追加: {product_data.get('name', '')}")

            return True

        except Exception as e:
            logger.error(f"新規行追加エラー: {e}")
            return False

    def update_product_image_urls(
        self,
        product_id: int,
        image_urls: list[str]
    ) -> bool:
        """
        商品の画像URLを更新する（AR〜BA列）

        画像アップロード成功後に、カラーミーの画像URLで
        スプレッドシートを更新する。

        Args:
            product_id: 商品ID
            image_urls: 画像URLリスト（最大10枚）

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            all_data = sheet.get_all_values()

            # 商品IDで行を検索
            row_num = None
            for i, row in enumerate(all_data[1:], start=2):
                if len(row) >= 1 and row[0].strip():
                    try:
                        if int(row[0].strip()) == product_id:
                            row_num = i
                            break
                    except ValueError:
                        continue

            if not row_num:
                logger.warning(f"商品ID {product_id} が見つかりません")
                return False

            # AR〜BA列（index 43〜52、10列分）を更新
            # AR列 = 44番目の列 = 列番号でいうとAR
            image_values = []
            for i in range(10):
                if i < len(image_urls):
                    image_values.append(image_urls[i] or "")
                else:
                    image_values.append("")

            # AR〜BA列を更新
            sheet.update(
                f'AR{row_num}:BA{row_num}',
                [image_values],
                value_input_option='RAW'
            )

            logger.info(f"商品ID {product_id} の画像URL更新: {len(image_urls)}枚")
            return True

        except Exception as e:
            logger.error(f"画像URL更新エラー: {e}")
            return False

    def update_product_image_urls_batch(
        self,
        updates: list[tuple[int, list[str]]]
    ) -> int:
        """
        複数商品の画像URLを一括更新する

        Args:
            updates: (商品ID, 画像URLリスト)のリスト

        Returns:
            int: 更新成功件数
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return 0

        if not updates:
            return 0

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            all_data = sheet.get_all_values()

            # 商品ID -> 行番号のマッピング
            id_to_row = {}
            for i, row in enumerate(all_data[1:], start=2):
                if len(row) >= 1 and row[0].strip():
                    try:
                        id_to_row[int(row[0].strip())] = i
                    except ValueError:
                        continue

            # 更新データを準備
            batch_updates = []
            success_count = 0

            for product_id, image_urls in updates:
                row_num = id_to_row.get(product_id)
                if not row_num:
                    continue

                # 画像URLを10列分に整形
                image_values = []
                for i in range(10):
                    if i < len(image_urls):
                        image_values.append(image_urls[i] or "")
                    else:
                        image_values.append("")

                batch_updates.append({
                    'range': f'AR{row_num}:BA{row_num}',
                    'values': [image_values]
                })
                success_count += 1

            if batch_updates:
                # 50件ずつバッチ更新
                batch_size = 50
                for i in range(0, len(batch_updates), batch_size):
                    batch = batch_updates[i:i + batch_size]
                    sheet.batch_update(batch, value_input_option='RAW')

                logger.info(f"画像URL一括更新完了: {success_count}件")

            return success_count

        except Exception as e:
            logger.error(f"画像URL一括更新エラー: {e}")
            return 0

    # ========================================
    # 新シート構造（2025-12 新設計）
    # ========================================

    def _col_to_letter(self, col_num: int) -> str:
        """列番号をアルファベットに変換"""
        result = ""
        while col_num > 0:
            col_num, remainder = divmod(col_num - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def create_new_sheets(self) -> dict:
        """
        新しい3つのシートを作成し、スプレッドシートの左側に配置する

        Returns:
            dict: {"suppliers": bool, "initial": bool, "management": bool}
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return {"suppliers": False, "initial": False, "management": False}

        result = {"suppliers": False, "initial": False, "management": False}

        try:
            existing_sheets = [ws.title for ws in self._spreadsheet.worksheets()]

            # 1. 商品仕入れ先一覧シート
            if Config.SHEET_SUPPLIERS not in existing_sheets:
                sheet = self._spreadsheet.add_worksheet(
                    title=Config.SHEET_SUPPLIERS,
                    rows=1000,
                    cols=30
                )
                # ヘッダーを設定
                sheet.update('A1:W1', [Config.SUPPLIER_HEADERS], value_input_option='RAW')
                # ヘッダー行を固定
                sheet.freeze(rows=1)
                result["suppliers"] = True
                logger.info(f"シート作成: {Config.SHEET_SUPPLIERS}")
            else:
                result["suppliers"] = True
                logger.info(f"シート既存: {Config.SHEET_SUPPLIERS}")

            # 2. 新カラーミー商品初期登録一覧シート
            if Config.SHEET_COLORME_INITIAL not in existing_sheets:
                sheet = self._spreadsheet.add_worksheet(
                    title=Config.SHEET_COLORME_INITIAL,
                    rows=1000,
                    cols=90
                )
                # ヘッダーを設定（管理シート + 初期登録専用列）
                all_headers = Config.COLORME_V2_HEADERS + Config.COLORME_INITIAL_EXTRA_HEADERS
                end_col = self._col_to_letter(len(all_headers))
                sheet.update(f'A1:{end_col}1', [all_headers], value_input_option='RAW')
                # ヘッダー行を固定
                sheet.freeze(rows=1)
                result["initial"] = True
                logger.info(f"シート作成: {Config.SHEET_COLORME_INITIAL}")
            else:
                result["initial"] = True
                logger.info(f"シート既存: {Config.SHEET_COLORME_INITIAL}")

            # 3. 新カラーミー商品管理シート
            if Config.SHEET_COLORME_V2 not in existing_sheets:
                sheet = self._spreadsheet.add_worksheet(
                    title=Config.SHEET_COLORME_V2,
                    rows=1000,
                    cols=85
                )
                # ヘッダーを設定
                end_col = self._col_to_letter(len(Config.COLORME_V2_HEADERS))
                sheet.update(f'A1:{end_col}1', [Config.COLORME_V2_HEADERS], value_input_option='RAW')
                # ヘッダー行を固定
                sheet.freeze(rows=1)
                result["management"] = True
                logger.info(f"シート作成: {Config.SHEET_COLORME_V2}")
            else:
                result["management"] = True
                logger.info(f"シート既存: {Config.SHEET_COLORME_V2}")

            # シートを左側に移動（逆順で移動することで正しい順序になる）
            self._move_sheets_to_left()

            return result

        except Exception as e:
            logger.error(f"新シート作成エラー: {e}")
            return result

    def _move_sheets_to_left(self):
        """新しい3つのシートをスプレッドシートの左側に移動"""
        try:
            sheets = self._spreadsheet.worksheets()
            sheet_titles = [ws.title for ws in sheets]

            # 移動対象のシート（表示順）
            target_sheets = [
                Config.SHEET_SUPPLIERS,
                Config.SHEET_COLORME_INITIAL,
                Config.SHEET_COLORME_V2,
            ]

            # 各シートを左側に移動
            for i, title in enumerate(target_sheets):
                if title in sheet_titles:
                    sheet = self._spreadsheet.worksheet(title)
                    self._spreadsheet.reorder_worksheets([sheet] + [ws for ws in sheets if ws.title != title])
                    # シートリストを更新
                    sheets = self._spreadsheet.worksheets()

            logger.info("新シートを左側に移動しました")

        except Exception as e:
            logger.warning(f"シート移動エラー: {e}")

    def get_supplier_products(self) -> list[dict]:
        """
        商品仕入れ先一覧シートから商品リストを取得する

        Returns:
            list[dict]: 仕入れ先商品のリスト
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return []

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_SUPPLIERS)
            records = sheet.get_all_values()

            products = []
            for i, row in enumerate(records[1:], start=2):  # ヘッダーをスキップ
                if len(row) >= 3 and row[2].strip():  # C列（URL）がある場合
                    product = {
                        "row_num": i,
                        "supplier_product_id": row[0].strip() if len(row) > 0 else "",
                        "supplier_product_name": row[1].strip() if len(row) > 1 else "",
                        "supplier_product_url": row[2].strip() if len(row) > 2 else "",
                        "supplier_site": row[3].strip() if len(row) > 3 else "",
                        "top_category": row[4].strip() if len(row) > 4 else "",
                        "parent_category": row[5].strip() if len(row) > 5 else "",
                        "child_category": row[6].strip() if len(row) > 6 else "",
                        "location": row[7].strip() if len(row) > 7 else "",
                        "first_fetched": row[8].strip() if len(row) > 8 else "",
                        "group_id": row[9].strip() if len(row) > 9 else "",
                        "current_price": self._parse_float(row[10]) if len(row) > 10 else 0,
                        "currency": row[11].strip() if len(row) > 11 else "USD",
                        "stock_status": row[12].strip() if len(row) > 12 else "",
                        "exchange_type": row[13].strip() if len(row) > 13 else "",
                        "exchange_rate": self._parse_float(row[14]) if len(row) > 14 else 0,
                        "jpy_price": self._parse_float(row[15]) if len(row) > 15 else 0,
                        "last_updated": row[16].strip() if len(row) > 16 else "",
                        "previous_price": self._parse_float(row[17]) if len(row) > 17 else 0,
                        "change_rate": self._parse_float(row[18]) if len(row) > 18 else 0,
                        "adopted": row[19].strip() == "TRUE" if len(row) > 19 else False,
                        "adopted_reason": row[20].strip() if len(row) > 20 else "",
                        "colorme_product_id": row[21].strip() if len(row) > 21 else "",
                        "note": row[22].strip() if len(row) > 22 else "",
                    }
                    products.append(product)

            logger.info(f"仕入れ先商品を{len(products)}件取得しました")
            return products

        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"シート '{Config.SHEET_SUPPLIERS}' が見つかりません")
            return []
        except Exception as e:
            logger.error(f"仕入れ先商品の取得エラー: {e}")
            return []

    def _parse_float(self, value: str) -> float:
        """文字列を浮動小数点数に変換"""
        if not value:
            return 0.0
        try:
            # カンマ、%、+を除去
            cleaned = value.strip().replace(',', '').replace('%', '').replace('+', '')
            return float(cleaned)
        except ValueError:
            return 0.0

    def get_unregistered_suppliers(self) -> list[dict]:
        """
        カラーミー未登録の採用済み仕入れ先商品を取得する

        条件:
        - 採用フラグがTRUE
        - カラーミー商品IDが空

        Returns:
            list[dict]: 未登録の仕入れ先商品リスト
        """
        products = self.get_supplier_products()
        unregistered = [
            p for p in products
            if p["adopted"] and not p["colorme_product_id"]
        ]
        logger.info(f"未登録の採用済み商品: {len(unregistered)}件")
        return unregistered

    def add_initial_registration_row(self, data: dict) -> bool:
        """
        初期登録一覧シートに行を追加する

        Args:
            data: 登録データ（日本語キー）

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME_INITIAL)

            # 84列分のデータを作成
            row = [""] * 84

            # A-C: 識別情報
            row[0] = str(data.get("カラーミー商品ID", ""))  # A
            row[1] = data.get("商品名", "")  # B
            row[2] = data.get("カラーミー商品URL", "")  # C

            # D-K: 仕入れ先情報
            row[3] = data.get("仕入れ先商品URL", "")  # D
            row[4] = data.get("仕入れ先商品名", "")  # E
            row[5] = data.get("仕入れ先サイト", "")  # F
            row[6] = data.get("最上位カテゴリ", "")  # G
            row[7] = data.get("親カテゴリ", "")  # H
            row[8] = data.get("子カテゴリ", "")  # I
            row[9] = str(data.get("仕入れ先価格（現地通貨）", ""))  # J
            row[10] = data.get("取引通貨", "SGD")  # K

            # L-X: 価格計算
            row[11] = data.get("為替種類", "クレカ")  # L
            row[12] = str(data.get("為替レート", ""))  # M
            row[14] = str(data.get("枚数", 1))  # O
            row[16] = str(data.get("設定マージン率", 0.2))  # Q
            row[18] = str(data.get("送料", 0))  # S
            row[19] = str(data.get("手数料", 0))  # T

            # Y-AD: カラーミー価格情報
            row[24] = str(data.get("販売価格", ""))  # Y
            row[25] = str(data.get("定価", ""))  # Z
            row[26] = str(data.get("会員価格", ""))  # AA
            row[27] = str(data.get("原価", ""))  # AB

            # AE-AI: カテゴリー・グループ
            row[30] = str(data.get("大カテゴリーID", ""))  # AE
            row[31] = str(data.get("小カテゴリーID", ""))  # AF
            row[32] = data.get("グループID", "")  # AG
            row[33] = data.get("型番", "")  # AH
            row[34] = data.get("掲載設定", "showing")  # AI

            # AJ-AP: 在庫管理
            row[35] = str(data.get("在庫数", 10))  # AJ
            row[36] = data.get("在庫管理", "する")  # AK
            row[37] = str(data.get("残りわずか数", 3))  # AL
            row[38] = data.get("売切れ表示", "表示")  # AM
            row[39] = str(data.get("最小購入数", 1))  # AN
            row[40] = str(data.get("最大購入数", 0))  # AO
            row[41] = data.get("単位", "")  # AP

            # AQ-AT: 送料・配送
            row[42] = str(data.get("個別送料", 0))  # AQ
            row[43] = str(data.get("クール便料金", 0))  # AR
            row[44] = str(data.get("重量(g)", 0))  # AS
            row[45] = "TRUE" if data.get("配送不要") else ""  # AT

            # AU-AX: 商品説明
            row[46] = data.get("商品説明", "")  # AU
            row[47] = data.get("簡易説明", "")  # AV
            row[48] = data.get("スマホ説明", "")  # AW
            row[49] = data.get("備考", "")  # AX

            # AY-BH: 画像
            row[50] = data.get("メイン画像URL", "")  # AY
            row[51] = data.get("サムネイルURL", "")  # AZ
            row[52] = data.get("画像URL1", "")  # BA
            row[53] = data.get("画像URL2", "")  # BB
            row[54] = data.get("画像URL3", "")  # BC
            row[55] = data.get("画像URL4", "")  # BD
            row[56] = data.get("画像URL5", "")  # BE
            row[57] = data.get("画像URL6", "")  # BF
            row[58] = data.get("画像URL7", "")  # BG
            row[59] = data.get("画像URL8", "")  # BH

            # BI-BK: SEO項目
            row[60] = data.get("ページタイトル", "")  # BI
            row[61] = data.get("メタディスクリプション", "")  # BJ
            row[62] = data.get("メタキーワード", "")  # BK

            # BL-BP: フラグ・設定
            row[63] = "TRUE" if data.get("軽減税率対象") else ""  # BL
            row[64] = "TRUE" if data.get("デジタルコンテンツ") else ""  # BM
            row[65] = "TRUE" if data.get("定期購入") else ""  # BN
            row[66] = str(data.get("表示順", 0))  # BO
            row[67] = data.get("利用不可決済", "")  # BP

            # BQ-BR: 掲載期間
            row[68] = data.get("掲載開始日時", "")  # BQ
            row[69] = data.get("掲載終了日時", "")  # BR

            # BS-BX: 更新制御
            row[70] = data.get("価格更新ON/OFF", "ON")  # BS
            row[71] = data.get("在庫連動ON/OFF", "ON")  # BT
            row[72] = data.get("表示連動", "連動")  # BU
            row[73] = data.get("同期モード", "新規登録")  # BV
            row[74] = data.get("同期ステータス", "")  # BW
            row[75] = data.get("同期日時", "")  # BX

            # BY-CB: システム情報
            row[76] = data.get("商品作成日時", "")  # BY
            row[77] = data.get("商品更新日時", "")  # BZ
            row[78] = str(data.get("前回仕入れ価格", ""))  # CA
            row[79] = str(data.get("価格変動率", ""))  # CB

            # CC-CF: 初期登録専用列
            row[80] = data.get("登録ステータス", "未登録")  # CC
            row[81] = data.get("登録日時", "")  # CD
            row[82] = data.get("要確認フラグ", "")  # CE
            row[83] = data.get("確認メモ", "")  # CF

            sheet.append_row(row, value_input_option='USER_ENTERED')
            logger.info(f"初期登録一覧に追加: {data.get('商品名', '')}")
            return True

        except Exception as e:
            logger.error(f"初期登録一覧への追加エラー: {e}")
            return False

    def get_confirmed_registrations(self) -> list[dict]:
        """
        確認済みの初期登録一覧を取得する

        Returns:
            list[dict]: 確認済み（登録待ち）の商品リスト
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return []

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME_INITIAL)
            records = sheet.get_all_values()

            confirmed = []
            for i, row in enumerate(records[1:], start=2):  # ヘッダーをスキップ
                if len(row) >= 81 and row[80].strip() == "確認済":
                    # 行データを辞書に変換
                    product = self._parse_initial_registration_row(row, i)
                    confirmed.append(product)

            logger.info(f"確認済み商品: {len(confirmed)}件")
            return confirmed

        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"シート '{Config.SHEET_COLORME_INITIAL}' が見つかりません")
            return []
        except Exception as e:
            logger.error(f"確認済み商品の取得エラー: {e}")
            return []

    def _parse_initial_registration_row(self, row: list, row_num: int) -> dict:
        """初期登録一覧の行データを辞書に変換"""
        return {
            "_row_num": row_num,
            "カラーミー商品ID": row[0].strip() if len(row) > 0 else "",
            "商品名": row[1].strip() if len(row) > 1 else "",
            "カラーミー商品URL": row[2].strip() if len(row) > 2 else "",
            "仕入れ先商品URL": row[3].strip() if len(row) > 3 else "",
            "仕入れ先商品名": row[4].strip() if len(row) > 4 else "",
            "仕入れ先サイト": row[5].strip() if len(row) > 5 else "",
            "仕入れ先価格": self._parse_float(row[9]) if len(row) > 9 else 0,
            "取引通貨": row[10].strip() if len(row) > 10 else "USD",
            "為替種類": row[11].strip() if len(row) > 11 else "",
            "販売価格": self._parse_float(row[24]) if len(row) > 24 else 0,
            "定価": self._parse_float(row[25]) if len(row) > 25 else 0,
            "会員価格": self._parse_float(row[26]) if len(row) > 26 else 0,
            "原価": self._parse_float(row[27]) if len(row) > 27 else 0,
            "大カテゴリーID": int(row[30]) if len(row) > 30 and row[30].strip().isdigit() else 0,
            "小カテゴリーID": int(row[31]) if len(row) > 31 and row[31].strip().isdigit() else 0,
            "グループID": row[32].strip() if len(row) > 32 else "",
            "型番": row[33].strip() if len(row) > 33 else "",
            "掲載設定": row[34].strip() if len(row) > 34 else "showing",
            "在庫数": int(self._parse_float(row[35])) if len(row) > 35 else 10,
            "在庫管理": row[36].strip() if len(row) > 36 else "する",
            "残りわずか数": int(self._parse_float(row[37])) if len(row) > 37 else 3,
            "売切れ表示": row[38].strip() if len(row) > 38 else "表示",
            "最小購入数": int(self._parse_float(row[39])) if len(row) > 39 else 1,
            "最大購入数": int(self._parse_float(row[40])) if len(row) > 40 else 0,
            "個別送料": int(self._parse_float(row[42])) if len(row) > 42 else 0,
            "商品説明": row[46].strip() if len(row) > 46 else "",
            "簡易説明": row[47].strip() if len(row) > 47 else "",
            "画像URL1": row[52].strip() if len(row) > 52 else "",
            "画像URL2": row[53].strip() if len(row) > 53 else "",
            "画像URL3": row[54].strip() if len(row) > 54 else "",
            "画像URL4": row[55].strip() if len(row) > 55 else "",
            "画像URL5": row[56].strip() if len(row) > 56 else "",
            "画像URL6": row[57].strip() if len(row) > 57 else "",
            "画像URL7": row[58].strip() if len(row) > 58 else "",
            "画像URL8": row[59].strip() if len(row) > 59 else "",
            "登録ステータス": row[80].strip() if len(row) > 80 else "",
        }

    def update_initial_registration_status(
        self,
        row_num: int,
        status: str,
        colorme_id: int = None,
        timestamp: str = None
    ) -> bool:
        """
        初期登録一覧の登録ステータスを更新する

        Args:
            row_num: 行番号
            status: ステータス（登録済/エラー等）
            colorme_id: 登録されたカラーミー商品ID
            timestamp: 登録日時

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME_INITIAL)

            updates = []

            # A列: カラーミー商品ID
            if colorme_id:
                updates.append({
                    'range': f'A{row_num}',
                    'values': [[str(colorme_id)]]
                })

            # CC列: 登録ステータス
            updates.append({
                'range': f'CC{row_num}',
                'values': [[status]]
            })

            # CD列: 登録日時
            if timestamp:
                updates.append({
                    'range': f'CD{row_num}',
                    'values': [[timestamp]]
                })

            if updates:
                sheet.batch_update(updates, value_input_option='RAW')

            logger.info(f"初期登録ステータス更新: 行{row_num} -> {status}")
            return True

        except Exception as e:
            logger.error(f"初期登録ステータス更新エラー: {e}")
            return False

    def copy_to_colorme_management(self, row_data: dict) -> bool:
        """
        登録済み商品を新カラーミー商品管理シートにコピーする

        Args:
            row_data: 初期登録一覧の行データ

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME_V2)

            # 80列分のデータを作成（初期登録専用列は除く）
            row = [""] * 80

            # データをコピー
            row[0] = str(row_data.get("colorme_id", ""))  # A: カラーミー商品ID
            row[1] = row_data.get("name", "")  # B: 商品名
            row[3] = row_data.get("supplier_url", "")  # D: 仕入れ先商品URL
            row[4] = row_data.get("supplier_name", "")  # E: 仕入れ先商品名
            row[5] = row_data.get("supplier_site", "")  # F: 仕入れ先サイト
            row[9] = str(row_data.get("supplier_price", ""))  # J: 仕入れ先価格
            row[10] = row_data.get("currency", "USD")  # K: 取引通貨
            row[11] = row_data.get("exchange_type", "")  # L: 為替種類
            row[30] = str(row_data.get("category_id_big", ""))  # AE: 大カテゴリーID
            row[31] = str(row_data.get("category_id_small", ""))  # AF: 小カテゴリーID
            row[32] = row_data.get("group_ids", "")  # AG: グループID
            row[33] = row_data.get("model_number", "")  # AH: 型番
            row[50] = row_data.get("description", "")  # AU: 商品説明
            row[51] = row_data.get("simple_description", "")  # AV: 簡易説明

            # 画像URL
            image_urls = row_data.get("image_urls", [])
            for i, url in enumerate(image_urls[:8]):
                row[56 + i] = url  # BA-BH: 画像URL1-8

            # SEO項目
            row[60] = row_data.get("page_title", "")  # BI: ページタイトル
            row[61] = row_data.get("meta_description", "")  # BJ: メタディスクリプション
            row[62] = row_data.get("meta_keywords", "")  # BK: メタキーワード

            # 更新制御（デフォルト値）
            row[70] = "OFF"  # BS: 価格更新ON/OFF
            row[71] = "OFF"  # BT: 在庫連動ON/OFF
            row[72] = "変更しない"  # BU: 表示連動
            row[73] = "なし"  # BV: 同期モード

            sheet.append_row(row, value_input_option='USER_ENTERED')
            logger.info(f"商品管理シートにコピー: {row_data.get('name', '')}")
            return True

        except Exception as e:
            logger.error(f"商品管理シートへのコピーエラー: {e}")
            return False

    def update_supplier_colorme_id(self, supplier_url: str, colorme_id: int) -> bool:
        """
        仕入れ先一覧のカラーミー商品IDを更新する

        Args:
            supplier_url: 仕入れ先商品URL
            colorme_id: カラーミー商品ID

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_SUPPLIERS)
            all_data = sheet.get_all_values()

            # URLで行を検索
            for i, row in enumerate(all_data[1:], start=2):
                if len(row) >= 3 and row[2].strip() == supplier_url:
                    # V列（22列目）を更新
                    sheet.update(f'V{i}', [[str(colorme_id)]], value_input_option='RAW')
                    logger.info(f"仕入れ先一覧のカラーミーID更新: {supplier_url} -> {colorme_id}")
                    return True

            logger.warning(f"仕入れ先が見つかりません: {supplier_url}")
            return False

        except Exception as e:
            logger.error(f"仕入れ先カラーミーID更新エラー: {e}")
            return False

    def update_supplier_price(self, supplier_url: str, update_data: dict) -> bool:
        """
        仕入れ先一覧の価格情報を更新する

        Args:
            supplier_url: 仕入れ先商品URL
            update_data: 更新データ（キーはヘッダー名に対応）
                - 現在価格（現地通貨）
                - 取引通貨
                - 在庫状況
                - 為替種類
                - 為替レート
                - 日本円換算価格
                - 最終価格更新日時
                - 前回価格（現地通貨）
                - 価格変動率

        Returns:
            bool: 成功時True
        """
        if not self._spreadsheet:
            return False

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_SUPPLIERS)
            all_data = sheet.get_all_values()

            # URLで行を検索（C列 = index 2）
            for i, row in enumerate(all_data[1:], start=2):
                if len(row) >= 3 and row[2].strip() == supplier_url:
                    # 列マッピング（ヘッダー名 → 列記号）
                    column_map = {
                        "現在価格（現地通貨）": "K",
                        "取引通貨": "L",
                        "在庫状況": "M",
                        "為替種類": "N",
                        "為替レート": "O",
                        "日本円換算価格": "P",
                        "最終価格更新日時": "Q",
                        "前回価格（現地通貨）": "R",
                        "価格変動率": "S",
                    }

                    updates = []
                    for key, col in column_map.items():
                        if key in update_data:
                            value = update_data[key]
                            if value is not None and value != "":
                                updates.append({
                                    'range': f'{col}{i}',
                                    'values': [[str(value)]]
                                })

                    if updates:
                        sheet.batch_update(updates, value_input_option='USER_ENTERED')
                        logger.info(f"仕入れ先価格更新: {supplier_url}")
                        return True
                    else:
                        logger.warning(f"更新データがありません: {supplier_url}")
                        return True

            logger.warning(f"仕入れ先が見つかりません: {supplier_url}")
            return False

        except Exception as e:
            logger.error(f"仕入れ先価格更新エラー: {e}")
            return False
