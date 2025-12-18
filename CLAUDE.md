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
- `COLORME_LOGIN_ID`: カラーミー管理画面ログインID（画像アップロード用）
- `COLORME_PASSWORD`: カラーミー管理画面パスワード（画像アップロード用）
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
- **クライアントシークレット**: ab1457f8b330def3479d7e49262113db77e76baff1471cd1077ccfcf28fa1ced
- **リダイレクトURI**: urn:ietf:wg:oauth:2.0:oob
- **スコープ**: read_products write_products
- **アクセストークン**: 1524f830f51836d36ff8eb32f71755ed996a05c737a822cf72d1474a167b0320

### カラーミー管理画面ログイン情報

- **管理画面URL**: https://admin.shop-pro.jp
- **ユーザー名**: yokohamacoin
- **パスワード**: Fran0833

### カラーミーFTP接続情報

- **FTPホスト**: ftp001.shop-pro.jp
- **FTPアカウント（ユーザー名）**: PA01517852
- **FTPパスワード**: fran0833
- **初期フォルダURL**: https://file001.shop-pro.jp/PA01517/852/

### 画像アップロードについて

- **API経由の画像アップロードは非対応**: `images[].src`に外部URLやBase64を指定しても機能しない
- **FTP+API連携も非対応**: FTPでアップロード後にAPIでURL指定しても機能しない
- **対応方法: Playwright経由のブラウザ自動化**
  - `src/colorme_image_uploader.py` モジュールを使用
  - 管理画面（admin.shop-pro.jp）にログインして商品編集画面から画像をアップロード
  - 環境変数 `COLORME_LOGIN_ID`, `COLORME_PASSWORD` が必要
