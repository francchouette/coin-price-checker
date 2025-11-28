# 海外コイン価格追跡・アラートプログラム 要件定義書

## 1. 🎯 目的と概要

### 目的
海外の主要なコインショップ（Bullionstar, APMEXなど）の商品価格を定期的に自動収集し、スプレッドシートに記録する。また、価格に急激な変動があった際に、運用者に自動で通知することで、迅速な仕入れ判断を支援する。

### 概要
本プログラムは、Python（Playwright使用）で記述され、GitHub Actionsによってスケジュール実行されます。URLリストと設定はGoogleスプレッドシートで管理され、運用者によるコードレスでの対象商品管理を可能とします。

---

## 2. ⚙️ 機能要件 (Functional Requirements)

| ID | 機能名 | 詳細要件 | 入力/トリガー | 出力/アクション |
| :--- | :--- | :--- | :--- | :--- |
| **FR-100** | **対象URLの読み込み** | **Googleスプレッドシート**の**「トラッキング対象」**シートから、トラッキング対象（`ON`となっている行）のURLリストを取得する。 | 定期実行トリガー | URLリスト (配列) |
| **FR-110** | **価格データ収集** | 読み込んだURLリストに基づき、Playwright（ヘッドレスブラウザ）を使用して、各Webサイト（Bullionstar, APMEX）の商品名と現在の販売価格をスクレイピングする。**ショップごとに異なるセレクタと通貨で取得する。** | URLリスト | 商品名、価格、通貨、日時 |
| **FR-120** | **価格データの保存** | 収集した商品名、価格、通貨、実行日時を、Googleスプレッドシートの**「価格履歴」**シートの最終行に追記する。 | 収集データ | スプレッドシートへのデータ追記 |
| **FR-200** | **前日価格の取得** | スプレッドシートの**「価格履歴」**シートから、対象商品の直近の実行時の価格データを取得する。 | 最新の収集データ | 直近の価格データ |
| **FR-210** | **価格変動の計算** | 最新の価格と、FR-200で取得した直近の価格を比較し、**変動率（%）**を計算する。**同一通貨間で比較する。** | 最新価格、直近価格 | 変動率 (%) |
| **FR-220** | **急変アラート判定** | 計算された変動率が、**設定値（±5%）**を超えた場合に「急変」と判定する。 | 変動率 | アラートフラグ (`True`/`False`) |
| **FR-230** | **アラート通知** | FR-220で急変が判定された場合、運用者が設定した通知チャネル（例：Slack, Email, LINE Notifyなど）を通じて以下の情報を含む通知を送信する。 | アラートフラグ=`True` | 通知メッセージ送信 |
| | | - 商品名 / 収集日時 / 最新価格 / 直近価格 / **変動率 (%)** / 通貨 / 対象URL | | |

---

## 3. 🛡️ 非機能要件 (Non-functional Requirements)

| 要件カテゴリ | ID | 詳細要件 |
| :--- | :--- | :--- |
| **信頼性** | NFR-100 | GitHub Actionsの実行ログを通じて、プログラムの実行結果とエラー内容を確認できること。 |
| | NFR-110 | スプレッドシートの書き込みが失敗した場合、**リトライ機構**（最大3回）を設けること。 |
| **性能** | NFR-200 | 全ての対象URLの収集、処理、書き込みを**10分以内**に完了させること。 |
| | NFR-210 | Webサイトへの負荷軽減のため、スクレイピングは各リクエスト間に**ランダムな待機時間（2秒〜5秒）**を設けること。**ショップごとの推奨待機時間を考慮する。** |
| **保守性** | NFR-300 | URL、CSSセレクタ、アラート閾値などの設定値は、Pythonコード内で明確に切り分けて定義すること。 |
| | NFR-310 | Google Sheets APIの認証情報などは、GitHub Secretsを通じて外部から安全に渡せるように設計すること。 |
| **セキュリティ** | NFR-400 | 認証情報（APIキー、サービスアカウントJSON）は、**GitHub Secrets**に保存し、コード内に直接記述しないこと。 |
| | NFR-410 | Playwrightを使用する際、ヘッドレスモードで実行し、不必要な情報（クッキーなど）を保持しないこと。 |

---

## 4. 💻 技術要件 (Technical Requirements)

