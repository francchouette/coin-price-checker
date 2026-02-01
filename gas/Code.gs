/**
 * 商品管理システム - Google Apps Script
 *
 * スプレッドシートの拡張メニューからGitHub Actionsをトリガーする
 *
 * セットアップ手順:
 * 1. このスクリプトをスプレッドシートのApps Scriptにコピー
 * 2. GITHUB_TOKEN をスクリプトプロパティに設定
 *    - ファイル > プロジェクトの設定 > スクリプトプロパティ
 *    - プロパティ名: GITHUB_TOKEN
 *    - 値: GitHub Personal Access Token (workflow権限が必要)
 */

// ========================================
// 設定
// ========================================

const REPO_OWNER = 'francchouette';  // GitHubリポジトリのオーナー
const REPO_NAME = 'coin-price-checker';  // リポジトリ名

// ワークフロー定義
const WORKFLOWS = {
  BS1_FETCH: 'bs1-fetch-products.yml',       // Bullionstar: 新商品取得
  BS1_UPDATE: 'bs1-update-prices.yml',       // Bullionstar: 価格・在庫更新
  BS2_REGISTER: 'bs2-register-products.yml', // Bullionstar: カラーミー登録
  CM_SYNC: 'cm-sync-prices.yml',             // カラーミー: 価格同期（フル）
  CM_QUICK_SYNC: 'cm-quick-sync.yml'         // カラーミー: クイック同期
};

// ========================================
// メニュー
// ========================================

/**
 * スプレッドシートを開いたときにカスタムメニューを追加
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('🛒 商品管理')
    .addSubMenu(ui.createMenu('Bullionstar')
      .addItem('📦 新商品を取得', 'bs1FetchProducts')
      .addItem('💰 価格・在庫を更新', 'bs1UpdatePrices')
      .addSeparator()
      .addItem('✅ 採用商品をカラーミーに登録', 'bs2RegisterProducts')
      .addItem('🧪 登録テスト（Dry Run）', 'bs2RegisterProductsDryRun'))
    .addSeparator()
    .addSubMenu(ui.createMenu('カラーミー')
      .addItem('⚡ クイック同期（価格取得なし）', 'cmQuickSync')
      .addItem('🔄 フル同期（価格取得あり）', 'cmSyncPrices'))
    .addSeparator()
    .addSubMenu(ui.createMenu('⚙️ 設定')
      .addItem('GitHub Token確認', 'checkGitHubTokenStatus')
      .addItem('GitHub Actionsを開く', 'openGitHubActions')
      .addSeparator()
      .addItem('🗑️ 不要な列を削除', 'deleteUnnecessaryColumns'))
    .addToUi();
}

// ========================================
// GitHub Actions トリガー
// ========================================

/**
 * GitHub Actions workflow_dispatch をトリガーする
 *
 * @param {string} workflowFileName - ワークフローファイル名
 * @param {Object} inputs - ワークフローの入力パラメータ
 * @returns {boolean} 成功した場合 true
 */
