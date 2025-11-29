"""
カラーミーショップAPI連携モジュール

商品価格の更新を行う。
"""

import logging
from dataclasses import dataclass
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
    source_url: str  # 価格取得元URL（Bullionstar, APMEXなど）
    quantity: int  # セット枚数
    margin_rate: float  # マージン率（1.1 = 10%）
    update_enabled: bool = False  # 価格更新ON/OFF
    stock_sync: bool = False  # 在庫連動ON/OFF
    stock_quantity: int = 10  # 在庫あり時の数量
    display_control: str = ""  # 表示連動: "連動" = 在庫に連動, "表示" = 常に表示, "非表示" = 常に非表示, "変更しない" or 空 = 変更しない
    source_currency: str = "USD"  # 取得元の通貨
    exchange_type: str = "クレカ"  # 為替種類: "クレカ" or "Wise"


class ColorMeClient:
    """カラーミーショップAPIクライアント"""

    API_BASE = "https://api.shop-pro.jp/v1"

    def __init__(self, access_token: Optional[str] = None, dry_run: Optional[bool] = None):
        """
        Args:
            access_token: アクセストークン（省略時は環境変数から取得）
            dry_run: ドライランモード（省略時は環境変数から取得）
        """
        self.access_token = access_token or Config.COLORME_ACCESS_TOKEN
        self.dry_run = dry_run if dry_run is not None else Config.COLORME_DRY_RUN

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
            if product and "price" in product:
                prices[product_id] = int(product["price"])
        return prices

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

        # ドライランモードの場合は実際に更新しない
        if self.dry_run:
            logger.info(f"[DRY RUN] 商品更新: 商品ID {product_id} → {updates}")
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
        exchange_rates: dict[str, float]
    ) -> dict:
        """
        複数商品の価格・在庫・表示状態を一括更新する

        Args:
            products: カラーミー商品リスト
            source_prices: 取得元URL -> 価格 の辞書
            source_stock: 取得元URL -> 在庫状況(True=在庫あり) の辞書
            exchange_rates: "通貨_種類" -> 為替レート の辞書（例: "USD_クレカ": 155.0）

        Returns:
            dict: {
                "success": int,
                "failed": int,
                "skipped": int,
                "calc_results": list  # 計算結果のリスト
            }
        """
        result = {"success": 0, "failed": 0, "skipped": 0, "calc_results": []}

        # カラーミーから現在価格を取得
        product_ids = [p.product_id for p in products]
        logger.info("カラーミーから現在価格を取得中...")
        current_prices = self.get_current_prices(product_ids)
        logger.info(f"カラーミー価格取得完了: {len(current_prices)}件")

        for product in products:
            # 取得元サイトの価格と在庫を取得
            source_price = source_prices.get(product.source_url)
            is_in_stock = source_stock.get(product.source_url, True)

            if source_price is None:
                logger.warning(
                    f"スキップ: {product.name} - 取得元価格なし"
                )
                result["skipped"] += 1
                continue

            # カラーミーの現在価格
            colorme_current_price = current_prices.get(product.product_id, 0)

            # 商品ごとの為替レートを取得
            rate_key = f"{product.source_currency}_{product.exchange_type}"
            exchange_rate = exchange_rates.get(rate_key)

            if not exchange_rate:
                logger.warning(
                    f"スキップ: {product.name} - 為替レートなし ({rate_key})"
                )
                result["skipped"] += 1
                continue

            # 新価格を計算: 取得価格 × 為替レート × 枚数 × マージン率
            new_price = int(
                source_price * exchange_rate * product.quantity * product.margin_rate
            )

            # 価格差額
            price_diff = new_price - colorme_current_price

            # 価格情報をログ出力
            logger.info(
                f"[価格計算] {product.name}\n"
                f"    カラーミー現在価格: {colorme_current_price:,}円\n"
                f"    取得元価格: {product.source_currency} {source_price:,.2f}\n"
                f"    為替レート: {exchange_rate:.2f} ({product.exchange_type})\n"
                f"    計算価格（反映候補）: {new_price:,}円\n"
                f"    差額: {price_diff:+,}円"
            )

            # 更新内容を構築
            updates = {}
            log_parts = []

            # 価格更新
            if product.update_enabled and new_price != colorme_current_price:
                updates["price"] = new_price
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

            # 計算結果を記録
            calc_result = {
                "product_id": product.product_id,
                "product_name": product.name,
                "colorme_price": colorme_current_price,
                "exchange_type": product.exchange_type,
                "exchange_rate": exchange_rate,
                "source_price": source_price,
                "calculated_price": new_price,
                "price_diff": price_diff,
                "update_enabled": product.update_enabled,
                "updated": False
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
