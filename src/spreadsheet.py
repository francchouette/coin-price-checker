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
            self._client = gspread.authorize(credentials)
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
