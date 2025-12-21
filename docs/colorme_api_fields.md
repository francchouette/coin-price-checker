# カラーミーショップAPI 商品フィールド一覧

カラーミーショップAPIの商品（products）エンドポイントで使用できるフィールドの一覧です。

## 参照元

- [カラーミーショップ デベロッパー APIドキュメント](https://developer.shop-pro.jp/docs/colorme-api)
- [Swagger仕様（OpenAPI）](https://api.shop-pro.jp/v1/swagger.json)

---

## GET（取得）できる全フィールド

| フィールド名 | 型 | 説明 |
|------------|-----|------|
| `id` | integer | 商品ID |
| `account_id` | string | ショップアカウントID |
| `name` | string | 商品名 |
| `model_number` | string | 型番 |
| `category.id_big` | integer | 大カテゴリーID |
| `category.id_small` | integer | 小カテゴリーID |
| `group_ids` | array | グループID配列 |
| `display_state` | string | 掲載設定 |
| `price` | integer | 定価 |
| `sales_price` | integer | 販売価格 |
| `sales_price_including_tax` | integer | 消費税込販売価格 |
| `sales_price_tax` | integer | 消費税額 |
| `members_price` | integer | 会員価格 |
| `members_price_including_tax` | integer | 消費税込会員価格 |
| `members_price_tax` | integer | 会員価格の消費税額 |
| `cost` | integer | 原価 |
| `stocks` | integer | 在庫数 |
| `stock_managed` | boolean | 在庫管理するか |
| `few_num` | integer | 残りわずかとなる在庫数 |
| `soldout_display` | boolean | 売り切れ時の表示設定 |
| `delivery_charge` | integer | 個別送料 |
| `cool_charge` | integer | クール便追加料金 |
| `min_num` | integer | 最小購入数量 |
| `max_num` | integer | 最大購入数量 |
| `unit` | string | 単位 |
| `weight` | integer | 重量(g) |
| `sort` | integer | 表示順 |
| `expl` | string | 商品説明 |
| `simple_expl` | string | 簡易説明 |
| `mobile_expl` | string | フィーチャーフォン向け説明 |
| `smartphone_expl` | string | スマホ向け説明 |
| `memo` | string | 備考 |
| `sale_start_date` | integer | 掲載開始時刻（UNIX timestamp） |
| `sale_end_date` | integer | 掲載終了時刻（UNIX timestamp） |
| `make_date` | integer | 商品作成日時（UNIX timestamp） |
| `update_date` | integer | 商品更新日時（UNIX timestamp） |
| `image_url` | string | メイン画像URL |
| `mobile_image_url` | string | モバイル用画像URL |
| `thumbnail_image_url` | string | サムネイル画像URL |
| `images` | array | 商品画像情報配列 |
| `options` | array | オプション情報配列 |
| `variants` | array | バリエーション配列 |
| `pickups` | array | おすすめ商品情報 |
| `digital_content` | boolean | デジタルコンテンツ商品か |
| `regular_purchase` | boolean | 定期購入商品か |
| `tax_reduced` | boolean | 軽減税率対象か |
| `without_shipping` | boolean | 配送不要商品か |
| `unavailable_payment_ids` | array | 利用不可決済方法ID |
| `unavailable_delivery_ids` | array | 利用不可配送方法ID |

---

## POST（新規登録）で指定できるフィールド

| フィールド名 | 型 | 必須 | 説明 |
|------------|-----|:---:|------|
| `name` | string | ✓ | 商品名（最大100文字） |
| `category_id_big` | integer | | 大カテゴリーID |
| `sales_price` | integer | | 販売価格（最小値0） |
| `display_state` | string | | 掲載設定 |
| `tax_reduced` | boolean | | 軽減税率対象フラグ |

### display_state の値

| 値 | 説明 |
|----|------|
| `showing` | 掲載する |
| `hidden` | 掲載しない |
| `showing_for_members` | 会員のみ表示 |
| `sale_for_members` | 会員のみ購入可 |

> **注意**: POSTは最小限のフィールドのみ指定可能。詳細設定はPUT（更新）で行う設計です。

---

## PUT（更新）で指定できるフィールド

| フィールド名 | 型 | 説明 |
|------------|-----|------|
| `name` | string | 商品名 |
| `price` | integer | 定価 |
| `sales_price` | integer | 販売価格 |
| `members_price` | integer | 会員価格 |
| `cost` | integer | 原価 |
| `model_number` | string | 型番 |
| `category_id_big` | integer | 大カテゴリーID |
| `category_id_small` | integer | 小カテゴリーID |
| `group_ids` | array | グループID配列 |
| `expl` | string | 商品説明 |
| `simple_expl` | string | 簡易説明 |
| `smartphone_expl` | string | スマホ向け説明 |
| `display_state` | string | 掲載設定 |
| `stock_managed` | boolean | 在庫管理設定 |
| `stocks` | integer/object | 在庫数または増減値 |
| `variants` | array | バリエーション更新 |
| `tax_reduced` | boolean | 軽減税率対象フラグ |

### stocks の指定方法

```json
// 在庫数を直接指定
"stocks": 10

// 増減値で指定
"stocks": { "diff": 5 }   // +5
"stocks": { "diff": -3 }  // -3
```

---

## API経由で更新できない項目（読み取り専用）

以下のフィールドはGETで取得できますが、PUT/POSTでは指定できません。
管理画面からのみ設定可能です。

| フィールド名 | 説明 |
|------------|------|
| `delivery_charge` | 個別送料 |
| `soldout_display` | 売り切れ時の表示設定 |
| `min_num` | 最小購入数量 |
| `max_num` | 最大購入数量 |
| `few_num` | 残りわずかとなる在庫数 |
| `unit` | 単位 |
| `weight` | 重量 |
| `memo` | 備考 |
| `sale_start_date` | 掲載開始時刻 |
| `sale_end_date` | 掲載終了時刻 |
| `sort` | 表示順 |
| `digital_content` | デジタルコンテンツ商品フラグ |
| `regular_purchase` | 定期購入商品フラグ |
| `without_shipping` | 配送不要商品フラグ |
| `unavailable_payment_ids` | 利用不可決済方法ID配列 |
| `unavailable_delivery_ids` | 利用不可配送方法ID配列 |
| `cool_charge` | クール便追加料金 |

---

## フィールド分類サマリー

| 操作 | 指定可能フィールド数 |
|-----|:---:|
| GET（取得） | 40+ |
| POST（新規登録） | 5 |
| PUT（更新） | 17 |
| 読み取り専用 | 16 |

---

## SEO関連フィールド

### 商品（products）API

**商品レベルのSEO設定はAPI経由で取得・更新できません。**

| フィールド | 状況 |
|-----------|------|
| `title`（ページタイトル） | ❌ APIなし |
| `meta_description` | ❌ APIなし |
| `meta_keywords` | ❌ APIなし |
| `canonical` | ❌ APIなし |
| `slug`（URL） | ❌ APIなし |

商品個別のSEO設定を変更する場合は、**管理画面から手動で設定**するか、**Playwright経由のブラウザ自動化**が必要です。

### カテゴリー（productCategory）API

カテゴリーにはSEOメタタグが存在し、GET/POST/PUT すべてで操作可能です。

| フィールド | 型 | GET | POST | PUT | 説明 |
|-----------|-----|:---:|:----:|:---:|------|
| `meta_tag.title` | string | ✓ | ✓ | ✓ | ページタイトル |
| `meta_tag.keywords` | string | ✓ | ✓ | ✓ | メタキーワード |
| `meta_tag.description` | string | ✓ | ✓ | ✓ | メタディスクリプション |

---

## 画像について

### API経由での画像アップロード

- **API経由の画像アップロードは非対応**
- `images[].src`に外部URLやBase64を指定しても機能しない
- FTP+API連携も非対応

### 対応方法

Playwright経由のブラウザ自動化を使用：
- `src/colorme_image_uploader.py` モジュールを使用
- 管理画面（admin.shop-pro.jp）にログインして商品編集画面から画像をアップロード

---

## 関連ファイル

- [src/colorme.py](../src/colorme.py) - カラーミーAPIクライアント
- [src/add_product.py](../src/add_product.py) - 商品追加処理
- [src/colorme_image_uploader.py](../src/colorme_image_uploader.py) - 画像アップロード

---

*最終更新: 2025-12-21*

---

## フィールド値の詳細説明

### display_state（掲載設定）

| 値 | 日本語 | 説明 |
|----|--------|------|
| `showing` | 掲載する | 一般ユーザーに商品が表示され、購入可能 |
| `hidden` | 掲載しない | 商品ページは非公開。直接URLにアクセスしても表示されない |
| `showing_for_members` | 会員のみ表示 | 会員登録済みユーザーにのみ商品が表示される |
| `sale_for_members` | 会員のみ購入可 | 全員に表示されるが、購入は会員のみ可能 |

### stock_managed（在庫管理）

| 値 | 説明 |
|----|------|
| `true` | 在庫管理する：在庫数が0になると購入不可。在庫数に応じて「残りわずか」表示 |
| `false` | 在庫管理しない：無限に購入可能。受注生産やサービス商品向け |

### soldout_display（売り切れ時の表示設定）

| 値 | 説明 |
|----|------|
| `true`（表示） | 在庫切れ時も商品を表示。「売り切れ」ラベルが表示される |
| `false`（非表示） | 在庫切れ時は商品を非表示にする |

### tax_reduced（軽減税率対象）

| 値 | 説明 |
|----|------|
| `true` | 軽減税率（8%）が適用される。飲食料品、新聞など |
| `false` | 標準税率（10%）が適用される |

### digital_content（デジタルコンテンツ商品）

| 値 | 説明 |
|----|------|
| `true` | ダウンロード販売商品。配送情報入力が不要になる |
| `false` | 通常の物理商品 |

### regular_purchase（定期購入商品）

| 値 | 説明 |
|----|------|
| `true` | 定期購入（サブスクリプション）商品。定期的に自動で注文が発生 |
| `false` | 通常の単発購入商品 |

### without_shipping（配送不要商品）

| 値 | 説明 |
|----|------|
| `true` | 配送が不要。店舗受け取りやサービス商品向け |
| `false` | 通常配送が必要 |

---

## 数値フィールドの単位と注意点

| フィールド | 単位 | 注意点 |
|-----------|------|--------|
| `price` | 円 | 税抜き定価。消費税は別途計算される |
| `sales_price` | 円 | 税抜き販売価格。これが実際の販売価格 |
| `members_price` | 円 | 税抜き会員価格。会員ログイン時に適用 |
| `cost` | 円 | 原価。利益計算用。顧客には表示されない |
| `stocks` | 個 | 在庫数。`stock_managed=true`時のみ有効 |
| `few_num` | 個 | この数以下で「残りわずか」表示 |
| `min_num` | 個 | 最小購入数量。1以上を設定 |
| `max_num` | 個 | 最大購入数量。0=無制限 |
| `weight` | g（グラム） | 重量。送料計算に使用される場合あり |
| `delivery_charge` | 円 | 個別送料。この商品のみ追加送料が発生 |
| `cool_charge` | 円 | クール便追加料金 |
| `sort` | - | 表示順。小さいほど先に表示 |

---

## 日時フィールド

| フィールド | 形式 | 説明 |
|-----------|------|------|
| `make_date` | UNIX timestamp | 商品が作成された日時 |
| `update_date` | UNIX timestamp | 商品が最後に更新された日時 |
| `sale_start_date` | UNIX timestamp | 掲載開始日時。この時刻まで非公開 |
| `sale_end_date` | UNIX timestamp | 掲載終了日時。この時刻以降は非公開 |

### UNIX timestampの変換例

```python
from datetime import datetime

# UNIX timestamp → 日時文字列
timestamp = 1734567890
dt = datetime.fromtimestamp(timestamp)
print(dt.strftime("%Y-%m-%d %H:%M:%S"))  # 2024-12-19 07:04:50

# 日時文字列 → UNIX timestamp
dt = datetime(2024, 12, 25, 10, 0, 0)
timestamp = int(dt.timestamp())
print(timestamp)  # 1735095600
```

---

## 画像フィールド

| フィールド | 説明 |
|-----------|------|
| `image_url` | メイン画像のURL。商品一覧や詳細ページで最初に表示される |
| `thumbnail_image_url` | サムネイル画像URL。一覧表示用の小さい画像 |
| `mobile_image_url` | モバイル用画像URL（現在はほぼ使用されない） |
| `images` | 追加画像の配列 |

### images配列の構造

```json
{
  "images": [
    {
      "src": "https://img21.shop-pro.jp/.../product_o1.png",
      "position": 1,
      "mobile": false
    },
    {
      "src": "https://img21.shop-pro.jp/.../product_o2.png",
      "position": 2,
      "mobile": false
    }
  ]
}
```

| プロパティ | 説明 |
|-----------|------|
| `src` | 画像のURL |
| `position` | 表示順序（1から始まる） |
| `mobile` | モバイル専用画像かどうか |

---

## カテゴリー・グループ

### category（カテゴリー）

```json
{
  "category": {
    "id_big": 123,
    "id_small": 456
  }
}
```

| プロパティ | 説明 |
|-----------|------|
| `id_big` | 大カテゴリーID。必須の第1階層 |
| `id_small` | 小カテゴリーID。任意の第2階層。0=未設定 |

### group_ids（グループ）

```json
{
  "group_ids": [1, 5, 12]
}
```

- 商品が属するグループIDの配列
- 複数のグループに所属可能
- グループは親子階層を持てる
- 空配列 `[]` = どのグループにも属さない

---

## 決済・配送制限

### unavailable_payment_ids（利用不可決済方法）

```json
{
  "unavailable_payment_ids": [1, 3]
}
```

- この商品で使用できない決済方法のID配列
- 例: 代引き不可、後払い不可など

### unavailable_delivery_ids（利用不可配送方法）

```json
{
  "unavailable_delivery_ids": [2]
}
```

- この商品で使用できない配送方法のID配列
- 例: メール便不可、大型商品で宅配便のみなど

---

## 実際のAPIレスポンス例

```json
{
  "product": {
    "id": 189851305,
    "account_id": "PA01517852",
    "model_number": "GOLD-BAR-001",
    "name": "1オンス ゴールドバー",
    "category": {
      "id_big": 10,
      "id_small": 0
    },
    "group_ids": [1, 5],
    "display_state": "showing",
    "sales_price": 298000,
    "sales_price_including_tax": 327800,
    "sales_price_tax": 29800,
    "price": 298000,
    "members_price": 295000,
    "members_price_including_tax": 324500,
    "members_price_tax": 29500,
    "cost": 280000,
    "cool_charge": 0,
    "delivery_charge": 0,
    "stocks": 5,
    "min_num": 1,
    "max_num": 10,
    "sale_start_date": null,
    "sale_end_date": null,
    "unit": "",
    "weight": 31,
    "few_num": 2,
    "sort": 100,
    "simple_expl": "純度99.99%の1オンス金塊",
    "expl": "<p>スイス製の1オンスゴールドバーです。</p>",
    "mobile_expl": null,
    "smartphone_expl": "",
    "make_date": 1734567890,
    "update_date": 1734654321,
    "memo": "仕入れ先: Bullionstar",
    "image_url": "https://img21.shop-pro.jp/PA01517/852/product/189851305.png",
    "mobile_image_url": null,
    "thumbnail_image_url": "https://img21.shop-pro.jp/PA01517/852/product/189851305_th.png",
    "images": [
      {
        "src": "https://img21.shop-pro.jp/PA01517/852/product/189851305_o1.png",
        "position": 1,
        "mobile": false
      }
    ],
    "pickups": [],
    "tax_reduced": false,
    "digital_content": false,
    "regular_purchase": false,
    "unavailable_payment_ids": [],
    "unavailable_delivery_ids": [],
    "without_shipping": false,
    "stock_managed": true,
    "soldout_display": true,
    "options": [],
    "variants": []
  }
}
```
