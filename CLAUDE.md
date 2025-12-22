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

## 重要なアーキテクチャ決定事項

### スプレッドシート列のトリガー

| アクション | コマンド | トリガー列 | 説明 |
|-----------|---------|-----------|------|
| price-check | `python -m src.main` | H列（価格更新ON/OFF） | H列がONの商品のみカラーミーに価格更新 |
| add-product | `python -m src.add_product` | AC列（同期モード） | AC列の値で処理分岐 |
| sync-colorme | `python -m src.main --sync-colorme` | AC列（同期モード） | AC列の値で処理分岐 |

### 価格計算

- **T列（販売適正価格）の値をそのままカラーミーに登録**
- T列はシート上で数式 `=Q/(2-F)+G+R+S` などで計算される
- コードは独自計算せず、T列の値を直接使用する
- T列が0または空の場合は価格更新をスキップ

### 商品一覧の差分追加

- **Bullionstar商品一覧** (`src/bullionstar_products.py`): URLをユニークキーとして差分のみ追加
- **APMEX商品一覧**: 同様にURLで差分チェック
- 毎回全件追加すると重複が発生するため、必ず差分チェックを行うこと

## 商品仕入れ先管理アーキテクチャ

### 概要

5つの仕入れ先サイト（Bullionstar、APMEX、他3サイト予定）から商品を取得し、カラーミーで販売するための統合管理システム。

### シート構成

| シート名 | 役割 | 列数 | 主要な操作 |
|---------|------|-----|-----------|
| ブリオンスター商品ページ一覧 | Bullionstar商品のマスタ＋採用管理 | 40列（A-AN） | `bullionstar_products.py` で自動取得 |
| 商品仕入れ先一覧 | **全仕入れ先の統合マスタ** | 36列（A-AJ） | BS商品取得時に自動同期、APMEX等は手動追加 |
| 新カラーミー商品管理 | カラーミー販売商品の管理 | 86列（A-CH） | `download_colorme_products.py` / `sync_colorme_products.py` |

### ブリオンスター商品ページ一覧シート

**40列構造（A-AN）**:

- A: 採用フラグ（「採用」「未採用」「検討中」）← ブリオンスター専用
- B: カラーミー登録状況（「登録済」「未登録」）← ブリオンスター専用
- C: 仕入れ先商品ID（BS-XXXXXX）
- D: 仕入れ先商品名
- E: 仕入れ先商品URL（ユニークキー）
- F: 仕入れ先サイト（固定値: Bullionstar）
- G-I: カテゴリ（最上位/親/子）
- J: 製造国
- K: 初回取得日
- L: 商品グループID
- M: 在庫状況
- N-O: 価格/通貨
- P-R: 為替種類/レート/日本円換算
- S-U: 更新日時/前回価格/変動率
- V-X: 採用理由/カラーミーID/備考
- Y-AH: 画像URL（10列）
- AI-AM: 仕様/説明/発行年/発行数
- AN: 仕入れ先商品ID(重複) ※商品仕入れ先一覧との連携用

### 商品仕入れ先一覧シート（統合マスタ）

**36列構造（A-AJ）**:

- A: 仕入れ先商品ID（SP-XXXXXX）
- B: 仕入れ先商品名
- C: 仕入れ先商品URL（ユニークキー）
- D: 仕入れ先サイト（Bullionstar / APMEX 等）
- E-G: カテゴリ（最上位/親/子）
- H: 製造国
- I: 初回取得日
- J: 商品グループID
- K: 在庫状況
- L-M: 価格/通貨
- N-P: 為替種類/レート/日本円換算
- Q-S: 更新日時/前回価格/変動率
- T-U: カラーミーID/備考
- V-AE: 画像URL（10列）
- AF-AJ: 仕様/説明/発行年/発行数

※採用フラグ・カラーミー登録状況は含まない（ブリオンスター商品ページ一覧のみ）

### 新カラーミー商品管理シート

