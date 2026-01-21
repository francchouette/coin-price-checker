"""
ブリオンスター商品ページ一覧シートの列定義（84列: A-CF）

全てのBSスクリプトはこのファイルから列情報をインポートする。
列構造が変更された場合、このファイルのみ修正すればOK。

使用例:
    from src.bs_sheet_columns import Col, get_cell, cell_ref

    # データ読み取り
    url = get_cell(row, Col.COLORME_URL)

    # スプレッドシート更新時の範囲指定
    batch_data.append({
        'range': cell_ref(Col.COLORME_URL, row_idx),
        'values': [[colorme_url]]
    })

    # 数式内での列参照
    formula = f'={Col.PRICE_JPY.letter}{row_num}*{Col.QUANTITY.letter}{row_num}'
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Column:
    """列情報を保持するイミュータブルなデータクラス"""
    index: int      # 0-based インデックス（row[index]で使用）
    letter: str     # 列文字 (A, B, ..., CF)（数式・範囲指定で使用）
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
    ブリオンスター商品ページ一覧の全84列定義

    使用方法:
        Col.ADOPTED_FLAG.index  -> 0
        Col.ADOPTED_FLAG.letter -> "A"
        Col.ADOPTED_FLAG.name   -> "採用フラグ"
    """

    # === A-C列: 管理列（3列）===
    ADOPTED_FLAG = _col(0, "採用フラグ")
    REGISTRATION_STATUS = _col(1, "カラーミー登録状況")
    SUPPLIER_ID = _col(2, "仕入れ先商品ID")

    # === D列: CM商品名（1列）===
    CM_PRODUCT_NAME = _col(3, "CM商品名")

    # === E-Q列: 仕入れ先商品情報（13列）===
    COLORME_URL = _col(4, "カラーミー商品URL")
    PRODUCT_URL = _col(5, "仕入れ先商品URL")
    PRODUCT_NAME = _col(6, "仕入れ先商品名")
    SITE = _col(7, "仕入れ先サイト")
    TOP_CATEGORY = _col(8, "最上位カテゴリ")
    PARENT_CATEGORY = _col(9, "親カテゴリ")
    CHILD_CATEGORY = _col(10, "子カテゴリ")
    COUNTRY = _col(11, "製造国")
    DESC_EN = _col(12, "商品説明（英語）")
    SPECS = _col(13, "仕様・スペック")
    YEAR = _col(14, "発行年")
    MINTAGE = _col(15, "発行数・限定数")
    STOCK_STATUS = _col(16, "仕入れ先在庫状況")

    # === R-AH列: 価格情報（17列）===
    PRICE = _col(17, "仕入れ先価格（現地通貨）")
    PREV_PRICE = _col(18, "前回仕入れ価格")
    PRICE_CHANGE = _col(19, "価格変動率")
    CURRENCY = _col(20, "取引通貨")
    EXCHANGE_TYPE = _col(21, "為替種類")
    EXCHANGE_RATE = _col(22, "為替レート")
    PRICE_JPY = _col(23, "仕入れ額(日本円)")
    QUANTITY = _col(24, "枚数")
    TOTAL_PURCHASE = _col(25, "仕入れ合計")
    MARGIN_RATE = _col(26, "設定マージン率")
    MARGIN_AMOUNT = _col(27, "設定マージン額")
    SHIPPING = _col(28, "送料")
    MISC_COST = _col(29, "諸経費")
    TOTAL_COST = _col(30, "合計原価")
    PROPER_PRICE = _col(31, "適正価格")
    PROFIT = _col(32, "粗利額")
    PROFIT_RATE = _col(33, "粗利率")

    # === AI-AN列: カラーミー価格情報（6列）===
    CM_SALES_PRICE = _col(34, "販売価格")
    CM_REGULAR_PRICE = _col(35, "定価")
    CM_MEMBERS_PRICE = _col(36, "会員価格")
    CM_COST = _col(37, "原価")
    CM_TAX_INCLUDED = _col(38, "消費税込販売価格")
    CM_TAX_AMOUNT = _col(39, "消費税額")

    # === AO-AT列: カテゴリー・グループ（6列）===
    CM_CATEGORY_BIG = _col(40, "大カテゴリーID")
    CM_CATEGORY_BIG_NAME = _col(41, "大カテゴリー名称")
    CM_CATEGORY_SMALL = _col(42, "小カテゴリーID")
    CM_CATEGORY_SMALL_NAME = _col(43, "小カテゴリー名称")
    CM_GROUP_ID = _col(44, "グループID")
    CM_GROUP_NAME = _col(45, "グループ名")

    # === AU列: 型番（1列）===
    CM_MODEL_NUMBER = _col(46, "型番")

    # === AV-BB列: 在庫管理（7列）===
    CM_STOCK = _col(47, "在庫数")
    CM_STOCK_MANAGED = _col(48, "在庫管理")
    CM_FEW_NUM = _col(49, "残りわずか数")
    CM_SOLDOUT_DISPLAY = _col(50, "売切れ表示")
    CM_MIN_NUM = _col(51, "最小購入数")
    CM_MAX_NUM = _col(52, "最大購入数")
    CM_UNIT = _col(53, "単位")

    # === BC-BF列: 送料・配送（4列）===
    CM_DELIVERY_CHARGE = _col(54, "個別送料")
    CM_COOL_CHARGE = _col(55, "クール便料金")
    CM_WEIGHT = _col(56, "重量(g)")
    CM_NO_DELIVERY = _col(57, "配送不要")

    # === BG-BJ列: 商品説明（4列）===
    CM_EXPL = _col(58, "商品説明")
    CM_SIMPLE_EXPL = _col(59, "簡易説明")
    CM_SMARTPHONE_EXPL = _col(60, "スマホ説明")
    CM_MEMO = _col(61, "備考")

    # === BK-BT列: 画像URL（10列）===
    IMAGE_1 = _col(62, "画像URL1")
    IMAGE_2 = _col(63, "画像URL2")
    IMAGE_3 = _col(64, "画像URL3")
    IMAGE_4 = _col(65, "画像URL4")
    IMAGE_5 = _col(66, "画像URL5")
    IMAGE_6 = _col(67, "画像URL6")
    IMAGE_7 = _col(68, "画像URL7")
    IMAGE_8 = _col(69, "画像URL8")
    IMAGE_9 = _col(70, "画像URL9")
    IMAGE_10 = _col(71, "画像URL10")

    # === BU-BW列: SEO項目（3列）===
    CM_PAGE_TITLE = _col(72, "ページタイトル")
    CM_META_DESC = _col(73, "メタディスクリプション")
    CM_META_KEYWORDS = _col(74, "メタキーワード")

    # === BX-CB列: フラグ・設定（5列）===
    CM_REDUCED_TAX = _col(75, "軽減税率対象")
    CM_DIGITAL = _col(76, "デジタルコンテンツ")
    CM_SUBSCRIPTION = _col(77, "定期購入")
    CM_SORT = _col(78, "表示順")
    CM_DISABLED_PAYMENT = _col(79, "利用不可決済")

    # === CA-CB列: 掲載期間（2列）===
    CM_START_DATE = _col(80, "掲載開始日時")
    CM_END_DATE = _col(81, "掲載終了日時")

    # === CC-CD列: システム情報（2列）===
    CM_SYNC_AT = _col(82, "同期日時")
    CM_CREATED_AT = _col(83, "商品作成日時")

    # 画像列の範囲（便利定数）
    IMAGE_FIRST = IMAGE_1
    IMAGE_LAST = IMAGE_10

    # 総列数
    TOTAL_COLUMNS = 84

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
        return cls.CM_CREATED_AT.letter  # CF


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


