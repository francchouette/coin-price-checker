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
    bullionstar_url: str
    quantity: int  # セット枚数
    margin_rate: float  # マージン率（1.1 = 10%）
    update_enabled: bool = False  # 価格更新ON/OFF


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

    def update_price(self, product_id: int, new_price: int) -> bool:
        """
        商品価格を更新する

        Args:
            product_id: 商品ID
            new_price: 新しい価格（円、整数）

        Returns:
            bool: 更新成功時True
        """
        if not self.access_token:
            logger.error("カラーミーアクセストークンが設定されていません")
            return False

        # ドライランモードの場合は実際に更新しない
        if self.dry_run:
            logger.info(f"[DRY RUN] 価格更新: 商品ID {product_id} → {new_price:,}円")
            return True

        try:
            response = requests.put(
                f"{self.API_BASE}/products/{product_id}.json",
                headers=self._headers(),
                json={"product": {"price": new_price}},
                timeout=10
            )
            response.raise_for_status()
            logger.info(f"価格更新成功: 商品ID {product_id} → {new_price:,}円")
            return True

        except requests.RequestException as e:
            logger.error(f"価格更新エラー (ID: {product_id}): {e}")
            return False

    def update_prices_batch(
        self,
        products: list[ColorMeProduct],
        bullionstar_prices: dict[str, float],
        exchange_rate: float
    ) -> dict:
        """
        複数商品の価格を一括更新する

        Args:
            products: カラーミー商品リスト
            bullionstar_prices: BullionstarURL -> USD価格 の辞書
            exchange_rate: USD/JPY為替レート

        Returns:
            dict: {"success": int, "failed": int, "skipped": int}
        """
        result = {"success": 0, "failed": 0, "skipped": 0}

        for product in products:
            # Bullionstar価格を取得
            usd_price = bullionstar_prices.get(product.bullionstar_url)

            if usd_price is None:
                logger.warning(
                    f"スキップ: {product.name} - Bullionstar価格なし"
                )
                result["skipped"] += 1
                continue

            # 新価格を計算: USD価格 × 為替レート × 枚数 × マージン率
            new_price = int(
                usd_price * exchange_rate * product.quantity * product.margin_rate
            )

            # 価格が変わらない場合はスキップ
            if new_price == product.current_price:
                logger.info(
                    f"スキップ: {product.name} - 価格変更なし ({new_price:,}円)"
                )
                result["skipped"] += 1
                continue

            # 商品個別の更新設定を確認
            if not product.update_enabled:
                logger.info(
                    f"[計算のみ] {product.name}: {product.current_price:,}円 → {new_price:,}円 (更新OFF)"
                )
                result["skipped"] += 1
                continue

            # 価格を更新
            logger.info(
                f"価格更新: {product.name} "
                f"({product.current_price:,}円 → {new_price:,}円)"
            )

            if self.update_price(product.product_id, new_price):
                result["success"] += 1
            else:
                result["failed"] += 1

        return result
