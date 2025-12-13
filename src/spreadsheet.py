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

            # カラーミー商品管理シートのAA列（在庫状況）から取得
            try:
                sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
                records = sheet.get_all_values()

                for row in records[1:]:  # ヘッダーをスキップ
                    if len(row) >= 27:
                        source_url = row[3].strip()  # D列: 取得元URL
                        if source_url in url_set and source_url not in stock_status:
                            # AA列: 在庫状況（index 26）
                            stock_status[source_url] = row[26].strip() == "In Stock"

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

            # カラーミー商品管理シートのL列（取得元価格）から取得
            try:
                sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
                records = sheet.get_all_values()

                for row in records[1:]:  # ヘッダーをスキップ
                    if len(row) >= 12:
                        source_url = row[3].strip()  # D列: 取得元URL
                        if source_url in url_set and source_url not in latest_prices:
                            # L列: 取得元価格（index 11）
                            if row[11].strip():
                                try:
                                    latest_prices[source_url] = float(row[11].strip())
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
        G: 価格更新 (6) - 入力 ON/OFF
        H: 在庫連動 (7) - 入力 ON/OFF
        I: 在庫数量 (8) - 入力
        J: 表示連動 (9) - 入力 連動/表示/非表示/変更しない
        K: 現在価格 (10) - 自動（カラーミーAPIから取得）
        L: 取得元価格 (11) - 自動
        M: 取得通貨 (12) - 入力 USD, SGD, EUR等
        N: 為替種類 (13) - 入力 クレカ, Wise
        O: 為替レート (14) - 自動（JPYの場合は1）
        P: 本体計算価格 (15) - 自動（取得価格×為替×枚数）
        Q: 送料 (16) - 入力
        R: 諸経費 (17) - 入力
        S: 販売価格 (18) - 自動 = round(P×F+Q+R, -2)
        T: 原価（諸経費込み） (19) - ユーザー入力/数式（更新しない）
        U: 販売粗利 (20) - ユーザー入力/数式（更新しない）
        V: 販売粗利率 (21) - 数式（更新しない）
        W: 差額 (22) - ユーザー入力/数式（更新しない）
        X: 最終更新 (23) - 自動

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

                            # 価格更新（G列、index 6）
                            update_enabled = False
                            if len(row) >= 7 and row[6].strip().upper() == "ON":
                                update_enabled = True

                            # 在庫連動（H列、index 7）
                            stock_sync = False
                            if len(row) >= 8 and row[7].strip().upper() == "ON":
                                stock_sync = True

                            # 在庫数量（I列、index 8）
                            stock_quantity = 10
                            if len(row) >= 9 and row[8].strip():
                                try:
                                    stock_quantity = int(row[8].strip())
                                except ValueError:
                                    pass

                            # 表示連動（J列、index 9）
                            display_control = ""
                            if len(row) >= 10:
                                display_control = row[9].strip()

                            # 現在価格（K列、index 10）- 計算結果から読み取り
                            current_price = 0
                            if len(row) >= 11 and row[10].strip():
                                try:
                                    # カンマ区切りの数値に対応
                                    current_price = int(row[10].strip().replace(',', ''))
                                except ValueError:
                                    pass

                            # 取得通貨（M列、index 12）
                            source_currency = "USD"
                            if len(row) >= 13 and row[12].strip():
                                source_currency = row[12].strip().upper()

                            # 為替種類（N列、index 13）
                            exchange_type = "クレカ"
                            if len(row) >= 14 and row[13].strip():
                                exchange_type = row[13].strip()

                            # 送料（Q列、index 16）
                            shipping_cost = 0
                            if len(row) >= 17 and row[16].strip():
                                try:
                                    shipping_cost = int(float(row[16].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # 諸経費（R列、index 17）
                            misc_cost = 0
                            if len(row) >= 18 and row[17].strip():
                                try:
                                    misc_cost = int(float(row[17].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # T列: 原価（index 19）
                            cost = 0
                            if len(row) >= 20 and row[19].strip():
                                try:
                                    cost = int(float(row[19].strip().replace(',', '')))
                                except ValueError:
                                    pass

                            # Y列: 前回価格（index 24）
                            previous_source_price = 0.0
                            if len(row) >= 25 and row[24].strip():
                                try:
                                    previous_source_price = float(row[24].strip())
                                except ValueError:
                                    pass

                            # Z列: 変動率（index 25）
                            source_change_rate = 0.0
                            if len(row) >= 26 and row[25].strip():
                                try:
                                    rate_str = row[25].strip().replace('%', '').replace('+', '')
                                    source_change_rate = float(rate_str)
                                except ValueError:
                                    pass

                            # AA列: 在庫状況（index 26）
                            source_stock_status = ""
                            if len(row) >= 27:
                                source_stock_status = row[26].strip()

                            products.append(ColorMeProduct(
                                product_id=int(product_id),
                                name=row[1].strip(),
                                current_price=current_price,
                                colorme_url=colorme_url,
                                source_url=source_url,
                                quantity=quantity,
                                margin_rate=margin_rate,
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
                                source_stock_status=source_stock_status
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
        K: 現在価格（カラーミーAPIから取得）
        L: 取得元価格
        O: 為替レート（JPYの場合は1）
        P: 本体計算価格（取得価格×為替×枚数）
        S: 販売価格 = round(P×F+Q+R, -2)
        X: 最終更新
        Y: 外部-前回価格（今回の取得元価格を次回の前回価格として保存）
        Z: 外部-変動率
        AA: 外部-在庫状況

        更新しない列（入力項目・数式）:
        M: 取得通貨（入力）
        N: 為替種類（入力）
        Q: 送料（入力）
        R: 諸経費（入力）
        T: 原価（諸経費込み）（ユーザー入力/数式）
        U: 販売粗利（ユーザー入力/数式）
        V: 販売粗利率（数式）
        W: 差額（ユーザー入力/数式）

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
                    # K-L列: 現在価格, 取得元価格
                    updates.append({
                        'range': f'K{row_num}:L{row_num}',
                        'values': [[
                            r["colorme_price"],
                            r["source_price"],
                        ]]
                    })
                    # O-P列: 為替レート, 本体計算価格
                    # （M列「取得通貨」とN列「為替種類」はスキップ）
                    updates.append({
                        'range': f'O{row_num}:P{row_num}',
                        'values': [[
                            r["exchange_rate"],
                            r["base_price"],  # P列: 取得価格×為替×枚数
                        ]]
                    })
                    # S列: 販売価格 = round(P×F+Q+R, -2)
                    # （Q列「送料」, R列「諸経費」はユーザー入力）
                    updates.append({
                        'range': f'S{row_num}',
                        'values': [[r["selling_price"]]]
                    })
                    # T-U列: 原価（諸経費込み）, 販売粗利 → ユーザーが手動で計算式を入れるためスキップ
                    # W列: 差額 → ユーザー入力のためスキップ
                    # （V列「販売粗利率（数式）」もスキップ）
                    # X列: 最終更新
                    updates.append({
                        'range': f'X{row_num}',
                        'values': [[timestamp]]
                    })
                    # Y-AA列: 前回価格, 変動率, 在庫状況（ダッシュボード統合）
                    change_rate = r.get("change_rate", 0)
                    in_stock = r.get("in_stock", True)
                    updates.append({
                        'range': f'Y{row_num}:AA{row_num}',
                        'values': [[
                            r["source_price"],  # Y列: 今回の価格を次回の前回価格として保存
                            f"{change_rate:+.2f}%" if change_rate else "",  # Z列: 変動率
                            "In Stock" if in_stock else "Out of Stock"  # AA列: 在庫状況
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

        - 新規商品は追加
        - 既存商品は商品名を更新
        - シートにあってAPIにない商品はそのまま（削除しない）

        Args:
            api_products: カラーミーAPIから取得した商品一覧

        Returns:
            dict: {"added": int, "updated": int, "unchanged": int}
        """
        if not self._spreadsheet:
            logger.error("スプレッドシートに接続されていません")
            return {"added": 0, "updated": 0, "unchanged": 0}

        result = {"added": 0, "updated": 0, "unchanged": 0}

        try:
            sheet = self._spreadsheet.worksheet(Config.SHEET_COLORME)
            all_data = sheet.get_all_values()

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

            # 更新・追加データを準備
            updates = []
            new_rows = []

            for product in api_products:
                product_id = product.get("id")
                product_name = product.get("name", "")

                if not product_id:
                    continue

                if product_id in existing_products:
                    # 既存商品：商品名が変わっていれば更新
                    existing = existing_products[product_id]
                    if existing["name"] != product_name:
                        updates.append({
                            'range': f'B{existing["row"]}',
                            'values': [[product_name]]
                        })
                        result["updated"] += 1
                        logger.info(f"更新: {product_id} - {existing['name']} → {product_name}")
                    else:
                        result["unchanged"] += 1
                else:
                    # 新規商品：行を追加
                    # 行番号を計算（現在の最終行 + 追加済み行数 + 1）
                    new_row_num = len(all_data) + len(new_rows) + 1

                    new_rows.append([
                        str(product_id),  # A: カラーミー商品ID
                        product_name,      # B: 商品名
                        "",                # C: 取得元URL
                        "1",               # D: 枚数
                        "1.1",             # E: マージン率
                        "OFF",             # F: 価格更新
                        "OFF",             # G: 在庫連動
                        "10",              # H: 在庫数量
                        "",                # I: 表示連動
                        "",                # J: 現在価格
                        "",                # K: 取得元価格
                        "USD",             # L: 取得通貨（デフォルト）
                        "クレカ",           # M: 為替種類（デフォルト）
                        "",                # N: 為替レート
                        "",                # O: 本体計算価格
                        "",                # P: 送料
                        "",                # Q: 諸経費
                        f"=IFERROR(ROUND(O{new_row_num}*E{new_row_num}+P{new_row_num}+Q{new_row_num},-2),\"\")",  # R: 販売価格
                        f"=IFERROR(ROUND(O{new_row_num}+P{new_row_num}+Q{new_row_num},-2),\"\")",  # S: 原価（諸経費込み）
                        f"=IFERROR(R{new_row_num}-S{new_row_num},\"\")",  # T: 販売粗利
                        f"=IFERROR(T{new_row_num}/R{new_row_num},\"\")",  # U: 販売粗利率
                        "",                # V: 差額
                        ""                 # W: 最終更新
                    ])
                    result["added"] += 1
                    logger.info(f"追加: {product_id} - {product_name}")

            # 既存行の更新
            if updates:
                sheet.batch_update(updates)

            # 新規行の追加（USER_ENTEREDで数式を評価）
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
