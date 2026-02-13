"""
新カラーミー商品管理シートの列定義（77列: A-BY）

全てのカラーミー商品管理スクリプトはこのファイルから列情報をインポートする。
列構造が変更された場合、このファイルのみ修正すればOK。

使用例:
    from src.cm_sheet_columns import Col, get_cell, cell_ref

    # データ読み取り
    product_id = get_cell(row, Col.PRODUCT_ID)

    # スプレッドシート更新時の範囲指定
    batch_data.append({
        'range': cell_ref(Col.SYNC_STATUS, row_idx),
        'values': [["同期済み"]]
    })

    # 数式内での列参照
    formula = f'={Col.PROPER_PRICE.letter}{row_num}'

変更履歴:
    - 2024-XX: 初版（89列: A-CK）
    - 2024-XX: 77列に削減（A-BY）
      - 削除: M-T列（最上位カテゴリ〜発行数・限定数）8列
      - 削除: AU-AV列（小カテゴリーID・小カテゴリー名）2列
      - 削除: CJ-CK列（商品作成日時・商品更新日時）2列
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Column:
    """列情報を保持するイミュータブルなデータクラス"""
    index: int      # 0-based インデックス（row[index]で使用）
    letter: str     # 列文字 (A, B, ..., BY)（数式・範囲指定で使用）
    name: str       # 列名（ドキュメント・ログ用）


def _index_to_letter(index: int) -> str:
    """0-based インデックスを列文字に変換 (0->A, 25->Z, 26->AA, etc.)"""
    result = ""
    index += 1  # 1-based に変換
    while index > 0:
        index -= 1
        result = chr(ord('A') + index % 26) + result
        index //= 26
    return result


def _col(index: int, name: str) -> Column:
    """インデックスと名前からColumnを生成（列文字は自動計算）"""
    return Column(index=index, letter=_index_to_letter(index), name=name)


class Col:
    """
    新カラーミー商品管理シートの全77列定義（A-BY）

    使用方法:
        Col.SYNC_MODE.index  -> 0
        Col.SYNC_MODE.letter -> "A"
        Col.SYNC_MODE.name   -> "同期モード"
    """

    # === A-F列: 操作項目（6列）===
    SYNC_MODE = _col(0, "同期モード")
    DISPLAY_SETTING = _col(1, "掲載設定")
    PRICE_UPDATE = _col(2, "価格更新ON/OFF")
    STOCK_SYNC = _col(3, "在庫連動ON/OFF")
    DISPLAY_SYNC = _col(4, "在庫によっての掲載連動")
    SYNC_STATUS = _col(5, "同期ステータス")

    # === G-I列: 識別情報（3列）===
    PRODUCT_ID = _col(6, "カラーミー商品ID")
    NAME = _col(7, "商品名")
    COLORME_URL = _col(8, "カラーミー商品URL")

    # === J-L列: 仕入れ先基本情報（3列）===
    # ※M-T列（最上位カテゴリ〜発行数・限定数）8列を削除
    SUPPLIER_URL = _col(9, "仕入れ先商品URL")
    SUPPLIER_NAME = _col(10, "仕入れ先商品名")
    SUPPLIER_SITE = _col(11, "仕入れ先サイト")

    # === M-Q列: 仕入れ先価格情報（5列）===
    # ※旧U-Y列から移動（インデックス調整: -8）
    SUPPLIER_STOCK = _col(12, "仕入れ先在庫状況")
    SUPPLIER_PRICE = _col(13, "仕入れ先価格（現地通貨）")
    PREV_PRICE = _col(14, "前回仕入れ価格")
    PRICE_CHANGE_RATE = _col(15, "価格変動率")
    CURRENCY = _col(16, "取引通貨")

    # === R-AD列: 価格計算（13列）===
    # ※旧Z-AL列から移動（インデックス調整: -8）
    EXCHANGE_TYPE = _col(17, "為替種類")
    EXCHANGE_RATE = _col(18, "為替レート")
    PURCHASE_PRICE_JPY = _col(19, "仕入れ額(日本円)")
    QUANTITY = _col(20, "枚数")
    PURCHASE_TOTAL = _col(21, "仕入れ合計")
    MARGIN_RATE = _col(22, "設定マージン率")
    MARGIN_AMOUNT = _col(23, "設定マージン額")
    SHIPPING = _col(24, "送料")
    FEE = _col(25, "諸経費")
    TOTAL_COST = _col(26, "合計原価")
    PROPER_PRICE = _col(27, "適正価格")
    GROSS_PROFIT = _col(28, "粗利額")
    GROSS_PROFIT_RATE = _col(29, "粗利率")

    # === AE-AJ列: カラーミー価格情報（6列）===
    # ※旧AM-AR列から移動（インデックス調整: -8）
    SALES_PRICE = _col(30, "販売価格")
    REGULAR_PRICE = _col(31, "定価")
    MEMBERS_PRICE = _col(32, "会員価格")
    COST = _col(33, "原価")
    TAX_INCLUDED_PRICE = _col(34, "消費税込販売価格")
    TAX_AMOUNT = _col(35, "消費税額")

    # === AK-AN列: カテゴリー・グループ（4列）===
    # ※旧AS-AX列から移動（インデックス調整: -8）
    # ※小カテゴリーID・小カテゴリー名（2列）を削除
    CATEGORY_ID_BIG = _col(36, "大カテゴリーID")
    CATEGORY_NAME_BIG = _col(37, "大カテゴリー名称")
    GROUP_IDS = _col(38, "グループID")
    GROUP_NAMES = _col(39, "グループ名")

    # === AO列: 型番（1列）===
    # ※旧AY列から移動（インデックス調整: -10）
    MODEL_NUMBER = _col(40, "型番")

    # === AP-AV列: 在庫管理（7列）===
    # ※旧AZ-BF列から移動（インデックス調整: -10）
    STOCKS = _col(41, "在庫数")
    STOCK_MANAGED = _col(42, "在庫管理")
    FEW_NUM = _col(43, "残りわずか数")
    SOLDOUT_DISPLAY = _col(44, "売切れ表示")
    MIN_NUM = _col(45, "最小購入数")
    MAX_NUM = _col(46, "最大購入数")
    UNIT = _col(47, "単位")

    # === AW-AZ列: 送料・配送（4列）===
    # ※旧BG-BJ列から移動（インデックス調整: -10）
    DELIVERY_CHARGE = _col(48, "個別送料")
    COOL_CHARGE = _col(49, "クール便料金")
    WEIGHT = _col(50, "重量(g)")
    NO_DELIVERY = _col(51, "配送不要")

    # === BA-BD列: 商品説明（4列）===
    # ※旧BK-BN列から移動（インデックス調整: -10）
    EXPL = _col(52, "商品説明")
    SIMPLE_EXPL = _col(53, "簡易説明")
    MOBILE_EXPL = _col(54, "スマホ説明")
    MEMO = _col(55, "備考")

    # === BE-BN列: 画像（10列）===
    # ※旧BO-BX列から移動（インデックス調整: -10）
    MAIN_IMAGE = _col(56, "メイン画像URL")
    THUMBNAIL = _col(57, "サムネイルURL")
    IMAGE_URL_1 = _col(58, "画像URL1")
    IMAGE_URL_2 = _col(59, "画像URL2")
    IMAGE_URL_3 = _col(60, "画像URL3")
    IMAGE_URL_4 = _col(61, "画像URL4")
    IMAGE_URL_5 = _col(62, "画像URL5")
    IMAGE_URL_6 = _col(63, "画像URL6")
    IMAGE_URL_7 = _col(64, "画像URL7")
    IMAGE_URL_8 = _col(65, "画像URL8")

    # === BO-BQ列: SEO（3列）===
    # ※旧BY-CA列から移動（インデックス調整: -10）
    PAGE_TITLE = _col(66, "ページタイトル")
    META_DESC = _col(67, "メタディスクリプション")
    META_KEYWORDS = _col(68, "メタキーワード")

    # === BR-BV列: フラグ（5列）===
    # ※旧CB-CF列から移動（インデックス調整: -10）
    REDUCED_TAX = _col(69, "軽減税率対象")
    DIGITAL_CONTENT = _col(70, "デジタルコンテンツ")
    SUBSCRIPTION = _col(71, "定期購入")
    DISPLAY_ORDER = _col(72, "表示順")
    DISABLED_PAYMENTS = _col(73, "利用不可決済")

    # === BW-BX列: 掲載期間（2列）===
    # ※旧CG-CH列から移動（インデックス調整: -10）
    START_DATE = _col(74, "掲載開始日時")
    END_DATE = _col(75, "掲載終了日時")

    # === BY列: システム情報（1列）===
    # ※旧CI-CK列から移動、商品作成/更新日時を削除（インデックス調整: -10、-2列）
    SYNC_DATETIME = _col(76, "同期日時")

    # 画像列の範囲（便利定数）
    IMAGE_FIRST = MAIN_IMAGE
    IMAGE_LAST = IMAGE_URL_8

    # 総列数（89列 - 12列 = 77列）
    TOTAL_COLUMNS = 77

    @classmethod
    def all_columns(cls) -> list[Column]:
        """全列のリストを返す（インデックス順、重複除外）"""
        seen_indices = set()
        columns = []
        for name in dir(cls):
            if not name.startswith('_') and name.isupper():
                attr = getattr(cls, name)
                if isinstance(attr, Column) and attr.index not in seen_indices:
                    columns.append(attr)
                    seen_indices.add(attr.index)
        return sorted(columns, key=lambda c: c.index)

    @classmethod
    def last_column_letter(cls) -> str:
        """最終列の文字を返す（範囲指定用）"""
        return cls.SYNC_DATETIME.letter  # BY

    @classmethod
    def headers(cls) -> list[str]:
        """ヘッダー行のリストを返す"""
        columns = cls.all_columns()
        return [col.name for col in columns]


# ヘルパー関数

def get_cell(row: list, col: Column, default: str = "") -> str:
    """行から安全に値を取得"""
    if len(row) > col.index:
        val = row[col.index]
        return str(val).strip() if val is not None else default
    return default


def get_cell_float(row: list, col: Column, default: float = 0.0) -> float:
    """行から安全にfloat値を取得"""
    val = get_cell(row, col)
    if val:
        try:
            # カンマ区切りの数値に対応
            return float(val.replace(",", ""))
        except ValueError:
            pass
    return default


def get_cell_int(row: list, col: Column, default: int = 0) -> int:
    """行から安全にint値を取得"""
    return int(get_cell_float(row, col, float(default)))


def get_cell_bool(row: list, col: Column, true_value: str = "ON") -> bool:
    """行から安全にブール値を取得"""
    val = get_cell(row, col).upper()
    return val == true_value.upper()


def cell_ref(col: Column, row_num: int) -> str:
    """列と行番号からセル参照文字列を生成 (例: "E123")"""
    return f"{col.letter}{row_num}"


def range_ref(col_start: Column, col_end: Column, row_num: int) -> str:
    """列範囲と行番号から範囲参照文字列を生成 (例: "BK123:BT123")"""
    return f"{col_start.letter}{row_num}:{col_end.letter}{row_num}"


def col_range_ref(col_start: Column, col_end: Column, row_start: int, row_end: int) -> str:
    """2次元範囲の参照文字列を生成 (例: "A1:BY100")"""
    return f"{col_start.letter}{row_start}:{col_end.letter}{row_end}"


def full_row_range(row_num: int) -> str:
    """行全体の範囲を生成 (例: "A2:BY2")"""
    return f"A{row_num}:{Col.last_column_letter()}{row_num}"


def preserve_or_set(existing_row: list, col: Column, new_value: str,
                    old_row_num: int, new_row_num: int, preserve_existing: bool = True) -> str:
    """
    既存セルが数式の場合は行番号を調整して保持する。
    preserve_existing=Trueの場合、既存の値（数式以外）も保持する。
    preserve_existing=Falseの場合、数式のみ保持し、それ以外はnew_valueを使用する。
    """
    import re

    if len(existing_row) > col.index:
        cell_value = existing_row[col.index]
        cell_str = str(cell_value) if cell_value is not None else ""
        if cell_str.startswith("="):
            # 数式の場合は行番号を調整
            return _adjust_formula_row(cell_str, old_row_num, new_row_num)
        elif preserve_existing and cell_str != "":
            return cell_str
    return new_value


def _adjust_formula_row(formula: str, old_row: int, new_row: int) -> str:
    """数式内の行番号を調整する"""
    import re

    if not formula or not formula.startswith("="):
        return formula

    def replace_row(match):
        col_part = match.group(1)
        row_part = match.group(2)
        if row_part.startswith("$"):
            return match.group(0)
        if int(row_part) == old_row:
            return f"{col_part}{new_row}"
        return match.group(0)

    pattern = r'(\$?[A-Z]+)(\$?\d+)'
    return re.sub(pattern, replace_row, formula)


# 数式生成ヘルパー

class Formula:
    """スプレッドシート数式を生成するヘルパークラス"""

    @staticmethod
    def supplier_name(row_num: int) -> str:
        """K列: 仕入れ先商品名 = INDEX/MATCHで商品仕入れ先一覧から取得"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$B:$B,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def supplier_site(row_num: int) -> str:
        """L列: 仕入れ先サイト"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$D:$D,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'
