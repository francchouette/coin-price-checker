"""
商品一覧クローラーモジュール

各サイトの商品一覧を取得するクローラーを提供する。
"""

from .britannia import BritanniaCrawler

__all__ = ["BritanniaCrawler"]
