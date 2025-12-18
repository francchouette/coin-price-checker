"""
カラーミーショップAPI連携モジュール

商品価格の更新を行う。
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class ColorMeProduct:
    """カラーミー商品のデータクラス"""
    product_id: int
    name: str
    current_price: int
    colorme_url: str  # カラーミー商品ページURL（C列）
    source_url: str  # 価格取得元URL（Bullionstar, APMEXなど）（D列）
    quantity: int  # セット枚数
    margin_rate: float  # マージン率（1.1 = 10%）
    fixed_margin: int = 0  # 固定マージン価格（G列）- 手入力
    update_enabled: bool = False  # 価格更新ON/OFF（H列）
    stock_sync: bool = False  # 在庫連動ON/OFF（I列）
    stock_quantity: int = 10  # 在庫あり時の数量（J列）
    display_control: str = ""  # 表示連動（K列）: "連動" = 在庫に連動, "表示" = 常に表示, "非表示" = 常に非表示, "変更しない" or 空 = 変更しない
    source_currency: str = "USD"  # 取得元の通貨（N列）
    exchange_type: str = "クレカ"  # 為替種類（O列）: "クレカ" or "Wise"
    shipping_cost: int = 0  # 送料（R列）
    misc_cost: int = 0  # 諸経費（S列）
    cost: int = 0  # 原価（U列）- カラーミーのcostフィールドに反映
    # 外部価格履歴用フィールド（Z-AB列）※G列追加により1列ずれ
    previous_source_price: float = 0.0  # Z列: 前回の取得元価格
    source_change_rate: float = 0.0  # AA列: 変動率
    source_stock_status: str = ""  # AB列: 在庫状況 "In Stock" / "Out of Stock"

    # === 拡張フィールド（AC列以降） ===  ※G列追加により1列ずれ
    sync_mode: str = ""  # AC列: 同期モード "取得のみ" / "更新" / "新規登録" / "なし"
    model_number: str = ""  # AD列: 型番
    category_id_big: int = 0  # AE列: カテゴリーID
    category_id_small: int = 0  # AF列: サブカテゴリーID
    group_ids: list[int] = field(default_factory=list)  # AG列: グループID（カンマ区切り）
    regular_price: int = 0  # AH列: 定価（APIのprice）
    members_price: int = 0  # AI列: 会員価格
    delivery_charge: int = 0  # AJ列: 個別送料
    stock_managed: bool = True  # AK列: 在庫管理 "する" / "しない"
    soldout_display: bool = True  # AL列: 売切れ表示 "表示" / "非表示"
    few_num: int = 0  # AM列: 適正在庫数
    min_num: int = 1  # AN列: 最小購入数
    max_num: int = 0  # AO列: 最大購入数（0=無制限）
    expl: str = ""  # AP列: 商品説明
    simple_expl: str = ""  # AQ列: 簡易説明
    # 画像URL（AR〜BA列: 最大10枚）
    image_urls: list[str] = field(default_factory=list)  # AR〜BA列: 画像URL1〜10
    sync_status: str = ""  # BB列: 同期ステータス
    sync_datetime: str = ""  # BC列: 同期日時


class ColorMeClient:
    """カラーミーショップAPIクライアント"""

    API_BASE = "https://api.shop-pro.jp/v1"

    def __init__(self, access_token: Optional[str] = None, dry_run: bool = False):
        """
        Args:
            access_token: アクセストークン（省略時は環境変数から取得）
            dry_run: ドライランモード（Trueの場合は実際に更新しない）
        """
        self.access_token = access_token or Config.COLORME_ACCESS_TOKEN
        self.dry_run = dry_run

    def _headers(self) -> dict:
        """APIリクエスト用ヘッダーを返す"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def get_product(self, product_id: int) -> Optional[dict]:
        """
        商品情報を取得する

        Args:
            product_id: 商品ID

        Returns:
            dict: 商品情報（取得失敗時はNone）
        """
        if not self.access_token:
            logger.error("カラーミーアクセストークンが設定されていません")
            return None

        try:
            response = requests.get(
                f"{self.API_BASE}/products/{product_id}.json",
                headers=self._headers(),
                timeout=10
            )
            response.raise_for_status()
            return response.json().get("product")

        except requests.RequestException as e:
            logger.error(f"商品取得エラー (ID: {product_id}): {e}")
            return None

    def get_categories(self) -> list[dict]:
        """
        カテゴリー一覧を取得する

        Returns:
            list: カテゴリー情報のリスト
            [
                {
                    "id_big": 大カテゴリーID,
                    "id_small": 小カテゴリーID,
                    "name_big": 大カテゴリー名,
                    "name_small": 小カテゴリー名
                },
                ...
            ]
        """
        if not self.access_token:
            logger.error("カラーミーアクセストークンが設定されていません")
            return []

        try:
            response = requests.get(
                f"{self.API_BASE}/categories.json",
                headers=self._headers(),
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            categories = []
            for cat in data.get("categories", []):
                categories.append({
                    "id_big": cat.get("id_big", 0),
                    "id_small": cat.get("id_small", 0),
                    "name_big": cat.get("name_big", ""),
                    "name_small": cat.get("name_small", "")
                })

            logger.info(f"カテゴリー取得完了: {len(categories)}件")
            return categories

        except requests.RequestException as e:
            logger.error(f"カテゴリー取得エラー: {e}")
            return []

    def get_groups(self) -> list[dict]:
        """
        グループ一覧を取得する

        Returns:
            list: グループ情報のリスト
        """
        if not self.access_token:
            logger.error("カラーミーアクセストークンが設定されていません")
            return []

        try:
            response = requests.get(
                f"{self.API_BASE}/groups.json",
                headers=self._headers(),
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            groups = []
            raw_groups = data.get("groups", [])

            # 最初のグループのフィールドをログ出力（デバッグ用）
            if raw_groups:
                logger.info(f"グループAPIレスポンス例: {list(raw_groups[0].keys())}")

            for grp in raw_groups:
                groups.append({
                    "id": grp.get("id", 0),
                    "name": grp.get("name", ""),
                    "parent_id": grp.get("parent_id") or grp.get("parent_group_id") or 0
                })

            logger.info(f"グループ取得完了: {len(groups)}件")
            return groups

        except requests.RequestException as e:
            logger.error(f"グループ取得エラー: {e}")
            return []

    def create_group(self, name: str, parent_id: int = 0) -> tuple[int, str]:
        """
        グループを新規作成する

        Args:
            name: グループ名
            parent_id: 親グループID（0の場合はルート）

        Returns:
            tuple[int, str]: (作成されたグループID, エラーメッセージ)
        """
        if not self.access_token:
            return 0, "アクセストークンが設定されていません"

        if not name:
            return 0, "グループ名が空です"

        try:
            group_data = {
                "name": name,
                "display_state": "showing"
            }
            if parent_id > 0:
                # カラーミーAPIでは parent_group_id を使用
                group_data["parent_group_id"] = parent_id

            logger.info(f"グループ作成リクエスト: {group_data}")
            response = requests.post(
                f"{self.API_BASE}/groups.json",
                headers=self._headers(),
                json={"group": group_data},
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            group_result = result.get("group", {})
            new_id = group_result.get("id", 0)
            returned_parent_id = group_result.get("parent_group_id")
            logger.info(f"グループ新規作成成功: {name} → ID: {new_id}, 親グループID: {returned_parent_id}")

            # 親IDが正しく設定されていない場合は警告
            if parent_id > 0 and returned_parent_id != parent_id:
                logger.warning(f"  親グループID不一致！リクエスト: {parent_id}, レスポンス: {returned_parent_id}")

            return new_id, ""

        except requests.RequestException as e:
            error_msg = str(e)
            logger.error(f"グループ作成エラー: {error_msg}")
            return 0, error_msg

    def get_current_prices(self, product_ids: list[int]) -> dict[int, int]:
        """
        複数商品の現在価格を取得する

        Args:
            product_ids: 商品IDのリスト

        Returns:
            dict: 商品ID -> 現在価格 の辞書
        """
        prices = {}
        for product_id in product_ids:
            product = self.get_product(product_id)
            if product:
                # デバッグ: 価格関連フィールドをログ出力（最初の3件のみ）
                if len(prices) < 3:
                    logger.info(
                        f"[API応答] 商品ID {product_id}: price={product.get('price')}, "
                        f"sales_price={product.get('sales_price')}, "
                        f"members_price={product.get('members_price')}"
                    )
                # 価格を取得（sales_priceを優先。これが消費者に表示される価格）
                price = product.get("price")
                sales_price = product.get("sales_price")

                # sales_priceを優先（これが消費者に表示される）
                # sales_priceが設定されていればそれを使用、なければpriceを使用
                if sales_price is not None and sales_price > 0:
                    actual_price = sales_price
                    logger.info(f"[価格選択] 商品ID {product_id}: sales_price {sales_price} を使用")
                else:
                    actual_price = price
                    logger.info(f"[価格選択] 商品ID {product_id}: price {price} を使用")
                if actual_price is not None:
                    try:
                        prices[product_id] = int(actual_price)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"価格変換エラー (ID: {product_id}): {actual_price} - {e}")
        return prices

    def get_all_products(self, limit: int = 100) -> list[dict]:
        """
        全商品一覧を取得する

        Args:
            limit: 取得件数上限

        Returns:
            list: 商品情報のリスト
        """
        if not self.access_token:
            logger.error("カラーミーアクセストークンが設定されていません")
            return []

        try:
            products = []
            offset = 0

            while True:
                response = requests.get(
                    f"{self.API_BASE}/products.json",
                    headers=self._headers(),
                    params={"limit": min(limit, 100), "offset": offset},
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()

                batch = data.get("products", [])
                if not batch:
                    break

                # 最初の商品の画像関連フィールドをログ出力（デバッグ用）
                if not products and batch:
                    first = batch[0]
                    img_fields = {k: v for k, v in first.items() if 'image' in k.lower() or 'img' in k.lower()}
                    logger.info(f"[APIデバッグ] 最初の商品の画像フィールド: {img_fields}")
                    logger.info(f"[APIデバッグ] 最初の商品の全フィールドキー: {list(first.keys())}")

                products.extend(batch)
                logger.info(f"商品取得: {len(products)}件")

                if len(batch) < 100:
                    break

                offset += len(batch)
                if len(products) >= limit:
                    break

            logger.info(f"全商品取得完了: {len(products)}件")
            return products

        except requests.RequestException as e:
            logger.error(f"商品一覧取得エラー: {e}")
            return []

    def update_product(self, product_id: int, updates: dict) -> bool:
        """
        商品情報を更新する

        Args:
            product_id: 商品ID
            updates: 更新する項目の辞書（price, stocks, display_stateなど）

        Returns:
            bool: 更新成功時True
        """
        if not self.access_token:
            logger.error("カラーミーアクセストークンが設定されていません")
            return False

        if not updates:
            return True

        # ドライランモードの場合は実際の更新をスキップ
        if self.dry_run:
            logger.info(f"[ドライラン] 商品更新スキップ: 商品ID {product_id} → {updates}")
            return True

        try:
            response = requests.put(
                f"{self.API_BASE}/products/{product_id}.json",
                headers=self._headers(),
                json={"product": updates},
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"商品更新成功: 商品ID {product_id} → {updates}")
            return True

        except requests.RequestException as e:
            logger.error(f"商品更新エラー (ID: {product_id}): {e}")
            return False

    def update_price(self, product_id: int, new_price: int) -> bool:
        """
        商品価格を更新する

        Args:
            product_id: 商品ID
            new_price: 新しい価格（円、整数）

        Returns:
            bool: 更新成功時True
        """
        return self.update_product(product_id, {"price": new_price})

    def update_products_batch(
        self,
        products: list[ColorMeProduct],
        source_prices: dict[str, float],
        source_stock: dict[str, bool],
        exchange_rates: dict[str, float],
        source_change_rates: dict[str, float] = None
    ) -> dict:
        """
        複数商品の価格・在庫・表示状態を一括更新する

        Args:
            products: カラーミー商品リスト
            source_prices: 取得元URL -> 価格 の辞書
            source_stock: 取得元URL -> 在庫状況(True=在庫あり) の辞書
            exchange_rates: "通貨_種類" -> 為替レート の辞書（例: "USD_クレカ": 155.0）
            source_change_rates: 取得元URL -> 変動率(%) の辞書（オプション）

        Returns:
            dict: {
                "success": int,
                "failed": int,
                "skipped": int,
                "calc_results": list  # 計算結果のリスト
            }
        """
        if source_change_rates is None:
            source_change_rates = {}
        result = {"success": 0, "failed": 0, "skipped": 0, "calc_results": []}

        # カラーミーから現在価格を取得
        product_ids = [p.product_id for p in products]
        logger.info("カラーミーから現在価格を取得中...")
        current_prices = self.get_current_prices(product_ids)
        logger.info(f"カラーミー価格取得完了: {len(current_prices)}件")

        for product in products:
            # 取得元サイトの価格と在庫と変動率を取得
            source_price = source_prices.get(product.source_url)
            is_in_stock = source_stock.get(product.source_url, True)
            change_rate = source_change_rates.get(product.source_url, 0.0)

            if source_price is None:
                logger.warning(
                    f"スキップ: {product.name} - 取得元価格なし"
                )
                result["skipped"] += 1
                continue

            # カラーミーの現在価格
            colorme_current_price = current_prices.get(product.product_id, 0)

            # JPYの場合は為替レート不要（1:1）
            if product.source_currency == "JPY":
                exchange_rate = 1.0
                exchange_rate_display = ""  # シートには空白を記録
            else:
                # 商品ごとの為替レートを取得
                rate_key = f"{product.source_currency}_{product.exchange_type}"
                exchange_rate = exchange_rates.get(rate_key)
                exchange_rate_display = exchange_rate

                if not exchange_rate:
                    logger.warning(
                        f"スキップ: {product.name} - 為替レートなし ({rate_key})"
                    )
                    result["skipped"] += 1
                    continue

            # O列相当: 取得価格 × 為替レート × 枚数
            base_price = source_price * exchange_rate * product.quantity

            # R列の計算式: round(O × E + P + Q, -2)
            # 販売価格 = round(本体価格 × マージン率 + 送料 + 諸経費, -2)
            new_price = round(
                base_price * product.margin_rate + product.shipping_cost + product.misc_cost,
                -2  # 100円単位で丸め
            )
            new_price = int(new_price)

            # 価格差額
            price_diff = new_price - colorme_current_price

            # 価格情報をログ出力
            if product.source_currency == "JPY":
                logger.info(
                    f"[価格計算] {product.name}\n"
                    f"    カラーミー現在価格: {colorme_current_price:,}円\n"
                    f"    取得元価格: {source_price:,.0f}円\n"
                    f"    本体価格(O列相当): {base_price:,.0f}円\n"
                    f"    マージン率: {product.margin_rate}, 送料: {product.shipping_cost:,}円, 諸経費: {product.misc_cost:,}円\n"
                    f"    販売価格(R列相当): {new_price:,}円\n"
                    f"    差額: {price_diff:+,}円"
                )
            else:
                logger.info(
                    f"[価格計算] {product.name}\n"
                    f"    カラーミー現在価格: {colorme_current_price:,}円\n"
                    f"    取得元価格: {product.source_currency} {source_price:,.2f}\n"
                    f"    為替レート: {exchange_rate:.2f} ({product.exchange_type})\n"
                    f"    本体価格(O列相当): {base_price:,.0f}円\n"
                    f"    マージン率: {product.margin_rate}, 送料: {product.shipping_cost:,}円, 諸経費: {product.misc_cost:,}円\n"
                    f"    販売価格(R列相当): {new_price:,}円\n"
                    f"    差額: {price_diff:+,}円"
                )

            # 更新内容を構築
            updates = {}
            log_parts = []

            # 価格更新（price, sales_price, costを更新）
            if product.update_enabled and new_price != colorme_current_price:
                updates["price"] = new_price
                updates["sales_price"] = new_price  # 特価も同じ値に更新
                # 原価をT列の値で更新（設定されている場合）
                if product.cost > 0:
                    updates["cost"] = product.cost
                log_parts.append(f"価格: {colorme_current_price:,}円 → {new_price:,}円")

            # 在庫更新
            if product.stock_sync:
                # Bullionstarの在庫に連動
                new_stock = product.stock_quantity if is_in_stock else 0
                updates["stocks"] = new_stock
                log_parts.append(f"在庫: {new_stock} ({'在庫あり' if is_in_stock else '在庫なし'}連動)")

            # 表示状態更新
            if product.display_control and product.display_control != "変更しない":
                if product.display_control == "連動":
                    # 在庫に連動（在庫あり=表示、なし=非表示）
                    display_state = "showing" if is_in_stock else "hidden"
                    display_label = "表示" if is_in_stock else "非表示"
                    updates["display_state"] = display_state
                    log_parts.append(f"表示: {display_label} (在庫連動)")
                elif product.display_control == "表示":
                    updates["display_state"] = "showing"
                    log_parts.append("表示: 表示")
                elif product.display_control == "非表示":
                    updates["display_state"] = "hidden"
                    log_parts.append("表示: 非表示")

            # 計算結果を記録（JPYの場合は為替レートを1に）
            calc_result = {
                "product_id": product.product_id,
                "product_name": product.name,
                "colorme_price": colorme_current_price,
                "exchange_type": product.exchange_type,
                "exchange_rate": 1 if product.source_currency == "JPY" else exchange_rate,
                "source_price": source_price,
                "source_currency": product.source_currency,
                "base_price": base_price,  # O列: 取得価格×為替×枚数
                "selling_price": new_price,  # R列: round(O×E+P+Q, -2)
                "update_enabled": product.update_enabled,
                "updated": False,
                "in_stock": is_in_stock,  # AA列: 在庫状況
                "change_rate": change_rate,  # Z列: 変動率
            }

            # 更新がない場合
            if not updates:
                if not product.update_enabled and new_price != colorme_current_price:
                    logger.info(f"    → 価格更新OFF")
                else:
                    logger.info(f"    → 変更なし")
                result["skipped"] += 1
                result["calc_results"].append(calc_result)
                continue

            # 更新実行
            log_msg = f"{product.name}: {', '.join(log_parts)}"
            logger.info(f"更新: {log_msg}")

            if self.update_product(product.product_id, updates):
                result["success"] += 1
                calc_result["updated"] = True
                # 価格更新成功時はK列に新価格を反映
                if "price" in updates:
                    calc_result["colorme_price"] = new_price
            else:
                result["failed"] += 1

            result["calc_results"].append(calc_result)

        return result

    def update_prices_batch(
        self,
        products: list[ColorMeProduct],
        bullionstar_prices: dict[str, float],
        exchange_rate: float
    ) -> dict:
        """
        複数商品の価格を一括更新する（後方互換性のため残す）
        """
        # 在庫情報なしで呼び出し
        return self.update_products_batch(
            products, bullionstar_prices, {}, exchange_rate
        )

    # ========================================
    # 商品管理拡張メソッド（全フィールド対応）
    # ========================================

    def get_product_full(self, product_id: int) -> Optional[ColorMeProduct]:
        """
        商品の全情報を取得してColorMeProductに変換する

        Args:
            product_id: 商品ID

        Returns:
            ColorMeProduct: 商品情報（取得失敗時はNone）
        """
        product_data = self.get_product(product_id)
        if not product_data:
            return None

        return self._api_response_to_product(product_data)

    def _api_response_to_product(self, data: dict) -> ColorMeProduct:
        """
        APIレスポンスをColorMeProductに変換する

        Args:
            data: APIからの商品データ

        Returns:
            ColorMeProduct: 変換された商品オブジェクト
        """
        product_id = data.get("id", 0)

        # カテゴリー情報
        category = data.get("category") or {}
        category_id_big = category.get("id_big", 0) if isinstance(category, dict) else 0
        category_id_small = category.get("id_small", 0) if isinstance(category, dict) else 0

        # グループID
        group_ids = data.get("group_ids") or []
        if isinstance(group_ids, str):
            group_ids = [int(g) for g in group_ids.split(",") if g.strip().isdigit()]

        # 画像URL（最大10枚）
        image_urls = []
        # メイン画像
        main_image = data.get("image_url") or ""
        if main_image:
            image_urls.append(main_image)
        # デバッグ: 画像関連フィールドを確認
        image_fields = {k: v for k, v in data.items() if 'image' in k.lower() or 'img' in k.lower()}
        if image_fields:
            logger.debug(f"[画像フィールド] 商品ID {product_id}: {image_fields}")
        # 追加画像（APIレスポンスは {src: URL, position: N, mobile: bool} の形式）
        other_images = data.get("images") or []
        if isinstance(other_images, list):
            for img in other_images:
                if isinstance(img, dict):
                    url = img.get("src") or img.get("url") or img.get("image_url") or ""
                    if url and url not in image_urls:
                        image_urls.append(url)
                elif isinstance(img, str) and img and img not in image_urls:
                    image_urls.append(img)
        # 10枚まで
        image_urls = image_urls[:10]

        # 在庫管理フラグ
        stock_managed = data.get("stock_managed")
        if stock_managed is None:
            stock_managed = True
        elif isinstance(stock_managed, str):
            stock_managed = stock_managed.lower() in ("true", "1", "yes")

        # 売切れ表示フラグ
        soldout_display = data.get("soldout_display")
        if soldout_display is None:
            soldout_display = True
        elif isinstance(soldout_display, str):
            soldout_display = soldout_display.lower() in ("true", "1", "yes", "showing")

        return ColorMeProduct(
            product_id=product_id,
            name=data.get("name", ""),
            current_price=data.get("sales_price") or data.get("price") or 0,
            colorme_url=f"https://ybx.jp/?pid={product_id}",
            source_url="",  # シートから読み込む
            quantity=1,  # シートから読み込む
            margin_rate=1.1,  # シートから読み込む
            model_number=data.get("model_number") or "",
            category_id_big=category_id_big,
            category_id_small=category_id_small,
            group_ids=group_ids,
            regular_price=data.get("price") or 0,
            members_price=data.get("members_price") or 0,
            cost=data.get("cost") or 0,
            delivery_charge=data.get("delivery_charge") or 0,
            stock_managed=stock_managed,
            stock_quantity=data.get("stocks") or 0,
            soldout_display=soldout_display,
            few_num=data.get("few_num") or 0,
            min_num=data.get("min_num") or 1,
            max_num=data.get("max_num") or 0,
            expl=data.get("expl") or "",
            simple_expl=data.get("simple_expl") or "",
            image_urls=image_urls,
            display_control="表示" if data.get("display_state") == "showing" else "非表示",
        )

    def get_all_products_full(self, limit: int = 1000) -> list[ColorMeProduct]:
        """
        全商品の完全情報を取得する

        Args:
            limit: 取得件数上限

        Returns:
            list[ColorMeProduct]: 商品リスト
        """
        products_data = self.get_all_products(limit)
        return [self._api_response_to_product(p) for p in products_data]

    def update_product_full(self, product: ColorMeProduct) -> tuple[bool, str]:
        """
        商品の全フィールドを更新する

        Args:
            product: 更新する商品情報

        Returns:
            tuple[bool, str]: (成功したか, エラーメッセージ)
        """
        if not self.access_token:
            return False, "アクセストークンが設定されていません"

        updates = {}

        # 基本情報
        if product.name:
            updates["name"] = product.name
        if product.model_number:
            updates["model_number"] = product.model_number

        # カテゴリー
        if product.category_id_big > 0:
            updates["category"] = {
                "id_big": product.category_id_big,
                "id_small": product.category_id_small or 0
            }

        # グループ
        if product.group_ids:
            updates["group_ids"] = product.group_ids

        # 価格情報
        if product.regular_price > 0:
            updates["price"] = product.regular_price
            updates["sales_price"] = product.regular_price
        if product.members_price > 0:
            updates["members_price"] = product.members_price
        if product.cost > 0:
            updates["cost"] = product.cost
        if product.delivery_charge >= 0:
            updates["delivery_charge"] = product.delivery_charge

        # 在庫情報
        updates["stock_managed"] = product.stock_managed
        if product.stock_quantity >= 0:
            updates["stocks"] = product.stock_quantity
        updates["soldout_display"] = "showing" if product.soldout_display else "hidden"
        if product.few_num >= 0:
            updates["few_num"] = product.few_num

        # 購入数量制限
        if product.min_num >= 1:
            updates["min_num"] = product.min_num
        if product.max_num >= 0:
            updates["max_num"] = product.max_num

        # 説明文
        if product.expl:
            updates["expl"] = product.expl
        if product.simple_expl:
            updates["simple_expl"] = product.simple_expl

        # 表示状態
        if product.display_control in ("表示", "showing"):
            updates["display_state"] = "showing"
        elif product.display_control in ("非表示", "hidden"):
            updates["display_state"] = "hidden"

        if not updates:
            return True, ""

        try:
            response = requests.put(
                f"{self.API_BASE}/products/{product.product_id}.json",
                headers=self._headers(),
                json={"product": updates},
                timeout=30
            )
            response.raise_for_status()
            logger.info(f"商品全項目更新成功: 商品ID {product.product_id}")
            return True, ""

        except requests.RequestException as e:
            error_msg = str(e)
            logger.error(f"商品全項目更新エラー (ID: {product.product_id}): {error_msg}")
            return False, error_msg

    def create_product(self, product: ColorMeProduct) -> tuple[int, str]:
        """
        新規商品を登録する

        Args:
            product: 登録する商品情報

        Returns:
            tuple[int, str]: (作成された商品ID, エラーメッセージ)
            商品ID=0の場合は失敗
        """
        if not self.access_token:
            return 0, "アクセストークンが設定されていません"

        # 必須項目チェック
        errors = []
        if not product.name:
            errors.append("商品名が未入力です")
        if product.regular_price <= 0:
            errors.append("価格が未入力です")
        if product.category_id_big <= 0:
            errors.append("カテゴリーIDが未入力です")
        if product.stock_quantity < 0:
            errors.append("在庫数が不正です")

        if errors:
            return 0, "バリデーションエラー: " + ", ".join(errors)

        # 登録データ作成
        product_data = {
            "name": product.name,
            "price": product.regular_price,
            "sales_price": product.regular_price,
            "category": {
                "id_big": product.category_id_big,
                "id_small": product.category_id_small or 0
            },
            "stocks": product.stock_quantity,
            "stock_managed": product.stock_managed,
            "display_state": "showing" if product.display_control in ("表示", "showing", "") else "hidden",
        }

        # オプション項目
        if product.model_number:
            product_data["model_number"] = product.model_number
        if product.group_ids:
            product_data["group_ids"] = product.group_ids
        if product.members_price > 0:
            product_data["members_price"] = product.members_price
        if product.cost > 0:
            product_data["cost"] = product.cost
        if product.delivery_charge > 0:
            product_data["delivery_charge"] = product.delivery_charge
        if product.few_num > 0:
            product_data["few_num"] = product.few_num
        if product.min_num > 1:
            product_data["min_num"] = product.min_num
        if product.max_num > 0:
            product_data["max_num"] = product.max_num
        if product.expl:
            product_data["expl"] = product.expl
            logger.info(f"  商品説明を設定: {len(product.expl)}文字")
        if product.simple_expl:
            product_data["simple_expl"] = product.simple_expl
            logger.info(f"  簡易説明を設定: {len(product.simple_expl)}文字")

        # デバッグ: 送信データの確認
        logger.debug(f"API送信データ: {list(product_data.keys())}")

        try:
            response = requests.post(
                f"{self.API_BASE}/products.json",
                headers=self._headers(),
                json={"product": product_data},
                timeout=30
            )
            response.raise_for_status()
            result = response.json()
            new_id = result.get("product", {}).get("id", 0)
            logger.info(f"商品新規登録成功: {product.name} → ID: {new_id}")
            return new_id, ""

        except requests.RequestException as e:
            error_msg = str(e)
            logger.error(f"商品新規登録エラー: {product.name} - {error_msg}")
            return 0, error_msg

    def _download_and_convert_image(self, image_url: str) -> tuple[bytes, str]:
        """
        画像をダウンロードし、必要に応じてJPEGに変換する

        カラーミーショップはjpg, jpeg, gif, pngのみサポート。
        WebPなど未対応形式はJPEGに変換する。

        Args:
            image_url: 画像のURL

        Returns:
            tuple[bytes, str]: (画像バイナリ, MIMEタイプ)
        """
        from io import BytesIO

        # 画像をダウンロード
        img_response = requests.get(image_url, timeout=60)
        img_response.raise_for_status()
        image_data = img_response.content

        # Content-Typeを確認
        content_type = img_response.headers.get("Content-Type", "").lower()
        url_lower = image_url.lower()

        # WebP形式の検出（Content-TypeまたはURL拡張子）
        is_webp = "webp" in content_type or url_lower.endswith(".webp")

        # サポートされている形式かチェック
        supported_types = ["image/jpeg", "image/jpg", "image/png", "image/gif"]
        is_supported = any(st in content_type for st in supported_types)

        if is_webp or not is_supported:
            # PILを使ってJPEGに変換
            try:
                from PIL import Image

                logger.info(f"  画像形式変換中: {content_type or 'unknown'} → JPEG")

                # 画像を読み込み
                img = Image.open(BytesIO(image_data))

                # RGBA（透過）の場合は白背景でRGBに変換
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')

                # JPEGとして保存
                output = BytesIO()
                img.save(output, format='JPEG', quality=90)
                image_data = output.getvalue()
                content_type = "image/jpeg"

                logger.info(f"  変換完了: {len(image_data):,} bytes")

            except ImportError:
                logger.warning("Pillowがインストールされていません。画像変換をスキップします。")
                # 変換できない場合はそのまま返す（エラーになる可能性あり）
            except Exception as e:
                logger.error(f"  画像変換エラー: {e}")
                raise

        return image_data, content_type

    def _image_to_base64_data_uri(self, image_data: bytes, content_type: str) -> str:
        """
        画像バイナリをBase64 Data URI形式に変換する

        Args:
            image_data: 画像バイナリ
            content_type: MIMEタイプ

        Returns:
            str: Data URI形式の文字列 (例: "data:image/jpeg;base64,/9j/4AAQ...")
        """
        import base64

        # MIMEタイプを正規化
        if "jpeg" in content_type or "jpg" in content_type:
            mime_type = "image/jpeg"
        elif "png" in content_type:
            mime_type = "image/png"
        elif "gif" in content_type:
            mime_type = "image/gif"
        else:
            mime_type = "image/jpeg"  # デフォルト

        # Base64エンコード
        b64_data = base64.b64encode(image_data).decode('utf-8')

        return f"data:{mime_type};base64,{b64_data}"

    def upload_product_images(
        self,
        product_id: int,
        image_urls: list[str],
        initial_delay: float = 3.0,
        max_retries: int = 3
    ) -> tuple[bool, str]:
        """
        商品画像をBase64形式でアップロードする

        カラーミーショップAPIでは、商品更新API（PUT）のimages配列に
        Base64 Data URI形式で画像を指定することで画像を登録できる。

        Args:
            product_id: 商品ID
            image_urls: 画像URLリスト（最大50枚、1枚目がメイン画像）
            initial_delay: 最初の画像アップロード前の待機時間（秒）
            max_retries: エラー時のリトライ回数

        Returns:
            tuple[bool, str]: (成功したか, エラーメッセージ)
        """
        import time

        if not self.access_token:
            return False, "アクセストークンが設定されていません"

        if not image_urls:
            return True, ""

        # 商品登録直後は少し待機
        if initial_delay > 0:
            logger.info(f"画像アップロード前に{initial_delay}秒待機...")
            time.sleep(initial_delay)

        # 画像をダウンロードしてBase64に変換
        images_data = []
        errors = []

        for i, url in enumerate(image_urls[:50]):  # 最大50枚（レギュラープラン以上）
            if not url:
                continue

            logger.info(f"  画像 {i+1}/{len(image_urls)}: {url[:80]}...")

            try:
                # 画像をダウンロードして変換
                image_data, content_type = self._download_and_convert_image(url)

                # サイズチェック（5MB制限）
                if len(image_data) > 5 * 1024 * 1024:
                    errors.append(f"画像{i+1}: サイズが5MBを超えています ({len(image_data):,} bytes)")
                    continue

                # Base64 Data URIに変換
                data_uri = self._image_to_base64_data_uri(image_data, content_type)

                images_data.append({"src": data_uri})
                logger.info(f"    → 変換成功 ({len(image_data):,} bytes)")

            except Exception as e:
                error_msg = str(e)
                errors.append(f"画像{i+1}: {error_msg}")
                logger.error(f"    → エラー: {error_msg}")

        if not images_data:
            if errors:
                return False, "; ".join(errors)
            return True, ""

        # 商品更新APIで画像を登録
        logger.info(f"カラーミーAPIに{len(images_data)}枚の画像を登録中...")

        for retry in range(max_retries):
            try:
                response = requests.put(
                    f"{self.API_BASE}/products/{product_id}.json",
                    headers=self._headers(),
                    json={"product": {"images": images_data}},
                    timeout=120  # 大きな画像データのため長めのタイムアウト
                )
                response.raise_for_status()

                logger.info(f"画像登録成功: 商品ID {product_id} ({len(images_data)}枚)")

                if errors:
                    # 一部成功の場合
                    return True, f"一部エラー: {'; '.join(errors)}"
                return True, ""

            except requests.RequestException as e:
                error_msg = str(e)
                if retry < max_retries - 1:
                    wait_time = 5 * (retry + 1)
                    logger.warning(f"    リトライ {retry + 1}/{max_retries}: {wait_time}秒待機")
                    time.sleep(wait_time)
                else:
                    logger.error(f"画像登録失敗: 商品ID {product_id} - {error_msg}")
                    return False, error_msg

        return False, "リトライ上限に達しました"

    def upload_product_image(
        self,
        product_id: int,
        image_url: str,
        is_main: bool = True
    ) -> tuple[bool, str]:
        """
        単一の商品画像をアップロードする（後方互換性のため残す）

        Args:
            product_id: 商品ID
            image_url: 画像のURL
            is_main: メイン画像かどうか（現在は使用されない）

        Returns:
            tuple[bool, str]: (成功したか, エラーメッセージ)
        """
        if not image_url:
            return True, ""

        return self.upload_product_images(product_id, [image_url])
