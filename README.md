# コイン価格管理システム

海外コインショップ（Bullionstar, APMEX等）から商品を取得し、カラーミーで販売するための統合管理システムです。

**GitHub:** <https://github.com/francchouette/coin-price-checker>

## 機能

- 仕入れ先サイトから価格・在庫を自動スクレイピング（Playwright使用）
- Googleスプレッドシートで商品・価格・在庫を一元管理
- カラーミーAPIへの価格・在庫・商品情報の自動同期
- Webダッシュボードによるタスク管理・モニタリング
- launchdによる定期自動実行（macOS）
- GitHub Actionsによるクラウド実行

## セットアップ

### Windows（簡単セットアップ）

1. このリポジトリをダウンロードまたはクローン
2. **`setup-windows.bat` をダブルクリック**

これだけで以下が自動的にインストール・設定されます:
- Python 3.11
- Google Cloud SDK
- 必要なPythonパッケージ全て
- Playwrightブラウザ
- Google Cloud認証（ブラウザが開くのでGoogleアカウントでログイン）
- デスクトップにショートカット作成

セットアップ完了後は、デスクトップの **「コイン価格管理」** をダブルクリックで起動できます。

### macOS

1. リポジトリをクローン
```bash
git clone https://github.com/francchouette/coin-price-checker.git
cd coin-price-checker
```

2. セットアップスクリプトを実行
```bash
bash scripts/setup.sh
```

3. Google Cloud認証（初回のみ）
```bash
gcloud auth application-default login \
  --scopes="openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets"

gcloud auth application-default set-quota-project coin-price-tracker-479614
```

4. 動作確認
```bash
bash カラーミー同期.command
# → ブラウザで localhost:8765 を開く
```

## 使い方

### ダッシュボード（推奨）

```bash
bash カラーミー同期.command
```

ブラウザで <http://localhost:8765> を開くと、3つのタスクを管理できます:

| タスク | 説明 | 所要時間目安 |
|--------|------|------------|
| フルスペック同期 | 全商品をカラーミーからDL → スクレイピング → 全項目同期 | 2-3時間 |
| ブリオンスター商品取得 | Bullionstarから商品一覧をスクレイピング | 30分-1時間 |
| APMEX商品取得 | APMEXから商品一覧+詳細をスクレイピング（レジューム対応） | 数日（中断・再開可） |
| 価格のみ同期 | 仕入れ先から価格・在庫のみ取得 → カラーミーに同期 | 1-2時間 |

### コマンドライン

```bash
# フルスペック同期（1行ずつ即時同期）
bash scripts/cm-sync-prices.sh

# 価格のみ同期（軽量版）
bash scripts/cm-price-only-sync.sh

# ブリオンスター商品取得
bash scripts/bs-scrape.sh

# テスト用（件数制限付き）
python -m src.fetch_supplier_prices --sync --limit 3 --verbose
python -m src.download_colorme_products --fetch-prices --sync --limit 3 --verbose
```

### 定期実行

ダッシュボードの各タスクカードにある「ON」ボタンで定期実行を有効にできます。
launchd plistが `~/Library/LaunchAgents/` に配置され、スケジュールに従って自動実行されます。

| タスク | 実行間隔 |
|--------|---------|
| フルスペック同期 | 4時間ごと（0, 4, 8, 12, 16, 20時） |
| ブリオンスター商品取得 | 6時間ごと（0, 6, 12, 18時） |
| 価格のみ同期 | 4時間ごと（0, 4, 8, 12, 16, 20時） |

## プロジェクト構成

```text
coin-price-checker/
├── setup-windows.bat          # Windows ワンクリックセットアップ
├── ダッシュボード起動.bat      # Windows ダッシュボード起動
├── .env.example              # 環境変数テンプレート
├── .github/workflows/        # GitHub Actions
├── launchd/                  # launchd plistテンプレート
├── scripts/
│   ├── setup.sh              # 初回セットアップ（macOS）
│   ├── cm-sync-prices.sh     # フルスペック同期
│   ├── cm-price-only-sync.sh # 価格のみ同期
│   ├── bs-scrape.sh          # ブリオンスター商品取得
│   ├── ap-scrape.sh          # APMEX商品取得
│   ├── cm-sync-dashboard.py  # Webダッシュボード
│   └── check_row3.py         # ベンチマーク診断
├── src/
│   ├── download_colorme_products.py  # カラーミー商品DL + スクレイピング + 同期
│   ├── fetch_supplier_prices.py      # 仕入れ先価格取得（軽量版）
│   ├── sync_colorme_products.py      # カラーミーAPI同期
│   ├── restore_formulas.py           # スプレッドシート数式復元
│   ├── bullionstar_products.py       # Bullionstar商品取得
│   ├── apmex_products.py             # APMEX商品取得（レジューム・キャッシュ対応）
│   ├── register_adopted_products.py  # 採用商品のカラーミー登録
│   ├── scraper.py                    # スクレイピング管理
│   ├── colorme.py                    # カラーミーAPIクライアント
│   ├── spreadsheet.py                # Google Sheets連携
│   ├── config.py                     # 設定管理
│   ├── cm_sheet_columns.py           # シート列定義
│   └── shops/                        # ショップ別スクレイパー
├── カラーミー同期.command     # ダッシュボード起動（ダブルクリック）
├── requirements.txt
└── README.md
```

## GitHub Secrets設定（GitHub Actions用）

| Secret名 | 内容 |
|----------|------|
| `SPREADSHEET_ID` | GoogleスプレッドシートのID |
| `COLORME_ACCESS_TOKEN` | カラーミーAPIアクセストークン |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | GCPサービスアカウント認証JSON |

## ライセンス

MIT License
