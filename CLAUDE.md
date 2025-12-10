# CLAUDE.md

このファイルはClaude Codeがこのリポジトリで作業する際のガイダンスを提供します。

## GCPプロジェクト情報

- **プロジェクト名**: coin-price-tracker
- **プロジェクト番号**: 613320899872
- **プロジェクト ID**: coin-price-tracker-479614

## 必要な環境変数

### GitHub Secrets（本番環境）
- `SPREADSHEET_ID`: Google スプレッドシートのID
- `COLORME_ACCESS_TOKEN`: カラーミーショップAPIのアクセストークン
- `GOOGLE_SERVICE_ACCOUNT_JSON`: GCPサービスアカウントの認証情報（JSON）

### ローカル開発
```bash
# ADC認証（スプレッドシートスコープ付き）
gcloud auth application-default login --scopes="openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets"
gcloud auth application-default set-quota-project coin-price-tracker-479614

# 環境変数
export SPREADSHEET_ID="1flJBKOu6MP--XmXctF5IfM5aLtyggH87dCQXu4qPOUE"
export COLORME_ACCESS_TOKEN="your-token-here"
```

## カラーミーAPI

- **クライアントID**: 42d93e7bbc9ed8469bc1e5c0da0c2d53b59ebc288dd65154d00649431367ae2a
- **リダイレクトURI**: urn:ietf:wg:oauth:2.0:oob
- **スコープ**: read_products write_products