**仕入れ先情報はVLOOKUP/INDEX-MATCHで取得**:

- J列: 仕入れ先商品URL（手動入力）
- K列以降: 商品仕入れ先一覧シートからINDEX/MATCHで自動取得

```text
# K列の数式例（仕入れ先商品名を取得）
=IFERROR(INDEX(商品仕入れ先一覧!$B:$B,MATCH($J2,商品仕入れ先一覧!$C:$C,0)),"")
```

### 仕入れ先商品ID形式

| プレフィックス | 用途 | 例 |
|--------------|------|-----|
| BS- | ブリオンスター商品ページ一覧シート専用 | BS-000001 |
| SP- | 商品仕入れ先一覧シート（統合マスタ） | SP-000001 |

### 業務フロー

```text
1. 仕入れ先から商品一覧を取得
   - Bullionstar: `python -m src.bullionstar_products`
     → ブリオンスター商品ページ一覧に保存（商品仕入れ先一覧には未反映）
   - APMEX: 手動で商品仕入れ先一覧に追加

2. 採用商品をカラーミーに自動登録
   - ブリオンスター商品ページ一覧でA列を「採用」に変更
   - `python -m src.register_adopted_products` を実行
     → カラーミーAPIで自動登録
     → B列を「登録済」に自動更新
     → W列にカラーミー商品IDを自動設定
     → 商品仕入れ先一覧に自動同期
   - 画像アップロードは `src/colorme_image_uploader.py` で別途実行

3. 商品仕入れ先一覧に同期（手動実行時）
   - `python -m src.sync_supplier_list` を実行
     → B列=「登録済」の商品のみ商品仕入れ先一覧にコピー
   - これにより新カラーミー商品管理シートからVLOOKUPで参照可能に
   - ※ `register_adopted_products` 実行時は自動で同期される

4. 新カラーミー商品管理シートで運用
   - `python -m src.download_colorme_products` でカラーミー商品をダウンロード
   - J列に仕入れ先商品URLを手動入力
   - K列以降は商品仕入れ先一覧からVLOOKUPで自動反映
   - `python -m src.sync_colorme_products` で価格・在庫をカラーミーに同期
```

### 在庫・表示連動ロジック

**新カラーミー商品管理シートのトリガー列**:

| 列 | 名称 | 値 | 動作 |
|----|------|-----|------|
| C | 価格更新ON/OFF | ON/OFF | ONの場合のみ価格をカラーミーに更新 |
| D | 在庫連動ON/OFF | ON | U列（仕入れ先在庫）に連動して在庫数を設定 |
| E | 表示連動 | 連動 | U列に連動して表示/非表示を切替 |
| U | 仕入れ先在庫状況 | In Stock / Out of Stock | D列・E列の判定基準 |

**在庫連動（D列=ON）**:

- U列が "In Stock" → AW列の在庫数を使用
- U列が "Out of Stock" → 在庫を0に設定

**表示連動（E列=連動）**:

- U列が "In Stock" → 掲載する
- U列が "Out of Stock" → 掲載しない

### コマンドオプション

```bash
# Bullionstar商品一覧取得（価格なし）
python -m src.bullionstar_products

# 価格・在庫も取得
python -m src.bullionstar_products --fetch-prices

# 件数制限（テスト用）
python -m src.bullionstar_products --fetch-prices --limit 100

# 為替種類指定
python -m src.bullionstar_products --fetch-prices --exchange-type Wise

# 商品仕入れ先一覧に同期（B列=「登録済」の商品のみ）
python -m src.sync_supplier_list

# 採用商品をカラーミーに自動登録（B列・W列自動更新、仕入れ先一覧同期含む）
python -m src.register_adopted_products

# ドライラン（実際の登録なし）
python -m src.register_adopted_products --dry-run

# 件数制限
python -m src.register_adopted_products --limit 5

# 仕入れ先一覧同期をスキップ
python -m src.register_adopted_products --skip-sync
```