| 項目 | 詳細仕様 |
| :--- | :--- |
| **プログラミング言語** | Python 3.9+ |
| **データ収集ライブラリ** | **Playwright for Python** (JavaScriptレンダリング対応のため) |
| **スプレッドシート連携** | `gspread` または `pygsheets` (Google Sheets API ラッパー) |
| **データ処理** | `pandas` (価格変動計算、データ整形に使用) |
| **実行環境** | GitHub Actions (Linux Runner) |
| **通知手段** | 運用者が選択可能なAPI（例: Slack Webhook, Gmail API, LINE Notifyなど）に対応できる設計とすること。初期実装ではSlack Webhookを想定する。 |
| **認証情報** | GitHub Secrets経由で環境変数として設定ファイル（JSON）を渡すこと。 |

---

## 5. 🧑‍💻 運用要件 (Operational Requirements)

| ID | 要件 | 実施方法 |
| :--- | :--- | :--- |
| **OPR-100** | **実行スケジュールの管理** | GitHub Actionsのワークフローファイル（YAML）に、**1日2回**（例：JST 9:00AM / 21:00PM）に実行する`schedule`（cron）を設定する。 |
| **OPR-110** | **対象URLの変更** | **スプレッドシートの「トラッキング対象」シート**を編集することで、運用者がコードレスで対象の追加・削除・一時停止を行えること。 |
| **OPR-120** | **エラー通知** | GitHub Actionsでの実行が失敗した場合、GitHubの標準機能（または追加設定）により、リポジトリの管理者へ失敗通知が届くように設定する。 |
| **OPR-130** | **アラート閾値の変更** | アラートの閾値（例: ±5%）は、スプレッドシートの「設定」シートなど、**コード外の場所**で変更できるように設計することが望ましい。 |

---

## 6. 📊 スプレッドシート構成

### 6.1 「トラッキング対象」シート

| 列 | 内容 | 例 | 備考 |
| :--- | :--- | :--- | :--- |
| A | トラッキング状態 | `ON` / `OFF` | |
| B | ショップ名 | `Bullionstar` / `APMEX` | ショップ名でセレクタを自動判定 |
| C | 商品名（参考） | `Gold PAMP 5g` | 手動入力（参考用） |
| D | URL | `https://...` | |

> **注**: CSSセレクタはショップ名から自動判定するため、スプレッドシートでの指定は不要。

### 6.2 「価格履歴」シート

| 列 | 内容 | 例 |
| :--- | :--- | :--- |
| A | 収集日時 | `2025-11-28 09:00:00` |
| B | ショップ名 | `Bullionstar` |
| C | 商品名 | `5 Gram PAMP Swiss Gold Bullion Bar` |
| D | 価格 | `126112.25` |
| E | 通貨 | `JPY` / `USD` |
| F | URL | `https://...` |

### 6.3 「設定」シート

| 列 | 内容 | 例 |
| :--- | :--- | :--- |
| A | 設定項目名 | `ALERT_THRESHOLD` |
| B | 設定値 | `5` |

---

## 7. 🔍 ショップ別スクレイピング仕様

### 7.1 Bullionstar

| 項目 | 値 |
| :--- | :--- |
| **サイトURL** | https://www.bullionstar.com/ |
| **商品名セレクタ** | `h1` |
| **価格セレクタ** | `.info tr` (数量別価格テーブル) |
| **価格抽出方法** | テーブルの最初の価格行から `¥XXX,XXX.XX` 形式を抽出 |
| **通貨** | **JPY（日本円）** |
| **在庫状態セレクタ** | `.product-default-wrap.product-price-update[status]` |
| **在庫判定** | `status="IN_STOCK"` → 在庫あり / `status="UNAVAILABLE"` → 在庫なし |
| **推奨待機時間** | 3秒 |
| **JavaScript** | 必須（動的レンダリング） |

#### 価格抽出ロジック
```python
# 価格テーブルから最初の価格を取得
rows = page.query_selector_all(".info tr")
for row in rows:
    text = row.inner_text()
    # "1 - 9    ¥126,112.25" のような形式
    match = re.search(r'¥([\d,]+\.?\d*)', text)
    if match:
        price_str = match.group(1).replace(',', '')
        return float(price_str)
```