def cell_ref(col: Column, row_num: int) -> str:
    """列と行番号からセル参照文字列を生成 (例: "E123")"""
    return f"{col.letter}{row_num}"


def range_ref(col_start: Column, col_end: Column, row_num: int) -> str:
    """列範囲と行番号から範囲参照文字列を生成 (例: "BK123:BT123")"""
    return f"{col_start.letter}{row_num}:{col_end.letter}{row_num}"


def col_range_ref(col_start: Column, col_end: Column, row_start: int, row_end: int) -> str:
    """2次元範囲の参照文字列を生成 (例: "A1:CF100")"""
    return f"{col_start.letter}{row_start}:{col_end.letter}{row_end}"


# 数式生成ヘルパー

class Formula:
    """スプレッドシート数式を生成するヘルパークラス"""

    @staticmethod
    def price_change(row_num: int) -> str:
        """T列: 価格変動率 = (R-S)/S*100"""
        r = Col.PRICE.letter
        s = Col.PREV_PRICE.letter
        return f'=IF({s}{row_num}="","",IF({s}{row_num}=0,"",({r}{row_num}-{s}{row_num})/{s}{row_num}*100))'

    @staticmethod
    def total_purchase(row_num: int) -> str:
        """Z列: 仕入れ合計 = Y(枚数) * X(仕入れ額日本円)"""
        y = Col.QUANTITY.letter
        x = Col.PRICE_JPY.letter
        return f'={y}{row_num}*{x}{row_num}'

    @staticmethod
    def total_cost(row_num: int) -> str:
        """AE列: 合計原価 = Z(仕入れ合計) + AC(送料) + AD(諸経費)"""
        z = Col.TOTAL_PURCHASE.letter
        ac = Col.SHIPPING.letter
        ad = Col.MISC_COST.letter
        return f'={z}{row_num}+{ac}{row_num}+{ad}{row_num}'

    @staticmethod
    def proper_price(row_num: int) -> str:
        """AF列: 適正価格 = ROUNDUP(AE/(2-AA)+AB+AC, -2)"""
        ae = Col.TOTAL_COST.letter
        aa = Col.MARGIN_RATE.letter
        ab = Col.MARGIN_AMOUNT.letter
        ac = Col.SHIPPING.letter
        return f'=ROUNDUP({ae}{row_num}/(2-{aa}{row_num})+{ab}{row_num}+{ac}{row_num},-2)'

    @staticmethod
    def profit(row_num: int) -> str:
        """AG列: 粗利額 = AF - AE"""
        af = Col.PROPER_PRICE.letter
        ae = Col.TOTAL_COST.letter
        return f'={af}{row_num}-{ae}{row_num}'

    @staticmethod
    def profit_rate(row_num: int) -> str:
        """AH列: 粗利率 = AG/AF"""
        ag = Col.PROFIT.letter
        af = Col.PROPER_PRICE.letter
        return f'=IF({af}{row_num}=0,"",{ag}{row_num}/{af}{row_num})'

    @staticmethod
    def sales_price(row_num: int) -> str:
        """AI列: 販売価格 = AF"""
        return f'={Col.PROPER_PRICE.letter}{row_num}'

    @staticmethod
    def regular_price(row_num: int) -> str:
        """AJ列: 定価 = AF"""
        return f'={Col.PROPER_PRICE.letter}{row_num}'

    @staticmethod
    def members_price(row_num: int) -> str:
        """AK列: 会員価格 = AF"""
        return f'={Col.PROPER_PRICE.letter}{row_num}'

    @staticmethod
    def cost(row_num: int) -> str:
        """AL列: 原価 = AE"""
        return f'={Col.TOTAL_COST.letter}{row_num}'

    @staticmethod
    def tax_included(row_num: int) -> str:
        """AM列: 消費税込販売価格 = AI*1.1"""
        return f'={Col.CM_SALES_PRICE.letter}{row_num}*1.1'

    @staticmethod
    def tax_amount(row_num: int) -> str:
        """AN列: 消費税額 = AI*0.1"""
        return f'={Col.CM_SALES_PRICE.letter}{row_num}*0.1'

    @classmethod
    def set_all_formulas(cls, row: list, row_num: int) -> None:
        """行データに全ての数式を設定"""
        row[Col.PRICE_CHANGE.index] = cls.price_change(row_num)
        row[Col.TOTAL_PURCHASE.index] = cls.total_purchase(row_num)
        row[Col.TOTAL_COST.index] = cls.total_cost(row_num)
        row[Col.PROPER_PRICE.index] = cls.proper_price(row_num)
        row[Col.PROFIT.index] = cls.profit(row_num)
        row[Col.PROFIT_RATE.index] = cls.profit_rate(row_num)
        row[Col.CM_SALES_PRICE.index] = cls.sales_price(row_num)
        row[Col.CM_REGULAR_PRICE.index] = cls.regular_price(row_num)
        row[Col.CM_MEMBERS_PRICE.index] = cls.members_price(row_num)
        row[Col.CM_COST.index] = cls.cost(row_num)
        row[Col.CM_TAX_INCLUDED.index] = cls.tax_included(row_num)
        row[Col.CM_TAX_AMOUNT.index] = cls.tax_amount(row_num)
