# 海外コイン価格追跡・アラートプログラム

海外の主要なコインショップ（Bullionstar, APMEXなど）の商品価格を定期的に自動収集し、Googleスプレッドシートに記録するプログラムです。

## リポジトリ

**GitHub:** https://github.com/francchouette/coin-price-checker

## 機能

- 対象商品の価格を自動スクレイピング（Playwright使用）
- Googleスプレッドシートへの価格履歴記録
- 価格急変時のアラート通知（Slack）
- GitHub Actionsによる定期実行（1日2回）

## 対応ショップ

| ショップ | 通貨 | 対応状況 |
|---------|------|---------|
| Bullionstar | JPY | 対応済み |
| APMEX | USD | 対応済み |

## セットアップ

### 1. Google Cloud設定

1. [Google Cloud Console](https://console.cloud.google.com/)でプロジェクトを作成
2. Google Sheets APIを有効化
3. サービスアカウントを作成し、JSONキーをダウンロード
4. スプレッドシートをサービスアカウントのメールアドレスと共有

### 2. Googleスプレッドシート設定

以下の3つのシートを作成:

#### 「トラッキング対象」シート
| A | B | C | D |
|---|---|---|---|
| トラッキング状態 | ショップ名 | 商品名 | URL |
| ON | Bullionstar | Gold PAMP 5g | https://... |

#### 「価格履歴」シート
| A | B | C | D | E | F |
|---|---|---|---|---|---|
| 収集日時 | ショップ名 | 商品名 | 価格 | 通貨 | URL |

#### 「設定」シート
| A | B |
|---|---|
| 設定項目名 | 設定値 |
| ALERT_THRESHOLD | 5 |

### 3. GitHub Secrets設定

リポジトリの Settings > Secrets and variables > Actions で以下を設定:

| Secret名 | 内容 |
|----------|------|
| `SPREADSHEET_ID` | GoogleスプレッドシートのID（URLの`/d/`と`/edit`の間の文字列） |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウントのJSONキー（内容をそのまま貼り付け） |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL（任意） |

### 4. ローカル実行

```bash
# 依存パッケージをインストール
pip install -r requirements.txt

# Playwrightブラウザをインストール
python -m playwright install chromium

# 環境変数を設定
export SPREADSHEET_ID="your-spreadsheet-id"
export GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
export SLACK_WEBHOOK_URL="https://hooks.slack.com/..."

# 実行
python -m src.main
```

### 5. テスト実行

```bash
python -m pytest tests/ -v
```

## 実行スケジュール

GitHub Actionsで1日2回自動実行されます:
- JST 9:00 AM
- JST 9:00 PM

手動実行も可能です（Actions > Price Check > Run workflow）

## プロジェクト構成

```
coin-price-checker/
├── .github/workflows/
│   └── price-check.yml      # GitHub Actionsワークフロー
├── src/
│   ├── main.py              # メインエントリーポイント
│   ├── config.py            # 設定管理
│   ├── scraper.py           # スクレイピング管理
│   ├── spreadsheet.py       # Google Sheets連携
│   ├── notifier.py          # 通知機能
│   └── shops/               # ショップ別スクレイパー
│       ├── base.py
│       ├── bullionstar.py
│       └── apmex.py
├── tests/                   # テストコード
├── docs/                    # ドキュメント
│   ├── requirements.md      # 要件定義書
│   ├── tasks.md             # タスク一覧
│   └── tracking_urls.md     # 監視対象URL一覧
├── requirements.txt
└── README.md
```

## ドキュメント

- [要件定義書](./docs/requirements.md)
- [タスク一覧](./docs/tasks.md)
- [監視対象URL一覧](./docs/tracking_urls.md)
- [ページ構造調査結果](./docs/page_structure_analysis.md)

## ライセンス

MIT License
