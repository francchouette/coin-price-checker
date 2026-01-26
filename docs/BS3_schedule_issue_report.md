# [BS3] 価格・在庫状況更新 スケジュール実行問題 調査報告書

**作成日**: 2026-01-26
**報告者**: Claude Code
**対象ワークフロー**: `.github/workflows/bs-update-prices.yml`

---

## 1. 問題の概要

`[BS3] 価格・在庫状況更新` ワークフローのスケジュール実行が動作していない。
手動実行（workflow_dispatch）は正常に動作するが、cron スケジュールによる自動実行が一度も発生していない。

---

## 2. 調査結果

### 2.1 ワークフロー実行履歴

```
gh run list --workflow=bs-update-prices.yml --limit 10
```

| 日時 (UTC) | イベント | 結果 |
|-----------|---------|------|
| 2026-01-25 07:51:55 | workflow_dispatch | failure |
| 2026-01-25 07:44:14 | workflow_dispatch | cancelled |
| 2026-01-25 02:42:44 | workflow_dispatch | success |

**全て `workflow_dispatch`（手動実行）であり、`schedule` による実行は0件**

### 2.2 他のスケジュールワークフローとの比較

| ワークフロー | スケジュール実行 | 最新実行日時 |
|-------------|----------------|-------------|
| fetch-suppliers.yml | あり | 2026-01-26 08:40:00 |
| price-check.yml | あり | 2026-01-26 08:17:40 |
| **bs-update-prices.yml** | **なし** | - |

他のワークフローは正常にスケジュール実行されている。

### 2.3 ワークフロー設定の確認

**GitHub上のファイル内容（確認済み）**:
```yaml
name: "[BS3] 価格・在庫状況更新"

on:
  schedule:
    - cron: '* * * * *'  # テスト用：毎分実行
  workflow_dispatch:
    # ...
```

**ワークフローの状態**:
```json
{
  "state": "active",
  "path": ".github/workflows/bs-update-prices.yml",
  "created_at": "2026-01-25T11:39:40.000+09:00",
  "updated_at": "2026-01-25T16:41:33.000+09:00"
}
```

ワークフローは `active` 状態であり、無効化されていない。

### 2.4 cron式の比較

| ワークフロー | cron式 | 動作状況 |
|-------------|--------|---------|
| fetch-suppliers.yml | `0 */4 * * *` | 正常 |
| price-check.yml | `0 */4 * * *` | 正常 |
| bs-update-prices.yml（変更前） | 6つの個別cron式 | 動作せず |
| bs-update-prices.yml（変更後） | `* * * * *` | 動作せず |

**変更前の設定（動作しなかった）**:
```yaml
schedule:
  - cron: '0 1 * * *'
  - cron: '0 5 * * *'
  - cron: '0 9 * * *'
  - cron: '0 13 * * *'
  - cron: '0 17 * * *'
  - cron: '0 21 * * *'
```

---

## 3. 実施した対応

| 日時 | 対応内容 | 結果 |
|------|---------|------|
| 2026-01-26 14:32 | cron式を `0 */4 * * *` に変更してプッシュ | 効果なし |
| 2026-01-26 14:32 | cron式を `* * * * *` に変更（テスト用） | 効果なし |
| 2026-01-26 14:45頃 | 空コミットをプッシュして再認識を促す | 効果確認中 |

---

## 4. 考えられる原因

### 4.1 GitHub Actions側の問題（可能性：高）
- ワークフローファイルの変更がGitHub Actionsのスケジューラーに反映されていない
- `updated_at` が `2026-01-25T16:41:33` のままで、最新の変更（2026-01-26）が認識されていない

### 4.2 cron式の問題（可能性：中）
- 変更前の6つの個別cron式がGitHubで正しく解釈されなかった可能性
- `* * * * *`（毎分）はGitHub Actions公式で「最短5分間隔」と注記があり、制限がある可能性

### 4.3 リポジトリの設定問題（可能性：低）
- 他のワークフローは動作しているため、リポジトリレベルの問題ではない

---

## 5. 推奨対応

### 即時対応
1. **ワークフローファイルを一度削除して再作成する**
   - GitHub Actionsのスケジューラーキャッシュをクリアする効果がある

2. **cron式を他の動作しているワークフローと同じ形式に統一**
   ```yaml
   schedule:
     - cron: '0 */4 * * *'
   ```

### 検証手順
1. ワークフローファイルを再作成後、10-15分待機
2. `gh run list --workflow=bs-update-prices.yml` で `event: schedule` の実行を確認

---

## 6. 参考情報

### GitHub Actionsスケジュール実行の制約
- 最短実行間隔: 5分
- 高負荷時の遅延: 最大15-30分
- デフォルトブランチ（main）のワークフローのみスケジュール実行可能
- 60日間コミットがないリポジトリはスケジュール無効化

### 関連ドキュメント
- [GitHub Actions: Events that trigger workflows - schedule](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule)

---

## 7. 添付資料

### A. ワークフロー実行ログ取得コマンド
```bash
# 実行履歴の確認
gh run list --workflow=bs-update-prices.yml --limit 20 --json event,status,conclusion,createdAt

# ワークフローの状態確認
gh api repos/francchouette/coin-price-checker/actions/workflows/226745346

# GitHub上のファイル内容確認
gh api repos/francchouette/coin-price-checker/contents/.github/workflows/bs-update-prices.yml --jq '.content' | base64 -d
```

### B. 比較対象ワークフローの設定

**fetch-suppliers.yml（正常動作）**:
```yaml
on:
  schedule:
    - cron: '0 */4 * * *'
```

**price-check.yml（正常動作）**:
```yaml
on:
  schedule:
    - cron: '0 */4 * * *'
```

---

以上
