# ページ構造調査結果

## 調査日: 2025-11-28

---

## 1. Bullionstar

### サイトURL
https://www.bullionstar.com/

### 商品名セレクタ
```css
h1
```
- 例: "5 Gram PAMP Swiss Gold Bullion Bar"

### 価格セレクタ

#### 方法1: 数量別価格テーブル（推奨）
```css
.info tr
```
- 構造:
  ```
  Quantity    Price
  1 - 9       ¥126,112.25
  10 - 24     ¥125,607.81
  25 or more  ¥124,851.13
  ```
- 最初の価格行（1-9個の価格）を取得する

#### 方法2: プロモーション価格
```css
.price-new span   /* プロモーション価格 */
.price-old span   /* 通常価格 */
```

### 在庫状態
```css
.product-default-wrap.product-price-update[status]
```
- `status="IN_STOCK"` - 在庫あり
- `status="UNAVAILABLE"` - 在庫なし

### 通貨
- デフォルト表示は**JPY（日本円）**
- `¥` 記号で価格表示
- 通貨切り替えはUIから可能だが、自動切り替えは困難
- **対応**: JPYで取得し、必要に応じて変換

### JavaScript要件
- **必須**: ページはJavaScriptで動的レンダリング
- Playwrightでのヘッドレスブラウザ必須
- `wait_until="networkidle"` または十分な待機時間が必要

### 特記事項
- 商品によって価格表示形式が異なる場合がある
- プロモーション中は`.promo`クラスが表示される
- 在庫切れ商品は価格が表示されない

---

## 2. APMEX

### サイトURL
https://www.apmex.com/

### 商品名セレクタ
```css
h1
```
- 例: "2025 1 oz American Silver Eagle Coin BU"

### 価格セレクタ（推奨）
```css
.mod-product-pricing .price.discounted
/* または */
.mod-product-pricing .price
```
- 例: "$60.35"
- 割引価格がある場合は `.price.discounted`
- 元価格は `.strike-through` 内

### 価格構造
```html
<div class="mod-product-pricing">
    <span class="price discounted">$60.35</span>
    <span class="strike-through">
        <span>$64.35</span>
    </span>
</div>
```

### 通貨
- **USD（米ドル）** 固定
- `$` 記号で価格表示

### JavaScript要件
- **必須**: 動的コンテンツ
- Bot検出あり（強めの保護）
- Playwrightでのアクセスは可能だが、長めの待機時間が必要
- `wait_until="domcontentloaded"` + 8秒待機推奨

### 特記事項
- 403エラーが発生しやすい（通常のHTTPリクエストでは不可）
- User-Agentの適切な設定が必要
- レート制限に注意

---

## 3. 実装推奨セレクタ設定

### Bullionstar用
```python
BULLIONSTAR_CONFIG = {
    "name_selector": "h1",
    "price_selector": ".info tr:nth-child(2)",  # 最初の価格行
    "status_selector": ".product-default-wrap.product-price-update",
    "currency": "JPY",
    "wait_time": 3000,  # ms
}
```

### APMEX用
```python
APMEX_CONFIG = {
    "name_selector": "h1",
    "price_selector": ".mod-product-pricing .price",
    "currency": "USD",
    "wait_time": 8000,  # ms
}
```

---

## 4. 価格抽出ロジック

### Bullionstar
```python
def extract_bullionstar_price(page):
    # 価格テーブルから最初の価格を取得
    rows = page.query_selector_all(".info tr")
    for row in rows:
        text = row.inner_text()
        # "1 - 9    ¥126,112.25" のような形式
        match = re.search(r'¥([\d,]+\.?\d*)', text)
        if match:
            price_str = match.group(1).replace(',', '')
            return float(price_str)
    return None
```

### APMEX
```python
def extract_apmex_price(page):
    # .mod-product-pricing から価格を取得
    el = page.query_selector(".mod-product-pricing .price")
    if el:
        text = el.inner_text().strip()
        # "$60.35" のような形式
        match = re.search(r'\$([\d,]+\.?\d*)', text)
        if match:
            price_str = match.group(1).replace(',', '')
            return float(price_str)
    return None
```

---

## 5. 要件定義への影響

### 変更点
1. **通貨対応**: BullionstarはJPY、APMEXはUSDで取得
2. **CSSセレクタ**: ショップごとに異なるセレクタが必要
3. **待機時間**: ショップごとに異なる待機時間が必要

### スプレッドシート設計の更新案
「トラッキング対象」シートに以下の列を追加:
- 通貨（Currency）: JPY / USD
- 待機時間（Wait Time）: ミリ秒

または、ショップ名から自動判定する設計も可能。
