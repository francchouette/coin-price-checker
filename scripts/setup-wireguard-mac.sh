#!/bin/bash
# ==============================================================
# WireGuard クライアント セットアップスクリプト（macOS）
# 使い方: bash setup-wireguard-mac.sh ~/wg-client.conf
# ==============================================================

set -e

CONF_FILE="${1:-}"

echo "=================================================="
echo "WireGuard macOS クライアントセットアップ"
echo "=================================================="

# WireGuard インストール確認
if ! command -v brew &>/dev/null; then
    echo "Homebrew が見つかりません。手動でインストールしてください："
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo ""
    echo "または App Store から「WireGuard」をインストールし、"
    echo "設定ファイル(wg-client.conf)を手動でインポートしてください。"
    exit 1
fi

if ! command -v wg &>/dev/null; then
    echo "WireGuard をインストール中..."
    brew install wireguard-tools
fi

# 設定ファイル確認
if [ -z "$CONF_FILE" ] || [ ! -f "$CONF_FILE" ]; then
    echo "使い方: bash setup-wireguard-mac.sh <設定ファイルパス>"
    echo "例: bash setup-wireguard-mac.sh ~/wg-client.conf"
    exit 1
fi

# 設定ファイルをコピー
echo ""
echo "設定ファイルを配置中..."
sudo mkdir -p /usr/local/etc/wireguard
sudo cp "$CONF_FILE" /usr/local/etc/wireguard/wg0.conf
sudo chmod 600 /usr/local/etc/wireguard/wg0.conf

echo ""
echo "=================================================="
echo "セットアップ完了！"
echo ""
echo "VPN 接続コマンド:"
echo "  接続:  sudo wg-quick up wg0"
echo "  切断:  sudo wg-quick down wg0"
echo "  状態:  sudo wg show"
echo "  IP確認: curl ifconfig.me"
echo "=================================================="
