# セットアップガイド

## 1. Google Cloud設定

### 1.1 プロジェクト作成
1. [Google Cloud Console](https://console.cloud.google.com/) にアクセス
2. 「プロジェクトを作成」をクリック
3. プロジェクト名: `coin-price-tracker` (任意)
4. 「作成」をクリック

### 1.2 Google Sheets API有効化
1. 左メニュー → 「APIとサービス」→「ライブラリ」
2. 「Google Sheets API」を検索
3. 「有効にする」をクリック

### 1.3 サービスアカウント作成
1. 左メニュー → 「APIとサービス」→「認証情報」
2. 「認証情報を作成」→「サービスアカウント」
3. 設定:
   - サービスアカウント名: `spreadsheet-access`
   - 「作成して続行」をクリック
   - ロールは空のまま「完了」

### 1.4 JSONキー取得
1. 作成したサービスアカウントをクリック
2. 「キー」タブ → 「鍵を追加」→「新しい鍵を作成」
3. キーのタイプ: JSON
4. 「作成」→ JSONファイルがダウンロードされる

**重要**: このJSONファイルの内容をGitHub Secretsに設定します。

---

## 2. Googleスプレッドシート設定

### 2.1 スプレッドシート作成
1. [Google スプレッドシート](https://sheets.google.com/) にアクセス
2. 「空白」をクリックして新規作成
3. ファイル名: `コイン価格トラッキング` (任意)

### 2.2 シートの作成
3つのシートを作成します（下部のタブで「+」をクリック）:
- `トラッキング対象`
- `価格履歴`
- `設定`

### 2.3 データのインポート
`templates/` フォルダ内のCSVファイルを各シートにインポート:

1. 各シートで「ファイル」→「インポート」
2. 「アップロード」タブで対応するCSVをアップロード
3. 「現在のシートを置換する」を選択
4. 「データをインポート」

| シート名 | CSVファイル |
|---------|------------|
| トラッキング対象 | `tracking_targets.csv` |
| 価格履歴 | `price_history.csv` |
| 設定 | `settings.csv` |

### 2.4 サービスアカウントと共有
1. スプレッドシートの右上「共有」をクリック
2. サービスアカウントのメールアドレスを入力
   - 形式: `xxxx@project-name.iam.gserviceaccount.com`
   - JSONファイルの `client_email` の値
3. 権限: 「編集者」
4. 「送信」をクリック

### 2.5 スプレッドシートIDの取得
URLからIDをコピー:
```
https://docs.google.com/spreadsheets/d/【ここがID】/edit
```

---

## 3. GitHub Secrets設定

### 3.1 Secrets設定画面を開く
1. GitHubリポジトリ: https://github.com/francchouette/coin-price-checker
2. 「Settings」タブ
3. 左メニュー「Secrets and variables」→「Actions」
4. 「New repository secret」

### 3.2 必須Secrets

#### SPREADSHEET_ID
- Name: `SPREADSHEET_ID`
- Secret: スプレッドシートのID（2.5で取得した値）

#### GOOGLE_SERVICE_ACCOUNT_JSON
- Name: `GOOGLE_SERVICE_ACCOUNT_JSON`
- Secret: ダウンロードしたJSONファイルの**中身全体**をコピー&ペースト

### 3.3 オプションSecrets

#### SLACK_WEBHOOK_URL（任意）
Slack通知を使用する場合:
1. [Slack API](https://api.slack.com/apps) でアプリを作成
2. 「Incoming Webhooks」を有効化
3. Webhook URLをコピー
4. GitHub Secretsに設定:
   - Name: `SLACK_WEBHOOK_URL`
   - Secret: `https://hooks.slack.com/services/...`

---

## 4. 動作確認

### 4.1 手動実行
1. GitHubリポジトリの「Actions」タブ
2. 左メニュー「Price Check」
3. 「Run workflow」→「Run workflow」

### 4.2 ログ確認
実行中のワークフローをクリックしてログを確認

### 4.3 スプレッドシート確認
「価格履歴」シートにデータが追加されていれば成功

---

## 5. トラブルシューティング

### エラー: 「SPREADSHEET_ID が設定されていません」
→ GitHub SecretsにSPREADSHEET_IDが設定されているか確認

### エラー: 「スプレッドシートへの接続に失敗しました」
→ 以下を確認:
- GOOGLE_SERVICE_ACCOUNT_JSONが正しいJSON形式か
- スプレッドシートがサービスアカウントと共有されているか
- Google Sheets APIが有効化されているか

### エラー: 「価格を取得できませんでした」
→ 対象サイトの構造が変更された可能性あり。issueで報告してください。

---

## 6. 運用

### 実行スケジュール
- 毎日 JST 9:00 AM
- 毎日 JST 9:00 PM

### 商品の追加・削除
スプレッドシートの「トラッキング対象」シートを編集:
- 追加: 新しい行を追加
- 削除: 行を削除、または「トラッキング状態」を`OFF`に変更

### アラート閾値の変更
「設定」シートの`ALERT_THRESHOLD`の値を変更（デフォルト: 5%）
