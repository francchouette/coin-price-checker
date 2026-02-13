#!/bin/bash
# ================================================================
# 初回セットアップスクリプト
#
# 新しいPCでリポジトリをクローンした後に実行してください。
#
# 実行内容:
#   1. Python依存パッケージのインストール
#   2. Playwrightブラウザのインストール
#   3. .env ファイルの作成（未作成の場合）
#   4. Google Cloud ADC認証の確認
#   5. launchd plistファイルの配置（macOSのみ）
#   6. ログディレクトリの作成
#
# 使用方法:
#   bash scripts/setup.sh
# ================================================================

set -euo pipefail

# --- 色定義 ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }
info() { echo "     $*"; }

# --- プロジェクトディレクトリ ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

echo ""
echo "=========================================="
echo " コイン価格管理 セットアップ"
echo "=========================================="
echo ""
echo "プロジェクト: ${PROJECT_DIR}"
echo ""

# ========================================
# 1. Python確認
# ========================================
echo "--- Python環境 ---"
PYTHON=""
if [ -f .env ]; then
    PYTHON_FROM_ENV=$(grep -E '^PYTHON_PATH=' .env 2>/dev/null | head -1 | cut -d'=' -f2-)
    if [ -n "${PYTHON_FROM_ENV}" ] && [ -x "${PYTHON_FROM_ENV}" ]; then
        PYTHON="${PYTHON_FROM_ENV}"
    fi
fi
if [ -z "${PYTHON}" ]; then
    PYTHON="$(which python3 2>/dev/null || true)"
fi
if [ -z "${PYTHON}" ]; then
    err "python3 が見つかりません。pyenv等でPython 3.11+をインストールしてください。"
    exit 1
fi
PYTHON_VER=$("${PYTHON}" --version 2>&1)
ok "Python: ${PYTHON} (${PYTHON_VER})"
echo ""

# ========================================
# 2. 依存パッケージインストール
# ========================================
echo "--- 依存パッケージ ---"
"${PYTHON}" -m pip install -r requirements.txt --quiet
ok "pip install 完了"

# Playwright ブラウザ
if "${PYTHON}" -c "import playwright" 2>/dev/null; then
    "${PYTHON}" -m playwright install chromium --quiet 2>/dev/null || "${PYTHON}" -m playwright install chromium
    ok "Playwright chromium インストール完了"
else
    warn "playwright がインストールされていません（pip install後に再実行してください）"
fi
echo ""

# ========================================
# 3. .env ファイル
# ========================================
echo "--- 環境設定 (.env) ---"
if [ -f .env ]; then
    ok ".env ファイルが存在します"
    # 必須項目チェック
    for key in SPREADSHEET_ID COLORME_ACCESS_TOKEN; do
        val=$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d'=' -f2-)
        if [ -z "${val}" ] || [ "${val}" = "your-spreadsheet-id" ] || [ "${val}" = "your-colorme-access-token" ]; then
            warn "${key} が未設定です。.env ファイルを編集してください。"
        else
            ok "${key} = ${val:0:20}..."
        fi
    done
else
    cp .env.example .env
    warn ".env ファイルを作成しました。APIキー等を設定してください:"
    info "  vi .env"
fi

# PYTHON_PATH を .env に追記（未設定の場合）
if ! grep -q '^PYTHON_PATH=' .env 2>/dev/null; then
    echo "" >> .env
    echo "PYTHON_PATH=${PYTHON}" >> .env
    ok "PYTHON_PATH を .env に追記: ${PYTHON}"
fi
echo ""

# ========================================
# 4. Google Cloud ADC認証
# ========================================
echo "--- Google Cloud認証 ---"
ADC_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-${HOME}/.config/gcloud/application_default_credentials.json}"
if [ -f "${ADC_FILE}" ]; then
    ok "ADC認証ファイルが存在します: ${ADC_FILE}"
else
    warn "ADC認証ファイルが見つかりません: ${ADC_FILE}"
    info "以下のコマンドで認証してください:"
    info '  gcloud auth application-default login --scopes="openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets"'
    info '  gcloud auth application-default set-quota-project coin-price-tracker-479614'
fi
echo ""

# ========================================
# 5. launchd plist配置（macOSのみ）
# ========================================
echo "--- 定期実行 (launchd) ---"
if [ "$(uname)" = "Darwin" ]; then
    LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
    mkdir -p "${LAUNCH_AGENTS_DIR}"

    # Pythonのディレクトリ（plistのPATH用）
    PYTHON_DIR="$(dirname "${PYTHON}")"

    for template in launchd/*.plist; do
        [ -f "${template}" ] || continue
        filename="$(basename "${template}")"
        target="${LAUNCH_AGENTS_DIR}/${filename}"

        # テンプレートの置換
        sed \
            -e "s|{{PROJECT_DIR}}|${PROJECT_DIR}|g" \
            -e "s|{{PYTHON_DIR}}|${PYTHON_DIR}|g" \
            -e "s|{{HOME}}|${HOME}|g" \
            "${template}" > "${target}"

        ok "配置: ${target}"
    done
    info "定期実行を有効にするにはダッシュボードからONにしてください。"
else
    warn "macOS以外の環境です。定期実行は手動でcron等を設定してください。"
fi
echo ""

# ========================================
# 6. ログディレクトリ
# ========================================
echo "--- ログディレクトリ ---"
mkdir -p logs
ok "logs/ ディレクトリを作成しました"
echo ""

# ========================================
# 7. スクリプト実行権限
# ========================================
echo "--- 実行権限 ---"
chmod +x scripts/*.sh
chmod +x "カラーミー同期.command"
ok "スクリプトに実行権限を付与しました"
echo ""

# ========================================
# 完了
# ========================================
echo "=========================================="
echo -e " ${GREEN}セットアップ完了${NC}"
echo "=========================================="
echo ""
echo "使い方:"
echo "  ダッシュボード起動:  bash カラーミー同期.command"
echo "  手動フル同期:        bash scripts/cm-sync-prices.sh"
echo "  手動価格のみ同期:    bash scripts/cm-price-only-sync.sh"
echo "  手動BS商品取得:      bash scripts/bs-scrape.sh"
echo ""
