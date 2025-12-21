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
 * 3. REPO_OWNER と REPO_NAME を適宜変更
 */

// ========================================
// 設定
// ========================================

const REPO_OWNER = 'abehiroshi';  // GitHubリポジトリのオーナー
const REPO_NAME = 'coin-price-check-script';  // リポジトリ名

// ========================================
// メニュー
// ========================================

/**
 * スプレッドシートを開いたときにカスタムメニューを追加
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('商品管理')
    .addItem('初期登録一覧を生成', 'generateInitialRegistration')
    .addItem('確認済み商品をカラーミーに登録', 'uploadToColorme')
    .addSeparator()
    .addItem('新シートを初期化', 'initializeNewSheets')
    .addSeparator()
    .addSubMenu(ui.createMenu('価格更新')
      .addItem('価格チェックを実行', 'runPriceCheck')
      .addItem('仕入れ先価格を更新', 'updateSupplierPrices'))
    .addToUi();
}

// ========================================
// GitHub Actions トリガー
// ========================================

/**
 * GitHub Actions workflow_dispatch をトリガーする
 *
 * @param {string} workflowFileName - ワークフローファイル名（例: 'generate-initial-registration.yml'）
 * @param {Object} inputs - ワークフローの入力パラメータ
 * @returns {boolean} 成功した場合 true
 */
function triggerGitHubAction(workflowFileName, inputs = {}) {
  const token = getGitHubToken();
  if (!token) {
    SpreadsheetApp.getUi().alert(
      'エラー',
      'GitHub Tokenが設定されていません。\n' +
      'スクリプトプロパティに GITHUB_TOKEN を設定してください。',
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
// メニューアクション
// ========================================

/**
 * 初期登録一覧を生成
 *
 * 商品仕入れ先一覧から未登録の採用商品を抽出し、
 * AIで商品情報を生成して初期登録一覧に追加
 */
function generateInitialRegistration() {
  const ui = SpreadsheetApp.getUi();

  // 確認ダイアログ
  const result = ui.alert(
    '初期登録一覧を生成',
    '仕入れ先一覧から未登録の採用商品を抽出し、\n' +
    'AIで商品情報を生成します。\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  // GitHub Actionをトリガー
  const success = triggerGitHubAction('generate-initial-registration.yml', {});

  if (success) {
    ui.alert(
      '処理開始',
      '初期登録一覧の生成を開始しました。\n\n' +
      '処理完了まで数分かかる場合があります。\n' +
      'GitHub Actionsページで進捗を確認できます。',
      ui.ButtonSet.OK
    );
  } else {
    ui.alert(
      'エラー',
      'GitHub Actionのトリガーに失敗しました。\n' +
      '設定を確認してください。',
      ui.ButtonSet.OK
    );
  }
}

/**
 * 確認済み商品をカラーミーに登録
 *
 * 初期登録一覧で「確認済」ステータスの商品を
 * カラーミーに一括登録
 */
function uploadToColorme() {
  const ui = SpreadsheetApp.getUi();

  // 確認済み商品の件数を取得
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('新カラーミー商品初期登録一覧');
  if (!sheet) {
    ui.alert('エラー', '「新カラーミー商品初期登録一覧」シートが見つかりません。', ui.ButtonSet.OK);
    return;
  }

  const data = sheet.getDataRange().getValues();
  let confirmedCount = 0;
  for (let i = 1; i < data.length; i++) {
    if (data[i][80] === '確認済') {  // CC列: 登録ステータス
      confirmedCount++;
    }
  }

  if (confirmedCount === 0) {
    ui.alert(
      '対象なし',
      '登録待ちの確認済み商品がありません。\n\n' +
      '初期登録一覧で商品を確認し、\n' +
      '登録ステータスを「確認済」に変更してください。',
      ui.ButtonSet.OK
    );
    return;
  }

  // 確認ダイアログ
  const result = ui.alert(
    'カラーミーに登録',
    `${confirmedCount}件の確認済み商品を\n` +
    'カラーミーに登録します。\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  // GitHub Actionをトリガー
  const success = triggerGitHubAction('upload-to-colorme.yml', {});

  if (success) {
    ui.alert(
      '処理開始',
      'カラーミーへの登録を開始しました。\n\n' +
      '処理完了まで数分かかる場合があります。\n' +
      'GitHub Actionsページで進捗を確認できます。',
      ui.ButtonSet.OK
    );
  } else {
    ui.alert(
      'エラー',
      'GitHub Actionのトリガーに失敗しました。\n' +
      '設定を確認してください。',
      ui.ButtonSet.OK
    );
  }
}

/**
 * 新シートを初期化
 *
 * 3つの新シートを作成し、ヘッダーを設定
 */
function initializeNewSheets() {
  const ui = SpreadsheetApp.getUi();

  // 確認ダイアログ
  const result = ui.alert(
    '新シートを初期化',
    '以下の3つのシートを作成します：\n\n' +
    '1. 商品仕入れ先一覧\n' +
    '2. 新カラーミー商品初期登録一覧\n' +
    '3. 新カラーミー商品管理\n\n' +
    '既存のシートがある場合はスキップされます。\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  // GitHub Actionをトリガー
  const success = triggerGitHubAction('initialize-sheets.yml', {});

  if (success) {
    ui.alert(
      '処理開始',
      'シートの初期化を開始しました。\n\n' +
      '処理完了後、シートが追加されます。',
      ui.ButtonSet.OK
    );
  } else {
    ui.alert(
      'エラー',
      'GitHub Actionのトリガーに失敗しました。\n' +
      '設定を確認してください。',
      ui.ButtonSet.OK
    );
  }
}

/**
 * 価格チェックを実行
 */
function runPriceCheck() {
  const ui = SpreadsheetApp.getUi();

  const result = ui.alert(
    '価格チェック',
    '価格チェックを実行します。\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction('price-check.yml', {});

  if (success) {
    ui.alert(
      '処理開始',
      '価格チェックを開始しました。\n\n' +
      '処理完了まで数分かかる場合があります。',
      ui.ButtonSet.OK
    );
  } else {
    ui.alert(
      'エラー',
      'GitHub Actionのトリガーに失敗しました。',
      ui.ButtonSet.OK
    );
  }
}

/**
 * 仕入れ先価格を更新
 */
function updateSupplierPrices() {
  const ui = SpreadsheetApp.getUi();

  const result = ui.alert(
    '仕入れ先価格更新',
    '仕入れ先一覧の価格・在庫を更新します。\n\n' +
    '処理を開始しますか？',
    ui.ButtonSet.YES_NO
  );

  if (result !== ui.Button.YES) {
    return;
  }

  const success = triggerGitHubAction('fetch-suppliers.yml', {});

  if (success) {
    ui.alert(
      '処理開始',
      '仕入れ先価格の更新を開始しました。\n\n' +
      '処理完了まで数分かかる場合があります。',
      ui.ButtonSet.OK
    );
  } else {
    ui.alert(
      'エラー',
      'GitHub Actionのトリガーに失敗しました。',
      ui.ButtonSet.OK
    );
  }
}

// ========================================
// ユーティリティ
// ========================================

/**
 * GitHub Actions の実行履歴URLを取得
 */
function getActionsUrl() {
  return `https://github.com/${REPO_OWNER}/${REPO_NAME}/actions`;
}

/**
 * デバッグ用: GitHub Tokenの設定状態を確認
 */
function checkGitHubToken() {
  const token = getGitHubToken();
  if (token) {
    Logger.log('GitHub Token is set (length: ' + token.length + ')');
  } else {
    Logger.log('GitHub Token is NOT set');
  }
}