function triggerGitHubAction(workflowFileName, inputs = {}) {
  const token = getGitHubToken();
  if (!token) {
    SpreadsheetApp.getUi().alert(
      '⚠️ エラー',
      'GitHub Tokenが設定されていません。\n\n' +
      '【設定方法】\n' +
      '1. 拡張機能 > Apps Script を開く\n' +
      '2. ⚙️ プロジェクトの設定 > スクリプトプロパティ\n' +
      '3. プロパティを追加:\n' +
      '   プロパティ名: GITHUB_TOKEN\n' +
      '   値: GitHubのPersonal Access Token',
      SpreadsheetApp.getUi().ButtonSet.OK
    );
    return false;
  }

  const url = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${workflowFileName}/dispatches`;

  const payload = {
    ref: 'main',
    inputs: inputs
  };

  const options = {
    method: 'post',
    headers: {
      'Accept': 'application/vnd.github.v3+json',
      'Authorization': `token ${token}`,
      'Content-Type': 'application/json'
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    const response = UrlFetchApp.fetch(url, options);
    const statusCode = response.getResponseCode();

    if (statusCode === 204) {
      return true;
    } else {
      Logger.log(`GitHub API Error: ${statusCode} - ${response.getContentText()}`);
      return false;
    }
  } catch (e) {
    Logger.log(`Error triggering GitHub Action: ${e.message}`);
    return false;
  }
}

/**
 * スクリプトプロパティからGitHub Tokenを取得
 */
function getGitHubToken() {
  const props = PropertiesService.getScriptProperties();
  return props.getProperty('GITHUB_TOKEN');
}

// ========================================
// Bullionstar: 商品取得・更新
// ========================================

/**
 * Bullionstar: 新商品を取得
 * Bullionstar社のサイトから商品一覧を取得してスプレッドシートに保存
 */
function bs1FetchProducts() {
  const ui = SpreadsheetApp.getUi();

  const result = ui.alert(
    '📦 Bullionstar: 新商品を取得',
    '【対象】Bullionstar社の商品のみ\n' +
    '【対象シート】ブリオンスター商品ページ一覧\n\n' +
    'Bullionstarのサイトから新商品を取得します。\n\n' +
    '【処理内容】\n' +
    '・Bullionstar商品一覧ページをスキャン\n' +
    '・新商品を「ブリオンスター商品ページ一覧」シートに追加\n' +
    '・AI商品名を自動生成\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction(WORKFLOWS.BS1_FETCH, {
    action: '新規取得'
  });

  showResultMessage(success, 'Bullionstar: 新商品取得');
}

/**
 * Bullionstar: 価格・在庫を更新
 * Bullionstar社の検討中商品の価格と在庫状況を更新
 */
function bs1UpdatePrices() {
  const ui = SpreadsheetApp.getUi();

  const result = ui.alert(
    '💰 Bullionstar: 価格・在庫を更新',
    '【対象】Bullionstar社の商品のみ\n' +
    '【対象シート】ブリオンスター商品ページ一覧\n\n' +
    '検討中のBullionstar商品の価格・在庫を更新します。\n\n' +
    '【処理内容】\n' +
    '・Bullionstarの各商品ページにアクセス\n' +
    '・最新価格を取得\n' +
    '・在庫状況を確認\n' +
    '・為替レートで日本円換算\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction(WORKFLOWS.BS1_UPDATE, {});

  showResultMessage(success, 'Bullionstar: 価格・在庫更新');
}

// ========================================
// Bullionstar: カラーミー登録
// ========================================

/**
 * Bullionstar: 採用商品をカラーミーに登録
 */
function bs2RegisterProducts() {
  const ui = SpreadsheetApp.getUi();

  // 採用商品の件数を確認
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('ブリオンスター商品ページ一覧');
  if (!sheet) {
    ui.alert('⚠️ エラー', '「ブリオンスター商品ページ一覧」シートが見つかりません。', ui.ButtonSet.OK);
    return;
  }

  const data = sheet.getDataRange().getValues();
  let adoptedCount = 0;
  for (let i = 1; i < data.length; i++) {
    if (data[i][0] === '採用' && data[i][1] !== '登録済') {  // A列: 採用フラグ, B列: 登録状況
      adoptedCount++;
    }
  }

  if (adoptedCount === 0) {
    ui.alert(
      '📋 対象なし',
      '【対象】Bullionstar社の商品のみ\n\n' +
      '登録待ちの採用商品がありません。\n\n' +
      '【操作方法】\n' +
      '1. 「ブリオンスター商品ページ一覧」シートを開く\n' +
      '2. 登録したいBullionstar商品のA列を「採用」に変更\n' +
      '3. このメニューを再実行',
      ui.ButtonSet.OK
    );
    return;
  }

  const result = ui.alert(
    '✅ Bullionstar: カラーミーに登録',
    '【対象】Bullionstar社の商品のみ\n' +
    '【対象シート】ブリオンスター商品ページ一覧\n\n' +
    `${adoptedCount}件のBullionstar採用商品をカラーミーに登録します。\n\n` +
    '【処理内容】\n' +
    '・カラーミーAPIで商品を新規登録\n' +
    '・画像をアップロード\n' +
    '・登録完了後、B列を「登録済」に更新\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction(WORKFLOWS.BS2_REGISTER, {
    dry_run: 'false'
  });

  showResultMessage(success, 'Bullionstar: カラーミー登録');
}

/**
 * Bullionstar: 登録テスト（Dry Run）
 * 実際の登録は行わず、処理内容を確認
 */
function bs2RegisterProductsDryRun() {
  const ui = SpreadsheetApp.getUi();

  const result = ui.alert(
    '🧪 Bullionstar: 登録テスト（Dry Run）',
    '【対象】Bullionstar社の商品のみ\n' +
    '【対象シート】ブリオンスター商品ページ一覧\n\n' +
    'カラーミー登録のテストを実行します。\n\n' +
    '【テスト内容】\n' +
    '・Bullionstar採用商品の確認\n' +
    '・登録データの検証\n' +
    '・実際の登録は行いません\n\n' +
    'テストを開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction(WORKFLOWS.BS2_REGISTER, {
    dry_run: 'true'
  });

  showResultMessage(success, 'Bullionstar: 登録テスト');
}

// ========================================
// カラーミー: 同期
// ========================================

/**
 * カラーミー: クイック同期（価格取得なし）
 * ダウンロード → カラーミーに同期（高速）
 */
function cmQuickSync() {
  const ui = SpreadsheetApp.getUi();

  // A列=「更新」の商品数をカウント
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('新カラーミー商品管理');
  let updateCount = 0;
  if (sheet) {
    const data = sheet.getDataRange().getValues();
    for (let i = 1; i < data.length; i++) {
      if (data[i][0] === '更新') {  // A列: 同期モード
        updateCount++;
      }
    }
  }

  const result = ui.alert(
    '⚡ カラーミー: クイック同期',
    '【対象】全仕入れ先の商品\n' +
    '【対象シート】新カラーミー商品管理\n\n' +
    '⚡ 価格取得をスキップして高速同期します\n\n' +
    '【Step 1】カラーミーから商品をダウンロード\n' +
    '【Step 2】A列=「更新」の商品をカラーミーに同期\n' +
    (updateCount > 0 ? `　　　　 （現在 ${updateCount} 件が対象）\n` : '') +
    '\n' +
    '⏱️ 所要時間: 約3〜5分\n\n' +
    '※ シートの価格情報をそのまま使用します\n' +
    '※ 最新価格が必要な場合は「フル同期」を使用\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction(WORKFLOWS.CM_QUICK_SYNC, {});

  showResultMessage(success, 'カラーミー: クイック同期');
}

/**
 * カラーミー: フル同期（価格取得あり）
 * ダウンロード → 価格取得 → カラーミーに同期
 */
function cmSyncPrices() {
  const ui = SpreadsheetApp.getUi();

  // A列=「更新」の商品数をカウント
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('新カラーミー商品管理');
  let updateCount = 0;
  if (sheet) {
    const data = sheet.getDataRange().getValues();
    for (let i = 1; i < data.length; i++) {
      if (data[i][0] === '更新') {  // A列: 同期モード
        updateCount++;
      }
    }
  }

  const result = ui.alert(
    '🔄 カラーミー: フル同期',
    '【対象】全仕入れ先の商品\n' +
    '【対象シート】新カラーミー商品管理\n\n' +
    '🔄 仕入れ先から最新価格を取得して同期します\n\n' +
    '【Step 1】カラーミーから商品をダウンロード\n' +
    '【Step 2】仕入れ先URLから最新価格を取得\n' +
    '【Step 3】A列=「更新」の商品をカラーミーに同期\n' +
    (updateCount > 0 ? `　　　　 （現在 ${updateCount} 件が対象）\n` : '') +
    '\n' +
    '⏱️ 所要時間: 約10〜20分（価格取得件数による）\n\n' +
    '※ 急ぎの場合は「クイック同期」を使用\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction(WORKFLOWS.CM_SYNC, {});

  showResultMessage(success, 'カラーミー: フル同期');
}

// ========================================
// 設定・ユーティリティ
// ========================================

/**
 * 結果メッセージを表示
 */
function showResultMessage(success, actionName) {
  const ui = SpreadsheetApp.getUi();

  if (success) {
    ui.alert(
      '🚀 処理開始',
      `${actionName}を開始しました。\n\n` +
      '処理完了まで数分かかる場合があります。\n' +
      '進捗はGitHub Actionsページで確認できます。\n\n' +
      `📎 ${getActionsUrl()}`,
      ui.ButtonSet.OK
    );
  } else {
    ui.alert(
      '❌ エラー',
      `${actionName}の開始に失敗しました。\n\n` +
      '【確認事項】\n' +
      '・GitHub Tokenが正しく設定されているか\n' +
      '・Tokenに workflow 権限があるか\n' +
      '・リポジトリ名が正しいか',
      ui.ButtonSet.OK
    );
  }
}

/**
 * GitHub Tokenの設定状態を確認
 */
function checkGitHubTokenStatus() {
  const ui = SpreadsheetApp.getUi();
  const token = getGitHubToken();

  if (token) {
    ui.alert(
      '✅ GitHub Token',
      `GitHub Tokenが設定されています。\n\n` +
      `文字数: ${token.length}文字\n` +
      `先頭: ${token.substring(0, 4)}...`,
      ui.ButtonSet.OK
    );
  } else {
    ui.alert(
      '⚠️ GitHub Token未設定',
      'GitHub Tokenが設定されていません。\n\n' +
      '【設定方法】\n' +
      '1. 拡張機能 > Apps Script を開く\n' +
      '2. ⚙️ プロジェクトの設定\n' +
      '3. スクリプトプロパティ > プロパティを追加\n' +
      '   プロパティ名: GITHUB_TOKEN\n' +
      '   値: GitHubのPersonal Access Token\n\n' +
      '【Token作成方法】\n' +
      '1. GitHub > Settings > Developer settings\n' +
      '2. Personal access tokens > Tokens (classic)\n' +
      '3. Generate new token\n' +
      '4. workflow にチェックを入れて作成',
      ui.ButtonSet.OK
    );
  }
}

/**
 * GitHub Actionsページを開く
 */
function openGitHubActions() {
  const ui = SpreadsheetApp.getUi();
  const url = getActionsUrl();

  ui.alert(
    '🔗 GitHub Actions',
    `以下のURLをブラウザで開いてください：\n\n${url}`,
    ui.ButtonSet.OK
  );
}

/**
 * GitHub Actions の実行履歴URLを取得
 */
function getActionsUrl() {
  return `https://github.com/${REPO_OWNER}/${REPO_NAME}/actions`;
}