#### 価格表示例
```
Quantity    Price
1 - 9       ¥126,112.25
10 - 24     ¥125,607.81
25 or more  ¥124,851.13
```

### 7.2 APMEX

| 項目 | 値 |
| :--- | :--- |
| **サイトURL** | https://www.apmex.com/ |
| **商品名セレクタ** | `h1` |
| **価格セレクタ** | `.mod-product-pricing .price` |
| **価格抽出方法** | `$XX.XX` 形式を抽出 |
| **通貨** | **USD（米ドル）** |
| **推奨待機時間** | 8秒（Bot検出対策） |
| **JavaScript** | 必須（動的レンダリング） |
| **注意事項** | Bot検出が強いため、適切なUser-Agent設定と長めの待機が必要 |

#### 価格抽出ロジック
```python
# .mod-product-pricing から価格を取得
el = page.query_selector(".mod-product-pricing .price")
if el:
    text = el.inner_text().strip()
    # "$60.35" のような形式
    match = re.search(r'\$([\d,]+\.?\d*)', text)
    if match:
        price_str = match.group(1).replace(',', '')
        return float(price_str)
```

#### 価格HTML構造例
```html
<div class="mod-product-pricing">
    <span class="price discounted">$60.35</span>
    <span class="strike-through">
        <span>$64.35</span>
    </span>
</div>
```

---

## 8. 📁 プロジェクト構成（推奨）

```
coin-price-check-script/
├── .github/
│   └── workflows/
│       └── price-check.yml      # GitHub Actions ワークフロー
├── src/
│   ├── __init__.py
│   ├── main.py                  # メインエントリーポイント
│   ├── scraper.py               # Playwrightによるスクレイピング
│   ├── shops/                   # ショップ別スクレイパー
│   │   ├── __init__.py
│   │   ├── base.py              # 基底クラス
│   │   ├── bullionstar.py       # Bullionstar用
│   │   └── apmex.py             # APMEX用
│   ├── spreadsheet.py           # Google Sheets連携
│   ├── notifier.py              # 通知機能（Slack等）
│   └── config.py                # 設定値管理
├── scripts/
│   └── check_page_structure.py  # ページ構造調査スクリプト
├── tests/
│   └── test_*.py                # テストコード
├── docs/
│   ├── requirements.md          # 本ドキュメント
│   ├── tasks.md                 # タスク一覧
│   ├── tracking_urls.md         # 監視対象URL一覧
│   └── page_structure_analysis.md # ページ構造調査結果
├── requirements.txt             # Python依存パッケージ
├── .gitignore
└── README.md
```

---

## 9. 🔐 セキュリティ考慮事項

1. **GitHub Secrets に保存すべき情報:**
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: Google Sheets API認証用サービスアカウントJSON
   - `SLACK_WEBHOOK_URL`: Slack通知用Webhook URL
   - `SPREADSHEET_ID`: 対象スプレッドシートのID

2. **コードに含めてはいけない情報:**
   - APIキー、認証トークン
   - サービスアカウントの秘密鍵
   - Webhook URL

---

## 10. 📝 開発時の注意事項

1. **スクレイピング対象サイトの利用規約を確認すること**
2. **過度なリクエストを避け、適切な待機時間を設けること**
   - Bullionstar: 3秒以上
   - APMEX: 8秒以上
3. **CSSセレクタはサイト更新により変更される可能性があるため、エラーハンドリングを適切に行うこと**
4. **テスト環境では本番スプレッドシートとは別のシートを使用すること**
5. **通貨の違いに注意**: BullionstarはJPY、APMEXはUSDで価格を取得

---

## 11. 📋 監視対象商品一覧

### 概要
- **総URL数**: 27件
- **対象ショップ**: Bullionstar (24件), APMEX (3件)

### カテゴリ別

| カテゴリ | 件数 | 通貨 |
|----------|------|------|
| シルバーコイン | 8件 | JPY/USD |
| ゴールドコイン | 5件 | JPY/USD |
| プラチナコイン | 3件 | JPY |
| シルバーバー | 5件 | JPY/USD |
| ゴールドバー | 6件 | JPY |

詳細は [tracking_urls.md](./tracking_urls.md) を参照。

---

*本ドキュメントは要件定義として保存され、開発の基準となります。*
*最終更新: 2025-11-28（ページ構造調査結果を反映）*
