"""
新カラーミー商品管理シートの列定義（89列: A-CK）

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
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Column:
    """列情報を保持するイミュータブルなデータクラス"""
    index: int      # 0-based インデックス（row[index]で使用）
    letter: str     # 列文字 (A, B, ..., CK)（数式・範囲指定で使用）
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
    新カラーミー商品管理シートの全89列定義（A-CK）

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

    # === J-O列: 仕入れ先基本情報（6列）===
    SUPPLIER_URL = _col(9, "仕入れ先商品URL")
    SUPPLIER_NAME = _col(10, "仕入れ先商品名")
    SUPPLIER_SITE = _col(11, "仕入れ先サイト")
    TOP_CATEGORY = _col(12, "最上位カテゴリ")
    PARENT_CATEGORY = _col(13, "親カテゴリ")
    CHILD_CATEGORY = _col(14, "子カテゴリ")

    # === P-T列: 仕入れ先商品詳細（5列）===
    COUNTRY = _col(15, "製造国")
    DESCRIPTION_EN = _col(16, "商品説明（英語）")
    SPECS = _col(17, "仕様・スペック")
    MINT_YEAR = _col(18, "発行年")
    MINTAGE = _col(19, "発行数・限定数")

    # === U-Y列: 仕入れ先価格情報（5列）===
    SUPPLIER_STOCK = _col(20, "仕入れ先在庫状況")
    SUPPLIER_PRICE = _col(21, "仕入れ先価格（現地通貨）")
    PREV_PRICE = _col(22, "前回仕入れ価格")
    PRICE_CHANGE_RATE = _col(23, "価格変動率")
    CURRENCY = _col(24, "取引通貨")

    # === Z-AL列: 価格計算（13列）===
    EXCHANGE_TYPE = _col(25, "為替種類")
    EXCHANGE_RATE = _col(26, "為替レート")
    PURCHASE_PRICE_JPY = _col(27, "仕入れ額(日本円)")
    QUANTITY = _col(28, "枚数")
    PURCHASE_TOTAL = _col(29, "仕入れ合計")
    MARGIN_RATE = _col(30, "設定マージン率")
    MARGIN_AMOUNT = _col(31, "設定マージン額")
    SHIPPING = _col(32, "送料")
    FEE = _col(33, "諸経費")
    TOTAL_COST = _col(34, "合計原価")
    PROPER_PRICE = _col(35, "適正価格")
    GROSS_PROFIT = _col(36, "粗利額")
    GROSS_PROFIT_RATE = _col(37, "粗利率")

    # === AM-AR列: カラーミー価格情報（6列）===
    SALES_PRICE = _col(38, "販売価格")
    REGULAR_PRICE = _col(39, "定価")
    MEMBERS_PRICE = _col(40, "会員価格")
    COST = _col(41, "原価")
    TAX_INCLUDED_PRICE = _col(42, "消費税込販売価格")
    TAX_AMOUNT = _col(43, "消費税額")

    # === AS-AX列: カテゴリー・グループ（6列）===
    CATEGORY_ID_BIG = _col(44, "大カテゴリーID")
    CATEGORY_NAME_BIG = _col(45, "大カテゴリー名称")
    CATEGORY_ID_SMALL = _col(46, "小カテゴリーID")
    CATEGORY_NAME_SMALL = _col(47, "小カテゴリー名")
    GROUP_IDS = _col(48, "グループID")
    GROUP_NAMES = _col(49, "グループ名")

    # === AY列: 型番（1列）===
    MODEL_NUMBER = _col(50, "型番")

    # === AZ-BF列: 在庫管理（7列）===
    STOCKS = _col(51, "在庫数")
    STOCK_MANAGED = _col(52, "在庫管理")
    FEW_NUM = _col(53, "残りわずか数")
    SOLDOUT_DISPLAY = _col(54, "売切れ表示")
    MIN_NUM = _col(55, "最小購入数")
    MAX_NUM = _col(56, "最大購入数")
    UNIT = _col(57, "単位")

    # === BG-BJ列: 送料・配送（4列）===
    DELIVERY_CHARGE = _col(58, "個別送料")
    COOL_CHARGE = _col(59, "クール便料金")
    WEIGHT = _col(60, "重量(g)")
    NO_DELIVERY = _col(61, "配送不要")

    # === BK-BN列: 商品説明（4列）===
    EXPL = _col(62, "商品説明")
    SIMPLE_EXPL = _col(63, "簡易説明")
    MOBILE_EXPL = _col(64, "スマホ説明")
    MEMO = _col(65, "備考")

    # === BO-BX列: 画像（10列）===
    MAIN_IMAGE = _col(66, "メイン画像URL")
    THUMBNAIL = _col(67, "サムネイルURL")
    IMAGE_URL_1 = _col(68, "画像URL1")
    IMAGE_URL_2 = _col(69, "画像URL2")
    IMAGE_URL_3 = _col(70, "画像URL3")
    IMAGE_URL_4 = _col(71, "画像URL4")
    IMAGE_URL_5 = _col(72, "画像URL5")
    IMAGE_URL_6 = _col(73, "画像URL6")
    IMAGE_URL_7 = _col(74, "画像URL7")
    IMAGE_URL_8 = _col(75, "画像URL8")

    # === BY-CA列: SEO（3列）===
    PAGE_TITLE = _col(76, "ページタイトル")
    META_DESC = _col(77, "メタディスクリプション")
    META_KEYWORDS = _col(78, "メタキーワード")

    # === CB-CF列: フラグ（5列）===
    REDUCED_TAX = _col(79, "軽減税率対象")
    DIGITAL_CONTENT = _col(80, "デジタルコンテンツ")
    SUBSCRIPTION = _col(81, "定期購入")
    DISPLAY_ORDER = _col(82, "表示順")
    DISABLED_PAYMENTS = _col(83, "利用不可決済")

    # === CG-CH列: 掲載期間（2列）===
    START_DATE = _col(84, "掲載開始日時")
    END_DATE = _col(85, "掲載終了日時")

    # === CI-CK列: システム情報（3列）===
    SYNC_DATETIME = _col(86, "同期日時")
    CREATED_DATE = _col(87, "商品作成日時")
    UPDATED_DATE = _col(88, "商品更新日時")

    # 画像列の範囲（便利定数）
    IMAGE_FIRST = MAIN_IMAGE
    IMAGE_LAST = IMAGE_URL_8

    # 総列数
    TOTAL_COLUMNS = 89

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
        return cls.UPDATED_DATE.letter  # CK

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
    """2次元範囲の参照文字列を生成 (例: "A1:CK100")"""
    return f"{col_start.letter}{row_start}:{col_end.letter}{row_end}"


def full_row_range(row_num: int) -> str:
    """行全体の範囲を生成 (例: "A2:CK2")"""
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
        elif preserve_existing and cell_value:
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

    @staticmethod
    def top_category(row_num: int) -> str:
        """M列: 最上位カテゴリ"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$E:$E,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def parent_category(row_num: int) -> str:
        """N列: 親カテゴリ"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$F:$F,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def child_category(row_num: int) -> str:
        """O列: 子カテゴリ"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$G:$G,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def country(row_num: int) -> str:
        """P列: 製造国"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$H:$H,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def description_en(row_num: int) -> str:
        """Q列: 商品説明（英語）"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$AF:$AF,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def specs(row_num: int) -> str:
        """R列: 仕様・スペック"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$AE:$AE,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def mint_year(row_num: int) -> str:
        """S列: 発行年"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$AH:$AH,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'

    @staticmethod
    def mintage(row_num: int) -> str:
        """T列: 発行数・限定数"""
        j = Col.SUPPLIER_URL.letter
        return f'=IFERROR(INDEX(商品仕入れ先一覧!$AI:$AI,MATCH(${j}{row_num},商品仕入れ先一覧!$C:$C,0)),"")'