// ========================================
// シート管理ユーティリティ
// ========================================

/**
 * 新カラーミー商品管理シートから不要な列を削除
 * 削除対象:
 * - M列〜T列（最上位カテゴリ〜発行数・限定数）
 * - AU列〜AV列（小カテゴリーID・小カテゴリー名）
 * - CJ列〜CK列（商品作成日時・商品更新日時）
 */
function deleteUnnecessaryColumns() {
  const ui = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('新カラーミー商品管理');

  if (!sheet) {
    ui.alert('⚠️ エラー', '「新カラーミー商品管理」シートが見つかりません。', ui.ButtonSet.OK);
    return;
  }

  const result = ui.alert(
    '⚠️ 列の削除',
    '以下の列を削除します：\n\n' +
    '・M列〜T列（最上位カテゴリ〜発行数・限定数）\n' +
    '・AU列〜AV列（小カテゴリーID・小カテゴリー名）\n' +
    '・CJ列〜CK列（商品作成日時・商品更新日時）\n\n' +
    '⚠️ この操作は取り消せません。\n' +
    '続行しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  try {
    // 右側の列から順に削除（列番号がずれないように）
    // CJ列〜CK列（88〜89列目）
    sheet.deleteColumns(88, 2);

    // AU列〜AV列（47〜48列目）
    sheet.deleteColumns(47, 2);

    // M列〜T列（13〜20列目）
    sheet.deleteColumns(13, 8);

    ui.alert(
      '✅ 完了',
      '不要な列を削除しました。\n\n' +
      '削除された列：\n' +
      '・M列〜T列（8列）\n' +
      '・AU列〜AV列（2列）\n' +
      '・CJ列〜CK列（2列）\n\n' +
      '合計12列を削除しました。',
      ui.ButtonSet.OK
    );
  } catch (e) {
    ui.alert(
      '❌ エラー',
      `列の削除中にエラーが発生しました：\n\n${e.message}`,
      ui.ButtonSet.OK
    );
  }
}
